"""Prompt-Engineering-Probe fuer Alert-Erklaerungen.

Liest EIN sig_alert (default: neuestes), holt yfinance-News fuer das Symbol,
baut Prompt, ruft LLM, printet Output. KEINE DB-Writes — pure Iteration.

Wenn Output-Qualitaet konsistent passt, wird das Pattern in lab_alert_explainer
promoted (mit DB-Persistenz in sig_alert_explanations + Digest-Integration).

Aufruf:
    python -m modules.llm.probe_alert                     # neuestes Alert in DB
    python -m modules.llm.probe_alert --symbol AAPL       # neuestes Alert fuer Symbol
    python -m modules.llm.probe_alert --max-news 5        # mehr News-Context
    python -m modules.llm.probe_alert --model llama3.1:8b # anderes Modell
    python -m modules.llm.probe_alert --json              # JSON-mode (sentiment+text)

Backup wenn keine News fuer Symbol verfuegbar (DACH/exotische):
  Probe laeuft trotzdem, prompt sagt explizit "keine News verfuegbar".
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import date, datetime, timezone

import duckdb

from .client import LLMError, OllamaClient
from .news_yfinance import fetch_news_yfinance, render_news_block


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)


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
"""

JSON_USER_PROMPT_TEMPLATE = USER_PROMPT_TEMPLATE + """
Antworte als JSON-Objekt mit Schluesseln:
  "explanation": String (2-3 Saetze)
  "sentiment":   String, einer von "negative", "neutral", "positive"
  "confidence":  Float 0..1 (wie sicher bist du dass die Erklaerung richtig ist?)
  "news_used":   Integer (wieviele der bereitgestellten News tatsaechlich relevant)
"""


def fetch_alert(con, symbol: str | None) -> dict | None:
    """Holt das neueste passende Alert. Returnt None wenn keins gefunden."""
    if symbol:
        row = con.execute(
            """
            SELECT
                a.ref_instrument_id, a.rule_name, a.direction, a.trigger_value,
                a.threshold, a.ts, a.details,
                r.symbol, r.name, r.asset_type, r.currency
            FROM sig_alerts a
            JOIN ref_instruments r ON r.ref_instrument_id = a.ref_instrument_id
            WHERE r.symbol = ?
            ORDER BY a.ts DESC, a.created_at DESC
            LIMIT 1
            """,
            [symbol],
        ).fetchone()
    else:
        row = con.execute(
            """
            SELECT
                a.ref_instrument_id, a.rule_name, a.direction, a.trigger_value,
                a.threshold, a.ts, a.details,
                r.symbol, r.name, r.asset_type, r.currency
            FROM sig_alerts a
            JOIN ref_instruments r ON r.ref_instrument_id = a.ref_instrument_id
            ORDER BY a.ts DESC, a.created_at DESC
            LIMIT 1
            """
        ).fetchone()

    if not row:
        return None

    return {
        "ref_instrument_id": row[0],
        "rule_name":         row[1],
        "direction":         row[2],
        "trigger_value":     row[3],
        "threshold":         row[4],
        "ts":                row[5],
        "details":           row[6],
        "symbol":            row[7],
        "name":              row[8],
        "asset_type":        row[9],
        "currency":          row[10],
    }


def build_prompt(alert: dict, news: list[dict], json_mode: bool) -> str:
    direction_str = f" ({alert['direction']})" if alert.get("direction") else ""
    template = JSON_USER_PROMPT_TEMPLATE if json_mode else USER_PROMPT_TEMPLATE
    return template.format(
        symbol         = alert["symbol"] or alert["ref_instrument_id"],
        name           = alert["name"] or "—",
        asset_type     = alert["asset_type"] or "stock",
        rule_name      = alert["rule_name"],
        direction_str  = direction_str,
        trigger_value  = alert["trigger_value"],
        ts             = alert["ts"].isoformat() if isinstance(alert["ts"], date) else str(alert["ts"]),
        news_block     = render_news_block(news),
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Alert-Erklaerung Prompt-Probe")
    p.add_argument("--symbol", help="Spezifisches Symbol (sonst neuestes Alert in DB)")
    p.add_argument("--max-news", type=int, default=3, help="Wieviele News-Items in den Prompt")
    p.add_argument("--model", help="LLM_DEFAULT_MODEL override")
    p.add_argument("--system", help="System-Prompt override (sonst SYSTEM_PROMPT konstant)")
    p.add_argument("--json", action="store_true", help="JSON-Mode (explanation+sentiment+confidence)")
    p.add_argument("--show-prompt", action="store_true", help="Druckt den finalen Prompt vor dem Call")
    args = p.parse_args()

    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        alert = fetch_alert(con, args.symbol)
    finally:
        con.close()

    if alert is None:
        print(f"FEHLER: kein Alert in sig_alerts gefunden{' fuer ' + args.symbol if args.symbol else ''}.", file=sys.stderr)
        print("       Erst lab_monitor laufen lassen.", file=sys.stderr)
        return 1

    print("==> Alert ausgewaehlt:")
    print(f"    {alert['symbol']:<10} ({alert['name']})")
    print(f"    Rule: {alert['rule_name']}  Direction: {alert['direction']}  Value: {alert['trigger_value']}  TS: {alert['ts']}")
    print()

    print(f"==> News holen (yfinance + RSS-Augment) fuer '{alert['symbol']}'/'{alert['name']}' (max {args.max_news})")
    news = fetch_news_yfinance(alert["symbol"], max_n=args.max_news, name=alert["name"])
    print(f"    {len(news)} Items gefunden")
    for n in news:
        print(f"    [{n['ts']}] ({n['publisher']}) {n['title'][:80]}")
    print()

    prompt = build_prompt(alert, news, json_mode=args.json)
    if args.show_prompt:
        print("==> Final prompt:")
        for line in prompt.splitlines():
            print(f"    {line}")
        print()

    system_prompt = args.system or SYSTEM_PROMPT

    print("==> LLM call ...")
    with OllamaClient(model=args.model) as llm:
        try:
            r = llm.generate(prompt, system=system_prompt, json_mode=args.json)
        except LLMError as e:
            print(f"FEHLER: {e}", file=sys.stderr)
            return 1

    print(f"    model      : {r.model}")
    print(f"    duration   : {r.duration_s:.2f}s")
    print(f"    eval_count : {r.eval_count} tokens")
    print(f"    speed      : {r.tps:.1f} tps")
    print()
    print("==> response:")
    if args.json:
        try:
            parsed = json.loads(r.text)
            print(json.dumps(parsed, indent=2, ensure_ascii=False))
        except json.JSONDecodeError as e:
            print(f"[WARN] JSON-Mode aber Output nicht parsbar: {e}", file=sys.stderr)
            print(r.text)
            return 1
    else:
        print(r.text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
