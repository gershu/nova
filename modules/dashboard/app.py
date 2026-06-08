"""nova-lab Dashboard — Streamlit Multi-Page-App mit st.navigation.

Die Nav-Struktur (gruppiert in Sektionen) ist hier zentral definiert; die
Seiten liegen in views/. app.py laeuft als Entrypoint bei jeder Interaktion:
setzt die Page-Config, rendert die globale Sidebar und delegiert dann an die
gewaehlte Seite.

Neue Seite hinzufuegen: Datei in views/ anlegen + hier in st.navigation
eintragen. Keine Datei-Praefixe / Umnummerieren noetig.
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


# ---------- Globale Sidebar (Quick-Status) ----------

with st.sidebar:
    st.title("nova-lab")
    st.caption(f"DB: `{DB_PATH}`")
    st.divider()
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


# ---------- Navigation ----------

nav = st.navigation({
    "Portfolio": [
        st.Page("views/overview.py",        title="Overview",     icon="📈",
                default=True),
        st.Page("views/allocation.py",      title="Allokation",   icon="⚖️"),
        st.Page("views/screener.py",        title="Screener",       icon="🧪"),
    ],
    "Entscheidungs-Assistent": [
        st.Page("views/tagesbriefing.py",   title="Tagesbriefing",    icon="📝"),
        st.Page("views/action_items.py",    title="Action Items",     icon="🎯"),
        st.Page("views/decision_journal.py", title="Decision Journal", icon="📓"),
    ],
    "Analyse": [
        st.Page("views/analysis.py",        title="Unternehmens-Analyse", icon="🏛"),
    ],
    "Markt & Signale": [
        st.Page("views/marktlage.py",       title="Marktlage",    icon="🌡"),
        st.Page("views/alerts.py",          title="Alerts",       icon="🔔"),
        st.Page("views/filings.py",         title="Filing-Aenderungen", icon="🗞"),
        st.Page("views/csp_picks.py",       title="CSP Picks",    icon="📞"),
    ],
    "System": [
        st.Page("views/health.py",          title="Daemon-Health", icon="🩺"),
        st.Page("views/database.py",        title="Database",     icon="🗄"),
    ],
})
nav.run()
