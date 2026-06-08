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


QUERY_URL    = "https://api.sec-api.io"
XBRL_URL     = "https://api.sec-api.io/xbrl-to-json"
INSIDER_URL  = "https://api.sec-api.io/insider-trading"
FORM13F_URL  = "https://api.sec-api.io/form-13f/holdings"


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

def _sec_ticker(ticker: str) -> str:
    """Symbol -> sec-api/EDGAR-Ticker normalisieren.

    Datenvendoren (IB) kodieren Class-Shares mit Unterstrich (BRK_B); sec-api
    erwartet den Punkt (BRK.B). US-Ticker enthalten nie '_', daher ist die
    Ersetzung eindeutig sicher. Whitespace/Case ebenfalls bereinigt.
    """
    return (ticker or "").strip().upper().replace("_", ".")


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
    ticker = _sec_ticker(ticker)
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
    "Einmaleffekte": ["one-time", "one time", "nonrecurring",
                      "non-recurring", "special item", "special charge",
                      "unusual item"],
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


def _pick_8k_doc(docs: list[dict]) -> str | None:
    """Primaeres 8-K-Body-Dokument (type == '8-K')."""
    for d in docs or []:
        if (d.get("type") or "").lower().startswith("8-k"):
            return d.get("documentUrl")
    return None


def find_8k_filings(ticker: str, *, n: int = 2) -> list[dict]:
    """Juengste 8-K eines Tickers mit Items + bestem Text-Dokument.

    text_url-Praeferenz: EX-99.1/EX-99-Exhibit (Press-Release) > primaeres
    8-K-Body-Dokument > linkToFilingDetails. items ist die (Roh-)Liste der
    8-K-Item-Bezeichnungen aus sec-api (z.B. ["Item 2.02 ...", "Item 9.01 ..."]).
    Schluessel kompatibel zu find_filings (accession_no/period_of_report).
    """
    ticker = _sec_ticker(ticker)
    filings = _query_raw(f'ticker:{ticker} AND formType:"8-K"', n)
    out = []
    for f in filings:
        docs = f.get("documentFormatFiles") or []
        text_url = (_pick_earnings_exhibit(docs) or _pick_8k_doc(docs)
                    or f.get("linkToFilingDetails"))
        out.append({
            "accession_no":     f.get("accessionNo"),
            "form_type":        f.get("formType"),
            "period_of_report": f.get("periodOfReport"),
            "filed_at":         f.get("filedAt"),
            "ticker":           f.get("ticker"),
            "items":            f.get("items") or [],
            "description":      f.get("description") or "",
            "text_url":         text_url,
        })
    return out


# SEC verlangt einen User-Agent mit Kontakt (Fair-Access-Policy); ein
# generischer UA fuehrt zu HTTP 403. Primaer laeuft der Download aber ueber
# die authentifizierte Proxy von sec-api.io (archive.sec-api.io + token).
_SEC_UA = "nova-lab research admin@nova-lab.local"
_ARCHIVE_PROXY = "https://archive.sec-api.io/"


def _unwrap_ix(url: str) -> str:
    """Inline-XBRL-Viewer-URL entpacken: '.../ix?doc=/Archives/x' ->
    'https://www.sec.gov/Archives/x'. Sonst unveraendert."""
    if "ix?doc=" in url:
        frag = url.split("ix?doc=", 1)[1]
        return ("https://www.sec.gov" + frag if frag.startswith("/")
                else frag)
    return url


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
    url = _unwrap_ix(url)
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


# ---------- Komplette Jahres-Metriken (fuer Moat-Score) ----------

