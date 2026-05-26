"""Stufe-3-Analyzer: lokaler LLM bewertet einen Screener-Pick.

Eingabe: ein Pick aus sig_screen_picks (Metrics + Trends + Criteria)
        + 10-K-Auszuege (Item 1, 1A, 7) via sec_filings.extractor
        + juengste News-Schlagzeilen aus ref_sa_articles.

Ausgabe: strukturiertes JSON mit Verdikt, Achsen-Scores, Thesis, Risiken,
         Zitaten — persistiert in sig_screen_thesis.

Design-Punkte:
  - LLM bekommt KEINE Freiheit zu fabulieren: Prompt zwingt zu Zitaten aus
    dem mitgegebenen Material. Ohne Zitat zaehlt eine Aussage nicht.
  - JSON-Output via Ollama format=json (harte Validierung).
  - Bei JSON-Parse-Fehlern wird der raw-Text als fallback gespeichert.
  - Texte werden geschnitten — qwen2.5:14b hat 32k Kontext, wir halten
    den Prompt unter ~10k Chars Input.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


# Hart-Limits fuer Text-Snippets im Prompt — gross genug fuer Substanz,
# klein genug fuer 32k-Kontext mit Headroom.
MAX_BUSINESS_CHARS = 2500
MAX_RISK_CHARS     = 3000
MAX_MDA_CHARS      = 3000
MAX_NEWS_ITEMS     = 12
MAX_NEWS_CHARS     = 150


SYSTEM_PROMPT = """\
Du bist ein vorsichtiger Aktien-Analyst im Stil von Quality-GARP-Investing
(Quality + Growth at Reasonable Price). Du bewertest ein einzelnes Unternehmen
ausschliesslich auf Basis der gelieferten Daten und Texte. Du ERFINDEST
KEINE Zahlen, Namen oder Ereignisse. Wenn eine Aussage nicht durch das
gelieferte Material belegt ist, lass sie weg.

Antwortformat: gueltiges JSON, KEIN Fliesstext rundherum. Schema:

