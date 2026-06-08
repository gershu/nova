"""Job-Handler je `kind`, zweiphasig getrennt:

  compute(job, model) -> dict   : LANGSAM (LLM-Inferenz), KEIN DB-Zugriff.
  persist(con, job, result) -> str : SCHNELL, schreibt das Ergebnis.

Diese Trennung ist bewusst: der Worker haelt die schreibende DuckDB-Connection
nur fuer das kurze persist() (unter dem Schreib-Lock); die langsame Inferenz
laeuft connection-/lock-frei -> das Dashboard kann waehrenddessen lesen.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from modules.llm.client import OllamaClient

_THEME_LABELS = {
    "return_on_capital": "Return on Capital",
    "balance_sheet":     "Balance Sheet",
    "stock_based_comp":  "Stock-based Compensation",
    "gaap_vs_non_gaap":  "GAAP vs non-GAAP",
    "insider":           "Insider",
}

_SYSTEM = (
    "Du bist ein nuechterner Buy-and-Hold-Investmentanalyst. Du fasst "
    "ausschliesslich die vorgegebenen Qualitaets-Scores zusammen — keine "
    "Kauf-/Verkaufsempfehlung, keine Kursprognose, keine erfundenen Zahlen.")

_PROMPT = """Wert: {symbol}
Gesamt-Qualitaets-Score (0-100): {score}
Teil-Scores je Thema (0-100, hoeher = besser):
{lines}

Aufgabe:
1. "narrative": 2-3 nuechterne deutsche Saetze, warum der Score so ausfaellt —
   nenne die staerksten und schwaechsten Themen konkret.
2. "red_flag": das EINE groesste Risiko in einem Satz (aus dem schwaechsten
   Thema abgeleitet) — oder "" wenn alle Themen stark sind.

Antworte ausschliesslich als JSON: {{"narrative": "...", "red_flag": "..."}}"""


def quality_input_hash(score, subs: dict) -> str:
    key = f"{score}|" + "|".join(
        f"{k}:{round(v, 3) if isinstance(v, (int, float)) else 'NA'}"
        for k, v in sorted(subs.items()))
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _parse(text: str):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            return (str(d.get("narrative", "")).strip(),
                    str(d.get("red_flag", "")).strip())
        except Exception:  # noqa: BLE001
            pass
    return text.strip(), ""


# ---- quality_narrative ----

def quality_compute(job, *, model=None) -> dict:
    """LLM-Call (langsam, kein DB)."""
    p = job["payload"]
    symbol = p.get("symbol") or job.get("ref_instrument_id")
    score = p.get("score")
    subs = p.get("subs") or {}
    lines = "\n".join(
        f"- {_THEME_LABELS.get(k, k)}: "
        f"{round(v * 100) if isinstance(v, (int, float)) else 'n/a'}"
        for k, v in subs.items())
    prompt = _PROMPT.format(symbol=symbol, score=score, lines=lines)
    with OllamaClient() as llm:
        r = llm.generate(prompt, system=_SYSTEM, json_mode=True, model=model)
    narrative, red_flag = _parse(r.text)
    return {"symbol": symbol, "score": score, "narrative": narrative,
            "red_flag": red_flag, "model": getattr(r, "model", model or "?")}


def quality_persist(con, job, result: dict) -> str:
    """Ergebnis schreiben (schnell, unter Schreib-Lock)."""
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM ref_quality_narrative WHERE ref_instrument_id=?",
                [job["ref_instrument_id"]])
    con.execute(
        "INSERT INTO ref_quality_narrative (ref_instrument_id, symbol, score, "
        "narrative, red_flag, model, input_hash, generated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [job["ref_instrument_id"], result["symbol"], result["score"],
         result["narrative"], result["red_flag"], result["model"],
         job.get("input_hash"), now])
    return f"{result['symbol']}: {result['narrative'][:70]}"


# ---- filing_change ----

_FC_SYSTEM = (
    "Du bist ein nuechterner Analyst. Du fasst die Veraenderung zwischen zwei "
    "SEC-Filings rein faktisch zusammen — keine Kauf-/Verkaufsempfehlung, "
    "keine Kursprognose, keine erfundenen Zahlen.")

_FC_PROMPT = """{symbol} {form}: neue Periode {period} vs. Vorperiode {prior}.
{lines}

