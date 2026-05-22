"""Deutsche Zahlenformatierung fuer Dashboard-Tabellen.

Streamlit's NumberColumn kann keine deutschen Tausender-Punkte erzwingen:
printf-Formatter (%d/%f) koennen nicht gruppieren, und 'localized' ist
browser-locale-abhaengig + fuer de-DE unzuverlaessig.

Loesung: die Tabellen-DataFrames mit einem pandas-Styler.format() anzeigen.
Der Styler aendert NUR die Anzeige — die darunterliegenden Werte bleiben
numerisch, d.h. das Spalten-Sortieren in st.dataframe funktioniert weiter.

Konvention: '.' als Tausender-Trenner, ',' als Dezimal-Trenner.

Verwendung in einer Page:

    from modules.dashboard.components.format import de_int, de_dec

    st.dataframe(
        df.style.format({"mtm_eur": de_int, "pnl_eur": de_int}),
        column_config={...},   # bei den gestylten Spalten KEIN format= setzen
    )
"""

from __future__ import annotations

import math


def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def de_int(x) -> str:
    """Ganzzahlig mit Tausender-Punkten:  1958241.4 -> '1.958.241'."""
    if _missing(x):
        return "—"
    try:
        return f"{float(x):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return str(x)


def de_dec(x, places: int = 2) -> str:
    """Dezimal, deutsch:  1958241.32 -> '1.958.241,32'."""
    if _missing(x):
        return "—"
    try:
        s = f"{float(x):,.{places}f}"          # '1,958,241.32' (en-US)
        return s.translate(str.maketrans({",": ".", ".": ","}))
    except (TypeError, ValueError):
        return str(x)
