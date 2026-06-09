"""Marktdaten (Kurse, Splits) via yfinance — reines Modul, kein Streamlit.

Single Source fuer den Marktdaten-Zugriff der Analyse-View. Alles defensiv:
yfinance kann fehlen/fehlschlagen -> None / leere Map.
"""

from __future__ import annotations


def latest_close(ticker: str):
    """Letzter Schlusskurs oder None."""
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="7d", auto_adjust=False)
        if h is None or h.empty:
            return None
        return float(h["Close"].iloc[-1])
    except Exception:  # noqa: BLE001
        return None


def price_history(ticker: str, start_iso: str, end_iso: str) -> dict:
    """Tages-Schlusskurse {iso_date: close} oder {} bei Fehler."""
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(start=start_iso, end=end_iso,
                                      auto_adjust=False)
        if h is None or h.empty:
            return {}
        return {str(idx.date()): float(row["Close"])
                for idx, row in h.iterrows()}
    except Exception:  # noqa: BLE001
        return {}


def splits(ticker: str) -> dict:
    """Aktiensplits {iso_date: ratio} oder {} bei Fehler."""
    try:
        import yfinance as yf
        s = yf.Ticker(ticker).splits
        if s is None or len(s) == 0:
            return {}
        return {str(idx.date()): float(r) for idx, r in s.items()
                if r and r > 0}
    except Exception:  # noqa: BLE001
        return {}


def _num(v):
    try:
        f = float(v)
        return None if (f != f) else f
    except (TypeError, ValueError):
        return None


def fundamentals_snapshot(ticker: str) -> dict:
    """Kennzahlen-Snapshot via yfinance Ticker.info, gemappt auf die
    ref_fundamentals_latest-Spaltennamen (fuer den On-Demand-Kennzahlen-
    Vergleich bei Nicht-Universums-Werten). Defensiv -> {} bei Fehler.

    Nicht alle Felder sind bei yfinance vorhanden (z.B. ROIC, FCF-Marge,
    Net-Debt/EBITDA) -> bleiben None. Einheiten an die View angeglichen:
    Margen/ROE/ROA als Anteil (0..1, View *100), debtToEquity /100 -> Ratio,
    dividendYield robust auf %-Wert (pct_raw) normiert.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        try:
            info = t.get_info() or {}
        except Exception:  # noqa: BLE001
            info = getattr(t, "info", {}) or {}
    except Exception:  # noqa: BLE001
        return {}
    if not info:
        return {}

    g = info.get
    mcap = _num(g("marketCap"))
    fcf = _num(g("freeCashflow"))
    d2e = _num(g("debtToEquity"))            # yfinance: Prozent (z.B. 150)
    dy = _num(g("dividendYield"))            # mal Anteil, mal Prozent
    if dy is not None and dy < 1:            # Anteil -> Prozent
        dy *= 100.0
    return {
        "name":      g("longName") or g("shortName"),
        "sector":    g("sector"),
        "industry":  g("industry"),
        "market_cap": mcap,
        "currency":  g("currency"),
        # Bewertung
        "pe_ttm":     _num(g("trailingPE")),
        "pe_forward": _num(g("forwardPE")),
        "peg_ratio":  _num(g("trailingPegRatio") or g("pegRatio")),
        "pb":         _num(g("priceToBook")),
        "ps_ttm":     _num(g("priceToSalesTrailing12Months")),
        "p_fcf":      (mcap / fcf if (mcap and fcf and fcf > 0) else None),
        "ev_ebitda":  _num(g("enterpriseToEbitda")),
        "ev_sales":   _num(g("enterpriseToRevenue")),
        # Profitabilitaet (Anteile)
        "gross_margin":     _num(g("grossMargins")),
        "operating_margin": _num(g("operatingMargins")),
        "net_margin":       _num(g("profitMargins")),
        "fcf_margin":       None,
        "roe":              _num(g("returnOnEquity")),
        "roa":              _num(g("returnOnAssets")),
        "roic":             None,
        # Verschuldung & Liquiditaet
        "debt_to_equity":     (d2e / 100.0 if d2e is not None else None),
        "net_debt_to_ebitda": None,
        "current_ratio":      _num(g("currentRatio")),
        "quick_ratio":        _num(g("quickRatio")),
        "interest_coverage":  None,
        # Cash & Dividende
        "fcf_yield":   (fcf / mcap if (mcap and fcf) else None),
        "dividend_yield":     dy,
        "payout_ratio":       _num(g("payoutRatio")),
        "dividend_per_share": _num(g("dividendRate")),
    }


def company_info(ticker: str) -> dict:
    """Stammdaten (Name, Sektor, Branche, Kurzbeschreibung, Market Cap) via
    yfinance Ticker.info. Defensiv -> {} bei Fehler/fehlenden Feldern."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        try:
            info = t.get_info() or {}
        except Exception:  # noqa: BLE001
            info = getattr(t, "info", {}) or {}
        return {
            "name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "summary": info.get("longBusinessSummary"),
            "market_cap": info.get("marketCap"),
            "currency": info.get("currency"),
        }
    except Exception:  # noqa: BLE001
        return {}
