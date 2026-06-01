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


QUERY_URL   = "https://api.sec-api.io"
XBRL_URL    = "https://api.sec-api.io/xbrl-to-json"
INSIDER_URL = "https://api.sec-api.io/insider-trading"


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


# ---------- Bilanz (Balance Sheet) ----------

_CASH        = ["CashAndCashEquivalentsAtCarryingValue",
                "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
_STI         = ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                "AvailableForSaleSecuritiesCurrent"]
_ASSETS_CUR  = ["AssetsCurrent"]
_LIAB_CUR    = ["LiabilitiesCurrent"]
_ASSETS      = ["Assets"]
_LIAB        = ["Liabilities"]
_EQUITY      = ["StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
_LT_DEBT     = ["LongTermDebtNoncurrent", "LongTermDebt"]
_CUR_DEBT    = ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"]
_INVENTORY   = ["InventoryNet"]
_GOODWILL    = ["Goodwill"]
_INTANGIBLES = ["IntangibleAssetsNetExcludingGoodwill",
                "FiniteLivedIntangibleAssetsNet"]


@dataclass
class BalanceSheet:
    """Bilanz-Kernzeilen eines Filings — Stichtagswerte (instant)."""
    period_end:    str | None = None
    form_type:     str | None = None
    accession_no:  str | None = None
    filed_at:      str | None = None
    currency:      str = "USD"

    cash:                  float | None = None
    short_term_invest:     float | None = None
    assets_current:        float | None = None
    liabilities_current:   float | None = None
    total_assets:          float | None = None
    total_liabilities:     float | None = None
    equity:                float | None = None
    long_term_debt:        float | None = None
    current_debt:          float | None = None
    inventory:             float | None = None
    goodwill:              float | None = None
    intangibles:           float | None = None

    warnings: list[str] = field(default_factory=list)

    @property
    def cash_and_sti(self) -> float | None:
        vals = [v for v in (self.cash, self.short_term_invest)
                if v is not None]
        return sum(vals) if vals else None

    @property
    def total_debt(self) -> float | None:
        vals = [v for v in (self.long_term_debt, self.current_debt)
                if v is not None]
        return sum(vals) if vals else None

    @property
    def net_debt(self) -> float | None:
        td, cash = self.total_debt, self.cash_and_sti
        if td is None:
            return None
        return td - (cash or 0.0)


def _pick_instant(stmt: dict, concepts: list[str],
                  period_end: str) -> float | None:
    """Erster Treffer eines Concepts zum Stichtag (instant ODER endDate).

    Bilanzfakten tragen meist period={'instant': date}; manche Filings
    nutzen endDate. Segment-dimensionierte Fakten werden ignoriert.
    """
    for c in concepts:
        for fct in stmt.get(c, []) or []:
            if fct.get("segment"):
                continue
            p = fct.get("period") or {}
            if (p.get("instant") or p.get("endDate")) != period_end:
                continue
            try:
                return float(fct["value"])
            except (KeyError, TypeError, ValueError):
                continue
    return None


def map_balance_sheet(stmt: dict, period_end: str) -> BalanceSheet:
    """BalanceSheets-dict -> BalanceSheet mit Kernzeilen zum Stichtag."""
    bs = BalanceSheet(period_end=period_end)
    bs.cash                = _pick_instant(stmt, _CASH,       period_end)
    bs.short_term_invest   = _pick_instant(stmt, _STI,        period_end)
    bs.assets_current      = _pick_instant(stmt, _ASSETS_CUR, period_end)
    bs.liabilities_current = _pick_instant(stmt, _LIAB_CUR,   period_end)
    bs.total_assets        = _pick_instant(stmt, _ASSETS,     period_end)
    bs.total_liabilities   = _pick_instant(stmt, _LIAB,       period_end)
    bs.equity              = _pick_instant(stmt, _EQUITY,     period_end)
    bs.long_term_debt      = _pick_instant(stmt, _LT_DEBT,    period_end)
    bs.current_debt        = _pick_instant(stmt, _CUR_DEBT,   period_end)
    bs.inventory           = _pick_instant(stmt, _INVENTORY,  period_end)
    bs.goodwill            = _pick_instant(stmt, _GOODWILL,   period_end)
    bs.intangibles         = _pick_instant(stmt, _INTANGIBLES, period_end)

    # Ableitung: Gesamtverbindlichkeiten aus Aktiva - Eigenkapital
    if bs.total_liabilities is None and \
            bs.total_assets is not None and bs.equity is not None:
        bs.total_liabilities = bs.total_assets - bs.equity
        bs.warnings.append(
            "total_liabilities abgeleitet (assets - equity)")
    return bs


def fetch_balance_sheet_from_filing(filing: dict) -> BalanceSheet | None:
    """Aus einem Filing-Record (find_filings-Output) die Bilanz holen."""
    if not filing or not filing.get("accession_no") \
            or not filing.get("period_of_report"):
        return None
    xbrl = fetch_xbrl(filing["accession_no"])
    stmt = xbrl.get("BalanceSheets") or {}
    if not stmt:
        return None
    bs = map_balance_sheet(stmt, filing["period_of_report"])
    bs.accession_no = filing["accession_no"]
    bs.form_type    = filing["form_type"]
    bs.filed_at     = filing["filed_at"]
    if bs.total_assets is None and bs.equity is None:
        return None
    return bs


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


def fetch_statements_from_filing(
    filing: dict,
) -> tuple[IncomeStatement | None, BalanceSheet | None]:
    """GuV + Bilanz aus EINEM XBRL-Call (spart API-Aufrufe).

    Returns (income, balance) — jeweils None, wenn die Sektion fehlt oder
    keine verwertbaren Pflichtfelder enthaelt.
    """
    if not filing or not filing.get("accession_no") \
            or not filing.get("period_of_report"):
        return None, None
    xbrl = fetch_xbrl(filing["accession_no"])
    period = filing["period_of_report"]

    inc = None
    inc_stmt = xbrl.get("StatementsOfIncome") or {}
    if inc_stmt:
        inc = map_income_statement(inc_stmt, period)
        inc.accession_no = filing["accession_no"]
        inc.form_type    = filing["form_type"]
        inc.filed_at     = filing["filed_at"]
        inc.segments     = extract_revenue_segments(
            inc_stmt, period, inc.period_months)
        if inc.revenue is None and inc.net_income is None:
            inc = None

    bs = None
    bs_stmt = xbrl.get("BalanceSheets") or {}
    if bs_stmt:
        bs = map_balance_sheet(bs_stmt, period)
        bs.accession_no = filing["accession_no"]
        bs.form_type    = filing["form_type"]
        bs.filed_at     = filing["filed_at"]
        if bs.total_assets is None and bs.equity is None:
            bs = None

    return inc, bs


# ---------- GAAP vs non-GAAP (Earnings-8-K Exhibit 99) ----------

# Anpassungs-Kategorien -> Schluesselwoerter (lowercase). SBC als Add-back
# ist das klassische Aggressivitaets-Signal (echte, wiederkehrende Kosten).
NON_GAAP_ADJUSTMENTS = {
    "Aktienverguetung (SBC)": ["stock-based compensation",
                               "share-based compensation",
                               "stock compensation",
                               "stock-based comp"],
    "Amortisation immaterieller": ["amortization of acquired intangible",
                                   "amortization of intangible",
                                   "intangible amortization",
                                   "amortization of purchased intangible"],
    "Restrukturierung": ["restructuring"],
    "Akquisitionskosten": ["acquisition-related", "acquisition related",
                           "transaction costs", "acquisition costs"],
    "Wertminderung": ["impairment"],
    "Rechtsstreit/Settlement": ["litigation", "legal settlement"],
    "Steueranpassungen": ["discrete tax", "tax effects of",
                          "non-gaap tax", "income tax adjustment"],
    "Waehrung (constant currency)": ["constant currency"],
}

_NON_GAAP_TERMS = ["non-gaap", "non gaap", "adjusted", "ebitda",
                   "core earnings", "free cash flow"]
_TAG_RE = re.compile(r"<[^>]+>")
_EPS_RE = re.compile(r"\$\s?\d{1,3}(?:[.,]\d{1,2})")


def _query_raw(query: str, n: int = 5) -> list[dict]:
    """Roh-Filings der Query-API (komplette Records inkl. Dokumente)."""
    payload = {"query": query, "from": "0", "size": str(max(1, int(n))),
               "sort": [{"filedAt": {"order": "desc"}}]}
    try:
        resp = requests.post(QUERY_URL, json=payload,
                             headers={"Authorization": _api_key()},
                             timeout=20)
    except requests.RequestException as e:
        raise SecApiError(f"Query-API-Request fehlgeschlagen: {e}") from e
    if resp.status_code != 200:
        raise SecApiError(
            f"Query-API HTTP {resp.status_code}: {resp.text[:200]}")
    return (resp.json() or {}).get("filings", [])


def _pick_earnings_exhibit(docs: list[dict]) -> str | None:
    """Bevorzugt EX-99.1, dann EX-99 — sonst erstes Dokument mit '99'."""
    for d in docs or []:
        t = (d.get("type") or "").lower()
        if t.startswith("ex-99.1") or t.startswith("ex-99.01"):
            return d.get("documentUrl")
    for d in docs or []:
        if (d.get("type") or "").lower().startswith("ex-99"):
            return d.get("documentUrl")
    for d in docs or []:
        if "99" in (d.get("description") or ""):
            return d.get("documentUrl")
    return None


def find_earnings_exhibits(ticker: str, *, n: int = 4) -> list[dict]:
    """Juengste Earnings-8-K (Item 2.02) mit Exhibit-99-URL."""
    filings = _query_raw(
        f'ticker:{ticker} AND formType:"8-K" AND items:"2.02"', n)
    out = []
    for f in filings:
        out.append({
            "accession_no": f.get("accessionNo"),
            "filed_at":     f.get("filedAt"),
            "exhibit_url":  _pick_earnings_exhibit(
                f.get("documentFormatFiles") or []),
            "link":         f.get("linkToFilingDetails"),
        })
    return out


# SEC verlangt einen User-Agent mit Kontakt (Fair-Access-Policy); ein
# generischer UA fuehrt zu HTTP 403. Primaer laeuft der Download aber ueber
# die authentifizierte Proxy von sec-api.io (archive.sec-api.io + token).
_SEC_UA = "nova-lab research admin@nova-lab.local"
_ARCHIVE_PROXY = "https://archive.sec-api.io/"


def _to_archive_proxy(url: str) -> str | None:
    """www.sec.gov/Archives/<path> -> archive.sec-api.io/<path> (Proxy)."""
    marker = "/Archives/"
    if marker in url:
        return _ARCHIVE_PROXY + url.split(marker, 1)[1]
    return None


def fetch_exhibit_text(url: str) -> str:
    """Exhibit-HTML laden und zu Plain-Text reduzieren.

    Primaerpfad: sec-api.io-Download-Proxy (authentifiziert, kein SEC-UA-
    Blocking). Fallback: direkter SEC-Abruf mit konformem User-Agent.
    """
    if not url:
        return ""
    proxy = _to_archive_proxy(url)
    try:
        if proxy:
            resp = requests.get(proxy, params={"token": _api_key()},
                                timeout=30)
        else:
            resp = requests.get(url, headers={"User-Agent": _SEC_UA},
                                timeout=30)
        # Fallback auf direkten SEC-Abruf, falls die Proxy scheitert
        if proxy and resp.status_code != 200:
            resp = requests.get(url, headers={"User-Agent": _SEC_UA},
                                timeout=30)
    except requests.RequestException as e:
        raise SecApiError(f"Exhibit-Download fehlgeschlagen: {e}") from e
    if resp.status_code != 200:
        raise SecApiError(
            f"Exhibit HTTP {resp.status_code}: {resp.text[:120]}")
    text = _TAG_RE.sub(" ", resp.text)
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def analyze_non_gaap(text: str) -> dict:
    """Heuristische Auswertung eines Earnings-Exhibit-Texts.

    Returns dict:
      uses_non_gaap : bool
      mentions      : int   (Treffer ueber alle Non-GAAP-Begriffe)
      categories    : {label: count}  (gefundene Anpassungs-Kategorien)
      adds_back_sbc : bool
      amounts       : [str] (Best-effort Dollar-/EPS-Beträge nahe 'non-gaap')
    """
    low = (text or "").lower()
    mentions = sum(low.count(t) for t in _NON_GAAP_TERMS)
    categories: dict[str, int] = {}
    for label, kws in NON_GAAP_ADJUSTMENTS.items():
        c = sum(low.count(k) for k in kws)
        if c:
            categories[label] = c

    # Best-effort: Dollar-Betraege im Umfeld von 'non-gaap'
    amounts: list[str] = []
    idx = low.find("non-gaap")
    scan = 0
    while idx != -1 and len(amounts) < 8 and scan < 20:
        window = text[idx:idx + 160]
        for m in _EPS_RE.findall(window):
            amounts.append(m.strip())
        idx = low.find("non-gaap", idx + 1)
        scan += 1

    return {
        "uses_non_gaap": mentions > 0,
        "mentions":      mentions,
        "categories":    categories,
        "adds_back_sbc": "Aktienverguetung (SBC)" in categories,
        "amounts":       list(dict.fromkeys(amounts)),   # dedupe, Reihenfolge
    }


# ---------- Stock-based Compensation / Cashflow ----------

_SBC = ["ShareBasedCompensation",
        "ShareBasedCompensationExpense",
        "AllocatedShareBasedCompensationExpense"]
_CFO = ["NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]
_DILUTED_SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding",
                   "WeightedAverageNumberOfShareOutstandingBasicAndDiluted"]


def fetch_sbc_from_filing(filing: dict) -> dict | None:
    """SBC + Kontext aus EINEM XBRL-Call.

    Returns dict {period_end, form_type, filed_at, sbc, cfo, revenue,
    net_income, diluted_shares} oder None, wenn weder SBC noch Umsatz
    auffindbar sind.
    """
    if not filing or not filing.get("accession_no") \
            or not filing.get("period_of_report"):
        return None
    xbrl = fetch_xbrl(filing["accession_no"])
    period = filing["period_of_report"]
    cf = xbrl.get("StatementsOfCashFlows") or {}
    inc = xbrl.get("StatementsOfIncome") or {}

    sbc, _ = _pick(cf, _SBC, period)
    cfo, _ = _pick(cf, _CFO, period)
    revenue, _ = _pick(inc, _REVENUE, period)
    net_income, _ = _pick(inc, _NET, period)
    diluted, _ = _pick(inc, _DILUTED_SHARES, period)

    if sbc is None and revenue is None:
        return None
    return {
        "period_end":     period,
        "form_type":      filing["form_type"],
        "filed_at":       filing["filed_at"],
        "sbc":            sbc,
        "cfo":            cfo,
        "revenue":        revenue,
        "net_income":     net_income,
        "diluted_shares": diluted,
    }


# ---------- Gewinnruecklagen + EPS (Verlauf) ----------

_RETAINED = ["RetainedEarningsAccumulatedDeficit"]
_EPS_BASIC = ["EarningsPerShareBasic",
              "IncomeLossFromContinuingOperationsPerBasicShare"]
_EPS_DILUTED = ["EarningsPerShareDiluted",
                "IncomeLossFromContinuingOperationsPerDilutedShare"]
_CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment",
          "PaymentsToAcquireProductiveAssets",
          "PaymentsForCapitalImprovements"]


