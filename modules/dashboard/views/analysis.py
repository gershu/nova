"""Unternehmens-Analyse — vereinheitlichte 6-Fragen-View (Phase 2: Geruest).

Fuehrt Thesis-Cockpit + Ad-Hoc zusammen. Eingabe: ein Ticker (Universum oder
Freitext). Die Datenschicht (modules.dashboard.company_data) waehlt die
Quelle automatisch (persistierte DB fuer Universums-Werte, sonst on-Demand
sec-api) und liefert quellenidentische Shapes.

Phase 2 liefert: Quellen-Badge, Ticker-Eingabe, Ueberblick-Scorecard
(Geruest) und die 6 Frage-Tabs als Struktur. Inhalte der Tabs 1-6 folgen in
Phase 3 (Wiederverwendung der vorhandenen Ad-Hoc-/Thesis-Bausteine).
"""

from __future__ import annotations

import streamlit as st

from modules.dashboard import company_data as cd

# DB optional (Universums-Auswahl) — defensiv.
try:
    from modules.dashboard.db import run_query as _run_query
except Exception:  # noqa: BLE001
    _run_query = None


# ---------- Cache-Wrapper um die (reine) Datenschicht ----------

@st.cache_data(ttl=3600, show_spinner=False)
def _resolve(ticker: str):
    return cd.resolve(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _income(ticker: str):
    return cd.income_history(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _universe_symbols() -> list[str]:
    if _run_query is None:
        return []
    try:
        df = _run_query(
            "SELECT DISTINCT symbol FROM ref_instruments "
            "WHERE active AND symbol IS NOT NULL ORDER BY symbol", None)
        return df["symbol"].tolist() if df is not None and not df.empty \
            else []
    except Exception:  # noqa: BLE001
        return []


# ---------- Die 6 Investorenfragen ----------

_QUESTIONS = [
    ("1 Geschaeft", "Ist das Geschaeft gut?",
     "Umsatzwachstum, Margen-Trend, ROIC/ROCE/ROE/ROA, FCF-Marge, "
     "Umsatz/Mitarbeiter."),
    ("2 Burggraben", "Hat das Unternehmen einen Burggraben?",
     "Moat-Score (Margen-Stabilitaet, ROIC-Stabilitaet, F&E-Effizienz, "
     "Rueckkaeufe, Marktanteil) + Peers."),
    ("3 Bilanz", "Ist die Bilanz solide?",
     "Current/Quick Ratio, Net Debt, Debt/Equity, Eigenkapitalquote, "
     "Goodwill-Anteil + Trend."),
    ("4 Management", "Ist das Management gut?",
     "Tenure, Ownership-Struktur, Turnover, Insider-Conviction, "
     "Kapitalallokation, SBC/Verwaesserung."),
    ("5 Gewinne echt", "Sind die Gewinne echt?",
     "Earnings-Quality-Score, GAAP vs non-GAAP, Owner Earnings vs "
     "Nettogewinn vs FCF."),
    ("6 Bewertung", "Ist die Bewertung attraktiv?",
     "EV, EV/FCF, Earnings Yield (EBIT/EV + klassisch), KGV, Kurs."),
]


# =====================================================================
# Seite
# =====================================================================

st.title("🏛 Unternehmens-Analyse")
st.caption("Vereinheitlichte Sicht nach 6 Investorenfragen. Quelle "
           "automatisch: DB fuer Universums-Werte, sonst on-Demand "
           "(sec-api.io).")

# ---- Ticker-Eingabe: Universum oder Freitext ----
_syms = _universe_symbols()
_c1, _c2 = st.columns([1, 3])
_mode = _c1.radio("Auswahl", (["Universum", "Freitext"] if _syms
                              else ["Freitext"]), horizontal=False,
                  key="ana_mode")
if _mode == "Universum" and _syms:
    ticker = _c2.selectbox(f"Wert ({len(_syms)} im Universum)", _syms,
                           key="ana_uni")
else:
    ticker = _c2.text_input("Ticker (US-gelistet, EDGAR)", value="",
                            placeholder="z. B. AAPL, MSFT, NVDA",
                            key="ana_free").strip().upper()

if not ticker:
    st.info("Ticker waehlen oder eingeben.")
    st.stop()

src = _resolve(ticker)
_badge = "🟢 DB" if src.income_source == "db" else "🟡 on-Demand"
st.markdown(
    f"### {src.ticker}{(' — ' + src.name) if src.name else ''}  \n"
    f"Datenquelle: **{_badge}**"
    f"{'  · im Universum' if src.in_universe else ''}")

tabs = st.tabs(["Ueberblick"] + [q[0] for q in _QUESTIONS]
               + ["Portfolio & Signale"])

# ---- Ueberblick / Scorecard (Geruest) ----
with tabs[0]:
    st.markdown("#### Gesamturteil")
    st.caption("Scorecard je Frage (Ampeln) folgt in Phase 3 — die "
               "Score-Logik wird aus den bestehenden Ad-Hoc-Modulen "
               "wiederverwendet.")
    sc = []
    for short, full, _desc in _QUESTIONS:
        sc.append({"Frage": full, "Bewertung": "— (Phase 3)"})
    st.dataframe(sc, use_container_width=True, hide_index=True)

    # Datenbasis-Nachweis (Phase-1-Datenschicht end-to-end)
    st.markdown("#### Datenbasis")
    try:
        ih = _income(ticker)
        rows = ih.get("rows") or []
        last = rows[-1] if rows else None
        m = st.columns(3)
        m[0].metric("GuV-Quelle", ih.get("source", "—"))
        m[1].metric("Perioden geladen", str(len(rows)))
        m[2].metric("Letzter Umsatz",
                    (f"{last['revenue'] / 1e9:.2f} Mrd {last['currency']}"
                     if last and last.get("revenue") else "—"))
        if last:
            st.caption(f"Letzte Periode {last['period_end']} "
                       f"({last.get('form_type') or '—'}).")
    except Exception as e:  # noqa: BLE001
        st.warning(f"Datenbasis nicht ladbar: {e.__class__.__name__}: {e}")

# ---- Frage-Tabs 1-6 (Phase-3-Platzhalter) ----
for i, (short, full, desc) in enumerate(_QUESTIONS, start=1):
    with tabs[i]:
        st.markdown(f"#### {full}")
        st.info(f"Folgt in Phase 3. Geplante Inhalte: {desc}")

# ---- Portfolio & Signale ----
with tabs[-1]:
    st.markdown("#### Portfolio & Signale")
    if src.in_universe:
        st.info("Folgt in Phase 3: Holdings, MtM, Thesis-Ampel, Signale, "
                "Termine, Screener-Links (nur fuer Universums-Werte).")
    else:
        st.caption(f"{src.ticker} ist nicht im Portfolio-Universum — kein "
                   "Portfolio-Kontext.")
