"""nova-lab ingest-Modul (B-Phase Schema): holt Marktdaten und persistiert
in DuckDB unter ~/nova_data/lab.duckdb in mkt_quotes_daily, getaggt mit
ref_instrument_id.

Aufruf:
  Lokal:    python -m modules.ingest
  Via nova: ~/nova/scripts/nova_run.sh    lab_ingest nova-hub --params-file <p.json>
            ~/nova/scripts/nova_submit.sh lab_ingest nova-hub --params-file <p.json>

Konfig (3-Tier):
  Tier 1 — Defaults im File
  Tier 2 — Env-Vars: LAB_DB_PATH, NOVA_JOB_ID
  Tier 3 — JSON via NOVA_PARAMS_FILE:
    {
      "source":              "ib",                       // optional, default 'ib'
      "ref_instrument_ids":  ["IB:AAPL:USD", ...],       // explizite Liste, ODER:
      "symbols":             ["AAPL", ...],              // matched gegen ref_instruments.symbol, ODER:
      "watchlist":           "active",                   // = WHERE active=true (Default)
      "since":               "2024-01-01",               // YYYY-MM-DD, ODER:
                                                         // "auto" = pro Instrument max(ts)+1
      "until":               "2026-05-02"                // optional, default = today
    }

Daily-Auto-Run-Pattern (~/jobs/lab_ingest_daily.json):
    {"source": "ib", "watchlist": "active", "since": "auto"}
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import date, datetime, timezone

import duckdb
import pandas as pd

from .sources.base import Instrument, SourceAdapter
from .sources.ib_src import IBAdapter
from .sources.yfinance_src import YFinanceAdapter

# ---------- Konfiguration ----------
DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SCHEMA_DIR = pathlib.Path(__file__).parent / "sql"

SOURCES: dict[str, type[SourceAdapter]] = {
    "yfinance": YFinanceAdapter,
    "ib":       IBAdapter,
}


# ---------- Hilfsfunktionen ----------

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
    """Idempotent — laedt alle nummerierten SQL-Files in sql/ in Reihenfolge."""
    for sql_file in sorted(SCHEMA_DIR.glob("0*.sql")):
        con.execute(sql_file.read_text())


def write_quotes_daily(
    con: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    source: str,
    run_id: str,
) -> int:
    """Insert-or-Replace in mkt_quotes_daily. Returnt Anzahl geschriebener Rows."""
    if df.empty:
        return 0
    df = df.copy()
    df["source"] = source
    df["run_id"] = run_id
    df["fetched_at"] = datetime.now(timezone.utc)

    con.register("incoming", df)
    try:
        con.execute(
            """
            INSERT OR REPLACE INTO mkt_quotes_daily
            (ref_instrument_id, ts, open, high, low, close, adj_close, volume,
             source, fetched_at, run_id)
            SELECT
             ref_instrument_id, ts, open, high, low, close, adj_close, volume,
             source, fetched_at, run_id
            FROM incoming
            """
        )
    finally:
        con.unregister("incoming")
    return len(df)


def log_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    source: str,
    started_at: datetime,
    completed_at: datetime | None,
    instruments_req: int,
    rows_added: int,
    status: str,
    error_msg: str | None,
) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO audit_ingest_runs
        (run_id, source, started_at, completed_at, instruments_req, rows_added, status, error_msg)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [run_id, source, started_at, completed_at, instruments_req, rows_added, status, error_msg],
    )


# ---------- Resolver ----------

def resolve_instruments(con: duckdb.DuckDBPyConnection, params: dict) -> list[Instrument]:
    """Loese die zu fetchenden Instruments auf:
       - params['ref_instrument_ids']: explizite Liste von Keys
       - params['symbols']:            Match gegen ref_instruments.symbol (kann mehrere zurueckgeben)
       - params['watchlist'] = 'active' (Default): WHERE active=true
    """
    sql = (
        "SELECT ref_instrument_id, symbol, currency, asset_type, con_id, exchange "
        "FROM ref_instruments "
    )

    if params.get("ref_instrument_ids"):
        ids = list(params["ref_instrument_ids"])
        placeholders = ",".join(["?"] * len(ids))
        rows = con.execute(
            sql + f"WHERE ref_instrument_id IN ({placeholders}) ORDER BY ref_instrument_id",
            ids,
        ).fetchall()
    elif params.get("symbols"):
        syms = list(params["symbols"])
        placeholders = ",".join(["?"] * len(syms))
        rows = con.execute(
            sql + f"WHERE symbol IN ({placeholders}) ORDER BY ref_instrument_id",
            syms,
        ).fetchall()
    else:
        watchlist = params.get("watchlist", "active")
        if watchlist == "active":
            rows = con.execute(
                sql + "WHERE active = true ORDER BY ref_instrument_id"
            ).fetchall()
        else:
            raise ValueError(f"Unbekannte watchlist '{watchlist}'. Aktuell unterstuetzt: 'active'.")

    return [
        Instrument(
            ref_instrument_id=r[0],
            symbol=r[1],
            currency=r[2],
            asset_type=r[3],
            con_id=r[4],
            exchange=r[5],
        )
        for r in rows
    ]


def resolve_since_per_instrument(
    con: duckdb.DuckDBPyConnection,
    ref_instrument_id: str,
    source: str,
    requested_since: date | str,
) -> date:
    """Wenn requested_since='auto', frage DB nach letztem Eintrag pro
    ref_instrument_id+source und starte +1 Tag. Sonst gib das Date zurueck."""
    if requested_since != "auto":
        return requested_since  # type: ignore[return-value]

    row = con.execute(
        "SELECT MAX(ts) FROM mkt_quotes_daily WHERE ref_instrument_id = ? AND source = ?",
        [ref_instrument_id, source],
    ).fetchone()
    last_ts = row[0] if row else None
    if last_ts is None:
        # Erste Erfassung — Default: 2 Jahre Historie
        from datetime import timedelta
        return date.today() - timedelta(days=730)
    from datetime import timedelta
    return last_ts + timedelta(days=1)


# ---------- Main ----------

def main() -> int:
    params = load_params()
    source_name = params.get("source", "ib")
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

    run_id = os.environ.get(
        "NOVA_JOB_ID",
        f"adhoc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    )

    if source_name not in SOURCES:
        print(f"FEHLER: Unbekannte Source '{source_name}'. Bekannte: {list(SOURCES)}", file=sys.stderr)
        return 64

    adapter = SOURCES[source_name]()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        adapter.bind_db(con)

        try:
            instruments = resolve_instruments(con, params)
        except ValueError as e:
            print(f"FEHLER: {e}", file=sys.stderr)
            return 64

        if not instruments:
            print("FEHLER: keine Instruments aufgeloest.", file=sys.stderr)
            print("       Hinweis: ref_instruments-Tabelle ist evtl. leer — erst portfolio importieren.", file=sys.stderr)
            return 64

        print("==> nova-lab ingest (B-Phase)")
        print(f"    source       : {source_name}")
        print(f"    instruments  : {len(instruments)}")
        sample = ", ".join(i.ref_instrument_id for i in instruments[:5])
        if len(instruments) > 5:
            sample += "..."
        print(f"                   {sample}")
        print(f"    since        : {since}")
        print(f"    until        : {until}")
        print(f"    db           : {DB_PATH}")
        print(f"    run_id       : {run_id}")

        started_at = datetime.now(timezone.utc)
        log_run(con, run_id, source_name, started_at, None, len(instruments), 0, "running", None)

        total_rows = 0
        failures: list[tuple[str, str]] = []
        for inst in instruments:
            inst_since = resolve_since_per_instrument(
                con, inst.ref_instrument_id, source_name, since
            )
            if inst_since > until:
                from datetime import timedelta
                last = inst_since - timedelta(days=1)
                print(f"    [SKIP] {inst.ref_instrument_id}: bereits aktuell (last={last})")
                continue

            result = adapter.fetch_quotes_daily(inst, inst_since, until)
            if not result.ok:
                failures.append((inst.ref_instrument_id, result.error or "unknown"))
                print(f"    [FAIL] {inst.ref_instrument_id}: {result.error}")
                continue
            if result.skipped:
                print(f"    [SKIP] {inst.ref_instrument_id}: keine Daten in {inst_since}..{until}")
                continue
            n = write_quotes_daily(con, result.rows, source_name, run_id)
            total_rows += n
            print(f"    [OK]   {inst.ref_instrument_id}: {n} rows ({inst_since}..{until})")

        completed_at = datetime.now(timezone.utc)
        if failures and total_rows == 0:
            status = "failed"
        elif failures:
            status = "partial"
        else:
            status = "success"
        err_msg = None if not failures else "; ".join(f"{rid}={e}" for rid, e in failures[:5])
        log_run(con, run_id, source_name, started_at, completed_at, len(instruments), total_rows, status, err_msg)

    finally:
        try:
            adapter.close()
        except Exception as e:  # noqa: BLE001
            print(f"    [WARN] adapter.close() raised: {e}", file=sys.stderr)
        con.close()

    print(f"==> done: {total_rows} rows, status={status}, failures={len(failures)}")
    return 0 if status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
