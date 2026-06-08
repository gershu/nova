"""Deep-Dive — fokussierte, synchrone LLM-Bewertung EINER Kennzahl.

On-demand aus der Unternehmens-Analyse: nimmt die (ohnehin geladene) Perioden-
Historie einer gewaehlten Kennzahl, zieht optional den MD&A-Text des juengsten
Filings und laesst die lokale LLM (nova-w5) daraus eine kurze, faktische
Einordnung erzeugen.

Bewusst SYNCHRON (interaktiv), im Gegensatz zur sonst asynchronen Job-Queue —
daher: kurzer Output, Health-Check vorab, und das Caching uebernimmt der
Aufrufer (Streamlit st.cache_data). Reine Funktionen, kein Streamlit-Import.
"""

from __future__ import annotations

from modules.dashboard import company_data as cd
from modules.llm.client import LLMError, OllamaClient


# ---------- Kennzahlen-Katalog (alles direkt aus year_metrics ableitbar) ----

# unit: 'cur' (Waehrung), 'pct' (Anteil 0..1), 'cnt' (Stueck/Personen)
METRICS: list[dict] = [
    {"key": "revenue",          "label": "Umsatz",                "unit": "cur"},
    {"key": "gross_margin",     "label": "Bruttomarge",           "unit": "pct"},
    {"key": "operating_income", "label": "Operatives Ergebnis",   "unit": "cur"},
    {"key": "op_margin",        "label": "Operative Marge",       "unit": "pct"},
    {"key": "net_income",       "label": "Nettogewinn",           "unit": "cur"},
    {"key": "net_margin",       "label": "Nettomarge",            "unit": "pct"},
    {"key": "fcf",              "label": "Free Cash Flow",        "unit": "cur"},
    {"key": "fcf_margin",       "label": "FCF-Marge",             "unit": "pct"},
    {"key": "rd_ratio",         "label": "F&E-Quote (R&D/Umsatz)", "unit": "pct"},
    {"key": "equity",           "label": "Eigenkapital",          "unit": "cur"},
    {"key": "net_debt",         "label": "Nettoverschuldung",     "unit": "cur"},
    {"key": "diluted_shares",   "label": "Verwaesserte Aktien",   "unit": "cnt"},
    {"key": "employees",        "label": "Mitarbeiter",           "unit": "cnt"},
]
_LABEL = {m["key"]: m["label"] for m in METRICS}
_UNIT = {m["key"]: m["unit"] for m in METRICS}


def _ratio(part, base):
    try:
        p, b = float(part), float(base)
        return p / b if b else None
    except (TypeError, ValueError):
        return None


def _metric_value(row: dict, key: str):
    g = row.get
    rev = g("revenue")
    if key == "gross_margin":
        return _ratio(g("gross_profit"), rev)
    if key == "op_margin":
        return _ratio(g("operating_income"), rev)
    if key == "net_margin":
        return _ratio(g("net_income"), rev)
    if key == "fcf_margin":
        return _ratio(g("fcf"), rev)
    if key == "rd_ratio":
        return _ratio(g("rd_expense"), rev)
    return g(key)  # direkte Felder (revenue, net_income, fcf, equity, ...)


def series(rows: list[dict], key: str) -> list[tuple[str, float]]:
    """[(period_end, value)] fuer die Kennzahl, None-Werte raus, chronologisch."""
    out = []
    for r in rows or []:
        v = _metric_value(r, key)
        if v is not None:
            out.append((str(r.get("period_end"))[:10], float(v)))
    out.sort(key=lambda t: t[0])
    return out


# ---------- Formatierung fuer den Prompt ----------

def _fmt(v: float, unit: str) -> str:
    if unit == "pct":
        return f"{v * 100:.1f}%"
    if unit == "cnt":
        return f"{v:,.0f}"
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.2f} Mrd"
    if a >= 1e6:
        return f"{v / 1e6:.1f} Mio"
    return f"{v:,.0f}"


