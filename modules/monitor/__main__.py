"""nova-lab monitor (B-Phase): liest mkt_quotes_daily, prueft Regeln,
schreibt Alerts in sig_alerts.

Schreibt jeden Alert in zwei Senken:
  - DB-Tabelle 'sig_alerts' (Audit + Trend-Auswertung)
  - CSV unter ~/nova_output/lab_monitor/alerts_<run_id>.csv

Aufruf:
  Lokal:    python -m modules.monitor
  Via nova: ~/nova/scripts/nova_run.sh    lab_monitor nova-hub --params-file <p.json>
            ~/nova/scripts/nova_submit.sh lab_monitor nova-hub --params-file <p.json>

Konfig (3-Tier):
  Tier 3 — JSON via NOVA_PARAMS_FILE:
    {
      "source":              "ib",                       // optional, default 'ib'
      "ref_instrument_ids":  ["IB:AAPL:USD", ...],       // optional explicit
      "symbols":             ["AAPL", ...],              // optional, matches ref_instruments.symbol
      "watchlist":           "active",                   // default
      "ts":                  "2026-05-02",               // optional, default = max(ts) in DB
      "rules": [                                         // optional, default = alle 4
        {"name": "daily_change_pct", "threshold": 5.0},
        {"name": "volume_spike", "lookback": 30, "threshold": 2.0},
        {"name": "high_low_52w", "window": 252},
        {"name": "sma_cross", "short": 10, "long": 50}
      ]
    }
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import sys
from datetime import date, datetime, timezone

import duckdb

from ..ingest.sources.base import Instrument
from .rules.base import Alert, Rule
from .rules.daily_change import DailyChangePctRule
from .rules.high_low_52w import HighLow52WRule
from .rules.sma_cross import SmaCrossRule
from .rules.volume_spike import VolumeSpikeRule

# ---------- Konfiguration ----------
DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SCHEMA_FILE = pathlib.Path(__file__).parent / "sql" / "0001_alerts.sql"
INGEST_SCHEMA_DIR = pathlib.Path(__file__).parent.parent / "ingest" / "sql"
OUTPUT_DIR = pathlib.Path.home() / "nova_output" / "lab_monitor"

RULES_REGISTRY: dict[str, type[Rule]] = {
    "daily_change_pct": DailyChangePctRule,
    "volume_spike":     VolumeSpikeRule,
    "high_low_52w":     HighLow52WRule,
    "sma_cross":        SmaCrossRule,
}

DEFAULT_RULE_CONFIG = [
    {"name": "daily_change_pct", "threshold": 5.0},
    {"name": "volume_spike", "lookback": 30, "threshold": 2.0},
    {"name": "high_low_52w", "window": 252},
    {"name": "sma_cross", "short": 10, "long": 50},
]


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
    """Laedt ingest-Schema (ref_instruments, mkt_quotes_*) + monitor-Schema (sig_alerts)."""
    if INGEST_SCHEMA_DIR.is_dir():
        for sql_file in sorted(INGEST_SCHEMA_DIR.glob("0*.sql")):
            con.execute(sql_file.read_text())
    con.execute(SCHEMA_FILE.read_text())


def resolve_instruments(con: duckdb.DuckDBPyConnection, params: dict) -> list[Instrument]:
    """Mirror der ingest-Funktion. Liefert Instrument-Objekte aus ref_instruments."""
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
            raise ValueError(f"Unbekannte watchlist '{watchlist}'.")

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


def resolve_ts(con: duckdb.DuckDBPyConnection, params: dict, source: str) -> date:
    if params.get("ts"):
        return date.fromisoformat(params["ts"])
    row = con.execute("SELECT MAX(ts) FROM mkt_quotes_daily WHERE source = ?", [source]).fetchone()
    if not row or not row[0]:
        raise ValueError("Keine Quotes in DB — ingest zuerst laufen lassen.")
    return row[0]


def build_rules(rule_configs: list[dict]) -> list[Rule]:
    rules: list[Rule] = []
    for cfg in rule_configs:
        name = cfg.get("name")
        if name not in RULES_REGISTRY:
            print(f"[WARN] Unbekannte Regel '{name}' uebersprungen.", file=sys.stderr)
            continue
        params = {k: v for k, v in cfg.items() if k != "name"}
        rules.append(RULES_REGISTRY[name](**params))
    return rules


def write_alerts_to_db(
    con: duckdb.DuckDBPyConnection,
    alerts: list[Alert],
    run_id: str,
) -> int:
    if not alerts:
        return 0
    rows = [
        (
            run_id, a.ref_instrument_id, a.rule_name, a.direction,
            a.trigger_value, a.threshold, a.ts, a.details,
        )
        for a in alerts
    ]
    con.executemany(
        """
        INSERT OR REPLACE INTO sig_alerts
        (run_id, ref_instrument_id, rule_name, direction, trigger_value, threshold, ts, details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def write_alerts_to_csv(alerts: list[Alert], run_id: str) -> pathlib.Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"alerts_{run_id}.csv"
    with out_file.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "run_id", "ref_instrument_id", "rule_name", "direction",
            "trigger_value", "threshold", "ts", "details",
        ])
        for a in alerts:
            writer.writerow([
                run_id, a.ref_instrument_id, a.rule_name, a.direction or "",
                a.trigger_value if a.trigger_value is not None else "",
                a.threshold if a.threshold is not None else "",
                a.ts.isoformat(),
                a.details or "",
            ])
    return out_file