def fetch_earnings_history_from_filing(filing: dict) -> dict | None:
    """Gewinnruecklagen, EPS, Eigenkapital, Free Cashflow + Net-Debt-Bausteine
    aus EINEM XBRL-Call.

    RetainedEarnings/Equity/Debt/Cash sind Stichtagswerte (Bilanz, instant);
    EPS, CFO, CapEx sind Perioden-Fakten (duration). Net Debt + verwaesserte
    Aktien werden mitgeliefert, damit die View daraus (mit Marktpreis) den
    Enterprise Value bilden kann. Returns None, wenn nichts auffindbar.
    """
    if not filing or not filing.get("accession_no") \
            or not filing.get("period_of_report"):
        return None
    xbrl = fetch_xbrl(filing["accession_no"])
    period = filing["period_of_report"]
    bs_stmt = xbrl.get("BalanceSheets") or {}
    inc = xbrl.get("StatementsOfIncome") or {}
    cf = xbrl.get("StatementsOfCashFlows") or {}

    retained = _pick_instant(bs_stmt, _RETAINED, period)
    eps_basic, _ = _pick(inc, _EPS_BASIC, period)
    eps_diluted, _ = _pick(inc, _EPS_DILUTED, period)
    net_income, _ = _pick(inc, _NET, period)
    diluted_shares, _ = _pick(inc, _DILUTED_SHARES, period)

    bs = map_balance_sheet(bs_stmt, period) if bs_stmt else None
    equity = bs.equity if bs else None
    net_debt = bs.net_debt if bs else None
    total_debt = bs.total_debt if bs else None

    cfo, _ = _pick(cf, _CFO, period)
    capex, _ = _pick(cf, _CAPEX, period)
    fcf = (cfo - capex) if (cfo is not None and capex is not None) else None

    if retained is None and eps_basic is None and eps_diluted is None \
            and equity is None and fcf is None:
        return None
    return {
        "period_end":  period,
        "form_type":   filing["form_type"],
        "filed_at":    filing["filed_at"],
        "retained_earnings": retained,
        "eps_basic":   eps_basic,
        "eps_diluted": eps_diluted,
        "net_income":  net_income,
        "equity":      equity,
        "cfo":         cfo,
        "capex":       capex,
        "fcf":         fcf,
        "net_debt":    net_debt,
        "total_debt":  total_debt,
        "diluted_shares": diluted_shares,
    }


