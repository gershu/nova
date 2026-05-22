"""nova-lab recommendations — LLM-Recommendation-Layer.

Hebt die verstreuten Signale (Portfolio-Zustand, aktive Markt-Setups,
Portfolio-Alerts) zu konkreten, begruendeten Handlungs-Vorschlaegen.
Bleibt human-in-loop: Vorschlaege, keine Order.

Persistiert in sig_recommendations.

Subcommands:
    init      Schema applyen
    run       Recommendations generieren (LLM-Call)
    show      Letzte Recommendations anzeigen

Konfig (NOVA_PARAMS_FILE JSON, optional):
    {"model": "qwen2.5:14b-instruct-q4_K_M", "ts": "2026-05-22"}

Beispiele:
    python -m modules.llm.recommendations init
    python -m modules.llm.recommendations run
    python -m modules.llm.recommendations show
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import duckdb

from ..client import LLMError, OllamaClient


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SQL_DIR = pathlib.Path(__file__).parent / "sql"

VALID_ACTIONS = {"review", "trim", "add", "hedge", "watch", "rebalance", "no_action"}
VALID_PRIORITY = {"high", "medium", "low"}


SYSTEM_PROMPT = (
    "Du bist Chief Investment Officer fuer ein Privatanleger-Portfolio.\n"
    "Aufgabe: aus Portfolio-Zustand, aktiven Markt-Setups und Alerts "
    "konkrete, BEGRUENDETE Handlungs-Vorschlaege ableiten.\n"
    "\n"
    "GRUNDREGELN:\n"
    "- Human-in-loop: du gibst VORSCHLAEGE, keine Order. Der Anleger "
    "  entscheidet und fuehrt selbst aus.\n"
    "- Jeder Vorschlag MUSS sich auf die gegebenen Daten stuetzen (Setup, "
    "  Alert, Positions-Gewicht). Erfinde KEINE Fakten, KEINE Nachrichten.\n"
    "- Konservativ: wenn nichts dringend ist, gib WENIGE oder KEINE "
    "  Vorschlaege. Eine leere Liste ist eine valide, ehrliche Antwort.\n"
    "- Sprache abwaegend ('erwaegen', 'pruefen'), nicht befehlend.\n"
    "- Kein Hype, kein Drama, keine Performance-Versprechen.\n"
    "\n"
    "ACTION-Vokabular (genau eines pro Vorschlag):\n"
    "  review     — Position genauer pruefen\n"
    "  trim       — Position-Groesse reduzieren erwaegen\n"
    "  add        — aufstocken erwaegen (selten, nur bei klarer Begruendung)\n"
    "  hedge      — Absicherung erwaegen\n"
    "  watch      — beobachten, noch keine Aktion\n"
    "  rebalance  — Allokation anpassen erwaegen\n"
    "  no_action  — explizit: kein Handlungsbedarf"
)


# ---------- Params / Schema ----------

def load_params() -> dict:
    pf = os.environ.get("NOVA_PARAMS_FILE")
    if pf and pathlib.Path(pf).is_file():
        try:
            return json.loads(pathlib.Path(pf).read_text())
        except json.JSONDecodeError:
            pass
    return {}


def cmd_init(args) -> int:
    sql_files = sorted(SQL_DIR.glob("0*.sql"))
    con = duckdb.connect(str(DB_PATH))
    try:
        for f in sql_files:
            con.execute(f.read_text())
            print(f"    ✓ {f.name}")
        return 0
    finally:
        con.close()


# ---------- Daten sammeln ----------

def fetch_portfolio_snapshot(con: duckdb.DuckDBPyConnection) -> dict:
    """Top-Positionen (aggregiert pro Instrument) mit Gewicht + PnL, EUR."""
    rows = con.execute("""
        SELECT symbol, name,
               SUM(mtm_eur)  AS mv_eur,
               SUM(pnl_eur)  AS pnl_eur,
               SUM(cost_total_eur) AS cost_eur
        FROM v_mkt_holdings
        WHERE mtm_eur IS NOT NULL
        GROUP BY symbol, name
        ORDER BY mv_eur DESC
    """).fetchall()
    total = sum(r[2] for r in rows if r[2] is not None) or 0.0
    positions = []
    for sym, name, mv, pnl, cost in rows:
        positions.append({
            "symbol":     sym,
            "name":       name,
            "mv_eur":     mv,
            "pnl_eur":    pnl,
            "weight_pct": (mv / total * 100.0) if total else 0.0,
            "pnl_pct":    (pnl / cost * 100.0) if cost else None,
        })
    return {"total_eur": total, "positions": positions}


def fetch_active_setups(con: duckdb.DuckDBPyConnection) -> list[dict]:
    try:
        latest = con.execute("SELECT max(ts) FROM sig_market_setups").fetchone()
    except duckdb.CatalogException:
        return []
    if not latest or not latest[0]:
        return []
    rows = con.execute("""
        SELECT setup_name, severity, category, summary
        FROM sig_market_setups WHERE ts = ?
        ORDER BY CASE severity WHEN 'critical' THEN 1
                               WHEN 'warning'  THEN 2 ELSE 3 END
    """, [latest[0]]).fetchall()
    return [{"name": r[0], "severity": r[1], "category": r[2], "summary": r[3]}
            for r in rows]


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone() is not None


def fetch_portfolio_alerts(con: duckdb.DuckDBPyConnection, ts: date) -> list[dict]:
    """Alerts der letzten 7 Tage auf gehaltenen Positionen.

    sig_alert_explanations wird nur gejoint wenn die Tabelle existiert —
    sonst lieferte ein fehlender Join still [] zurueck und wuerde echte
    Alerts verschlucken.
    """
    if not _table_exists(con, "sig_alerts"):
        return []
    since = (ts - timedelta(days=7)).isoformat()
    has_expl = _table_exists(con, "sig_alert_explanations")

    if has_expl:
        sql = """
            SELECT a.ts, COALESCE(i.symbol, a.ref_instrument_id) AS symbol,
                   a.rule_name, a.direction, a.trigger_value,
                   e.explanation, e.sentiment
            FROM sig_alerts a
            LEFT JOIN ref_instruments i USING (ref_instrument_id)
            LEFT JOIN sig_alert_explanations e
                   ON e.ref_instrument_id = a.ref_instrument_id
                  AND e.rule_name         = a.rule_name
                  AND e.direction         = COALESCE(a.direction, '')
                  AND e.ts                = a.ts
            WHERE a.ts >= ?
              AND a.ref_instrument_id IN (
                  SELECT DISTINCT ref_instrument_id FROM pos_holdings WHERE valid_to IS NULL
              )
            ORDER BY a.ts DESC
        """
    else:
        sql = """
            SELECT a.ts, COALESCE(i.symbol, a.ref_instrument_id) AS symbol,
                   a.rule_name, a.direction, a.trigger_value,
                   NULL AS explanation, NULL AS sentiment
            FROM sig_alerts a
            LEFT JOIN ref_instruments i USING (ref_instrument_id)
            WHERE a.ts >= ?
              AND a.ref_instrument_id IN (
                  SELECT DISTINCT ref_instrument_id FROM pos_holdings WHERE valid_to IS NULL
              )
            ORDER BY a.ts DESC
        """
    rows = con.execute(sql, [since]).fetchall()
    return [{"ts": str(r[0]), "symbol": r[1], "rule_name": r[2],
             "direction": r[3], "trigger_value": r[4],
             "explanation": r[5], "sentiment": r[6]} for r in rows]


# ---------- Prompt ----------

def build_user_prompt(ts: date, snap: dict, setups: list[dict],
                       alerts: list[dict]) -> str:
    lines = [
        f"DATUM: {ts.isoformat()}",
        "",
        f"PORTFOLIO (Total {snap['total_eur']:,.0f} EUR, "
        f"{len(snap['positions'])} Positionen):",
    ]
    for p in snap["positions"][:12]:
        pnl_p = f"{p['pnl_pct']:+.1f}%" if p["pnl_pct"] is not None else "—"
        lines.append(
            f"  {p['symbol']:<10s} {p['weight_pct']:5.1f}%  "
            f"MV {p['mv_eur']:>12,.0f} EUR  PnL {pnl_p}"
        )

    lines.append("")
    lines.append("AKTIVE MARKT-SETUPS:")
    if setups:
        for s in setups:
            lines.append(f"  [{s['severity']}] {s['name']} ({s['category']}): {s['summary']}")
    else:
        lines.append("  (keine aktiven Setups)")

    lines.append("")
    lines.append("PORTFOLIO-ALERTS (letzte 7 Tage):")
    if alerts:
        for a in alerts:
            dir_s = f" {a['direction']}" if a["direction"] else ""
            lines.append(f"  {a['ts']}  {a['symbol']:<10s} {a['rule_name']}{dir_s}")
            if a["explanation"]:
                exp = a["explanation"][:180].replace("\n", " ")
                lines.append(f"      Kontext: {exp}")
    else:
        lines.append("  (keine Alerts auf gehaltenen Positionen)")

    lines.append("")
    lines.append("AUFGABE:")
    lines.append("Leite 0-5 konkrete Handlungs-Vorschlaege ab. Jeder Vorschlag")
    lines.append("stuetzt sich auf ein konkretes Setup, einen Alert oder ein")
    lines.append("Positions-Merkmal aus den Daten oben. Wenn nichts dringend")
    lines.append("ist: leere Liste.")
    lines.append("")
    lines.append("Antworte als JSON-Objekt mit Schluessel 'recommendations',")
    lines.append("einer Liste von Objekten mit:")
    lines.append('  "category":  "position" | "risk" | "market" | "opportunity"')
    lines.append('  "symbol":    Ticker-Symbol oder null (portfolio-/markt-weit)')
    lines.append('  "action":    review | trim | add | hedge | watch | rebalance | no_action')
    lines.append('  "priority":  "high" | "medium" | "low"')
    lines.append('  "title":     String, 1 Zeile (max 80 Zeichen)')
    lines.append('  "rationale": String, 1-3 Saetze Begruendung mit Bezug auf die Daten')
    return "\n".join(lines)


# ---------- Persist ----------

def write_recommendations(con, ts: date, model: str, run_id: str,
                           recs: list[dict], based_on: dict) -> int:
    # Symbol -> ref_instrument_id Lookup (nur eindeutige Treffer)
    sym_map: dict[str, str] = {}
    for sym, rid, cnt in con.execute("""
        SELECT symbol, any_value(ref_instrument_id), count(*)
        FROM ref_instruments GROUP BY symbol
    """).fetchall():
        if cnt == 1:
            sym_map[sym] = rid

    con.execute("DELETE FROM sig_recommendations WHERE ts = ? AND model = ?",
                [ts, model])

    based_on_json = json.dumps(based_on)
    n = 0
    for i, rec in enumerate(recs, start=1):
        action   = str(rec.get("action", "review")).lower()
        priority = str(rec.get("priority", "medium")).lower()
        if action not in VALID_ACTIONS:
            action = "review"
        if priority not in VALID_PRIORITY:
            priority = "medium"
        symbol = rec.get("symbol")
        ref_id = sym_map.get(symbol) if symbol else None
        con.execute("""
            INSERT INTO sig_recommendations
                (ts, model, rec_id, category, ref_instrument_id, symbol,
                 action, priority, title, rationale, based_on, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            ts, model, i,
            rec.get("category"), ref_id, symbol,
            action, priority,
            rec.get("title"), rec.get("rationale"),
            based_on_json, run_id,
        ])
        n += 1
    return n