def fetch_concept_series(cik, taxonomy: str, tag: str) -> dict:
    """XBRL-Zeitreihe via SEC company-concept-API (10-K-Kontexte).

    Returns {end_date_iso: wert}. {} bei Fehler/404. Robuste Quelle, wenn
    xbrl-to-json ein Konzept nicht (mehr) am erwarteten Ort fuehrt.
    """
    if cik in (None, "", 0):
        return {}
    c10 = str(cik).lstrip("0").zfill(10)
    url = (f"https://data.sec.gov/api/xbrl/companyconcept/CIK{c10}"
           f"/{taxonomy}/{tag}.json")
    try:
        resp = requests.get(url, headers={
            "User-Agent": _SEC_UA, "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov"}, timeout=30)
    except requests.RequestException:
        return {}
    if resp.status_code != 200:
        return {}
    units = (resp.json() or {}).get("units") or {}
    out: dict = {}
    for items in units.values():
        for it in items or []:
            end, val, form = it.get("end"), it.get("val"), it.get("form", "")
            if end is None or val is None or "10-K" not in (form or ""):
                continue
            try:
                out[end] = float(val)
            except (TypeError, ValueError):
                pass
    return out


def fetch_employee_counts_detail(cik) -> dict:
    """dei:EntityNumberOfEmployees-Zeitreihe via SEC company-concept-API,
    mit Diagnose. Returns {map, url, status, error}."""
    out = {"map": {}, "url": None, "status": None, "error": None}
    if cik in (None, "", 0):
        out["error"] = "keine CIK"
        return out
    c10 = str(cik).lstrip("0").zfill(10)
    url = (f"https://data.sec.gov/api/xbrl/companyconcept/CIK{c10}"
           "/dei/EntityNumberOfEmployees.json")
    out["url"] = url
    # data.sec.gov ueber sec-api-Archiv-Proxy nicht erreichbar -> direkt,
    # konformer User-Agent.
    try:
        resp = requests.get(url, headers={
            "User-Agent": _SEC_UA, "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov"}, timeout=30)
    except requests.RequestException as e:
        out["error"] = f"Request: {e}"
        return out
    out["status"] = resp.status_code
    if resp.status_code != 200:
        out["error"] = f"HTTP {resp.status_code}: {resp.text[:120]}"
        return out
    units = (resp.json() or {}).get("units") or {}
    m: dict = {}
    for items in units.values():
        for it in items or []:
            end, val, form = it.get("end"), it.get("val"), it.get("form", "")
            if end is None or val is None or "10-K" not in (form or ""):
                continue
            try:
                m[end] = float(val)
            except (TypeError, ValueError):
                pass
    out["map"] = m
    if not m:
        out["error"] = "keine 10-K-Mitarbeiterwerte in der Antwort"
    return out


def fetch_employee_counts(cik) -> dict:
    """Duenne Schale: nur die {end_iso: anzahl}-Map."""
    return fetch_employee_counts_detail(cik).get("map", {})


