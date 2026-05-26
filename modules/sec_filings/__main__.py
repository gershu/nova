"""nova-lab sec_filings CLI — GuV-Kerndaten aus SEC-EDGAR.

Subcommands:
    init                  SQL-Schema anlegen (idempotent)
    fetch <ticker>        Juengstes Filing eines Symbols laden
    fetch-all [--since-days N]
                          Holdings + Watchlists aktualisieren (Daemon-Modus)
    backfill <ticker> [--quarters N]
                          Historie: letzte N Filings (10-Q + 10-K) laden
    show <ticker>         Gespeicherte GuV eines Symbols zeigen

Environment:
    NOVA_SEC_API_KEY      Pflicht — via ~/.nova_env oder Shell
    LAB_DB_PATH           optional — default ~/nova_data/lab.duckdb

Beispiele:
    python -m modules.sec_filings init
    python -m modules.sec_filings fetch NVDA
    python -m modules.sec_filings fetch-all --since-days 6
    python -m modules.sec_filings show NVDA
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
import uuid
from datetime import datetime, timezone

import duckdb

from .client import (
    IncomeStatement, SecApiError,
    fetch_income, fetch_income_from_filing, find_filings,
)


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SQL_DIR = pathlib.Path(__file__).parent / "sql"
SQL_FILES = sorted(SQL_DIR.glob("0*.sql"))

# Watchlists, deren Members zusaetzlich zu den Holdings refresht werden.
_WATCHLISTS = ("buy_candidates", "csp_universe")

# asset_type-Werte ohne SEC-Filing (ETFs, Optionen, Cash) — uebersprungen.
_SKIP_ASSET_TYPES = ("ETF", "FUND", "CASH", "OPT", "FUT", "IND")


# ---------- Schema ----------

def apply_schema(con: duckdb.DuckDBPyConnection, *, verbose: bool = False) -> None:
    """SQL-Files idempotent anwenden (CREATE TABLE IF NOT EXISTS).

    Wird von init UND fetch-all aufgerufen — so crasht der Daemon nicht,
    falls 'init' auf einer DB nie lief.
    """
    for f in SQL_FILES:
        con.execute(f.read_text())
        if verbose:
            print(f"    ✓ {f.name}")


def cmd_init(args) -> int:
    if not SQL_FILES:
        print(f"FEHLER: keine SQL-Files in {SQL_DIR}", file=sys.stderr)
        return 64
    con = duckdb.connect(str(DB_PATH))
    try:
        print(f"==> Applying {len(SQL_FILES)} SQL-Files in {DB_PATH}")
        apply_schema(con, verbose=True)
        print("==> ref_income_statement bereit.")
        return 0
    finally:
        con.close()


# ---------- Upsert-Helfer ----------

_COLS = [
    "ref_instrument_id", "period_end", "form_type", "fiscal_period",
    "accession_no", "filed_at", "period_months", "currency",
    "revenue", "cost_of_revenue", "gross_profit", "rd_expense",
    "sga_expense", "operating_expense", "operating_income", "other_income",
    "pretax_income", "tax_expense", "net_income", "source", "fetched_at",
]


def _upsert(con: duckdb.DuckDBPyConnection, inc: IncomeStatement) -> None:
    """Eine IncomeStatement-Zeile schreiben (Insert oder Update)."""
    row = [
        inc.ref_instrument_id,
        inc.period_end,
        inc.form_type,
        None,                                       # fiscal_period (Reserve)
        inc.accession_no,
        (inc.filed_at[:19] if inc.filed_at else None),
        inc.period_months,
        inc.currency or "USD",
        inc.revenue, inc.cost_of_revenue, inc.gross_profit, inc.rd_expense,
        inc.sga_expense, inc.operating_expense, inc.operating_income,
        inc.other_income, inc.pretax_income, inc.tax_expense, inc.net_income,
        "sec-api.io",
        datetime.now(timezone.utc).replace(tzinfo=None),
    ]
    placeholders = ", ".join("?" for _ in _COLS)
    updates = ", ".join(f"{c} = excluded.{c}"
                        for c in _COLS if c not in ("ref_instrument_id",
                                                    "period_end"))
    con.execute(
        f"INSERT INTO ref_income_statement ({', '.join(_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT (ref_instrument_id, period_end) DO UPDATE SET {updates}",
        row,
    )


def _upsert_segments(con: duckdb.DuckDBPyConnection,
                      inc: IncomeStatement) -> int:
    """Segment-Zeilen (instrument, period) komplett neu setzen.

    Delete-then-insert ist hier sauberer als per-Member-Upsert: wenn eine
    Achse oder ein Member im neuen Filing wegfaellt, soll die alte Zeile
    auch weg.
    """
    if not inc.ref_instrument_id or not inc.period_end:
        return 0
    con.execute(
        "DELETE FROM ref_revenue_segments "
        "WHERE ref_instrument_id = ? AND period_end = ?",
        [inc.ref_instrument_id, inc.period_end],
    )
    if not inc.segments:
        return 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for seg in inc.segments:
        con.execute("""
            INSERT INTO ref_revenue_segments
                (ref_instrument_id, period_end, axis, member, member_label,
                 value, currency, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'sec-api.io', ?)
        """, [inc.ref_instrument_id, inc.period_end, seg["axis"],
              seg["member"], seg.get("member_label"), seg["value"],
              inc.currency or "USD", now])
    return len(inc.segments)


def _resolve(con: duckdb.DuckDBPyConnection,
             ticker: str) -> tuple[str, str] | None:
    """ticker -> (ref_instrument_id, symbol) ueber ref_instruments."""
    row = con.execute(
        "SELECT ref_instrument_id, symbol FROM ref_instruments "
        "WHERE upper(symbol) = upper(?) ORDER BY ref_instrument_id LIMIT 1",
        [ticker],
    ).fetchone()
    return (row[0], row[1]) if row else None


# ---------- fetch ----------

def cmd_fetch(args) -> int:
    con = duckdb.connect(str(DB_PATH))
    try:
        apply_schema(con)
        resolved = _resolve(con, args.ticker)
        if not resolved:
            print(f"FEHLER: '{args.ticker}' nicht in ref_instruments.",
                  file=sys.stderr)
            return 64
        ref_id, symbol = resolved
        print(f"==> Fetching GuV fuer {symbol} ({ref_id})")
        try:
            inc = fetch_income(symbol)
        except SecApiError as e:
            print(f"✗ sec-api.io-Fehler: {e}", file=sys.stderr)
            return 65
        if inc is None:
            print(f"    — kein 10-Q/10-K fuer {symbol} gefunden.")
            return 0
        inc.ref_instrument_id = ref_id
        _upsert(con, inc)
        n_seg = _upsert_segments(con, inc)
        print(f"    ✓ {inc.form_type} per {inc.period_end} · "
              f"Revenue {inc.revenue} · Net {inc.net_income} · "
              f"{n_seg} Segment-Zeilen")
        for w in inc.warnings:
            print(f"      ⚠ {w}")
        return 0
    finally:
        con.close()


# ---------- fetch-all ----------

def _targets(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    """(ref_instrument_id, symbol) fuer Holdings + Watchlist-Members."""
    wl = ", ".join(f"'{w}'" for w in _WATCHLISTS)
    skip = ", ".join(f"'{a}'" for a in _SKIP_ASSET_TYPES)
    rows = con.execute(f"""
        WITH universe AS (
            SELECT DISTINCT ref_instrument_id FROM v_mkt_holdings
            UNION
            SELECT DISTINCT ref_instrument_id FROM list_watchlist_members
            WHERE watchlist_id IN ({wl})
        )
        SELECT i.ref_instrument_id, i.symbol
        FROM universe u
        JOIN ref_instruments i USING (ref_instrument_id)
        WHERE i.symbol IS NOT NULL
          AND (i.asset_type IS NULL OR upper(i.asset_type) NOT IN ({skip}))
        ORDER BY i.symbol
    """).fetchall()
    return [(r[0], r[1]) for r in rows]


def cmd_fetch_all(args) -> int:
    run_id = (f"sec-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
              f"-{uuid.uuid4().hex[:6]}")
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    con = duckdb.connect(str(DB_PATH))
    try:
        apply_schema(con)
        targets = _targets(con)
        if not targets:
            print("Keine Ziel-Instrumente (Holdings/Watchlists leer).")
            return 0

        # Symbole mit frischem Snapshot ueberspringen (Daemon-Schoner).
        recent: set[str] = set()
        if args.since_days > 0:
            for (rid,) in con.execute(
                "SELECT ref_instrument_id FROM ref_income_statement "
                "WHERE fetched_at >= now() - INTERVAL (?) DAY",
                [args.since_days],
            ).fetchall():
                recent.add(rid)

        print(f"==> fetch-all: {len(targets)} Instrumente  (run_id={run_id})")
        n_up, n_skip, n_fail = 0, 0, 0
        for ref_id, symbol in targets:
            if ref_id in recent:
                n_skip += 1
                print(f"    · {symbol:<8s} uebersprungen (frisch)")
                continue
            try:
                inc = fetch_income(symbol)
            except SecApiError as e:
                n_fail += 1
                print(f"    ✗ {symbol:<8s} FAIL: {e}", file=sys.stderr)
                continue
            if inc is None:
                n_skip += 1
                print(f"    · {symbol:<8s} kein Filing")
                continue
            inc.ref_instrument_id = ref_id
            _upsert(con, inc)
            n_seg = _upsert_segments(con, inc)
            n_up += 1
            print(f"    ✓ {symbol:<8s} {inc.form_type} per {inc.period_end}"
                  f"  ({n_seg} seg)")
            time.sleep(args.sleep)

        finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        status = "ok" if n_fail == 0 else ("partial" if n_up > 0 else "fail")
        con.execute("""
            INSERT INTO audit_sec_filings_runs
                (run_id, started_at, finished_at, instrument_count,
                 rows_upserted, rows_skipped, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [run_id, started_at, finished_at, len(targets),
              n_up, n_skip, status])
        print(f"\n==> Summary: {n_up} upserted · {n_skip} skipped · "
              f"{n_fail} fail · status={status}")
        return 0 if n_fail == 0 else 1
    finally:
        con.close()


