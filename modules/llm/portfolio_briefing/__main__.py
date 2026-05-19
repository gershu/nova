"""nova-lab portfolio_briefing — taeglicher LLM-Brief zum Portfolio.

Synthetisiert: Per-Currency-Snapshot heute vs gestern, Top-Movers,
Alerts heute auf gehaltenen Werten, Alert-Erklaerungen.
Generiert 2-3 Absaetze Markdown-Briefing in JSON-Mode.
Persistiert in sig_portfolio_briefings.

Aufruf:
  Lokal:    python -m modules.llm.portfolio_briefing
  Via nova: ~/nova/scripts/nova_run.sh    lab_portfolio_briefing nova-hub --params-file <p.json>
            ~/nova/scripts/nova_submit.sh lab_portfolio_briefing nova-hub --params-file <p.json>

Konfig (3-Tier):
  Tier 3 — JSON via NOVA_PARAMS_FILE:
    {
      "ts":            "2026-05-08",                      // optional, default = MAX(ts) in mkt_quotes_daily
      "model":         "qwen2.5:14b-instruct-q4_K_M",     // optional, default = LLM_DEFAULT_MODEL
      "base_currency": "EUR",                             // optional, default 'EUR'
      "force":         false,                             // optional, default false (skip wenn schon vorhanden)
      "max_movers":    5,                                 // optional, default 5
      "max_alerts":    20                                 // optional, default 20
    }
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import date, datetime, timezone

import duckdb

from ..client import LLMError, OllamaClient


# ---------- Konfiguration ----------
DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
MONITOR_SCHEMA_DIR = pathlib.Path(__file__).parent.parent.parent / "monitor" / "sql"


SYSTEM_PROMPT = (
    "Du bist persoenlicher Chief Investment Officer fuer ein "
    "Privatanleger-Portfolio. Aufgabe: taegliches Briefing in GENAU drei "
    "Absaetzen, getrennt durch Leerzeile. Sachlich, knapp, auf Deutsch.\n"
    "\n"
    "STRENG VERBOTEN:\n"
    "- Empfehlungen jeglicher Art ('weiter beobachten', 'Anleger sollten', "
    "  'es lohnt sich', 'attraktiv', 'Kaufgelegenheit') — HART-Policy.\n"
    "- Erfinden von Marktnachrichten oder Kontext, der nicht im Prompt steht.\n"
    "  Speziell: wenn ein Alert-Kontext sagt 'keine relevanten Nachrichten', "
    "  dann musst du das Briefing entsprechend formulieren ('ohne erkennbaren "
    "  Newshintergrund') und NICHT 'positive Marktinformationen' o.ae. erfinden.\n"
    "- Hype, Drama, emotionale Sprache.\n"
    "\n"
    "ERLAUBT und ERWUENSCHT:\n"
    "- Faktische Beobachtungen ('AAPL gab 7% ab', 'Volumen 3x ueber Schnitt').\n"
    "- Ehrliches Eingestaendnis bei fehlenden Daten ('ohne News-Erklaerung').\n"
    "- Multi-Currency-Aspekte: WENN das Portfolio USD/EUR/NOK-Anteile hat, "
    "  erwaehne mindestens kurz die FX-Lage in Absatz 3."
)


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
    if MONITOR_SCHEMA_DIR.is_dir():
        for sql_file in sorted(MONITOR_SCHEMA_DIR.glob("0*.sql")):
            con.execute(sql_file.read_text())


def resolve_ts(con: duckdb.DuckDBPyConnection, params: dict) -> date:
    if params.get("ts"):
        return date.fromisoformat(params["ts"])
    row = con.execute("SELECT MAX(ts) FROM mkt_quotes_daily").fetchone()
    if not row or not row[0]:
        raise ValueError("Keine quotes in DB — erst lab_ingest laufen lassen.")
    return row[0]


def previous_trading_day(con: duckdb.DuckDBPyConnection, ts: date) -> date | None:
    row = con.execute(
        "SELECT MAX(ts) FROM mkt_quotes_daily WHERE ts < ?",
        [ts],
    ).fetchone()
    return row[0] if row and row[0] else None


# ---------- Daten-Sammlung ----------

def fetch_per_currency_totals(
    con: duckdb.DuckDBPyConnection,
    ts: date,
    prev_ts: date | None,
    base_currency: str,
) -> dict:
    """Pro Currency: total in local + base, today + prev. Plus delta."""
    # Latest quote pro instrument <= ts
    sql = """
        WITH ranked AS (
            SELECT ref_instrument_id, ts, close,
                   ROW_NUMBER() OVER (PARTITION BY ref_instrument_id ORDER BY ts DESC) AS rn
            FROM mkt_quotes_daily
            WHERE ts <= ?
        ),
        latest AS (SELECT ref_instrument_id, close AS last_close FROM ranked WHERE rn = 1)
        SELECT h.currency, SUM(h.quantity * l.last_close) AS total_local
        FROM pos_holdings h
        LEFT JOIN latest l ON l.ref_instrument_id = h.ref_instrument_id
        WHERE l.last_close IS NOT NULL
        GROUP BY h.currency
    """
    rows_today = con.execute(sql, [ts]).fetchall()

    rows_prev = []
    if prev_ts:
        rows_prev = con.execute(sql, [prev_ts]).fetchall()

    # FX rates: ccy -> rate-to-base for ts and prev_ts
    fx_today = _fx_rates(con, base_currency, ts)
    fx_prev = _fx_rates(con, base_currency, prev_ts) if prev_ts else fx_today

    out: dict[str, dict] = {}
    for ccy, total_local in rows_today:
        out[ccy] = {
            "ccy":           ccy,
            "total_local":   total_local,
            "total_base":    total_local * fx_today.get(ccy, 1.0 if ccy == base_currency else None) if fx_today.get(ccy) is not None or ccy == base_currency else None,
            "prev_local":    None,
            "prev_base":     None,
        }
    for ccy, total_local in rows_prev:
        if ccy not in out:
            out[ccy] = {"ccy": ccy, "total_local": None, "total_base": None}
        rate = fx_prev.get(ccy, 1.0 if ccy == base_currency else None)
        out[ccy]["prev_local"] = total_local
        out[ccy]["prev_base"] = total_local * rate if rate is not None else None

    # Delta
    for ccy, d in out.items():
        d["delta_base"] = (
            (d["total_base"] - d["prev_base"])
            if d.get("total_base") is not None and d.get("prev_base") is not None
            else None
        )
        d["delta_pct"] = (
            ((d["total_base"] / d["prev_base"] - 1) * 100)
            if d.get("total_base") is not None and d.get("prev_base") not in (None, 0)
            else None
        )
    return out


def _fx_rates(con: duckdb.DuckDBPyConnection, base: str, ts: date | None) -> dict[str, float]:
    rates: dict[str, float] = {base: 1.0}
    if ts is None:
        return rates
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT currency_from, ts, rate,
                   ROW_NUMBER() OVER (PARTITION BY currency_from ORDER BY ts DESC) AS rn
            FROM mkt_fx_daily
            WHERE currency_to = ? AND ts <= ?
        )
        SELECT currency_from, rate FROM ranked WHERE rn = 1
        """,
        [base, ts],
    ).fetchall()
    for ccy, rate in rows:
        if ccy and rate is not None:
            rates[ccy] = float(rate)
    return rates


