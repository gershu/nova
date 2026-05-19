"""LLM-Strukturierer fuer einzelne Stocks — on-demand Deep-Dive.

Rolle: PURE STRUKTURIERUNG. Fasst Fundamentals + jüngste News zusammen,
listet Stärken + rote Flaggen. Gibt KEINE Buy/Hold/Avoid-Empfehlung.

Pattern analog zu modules/llm/alert_explainer:
  - News-Quelle: news_rss + news_yfinance kombiniert
  - Output: JSON-Mode-Response mit {summary, strengths[], red_flags[]}
  - Hallucination-Guards: explicit "wenn fehlt: 'n/a'", "nur aus Input"
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from modules.llm.client import LLMResponse, OllamaClient


# Felder die im Prompt erscheinen — nicht alle 30, sondern die signal-tragenden.
PROMPT_FUNDAMENTALS_FIELDS = [
    ("Sector / Industry",  ["sector", "industry"]),
    ("Market Cap",         ["market_cap"]),
    ("P/E TTM",            ["pe_ttm"]),
    ("P/E Forward",        ["pe_forward"]),
    ("P/B",                ["pb"]),
    ("FCF Yield",          ["fcf_yield"]),
    ("Dividend Yield",     ["dividend_yield"]),
    ("Payout Ratio",       ["payout_ratio"]),
    ("ROE",                ["roe"]),
    ("ROIC",               ["roic"]),
    ("Operating Margin",   ["operating_margin"]),
    ("Net Margin",         ["net_margin"]),
    ("Gross Margin",       ["gross_margin"]),
    ("Debt/Equity",        ["debt_to_equity"]),
    ("Net Debt/EBITDA",    ["net_debt_to_ebitda"]),
    ("Interest Coverage",  ["interest_coverage"]),
    ("Revenue CAGR 5y",    ["revenue_cagr_5y"]),
    ("EPS CAGR 5y",        ["eps_cagr_5y"]),
    ("FCF CAGR 5y",        ["fcf_cagr_5y"]),
]


@dataclass
class BriefResult:
    symbol:            str
    summary:           Optional[str] = None
    strengths:         list[str]     = field(default_factory=list)
    red_flags:         list[str]     = field(default_factory=list)
    model:             Optional[str] = None
    eval_tokens:       Optional[int] = None
    duration_s:        Optional[float] = None
    fundamentals_used: int = 0
    news_count:        int = 0
    sa_count:          int = 0
    error:             Optional[str] = None


def _fmt_value(name: str, val) -> str:
    """Format mit human-readable Conventions (pct vs absolute)."""
    if val is None:
        return "n/a"
    try:
        v = float(val)
        if v != v:    # NaN
            return "n/a"
    except (TypeError, ValueError):
        return str(val)
    # Percentages
    if any(x in name.lower() for x in ("margin", "yield", "roe", "roa", "roic", "ratio", "cagr", "payout")):
        return f"{v * 100:.2f}%"
    # Market cap / large numbers
    if "cap" in name.lower() or "debt/equity" in name.lower() or "ebitda" in name.lower() or "coverage" in name.lower():
        if abs(v) >= 1_000_000_000:
            return f"{v/1_000_000_000:.1f}B"
        if abs(v) >= 1_000_000:
            return f"{v/1_000_000:.1f}M"
    return f"{v:,.2f}"


def _build_fundamentals_block(fundamentals: dict) -> tuple[str, int]:
    """Return (string, count_of_filled_fields)."""
    lines: list[str] = []
    filled = 0
    for label, keys in PROMPT_FUNDAMENTALS_FIELDS:
        # Try first key with a non-None value
        val = None
        for k in keys:
            v = fundamentals.get(k)
            if v is not None:
                val = v
                break
        formatted = _fmt_value(label, val)
        lines.append(f"- {label:<22s} {formatted}")
        if val is not None and formatted != "n/a":
            filled += 1
    return "\n".join(lines), filled


def _build_news_block(news_items: list[dict]) -> tuple[str, int]:
    """News-Items: liste von dicts mit 'title' + optional 'published' + 'summary'."""
    if not news_items:
        return "(keine News in den letzten 14 Tagen)", 0
    lines: list[str] = []
    for n in news_items[:8]:   # Cap auf 8, mehr ueberlastet den Prompt
        title = (n.get("title") or "").strip()
        if not title:
            continue
        pub = n.get("published") or n.get("published_at") or ""
        if pub:
            lines.append(f"- [{pub}] {title}")
        else:
            lines.append(f"- {title}")
    return "\n".join(lines) if lines else "(keine News)", len(lines)


SYSTEM_PROMPT = """Du bist ein neutraler Strukturierer fuer einen Value-Investor.

Deine Aufgabe: aus Fundamentals + News + Editorial-Opinions einen knappen
strukturierten Brief erstellen.