Aufgabe:
1. "summary": 2-3 nuechterne deutsche Saetze, was sich wesentlich geaendert
   hat (Umsatz/Margen/Gewinn) und ob es die Qualitaets-These stuetzt oder
   belastet.
2. "impact": eines von "positiv" | "neutral" | "negativ" fuer die These.

Antworte ausschliesslich als JSON: {{"summary": "...", "impact": "..."}}"""


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pct_delta(new, old):
    n, o = _num(new), _num(old)
    if n is None or o in (None, 0):
        return None
    return n / o - 1.0


def _margin(part, base):
    p, b = _num(part), _num(base)
    return (p / b) if (p is not None and b not in (None, 0)) else None


def filing_deltas(inc_new, inc_old) -> dict:
    """Umsatz-/Margen-/Gewinn-Deltas zweier IncomeStatement-Objekte."""
    def g(o, a):
        return getattr(o, a, None) if o is not None else None
    rn, ro = g(inc_new, "revenue"), g(inc_old, "revenue")
    return {
        "revenue_new": _num(rn), "revenue_old": _num(ro),
        "revenue_delta": _pct_delta(rn, ro),
        "gross_margin_new": _margin(g(inc_new, "gross_profit"), rn),
        "gross_margin_old": _margin(g(inc_old, "gross_profit"), ro),
        "op_margin_new": _margin(g(inc_new, "operating_income"), rn),
        "op_margin_old": _margin(g(inc_old, "operating_income"), ro),
        "net_income_new": _num(g(inc_new, "net_income")),
        "net_income_old": _num(g(inc_old, "net_income")),
        "ni_delta": _pct_delta(g(inc_new, "net_income"),
                               g(inc_old, "net_income")),
    }


def _fc_lines(d: dict) -> str:
    def pc(x):
        return f"{x * 100:+.1f}%" if isinstance(x, (int, float)) else "n/a"

    def mg(x):
        return f"{x * 100:.1f}%" if isinstance(x, (int, float)) else "n/a"
    return "\n".join([
        f"- Umsatz: {d.get('revenue_old')} -> {d.get('revenue_new')} "
        f"({pc(d.get('revenue_delta'))})",
        f"- Bruttomarge: {mg(d.get('gross_margin_old'))} -> "
        f"{mg(d.get('gross_margin_new'))}",
        f"- Operative Marge: {mg(d.get('op_margin_old'))} -> "
        f"{mg(d.get('op_margin_new'))}",
        f"- Nettogewinn: {d.get('net_income_old')} -> "
        f"{d.get('net_income_new')} ({pc(d.get('ni_delta'))})",
    ])


def filing_change_compute(job, *, model=None) -> dict:
    from modules.sec_filings import client as sec
    p = job["payload"]
    nf, pf = p.get("new_filing"), p.get("prior_filing")
    inc_new = sec.fetch_income_from_filing(nf) if nf else None
    inc_old = sec.fetch_income_from_filing(pf) if pf else None
    deltas = filing_deltas(inc_new, inc_old)
    base = {"symbol": p.get("symbol"), "form": p.get("form"),
            "accession": p.get("accession"), "period": p.get("period"),
            "prior_period": p.get("prior_period"), "deltas": deltas}
    if inc_new is None:
        return {**base, "summary": "Keine verwertbaren GuV-Daten im neuen "
                "Filing.", "impact": "n/a", "model": "—"}
    prompt = _FC_PROMPT.format(symbol=p.get("symbol"), form=p.get("form"),
                               period=p.get("period"),
                               prior=p.get("prior_period") or "—",
                               lines=_fc_lines(deltas))
    with OllamaClient() as llm:
        r = llm.generate(prompt, system=_FC_SYSTEM, json_mode=True, model=model)
    m = re.search(r"\{.*\}", r.text, re.DOTALL)
    summary, impact = (r.text.strip(), "n/a")
    if m:
        try:
            d = json.loads(m.group(0))
            summary = str(d.get("summary", "")).strip() or r.text.strip()
            impact = str(d.get("impact", "n/a")).strip() or "n/a"
        except Exception:  # noqa: BLE001
            pass
    return {**base, "summary": summary, "impact": impact,
            "model": getattr(r, "model", model or "?")}


def filing_change_persist(con, job, result: dict) -> str:
    now = datetime.now(timezone.utc)
    rid = job["ref_instrument_id"]
    con.execute("DELETE FROM ref_filing_change WHERE ref_instrument_id=? "
                "AND form=? AND accession=?",
                [rid, result["form"], result["accession"]])
    con.execute(
        "INSERT INTO ref_filing_change (ref_instrument_id, symbol, form, "
        "accession, period, prior_period, summary, impact, deltas_json, "
        "model, generated_at, event_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [rid, result["symbol"], result["form"], result["accession"],
         result["period"], result["prior_period"], result["summary"],
         result["impact"], json.dumps(result["deltas"]), result["model"], now,
         result.get("event_type")])
    # 'seen' bestaetigen (wurde beim enqueue gesetzt; hier idempotent halten).
    con.execute("UPDATE ref_filing_seen SET last_accession=?, last_period=?, "
                "seen_at=? WHERE ref_instrument_id=? AND form=?",
                [result["accession"], result["period"], now, rid,
                 result["form"]])
    return (f"{result['symbol']} {result['form']} {result['period']}: "
            f"{result['impact']} — {result['summary'][:50]}")


# ---- filing_8k (Text-Summary statt GuV-Diff) ----
#
# 8-K-Filings haben keine GuV (Ereignis-Meldungen) -> der filing_change-Diff
# liefert dort nur "keine GuV-Daten". Stattdessen: Exhibit/Body-Text holen und
# das Ereignis kurz zusammenfassen. Ergebnis landet in derselben Tabelle
# (ref_filing_change) -> persist wird wiederverwendet.

_8K_SYSTEM = (
    "Du bist ein nuechterner Analyst. Du fasst ein SEC-8-K (Ad-hoc-Meldung) "
    "rein faktisch zusammen — keine Kauf-/Verkaufsempfehlung, keine "
    "Kursprognose, keine erfundenen Zahlen. Nutze nur den gegebenen Text.")

# Feste Ereignistyp-Taxonomie. Auch der LLM-Fallback muss einen dieser Werte
# liefern.
_8K_CATEGORIES = [
    "Earnings", "Personalwechsel", "M&A", "Kapital/Dividende", "Rechtliches",
    "Guidance/RegFD", "Vertrag/Operativ", "Governance", "Sonstiges",
]

# 8-K-Item-Code -> Kategorie (SEC-Item-Nummern). Deterministisch + praezise;
# 9.01 (Exhibits) wird ignoriert, da fast immer beigefuegt.
_8K_ITEM_MAP = {
    "1.01": "Vertrag/Operativ", "1.02": "Vertrag/Operativ",
    "1.03": "Rechtliches", "1.04": "Rechtliches",
    "2.01": "M&A", "2.02": "Earnings",
    "2.03": "Kapital/Dividende", "2.04": "Kapital/Dividende",
    "2.05": "Vertrag/Operativ", "2.06": "Vertrag/Operativ",
    "3.01": "Kapital/Dividende", "3.02": "Kapital/Dividende",
    "3.03": "Kapital/Dividende",
    "4.01": "Rechtliches", "4.02": "Rechtliches",
    "5.01": "M&A", "5.02": "Personalwechsel", "5.03": "Governance",
    "5.07": "Governance", "5.08": "Governance",
    "7.01": "Guidance/RegFD", "8.01": "Sonstiges",
}
# Prioritaet, wenn mehrere Items vorkommen (wichtigstes Ereignis gewinnt).
_8K_PRIORITY = ["M&A", "Earnings", "Personalwechsel", "Rechtliches",
                "Kapital/Dividende", "Guidance/RegFD", "Vertrag/Operativ",
                "Governance", "Sonstiges"]

_8K_PROMPT = """{symbol} 8-K, eingereicht {filed_at}.
Gemeldete Items: {items}

