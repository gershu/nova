"""Instrument-Identitaet + Asset-Klassen-Mapping.

Generische Utilities — frueher in import_xlsx.py, nach dem Drop des
Excel-Importers hierher extrahiert. Genutzt von modules.instruments und
ueberall wo ref_instrument_ids konstruiert werden.
"""

from __future__ import annotations


# IB-Asset-Klassen (secType-Werte)
VALID_ASSET_CLASSES = {
    "STK", "ETF", "BOND", "OPT", "FUT", "FOP", "IND", "CASH", "CRYPTO",
    "FUND", "WAR", "BAG", "CMDTY", "CFD",
}

# nova-lab asset_type (lowercase) -> IB-Asset-Klasse
ASSET_TYPE_TO_CLASS = {
    "stock":         "STK",
    "etf":           "ETF",
    "bond":          "BOND",
    "option":        "OPT",
    "future":        "FUT",
    "future_option": "FOP",
    "index":         "IND",
    "fx":            "CASH",
    "crypto":        "CRYPTO",
    "fund":          "FUND",
    "warrant":       "WAR",
    "combo":         "BAG",
    "commodity":     "CMDTY",
    "cfd":           "CFD",
}


def make_ref_instrument_id(symbol: str, currency: str, source: str) -> str:
    """Deterministic VARCHAR-PK: '{SOURCE}:{SYMBOL}:{CURRENCY}'.

    Reproduzierbar ueber Re-Imports (im Gegensatz zu Sequence-PKs).
    Spaces im symbol -> Underscores. Alles uppercase.
    """
    sym = (symbol or "").strip().upper().replace(" ", "_")
    cur = (currency or "").strip().upper()
    src = (source or "").strip().upper()
    if not (sym and cur and src):
        raise ValueError(
            f"ref_instrument_id needs all three non-empty: "
            f"source={src!r} symbol={sym!r} currency={cur!r}"
        )
    return f"{src}:{sym}:{cur}"
