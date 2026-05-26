"""sec-api.io-Client — juengstes 10-Q/10-K finden + GuV via XBRL-to-JSON.

API-Docs: https://sec-api.io/docs
Auth:     API-Key via NOVA_SEC_API_KEY env-var (~/.nova_env auf nova-hub).

Zwei Endpunkte:
  - Query-API   POST https://api.sec-api.io        — Filing-Suche
  - XBRL-to-JSON GET https://api.sec-api.io/xbrl-to-json — Finanzberichte

Die XBRL-to-JSON-Antwort liefert StatementsOfIncome als dict
{US-GAAP-Concept: [fact, ...]}, jede fact mit period {startDate,endDate}
und value (String). Dimensionierte Segment-Fakten tragen zusaetzlich einen
'segment'-Key — die werden hier uebersprungen (nur konsolidierte Summen).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date

import requests


QUERY_URL = "https://api.sec-api.io"
XBRL_URL  = "https://api.sec-api.io/xbrl-to-json"


class SecApiError(RuntimeError):
    """sec-api.io-Aufruf fehlgeschlagen (HTTP-Fehler, leere Antwort, etc.)."""


# ---------- US-GAAP-Concept-Kandidaten (Prioritaets-Reihenfolge) ----------

_REVENUE = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
_COGS = [
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "CostOfGoodsSold",
]
_GROSS   = ["GrossProfit"]
_RD      = ["ResearchAndDevelopmentExpense"]
_SGA     = [
    "SellingGeneralAndAdministrativeExpense",
    "GeneralAndAdministrativeExpense",
]
_OPEX    = ["OperatingExpenses", "CostsAndExpenses"]
_OPINC   = ["OperatingIncomeLoss"]
_PRETAX  = [
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesAndExtraordinaryItemsNoncontrollingInterest",
]
_TAX     = ["IncomeTaxExpenseBenefit"]
_NET     = ["NetIncomeLoss", "ProfitLoss"]


# Achsen, die KEINE echte Disaggregation sind, sondern Konsolidierungs-
# bzw. Eliminations-Qualifier. Werden bei der Segment-Extraktion ignoriert.
_CONSOLIDATION_AXES = {
    "srt:ConsolidationItemsAxis",
    "us-gaap:ConsolidationItemsAxis",
}

# Bekannte Achsen mit menschenlesbaren Labels — fuer den Sankey-Selector.
# Unbekannte Achsen werden ueber _humanize() formatiert.
AXIS_LABELS = {
    "us-gaap:StatementBusinessSegmentsAxis": "Reportable Segments",
    "srt:ProductOrServiceAxis":              "Produkt / Service",
    "srt:StatementGeographicalAxis":         "Geografie",
}


@dataclass
class IncomeStatement:
    """GuV-Kernzeilen eines Filings — Betraege in Berichtswaehrung."""
    ref_instrument_id: str | None = None
    period_end:        str | None = None      # ISO-Datum (periodOfReport)
    form_type:         str | None = None
    accession_no:      str | None = None
    filed_at:          str | None = None
    period_months:     int | None = None
    currency:          str = "USD"

    revenue:           float | None = None
    cost_of_revenue:   float | None = None
    gross_profit:      float | None = None
    rd_expense:        float | None = None
    sga_expense:       float | None = None
    operating_expense: float | None = None
    operating_income:  float | None = None
    other_income:      float | None = None
    pretax_income:     float | None = None
    tax_expense:       float | None = None
    net_income:        float | None = None

    # Umsatz-Disaggregationen, je dict: {axis, member, member_label, value}.
    # Eine Periode kann mehrere Achsen liefern (Reportable Segments,
    # Produkt-Linie, Geografie). Der Sankey-Tab waehlt visuell.
    segments: list[dict] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)


def _api_key() -> str:
    key = os.environ.get("NOVA_SEC_API_KEY", "").strip()
    if not key:
        raise SecApiError(
            "NOVA_SEC_API_KEY nicht gesetzt. "
            "Key anlegen unter https://sec-api.io/signup und in ~/.nova_env "
            "ablegen (export NOVA_SEC_API_KEY=...)."
        )
    return key


# ---------- Filing-Suche ----------

def find_filings(
    ticker: str,
    *,
    n: int = 1,
    forms: tuple[str, ...] = ("10-Q", "10-K"),
) -> list[dict]:
    """Bis zu N juengste Filings (per filedAt desc) der gegebenen Form-Typen.

    Returns liste mit accession_no/form_type/period_of_report/filed_at/ticker.
    Leere Liste, wenn der Name kein passendes EDGAR-Filing hat (z.B. ETFs,
    nicht US-gelistete Werte).
    """
    form_q = " OR ".join(f'formType:"{f}"' for f in forms)
    payload = {
        "query": f"ticker:{ticker} AND ({form_q})",
        "from":  "0",
        "size":  str(max(1, int(n))),
        "sort":  [{"filedAt": {"order": "desc"}}],
    }
    try:
        resp = requests.post(
            QUERY_URL, json=payload,
            headers={"Authorization": _api_key()}, timeout=20)
    except requests.RequestException as e:
        raise SecApiError(f"Query-API-Request fehlgeschlagen: {e}") from e
    if resp.status_code != 200:
        raise SecApiError(
            f"Query-API HTTP {resp.status_code}: {resp.text[:200]}")

    return [{
        "accession_no":     f.get("accessionNo"),
        "form_type":        f.get("formType"),
        "period_of_report": f.get("periodOfReport"),
        "filed_at":         f.get("filedAt"),
        "ticker":           f.get("ticker"),
    } for f in (resp.json() or {}).get("filings", [])]


def find_latest_filing(
    ticker: str,
    *,
    forms: tuple[str, ...] = ("10-Q", "10-K"),
) -> dict | None:
    """Juengstes einzelnes Filing — duenne Schale um find_filings."""
    res = find_filings(ticker, n=1, forms=forms)
    return res[0] if res else None


def fetch_xbrl(accession_no: str) -> dict:
    """XBRL-to-JSON fuer ein Filing — komplette Finanzberichte als dict."""
    try:
        resp = requests.get(
            XBRL_URL,
            params={"accession-no": accession_no, "token": _api_key()},
            timeout=40)
    except requests.RequestException as e:
        raise SecApiError(f"XBRL-to-JSON-Request fehlgeschlagen: {e}") from e
    if resp.status_code != 200:
        raise SecApiError(
            f"XBRL-to-JSON HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json() or {}


# ---------- XBRL -> GuV-Kernzeilen ----------

def _pick(stmt: dict, concepts: list[str],
          period_end: str) -> tuple[float | None, int | None]:
    """Bestpassende Fact zu period_end — (Wert, Dauer in Tagen).

    Iteriert die Concept-Kandidaten in Prioritaets-Reihenfolge; nimmt den
    ersten Concept mit Treffer und davon die Fact mit der kuerzesten Dauer
    (= das Quartal bei 10-Q, das Jahr bei 10-K). Segment-dimensionierte
    Fakten werden ignoriert.
    """
    for c in concepts:
        matched: list[tuple[int, float]] = []
        for fct in stmt.get(c, []) or []:
            if fct.get("segment"):
                continue
            p = fct.get("period") or {}
            if p.get("endDate") != period_end:
                continue
            try:
                v = float(fct["value"])
            except (KeyError, TypeError, ValueError):
                continue
            dur = 10 ** 6
            start = p.get("startDate")
            if start:
                try:
                    dur = (date.fromisoformat(p["endDate"])
                           - date.fromisoformat(start)).days
                except ValueError:
                    dur = 10 ** 6
            matched.append((dur, v))
        if matched:
            matched.sort(key=lambda t: t[0])
            dur, val = matched[0]
            return val, (None if dur >= 10 ** 6 else dur)
    return None, None


def _humanize(qname: str) -> str:
    """XBRL-QName -> menschenlesbarer Name.

      'nvda:ComputeAndNetworkingSegmentMember' -> 'Compute And Networking'
      'us-gaap:StatementBusinessSegmentsAxis'  -> 'Statement Business Segments'
    """
    s = qname.split(":")[-1] if ":" in qname else qname
    for suf in ("Member", "Segment", "Axis"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    s = re.sub(r"(?<!^)(?=[A-Z])", " ", s)
    return s.strip()


def _normalize_members(seg) -> list[tuple[str, str]]:
    """Robust: (dimension, member)-Tupel aus seg-Objekt extrahieren.

    Unterstuetzt drei Shapes:
      - Apple:  {'dimension': '...', 'value': '...'}
      - NVIDIA: {'explicitMember': {'dimension':'...','$t':'...'}}
      - NVIDIA: {'explicitMember': [{...}, {...}]}    (multi-dim)
    """
    if not seg:
        return []
    if isinstance(seg, list):
        out: list[tuple[str, str]] = []
        for s in seg:
            out.extend(_normalize_members(s))
        return out
    if not isinstance(seg, dict):
        return []
    # Apple-Shape — Dimension + Wert direkt im seg-Dict.
    if "dimension" in seg and ("$t" in seg or "value" in seg):
        dim = seg.get("dimension")
        mem = seg.get("$t") or seg.get("value")
        return [(dim, mem)] if dim and mem else []
    # NVIDIA-Shape — unter explicitMember.
    em = seg.get("explicitMember")
    if em is None:
        return []
    items = em if isinstance(em, list) else [em]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        dim = it.get("dimension")
        mem = it.get("$t") or it.get("value")
        if dim and mem:
            out.append((dim, mem))
    return out


def extract_revenue_segments(stmt: dict, period_end: str,
                              period_months: int | None = 3) -> list[dict]:
    """Saubere Ein-Achsen-Aufschluesselungen des Umsatzes.

    Strategie:
      - Auf den Revenue-Concepts alle dimensionierten Fakten sammeln.
      - Consolidation-Qualifier (z.B. srt:ConsolidationItemsAxis) ignorieren.
      - Nur Fakten mit GENAU EINER verbleibenden Achse aufnehmen
        (cross-tabulierte Werte rausfiltern — die sind Schnittpunkte).
      - Periode == period_end, Dauer passend zu period_months.
      - Pro (Achse, Member) den ersten Treffer nehmen (XBRL dedupliziert
        durch Member-Eindeutigkeit ohnehin).

    Returns: [{axis, member, member_label, value}, ...].
    """
    # Erlaubte Dauer-Spanne in Tagen.
    if period_months == 12:
        dur_min, dur_max = 330, 400
    else:                                  # default: Quartal
        dur_min, dur_max = 60, 100

    seen: dict[tuple[str, str], dict] = {}
    for concept in _REVENUE:
        for fct in stmt.get(concept, []) or []:
            seg = fct.get("segment")
            if not seg:
                continue
            members = _normalize_members(seg)
            real = [(d, m) for d, m in members
                    if d not in _CONSOLIDATION_AXES]
            if len(real) != 1:
                continue                   # 0 = nur Total, >1 = cross-tab
            dim, mem = real[0]
            p = fct.get("period") or {}
            if p.get("endDate") != period_end:
                continue
            start = p.get("startDate")
            if start:
                try:
                    dur = (date.fromisoformat(p["endDate"])
                           - date.fromisoformat(start)).days
                except ValueError:
                    continue
                if not (dur_min <= dur <= dur_max):
                    continue
            try:
                v = float(fct["value"])
            except (KeyError, TypeError, ValueError):
                continue
            key = (dim, mem)
            if key in seen:
                continue
            seen[key] = {
                "axis":         dim,
                "member":       mem,
                "member_label": _humanize(mem),
                "value":        v,
            }
    return list(seen.values())


def map_income_statement(stmt: dict, period_end: str) -> IncomeStatement:
    """StatementsOfIncome-dict -> IncomeStatement mit GuV-Kernzeilen."""
    inc = IncomeStatement(period_end=period_end)

    inc.revenue,          dur = _pick(stmt, _REVENUE, period_end)
    inc.cost_of_revenue,  _   = _pick(stmt, _COGS,    period_end)
    inc.gross_profit,     _   = _pick(stmt, _GROSS,   period_end)
    inc.rd_expense,       _   = _pick(stmt, _RD,      period_end)
    inc.sga_expense,      _   = _pick(stmt, _SGA,     period_end)
    inc.operating_expense, _  = _pick(stmt, _OPEX,    period_end)
    inc.operating_income, _   = _pick(stmt, _OPINC,   period_end)
    inc.pretax_income,    _   = _pick(stmt, _PRETAX,  period_end)
    inc.tax_expense,      _   = _pick(stmt, _TAX,     period_end)
    inc.net_income,       _   = _pick(stmt, _NET,     period_end)

    # Ableitungen, falls eine Zeile im XBRL fehlt
    if inc.gross_profit is None and \
            inc.revenue is not None and inc.cost_of_revenue is not None:
        inc.gross_profit = inc.revenue - inc.cost_of_revenue
        inc.warnings.append("gross_profit abgeleitet (revenue - cost_of_revenue)")
    if inc.operating_expense is None and \
            inc.rd_expense is not None and inc.sga_expense is not None:
        inc.operating_expense = inc.rd_expense + inc.sga_expense
        inc.warnings.append("operating_expense abgeleitet (rd + sga)")
    if inc.operating_income is None and \
            inc.gross_profit is not None and inc.operating_expense is not None:
        inc.operating_income = inc.gross_profit - inc.operating_expense
        inc.warnings.append("operating_income abgeleitet (gross - opex)")
    if inc.pretax_income is not None and inc.operating_income is not None:
        inc.other_income = inc.pretax_income - inc.operating_income

    if dur is not None:
        inc.period_months = 12 if dur > 200 else 3
    return inc


def fetch_income_from_filing(filing: dict) -> IncomeStatement | None:
    """Aus einem konkreten Filing-Record (find_filings-Output) die GuV holen.

    Returns None, wenn das Filing keine verwertbare StatementsOfIncome-Sektion
    hat oder die Pflichtfelder fehlen.
    """
    if not filing or not filing.get("accession_no") \
            or not filing.get("period_of_report"):
        return None
    xbrl = fetch_xbrl(filing["accession_no"])
    stmt = xbrl.get("StatementsOfIncome") or {}
    if not stmt:
        return None
    inc = map_income_statement(stmt, filing["period_of_report"])
    inc.accession_no = filing["accession_no"]
    inc.form_type    = filing["form_type"]
    inc.filed_at     = filing["filed_at"]
    inc.segments     = extract_revenue_segments(
        stmt, filing["period_of_report"], inc.period_months)
    if inc.revenue is None and inc.net_income is None:
        return None
    return inc


def fetch_income(ticker: str) -> IncomeStatement | None:
    """End-to-End: juengstes Filing finden + GuV extrahieren."""
    filing = find_latest_filing(ticker)
    if not filing:
        return None
    return fetch_income_from_filing(filing)