# ---------- backfill ----------

def cmd_backfill(args) -> int:
    """Historie: letzte N Filings (10-Q + 10-K) je Periode upserten."""
    con = duckdb.connect(str(DB_PATH))
    try:
        apply_schema(con)
        resolved = _resolve(con, args.ticker)
        if not resolved:
            print(f"FEHLER: '{args.ticker}' nicht in ref_instruments.",
                  file=sys.stderr)
            return 64
        ref_id, symbol = resolved
        print(f"==> Backfill GuV-Historie fuer {symbol} ({ref_id})  —  "
              f"bis zu {args.quarters} Filings")
        try:
            filings = find_filings(symbol, n=args.quarters)
        except SecApiError as e:
            print(f"✗ Query-API-Fehler: {e}", file=sys.stderr)
            return 65
        if not filings:
            print("    Keine Filings gefunden.")
            return 0

        n_ok, n_skip, n_fail = 0, 0, 0
        for f in filings:
            tag = f"{f['period_of_report']} {f['form_type']:<5s}"
            try:
                inc = fetch_income_from_filing(f)
            except SecApiError as e:
                n_fail += 1
                print(f"    ✗ {tag} FAIL: {e}", file=sys.stderr)
                continue
            if inc is None:
                n_skip += 1
                print(f"    · {tag} keine GuV-Sektion")
                continue
            inc.ref_instrument_id = ref_id
            _upsert(con, inc)
            n_seg = _upsert_segments(con, inc)
            n_ok += 1
            print(f"    ✓ {tag}  Revenue {inc.revenue:>15,.0f}  "
                  f"({n_seg} seg)")
            time.sleep(args.sleep)
        print(f"\n==> {n_ok} OK · {n_skip} skipped · {n_fail} fail")
        return 0 if n_fail == 0 else 1
    finally:
        con.close()