# ---------- Insider Trading (Form 3/4/5) ----------

# Transaktions-Codes (SEC Form 4): die diskretionaeren Markt-Trades sind
# P (Kauf) und S (Verkauf). A/M/F/G/C sind Awards, Ausuebungen, Steuer-
# einbehalte, Schenkungen, Wandlungen — kein echtes Buy/Sell-Signal.
INSIDER_CODE_LABELS = {
    "P": "Kauf (Markt)",
    "S": "Verkauf (Markt)",
    "A": "Zuteilung/Grant",
    "M": "Optionsausuebung",
    "F": "Steuereinbehalt",
    "G": "Schenkung",
    "C": "Wandlung",
    "D": "Rueckgabe an Emittent",
}


def _relationship_label(rel: dict | None) -> str:
    if not isinstance(rel, dict):
        return "—"
    parts = []
    if rel.get("isDirector"):
        parts.append("Director")
    if rel.get("isOfficer"):
        parts.append(rel.get("officerTitle") or "Officer")
    if rel.get("isTenPercentOwner"):
        parts.append("10%-Eigner")
    if rel.get("isOther") and not parts:
        parts.append(rel.get("otherText") or "Sonstige")
    return ", ".join(parts) if parts else "—"


def _flatten_insider_record(rec: dict) -> list[dict]:
    """Ein Form-4-Record -> flache Transaktionszeilen (non-deriv + deriv)."""
    owner = rec.get("reportingOwner") or {}
    owner_name = owner.get("name") or "—"
    rel = _relationship_label(owner.get("relationship"))
    filed_at = rec.get("filedAt")
    out: list[dict] = []
    for tbl_key, is_deriv in (("nonDerivativeTable", False),
                              ("derivativeTable", True)):
        tbl = rec.get(tbl_key) or {}
        for tx in (tbl.get("transactions") or []):
            coding = tx.get("coding") or {}
            amounts = tx.get("amounts") or {}
            try:
                shares = float(amounts.get("shares")) \
                    if amounts.get("shares") is not None else None
            except (TypeError, ValueError):
                shares = None
            try:
                price = float(amounts.get("pricePerShare")) \
                    if amounts.get("pricePerShare") is not None else None
            except (TypeError, ValueError):
                price = None
            value = (shares * price
                     if shares is not None and price is not None else None)
            out.append({
                "filed_at":      filed_at,
                "owner":         owner_name,
                "relationship":  rel,
                "transaction_date": (tx.get("transactionDate") or "")[:10],
                "code":          coding.get("code"),
                "shares":        shares,
                "price":         price,
                "value":         value,
                "acquired_disposed": amounts.get("acquiredDisposedCode"),
                "security":      tx.get("securityTitle"),
                "derivative":    is_deriv,
            })
    return out


