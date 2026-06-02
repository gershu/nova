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