# ---------- Main ----------

def main() -> int:
    params = load_params()
    source = params.get("source", "ib")
    rule_configs = params.get("rules", DEFAULT_RULE_CONFIG)

    run_id = os.environ.get(
        "NOVA_JOB_ID",
        f"adhoc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))

    try:
        ensure_schema(con)

        try:
            instruments = resolve_instruments(con, params)
        except ValueError as e:
            print(f"FEHLER: {e}", file=sys.stderr)
            return 64

        if not instruments:
            print("FEHLER: keine Instruments aufgeloest.", file=sys.stderr)
            return 64

        try:
            ts = resolve_ts(con, params, source)
        except ValueError as e:
            print(f"FEHLER: {e}", file=sys.stderr)
            return 64

        rules = build_rules(rule_configs)
        if not rules:
            print("FEHLER: keine Regeln konfiguriert.", file=sys.stderr)
            return 64

        print("==> nova-lab monitor (B-Phase)")
        print(f"    source       : {source}")
        print(f"    ts           : {ts}")
        print(f"    instruments  : {len(instruments)}")
        sample = ", ".join(i.symbol for i in instruments[:8])
        if len(instruments) > 8:
            sample += "..."
        print(f"                   {sample}")
        print(f"    rules        : {', '.join(r.name for r in rules)}")
        print(f"    db           : {DB_PATH}")
        print(f"    run_id       : {run_id}")

        all_alerts: list[Alert] = []
        per_rule_count: dict[str, int] = {r.name: 0 for r in rules}

        for inst in instruments:
            for rule in rules:
                try:
                    alerts = rule.evaluate(con, inst, ts, source)
                except Exception as e:  # noqa: BLE001
                    print(f"    [ERR] {inst.symbol}/{rule.name}: {e.__class__.__name__}: {e}", file=sys.stderr)
                    continue
                for a in alerts:
                    all_alerts.append(a)
                    per_rule_count[a.rule_name] = per_rule_count.get(a.rule_name, 0) + 1
                    val = "" if a.trigger_value is None else f" value={a.trigger_value}"
                    direction = "" if not a.direction else f" {a.direction}"
                    print(f"    [ALERT] {inst.symbol} ({inst.ref_instrument_id}) {a.rule_name}{direction}{val}")

        n_db = write_alerts_to_db(con, all_alerts, run_id)
        csv_file = write_alerts_to_csv(all_alerts, run_id)

        print()
        print(f"==> done: {len(all_alerts)} alerts insgesamt")
        for rname, n in per_rule_count.items():
            print(f"    {rname:24s}: {n}")
        print(f"    db_rows : {n_db}")
        print(f"    csv     : {csv_file}")

    finally:
        con.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