_INSIDER_PAGE = 50          # API-Hardlimit fuer 'size'


def fetch_insider_transactions(ticker: str, *, n: int = 300) -> list[dict]:
    """Bis zu N juengste Form-3/4/5-Records -> flache Transaktionsliste.

    Der Insider-Endpoint erlaubt max. size=50 pro Request; daher wird in
    50er-Schritten ueber 'from' paginiert. Leere Liste, wenn keine
    Insider-Filings vorliegen (z.B. ETFs, nicht US-gelistete Werte).
    """
    rows: list[dict] = []
    fetched = 0
    target = max(1, int(n))
    auth = {"Authorization": _api_key()}
    while fetched < target:
        page = min(_INSIDER_PAGE, target - fetched)
        payload = {
            "query": f"issuer.tradingSymbol:{ticker}",
            "from":  str(fetched),
            "size":  str(page),
            "sort":  [{"filedAt": {"order": "desc"}}],
        }
        try:
            resp = requests.post(
                INSIDER_URL, json=payload, headers=auth, timeout=30)
        except requests.RequestException as e:
            raise SecApiError(
                f"Insider-API-Request fehlgeschlagen: {e}") from e
        if resp.status_code != 200:
            raise SecApiError(
                f"Insider-API HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json() or {}
        records = body.get("transactions") or body.get("data") or []
        if not records:
            break
        for rec in records:
            rows.extend(_flatten_insider_record(rec))
        fetched += len(records)
        if len(records) < page:     # letzte Seite erreicht
            break
    return rows
