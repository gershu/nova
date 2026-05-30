"""nova-lab health CLI.

Subcommands:
    status [--group G]    Tabelle pro Daemon (fresh/stale/failed/up/down)
    detail LABEL          Letzte 5 Audit-Runs + Log-Tail eines Daemons
    run                   Snapshot in sig_health_snapshots persistieren
                          (vom Daily-Daemon aufgerufen)
    watch [--interval S]  status alle N Sekunden refreshen

Environment:
    LAB_DB_PATH    optional — default ~/nova_data/lab.duckdb
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

import duckdb

from .reader import (
    DaemonStatus, check_all, check_one, load_manifest, summary,
)


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SQL_DIR = pathlib.Path(__file__).parent / "sql"
SQL_FILES = sorted(SQL_DIR.glob("0*.sql"))


def apply_schema(con: duckdb.DuckDBPyConnection, *, verbose: bool = False) -> None:
    for f in SQL_FILES:
        con.execute(f.read_text())
        if verbose:
            print(f"    ✓ {f.name}")


# ---------- Tabellen-Helfer fuer CLI-Output ----------

_OVERALL_GLYPH = {
    "fresh":   "✓",
    "up":      "✓",
    "stale":   "⚠",
    "failed":  "✗",
    "down":    "✗",
    "unknown": "?",
}

# Reihenfolge: gut zu schlecht, fuer Sortierung im Output
_OVERALL_ORDER = {
    "failed": 0, "down": 1, "stale": 2, "unknown": 3, "fresh": 4, "up": 5,
}


def _fmt_ts(ts) -> str:
    if ts is None:
        return "—"
    if isinstance(ts, str):
        return ts[:16]
    return ts.strftime("%Y-%m-%d %H:%M")


def _fmt_status(s: DaemonStatus) -> str:
    g = _OVERALL_GLYPH.get(s.overall, "?")
    return f"{g} {s.overall}"


def _print_table(statuses: list[DaemonStatus], *, group_filter: str | None) -> None:
    rows = statuses if not group_filter else [
        s for s in statuses if s.group == group_filter]
    if not rows:
        print(f"(keine Daemons{' in Gruppe ' + group_filter if group_filter else ''})")
        return

    # Spalten-Breiten
    w_grp   = max(len(s.group) for s in rows + [
        DaemonStatus("","group","","")])
    w_title = max(len(s.title) for s in rows)
    w_stat  = max(len(_fmt_status(s)) for s in rows)
    w_age   = 10  # "23h fresh" o.ae.

    # Header
    print(f"  {'GROUP':<{w_grp}}  {'DAEMON':<{w_title}}  "
          f"{'STATUS':<{w_stat}}  {'LAST/AGE':<{w_age}}  METRIC")
    print(f"  {'-'*w_grp}  {'-'*w_title}  {'-'*w_stat}  "
          f"{'-'*w_age}  {'-'*40}")

    # Sort: schlechtester zuerst, dann nach Group + Title
    rows.sort(key=lambda s: (_OVERALL_ORDER.get(s.overall, 99),
                              s.group, s.title))
    for s in rows:
        age = ""
        if s.last_run_ts is not None and s.age_hours is not None:
            if s.age_hours < 24:
                age = f"{int(s.age_hours)}h"
            elif s.age_hours < 24 * 14:
                age = f"{int(s.age_hours / 24)}d"
            else:
                age = f"{int(s.age_hours / 24 / 7)}w"
        elif s.overall in ("up", "down"):
            age = "—"
        metric = s.metric or s.detail or ""
        print(f"  {s.group:<{w_grp}}  {s.title:<{w_title}}  "
              f"{_fmt_status(s):<{w_stat}}  {age:<{w_age}}  {metric}")

    s = summary(statuses)
    print(f"\n  SUMMARY: {s['total']} daemons · {s['fresh']} fresh · "
          f"{s['stale']} stale · {s['failed']} failed · "
          f"{s['up']} up · {s['down']} down · {s['unknown']} unknown")


# ---------- Subcommands ----------

def cmd_status(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        statuses = check_all(con)
    finally:
        con.close()
    _print_table(statuses, group_filter=args.group)
    bad = sum(1 for s in statuses
               if s.overall in ("failed", "down", "stale"))
    return 0 if bad == 0 else 2


def cmd_detail(args) -> int:
    """Letzte Runs + Log-Tail eines einzelnen Daemons."""
    manifest = load_manifest()
    target = next((d for d in manifest.get("daemons", [])
                    if d["label"] == args.label), None)
    if not target:
        print(f"FEHLER: Label '{args.label}' nicht im Manifest.",
              file=sys.stderr)
        return 64
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        s = check_one(con, target)
        print(f"==> {s.title}  ({s.label})")
        print(f"    Gruppe   : {s.group}")
        print(f"    Schedule : {s.schedule}")
        print(f"    Status   : {_fmt_status(s)}")
        if s.last_run_ts:
            print(f"    Letzter Lauf: {_fmt_ts(s.last_run_ts)}  "
                  f"({s.age_hours:.1f}h alt)" if s.age_hours
                  else f"    Letzter Lauf: {_fmt_ts(s.last_run_ts)}")
        if s.metric:
            print(f"    Metric   : {s.metric}")
        if s.detail:
            print(f"    Detail   : {s.detail}")

        if target.get("audit_table"):
            print(f"\n    Letzte 5 Runs aus {target['audit_table']}:")
            try:
                ts_col = "ts" if _ts_col_exists(con, target["audit_table"], "ts") \
                    else "finished_at"
                df = con.execute(
                    f"SELECT {ts_col}, status, * "
                    f"EXCLUDE ({ts_col}, status) FROM {target['audit_table']} "
                    f"ORDER BY {ts_col} DESC LIMIT 5"
                ).df()
                if not df.empty:
                    print(df.to_string(index=False))
            except Exception as e:  # noqa: BLE001
                print(f"      (Audit-Query fail: {e.__class__.__name__})")
    finally:
        con.close()

    log = target.get("log_path") or _guess_log_path(target["label"])
    if log and pathlib.Path(log).is_file():
        print(f"\n    Log-Tail ({log}):")
        try:
            r = subprocess.run(["tail", "-n", "20", log],
                                capture_output=True, text=True, timeout=3)
            for line in (r.stdout or "").splitlines():
                print(f"      {line}")
        except subprocess.SubprocessError:
            print("      (tail failed)")
    return 0


def _ts_col_exists(con, table, col):
    return bool(con.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name=? AND column_name=?", [table, col]).fetchone())


def _guess_log_path(label: str) -> str | None:
    """Konvention: /Users/novaadm/Library/Logs/nova-<label-dotted-by-dash>.log."""
    candidate = pathlib.Path(
        f"/Users/novaadm/Library/Logs/nova-{label.replace('.', '-')}.log")
    return str(candidate)


def cmd_run(args) -> int:
    """Snapshot in sig_health_snapshots persistieren (Daily-Daemon)."""
    run_id = (f"hlth-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
              f"-{uuid.uuid4().hex[:6]}")
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    con = duckdb.connect(str(DB_PATH))
    try:
        apply_schema(con)
        statuses = check_all(con)
        s = summary(statuses)
        details = [{
            "label":           x.label,
            "title":           x.title,
            "group":           x.group,
            "overall":         x.overall,
            "last_run_ts":     str(x.last_run_ts) if x.last_run_ts else None,
            "last_run_status": x.last_run_status,
            "age_hours":       x.age_hours,
            "metric":          x.metric,
            "detail":          x.detail,
        } for x in statuses]
        con.execute("""
            INSERT INTO sig_health_snapshots
                (run_id, ts, total_daemons, fresh_count, stale_count,
                 failed_count, down_count, unknown_count, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [run_id, started, s["total"], s["fresh"], s["stale"],
              s["failed"], s["down"], s["unknown"],
              json.dumps(details, default=str)])
        print(f"==> snapshot {run_id}")
        print(f"    {s['total']} daemons · {s['fresh']} fresh · "
              f"{s['stale']} stale · {s['failed']} failed · "
              f"{s['up']} up · {s['down']} down · {s['unknown']} unknown")
        # Sortierte Tabelle als Logging-Output (fuers Daemon-Logfile lesbar)
        _print_table(statuses, group_filter=None)
        return 0
    finally:
        con.close()


