"""nova-lab alert_explainer (L3b): pro sig_alert -> News + LLM-Erklaerung.

Liest sig_alerts vom Tag, holt yfinance.news pro Symbol, ruft LLM im
JSON-Mode (sentiment + confidence + news_used + explanation), persistiert
in sig_alert_explanations.

Default-Verhalten: ueberspringe alerts die schon Erklaerungen vom selben
Modell haben (idempotent). --force erzwingt Neu-Generierung.

Aufruf:
  Lokal:    python -m modules.llm.alert_explainer
  Via nova: ~/nova/scripts/nova_run.sh    lab_alert_explainer nova-hub --params-file <p.json>
            ~/nova/scripts/nova_submit.sh lab_alert_explainer nova-hub --params-file <p.json>

Konfig (3-Tier):
  Tier 3 — JSON via NOVA_PARAMS_FILE:
    {
      "ts":        "2026-05-08",                         // optional, default = MAX(ts) in sig_alerts
      "model":     "qwen2.5:14b-instruct-q4_K_M",        // optional, default = LLM_DEFAULT_MODEL
      "max_news":  3,                                    // optional, default 3
      "force":     false,                                // optional, default false (skip already-done)
      "max_alerts":50                                    // optional, safety-cap (default 50)
    }
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from datetime import date, datetime, timezone

import duckdb

from ..client import LLMError, OllamaClient
from ..news_yfinance import fetch_news_yfinance, render_news_block


# ---------- Konfiguration ----------
DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
MONITOR_SCHEMA_DIR = pathlib.Path(__file__).parent.parent.parent / "monitor" / "sql"


SYSTEM_PROMPT = (
    "Du bist ein knapper Finanz-Analyst. Deine Aufgabe: einen Marktdaten-Alert "
    "in 2-3 Saetzen einordnen, gestuetzt auf bereitgestellte Nachrichten. "
    "Wenn die Nachrichten keinen plausiblen Zusammenhang zum Alert haben, sag "
    "das ehrlich. Erfinde NIEMALS Fakten. Antworte auf Deutsch."
)


USER_PROMPT_TEMPLATE = """Alert:
  Symbol      : {symbol} ({name})
  Asset-Class : {asset_type}
  Regel       : {rule_name}{direction_str}
  Wert        : {trigger_value}
  Datum       : {ts}

Nachrichten zum Symbol (juengste zuerst):
{news_block}

Erklaere den Alert in 2-3 Saetzen, basierend auf den Nachrichten.
Wenn keine Nachrichten verfuegbar sind oder kein Zusammenhang erkennbar ist, sag das.

Antworte als JSON-Objekt mit Schluesseln:
  "explanation": String (2-3 Saetze deutsch)
  "sentiment":   String, einer von "negative", "neutral", "positive"
  "confidence":  Float 0..1 (wie sicher bist du dass die Erklaerung richtig ist?)
  "news_used":   Integer (wieviele der bereitgestellten News tatsaechlich relevant)
"""


# ---------- Hilfsfunktionen ----------

def load_params() -> dict:
    pf = os.environ.get("NOVA_PARAMS_FILE")
    if not pf:
        return {}
    p = pathlib.Path(pf)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        print(f"[WARN] params file ist kein gueltiges JSON: {e}", file=sys.stderr)
        return {}


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Laedt monitor-Schema (sig_alerts + sig_alert_explanations)."""
    if MONITOR_SCHEMA_DIR.is_dir():
        for sql_file in sorted(MONITOR_SCHEMA_DIR.glob("0*.sql")):
            con.execute(sql_file.read_text())


def resolve_ts(con: duckdb.DuckDBPyConnection, params: dict) -> date:
    if params.get("ts"):
        return date.fromisoformat(params["ts"])
    row = con.execute("SELECT MAX(ts) FROM sig_alerts").fetchone()
    if not row or not row[0]:
        raise ValueError("Keine sig_alerts in DB — erst lab_monitor laufen lassen.")
    return row[0]


def fetch_alerts_to_explain(
    con: duckdb.DuckDBPyConnection,
    ts: date,
    model: str,
    force: bool,
    max_alerts: int,
) -> list[dict]:
    """Holt sig_alerts vom Tag, joint ref_instruments fuer symbol/name.
    Wenn force=False, skip alerts die schon Erklaerung von diesem Modell haben."""
    sql = """
        SELECT DISTINCT
            a.ref_instrument_id,
            a.rule_name,
            COALESCE(a.direction, '') AS direction,
            a.ts,
            a.trigger_value,
            a.threshold,
            r.symbol,
            r.name,
            r.asset_type
        FROM sig_alerts a
        JOIN ref_instruments r ON r.ref_instrument_id = a.ref_instrument_id
        WHERE a.ts = ?
    """
    params: list = [ts]
    if not force:
        sql += """
          AND NOT EXISTS (
            SELECT 1 FROM sig_alert_explanations e
            WHERE e.ref_instrument_id = a.ref_instrument_id
              AND e.rule_name         = a.rule_name
              AND e.direction         = COALESCE(a.direction, '')
              AND e.ts                = a.ts
              AND e.model             = ?
          )
        """
        params.append(model)
    sql += " ORDER BY r.symbol, a.rule_name LIMIT ?"
    params.append(max_alerts)

    rows = con.execute(sql, params).fetchall()
    return [
        {
            "ref_instrument_id": r[0],
            "rule_name":         r[1],
            "direction":         r[2],
            "ts":                r[3],
            "trigger_value":     r[4],
            "threshold":         r[5],
            "symbol":            r[6],
            "name":              r[7],
            "asset_type":        r[8],
        }
        for r in rows
    ]


