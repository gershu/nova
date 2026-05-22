"""nova-lab setup CLI — Trading-Setup-Detection.

Wertet die Setup-Definitionen aus config/setups.yaml gegen den aktuellen
DB-Zustand aus und schreibt aktive Setups nach sig_market_setups.

Subcommands:
    init         Schema applyen (sig_market_setups)
    run          Setups evaluieren + aktive in sig_market_setups schreiben
    show         Aktuell aktive Setups (latest ts) — read-only
    list-rules   Konfigurierte Setup-Regeln aus setups.yaml zeigen
    eval         Alle Setups auswerten + Bedingungen anzeigen (dry-run, kein Write)

Beispiele:
    python -m modules.setup init
    python -m modules.setup run
    python -m modules.setup eval        # Diagnose: was triggert + warum
    python -m modules.setup show
"""

from __future__ import annotations

import argparse
import json
import operator
import os
import pathlib
import sys
import uuid
from datetime import date, datetime, timezone

import duckdb
import yaml

from .metrics import METRICS


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
REPO_DIR    = pathlib.Path(__file__).parent.parent.parent
SQL_DIR     = pathlib.Path(__file__).parent / "sql"
CONFIG_FILE = REPO_DIR / "config" / "setups.yaml"

OPS = {
    ">":  operator.gt, ">=": operator.ge,
    "<":  operator.lt, "<=": operator.le,
    "==": operator.eq, "!=": operator.ne,
}


# ---------- Config ----------

def _load_setups() -> dict:
    if not CONFIG_FILE.is_file():
        print(f"FEHLER: {CONFIG_FILE} nicht gefunden.", file=sys.stderr)
        sys.exit(64)
    data = yaml.safe_load(CONFIG_FILE.read_text()) or {}
    return data.get("setups", {})


def _evaluate(con, spec: dict) -> tuple[bool, list[dict]]:
    """Wertet ein Setup aus. Returns (triggered, condition_results)."""
    match_mode = spec.get("match", "all")
    results: list[dict] = []
    for cond in spec.get("conditions", []):
        metric_name = cond["metric"]
        op_sym      = cond["op"]
        threshold   = cond["value"]
        fn = METRICS.get(metric_name)
        if fn is None:
            actual, passed = None, False
        else:
            actual = fn(con)
            passed = (actual is not None
                      and OPS[op_sym](actual, threshold))
        results.append({
            "metric":    metric_name,
            "op":        op_sym,
            "threshold": threshold,
            "actual":    round(actual, 4) if actual is not None else None,
            "passed":    passed,
        })
    triggered = (all(r["passed"] for r in results) if match_mode == "all"
                 else any(r["passed"] for r in results))
    return triggered, results


# ---------- init ----------

def cmd_init(args) -> int:
    sql_files = sorted(SQL_DIR.glob("0*.sql"))
    if not sql_files:
        print(f"FEHLER: keine SQL-Files in {SQL_DIR}", file=sys.stderr)
        return 64
    con = duckdb.connect(str(DB_PATH))
    try:
        for f in sql_files:
            con.execute(f.read_text())
            print(f"    ✓ {f.name}")
        return 0
    finally:
        con.close()


# ---------- run ----------

def cmd_run(args) -> int:
    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64
    setups = _load_setups()
    if not setups:
        print("Keine Setups in config/setups.yaml definiert.")
        return 0

    run_id = f"setup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    ts = date.today()

    con = duckdb.connect(str(DB_PATH))
    try:
        n_active = 0
        print(f"==> setup run  (ts={ts}, run_id={run_id})")
        for name, spec in setups.items():
            triggered, results = _evaluate(con, spec)
            mark = "🔴" if triggered else "·"
            print(f"    {mark} {name}")
            if not triggered:
                continue
            con.execute("""
                INSERT OR REPLACE INTO sig_market_setups
                    (setup_name, ts, severity, category, summary, details, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                name, ts,
                spec.get("severity", "info"),
                spec.get("category"),
                spec.get("summary"),
                json.dumps({"match": spec.get("match", "all"),
                             "conditions": results}),
                run_id,
            ])
            n_active += 1
        print(f"\n==> {n_active} aktive Setups geschrieben.")
        return 0
    finally:
        con.close()


# ---------- eval (dry-run) ----------

def cmd_eval(args) -> int:
    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64
    setups = _load_setups()
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        for name, spec in setups.items():
            triggered, results = _evaluate(con, spec)
            mark = "🔴 AKTIV " if triggered else "·  inaktiv"
            print(f"{mark}  {name}  ({spec.get('severity','info')})")
            for r in results:
                ok = "✓" if r["passed"] else "✗"
                actual = r["actual"] if r["actual"] is not None else "n/a"
                print(f"      {ok} {r['metric']} {r['op']} {r['threshold']}"
                      f"   (actual: {actual})")
            print()
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
        latest = con.execute("SELECT max(ts) FROM sig_market_setups").fetchone()
        if not latest or not latest[0]:
            print("Keine Setups in sig_market_setups. Erst 'run' ausfuehren.")
            return 0
        latest_ts = latest[0]
        rows = con.execute("""
            SELECT setup_name, severity, category, summary
            FROM sig_market_setups WHERE ts = ?
            ORDER BY CASE severity WHEN 'critical' THEN 1
                                   WHEN 'warning'  THEN 2
                                   ELSE 3 END, setup_name
        """, [latest_ts]).fetchall()
        print(f"==> Aktive Setups am {latest_ts}  ({len(rows)}):")
        print()
        for name, sev, cat, summary in rows:
            icon = {"critical": "🔴", "warning": "🟠", "info": "🟢"}.get(sev, "·")
            print(f"  {icon} [{sev:<8s}] {name:<26s} {cat or '':<10s} {summary or ''}")
        return 0
    finally:
        con.close()


# ---------- list-rules ----------

def cmd_list_rules(args) -> int:
    setups = _load_setups()
    if not setups:
        print("Keine Setups in config/setups.yaml.")
        return 0
    print(f"==> {len(setups)} Setup-Regeln in {CONFIG_FILE.name}:")
    print()
    for name, spec in setups.items():
        print(f"  {name}  [{spec.get('severity','info')} · {spec.get('category','?')}]")
        print(f"    {spec.get('summary','')}")
        match_mode = spec.get("match", "all")
        for cond in spec.get("conditions", []):
            print(f"    {match_mode}: {cond['metric']} {cond['op']} {cond['value']}")
        print()
    return 0


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init",       help="Schema applyen")
    sub.add_parser("run",        help="Setups evaluieren + schreiben")
    sub.add_parser("eval",       help="Dry-run — Bedingungen anzeigen, kein Write")
    sub.add_parser("show",       help="Aktuell aktive Setups")
    sub.add_parser("list-rules", help="Konfigurierte Setup-Regeln")

    args = p.parse_args()
    dispatch = {
        "init":       cmd_init,
        "run":        cmd_run,
        "eval":       cmd_eval,
        "show":       cmd_show,
        "list-rules": cmd_list_rules,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