def cmd_watch(args) -> int:
    """Endlosschleife — status alle N Sekunden."""
    while True:
        os.system("clear")
        print(f"  nova-lab health · {datetime.now().strftime('%H:%M:%S')} "
              f"(refresh alle {args.interval}s, Ctrl+C zum Beenden)")
        print()
        con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            statuses = check_all(con)
        finally:
            con.close()
        _print_table(statuses, group_filter=args.group)
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_st = sub.add_parser("status", help="Tabelle pro Daemon")
    p_st.add_argument("--group", default=None,
        help="Nur diese Gruppe anzeigen (market_data/market_signals/llm/…)")

    p_d = sub.add_parser("detail", help="Letzte Runs + Log eines Daemons")
    p_d.add_argument("label", help="z.B. lab.fred_ingest")

    sub.add_parser("run", help="Snapshot persistieren (Daily-Daemon)")

    p_w = sub.add_parser("watch", help="status periodisch refreshen")
    p_w.add_argument("--interval", type=int, default=30,
        help="Sekunden zwischen Refresh (default 30)")
    p_w.add_argument("--group", default=None)

    args = p.parse_args()
    if not DB_PATH.is_file() and args.cmd != "run":
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}.", file=sys.stderr)
        return 64

    dispatch = {"status": cmd_status, "detail": cmd_detail,
                 "run": cmd_run, "watch": cmd_watch}
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
