"""GuruFocus -> nova-Mapping (Pfad A: GuruFocus ersetzt sec-api als Datenquelle).

Zwei reine Mapping-Funktionen (kein Netz, kein Streamlit):

  kpi_snapshot(summary, keyratios) -> (f, med)
      f:   {kpi_spalte: wert}   med: {kpi_spalte: industrie_median}
      Spaltennamen + Einheiten identisch zu ref_fundamentals_latest / der
      Analyse-KPI-Tabelle (Margen/ROE/ROA/ROIC als Anteil 0..1, Ratios roh,
      dividend_yield als %-Wert). Industrie-Median kommt aus summary.ratio
      (GuruFocus liefert ihn fertig) -> echter Sektor-Vergleich auch on-demand.

  metric_rows(financials, n_years) -> [year_metrics-Dict, …]
      Gleiche Shape wie company_data.year_metrics-Zeilen (revenue, gross_profit,
      operating_income, net_income, rd_expense, equity, net_debt,
      diluted_shares, fcf, employees, period_end, form_type) -> Drop-in fuer
      den Deep-Dive (dd.series). GuruFocus-Geldwerte sind in Mio, Aktien in Mio
      -> hier auf absolute USD/Stueck skaliert.
"""

from __future__ import annotations

_M = 1_000_000.0


def _f(x):
    """GuruFocus-Wert -> float|None (Strings mit Tausender-Komma, 'N/A')."""
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "N/A", "NaN", "-", "—", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _mn(v):
    return v * _M if v is not None else None


def _absmn(v):
    """Mittelabfluss (in GuruFocus negativ) -> positiver Betrag, * Mio."""
    return _mn(abs(v)) if v is not None else None


# ---------- KPI-Snapshot (summary.ratio + keyratios + company_data) ----------

# kpi_spalte -> (key in summary.ratio, transform)
#   transform: 'r'=roh, 'p'=Prozent/100 (-> Anteil)
_RATIO_MAP = {
    "pe_ttm":           ("P/E(ttm)", "r"),
    "pe_forward":       ("Forward P/E", "r"),
    "peg_ratio":        ("PEG", "r"),
    "pb":               ("P/B", "r"),
    "ps_ttm":           ("P/S", "r"),
    "p_fcf":            ("PFCF", "r"),
    "operating_margin": ("Operating margin (%)", "p"),
    "net_margin":       ("Net-margin (%)", "p"),
    "roe":              ("ROE (%)", "p"),
    "roa":              ("ROA (%)", "p"),
    "debt_to_equity":   ("Debt-to-Equity", "r"),
    "net_debt_to_ebitda": ("Debt-to-Ebitda", "r"),
    "current_ratio":    ("Current Ratio", "r"),
    "quick_ratio":      ("Quick Ratio", "r"),
    "interest_coverage": ("Interest Coverage", "r"),
}


def _apply(v, kind):
    if v is None:
        return None
    return v / 100.0 if kind == "p" else v


def kpi_snapshot(summary: dict, keyratios: dict) -> tuple[dict, dict]:
    s = (summary or {}).get("summary", summary) or {}
    ratio = s.get("ratio", {}) or {}
    cd = s.get("company_data", {}) or {}
    kr = keyratios or {}
    prof = kr.get("Profitability", {}) or {}
    val = kr.get("Valuation Ratio", {}) or {}
    div = kr.get("Dividends", {}) or {}

    f: dict = {}
    med: dict = {}
    for col, (key, kind) in _RATIO_MAP.items():
        rec = ratio.get(key)
        if isinstance(rec, dict):
            f[col] = _apply(_f(rec.get("value")), kind)
            med[col] = _apply(_f((rec.get("indu") or {}).get("indu_med")), kind)

    # Felder, die nur in keyratios/company_data stehen
    f["gross_margin"] = _apply(_f(prof.get("Gross Margin %")), "p")
    f["fcf_margin"] = _apply(_f(prof.get("FCF Margin %")), "p")
    f["ev_ebitda"] = _f(val.get("EV-to-EBITDA"))
    f["ev_sales"] = None  # GuruFocus: nicht direkt
    # ROIC + Median aus company_data
    f["roic"] = _apply(_f(cd.get("roic")), "p")
    med["roic"] = _apply(_f(cd.get("roic_med")), "p")
    # Cash & Dividende
    f["dividend_yield"] = _f(div.get("Dividend Yield %"))          # pct_raw (%-Wert)
    f["payout_ratio"] = _f(div.get("Dividend Payout Ratio"))       # bereits Anteil
    f["dividend_per_share"] = _f(div.get("Dividends per Share (TTM)"))
    pfcf = f.get("p_fcf")
    f["fcf_yield"] = (1.0 / pfcf) if (pfcf and pfcf > 0) else None  # Anteil

    # Stammdaten (fuer Anzeige)
    gen = s.get("general", {}) or {}
    f["name"] = gen.get("company") or gen.get("company_name")
    f["sector"] = gen.get("sector") or gen.get("group")
    return f, med


