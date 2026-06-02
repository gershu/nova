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

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.dashboard import company_data as cd
from modules.dashboard import finmetrics as fm
from modules.dashboard.components.format import _missing, de_dec

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
def _year_metrics(ticker: str):
    return cd.year_metrics(ticker)


def _pct(v, places: int = 1) -> str:
    return "—" if _missing(v) else de_dec(float(v) * 100.0, places) + " %"


def _money(v, cur: str = "USD") -> str:
    if _missing(v):
        return "—"
    a = abs(float(v))
    if a >= 1e9:
        return f"{de_dec(v / 1e9, 2)} Mrd {cur}"
    if a >= 1e6:
        return f"{de_dec(v / 1e6, 1)} Mio {cur}"
    return f"{de_dec(v, 0)} {cur}"


def render_business(ticker: str, src) -> None:
    """Tab 1 — Ist das Geschaeft gut? (Wachstum, Margen, Renditen, FCF)."""
    cur = src.currency or "USD"
    inc = _income(ticker)
    rows = [r for r in (inc.get("rows") or [])
            if (r.get("form_type") or "").upper().startswith("10-K")] \
        or (inc.get("rows") or [])
    if not rows:
        st.info("Keine GuV-Daten verfuegbar.")
        return

    # --- Umsatzwachstum ---
    rev_pts = [(pd.to_datetime(r["period_end"]), r["revenue"]) for r in rows
               if r.get("revenue") is not None]
    rev_cagr = None
    if len(rev_pts) >= 2:
        yrs = (rev_pts[-1][0] - rev_pts[0][0]).days / 365.25
        rev_cagr = fm.cagr(rev_pts[0][1], rev_pts[-1][1], yrs)

    # --- Renditen je Jahr (Trendampel) ---
    ym = _year_metrics(ticker).get("rows") or []
    rets = [fm.returns_from_metrics(d) for d in ym]
    fcf_pts = [(pd.to_datetime(d["period_end"]),
                fm.safe_div(d.get("fcf"), d.get("revenue"))) for d in ym]
    fcf_margin_last = fcf_pts[-1][1] if fcf_pts else None

    st.markdown("#### Wachstum & Rendite")
    m = st.columns(5)
    m[0].metric("Umsatz-CAGR", _pct(rev_cagr) if rev_cagr is not None
                else "—", help="Jaehrliches Umsatzwachstum ueber den Zeitraum")
    for col, key, label in zip(
            m[1:], ("roic", "roce", "roe"),
            ("ROIC", "ROCE", "ROE")):
        cv, slope, emoji, dcc = fm.trend_ampel([r[key] for r in rets])
        col.metric(f"{emoji} {label}", _pct(cv) if cv is not None else "—",
                   delta=(f"{slope:+.1f} pp/J" if slope is not None
                          else None), delta_color=dcc)
    cvr, slr, er, dr = fm.trend_ampel([r["roa"] for r in rets])
    m2 = st.columns(5)
    m2[0].metric(f"{er} ROA", _pct(cvr) if cvr is not None else "—",
                 delta=(f"{slr:+.1f} pp/J" if slr is not None else None),
                 delta_color=dr)
    m2[1].metric("FCF-Marge", _pct(fcf_margin_last))
    st.caption("Renditen mit Trendampel (lineare Steigung pp/Jahr). ROIC = "
               "NOPAT/(Schulden+EK−Cash). Renditen/FCF aus on-Demand-"
               "Jahresdaten; GuV-Quelle: " + inc.get("source", "—") + ".")

    # --- Margen-Trend ---
    msr = fm.margin_series(rows)
    df = pd.DataFrame([{
        "period_end": pd.to_datetime(x["period_end"]),
        "Bruttomarge": (x["gross"] * 100 if x["gross"] is not None else None),
        "Operative Marge": (x["operating"] * 100
                            if x["operating"] is not None else None),
        "Nettomarge": (x["net"] * 100 if x["net"] is not None else None),
    } for x in msr])
    if len(df) >= 2:
        st.markdown("#### Margen-Trend")
        fig = go.Figure()
        for name, color in [("Bruttomarge", "#0F6E56"),
                            ("Operative Marge", "#1D9E75"),
                            ("Nettomarge", "#5DCAA5")]:
            fig.add_trace(go.Scatter(
                x=df["period_end"], y=df[name], name=name,
                mode="lines+markers", line=dict(color=color, width=2),
                connectgaps=False,
                hovertemplate=f"%{{x|%Y}}<br>{name}: %{{y:.1f}}%"
                              "<extra></extra>"))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title="%", legend=dict(orientation="h",
                                                       y=-0.2),
                          hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

    # --- Umsatzverlauf ---
    rdf = pd.DataFrame([{"period_end": pd.to_datetime(r["period_end"]),
                         "revenue": r.get("revenue")} for r in rows])
    if len(rdf) >= 2:
        fig2 = go.Figure(go.Bar(x=rdf["period_end"], y=rdf["revenue"],
                                marker_color="#0F6E56",
                                hovertemplate="%{x|%Y}<br>%{y:,.0f}"
                                              "<extra></extra>"))
        fig2.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                           title=f"Umsatz ({cur})", yaxis_title=cur)
        st.plotly_chart(fig2, use_container_width=True)


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

# ---- Frage-Tab 1: Geschaeft (Phase 3) ----
with tabs[1]:
    st.markdown(f"#### {_QUESTIONS[0][1]}")
    try:
        render_business(ticker, src)
    except Exception as e:  # noqa: BLE001
        st.warning(f"Geschaeft nicht ladbar: {e.__class__.__name__}: {e}")

# ---- Frage-Tabs 2-6 (Phase-3-Platzhalter, folgen) ----
for i, (short, full, desc) in enumerate(_QUESTIONS[1:], start=2):
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
