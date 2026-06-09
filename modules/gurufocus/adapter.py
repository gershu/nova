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
    vq = ann.get("valuation_and_quality", {}) or {}

    def at(sec, name, i):
        arr = sec.get(name) or []
        return _f(arr[i]) if i < len(arr) else None

    rows = []
    for i, period in enumerate(fy):
        if str(period).upper() in ("TTM", "PRELIMINARY"):
            continue  # nur abgeschlossene Geschaeftsjahre
        rev = at(inc, "Revenue", i)
        sh = at(ps, "Shares Outstanding (Diluted Average)", i)
        shares_abs = _mn(sh)
        fcfps = at(ps, "Free Cash Flow per Share", i)
        fcf = (fcfps * shares_abs) if (fcfps is not None
                                       and shares_abs is not None) else None
        ltd = at(bs, "Long-Term Debt & Capital Lease Obligation", i)
        std = at(bs, "Short-Term Debt & Capital Lease Obligation", i)
        cash = at(bs, "Cash, Cash Equivalents, Marketable Securities", i)
        if cash is None:
            cash = at(bs, "Cash and Cash Equivalents", i)
        debt = None
        if ltd is not None or std is not None:
            debt = (ltd or 0.0) + (std or 0.0)
        net_debt = (_mn(debt) - (_mn(cash) or 0.0)) if debt is not None else None
        rows.append({
            "period_end":       str(period),
            "form_type":        form,
            "accession_no":     None,
            "revenue":          _mn(rev),
            "gross_profit":     _mn(at(inc, "Gross Profit", i)),
            "operating_income": _mn(at(inc, "Operating Income", i)),
            "net_income":       _mn(at(inc, "Net Income", i)),
            "rd_expense":       _mn(at(inc, "Research & Development", i)),
            "equity":           _mn(at(bs, "Total Stockholders Equity", i)),
            "net_debt":         net_debt,
            "diluted_shares":   shares_abs,
            "fcf":              fcf,
            "employees":        at(vq, "Number of Employees", i),
        })
    if n_years:
        rows = rows[-int(n_years):]
    return rows
