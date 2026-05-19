"""nova-lab market_monitor CLI — Z-Score-Alerts auf Economic Series.

Liest mkt_economic_series, berechnet pro Series Z-Score ueber rolling-Window,
schreibt Alerts in sig_alerts wenn |z| >= threshold. Pseudo-ref_instrument_id =
'FRED:<series_id>' (kein Eintrag in ref_instruments noetig — sig_alerts hat
keinen Hard-FK).

Subcommands:
    run     Z-Score-Check fuer alle aktiven Series, Alerts in sig_alerts
            schreiben.
    show    Aktuelle Z-Scores pro Series (read-only, kein DB-Write).

Beispiele:
    python -m modules.market_monitor run
    python -m modules.market_monitor run --window 90 --threshold 2.0
    python -m modules.market_monitor show
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import uuid
from datetime import date, datetime, timezone

import duckdb


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)

DEFAULT_WINDOW    = 90
DEFAULT_THRESHOLD = 2.0


# ---------- run ----------

def _compute_zscores(
    con: duckdb.DuckDBPyConnection,
    window: int,
) -> list[tuple]:
    """Returns latest Z-Score pro aktiver Series.

    Output-Row: (series_id, name, latest_ts, latest_value, mean_window,
                 std_window, z_score).
    """
    rows = con.execute("""
        WITH window_ts AS (
            -- Letzte 'window' Tage pro Series
            SELECT m.series_id, m.ts, m.value,
                   ROW_NUMBER() OVER (PARTITION BY m.series_id ORDER BY m.ts DESC) AS rk
            FROM mkt_economic_series m
            JOIN ref_economic_series s USING (series_id)
            WHERE s.active = TRUE
              AND m.source = 'fred'
        ),
        stats AS (
            SELECT series_id,
                   avg(value) AS mean_w,
                   stddev_samp(value) AS std_w
            FROM window_ts
            WHERE rk <= ?
            GROUP BY series_id
        ),
        latest AS (
            SELECT w.series_id, w.ts, w.value
            FROM window_ts w
            WHERE w.rk = 1
        )
        SELECT l.series_id,
               s.name,
               l.ts                                             AS latest_ts,
               l.value                                          AS latest_value,
               st.mean_w                                        AS mean_w,
               st.std_w                                         AS std_w,
               (l.value - st.mean_w) / NULLIF(st.std_w, 0)      AS z_score
        FROM latest l
        JOIN stats   st USING (series_id)
        JOIN ref_economic_series s USING (series_id)
        ORDER BY abs((l.value - st.mean_w) / NULLIF(st.std_w, 0)) DESC NULLS LAST
    """, [window]).fetchall()
    return rows


def cmd_run(args) -> int:
    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    run_id = f"market-monitor-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"

    con = duckdb.connect(str(DB_PATH))
    try:
        rows = _compute_zscores(con, args.window)
        if not rows:
            print("Keine Economic-Series-Daten vorhanden.")
            return 0

        n_alerts = 0
        print(f"==> market_monitor run  (window={args.window}d, threshold={args.threshold}σ, run_id={run_id})")
        print(f"    {'series_id':<14s} {'latest':>10s}  {'z-score':>10s}  alert")
        print(f"    {'-'*14} {'-'*10}  {'-'*10}  {'-'*5}")
        for (sid, name, ts, value, mean_w, std_w, z) in rows:
            if z is None:
                print(f"    {sid:<14s} {value:>10.4f}  {'(no std)':>10s}")
                continue
            triggered = abs(z) >= args.threshold
            badge = ("🔴" if z > 0 else "🔵") if triggered else "·"
            print(f"    {sid:<14s} {value:>10.4f}  {z:+9.2f}σ  {badge}")
            if not triggered:
                continue

            direction = "up" if z > 0 else "down"
            details = json.dumps({
                "window":  args.window,
                "mean":    round(mean_w, 6) if mean_w is not None else None,
                "std":     round(std_w,  6) if std_w  is not None else None,
                "value":   value,
                "z_score": round(z, 3),
                "series_name": name,
            })
            # ref_instrument_id-Konvention: 'FRED:<series>' — kein ref_instruments-Eintrag noetig
            ref_id = f"FRED:{sid}"
            # Idempotency-Check: gibt's heute schon einen zscore_high-Alert fuer
            # diese Series in dieser Richtung? PK ist (run_id, ...) — Tages-Dedup
            # muessen wir selbst machen.
            already = con.execute("""
                SELECT 1 FROM sig_alerts
                WHERE ref_instrument_id = ? AND rule_name = 'zscore_high'
                  AND direction = ? AND ts = ?
                LIMIT 1
            """, [ref_id, direction, ts]).fetchone()
            if already:
                continue
            con.execute("""
                INSERT INTO sig_alerts
                    (run_id, ref_instrument_id, rule_name, direction,
                     trigger_value, threshold, ts, details)
                VALUES (?, ?, 'zscore_high', ?, ?, ?, ?, ?)
            """, [run_id, ref_id, direction, z, args.threshold, ts, details])
            n_alerts += 1

        print(f"\n==> {n_alerts} Alerts geschrieben.")
        return 0
    finally:
        con.close()


# ---------- show ----------

def cmd_show(args) -> int:
    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = _compute_zscores(con, args.window)
        if not rows:
            print("Keine Economic-Series-Daten vorhanden.")
            return 0
        print(f"==> Z-Scores (window={args.window}d)")
        print(f"    {'series_id':<14s} {'latest_ts':<12s} {'value':>10s}  {'z-score':>10s}")
        print(f"    {'-'*14} {'-'*12} {'-'*10}  {'-'*10}")
        for (sid, name, ts, value, mean_w, std_w, z) in rows:
            z_str = f"{z:+9.2f}σ" if z is not None else "  (no std)"
            print(f"    {sid:<14s} {str(ts):<12s} {value:>10.4f}  {z_str:>10s}  {name}")
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Z-Score-Check + Alert-INSERT")
    p_run.add_argument("--window",    type=int,   default=DEFAULT_WINDOW)
    p_run.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)

    p_show = sub.add_parser("show", help="Aktuelle Z-Scores (read-only)")
    p_show.add_argument("--window", type=int, default=DEFAULT_WINDOW)

    args = p.parse_args()
    dispatch = {"run": cmd_run, "show": cmd_show}
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