{
  "verdict":            "BUY_CONVICTION" | "WATCH" | "PASS",
  "classification":     "QUALITY_GROWTH" | "GARP" | "QUALITY_VALUE" | "WACHSTUMS-STAR" | "ZYKLIKER" | "UNKLAR",
  "growth_score_llm":   0-100,
  "value_score_llm":    0-100,
  "conviction_score":   0-100,
  "thesis_text":        "3-5 Saetze auf Deutsch; nenne konkrete Zahlen oder Fakten aus dem Material.",
  "risks":              [{"risk": "<Risiko>", "citation": "<Item 1A | Metric: <name> | News | MD&A>"}],
  "moat_assessment":    "<1-2 Saetze: woher kommt der Wettbewerbsvorsprung?>"
}
"""


@dataclass
class AnalyzerInput:
    """Alles was der Prompt-Builder braucht. Vom CLI gefuellt."""
    symbol:           str
    name:             str
    sector:           str | None
    market_cap:       float | None
    metrics:          dict           # aus pick.metrics_json
    criteria_detail:  list[dict]     # aus pick.criteria_detail_json
    trends:           dict           # aus pick.trend_flags_json
    axis_scores:      dict           # quality/growth/value/composite
    business_text:    str = ""       # 10-K Item 1
    risk_text:        str = ""       # 10-K Item 1A
    mda_text:         str = ""       # 10-K Item 7
    news_items:       list[dict] = field(default_factory=list)
    filing_form:      str | None = None
    filing_period:    str | None = None


def _trunc(s: str, limit: int) -> str:
    s = (s or "").strip()
    if len(s) <= limit:
        return s
    return s[:limit].rsplit(" ", 1)[0] + " […]"


def _fmt_metric(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) >= 1e9:  return f"{v/1e9:,.1f} Mrd"
        if abs(v) >= 1e6:  return f"{v/1e6:,.0f} Mio"
        if abs(v) < 1:     return f"{v*100:+.2f} %"
        return f"{v:,.2f}"
    return str(v)


def build_prompt(inp: AnalyzerInput) -> str:
    """Vollstaendiger User-Prompt (System-Prompt wird separat uebergeben)."""
    parts: list[str] = []

    parts.append(f"### Unternehmen\n"
                 f"{inp.name} ({inp.symbol})\n"
                 f"Sektor: {inp.sector or '—'}\n"
                 f"Market Cap: {_fmt_metric(inp.market_cap)}\n"
                 f"Filing-Basis: {inp.filing_form or '—'} "
                 f"per {inp.filing_period or '—'}")

    # Kennzahlen
    m_lines = ["### Kennzahlen (aktueller Stand)"]
    for k in ("market_cap", "roic", "gross_margin", "net_margin",
              "net_debt_to_ebitda", "peg_ratio", "fcf_yield",
              "pe_forward", "revenue_cagr_5y", "net_income_cagr_5y",
              "revenue_q_yoy"):
        if k in inp.metrics:
            m_lines.append(f"  {k:<22s} {_fmt_metric(inp.metrics[k])}")
    parts.append("\n".join(m_lines))

    # Kriterien-Pass/Fail (kompakt)
    crit_lines = ["### Stufe-1-Kriterien"]
    for c in inp.criteria_detail:
        flag = "✓" if c.get("passed") else "✗"
        v = c.get("value")
        crit_lines.append(f"  {flag} {c['name']:<32s} "
                          f"value={_fmt_metric(v)}  thr={c['threshold']}")
    parts.append("\n".join(crit_lines))

    # Trends Stufe 2
    t_lines = ["### Trends (Stufe 2)"]
    for k, v in inp.trends.items():
        if isinstance(v, bool):
            t_lines.append(f"  {k:<22s} {'JA' if v else 'NEIN'}")
        elif isinstance(v, (int, float)):
            t_lines.append(f"  {k:<22s} {_fmt_metric(v)}")
    if len(t_lines) == 1:
        t_lines.append("  (noch keine Trend-Daten)")
    parts.append("\n".join(t_lines))

    # Achsen-Scores
    parts.append(
        "### Stufe-1+2-Score (regelbasiert)\n"
        f"  Quality: {inp.axis_scores.get('quality_score', 0):.2f}\n"
        f"  Growth:  {inp.axis_scores.get('growth_score', 0):.2f}\n"
        f"  Value:   {inp.axis_scores.get('value_score', 0):.2f}\n"
        f"  Composite: {inp.axis_scores.get('composite_score', 0):.2f}"
    )

    if inp.business_text:
        parts.append("### 10-K Item 1 — Geschaefts­modell (Auszug)\n"
                     + _trunc(inp.business_text, MAX_BUSINESS_CHARS))
    if inp.risk_text:
        parts.append("### 10-K Item 1A — Risikofaktoren (Auszug)\n"
                     + _trunc(inp.risk_text, MAX_RISK_CHARS))
    if inp.mda_text:
        parts.append("### 10-K Item 7 — MD&A (Auszug)\n"
                     + _trunc(inp.mda_text, MAX_MDA_CHARS))

    if inp.news_items:
        n_lines = [f"### Aktuelle News ({len(inp.news_items)} Items)"]
        for art in inp.news_items[:MAX_NEWS_ITEMS]:
            line = f"  [{art.get('ts', '')[:10]}] {art.get('title', '')}"
            if art.get("summary"):
                line += f" — {_trunc(art['summary'], MAX_NEWS_CHARS)}"
            n_lines.append(line)
        parts.append("\n".join(n_lines))

    parts.append(
        "### Aufgabe\n"
        "Bewerte das Unternehmen anhand des gelieferten Materials.\n"
        "Schreibe die Thesis in 3-5 Saetzen auf Deutsch. Belege jede\n"
        "Risiko-Aussage mit einer 'citation' (Item 1A, Item 7, News oder\n"
        "konkrete Metric). Antworte ausschliesslich mit dem JSON-Objekt.")
    return "\n\n".join(parts)


_VALID_VERDICTS = {"BUY_CONVICTION", "WATCH", "PASS"}


def parse_response(text: str) -> dict:
    """LLM-JSON parsen mit Robustness.

    Versucht zuerst direkten json.loads, dann regex-extraction des ersten
    {...}-Blocks (falls das Modell trotz format=json Whitespace drum baut).
    """
    if not text or not text.strip():
        return {"_parse_error": "leere Antwort"}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {"_parse_error": "kein JSON-Block gefunden",
                    "raw": text[:500]}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            return {"_parse_error": f"JSON parse fail: {e}",
                    "raw": text[:500]}

    # Validierung mit weichen Defaults
    if data.get("verdict") not in _VALID_VERDICTS:
        data.setdefault("_warn", []).append(
            f"verdict '{data.get('verdict')}' nicht in {_VALID_VERDICTS}")
    for k in ("growth_score_llm", "value_score_llm", "conviction_score"):
        v = data.get(k)
        if v is None or not isinstance(v, (int, float)):
            data.setdefault("_warn", []).append(f"{k} fehlt/ungueltig")
    return data


def call_llm(prompt: str, system: str = SYSTEM_PROMPT,
              model: str | None = None) -> "LLMResponse":  # noqa: F821
    """Ollama-Call mit json_mode. Lazy import vermeidet llm-Dependency
    beim Lesen reiner Filter-Module."""
    from modules.llm.client import OllamaClient
    with OllamaClient(model=model) as llm:
        return llm.generate(prompt, system=system, json_mode=True,
                            options={"temperature": 0.2})