def build_prompt(alert: dict, news: list[dict]) -> str:
    direction_str = f" ({alert['direction']})" if alert["direction"] else ""
    return USER_PROMPT_TEMPLATE.format(
        symbol         = alert["symbol"] or alert["ref_instrument_id"],
        name           = alert["name"] or "—",
        asset_type     = alert["asset_type"] or "stock",
        rule_name      = alert["rule_name"],
        direction_str  = direction_str,
        trigger_value  = alert["trigger_value"],
        ts             = alert["ts"].isoformat() if isinstance(alert["ts"], date) else str(alert["ts"]),
        news_block     = render_news_block(news),
    )


def write_explanation(
    con: duckdb.DuckDBPyConnection,
    alert: dict,
    news_count: int,
    parsed: dict,
    duration_s: float,
    eval_tokens: int,
    model: str,
    run_id: str,
) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO sig_alert_explanations
            (ref_instrument_id, rule_name, direction, ts,
             model, explanation, sentiment, confidence,
             news_count, news_used, eval_tokens, duration_s,
             run_id, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            alert["ref_instrument_id"],
            alert["rule_name"],
            alert["direction"],
            alert["ts"],
            model,
            (parsed.get("explanation") or "")[:2000],          # safety-cap
            (parsed.get("sentiment") or "").lower()[:20] or None,
            float(parsed.get("confidence", 0)) if parsed.get("confidence") is not None else None,
            news_count,
            int(parsed.get("news_used", 0)) if parsed.get("news_used") is not None else None,
            eval_tokens,
            duration_s,
            run_id,
            datetime.now(timezone.utc),
        ],
    )


# ---------- Main ----------

def main() -> int:
    params = load_params()
    model = params.get("model") or os.environ.get("LLM_DEFAULT_MODEL", "qwen2.5:14b-instruct-q4_K_M")
    max_news = int(params.get("max_news", 3))
    force = bool(params.get("force", False))
    max_alerts = int(params.get("max_alerts", 50))

    run_id = os.environ.get(
        "NOVA_JOB_ID",
        f"adhoc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))

    try:
        ensure_schema(con)

        try:
            ts = resolve_ts(con, params)
        except ValueError as e:
            print(f"FEHLER: {e}", file=sys.stderr)
            return 64

        alerts = fetch_alerts_to_explain(con, ts, model, force, max_alerts)

        print("==> nova-lab alert_explainer")
        print(f"    ts          : {ts}")
        print(f"    model       : {model}")
        print(f"    max_news    : {max_news}")
        print(f"    max_alerts  : {max_alerts}")
        print(f"    force       : {force}")
        print(f"    run_id      : {run_id}")
        print(f"    db          : {DB_PATH}")
        print(f"    alerts to do: {len(alerts)}")

        if not alerts:
            print()
            print("==> nichts zu tun (alle alerts haben schon erklaerungen vom selben modell, oder keine alerts da).")
            return 0

        ok_count   = 0
        fail_count = 0
        no_news    = 0
        total_tokens = 0
        total_dur    = 0.0

        with OllamaClient(model=model) as llm:
            for ix, alert in enumerate(alerts, start=1):
                sym = alert["symbol"] or alert["ref_instrument_id"]
                print(f"    [{ix:>2d}/{len(alerts)}] {sym:<10} {alert['rule_name']:<18} {alert['direction'] or '':<8} ", end="", flush=True)

                # News (yfinance + RSS-Augment fuer DACH-Coverage)
                news = fetch_news_yfinance(sym, max_n=max_news, name=alert.get("name"))
                if not news:
                    no_news += 1

                # LLM
                prompt = build_prompt(alert, news)
                try:
                    r = llm.generate(prompt, system=SYSTEM_PROMPT, json_mode=True)
                except LLMError as e:
                    print(f"LLM-Fail: {e.__class__.__name__}: {e}")
                    fail_count += 1
                    continue

                # Parse
                try:
                    parsed = json.loads(r.text)
                except json.JSONDecodeError as e:
                    print(f"JSON-Parse-Fail: {e}")
                    fail_count += 1
                    continue

                write_explanation(
                    con, alert,
                    news_count=len(news),
                    parsed=parsed,
                    duration_s=r.duration_s,
                    eval_tokens=r.eval_count,
                    model=model,
                    run_id=run_id,
                )
                ok_count += 1
                total_tokens += r.eval_count
                total_dur += r.duration_s

                sentiment = parsed.get("sentiment", "?")
                confidence = parsed.get("confidence", 0)
                news_used = parsed.get("news_used", 0)
                print(f"OK news={len(news)} used={news_used} sentiment={sentiment} conf={confidence:.2f} {r.duration_s:.1f}s")

                # Polite zwischen yfinance + ollama
                time.sleep(0.2)

        print()
        print(f"==> done")
        print(f"    ok           : {ok_count}")
        print(f"    failed       : {fail_count}")
        print(f"    alerts ohne news: {no_news}")
        print(f"    total tokens : {total_tokens}")
        print(f"    total time   : {total_dur:.1f}s  (avg {total_dur/ok_count:.1f}s/alert)" if ok_count else "")
        return 0 if fail_count == 0 else 2

    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