# ---------- run ----------

def cmd_run(args) -> int:
    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64
    params = load_params()
    model = (params.get("model")
             or os.environ.get("LLM_DEFAULT_MODEL", "qwen2.5:14b-instruct-q4_K_M"))
    run_id = f"rec-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"

    con = duckdb.connect(str(DB_PATH))
    try:
        ts = (date.fromisoformat(params["ts"]) if params.get("ts") else date.today())

        snap   = fetch_portfolio_snapshot(con)
        if not snap["positions"]:
            print("FEHLER: Portfolio leer (v_mkt_holdings).", file=sys.stderr)
            return 64
        setups = fetch_active_setups(con)
        alerts = fetch_portfolio_alerts(con, ts)

        prompt = build_user_prompt(ts, snap, setups, alerts)
        based_on = {
            "setups": [s["name"] for s in setups],
            "alerts": [f"{a['symbol']}:{a['rule_name']}" for a in alerts],
            "n_positions": len(snap["positions"]),
        }

        print(f"==> recommendations run  (ts={ts}, model={model})")
        print(f"    Input: {len(snap['positions'])} Positionen, "
              f"{len(setups)} Setups, {len(alerts)} Alerts")
        print("==> LLM call ...")

        with OllamaClient(model=model) as llm:
            try:
                r = llm.generate(prompt, system=SYSTEM_PROMPT, json_mode=True)
            except LLMError as e:
                print(f"FEHLER: {e}", file=sys.stderr)
                return 1
        try:
            parsed = json.loads(r.text)
        except json.JSONDecodeError as e:
            print(f"FEHLER: LLM-Output kein valides JSON: {e}", file=sys.stderr)
            print(r.text, file=sys.stderr)
            return 1

        recs = parsed.get("recommendations", [])
        if not isinstance(recs, list):
            print("FEHLER: 'recommendations' ist keine Liste.", file=sys.stderr)
            return 1

        n = write_recommendations(con, ts, model, run_id, recs, based_on)
        print(f"    duration: {r.duration_s:.1f}s · tokens: {r.eval_count}")
        print(f"==> {n} Recommendations geschrieben: sig_recommendations({ts}, {model})")
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
        latest = con.execute("SELECT max(ts) FROM sig_recommendations").fetchone()
        if not latest or not latest[0]:
            print("Keine Recommendations. Erst 'run' ausfuehren.")
            return 0
        rows = con.execute("""
            SELECT rec_id, priority, category, symbol, action, title, rationale
            FROM sig_recommendations WHERE ts = ?
            ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2
                                   ELSE 3 END, rec_id
        """, [latest[0]]).fetchall()
        print(f"==> Recommendations vom {latest[0]}  ({len(rows)}):")
        print()
        for rid, prio, cat, sym, action, title, rationale in rows:
            icon = {"high": "🔴", "medium": "🟠", "low": "🟢"}.get(prio, "·")
            tgt = f" [{sym}]" if sym else ""
            print(f"  {icon} #{rid} [{action}]{tgt}  {title}")
            if rationale:
                print(f"       {rationale}")
            print()
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="Schema applyen")
    sub.add_parser("run",  help="Recommendations generieren (LLM-Call)")
    sub.add_parser("show", help="Letzte Recommendations anzeigen")

    args = p.parse_args()
    dispatch = {"init": cmd_init, "run": cmd_run, "show": cmd_show}
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