def fetch_top_movers(
    con: duckdb.DuckDBPyConnection,
    ts: date,
    n: int = 5,
) -> list[dict]:
    """Top N Movers im Portfolio nach abs(% change) heute vs gestern.
    Filtert auf Holdings (NICHT alle Watchlist)."""
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT ref_instrument_id, ts, close,
                   ROW_NUMBER() OVER (PARTITION BY ref_instrument_id ORDER BY ts DESC) AS rn
            FROM mkt_quotes_daily
            WHERE ts <= ?
        ),
        today AS    (SELECT ref_instrument_id, close AS close_today FROM ranked WHERE rn = 1),
        yesterday AS(SELECT ref_instrument_id, close AS close_prev  FROM ranked WHERE rn = 2),
        in_portfolio AS (SELECT DISTINCT ref_instrument_id FROM pos_holdings)
        SELECT
            r.symbol,
            r.currency,
            t.close_today,
            (t.close_today / y.close_prev - 1) * 100 AS d_day_pct
        FROM today t
        JOIN yesterday y USING (ref_instrument_id)
        JOIN in_portfolio p USING (ref_instrument_id)
        LEFT JOIN ref_instruments r ON r.ref_instrument_id = t.ref_instrument_id
        WHERE y.close_prev > 0
        ORDER BY abs(d_day_pct) DESC
        LIMIT ?
        """,
        [ts, n],
    ).fetchall()
    return [
        {"symbol": r[0], "currency": r[1], "close": r[2], "pct": r[3]}
        for r in rows
    ]


def fetch_alerts_for_holdings(
    con: duckdb.DuckDBPyConnection,
    ts: date,
    max_n: int = 20,
) -> list[dict]:
    """sig_alerts vom Tag joinen mit pos_holdings (nur fuer gehaltene Werte).
    DISTINCT damit multiple Monitor-Runs nicht doppeln.
    has_context = (news_used > 0) — wird im Prompt zur Bucket-Trennung genutzt
    damit LLM keine Markt-Gruende fuer alerts ohne News erfindet."""
    rows = con.execute(
        """
        SELECT DISTINCT
            r.symbol,
            a.rule_name,
            a.direction,
            a.trigger_value,
            COALESCE(e.explanation, '') AS explanation,
            COALESCE(e.sentiment, '')   AS sentiment,
            COALESCE(e.confidence, 0)   AS confidence,
            COALESCE(e.news_used, 0)    AS news_used
        FROM sig_alerts a
        JOIN pos_holdings p ON p.ref_instrument_id = a.ref_instrument_id
        LEFT JOIN ref_instruments r ON r.ref_instrument_id = a.ref_instrument_id
        LEFT JOIN sig_alert_explanations e
          ON e.ref_instrument_id = a.ref_instrument_id
         AND e.rule_name         = a.rule_name
         AND e.direction         = COALESCE(a.direction, '')
         AND e.ts                = a.ts
        WHERE a.ts = ?
        ORDER BY r.symbol, a.rule_name
        LIMIT ?
        """,
        [ts, max_n],
    ).fetchall()
    return [
        {
            "symbol":        r[0],
            "rule_name":     r[1],
            "direction":     r[2] or "",
            "trigger_value": r[3],
            "explanation":   r[4],
            "sentiment":     r[5],
            "confidence":    r[6],
            "news_used":     int(r[7] or 0),
            "has_context":   int(r[7] or 0) > 0,
        }
        for r in rows
    ]


# ---------- Prompt-Bau ----------

def build_user_prompt(
    ts: date,
    base_currency: str,
    per_ccy: dict,
    movers: list[dict],
    alerts: list[dict],
) -> str:
    # Total over all currencies
    total_today = sum(d["total_base"] for d in per_ccy.values() if d.get("total_base") is not None)
    total_prev  = sum(d["prev_base"]  for d in per_ccy.values() if d.get("prev_base")  is not None)
    delta_abs   = total_today - total_prev if total_prev else 0
    delta_pct   = ((total_today / total_prev - 1) * 100) if total_prev else 0

    lines = [
        f"DATUM: {ts.isoformat()}",
        f"BASE-CURRENCY: {base_currency}",
        "",
        f"PORTFOLIO-SNAPSHOT (in {base_currency}):",
        f"  Total heute        : {total_today:>14,.2f} {base_currency}",
        f"  Total Vortag       : {total_prev:>14,.2f} {base_currency}",
        f"  Δ Tag              : {delta_abs:>+14,.2f} {base_currency}  ({delta_pct:+.2f}%)",
        "",
        f"PER-CURRENCY-AUFTEILUNG:",
    ]
    for ccy in sorted(per_ccy.keys()):
        d = per_ccy[ccy]
        tot = d.get("total_base")
        delta = d.get("delta_base")
        delta_p = d.get("delta_pct")
        delta_s = f"{delta:+,.2f} ({delta_p:+.2f}%)" if delta is not None and delta_p is not None else "—"
        tot_s = f"{tot:>12,.2f}" if tot is not None else "       —"
        lines.append(f"  {ccy:<5s}: {tot_s} {base_currency}   Δ {delta_s}")

    lines.append("")
    lines.append("TOP MOVERS HEUTE (im Portfolio, nach |Δ %|):")
    if movers:
        for m in movers:
            lines.append(f"  {m['symbol']:<10s} {m['pct']:+6.2f}%  (close {m['close']:.2f} {m['currency']})")
    else:
        lines.append("  (keine relevanten Bewegungen)")

    # Trennung in zwei Buckets: alerts MIT News-Context vs OHNE.
    # LLM tendiert zu halluzinieren wenn es alerts ohne Context sieht —
    # explizite Trennung macht den Unterschied unmissverstaendlich.
    with_ctx    = [a for a in alerts if a.get("has_context")]
    without_ctx = [a for a in alerts if not a.get("has_context")]

    lines.append("")
    lines.append("ALERTS MIT erkennbarem News-Context (Erklaerungen DURFEN genutzt werden):")
    if with_ctx:
        for a in with_ctx:
            val_s = f" value={a['trigger_value']:.4g}" if a.get("trigger_value") is not None else ""
            dir_s = f" {a['direction']}" if a.get("direction") else ""
            lines.append(f"  - {a['symbol']:<10s} {a['rule_name']:<18s}{dir_s}{val_s}")
            if a.get("explanation"):
                exp = a["explanation"][:200].replace("\n", " ")
                sent = f" [{a['sentiment']}, conf={a['confidence']:.0%}]" if a.get("sentiment") else ""
                lines.append(f"      → {exp}{sent}")
    else:
        lines.append("  (keine)")

    lines.append("")
    lines.append("ALERTS OHNE News-Context (NIEMALS Marktgruende dafuer erfinden — nur faktisch nennen):")
    if without_ctx:
        for a in without_ctx:
            val_s = f" value={a['trigger_value']:.4g}" if a.get("trigger_value") is not None else ""
            dir_s = f" {a['direction']}" if a.get("direction") else ""
            lines.append(f"  - {a['symbol']:<10s} {a['rule_name']:<18s}{dir_s}{val_s}  (KEIN News-Context)")
    else:
        lines.append("  (keine)")

    lines.append("")
    lines.append("AUFGABE:")
    lines.append("Schreibe ein Briefing in GENAU drei Absaetzen (durch Leerzeile getrennt).")
    lines.append("  Absatz 1: Was ist heute im Portfolio passiert — die wichtigsten Bewegungen.")
    lines.append("            Faktisch, mit Zahlen.")
    lines.append("  Absatz 2: Welche Alerts heute aufgetreten sind und WAS DER ALERT-KONTEXT SAGT.")
    lines.append("            Wenn Alert-Context 'keine Nachrichten' sagt, schreibe 'ohne erkennbaren Newshintergrund'.")
    lines.append("            NIEMALS dazu Markt-Vermutungen erfinden.")
    lines.append("  Absatz 3: Gesamtbild — Multi-Currency-Verteilung (FX-Effekt erwaehnen wenn nicht-EUR-Anteil >0)")
    lines.append("            und beobachtete Risiken auf Sentiment-Ebene. KEINE Empfehlungen.")
    lines.append("")
    lines.append("Antworte als JSON-Objekt mit Schluesseln:")
    lines.append('  "headline":   String (1 Zeile, max 80 Zeichen, kein "##" / Markdown)')
    lines.append('  "body":       String (2-3 Absaetze Markdown, deutsche Sprache)')
    lines.append('  "sentiment":  String, einer von "negative" / "neutral" / "positive"')
    lines.append('  "confidence": Float 0..1 (wie sicher die Einschaetzung ist)')

    return "\n".join(lines)


# ---------- Persist ----------

def write_briefing(
    con: duckdb.DuckDBPyConnection,
    ts: date,
    model: str,
    base_currency: str,
    portfolio_total: float,
    delta_abs: float,
    delta_pct: float,
    holdings_count: int,
    alerts_count: int,
    parsed: dict,
    eval_tokens: int,
    duration_s: float,
    run_id: str,
) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO sig_portfolio_briefings
            (ts, model, base_currency, portfolio_total, delta_abs_day, delta_pct_day,
             holdings_count, alerts_count,
             headline, body, sentiment, confidence,
             eval_tokens, duration_s, run_id, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ts, model, base_currency, portfolio_total, delta_abs, delta_pct,
            holdings_count, alerts_count,
            (parsed.get("headline") or "")[:200],
            (parsed.get("body") or "")[:5000],
            (parsed.get("sentiment") or "").lower()[:20] or None,
            float(parsed.get("confidence", 0)) if parsed.get("confidence") is not None else None,
            eval_tokens, duration_s, run_id, datetime.now(timezone.utc),
        ],
    )


# ---------- Main ----------

def main() -> int:
    params = load_params()
    model = params.get("model") or os.environ.get("LLM_DEFAULT_MODEL", "qwen2.5:14b-instruct-q4_K_M")
    base_currency = (params.get("base_currency") or "EUR").upper()
    force = bool(params.get("force", False))
    max_movers = int(params.get("max_movers", 5))
    max_alerts = int(params.get("max_alerts", 20))

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

        # Idempotent: skip wenn schon Briefing fuer (ts, model) existiert
        if not force:
            existing = con.execute(
                "SELECT generated_at FROM sig_portfolio_briefings WHERE ts = ? AND model = ?",
                [ts, model],
            ).fetchone()
            if existing:
                print(f"==> Briefing fuer {ts} mit {model} existiert bereits ({existing[0]}). Skip.")
                print(f"    --force im params-file fuer Re-Generation.")
                return 0

        # Sanity: gibt's ueberhaupt Holdings?
        n_holdings = con.execute("SELECT count(*) FROM pos_holdings").fetchone()[0]
        if not n_holdings:
            print("FEHLER: pos_holdings ist leer. Erst portfolio importieren.", file=sys.stderr)
            return 64

        prev_ts = previous_trading_day(con, ts)

        per_ccy = fetch_per_currency_totals(con, ts, prev_ts, base_currency)
        movers  = fetch_top_movers(con, ts, n=max_movers)
        alerts  = fetch_alerts_for_holdings(con, ts, max_n=max_alerts)

        # Snapshot-numbers fuer DB
        portfolio_total = sum(d["total_base"] for d in per_ccy.values() if d.get("total_base") is not None)
        prev_total      = sum(d["prev_base"]  for d in per_ccy.values() if d.get("prev_base")  is not None)
        delta_abs       = portfolio_total - prev_total if prev_total else 0
        delta_pct       = ((portfolio_total / prev_total - 1) * 100) if prev_total else 0

        prompt = build_user_prompt(ts, base_currency, per_ccy, movers, alerts)

        print("==> nova-lab portfolio_briefing")
        print(f"    ts             : {ts}")
        print(f"    prev_ts        : {prev_ts}")
        print(f"    base_currency  : {base_currency}")
        print(f"    holdings       : {n_holdings}")
        print(f"    movers         : {len(movers)}")
        print(f"    alerts         : {len(alerts)}")
        print(f"    model          : {model}")
        print(f"    portfolio_total: {portfolio_total:,.2f} {base_currency}")
        print(f"    Δ vs vortag    : {delta_abs:+,.2f} ({delta_pct:+.2f}%)")
        print(f"    run_id         : {run_id}")
        print()
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
            print(f"FEHLER: LLM-Output ist kein valid JSON: {e}", file=sys.stderr)
            print(r.text)
            return 1

        write_briefing(
            con, ts, model, base_currency,
            portfolio_total, delta_abs, delta_pct,
            n_holdings, len(alerts),
            parsed, r.eval_count, r.duration_s, run_id,
        )

        print(f"    duration   : {r.duration_s:.2f}s")
        print(f"    tokens     : {r.eval_count}")
        print(f"    tps        : {r.tps:.1f}")
        print(f"    sentiment  : {parsed.get('sentiment', '?')}")
        print(f"    confidence : {parsed.get('confidence', '?')}")
        print()
        print(f"==> Briefing geschrieben: sig_portfolio_briefings({ts}, {model})")
        print()
        print(f"--- Headline ---")
        print(parsed.get("headline", "(leer)"))
        print()
        print(f"--- Body ---")
        print(parsed.get("body", "(leer)"))

        return 0

    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