def _pick_employees(xbrl: dict, period: str):
    """dei:EntityNumberOfEmployees ueber alle XBRL-Sektionen suchen."""
    for sect in xbrl.values():
        if not isinstance(sect, dict):
            continue
        for concept, facts in sect.items():
            if "NumberOfEmployees" not in concept \
                    or not isinstance(facts, list):
                continue
            best = None
            for f in facts:
                if not isinstance(f, dict):
                    continue
                p = f.get("period") or {}
                d = p.get("instant") or p.get("endDate")
                try:
                    v = float(f["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                if d == period:
                    return v
                best = v
            if best is not None:
                return best
    return None

# Kapitalallokation (Cashflow, als positive Mittelabfluesse berichtet).
_BUYBACKS = ["PaymentsForRepurchaseOfCommonStock",
             "PaymentsForRepurchaseOfEquity"]
_DIVIDENDS = ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends",
              "Dividends", "DividendsCommonStockCash"]
_ACQUISITIONS = ["PaymentsToAcquireBusinessesNetOfCashAcquired",
                 "PaymentsToAcquireBusinessesAndInterestInAffiliates",
                 "PaymentsToAcquireBusinessesGross"]
# Abschreibungen (D&A) — kombinierte Concepts zuerst, sonst Summe Einzeln.
_DA = ["DepreciationDepletionAndAmortization",
       "DepreciationAmortizationAndAccretionNet",
       "DepreciationAndAmortization",
       "DepreciationAmortizationAndDepletion"]
_DEP_ONLY = ["Depreciation", "DepreciationNonproduction"]
_AMORT_ONLY = ["AmortizationOfIntangibleAssets",
               "AmortizationOfDeferredCharges"]
# Sachanlagen fuer Greenwald-Kapitalintensitaet (Brutto bevorzugt).
_PPE_GROSS = ["PropertyPlantAndEquipmentGross"]
_PPE_NET = ["PropertyPlantAndEquipmentNet"]
_SHARES_OUT = ["CommonStockSharesOutstanding", "CommonStockSharesIssued"]


def fetch_year_metrics_from_filing(filing: dict) -> dict | None:
    """GuV + Bilanz + Cashflow eines Geschaeftsjahres aus EINEM XBRL-Call.

    Liefert alle Bausteine fuer den Moat-Score: Marge, ROIC, FCF, F&E,
    Aktienzahl. Returns None, wenn weder Umsatz noch Nettogewinn da sind.
    """
    if not filing or not filing.get("accession_no") \
            or not filing.get("period_of_report"):
        return None
    xbrl = fetch_xbrl(filing["accession_no"])
    period = filing["period_of_report"]
    inc_stmt = xbrl.get("StatementsOfIncome") or {}
    bs_stmt = xbrl.get("BalanceSheets") or {}
    cf = xbrl.get("StatementsOfCashFlows") or {}

    inc = map_income_statement(inc_stmt, period) if inc_stmt else None
    bs = map_balance_sheet(bs_stmt, period) if bs_stmt else None
    if inc is None and bs is None:
        return None

    cfo, _ = _pick(cf, _CFO, period)
    capex, _ = _pick(cf, _CAPEX, period)
    fcf = (cfo - capex) if (cfo is not None and capex is not None) else None
    diluted_shares, _ = _pick(inc_stmt, _DILUTED_SHARES, period)
    shares_outstanding = _pick_instant(bs_stmt, _SHARES_OUT, period)
    employees = _pick_employees(xbrl, period)

    # Kapitalallokation (Mittelverwendung; als positive Betraege fuehren)
    buybacks, _ = _pick(cf, _BUYBACKS, period)
    dividends, _ = _pick(cf, _DIVIDENDS, period)
    acquisitions, _ = _pick(cf, _ACQUISITIONS, period)

    # Abschreibungen (D&A) fuer Owner Earnings
    dep_amort, _ = _pick(cf, _DA, period)
    if dep_amort is None:
        dep, _ = _pick(cf, _DEP_ONLY, period)
        amort, _ = _pick(cf, _AMORT_ONLY, period)
        if dep is not None or amort is not None:
            dep_amort = (dep or 0.0) + (amort or 0.0)

    # Sachanlagen (Greenwald-Kapitalintensitaet): Brutto, sonst Netto
    ppe_gross = _pick_instant(bs_stmt, _PPE_GROSS, period)
    ppe_is_net = False
    if ppe_gross is None:
        ppe_gross = _pick_instant(bs_stmt, _PPE_NET, period)
        ppe_is_net = ppe_gross is not None

    if inc is not None and inc.revenue is None and inc.net_income is None:
        return None
    return {
        "period_end":       period,
        "form_type":        filing["form_type"],
        "accession_no":     filing.get("accession_no"),
        "revenue":          inc.revenue if inc else None,
        "gross_profit":     inc.gross_profit if inc else None,
        "rd_expense":       inc.rd_expense if inc else None,
        "operating_income": inc.operating_income if inc else None,
        "pretax_income":    inc.pretax_income if inc else None,
        "tax_expense":      inc.tax_expense if inc else None,
        "net_income":       inc.net_income if inc else None,
        "equity":           bs.equity if bs else None,
        "total_debt":       bs.total_debt if bs else None,
        "cash_and_sti":     bs.cash_and_sti if bs else None,
        "net_debt":         bs.net_debt if bs else None,
        "cfo":              cfo,
        "capex":            capex,
        "fcf":              fcf,
        "diluted_shares":   diluted_shares,
        "shares_outstanding": shares_outstanding,
        "employees":        employees,
        "buybacks":         buybacks,
        "dividends":        dividends,
        "acquisitions":     acquisitions,
        "dep_amort":        dep_amort,
        "ppe_gross":        ppe_gross,
        "ppe_is_net":       ppe_is_net,
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
    operating_income, _ = _pick(inc, _OPINC, period)
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
        "operating_income": operating_income,
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


def _role_flags(rel: dict | None) -> tuple[bool, bool]:
    """(is_ceo, is_cfo) aus officerTitle."""
    if not isinstance(rel, dict):
        return False, False
    title = (rel.get("officerTitle") or "").lower()
    toks = title.replace(".", " ").split()
    is_ceo = "chief executive" in title or "ceo" in toks
    is_cfo = "chief financial" in title or "cfo" in toks
    return is_ceo, is_cfo


def _record_is_planned(rec: dict) -> bool:
    """Heuristik: gehoert das Filing zu einem 10b5-1-Handelsplan (Routine)?"""
    fns = rec.get("footnotes")
    texts: list[str] = []
    if isinstance(fns, list):
        for f in fns:
            texts.append(str(f.get("text", "")) if isinstance(f, dict)
                         else str(f))
    elif isinstance(fns, dict):
        texts.extend(str(v) for v in fns.values())
    blob = " ".join(texts).lower()
    return "10b5-1" in blob or "10b5 1" in blob or "rule 10b5" in blob \
        or "trading plan" in blob


def _flatten_insider_record(rec: dict) -> list[dict]:
    """Ein Form-4-Record -> flache Transaktionszeilen (non-deriv + deriv)."""
    owner = rec.get("reportingOwner") or {}
    owner_name = owner.get("name") or "—"
    owner_cik = owner.get("cik")
    rel_dict = owner.get("relationship")
    rel = _relationship_label(rel_dict)
    is_ceo, is_cfo = _role_flags(rel_dict)
    _rd = rel_dict if isinstance(rel_dict, dict) else {}
    is_officer = bool(_rd.get("isOfficer"))
    is_director = bool(_rd.get("isDirector"))
    is_tenpct = bool(_rd.get("isTenPercentOwner"))
    planned = _record_is_planned(rec)
    filed_at = rec.get("filedAt")
    out: list[dict] = []
    for tbl_key, is_deriv in (("nonDerivativeTable", False),
                              ("derivativeTable", True)):
        tbl = rec.get(tbl_key) or {}
        for tx in (tbl.get("transactions") or []):
            coding = tx.get("coding") or {}
            amounts = tx.get("amounts") or {}
            post = tx.get("postTransactionAmounts") or {}
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
            try:
                shares_following = float(
                    post.get("sharesOwnedFollowingTransaction")) \
                    if post.get("sharesOwnedFollowingTransaction") \
                    is not None else None
            except (TypeError, ValueError):
                shares_following = None
            value = (shares * price
                     if shares is not None and price is not None else None)
            out.append({
                "filed_at":      filed_at,
                "owner":         owner_name,
                "owner_cik":     owner_cik,
                "relationship":  rel,
                "is_ceo":        is_ceo,
                "is_cfo":        is_cfo,
                "is_officer":    is_officer,
                "is_director":   is_director,
                "is_tenpct":     is_tenpct,
                "planned":       planned,
                "transaction_date": (tx.get("transactionDate") or "")[:10],
                "code":          coding.get("code"),
                "shares":        shares,
                "price":         price,
                "value":         value,
                "shares_following": shares_following,
                "acquired_disposed": amounts.get("acquiredDisposedCode"),
                "security":      tx.get("securityTitle"),
                "derivative":    is_deriv,
            })
    return out


_INSIDER_PAGE = 50          # API-Hardlimit fuer 'size'


def get_issuer_cik(ticker: str) -> str | None:
    """Konstante Emittenten-CIK zum Ticker (ueber die Query-API, die auch
    historische Filings auf den heutigen Ticker mappt). Wichtig fuer
    Ticker-Umbenennungen (z.B. FB -> META) im Insider-Endpoint."""
    fil = _query_raw(f'ticker:{ticker}', 1)
    return fil[0].get("cik") if fil else None


def _issuer_query(ticker: str, issuer_cik) -> str:
    if issuer_cik not in (None, "", 0):
        return f'issuer.cik:{str(issuer_cik).lstrip("0") or "0"}'
    return f'issuer.tradingSymbol:{ticker}'


def _insider_first(query: str) -> str | None:
    payload = {"query": query, "from": "0", "size": "1",
               "sort": [{"filedAt": {"order": "asc"}}]}
    try:
        resp = requests.post(
            INSIDER_URL, json=payload,
            headers={"Authorization": _api_key()}, timeout=20)
    except requests.RequestException as e:
        raise SecApiError(f"Insider-First-Request fehlgeschlagen: {e}") from e
    if resp.status_code != 200:
        raise SecApiError(
            f"Insider-First HTTP {resp.status_code}: {resp.text[:160]}")
    body = resp.json() or {}
    recs = body.get("transactions") or body.get("data") or []
    return recs[0].get("filedAt") if recs else None


def fetch_insider_first_filing(ticker: str, owner_name: str,
                               owner_cik=None, issuer_cik=None) -> str | None:
    """Fruehestes Insider-Filing (filedAt) einer Person beim Emittenten.

    Emittent per issuer.cik (faengt Ticker-Umbenennungen ab); Person per
    exakter CIK, sonst Name. Gezielte Abfrage (sort filedAt asc, size 1).
    """
    iq = _issuer_query(ticker, issuer_cik)
    if owner_cik not in (None, "", 0):
        cik = str(owner_cik).lstrip("0") or "0"
        res = _insider_first(f'{iq} AND reportingOwner.cik:{cik}')
        if res:
            return res
    if owner_name and owner_name != "—":
        safe = owner_name.replace('"', " ").strip()
        return _insider_first(f'{iq} AND reportingOwner.name:"{safe}"')
    return None


def find_latest_proxy(ticker: str) -> dict | None:
    """Juengste DEF 14A (Proxy) mit Haupt-Dokument-URL."""
    filings = _query_raw(f'ticker:{ticker} AND formType:"DEF 14A"', 1)
    if not filings:
        return None
    f = filings[0]
    docs = f.get("documentFormatFiles") or []
    url = None
    for d in docs:
        if (d.get("type") or "").upper().startswith("DEF 14A"):
            url = d.get("documentUrl"); break
    if not url:                                  # Fallback: erstes HTML
        for d in docs:
            u = (d.get("documentUrl") or "")
            if u.lower().endswith((".htm", ".html")):
                url = u; break
    return {"filed_at": f.get("filedAt"), "url": url,
            "link": f.get("linkToFilingDetails")}


def parse_beneficial_ownership(text: str) -> dict | None:
    """Aus DEF-14A-Text die 'directors and officers as a group'-Zeile lesen.

    Diese Zeile fasst die Management-Beteiligung exakt zusammen (Stueck +
    %-Anteil). Returns {group_shares, group_pct} oder None.
    """
    low = (text or "").lower()
    for m in re.finditer(r"as a group", low):
        pre = low[max(0, m.start() - 160):m.start()]
        if "director" not in pre and "officer" not in pre:
            continue
        seg = text[m.end():m.end() + 400]
        # beschreibenden Satz '... shares ... outstanding' aussortieren
        if "outstanding" in seg[:170].lower():
            continue
        pc = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", seg)
        if pc:
            # Aktien = letzte Komma-Zahl VOR dem Prozentwert (Tabellenzeile)
            before = seg[:pc.start()]
            nums = re.findall(r"[\d][\d,]{3,}", before)
            shares = float(nums[-1].replace(",", "")) if nums else None
            return {"group_shares": shares,
                    "group_pct": float(pc.group(1)) / 100, "lt_one": False}
        # Aktienzahl direkt gefolgt von '*' = weniger als 1 % (kein Prozent)
        if re.search(r"[\d][\d,]{2,}\s*\*", seg):
            sh = re.search(r"([\d][\d,]{3,})", seg)
            return {"group_shares":
                    float(sh.group(1).replace(",", "")) if sh else None,
                    "group_pct": None, "lt_one": True}
        # sonst: beschreibender Satz (z.B. 'shares ... outstanding') -> weiter
    return None


def fetch_beneficial_ownership(ticker: str) -> dict | None:
    """DEF 14A laden + Management-Beteiligung (Gruppe) extrahieren."""
    d = fetch_beneficial_ownership_detail(ticker)
    if d.get("group_pct") is None and d.get("group_shares") is None:
        return None
    return {"group_shares": d.get("group_shares"),
            "group_pct": d.get("group_pct"),
            "filed_at": d.get("filed_at"), "link": d.get("link")}


def fetch_beneficial_ownership_detail(ticker: str) -> dict:
    """Wie fetch_beneficial_ownership, aber mit Diagnose-Feldern (URL,
    Textlaenge, Snippet, Fehler) — auch wenn das Parsing scheitert."""
    out = {"url": None, "filed_at": None, "link": None, "text_len": 0,
           "snippet": None, "group_shares": None, "group_pct": None,
           "error": None}
    pr = find_latest_proxy(ticker)
    if not pr or not pr.get("url"):
        out["error"] = "keine DEF 14A / kein Dokument-URL"
        return out
    out["url"], out["filed_at"], out["link"] = (
        pr["url"], pr.get("filed_at"), pr.get("link"))
    try:
        text = fetch_exhibit_text(pr["url"])
    except SecApiError as e:
        out["error"] = f"Download fehlgeschlagen: {e}"
        return out
    out["text_len"] = len(text or "")
    res = parse_beneficial_ownership(text)
    if res:
        out["group_shares"] = res["group_shares"]
        out["group_pct"] = res["group_pct"]
        out["lt_one"] = res.get("lt_one", False)
    low = (text or "").lower()
    snip_first = None
    for m in re.finditer(r"as a group", low):
        pre = low[max(0, m.start() - 140):m.start()]
        if "director" not in pre and "officer" not in pre:
            continue
        snip = text[max(0, m.start() - 70):m.start() + 400]
        if snip_first is None:
            snip_first = snip
        after = text[m.end():m.end() + 400]
        if "outstanding" in after[:170].lower():
            continue                      # beschreibender Satz
        # bevorzugt den Treffer mit Prozent/Stern (= Tabellenzeile)
        if re.search(r"\d\s*%|[\d][\d,]{2,}\s*\*", after):
            out["snippet"] = snip
            break
    if out["snippet"] is None:
        out["snippet"] = snip_first
    if res is None:
        out["error"] = ("Gruppe-Zeile gefunden, aber nicht parsbar"
                        if out["snippet"] else
                        "'directors/officers as a group'-Zeile nicht "
                        "gefunden")
    return out


def _13f_match(h: dict, ticker: str) -> bool:
    t = (h.get("ticker") or "").upper()
    return t == ticker.upper()


def _13f_shares(h: dict):
    sp = h.get("shrsOrPrnAmt") or h.get("sharesOrPrincipalAmount") or {}
    val = sp.get("sshPrnamt") if isinstance(sp, dict) else None
    if val is None:
        val = h.get("shares") or h.get("sshPrnamt")
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def fetch_institutional_holdings(ticker: str, *, n: int = 50) -> dict:
    """Institutionelle 13F-Positionen im Wert (Best-effort, groesste Halter).

    Form-13F-Holdings-Endpoint, gefiltert auf den Ticker. Returns
    {holdings: [{manager, shares, value, period, filed_at}], error}.
    Hinweis: keine Vollaggregation aller Filer (Kosten/Pagination) — eine
    Stichprobe der zuletzt gemeldeten Positionen.
    """
    # Sortfeld unsicher (Endpoint kann Filings liefern); daher mehrere
    # probieren — groesste Positionen bevorzugt, filedAt als Fallback.
    auth = {"Authorization": _api_key()}
    size = str(min(int(n), 50))
    last_err = None
    for sort_field in ("value", "holdings.value", "filedAt"):
        payload = {"query": f"holdings.ticker:{ticker}", "from": "0",
                   "size": size, "sort": [{sort_field: {"order": "desc"}}]}
        try:
            resp = requests.post(FORM13F_URL, json=payload, headers=auth,
                                 timeout=30)
        except requests.RequestException as e:
            last_err = f"Request: {e}"; continue
        if resp.status_code != 200:
            last_err = f"HTTP {resp.status_code}: {resp.text[:120]}"; continue
        body = resp.json() or {}
        recs = (body.get("data") or body.get("filings")
                or body.get("holdings") or [])
        out: list[dict] = []
        for rec in recs:
            manager = (rec.get("companyName")
                       or (rec.get("filer") or {}).get("name")
                       or rec.get("managerName") or rec.get("name") or "—")
            filed = rec.get("filedAt")
            period = rec.get("periodOfReport")
            hs = rec.get("holdings")
            cand = hs if isinstance(hs, list) else [rec]
            for h in cand:
                if not isinstance(h, dict) or not _13f_match(h, ticker):
                    continue
                try:
                    value = (float(h.get("value"))
                             if h.get("value") is not None else None)
                except (TypeError, ValueError):
                    value = None
                out.append({"manager": manager, "shares": _13f_shares(h),
                            "value": value, "period": period,
                            "filed_at": filed})
        if out:
            return {"holdings": out, "error": None, "sort": sort_field}
        last_err = f"keine Positionen (sort={sort_field})"
    return {"holdings": [], "error": last_err or "keine 13F-Positionen"}


def fetch_mgmt_changes(ticker: str, *, n: int = 50) -> list[dict]:
    """8-K Item 5.02 (Abgang/Bestellung von Direktoren/Officers).

    Proxy fuer Management-Turnover. Returns [{filed_at, accession_no}, ...].
    """
    filings = _query_raw(
        f'ticker:{ticker} AND formType:"8-K" AND items:"5.02"', n)
    return [{"filed_at": f.get("filedAt"),
             "accession_no": f.get("accessionNo")} for f in filings]


def fetch_insider_transactions(ticker: str, *, n: int = 300,
                               issuer_cik=None) -> list[dict]:
    """Bis zu N juengste Form-3/4/5-Records -> flache Transaktionsliste.

    Emittent per issuer.cik (faengt Ticker-Umbenennungen wie FB->META ab),
    sonst per tradingSymbol. Der Endpoint erlaubt max. size=50 pro Request;
    daher 50er-Paging ueber 'from'. Leere Liste, wenn keine Filings.
    """
    rows: list[dict] = []
    fetched = 0
    target = max(1, int(n))
    auth = {"Authorization": _api_key()}
    iq = _issuer_query(ticker, issuer_cik)
    while fetched < target:
        page = min(_INSIDER_PAGE, target - fetched)
        payload = {
            "query": iq,
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
