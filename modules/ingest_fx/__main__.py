"""nova-lab FX-Ingest (B-Phase): holt EOD FX-Rates und schreibt in mkt_fx_daily.

Symmetrische Speicherung: jeder Fetch von z.B. EURUSD=X schreibt zwei Zeilen
  - (EUR, USD, rate)
  - (USD, EUR, 1/rate)
→ Downstream-Joins koennen einfach `WHERE currency_to = 'EUR'` filtern, ohne
  zwischen direct/inverse unterscheiden zu muessen.

Aufruf:
  Lokal:    python -m modules.ingest_fx
  Via nova: ~/nova/scripts/nova_run.sh    lab_ingest_fx nova-hub --params-file <p.json>
            ~/nova/scripts/nova_submit.sh lab_ingest_fx nova-hub --params-file <p.json>

Konfig (3-Tier):
  Tier 3 — JSON via NOVA_PARAMS_FILE:
    {
      "source":  "yfinance",                              // optional, default 'yfinance'
      "base":    "EUR",                                   // optional, default 'EUR'
      "pairs":   [["EUR","USD"], ["EUR","NOK"]],          // optional explizite Pairs
                                                          // Wenn nicht gesetzt: auto-derive aus
                                                          // DISTINCT currency in pos_holdings + ref_instruments
      "since":   "2024-01-01",                            // YYYY-MM-DD oder "auto" (max(ts)+1)
      "until":   "2026-05-09"                             // optional, default = today
    }

Daily-Auto-Run (~/jobs/lab_ingest_fx_daily.json):
    {"source": "yfinance", "since": "auto"}
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from datetime import date, datetime, timedelta, timezone

import duckdb
import pandas as pd

# ---------- Konfiguration ----------
DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
INGEST_SCHEMA_DIR = pathlib.Path(__file__).parent.parent / "ingest" / "sql"


def load_params() -> dict:
    pf = os.environ.get("NOVA_PARAMS_FILE")
    if not pf:
        return {}
    p = pathlib.Path(pf)
    if not p.is_file():
        print(f"[WARN] NOVA_PARAMS_FILE gesetzt ({pf}), aber Datei existiert nicht", file=sys.stderr)
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        print(f"[WARN] params file ist kein gueltiges JSON: {e}", file=sys.stderr)
        return {}


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    for sql_file in sorted(INGEST_SCHEMA_DIR.glob("0*.sql")):
        con.execute(sql_file.read_text())


def derive_pairs(con: duckdb.DuckDBPyConnection, base: str) -> list[tuple[str, str]]:
    """Auto-derive aus pos_holdings + ref_instruments: alle nicht-base
    Currencies. Liefert pairs (base, foreign)."""
    rows = con.execute(
        """
        SELECT DISTINCT currency
        FROM (
            SELECT currency FROM pos_holdings    WHERE currency IS NOT NULL
            UNION
            SELECT currency FROM ref_instruments WHERE currency IS NOT NULL
        )
        WHERE currency != ?
        ORDER BY currency
        """,
        [base],
    ).fetchall()
    return [(base, r[0]) for r in rows]


def resolve_since(
    con: duckdb.DuckDBPyConnection,
    base: str,
    quote: str,
    source: str,
    requested_since: date | str,
) -> date:
    if requested_since != "auto":
        return requested_since  # type: ignore[return-value]
    row = con.execute(
        "SELECT MAX(ts) FROM mkt_fx_daily WHERE currency_from = ? AND currency_to = ? AND source = ?",
        [base, quote, source],
    ).fetchone()
    last_ts = row[0] if row else None
    if last_ts is None:
        # Erste Erfassung — Default 2 Jahre
        return date.today() - timedelta(days=730)
    return last_ts + timedelta(days=1)


def fetch_yfinance(
    base: str, quote: str, since: date, until: date,
) -> pd.DataFrame:
    """Holt {base}{quote}=X von yfinance. Returnt DataFrame mit
    columns [currency_from, currency_to, ts, rate].
    rate = 1 base = X quote (yfinance-Konvention).
    """
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame(columns=["currency_from", "currency_to", "ts", "rate"])

    ticker_str = f"{base}{quote}=X"
    df = yf.Ticker(ticker_str).history(
        start=since.isoformat(),
        end=(until + timedelta(days=1)).isoformat(),
        auto_adjust=False,
        actions=False,
    )
    if df.empty:
        return pd.DataFrame(columns=["currency_from", "currency_to", "ts", "rate"])

    df = df.reset_index()
    out = pd.DataFrame({
        "currency_from": base,
        "currency_to":   quote,
        "ts":            pd.to_datetime(df["Date"]).dt.date,
        "rate":          df["Close"].astype(float),
    })
    return out


def write_fx_symmetric(
    con: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    source: str,
    run_id: str,
) -> int:
    """Schreibt direct + inverted Direction. Returnt total rows."""
    if df.empty:
        return 0

    # Direct
    direct = df.copy()
    direct["source"] = source
    direct["run_id"] = run_id
    direct["fetched_at"] = datetime.now(timezone.utc)

    # Inverted: swap from/to, invert rate
    inverted = df.copy()
    inverted["currency_from"], inverted["currency_to"] = (
        inverted["currency_to"].copy(), inverted["currency_from"].copy()
    )
    inverted["rate"] = 1.0 / inverted["rate"]
    inverted["source"] = source
    inverted["run_id"] = run_id
    inverted["fetched_at"] = datetime.now(timezone.utc)

    combined = pd.concat([direct, inverted], ignore_index=True)
    con.register("incoming_fx", combined)
    try:
        con.execute(
            """
            INSERT OR REPLACE INTO mkt_fx_daily
            (currency_from, currency_to, ts, rate, source, fetched_at, run_id)
            SELECT
             currency_from, currency_to, ts, rate, source, fetched_at, run_id
            FROM incoming_fx
            """
        )
    finally:
        con.unregister("incoming_fx")
    return len(combined)


# ---------- Main ----------

def main() -> int:
    params = load_params()
    source = params.get("source", "yfinance")
    base = params.get("base", "EUR").upper()
    since_param = params.get("since")
    until_str = params.get("until")

    if not since_param:
        print("FEHLER: 'since' (YYYY-MM-DD oder 'auto') muss in params angegeben sein.", file=sys.stderr)
        return 64

    try:
        since: date | str = since_param if since_param == "auto" else date.fromisoformat(since_param)
        until = date.fromisoformat(until_str) if until_str else date.today()
    except ValueError as e:
        print(f"FEHLER: ungueltiges Datum: {e}", file=sys.stderr)
        return 64

    if source != "yfinance":
        print(f"FEHLER: aktuell nur source=yfinance unterstuetzt (got {source}).", file=sys.stderr)
        return 64

    run_id = os.environ.get(
        "NOVA_JOB_ID",
        f"adhoc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_schema(con)

        # Pairs aufloesen
        if params.get("pairs"):
            pairs = [(p[0].upper(), p[1].upper()) for p in params["pairs"]]
        else:
            pairs = derive_pairs(con, base)

        if not pairs:
            print(f"==> nova-lab ingest_fx: keine Pairs zu fetchen (DB-currencies sind alle {base}).")
            return 0

        print("==> nova-lab ingest_fx (B-Phase)")
        print(f"    source : {source}")
        print(f"    base   : {base}")
        print(f"    pairs  : {len(pairs)}  ({', '.join(f'{a}{b}' for a, b in pairs)})")
        print(f"    since  : {since}")
        print(f"    until  : {until}")
        print(f"    db     : {DB_PATH}")
        print(f"    run_id : {run_id}")

        total_rows = 0
        failures: list[tuple[str, str]] = []
        for from_ccy, to_ccy in pairs:
            pair_since = resolve_since(con, from_ccy, to_ccy, source, since)
            if pair_since > until:
                last = pair_since - timedelta(days=1)
                print(f"    [SKIP] {from_ccy}{to_ccy}: bereits aktuell (last={last})")
                continue

            try:
                df = fetch_yfinance(from_ccy, to_ccy, pair_since, until)
            except Exception as e:  # noqa: BLE001
                pair_label = f"{from_ccy}{to_ccy}"
                failures.append((pair_label, f"{e.__class__.__name__}: {e}"))
                print(f"    [FAIL] {pair_label}: {e.__class__.__name__}: {e}")
                continue

            if df.empty:
                print(f"    [SKIP] {from_ccy}{to_ccy}: keine Daten in {pair_since}..{until}")
                continue

            n = write_fx_symmetric(con, df, source, run_id)
            total_rows += n
            print(f"    [OK]   {from_ccy}{to_ccy}: {n} rows ({pair_since}..{until})  (incl. inverse {to_ccy}{from_ccy})")

            time.sleep(0.1)  # yfinance-Rate-Hygiene

        if failures and total_rows == 0:
            status = "failed"
        elif failures:
            status = "partial"
        else:
            status = "success"

    finally:
        con.close()

    print(f"==> done: {total_rows} rows, status={status}, failures={len(failures)}")
    return 0 if status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
