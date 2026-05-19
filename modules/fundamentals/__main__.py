"""nova-lab fundamentals CLI.

Persistiert Value-Investor-Kennzahlen in ref_fundamentals_snapshot.

Source-Strategie (Pfad 3, Hybrid):
    - yfinance ist heute primary (keine IB-Subscription)
    - ib_adapter ist Stub; raised NotConfigured. Wird automatisch geskippt.
    - User kann --source erzwingen, sonst probiert die CLI verfuegbare Adapter
      in Prio-Order: ib (wenn available) -> yfinance.

Subcommands:
    refresh <symbol>            Single-symbol refresh (--source default yfinance)
    refresh-all                 Alle holdings + watchlist-members
    show <ref_instrument_id>    Pretty-print latest snapshot
    coverage                    Aggregat: wieviele Symbols haben fundamentals

Beispiele:
    python -m modules.fundamentals refresh IB:AAPL:USD
    python -m modules.fundamentals refresh-all --since-days 7
    python -m modules.fundamentals show IB:AAPL:USD
    python -m modules.fundamentals coverage
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import uuid
from datetime import date, timedelta

import duckdb

from .base import Fundamentals, FundamentalsAdapter, NotConfigured
from .yf_adapter import YFinanceFundamentalsAdapter
from .ib_adapter import IBFundamentalsAdapter


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SCHEMA_FILE = pathlib.Path(__file__).parent / "sql" / "0001_fundamentals.sql"


def _ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    if not SCHEMA_FILE.is_file():
        raise FileNotFoundError(f"Schema-File fehlt: {SCHEMA_FILE}")
    con.execute(SCHEMA_FILE.read_text())


def _resolve_instrument(con: duckdb.DuckDBPyConnection, ref_id: str) -> tuple[str, str]:
    """Returns (symbol, currency) — fail wenn instrument unbekannt."""
    row = con.execute(
        "SELECT symbol, currency FROM ref_instruments WHERE ref_instrument_id = ?",
        [ref_id],
    ).fetchone()
    if row is None:
        raise ValueError(f"Unbekanntes Instrument: {ref_id} — pflegen via "
                         f"`python -m modules.instruments add ...`")
    return row[0], row[1]


def _pick_adapter(name: str | None) -> FundamentalsAdapter:
    """Adapter-Auswahl. None = auto (ib wenn available, sonst yfinance)."""
    if name == "yfinance":
        return YFinanceFundamentalsAdapter()
    if name == "ib":
        return IBFundamentalsAdapter()
    # auto
    ib = IBFundamentalsAdapter()
    if getattr(ib, "available", False):
        return ib
    return YFinanceFundamentalsAdapter()


def _persist(con: duckdb.DuckDBPyConnection, fund: Fundamentals) -> None:
    """INSERT OR REPLACE in ref_fundamentals_snapshot."""
    d = fund.to_db_dict()
    # ts kommt als ISO-str — DuckDB akzeptiert das fuer DATE.
    cols = [
        "ref_instrument_id", "ts", "source",
        "sector", "industry", "country", "employees",
        "market_cap", "enterprise_value", "shares_outstanding",
        "pe_ttm", "pe_forward", "pb", "ps_ttm", "p_fcf",
        "ev_ebitda", "ev_sales", "peg_ratio",
        "roe", "roa", "roic",
        "gross_margin", "operating_margin", "net_margin", "fcf_margin",
        "debt_to_equity", "net_debt_to_ebitda", "current_ratio",
        "quick_ratio", "interest_coverage",
        "fcf_yield", "dividend_yield", "payout_ratio", "dividend_per_share",
        "revenue_cagr_5y", "eps_cagr_5y", "fcf_cagr_5y", "dividend_cagr_5y",
        "payload_json", "run_id",
    ]
    placeholders = ", ".join("?" for _ in cols)
    con.execute(
        f"INSERT OR REPLACE INTO ref_fundamentals_snapshot ({', '.join(cols)}) "
        f"VALUES ({placeholders})",
        [d.get(c) for c in cols],
    )


def _fmt(v, places=2):
    if v is None:
        return "—"
    return f"{v:,.{places}f}"


def _fmt_pct(v):
    if v is None:
        return "—"
    return f"{v * 100:.2f}%"


# ---------- refresh ----------

def cmd_refresh(args) -> int:
    con = duckdb.connect(str(DB_PATH))
    try:
        _ensure_schema(con)
        symbol, currency = _resolve_instrument(con, args.ref_instrument_id)
        adapter = _pick_adapter(args.source)
        run_id = args.run_id or str(uuid.uuid4())

        print(f"==> Refresh {args.ref_instrument_id} via {adapter.name}  (symbol={symbol} ccy={currency})")
        try:
            fund = adapter.fetch(args.ref_instrument_id, symbol, currency, run_id=run_id)
        except NotConfigured as e:
            print(f"    Adapter {adapter.name} nicht aktiv:\n{e}", file=sys.stderr)
            return 64

        n_filled = fund.filled_count()
        print(f"    Source-Notes : {'; '.join(fund.notes) if fund.notes else '-'}")
        print(f"    Filled metrics: {n_filled}/32")
        if n_filled == 0:
            print(f"    WARN: keine Metriken extrahiert — DB-Insert trotzdem (zur Audit-Trail).")

        _persist(con, fund)
        print(f"    Persisted -> ref_fundamentals_snapshot (ts={fund.ts}, source={fund.source})")
        return 0
    finally:
        con.close()


# ---------- refresh-all ----------

def cmd_refresh_all(args) -> int:
    con = duckdb.connect(str(DB_PATH))
    try:
        _ensure_schema(con)

        # Universe: holdings + watchlist-members (v_relevant_instruments)
        rows = con.execute("""
            SELECT DISTINCT ri.ref_instrument_id, ri.symbol, ri.currency
            FROM v_relevant_instruments v
            JOIN ref_instruments ri USING (ref_instrument_id)
            WHERE ri.active = TRUE
            ORDER BY ri.symbol
        """).fetchall()

        if not rows:
            print("Universe leer — keine holdings, keine watchlist-members.")
            return 0

        # Bereits-frische skippen
        skip_before = None
        if args.since_days is not None:
            skip_before = date.today() - timedelta(days=args.since_days)
            recent = con.execute("""
                SELECT ref_instrument_id, MAX(ts) AS last_ts
                FROM ref_fundamentals_snapshot
                GROUP BY ref_instrument_id
            """).fetchall()
            recent_map = {r[0]: r[1] for r in recent}
        else:
            recent_map = {}

        adapter = _pick_adapter(args.source)
        run_id = args.run_id or str(uuid.uuid4())

        print(f"==> refresh-all via {adapter.name}  ({len(rows)} instruments)")
        print(f"    skip-if-fresher-than: {skip_before or '(no skip)'}")
        print(f"    run_id: {run_id}")
        print()

        n_ok, n_skipped, n_failed = 0, 0, 0
        for ref_id, sym, ccy in rows:
            last = recent_map.get(ref_id)
            if skip_before is not None and last is not None and last >= skip_before:
                print(f"  [skip]  {ref_id}  (last_ts={last})")
                n_skipped += 1
                continue
            try:
                fund = adapter.fetch(ref_id, sym, ccy, run_id=run_id)
            except NotConfigured as e:
                print(f"  [fail]  {ref_id}  -> {adapter.name} not configured")
                n_failed += 1
                continue
            except Exception as e:  # noqa: BLE001
                print(f"  [fail]  {ref_id}  -> {e.__class__.__name__}: {e}")
                n_failed += 1
                continue

            filled = fund.filled_count()
            _persist(con, fund)
            tag = "OK " if filled > 0 else "thin"
            print(f"  [{tag}]  {ref_id:<20s}  filled={filled}/32  notes={'; '.join(fund.notes) or '-'}")
            n_ok += 1

        print()
        print(f"==> Done. ok={n_ok}  skipped={n_skipped}  failed={n_failed}")
        return 0 if n_failed == 0 else 65
    finally:
        con.close()


# ---------- show ----------

def cmd_show(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        row = con.execute("""
            SELECT * FROM ref_fundamentals_latest
            WHERE ref_instrument_id = ?
        """, [args.ref_instrument_id]).fetchone()
        if row is None:
            print(f"Keine Fundamentals fuer {args.ref_instrument_id}.")
            print(f"  -> `python -m modules.fundamentals refresh {args.ref_instrument_id}`")
            return 0

        cols = [d[0] for d in con.description]
        d = dict(zip(cols, row))

        print(f"==> Fundamentals: {args.ref_instrument_id}   (source={d['source']}, ts={d['ts']})")
        print()

        sections = [
            ("Identity", [
                ("sector",            d.get("sector"),            "{}"),
                ("industry",          d.get("industry"),          "{}"),
                ("country",           d.get("country"),           "{}"),
                ("employees",         d.get("employees"),         "{:,}"),
                ("market_cap",        d.get("market_cap"),        "{:,.0f}"),
                ("enterprise_value",  d.get("enterprise_value"),  "{:,.0f}"),
            ]),
            ("Valuation", [
                ("pe_ttm",       d.get("pe_ttm"),     "{:.2f}"),
                ("pe_forward",   d.get("pe_forward"), "{:.2f}"),
                ("pb",           d.get("pb"),         "{:.2f}"),
                ("ps_ttm",       d.get("ps_ttm"),     "{:.2f}"),
                ("p_fcf",        d.get("p_fcf"),      "{:.2f}"),
                ("ev_ebitda",    d.get("ev_ebitda"),  "{:.2f}"),
                ("ev_sales",     d.get("ev_sales"),   "{:.2f}"),
                ("peg_ratio",    d.get("peg_ratio"),  "{:.2f}"),
            ]),
            ("Quality", [
                ("roe",              d.get("roe"),              "pct"),
                ("roa",              d.get("roa"),              "pct"),
                ("roic",             d.get("roic"),             "pct"),
                ("gross_margin",     d.get("gross_margin"),     "pct"),
                ("operating_margin", d.get("operating_margin"), "pct"),
                ("net_margin",       d.get("net_margin"),       "pct"),
                ("fcf_margin",       d.get("fcf_margin"),       "pct"),
            ]),
            ("Solidity", [
                ("debt_to_equity",     d.get("debt_to_equity"),     "{:.2f}"),
                ("net_debt_to_ebitda", d.get("net_debt_to_ebitda"), "{:.2f}"),
                ("current_ratio",     d.get("current_ratio"),       "{:.2f}"),
                ("quick_ratio",       d.get("quick_ratio"),         "{:.2f}"),
                ("interest_coverage", d.get("interest_coverage"),   "{:.2f}"),
            ]),
            ("Dividend / Yield", [
                ("dividend_yield",     d.get("dividend_yield"),     "pct"),
                ("payout_ratio",       d.get("payout_ratio"),       "pct"),
                ("dividend_per_share", d.get("dividend_per_share"), "{:.2f}"),
                ("fcf_yield",          d.get("fcf_yield"),          "pct"),
            ]),
            ("Growth (5y CAGR)", [
                ("revenue_cagr_5y",  d.get("revenue_cagr_5y"),  "pct"),
                ("eps_cagr_5y",      d.get("eps_cagr_5y"),      "pct"),
                ("fcf_cagr_5y",      d.get("fcf_cagr_5y"),      "pct"),
                ("dividend_cagr_5y", d.get("dividend_cagr_5y"), "pct"),
            ]),
        ]
        for section_name, fields in sections:
            print(f"  {section_name}")
            for name, val, fmt in fields:
                if val is None:
                    display = "—"
                elif fmt == "pct":
                    display = _fmt_pct(val)
                else:
                    try:
                        display = fmt.format(val)
                    except (ValueError, TypeError):
                        display = str(val)
                print(f"    {name:<22s} {display}")
            print()
        return 0
    finally:
        con.close()


# ---------- coverage ----------

def cmd_coverage(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        # Universe
        rows = con.execute("""
            SELECT v.ref_instrument_id, ri.symbol, v.source AS in_list
            FROM v_relevant_instruments v
            LEFT JOIN ref_instruments ri USING (ref_instrument_id)
        """).fetchall()
        if not rows:
            print("Universe leer.")
            return 0

        # Was haben wir bereits in fundamentals?
        have = con.execute("""
            SELECT ref_instrument_id, ts, source FROM ref_fundamentals_latest
        """).fetchall()
        have_map = {r[0]: (r[1], r[2]) for r in have}

        # Pro instrument
        n_total = len({r[0] for r in rows})
        n_covered = sum(1 for r in rows if r[0] in have_map)
        n_stale = sum(1 for r in rows
                      if r[0] in have_map and have_map[r[0]][0] < date.today() - timedelta(days=10))

        print(f"==> Fundamentals-Coverage")
        print(f"    Universe (holdings + watchlists): {n_total} unique instruments")
        print(f"    With fundamentals snapshot       : {n_covered}/{n_total}  "
              f"({100.0*n_covered/n_total:.0f}%)")
        print(f"    Stale (>10 days old)             : {n_stale}")
        print()
        print(f"    Missing:")
        for r in rows:
            if r[0] not in have_map:
                print(f"      {r[0]:<22s}  symbol={r[1] or '?'}")
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("refresh", help="Single-symbol refresh")
    pr.add_argument("ref_instrument_id")
    pr.add_argument("--source", choices=["yfinance", "ib"], default=None,
                    help="Force source (default: auto — ib wenn available, sonst yfinance)")
    pr.add_argument("--run-id", default=None)

    pra = sub.add_parser("refresh-all", help="Refresh alle relevanten Instrumente")
    pra.add_argument("--source", choices=["yfinance", "ib"], default=None)
    pra.add_argument("--run-id", default=None)
    pra.add_argument("--since-days", type=int, default=None,
                     help="Wenn letztes Snapshot juenger als N Tage -> skip (default: kein skip)")

    ps = sub.add_parser("show", help="Pretty-print latest snapshot")
    ps.add_argument("ref_instrument_id")

    sub.add_parser("coverage", help="Universe-Coverage-Report")

    args = p.parse_args()

    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    dispatch = {
        "refresh":     cmd_refresh,
        "refresh-all": cmd_refresh_all,
        "show":        cmd_show,
        "coverage":    cmd_coverage,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
