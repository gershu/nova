"""nova-lab options-history CLI — historische Trend-Queries auf
mkt_options_snapshot.

Persistierung passiert als Side-Effect von screener_csp (jeder run
schreibt evaluierte Strikes). Diese CLI liest nur — kein DB-write.

Subcommands:
    show <ref_id>                                 Letzter Snapshot, alle Strikes
    strike <ref_id> --strike S --exp YYYY-MM-DD   Time-series fuer eine Option
    iv-trend <ref_id> [--days 30]                 IV-Median pro Tag (alle Strikes)
    premium-trend <ref_id> [--days 30]            Best-Yield pro Tag (Top-Strike-Proxy)

Beispiele:
    python -m modules.options_history show IB:AAPL:USD
    python -m modules.options_history strike IB:AAPL:USD --strike 275 --exp 2026-06-12
    python -m modules.options_history iv-trend IB:NVDA:USD --days 60
    python -m modules.options_history premium-trend IB:MSFT:USD --days 30
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from datetime import date, timedelta

import duckdb


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)


def _fmt(v, places=2):
    if v is None:
        return "—"
    return f"{v:,.{places}f}"


def _fmt_pct(v):
    if v is None:
        return "—"
    return f"{v:.2f}%"


# ---------- show ----------

def cmd_show(args) -> int:
    """Letzter snapshot fuer alle strikes des instruments."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = con.execute(
            """
            WITH latest_ts AS (
                SELECT MAX(ts) AS max_ts FROM mkt_options_snapshot WHERE ref_instrument_id = ?
            )
            SELECT s.expiration, s.strike, s."right", s.ts, s.bid, s.ask, s.iv,
                   s.volume, s.dte, s.underlying_spot
            FROM mkt_options_snapshot s, latest_ts l
            WHERE s.ref_instrument_id = ? AND s.ts = l.max_ts
            ORDER BY s.expiration, s.strike
            """,
            [args.ref_instrument_id, args.ref_instrument_id],
        ).fetchall()
    finally:
        con.close()

    if not rows:
        print(f"Keine Snapshots fuer '{args.ref_instrument_id}'.")
        print(f"(screener_csp muss erst gelaufen sein und das Instrument im csp_universe haben.)")
        return 0

    ts = rows[0][3]
    spot = rows[0][9]
    print(f"==> Latest snapshot fuer {args.ref_instrument_id}")
    print(f"    ts          : {ts}")
    print(f"    spot        : {_fmt(spot)}")
    print(f"    strikes     : {len(rows)}")
    print()
    print(f"{'expiration':<12s} {'strike':>8s} {'right':<5s} {'bid':>6s} {'ask':>6s} {'iv':>6s} {'volume':>8s} {'dte':>5s}")
    for exp, strike, right, _ts, bid, ask, iv, vol, dte, _spot in rows:
        print(
            f"{exp.isoformat():<12s} {_fmt(strike):>8s} {right:<5s} "
            f"{_fmt(bid):>6s} {_fmt(ask):>6s} {_fmt(iv, 4):>6s} "
            f"{(str(vol) if vol else '—'):>8s} {dte:>5d}"
        )
    return 0


# ---------- strike (time-series) ----------

def cmd_strike(args) -> int:
    """Time-series fuer (instrument, strike, expiration)."""
    exp = date.fromisoformat(args.exp)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT ts, bid, ask, iv, volume, underlying_spot, dte
            FROM mkt_options_snapshot
            WHERE ref_instrument_id = ?
              AND strike = ?
              AND expiration = ?
              AND "right" = ?
            ORDER BY ts
            """,
            [args.ref_instrument_id, args.strike, exp, args.right],
        ).fetchall()
    finally:
        con.close()

    if not rows:
        print(f"Keine Daten fuer {args.ref_instrument_id} strike={args.strike} exp={args.exp} right={args.right}")
        return 0

    print(f"==> Time-series fuer {args.ref_instrument_id} {args.right}-Strike {args.strike} exp={args.exp}")
    print(f"    {len(rows)} snapshots")
    print()
    print(f"{'ts':<12s} {'bid':>6s} {'ask':>6s} {'iv':>8s} {'volume':>8s} {'spot':>8s} {'dte':>5s}")
    for ts, bid, ask, iv, vol, spot, dte in rows:
        print(
            f"{ts.isoformat():<12s} {_fmt(bid):>6s} {_fmt(ask):>6s} "
            f"{_fmt(iv, 4):>8s} {(str(vol) if vol else '—'):>8s} "
            f"{_fmt(spot):>8s} {dte:>5d}"
        )

    # Quick-Stats
    bids = [r[1] for r in rows if r[1] is not None]
    if len(bids) >= 2:
        print()
        print(f"    bid trend  : {bids[0]:.2f} -> {bids[-1]:.2f}  ({((bids[-1]/bids[0]-1)*100):+.1f}%)")
    return 0


# ---------- iv-trend ----------

def cmd_iv_trend(args) -> int:
    """IV-Median pro Tag (alle Strikes, alle Expirations)."""
    since = date.today() - timedelta(days=args.days)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT ts,
                   COUNT(*) AS n_strikes,
                   MEDIAN(iv) AS iv_median,
                   AVG(iv) AS iv_avg,
                   MIN(iv) AS iv_min,
                   MAX(iv) AS iv_max
            FROM mkt_options_snapshot
            WHERE ref_instrument_id = ?
              AND ts >= ?
              AND iv IS NOT NULL
            GROUP BY ts
            ORDER BY ts
            """,
            [args.ref_instrument_id, since],
        ).fetchall()
    finally:
        con.close()

    if not rows:
        print(f"Keine IV-Daten fuer {args.ref_instrument_id} in den letzten {args.days} Tagen.")
        return 0

    print(f"==> IV-Trend fuer {args.ref_instrument_id} (letzte {args.days} Tage)")
    print()
    print(f"{'ts':<12s} {'n':>4s} {'median':>8s} {'avg':>8s} {'min':>8s} {'max':>8s}")
    for ts, n, med, avg, lo, hi in rows:
        print(
            f"{ts.isoformat():<12s} {n:>4d} "
            f"{_fmt(med, 4):>8s} {_fmt(avg, 4):>8s} "
            f"{_fmt(lo, 4):>8s} {_fmt(hi, 4):>8s}"
        )

    medians = [r[2] for r in rows if r[2] is not None]
    if len(medians) >= 2:
        first, last = medians[0], medians[-1]
        change = (last / first - 1) * 100 if first else 0
        print()
        print(f"    IV-median: {first:.4f} -> {last:.4f}  ({change:+.1f}%)")
    return 0