# ---------- Qualitaets-/Score-Snapshot (summary.company_data) ----------

def quality_snapshot(summary: dict) -> dict:
    """GuruFocus-Qualitaets-Kennzahlen je Wert (ersetzt den hauseigenen
    Shearn-Score). Quelle: summary.company_data."""
    s = (summary or {}).get("summary", summary) or {}
    cd = s.get("company_data", {}) or {}
    gen = s.get("general", {}) or {}

    def n(k):
        return _f(cd.get(k))
    return {
        "name":         gen.get("company") or gen.get("company_name"),
        "sector":       gen.get("sector") or gen.get("group"),
        "gf_score":     n("gf_score"),               # 0..100 (Gesamt)
        "gf_value":     n("gf_value"),               # intrinsischer Wert
        "price_to_gf_value": n("p2gf_value"),        # Kurs / GF-Value
        "gf_valuation": cd.get("gf_valuation"),      # Text (Over/Undervalued)
        "rank_financial_strength": n("rank_financial_strength"),
        "rank_profitability":      n("rank_profitability"),
        "rank_growth":             n("rank_growth"),
        "rank_balancesheet":       n("rank_balancesheet"),
        "predictability": n("predictability"),       # 0..5
        "fscore":       n("fscore"),                 # Piotroski 0..9
        "zscore":       n("zscore"),                 # Altman
        "mscore":       n("mscore"),                 # Beneish
        "moat_score":   n("moat_score"),
        "roic":         n("roic"),                   # %
        "wacc":         n("wacc"),                   # %
    }


# ---------- Mehrjahres-Metriken (financials.annuals) ----------

def _section(financials: dict, quarterly: bool) -> dict:
    d = financials or {}
    if "financials" in d:
        d = d["financials"]
    return d.get("quarterly" if quarterly else "annuals", {}) or {}