STRIKTE REGELN:
- KEINE Buy/Hold/Avoid-Empfehlung.
- KEINE Spekulation ueber Kursrichtung ("wird steigen/fallen", "kaufenswert").
- KEINE vergleichenden Wertungen ("besser als Peer X").
- NUR Fakten + dokumentierte Meinungen aus dem Input. Wenn ein Datenpunkt
  fehlt, "n/a" oder gar nicht erwaehnen.
- Sprache: neutral, sachlich, deutsch.
- Bei Erfindungen oder Halluzinationen riskierst Du die Bewertung.

WICHTIG zum Editorial-Opinion-Block (Seeking Alpha):
- Diese Inhalte sind ARGUMENTATIV / THESIS-DRIVEN (Author X sagt Y).
- Gib die Argumente FAKTISCH wieder ("Ein SA-Author argumentiert X"),
  uebernimm sie NICHT als Deine Bewertung.
- Wenn mehrere Autoren widersprechende Thesen haben, beide kurz nennen.

Output ist STRIKT als JSON mit drei Feldern:
  summary    : str (2-3 Saetze, Sachlage)
  strengths  : list[str] (faktische Pluspunkte aus dem Input, max 5)
  red_flags  : list[str] (faktische Warnungen aus dem Input, max 5; "keine offensichtlichen" wenn keine)
"""


def _build_sa_block(sa_articles: list[dict]) -> tuple[str, int]:
    """Editorial-Opinion-Block aus Seeking-Alpha-Artikeln.

    Returns (block_string, article_count).
    """
    if not sa_articles:
        return "(keine Editorial Opinions in den letzten 30 Tagen)", 0
    lines: list[str] = []
    n_articles = 0
    for a in sa_articles[:6]:   # Cap auf 6, sonst ueberlastet
        title = (a.get("title") or "").strip()
        if not title:
            continue
        ts = a.get("ts") or ""
        summary = (a.get("summary") or "").strip()
        if len(summary) > 250:
            summary = summary[:247] + "..."
        if ts:
            ts_str = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            lines.append(f"- [{ts_str}] {title}")
        else:
            lines.append(f"- {title}")
        if summary:
            lines.append(f"  Auszug: {summary}")
        n_articles += 1
    return "\n".join(lines) if lines else "(keine Editorial Opinions)", n_articles


def _build_user_prompt(symbol: str, name: str, fundamentals_block: str,
                        news_block: str, sa_block: str | None = None) -> str:
    sa_section = ""
    if sa_block:
        sa_section = f"\n=== Editorial Opinions (Seeking Alpha — argumentativ!) ===\n{sa_block}\n"
    return f"""Symbol: {symbol}
Name:   {name}

=== Fundamentals ===
{fundamentals_block}

=== News (letzte 14 Tage) ===
{news_block}
{sa_section}
Erstelle den Brief gemaess den Regeln.
"""


# Defensive JSON-Extraktion (LLM kann gelegentlich Whitespace/Trash drumherum produzieren)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_response(text: str) -> dict:
    """Versucht JSON zu parsen, faengt non-JSON-Krams ab."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_RE.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def generate_brief(
    symbol: str,
    name: str,
    fundamentals: dict,
    news_items: list[dict],
    *,
    sa_articles: list[dict] | None = None,
    client: OllamaClient | None = None,
    model: str | None = None,
) -> BriefResult:
    """Run end-to-end: build prompt -> LLM -> parse -> BriefResult.

    sa_articles: optionale Seeking-Alpha-Artikel (dicts mit ts/title/summary).
                 Werden als separater Editorial-Opinion-Block in den Prompt
                 gegeben mit klarem 'argumentativ, nicht uebernehmen'-Hint.
    """
    result = BriefResult(symbol=symbol)

    fund_block, n_fund = _build_fundamentals_block(fundamentals)
    news_block, n_news = _build_news_block(news_items)
    sa_block, n_sa = _build_sa_block(sa_articles or [])
    result.fundamentals_used = n_fund
    result.news_count = n_news
    result.sa_count = n_sa

    if n_fund == 0 and n_news == 0 and n_sa == 0:
        result.error = "no input (fundamentals empty + no news + no SA)"
        return result

    user_prompt = _build_user_prompt(symbol, name, fund_block, news_block,
                                      sa_block if n_sa > 0 else None)

    if client is None:
        client = OllamaClient()

    start = time.time()
    try:
        resp: LLMResponse = client.generate(
            user_prompt,
            model=model,
            system=SYSTEM_PROMPT,
            json_mode=True,
        )
    except Exception as e:  # noqa: BLE001
        result.error = f"{e.__class__.__name__}: {e}"
        return result
    result.duration_s = time.time() - start
    result.model = resp.model
    result.eval_tokens = resp.eval_count

    parsed = _parse_response(resp.text)
    result.summary = (parsed.get("summary") or "").strip() or None
    s = parsed.get("strengths") or []
    result.strengths = [str(x).strip() for x in s if str(x).strip()] if isinstance(s, list) else []
    f = parsed.get("red_flags") or []
    result.red_flags = [str(x).strip() for x in f if str(x).strip()] if isinstance(f, list) else []

    return result
