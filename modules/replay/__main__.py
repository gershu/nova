"""nova-lab Replay CLI — Historische Stress-Analyse mit heutigem Portfolio.

Subcommands:
    worst-day [--lookback-days 730] [--top 10]      Schlimmste Einzeltage
    worst-week [--lookback-days 730] [--top 10]     Schlimmste 5-Trading-Day-Windows
    drawdown [--lookback-days 730]                  Maximum-Drawdown peak->trough
    replay --from YYYY-MM-DD --to YYYY-MM-DD        Tag-fuer-Tag Time-Series
        [--csv]

Globale Flags:
    --base <CCY>                                    default EUR
    --source <ib|yfinance|...>                      explizite quote-source
                                                    (default: per-instrument preferred)
    --min-coverage <pct>                            default 80 — Tage mit weniger
                                                    coverage werden ignoriert
"""

from __future__ import annotations

import argparse
import csv
import os
import pathlib
import sys
from datetime import date, timedelta

import duckdb

from .engine import (
    DailyValue,
    MIN_COVERAGE_PCT_DEFAULT,
    day_deltas,
    max_drawdown,
    portfolio_value_series,
    worst_days,
    worst_weeks,
)


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
OUTPUT_DIR = pathlib.Path.home() / "nova_output" / "lab_replay"


def _fmt_num(v: float | None, places: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:,.{places}f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _connect_and_load_series(args) -> tuple[duckdb.DuckDBPyConnection, list[DailyValue]]:
    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        raise SystemExit(64)

    con = duckdb.connect(str(DB_PATH), read_only=True)

    # Sanity: portfolio vorhanden?
    n_holdings = con.execute(
        "SELECT count(*) FROM pos_holdings WHERE valid_to IS NULL"
    ).fetchone()[0]
    if not n_holdings:
        print("FEHLER: pos_holdings ist leer.", file=sys.stderr)
        raise SystemExit(64)

    # ts-range
    from_ts, to_ts = _resolve_ts_range(args)

    series = portfolio_value_series(
        con,
        base_currency=args.base.upper(),
        from_ts=from_ts,
        to_ts=to_ts,
        source=args.source,
        min_coverage_pct=args.min_coverage,
    )

    print(f"==> nova-lab replay")
    print(f"    base currency : {args.base}")
    print(f"    source        : {args.source or '(per-instrument preferred)'}")
    print(f"    holdings      : {n_holdings}")
    print(f"    range         : {from_ts} -> {to_ts}")
    print(f"    series points : {len(series)} (min_coverage_pct={args.min_coverage})")
    if not series:
        print("    [WARN] Series leer — Coverage zu niedrig oder keine Quotes im Range.")
    print()

    return con, series


def _resolve_ts_range(args) -> tuple[date, date]:
    """Subcommand-spezifisch. worst-day/week/drawdown nutzen lookback_days;
    replay nutzt explicit from/to."""
    if hasattr(args, "from_ts") and args.from_ts:
        from_ts = date.fromisoformat(args.from_ts)
    else:
        lb = getattr(args, "lookback_days", 730)
        from_ts = date.today() - timedelta(days=lb)
    if hasattr(args, "to_ts") and args.to_ts:
        to_ts = date.fromisoformat(args.to_ts)
    else:
        to_ts = date.today()
    return from_ts, to_ts


# ---------- worst-day ----------

def cmd_worst_day(args) -> int:
    con, series = _connect_and_load_series(args)
    try:
        worst = worst_days(series, top_n=args.top)
    finally:
        con.close()
    if not worst:
        print("Keine Tagesdeltas berechenbar.")
        return 0

    print(f"=== Top-{len(worst)} Worst Days ===")
    print(f"{'date':<12s} {'prev':<12s} {'before':>14s} {'after':>14s} {'Δ abs':>14s} {'Δ %':>8s} {'cov%':>6s}")
    for d in worst:
        print(
            f"{d.ts.isoformat():<12s} {d.prev_ts.isoformat():<12s} "
            f"{_fmt_num(d.total_before):>14s} {_fmt_num(d.total_after):>14s} "
            f"{_fmt_num(d.delta_abs):>14s} {_fmt_pct(d.delta_pct):>8s} "
            f"{d.coverage_pct:>5.0f}%"
        )
    return 0


# ---------- worst-week ----------

def cmd_worst_week(args) -> int:
    con, series = _connect_and_load_series(args)
    try:
        worst = worst_weeks(series, window=args.window, top_n=args.top)
    finally:
        con.close()
    if not worst:
        print("Keine Wochenfenster berechenbar.")
        return 0

    print(f"=== Top-{len(worst)} Worst {args.window}-Trading-Day Windows ===")
    print(f"{'from':<12s} {'to':<12s} {'before':>14s} {'after':>14s} {'Δ abs':>14s} {'Δ %':>8s}")
    for d in worst:
        print(
            f"{d.prev_ts.isoformat():<12s} {d.ts.isoformat():<12s} "
            f"{_fmt_num(d.total_before):>14s} {_fmt_num(d.total_after):>14s} "
            f"{_fmt_num(d.delta_abs):>14s} {_fmt_pct(d.delta_pct):>8s}"
        )
    return 0


# ---------- drawdown ----------

def cmd_drawdown(args) -> int:
    con, series = _connect_and_load_series(args)
    try:
        info = max_drawdown(series)
    finally:
        con.close()
    if info is None:
        print("Kein Drawdown berechenbar.")
        return 0

    print(f"=== Maximum Drawdown ({args.base}) ===")
    print(f"    Peak     : {info.peak_ts.isoformat()}  @ {_fmt_num(info.peak_val):>14s} {args.base}")
    print(f"    Trough   : {info.trough_ts.isoformat()}  @ {_fmt_num(info.trough_val):>14s} {args.base}")
    print(f"    Δ        : {_fmt_num(info.drawdown_abs):>14s} {args.base}  ({_fmt_pct(info.drawdown_pct)})")
    print(f"    Days     : {info.days_peak_to_trough} (peak->trough)")
    if info.recovered_ts:
        print(f"    Recovery : {info.recovered_ts.isoformat()}  ({info.days_to_recovery} days from trough)")
    else:
        print(f"    Recovery : noch nicht erreicht (peak {info.peak_val:,.2f} ueberschritten?)")
    return 0


# ---------- replay ----------

def cmd_replay(args) -> int:
    if not args.from_ts:
        print("FEHLER: replay braucht --from YYYY-MM-DD.", file=sys.stderr)
        return 64

    con, series = _connect_and_load_series(args)
    try:
        deltas = day_deltas(series)
    finally:
        con.close()

    if not series:
        return 0

    # Summary stats
    vals = [s.total_base for s in series if s.total_base is not None]
    min_val = min(vals) if vals else 0
    max_val = max(vals) if vals else 0
    start_val = series[0].total_base if series[0].total_base else 0
    end_val = series[-1].total_base if series[-1].total_base else 0
    total_pct = ((end_val / start_val - 1) * 100) if start_val else 0

    print(f"=== Replay Summary ({series[0].ts} -> {series[-1].ts}) ===")
    print(f"    points       : {len(series)} trading days")
    print(f"    start value  : {_fmt_num(start_val):>14s} {args.base}")
    print(f"    end value    : {_fmt_num(end_val):>14s} {args.base}")
    print(f"    min          : {_fmt_num(min_val):>14s} {args.base}")
    print(f"    max          : {_fmt_num(max_val):>14s} {args.base}")
    print(f"    period Δ     : {_fmt_pct(total_pct)}")
    if deltas:
        worst_d = min(deltas, key=lambda d: d.delta_pct)
        best_d = max(deltas, key=lambda d: d.delta_pct)
        print(f"    worst day    : {worst_d.ts.isoformat()}  {_fmt_pct(worst_d.delta_pct)}")
        print(f"    best day     : {best_d.ts.isoformat()}  {_fmt_pct(best_d.delta_pct)}")

    # CSV export
    if args.csv:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = OUTPUT_DIR / f"replay_{series[0].ts}_{series[-1].ts}.csv"
        with csv_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "total_base", "priced_lots", "total_lots", "coverage_pct", "delta_abs", "delta_pct"])
            delta_lookup = {d.ts: d for d in deltas}
            for s in series:
                d = delta_lookup.get(s.ts)
                w.writerow([
                    s.ts.isoformat(),
                    f"{s.total_base:.4f}" if s.total_base is not None else "",
                    s.priced_lots, s.total_lots,
                    f"{s.coverage_pct:.1f}",
                    f"{d.delta_abs:.4f}" if d else "",
                    f"{d.delta_pct:.4f}" if d else "",
                ])
        print()
        print(f"==> CSV: {csv_path}")

    return 0


