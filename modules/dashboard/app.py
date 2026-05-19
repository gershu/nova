"""nova-lab Dashboard — Streamlit Single-Page-App + Sidebar.

Pages werden automatisch aus pages/ geladen (Streamlit Multi-Page-App).
Diese Datei ist nur Landing + globale Sidebar.
"""

from __future__ import annotations

import streamlit as st

from modules.dashboard.db import DB_PATH, connection


st.set_page_config(
    page_title="nova-lab Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------- Sidebar (global state) ----------

with st.sidebar:
    st.title("nova-lab")
    st.caption(f"DB: `{DB_PATH}`")

    st.divider()
    # Quick-status: DB-Tabellen + zentrale Counts (kurzlebige Connection)
    try:
        with connection() as con:
            n_pos = con.execute(
                "SELECT COUNT(*) FROM pos_holdings WHERE valid_to IS NULL"
            ).fetchone()[0]
            n_inst = con.execute("SELECT COUNT(*) FROM ref_instruments").fetchone()[0]
            latest_quote = con.execute(
                "SELECT MAX(ts) FROM mkt_quotes_daily"
            ).fetchone()
        st.caption(f"📈 {n_pos} holdings · {n_inst} instruments")
        if latest_quote and latest_quote[0]:
            st.caption(f"🕐 latest quote: {latest_quote[0]}")
    except Exception as e:  # noqa: BLE001
        st.warning(f"DB-Sanity-Check fehlgeschlagen: {e.__class__.__name__}")


# ---------- Landing ----------

st.title("📊 nova-lab Dashboard")
st.markdown("""
Single-User-Frontend fuer die nova-lab Portfolio-Daten. Read-only auf
DuckDB; Daemons schreiben weiter parallel.

**Navigation (Sidebar):**

- **1 Overview** — Portfolio-Total in EUR + MTM-Trend + Positions-Tabelle
- **2 Composition** — Allokation, Top-15, Portfolio-Views, Korrelations-Matrix
- **3 Tagesbriefing** — LLM-Briefing: Headline, Body, KPIs, Sentiment, History
- **4 CSP Picks** — Cash-Secured-Put-Kandidaten aus screener_csp
- **5 Alerts** — sig_alerts + LLM-Erklaerungen, Filter nach Regel/Sentiment/Zeitraum
- **6 Marktlage** — VIX, Economic Indicators, Z-Scores, Korrelations-Matrix
- **7 Database** — Inventar aller Tabellen + Views mit Daten- + SQL-Inspektion

Cross-Currency-Aggregation erfolgt in EUR. Native-Werte bleiben sichtbar.

Alle Werte sind in der Native-Currency der Position. Kein FX-Reporting
(noch nicht).

Daten-Refresh: `F5` (oder Page-Wechsel) — DB wird neu gelesen, 60s Query-Cache.
""")