Auszug aus dem Filing (gekuerzt):
\"\"\"
{text}
\"\"\"

Aufgabe:
1. "summary": 2-3 nuechterne deutsche Saetze — welches Ereignis wird gemeldet
   und ist es fuer die Qualitaets-These relevant?
2. "impact": eines von "positiv" | "neutral" | "negativ" fuer die These.
3. "category": GENAU einer von {cats}.

Antworte ausschliesslich als JSON:
{{"summary": "...", "impact": "...", "category": "..."}}"""

_8K_MAX_CHARS = 6000

_ITEM_CODE_RE = re.compile(r"(\d\.\d{2})")


def _items_str(items) -> str:
    if isinstance(items, (list, tuple)):
        return "; ".join(str(i) for i in items) or "—"
    return str(items or "—")


def cat_from_items(items) -> str | None:
    """Ereignistyp deterministisch aus 8-K-Item-Codes. None, wenn die Items
    keine (ausser 9.01) abbilden — dann uebernimmt der LLM-Fallback."""
    if not isinstance(items, (list, tuple)):
        items = [items] if items else []
    cats = set()
    for it in items:
        for code in _ITEM_CODE_RE.findall(str(it)):
            c = _8K_ITEM_MAP.get(code)
            if c:
                cats.add(c)
    for c in _8K_PRIORITY:
        if c in cats:
            return c
    return None


def filing_8k_compute(job, *, model=None) -> dict:
    from modules.sec_filings import client as sec
    p = job["payload"]
    items = p.get("items") or []
    # Primaer: deterministischer Item-Code (praezise). Fallback: LLM-Kategorie.
    cat_items = cat_from_items(items)
    base = {"symbol": p.get("symbol"), "form": "8-K",
            "accession": p.get("accession"), "period": p.get("period"),
            "prior_period": None,
            "deltas": {"kind": "8k", "items": items,
                       "filed_at": p.get("filed_at"),
                       "cat_items": cat_items}}
    text = ""
    url = p.get("text_url")
    if url:
        try:
            text = sec.fetch_exhibit_text(url)[:_8K_MAX_CHARS]
        except Exception:  # noqa: BLE001
            text = ""
    if not text.strip():
        return {**base, "summary": "Kein lesbarer 8-K-Text abrufbar.",
                "impact": "n/a", "event_type": cat_items or "Sonstiges",
                "model": "—"}
    prompt = _8K_PROMPT.format(symbol=p.get("symbol"),
                               filed_at=p.get("filed_at") or "—",
                               items=_items_str(items), text=text,
                               cats=" | ".join(_8K_CATEGORIES))
    with OllamaClient() as llm:
        r = llm.generate(prompt, system=_8K_SYSTEM, json_mode=True, model=model)
    m = re.search(r"\{.*\}", r.text, re.DOTALL)
    summary, impact, cat_llm = (r.text.strip(), "n/a", None)
    if m:
        try:
            d = json.loads(m.group(0))
            summary = str(d.get("summary", "")).strip() or r.text.strip()
            impact = str(d.get("impact", "n/a")).strip() or "n/a"
            c = str(d.get("category", "")).strip()
            cat_llm = c if c in _8K_CATEGORIES else None
        except Exception:  # noqa: BLE001
            pass
    event_type = cat_items or cat_llm or "Sonstiges"
    return {**base, "summary": summary, "impact": impact,
            "event_type": event_type,
            "model": getattr(r, "model", model or "?")}


COMPUTE = {"quality_narrative": quality_compute,
           "filing_change": filing_change_compute,
           "filing_8k": filing_8k_compute}
PERSIST = {"quality_narrative": quality_persist,
           "filing_change": filing_change_persist,
           "filing_8k": filing_change_persist}