# ---------- Main ----------

def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base", default="EUR", help="Base currency (default EUR)")
    p.add_argument("--source", default=None,
                   help="Quote-Source filter (default: per-instrument preferred)")
    p.add_argument("--min-coverage", dest="min_coverage", type=float,
                   default=MIN_COVERAGE_PCT_DEFAULT,
                   help=f"Min coverage_pct (default {MIN_COVERAGE_PCT_DEFAULT}). "
                        f"Tage mit weniger werden ignoriert.")


def main() -> int:
    parser = argparse.ArgumentParser(description="nova-lab Replay CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_wd = sub.add_parser("worst-day", help="N schlimmste Einzeltage in Lookback-Window")
    _add_common_args(p_wd)
    p_wd.add_argument("--lookback-days", type=int, default=730, dest="lookback_days")
    p_wd.add_argument("--top", type=int, default=10)
    p_wd.add_argument("--from", dest="from_ts", help="explicit start (override lookback)")
    p_wd.add_argument("--to", dest="to_ts", help="explicit end (default today)")

    p_ww = sub.add_parser("worst-week", help="N schlimmste rolling-N-Tag-Windows")
    _add_common_args(p_ww)
    p_ww.add_argument("--lookback-days", type=int, default=730, dest="lookback_days")
    p_ww.add_argument("--window", type=int, default=5, help="trading days (default 5)")
    p_ww.add_argument("--top", type=int, default=10)
    p_ww.add_argument("--from", dest="from_ts")
    p_ww.add_argument("--to", dest="to_ts")

    p_dd = sub.add_parser("drawdown", help="Maximum Drawdown peak->trough")
    _add_common_args(p_dd)
    p_dd.add_argument("--lookback-days", type=int, default=730, dest="lookback_days")
    p_dd.add_argument("--from", dest="from_ts")
    p_dd.add_argument("--to", dest="to_ts")

    p_rp = sub.add_parser("replay", help="Tag-fuer-Tag Time-Series ueber Range")
    _add_common_args(p_rp)
    p_rp.add_argument("--from", dest="from_ts", required=True, help="YYYY-MM-DD start")
    p_rp.add_argument("--to", dest="to_ts", default=None, help="YYYY-MM-DD end (default today)")
    p_rp.add_argument("--csv", action="store_true", help="zusaetzlich CSV-Export")

    args = parser.parse_args()

    dispatcher = {
        "worst-day":  cmd_worst_day,
        "worst-week": cmd_worst_week,
        "drawdown":   cmd_drawdown,
        "replay":     cmd_replay,
    }
    return dispatcher[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