def metric_rows(financials: dict, n_years: int | None = None,
                *, quarterly: bool = False) -> list[dict]:
    ann = _section(financials, quarterly)
    form = "10-Q" if quarterly else "10-K"
    fy = ann.get("Fiscal Year", []) or []
    inc = ann.get("income_statement", {}) or {}
    bs = ann.get("balance_sheet", {}) or {}
    ps = ann.get("per_share_data_array", {}) or {}
    cf = ann.get("cashflow_statement", {}) or {}
    vq = ann.get("valuation_and_quality", {}) or {}

    def at(sec, name, i):
        arr = sec.get(name) or []
        return _f(arr[i]) if i < len(arr) else None

    rows = []
    for i, period in enumerate(fy):
        if str(period).upper() in ("TTM", "PRELIMINARY"):
            continue  # nur abgeschlossene Geschaeftsjahre
        sh = at(ps, "Shares Outstanding (Diluted Average)", i)
        shares_abs = _mn(sh)
        cfo = _mn(at(cf, "Cash Flow from Operations", i))
        capex = _absmn(at(cf, "Purchase Of Property, Plant, Equipment", i))
        fcf = (cfo - capex) if (cfo is not None and capex is not None) else None
        if fcf is None:                       # Fallback: FCF/Aktie * Aktien
            fcfps = at(ps, "Free Cash Flow per Share", i)
            fcf = (fcfps * shares_abs) if (fcfps is not None
                                           and shares_abs is not None) else None
        ltd = at(bs, "Long-Term Debt & Capital Lease Obligation", i)
        std = at(bs, "Short-Term Debt & Capital Lease Obligation", i)
        cash = at(bs, "Cash, Cash Equivalents, Marketable Securities", i)
        if cash is None:
            cash = at(bs, "Cash and Cash Equivalents", i)
        debt = (ltd or 0.0) + (std or 0.0) if (ltd is not None
                                               or std is not None) else None
        net_debt = (_mn(debt) - (_mn(cash) or 0.0)) if debt is not None else None
        ppe_gross = at(bs, "Gross Property, Plant and Equipment", i)
        ppe_is_net = False
        if ppe_gross is None:
            ppe_gross = at(bs, "Property, Plant and Equipment", i)
            ppe_is_net = ppe_gross is not None
        sh_eop = at(ps, "Shares Outstanding (EOP)", i)
        if sh_eop is None:
            sh_eop = at(ps, "Shares Outstanding (Basic Average)", i)
        rows.append({
            "period_end":       str(period),
            "form_type":        form,
            "accession_no":     None,
            "revenue":          _mn(at(inc, "Revenue", i)),
            "gross_profit":     _mn(at(inc, "Gross Profit", i)),
            "rd_expense":       _mn(at(inc, "Research & Development", i)),
            "operating_income": _mn(at(inc, "Operating Income", i)),
            "pretax_income":    _mn(at(inc, "Pretax Income", i)),
            "tax_expense":      _absmn(at(inc, "Tax Provision", i)),
            "net_income":       _mn(at(inc, "Net Income", i)),
            "equity":           _mn(at(bs, "Total Stockholders Equity", i)),
            "total_debt":       _mn(debt),
            "cash_and_sti":     _mn(cash),
            "net_debt":         net_debt,
            "cfo":              cfo,
            "capex":            capex,
            "fcf":              fcf,
            "dep_amort": _mn(at(cf, "Cash Flow Depreciation, Depletion "
                                "and Amortization", i)),
            "buybacks":         _absmn(at(cf, "Repurchase of Stock", i)),
            "dividends":        _absmn(at(cf, "Cash Flow for Dividends", i)),
            "acquisitions":     _absmn(at(cf, "Purchase Of Business", i)),
            "ppe_gross":        _mn(ppe_gross),
            "ppe_is_net":       ppe_is_net,
            "diluted_shares":   shares_abs,
            "shares_outstanding": _mn(sh_eop),
            "employees":        at(vq, "Number of Employees", i),
        })
    if n_years:
        rows = rows[-int(n_years):]
    return rows


# _INCOME_COLS (company_data) -> GuruFocus income_statement-Zeilen
_INCOME_MAP = {
    "revenue":           "Revenue",
    "cost_of_revenue":   "Cost of Goods Sold",
    "gross_profit":      "Gross Profit",
    "rd_expense":        "Research & Development",
    "sga_expense":       "Selling, General, & Admin. Expense",
    "operating_expense": "Total Operating Expense",
    "operating_income":  "Operating Income",
    "other_income":      "Other Income (Expense)",
    "pretax_income":     "Pretax Income",
    "tax_expense":       "Tax Provision",
    "net_income":        "Net Income",
}


def income_rows(financials: dict, n_years: int | None = None,
                *, quarterly: bool = False) -> list[dict]:
    """GuV-Historie in income_history-Shape (company_data._row_from_*)."""
    ann = _section(financials, quarterly)
    form = "10-Q" if quarterly else "10-K"
    fy = ann.get("Fiscal Year", []) or []
    inc = ann.get("income_statement", {}) or {}

    def at(name, i):
        arr = inc.get(name) or []
        return _f(arr[i]) if i < len(arr) else None

    rows = []
    for i, period in enumerate(fy):
        if str(period).upper() in ("TTM", "PRELIMINARY"):
            continue
        d = {"period_end": str(period), "form_type": form, "currency": "USD",
             "accession_no": None, "filed_at": None}
        for col, key in _INCOME_MAP.items():
            v = at(key, i)
            d[col] = _absmn(v) if col == "tax_expense" else _mn(v)
        rows.append(d)
    if n_years:
        rows = rows[-int(n_years):]
    return rows