def _cagr(points: list[tuple[str, float]], unit: str):
    """CAGR fuer cur/cnt; bei pct/negativen Werten None (nicht sinnvoll)."""
    if unit == "pct" or len(points) < 2:
        return None
    first, last = points[0][1], points[-1][1]
    if first is None or last is None or first <= 0 or last <= 0:
        return None
    yrs = len(points) - 1
    return (last / first) ** (1 / yrs) - 1 if yrs else None


_SYSTEM = (
    "Du bist ein nuechterner Buy-and-Hold-Investmentanalyst. Du bewertest "
    "AUSSCHLIESSLICH die vorgegebene Kennzahl-Historie (und ggf. den Filing-"
    "Auszug) — keine Kauf-/Verkaufsempfehlung, keine Kursprognose, keine "
    "erfundenen Zahlen.")

_PROMPT = """{symbol} ({name}) — Deep Dive zur Kennzahl: {metric} ({period}).

Verlauf:
{lines}
{cagr}
{mdna}
Aufgabe: 4-6 nuechterne deutsche Saetze. Beschreibe den Verlauf (Richtung,
Brueche, Tempo), nenne plausible Treiber {mdna_hint}, und das groesste Risiko
fuer die These. Nur die gegebenen Zahlen/Texte nutzen, nichts erfinden."""


def build_prompt(symbol, name, key, period, points, mdna_text):
    unit = _UNIT.get(key, "cur")
    lines = "\n".join(f"- {p}: {_fmt(v, unit)}" for p, v in points)
    cg = _cagr(points, unit)
    cagr = f"CAGR ueber den Zeitraum: {cg * 100:.1f}%\n" if cg is not None else ""
    if mdna_text:
        mdna = ("\nAuszug aus dem MD&A des juengsten Filings (gekuerzt):\n"
                f"\"\"\"\n{mdna_text}\n\"\"\"\n")
        hint = "(auch aus dem MD&A-Text, falls einschlaegig)"
    else:
        mdna, hint = "", "(soweit aus den Zahlen ableitbar)"
    return _PROMPT.format(
        symbol=symbol, name=name or "—", metric=_LABEL.get(key, key),
        period=("Jahre" if period == "annual" else "Quartale"),
        lines=lines, cagr=cagr, mdna=mdna, mdna_hint=hint)


def deep_dive(ticker: str, key: str, *, n_years: int = 8,
              period: str = "annual", use_mdna: bool = True,
              model: str | None = None) -> dict:
    """Synchroner Deep Dive. Returns dict mit points/cagr/assessment/meta.
    error gesetzt (statt assessment), wenn keine Daten oder LLM nicht
    erreichbar."""
    src = cd.resolve(ticker)
    label = _LABEL.get(key, key)
    base = {"key": key, "label": label, "symbol": src.ticker,
            "name": src.name, "points": [], "mdna_used": False,
            "assessment": None, "model": None, "error": None}

    ym = cd.year_metrics(ticker, n_years=n_years, period=period, src=src)
    points = series(ym.get("rows", []), key)
    base["points"] = points
    if len(points) < 2:
        base["error"] = "Zu wenige Datenpunkte fuer diese Kennzahl."
        return base

    # MD&A optional (langsam: 2 sec-api-Calls) — best effort.
    mdna_text = ""
    if use_mdna:
        rows = ym.get("rows", [])
        acc = rows[-1].get("accession_no") if rows else None
        form = rows[-1].get("form_type") if rows else None
        if acc:
            try:
                from modules.sec_filings.extractor import fetch_mdna_from_filing
                mdna_text = fetch_mdna_from_filing(acc, form, max_chars=4000)
            except Exception:  # noqa: BLE001
                mdna_text = ""
    base["mdna_used"] = bool(mdna_text)

    # LLM-Preflight (nova-w5 erreichbar?) + synchroner Call.
    try:
        with OllamaClient() as llm:
            ok, msg = llm.health_check()
            if not ok:
                base["error"] = f"LLM nicht erreichbar: {msg}"
                return base
            prompt = build_prompt(src.ticker, src.name, key, period, points,
                                  mdna_text)
            r = llm.generate(prompt, system=_SYSTEM, model=model)
        base["assessment"] = (r.text or "").strip()
        base["model"] = getattr(r, "model", model or "?")
    except LLMError as e:
        base["error"] = f"LLM-Fehler: {e}"
    return base