# ---------- premium-trend ----------

def cmd_premium_trend(args) -> int:
    """Best annualisierte Premium-Rendite pro Tag (Proxy fuer Premium-Niveau)."""
    since = date.today() - timedelta(days=args.days)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = con.execute(
            """
            WITH per_day AS (
                SELECT
                    ts,
                    strike,
                    expiration,
                    bid,
                    dte,
                    -- annualisierte Premium-Rendite, gleiche Formel wie engine
                    CASE WHEN strike > 0 AND dte > 0
                         THEN (bid / strike) * (365.0 / dte) * 100.0
                         ELSE NULL END AS ann_yield_pct
                FROM mkt_options_snapshot
                WHERE ref_instrument_id = ?
                  AND ts >= ?
                  AND bid IS NOT NULL AND bid > 0
                  AND "right" = 'P'
            ),
            best_per_day AS (
                SELECT ts,
                       MAX(ann_yield_pct) AS best_yield,
                       MEDIAN(ann_yield_pct) AS median_yield,
                       COUNT(*) AS n_strikes
                FROM per_day
                GROUP BY ts
            )
            SELECT ts, n_strikes, best_yield, median_yield
            FROM best_per_day
            ORDER BY ts
            """,
            [args.ref_instrument_id, since],
        ).fetchall()
    finally:
        con.close()

    if not rows:
        print(f"Keine Premium-Daten fuer {args.ref_instrument_id} in den letzten {args.days} Tagen.")
        return 0

    print(f"==> Premium-Trend (best ann.yield) fuer {args.ref_instrument_id} (letzte {args.days} Tage)")
    print()
    print(f"{'ts':<12s} {'n':>4s} {'best yield':>11s} {'median':>10s}")
    for ts, n, best, med in rows:
        print(
            f"{ts.isoformat():<12s} {n:>4d} "
            f"{_fmt_pct(best):>11s} {_fmt_pct(med):>10s}"
        )

    bests = [r[2] for r in rows if r[2] is not None]
    if len(bests) >= 2:
        first, last = bests[0], bests[-1]
        change = last - first
        print()
        print(f"    best-yield: {first:.2f}% -> {last:.2f}%  ({change:+.2f}%-points)")
    return 0


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser(description="nova-lab options-history CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show", help="Letzter snapshot alle strikes")
    p_show.add_argument("ref_instrument_id")

    p_strike = sub.add_parser("strike", help="Time-series fuer eine Option")
    p_strike.add_argument("ref_instrument_id")
    p_strike.add_argument("--strike", type=float, required=True)
    p_strike.add_argument("--exp", required=True, help="YYYY-MM-DD")
    p_strike.add_argument("--right", default="P", choices=["P", "C"])

    p_iv = sub.add_parser("iv-trend", help="IV-Median pro Tag")
    p_iv.add_argument("ref_instrument_id")
    p_iv.add_argument("--days", type=int, default=30)

    p_prem = sub.add_parser("premium-trend", help="Best annualisierte Premium-Rendite pro Tag")
    p_prem.add_argument("ref_instrument_id")
    p_prem.add_argument("--days", type=int, default=30)

    args = parser.parse_args()

    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    dispatcher = {
        "show":          cmd_show,
        "strike":        cmd_strike,
        "iv-trend":      cmd_iv_trend,
        "premium-trend": cmd_premium_trend,
    }
    return dispatcher[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