# ---------- show ----------

def cmd_show(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        resolved = _resolve(con, args.ticker)
        if not resolved:
            print(f"FEHLER: '{args.ticker}' nicht in ref_instruments.",
                  file=sys.stderr)
            return 64
        ref_id, symbol = resolved
        row = con.execute("""
            SELECT period_end, form_type, currency, revenue, cost_of_revenue,
                   gross_profit, rd_expense, sga_expense, operating_expense,
                   operating_income, other_income, pretax_income,
                   tax_expense, net_income, accession_no, fetched_at
            FROM ref_income_statement
            WHERE ref_instrument_id = ?
            ORDER BY period_end DESC LIMIT 1
        """, [ref_id]).fetchone()
        if not row:
            print(f"(keine GuV-Daten fuer {symbol} — erst 'fetch {symbol}')")
            return 0
        keys = ["period_end", "form_type", "currency", "revenue",
                "cost_of_revenue", "gross_profit", "rd_expense",
                "sga_expense", "operating_expense", "operating_income",
                "other_income", "pretax_income", "tax_expense",
                "net_income", "accession_no", "fetched_at"]
        print(f"==> {symbol}  ({ref_id})")
        for k, v in zip(keys, row):
            if isinstance(v, float):
                print(f"  {k:<20s} {v:>20,.0f}")
            else:
                print(f"  {k:<20s} {v}")

        # Segmente nach Achse gruppiert anzeigen
        period_end = row[0]
        segs = con.execute("""
            SELECT axis, member_label, value FROM ref_revenue_segments
            WHERE ref_instrument_id = ? AND period_end = ?
            ORDER BY axis, value DESC
        """, [ref_id, period_end]).fetchall()
        if segs:
            print(f"\n  Umsatz-Aufschluesselungen ({len(segs)} Zeilen):")
            cur_axis = None
            for axis, label, val in segs:
                if axis != cur_axis:
                    print(f"\n    [{axis}]")
                    cur_axis = axis
                print(f"      {label:<32s} {val:>20,.0f}")
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="SQL-Schema anlegen (idempotent)")

    p_fetch = sub.add_parser("fetch", help="Ein Symbol aktualisieren")
    p_fetch.add_argument("ticker")

    p_all = sub.add_parser("fetch-all", help="Holdings + Watchlists (Daemon)")
    p_all.add_argument("--since-days", type=int, default=0,
                       help="Symbole mit Snapshot juenger als N Tage skippen")
    p_all.add_argument("--sleep", type=float, default=0.4,
                       help="Pause zwischen API-Calls (Rate-Limit-Schoner)")

    p_back = sub.add_parser("backfill",
        help="Historie: letzte N Filings je Periode upserten")
    p_back.add_argument("ticker")
    p_back.add_argument("--quarters", type=int, default=20,
        help="Wieviele juengste Filings (10-Q + 10-K) — default 20")
    p_back.add_argument("--sleep", type=float, default=0.4,
        help="Pause zwischen API-Calls (Rate-Limit-Schoner)")

    p_show = sub.add_parser("show", help="Gespeicherte GuV zeigen")
    p_show.add_argument("ticker")

    args = p.parse_args()
    if not DB_PATH.is_file() and args.cmd != "init":
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}. 'init' zuerst.",
              file=sys.stderr)
        return 64

    dispatch = {
        "init":      cmd_init,
        "fetch":     cmd_fetch,
        "fetch-all": cmd_fetch_all,
        "backfill":  cmd_backfill,
        "show":      cmd_show,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
