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

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from modules.dashboard import company_data as cd
from modules.dashboard import finmetrics as fm
from modules.dashboard import market as mkt
from modules.dashboard.score_config import CFG as _SCORE
from modules.dashboard.components.format import _missing, de_dec

# DB optional (Universums-Auswahl) — defensiv.
try:
    from modules.dashboard.db import run_query as _run_query
except Exception:  # noqa: BLE001
    _run_query = None

# Anzeige-Defaults (im Seiten-Body durch die Widgets ueberschrieben). Als
# Modul-Globals vorbelegt, damit die render_*-Funktionen sie immer aufloesen.
N_YEARS = 5
PERIOD = "annual"  # 'annual' (10-K) | 'quarterly' (10-Q)
_XHOVER = "%Y"     # Plotly-Datumsformat (Jahr bzw. Monat+Jahr bei Quartal)


# ---------- Cache-Wrapper um die (reine) Datenschicht ----------

@st.cache_data(ttl=3600, show_spinner=False)
def _resolve(ticker: str):
    return cd.resolve(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _income(ticker: str, n: int = 5, period: str = "annual"):
    return cd.income_history(ticker, n_years=n, period=period)


@st.cache_data(ttl=3600, show_spinner=False)
def _year_metrics(ticker: str, n: int = 5, period: str = "annual"):
    return cd.year_metrics(ticker, n_years=n, period=period)


@st.cache_data(ttl=3600, show_spinner=False)
def _balance(ticker: str):
    return cd.balance(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _balance_hist(ticker: str, n: int = 5, period: str = "annual"):
    return cd.balance_history(ticker, n_years=n, period=period)


@st.cache_data(ttl=3600, show_spinner=False)
def _latest_price(ticker: str):
    return mkt.latest_close(ticker)


@st.cache_data(ttl=86400, show_spinner=False)
def _prices(ticker: str, start_iso: str, end_iso: str):
    return mkt.price_history(ticker, start_iso, end_iso)


@st.cache_data(ttl=86400, show_spinner=False)
def _splits(ticker: str):
    return mkt.splits(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _sbc(ticker: str):
    return cd.sbc_latest(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _nongaap(ticker: str):
    return cd.earnings_nongaap(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _insider_tx(ticker: str):
    return cd.insider_tx(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _mgmt_changes(ticker: str):
    return cd.mgmt_changes(ticker)


@st.cache_data(ttl=86400, show_spinner=False)
def _beneficial(ticker: str):
    return cd.beneficial(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _institutional(ticker: str):
    return cd.institutional(ticker)


@st.cache_data(ttl=86400, show_spinner=False)
def _first_filing(ticker: str, owner: str, cik):
    return cd.first_filing(ticker, owner, cik)


@st.cache_data(ttl=3600, show_spinner=False)
def _sbc_hist(ticker: str, n: int = 5, period: str = "annual"):
    return cd.sbc_history(ticker, n_years=n, period=period)


@st.cache_data(ttl=3600, show_spinner=False)
def _earnings_hist(ticker: str, n: int = 5, period: str = "annual"):
    return cd.earnings_history(ticker, n_years=n, period=period)


@st.cache_data(ttl=86400, show_spinner=False)
def _ppe_series(ticker: str):
    return cd.ppe_series(ticker)


@st.cache_data(ttl=86400, show_spinner=False)
def _emp_map(ticker: str):
    return cd.employee_map(ticker)


@st.cache_data(ttl=86400, show_spinner=False)
def _emp_text(accession_no: str):
    return cd.employee_from_text(accession_no)


def _render_physical(ticker: str, cur: str) -> None:
    """Physical Growth Index (PP&E/CapEx/Mitarbeiter) — Button-gated."""
    rows = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    if len(rows) < 2:
        st.info("Mind. 2 Jahre noetig."); return
    ppe_m = _ppe_series(ticker)
    if ppe_m:
        rows = [dict(d, ppe_gross=(_series_at(ppe_m, str(d["period_end"])[:10])
                                   or d.get("ppe_gross"))) for d in rows]
    emp_m = _emp_map(ticker)
    if emp_m:
        rows = [dict(d, employees=(_series_at(emp_m, str(d["period_end"])[:10])
                                   or d.get("employees"))) for d in rows]
    if not any(d.get("employees") for d in rows):
        rows = [dict(d, employees=(d.get("employees")
                     or _emp_text(d.get("accession_no")))) for d in rows]

    def _sg(key, valfn=None):
        pts = []
        for d in rows:
            v = valfn(d) if valfn else d.get(key)
            if v is not None:
                pts.append((pd.to_datetime(d["period_end"]), v))
        if not pts:
            return None, None
        if len(pts) < 2:
            return pts[-1][1], None
        yrs = (pts[-1][0] - pts[0][0]).days / 365.25
        return pts[-1][1], fm.cagr(pts[0][1], pts[-1][1], yrs)

    ppe_last, g_ppe = _sg("ppe_gross")
    cx_last, g_cx = _sg(None, lambda d: _abs_or(d.get("capex")))
    emp_last, g_emp = _sg("employees")
    revemp_last, g_re = _sg(None, lambda d: fm.safe_div(d.get("revenue"),
                                                        d.get("employees")))
    w = _SCORE["physical_growth"]["weights"]
    comp = [("ppe", g_ppe), ("employees", g_emp), ("capex", g_cx)]
    num = den = 0.0
    for k, g in comp:
        if g is not None:
            num += w[k] * g; den += w[k]
    idx = (num / den) if den else None
    if idx is not None:
        st.markdown(f"**Physical Growth Index: {_pct(idx)} p.a.**")
    m = st.columns(4)
    m[0].metric("PP&E", _money(ppe_last, cur),
                delta=_pct(g_ppe) if g_ppe is not None else None,
                delta_color="off")
    m[1].metric("CapEx", _money(cx_last, cur),
                delta=_pct(g_cx) if g_cx is not None else None,
                delta_color="off")
    m[2].metric("Mitarbeiter", de_dec(emp_last, 0) if emp_last else "—",
                delta=_pct(g_emp) if g_emp is not None else None,
                delta_color="off")
    m[3].metric("Umsatz / Mitarbeiter",
                _money(revemp_last, cur) if revemp_last is not None else "—",
                delta=_pct(g_re) if g_re is not None else None,
                delta_color="off")
    st.caption("Index = 0,4·ΔPP&E + 0,3·ΔMitarbeiter + 0,3·ΔCapEx (CAGR). "
               "PP&E via company-concept, Mitarbeiter via dei/10-K-Text.")


_MOAT_LABELS = {
    "gross_margin_trend": "Gross-Margin-Trend", "roic_stability":
    "ROIC-Stabilitaet", "fcf_margin": "FCF-Marge", "rnd_efficiency":
    "R&D-Effizienz", "market_share_proxy": "Marktanteil (Proxy)",
    "buybacks": "Aktienrueckkaeufe"}


def _pct(v, places: int = 1) -> str:
    return "—" if _missing(v) else de_dec(float(v) * 100.0, places) + " %"


def _abs_or(v):
    return abs(v) if v is not None else None


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
    inc = _income(ticker, N_YEARS, PERIOD)
    rows = [r for r in (inc.get("rows") or [])
            if (r.get("form_type") or "").upper().startswith(
                "10-Q" if PERIOD == "quarterly" else "10-K")] \
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
    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
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
                hovertemplate=f"%{{x|{_XHOVER}}}<br>{name}: %{{y:.1f}}%"
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
                                hovertemplate="%{x|" + _XHOVER + "}<br>%{y:,.0f}"
                                              "<extra></extra>"))
        fig2.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                           title=f"Umsatz ({cur})", yaxis_title=cur)
        st.plotly_chart(fig2, use_container_width=True)

    # --- Umsatz & GuV (Segmente, Kostenaufteilung, Sankey) ---
    with st.expander("Umsatz & GuV — Segmente, Kostenaufteilung, Sankey"):
        try:
            _render_revenue_guv(ticker, src)
        except Exception as e:  # noqa: BLE001
            st.warning(f"Umsatz & GuV nicht ladbar: {e.__class__.__name__}")

    # --- Physical Growth (button-gated, da Mitarbeiter-Text teuer) ---
    with st.expander("Physical Growth (PP&E, CapEx, Mitarbeiter)"):
        if st.button("Physical Growth laden", key=f"phys_{ticker}"):
            _render_physical(ticker, cur)
        else:
            st.caption("Laedt PP&E/CapEx/Mitarbeiter + Index on-Demand "
                       "(mehrere API-Calls).")


@st.cache_data(ttl=3600, show_spinner=False)
def _segments(ticker: str):
    return cd.revenue_segments(ticker)


def _render_revenue_guv(ticker: str, src) -> None:
    """Umsatz & GuV (aus Thesis): Segmente, Kostenaufteilung, GuV-Sankey."""
    cur = src.currency or "USD"
    inc = _income(ticker, N_YEARS, PERIOD)
    rows = [r for r in (inc.get("rows") or [])
            if (r.get("form_type") or "").upper().startswith(
                "10-Q" if PERIOD == "quarterly" else "10-K")] \
        or (inc.get("rows") or [])

    # --- Umsatz-Segmente (Stacked Bar) ---
    seg = _segments(ticker)
    srows = seg.get("rows") or []
    if srows:
        axes = list(dict.fromkeys(r["axis"] for r in srows))
        ax = (st.selectbox("Segment-Achse", axes, key=f"seg_{ticker}")
              if len(axes) > 1 else axes[0])
        ssel = [r for r in srows if r["axis"] == ax]
        sdf = pd.DataFrame(ssel)
        piv = sdf.pivot_table(index="period_end", columns="member_label",
                              values="value", aggfunc="first")
        fig = go.Figure()
        pal = ["#0F6E56", "#1D9E75", "#5DCAA5", "#9FE1CB", "#3B6D11",
               "#639922", "#97C459", "#C0DD97"]
        for i, col in enumerate(piv.columns):
            fig.add_trace(go.Bar(name=str(col),
                                 x=pd.to_datetime(piv.index), y=piv[col],
                                 marker_color=pal[i % len(pal)]))
        fig.update_layout(barmode="stack", height=320,
                          margin=dict(l=10, r=10, t=30, b=10),
                          title=f"Umsatz-Segmente ({cur})",
                          legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("Keine Segment-Daten verfuegbar.")

    # --- Kostenaufteilung (absolut, Stacked Bar) ---
    if len(rows) >= 2:
        def _rest(r):
            o = r.get("operating_expense")
            if o is None:
                return None
            return max(0.0, o - (r.get("rd_expense") or 0)
                       - (r.get("sga_expense") or 0))
        cdf = pd.DataFrame([{
            "pe": pd.to_datetime(r["period_end"]),
            "Herstellkosten": r.get("cost_of_revenue"),
            "F&E": r.get("rd_expense"),
            "Vertrieb & Verw.": r.get("sga_expense"),
            "Uebriger OpEx": _rest(r),
            "Operatives Ergebnis": r.get("operating_income"),
        } for r in rows])
        fig = go.Figure()
        for name, color in [("Herstellkosten", "#A32D2D"),
                            ("F&E", "#C75B5B"), ("Vertrieb & Verw.", "#E08A8A"),
                            ("Uebriger OpEx", "#B4B2A9"),
                            ("Operatives Ergebnis", "#1D9E75")]:
            fig.add_trace(go.Bar(name=name, x=cdf["pe"], y=cdf[name],
                                 marker_color=color))
        fig.update_layout(barmode="stack", height=320,
                          margin=dict(l=10, r=10, t=30, b=10),
                          title=f"Kostenaufteilung absolut ({cur})",
                          legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig, use_container_width=True)

    # --- GuV-Sankey (juengste Periode) ---
    if rows:
        r = rows[-1]
        _sankey_guv(r, cur)


def _sankey_guv(r: dict, cur: str) -> None:
    def g(k):
        v = r.get(k)
        return None if _missing(v) else float(v)
    rev, cogs, gross = g("revenue"), g("cost_of_revenue"), g("gross_profit")
    rd, sga, opex = g("rd_expense"), g("sga_expense"), g("operating_expense")
    opinc, tax, net = g("operating_income"), g("tax_expense"), g("net_income")
    if rev is None or net is None:
        return
    labels, idx, colors = [], {}, []
    GREEN, RED, GRAY = "#3B6D11", "#A32D2D", "#444441"

    def node(key, name, color):
        idx[key] = len(labels); labels.append(name); colors.append(color)
    node("rev", "Umsatz", GRAY)
    for k, n, c in [("cogs", "Herstellkosten", RED),
                    ("gross", "Bruttogewinn", GREEN),
                    ("opex", "Betriebsaufwand", RED), ("rd", "F&E", RED),
                    ("sga", "Vertrieb & Verw.", RED),
                    ("opinc", "Operatives Ergebnis", GREEN),
                    ("tax", "Steuern", RED), ("net", "Nettogewinn", GREEN)]:
        node(k, n, c)
    S, T, V = [], [], []

    def link(a, b, v):
        if v and v > 0 and a in idx and b in idx:
            S.append(idx[a]); T.append(idx[b]); V.append(v)
    link("rev", "cogs", cogs); link("rev", "gross", gross)
    link("gross", "opex", opex); link("gross", "opinc", opinc)
    link("opex", "rd", rd); link("opex", "sga", sga)
    link("opinc", "tax", tax); link("opinc", "net", net)
    if not V:
        return
    fig = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(label=labels, color=colors, pad=18, thickness=14,
                  line=dict(color="white", width=1)),
        link=dict(source=S, target=T, value=V)))
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=24, b=10),
                      title=f"GuV-Struktur {str(r['period_end'])[:10]} ({cur})")
    st.plotly_chart(fig, use_container_width=True)


def render_balance_tab(ticker: str, src) -> None:
    """Tab 3 — Ist die Bilanz solide?"""
    cur = src.currency or "USD"
    bs = _balance(ticker)
    if bs is None:
        st.info("Keine Bilanzdaten verfuegbar.")
        return

    cr = fm.safe_div(bs.assets_current, bs.liabilities_current)
    inv = bs.inventory or 0.0
    quick = fm.safe_div((bs.assets_current or 0.0) - inv,
                        bs.liabilities_current)
    de = fm.safe_div(bs.total_debt, bs.equity)
    eqr = fm.safe_div(bs.equity, bs.total_assets)
    intang = (bs.goodwill or 0.0) + (bs.intangibles or 0.0)
    intang_pct = fm.safe_div(intang, bs.total_assets)
    nd = bs.net_debt

    # Leichte Bewertung (Schwellen analog Balance-Sheet-Score)
    checks = []
    if cr is not None:
        checks.append(("Current Ratio > 1,5", cr > 1.5))
    if nd is not None:
        checks.append(("Netto-Cash", nd < 0))
    if de is not None:
        checks.append(("Debt/Equity < 0,5", de < 0.5))
    if eqr is not None:
        checks.append(("Eigenkapitalquote > 40 %", eqr > 0.40))
    if checks:
        passed = sum(1 for _, ok in checks if ok)
        r = passed / len(checks)
        lines = "  \n".join(f"{'✅' if ok else '❌'} {n}"
                            for n, ok in checks)
        box = st.success if r >= 0.75 else st.info if r >= 0.5 else st.warning
        verdict = ("stark" if r >= 0.75 else "solide" if r >= 0.5
                   else "schwach")
        box(f"Bilanz wirkt **{verdict}** — {passed}/{len(checks)}  \n{lines}")

    st.caption(f"Stichtag {str(bs.period_end)[:10]} ({bs.form_type}).")
    m = st.columns(3)
    m[0].metric("Current Ratio", de_dec(cr, 2) if not _missing(cr) else "—")
    m[1].metric("Quick Ratio",
                de_dec(quick, 2) if not _missing(quick) else "—")
    m[2].metric("Debt / Equity", de_dec(de, 2) if not _missing(de) else "—")
    m2 = st.columns(3)
    m2[0].metric("Net Debt" if (nd or 0) >= 0 else "Net Cash",
                 _money(abs(nd) if nd is not None else None, cur))
    m2[1].metric("Eigenkapitalquote", _pct(eqr))
    m2[2].metric("Goodwill + Intangibles", _pct(intang_pct))

    hist = _balance_hist(ticker, N_YEARS, PERIOD)
    if len(hist) >= 2:
        bdf = pd.DataFrame([{
            "period_end": pd.to_datetime(b.period_end),
            "current_ratio": fm.safe_div(b.assets_current,
                                         b.liabilities_current),
            "debt_to_equity": fm.safe_div(b.total_debt, b.equity),
            "net_debt": b.net_debt,
        } for b in hist])
        st.markdown("#### Trend (10-K, jaehrlich)")
        t1, t2 = st.columns(2)
        f1 = go.Figure()
        f1.add_trace(go.Scatter(x=bdf["period_end"], y=bdf["current_ratio"],
                                name="Current Ratio", mode="lines+markers",
                                line=dict(color="#0F6E56", width=2)))
        f1.add_trace(go.Scatter(x=bdf["period_end"], y=bdf["debt_to_equity"],
                                name="Debt/Equity", mode="lines+markers",
                                line=dict(color="#A32D2D", width=2)))
        f1.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title="Current Ratio & Debt/Equity",
                         legend=dict(orientation="h", y=-0.2),
                         hovermode="x unified")
        t1.plotly_chart(f1, use_container_width=True)
        nd_col = ["#1D9E75" if (v is not None and v < 0) else "#A32D2D"
                  for v in bdf["net_debt"]]
        f2 = go.Figure(go.Bar(x=bdf["period_end"], y=bdf["net_debt"],
                              marker_color=nd_col,
                              hovertemplate="%{x|" + _XHOVER + "}<br>%{y:,.0f}"
                                            "<extra></extra>"))
        f2.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title=f"Net Debt ({cur}) — gruen = Netto-Cash",
                         yaxis_title=cur)
        t2.plotly_chart(f2, use_container_width=True)


def render_valuation_tab(ticker: str, src) -> None:
    """Tab 6 — Ist die Bewertung attraktiv? (EV, EV/FCF, Yields, KGV, Kurs)."""
    cur = src.currency or "USD"
    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    if not ym:
        st.info("Keine Jahresdaten verfuegbar.")
        return
    last = ym[-1]
    price = _latest_price(ticker)
    shares = last.get("shares_outstanding") or last.get("diluted_shares")

    if price is None or not shares:
        st.info("Bewertung braucht Marktpreis × Aktien — nicht verfuegbar "
                "(yfinance) bzw. keine Aktienzahl.")
    else:
        mcap = price * shares
        ev = mcap + (last.get("net_debt") or 0.0)
        ev_fcf = (ev / last["fcf"]
                  if (last.get("fcf") and last["fcf"] > 0) else None)
        ey_ebit = fm.safe_div(last.get("operating_income"), ev)
        ey_class = fm.safe_div(last.get("net_income"), mcap)
        pe = (mcap / last["net_income"]
              if (last.get("net_income") and last["net_income"] > 0)
              else None)

        m = st.columns(3)
        m[0].metric("Enterprise Value", _money(ev, cur),
                    help="Marktkap. (Kurs × Aktien) + Net Debt")
        m[1].metric("EV / FCF",
                    f"{de_dec(ev_fcf, 1)}x" if ev_fcf is not None else "—",
                    help="Niedriger = guenstiger")
        m[2].metric("KGV (P/E)",
                    f"{de_dec(pe, 1)}" if pe is not None else "—")
        m2 = st.columns(3)
        m2[0].metric("Earnings Yield (EBIT/EV)", _pct(ey_ebit),
                     help="Operatives Ergebnis / EV (Greenblatt)")
        m2[1].metric("Earnings Yield (klassisch)", _pct(ey_class),
                     help="Nettogewinn / Marktkapitalisierung")
        m2[2].metric("Marktkapitalisierung", _money(mcap, cur),
                     help=f"Kurs {de_dec(price, 2)} {cur} × "
                          f"{de_dec(shares / 1e9, 2)} Mrd Aktien")

    # --- Kurs-Chart (2 Jahre) ---
    end_iso = date.today().isoformat()
    start_iso = (pd.to_datetime(end_iso) - pd.Timedelta(days=730)) \
        .date().isoformat()
    px = _prices(ticker, start_iso, end_iso)
    if px:
        pdf = pd.DataFrame(
            [{"d": pd.to_datetime(k), "c": v} for k, v in sorted(px.items())])
        fig = go.Figure(go.Scatter(x=pdf["d"], y=pdf["c"], mode="lines",
                                   line=dict(color="#0F6E56", width=1.5)))
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                          title=f"Kurs ({cur}, 2 Jahre)", yaxis_title=cur)
        st.plotly_chart(fig, use_container_width=True)

    st.caption("Bewertung mit aktuellem Marktpreis (yfinance) und letzter "
               "Jahres-GuV/-Bilanz. EV/FCF & Yields wie im Earnings-Modul.")

    # --- Gewinnruecklagen, EPS, Equity, FCF & EV — Verlauf ---
    eh = _earnings_hist(ticker, N_YEARS, PERIOD)
    if len(eh) >= 2:
        with st.expander("Gewinnruecklagen, EPS, Equity, FCF & EV — "
                         "Verlauf"):
            splits = _splits(ticker)
            yends = [str(d["period_end"])[:10] for d in eh]
            start = (pd.to_datetime(min(yends)) - pd.Timedelta(days=10)) \
                .date().isoformat()
            px = _prices(ticker, start, date.today().isoformat())

            def _evy(d):
                sh = d.get("diluted_shares")
                if sh:
                    sh *= fm.split_factor(splits, str(d["period_end"])[:10])
                c = _series_at(px, str(d["period_end"])[:10], tol_days=15)
                return (c * sh + (d.get("net_debt") or 0.0)
                        if (c and sh) else None)

            def _adj(d, key):
                v = d.get(key)
                return (v / fm.split_factor(splits, str(d["period_end"])[:10])
                        if v is not None else None)

            edf = pd.DataFrame([{
                "pe": pd.to_datetime(d["period_end"]),
                "retained": d.get("retained_earnings"),
                "equity": d.get("equity"), "fcf": d.get("fcf"),
                "eps_b": _adj(d, "eps_basic"), "eps_d": _adj(d, "eps_diluted"),
                "ev": _evy(d)} for d in eh])

            t1, t2 = st.columns(2)
            rc = ["#1D9E75" if (v is not None and v >= 0) else "#A32D2D"
                  for v in edf["retained"]]
            f1 = go.Figure(go.Bar(x=edf["pe"], y=edf["retained"],
                                  marker_color=rc))
            f1.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                             title=f"Gewinnruecklagen ({cur})")
            t1.plotly_chart(f1, use_container_width=True)
            f2 = go.Figure(go.Bar(x=edf["pe"], y=edf["equity"],
                                  marker_color="#1D9E75"))
            f2.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                             title=f"Eigenkapital ({cur})")
            t2.plotly_chart(f2, use_container_width=True)

            t3, t4 = st.columns(2)
            f3 = go.Figure()
            f3.add_trace(go.Scatter(x=edf["pe"], y=edf["eps_b"],
                                    name="EPS unverw.", mode="lines+markers",
                                    line=dict(color="#0F6E56", width=2),
                                    connectgaps=False))
            f3.add_trace(go.Scatter(x=edf["pe"], y=edf["eps_d"],
                                    name="EPS verw.", mode="lines+markers",
                                    line=dict(color="#B4862B", width=2),
                                    connectgaps=False))
            f3.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                             title=f"EPS ({cur}, split-bereinigt)",
                             legend=dict(orientation="h", y=-0.3))
            t3.plotly_chart(f3, use_container_width=True)
            fc = ["#1D9E75" if (v is not None and v >= 0) else "#A32D2D"
                  for v in edf["fcf"]]
            f4 = go.Figure(go.Bar(x=edf["pe"], y=edf["fcf"], marker_color=fc))
            f4.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                             title=f"Free Cash Flow ({cur})")
            t4.plotly_chart(f4, use_container_width=True)

            if edf["ev"].notna().any():
                f5 = go.Figure(go.Scatter(
                    x=edf["pe"], y=edf["ev"], mode="lines+markers",
                    line=dict(color="#444441", width=2), connectgaps=False))
                f5.update_layout(height=260,
                                 margin=dict(l=10, r=10, t=30, b=10),
                                 title=f"Enterprise Value ({cur}, "
                                       "Jahresend-Kurs × Aktien + Net Debt)")
                st.plotly_chart(f5, use_container_width=True)
            st.caption("EPS und EV split-bereinigt; EV-Historie ist eine "
                       "Naeherung (Jahresend-Kurs).")


def render_moat_tab(ticker: str, src) -> None:
    """Tab 2 — Hat das Unternehmen einen Burggraben? (Moat-Score)."""
    rows = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    if len(rows) < 2:
        st.info("Mind. 2 Jahresberichte fuer den Moat-Score noetig.")
        return
    rows = fm.split_adjust_shares(rows, _splits(ticker))   # gegen Split-Bias

    mcfg = _SCORE["moat"]
    sig = fm.moat_signals(rows, mcfg["thresholds"])
    wts, bands = mcfg["weights"], mcfg["bands"]

    num = den = 0.0
    for name, (sc, _d) in sig.items():
        if sc is not None:
            num += sc * wts.get(name, 0); den += wts.get(name, 0)
    if den == 0:
        st.info("Keine Moat-Signale auswertbar."); return
    score = round(100 * num / den)
    n_ok = sum(1 for _, (sc, _d) in sig.items() if sc is not None)

    box = (st.success if score >= bands["strong"]
           else st.info if score >= bands["mixed"] else st.warning)
    verdict = ("breiter Moat" if score >= bands["strong"]
               else "schmaler Moat" if score >= bands["mixed"]
               else "kein klarer Moat")
    box(f"## {score}/100 — {verdict}")
    st.caption(f"Gewichteter Mittelwert ueber {n_ok}/6 auswertbare Signale "
               "(fehlende ausgeklammert, renormiert).")

    bar = pd.DataFrame([{
        "Signal": _MOAT_LABELS[k],
        "Score": round(100 * sig[k][0]) if sig[k][0] is not None else None,
        "Detail": sig[k][1]} for k in wts])
    fig = go.Figure(go.Bar(
        x=bar["Score"], y=bar["Signal"], orientation="h",
        marker_color=["#1D9E75" if (s is not None and s >= 70)
                      else "#B4862B" if (s is not None and s >= 40)
                      else "#A32D2D" if s is not None else "#CFCDC6"
                      for s in bar["Score"]],
        text=[f"{s}" if s is not None else "n/a" for s in bar["Score"]],
        textposition="auto", customdata=bar["Detail"],
        hovertemplate="%{y}: %{x}/100<br>%{customdata}<extra></extra>"))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis=dict(range=[0, 100], title="Teil-Score"))
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(pd.DataFrame([{
        "Signal": _MOAT_LABELS[k], "Gewicht": f"{int(wts[k] * 100)} %",
        "Score": (f"{round(100 * sig[k][0])}/100"
                  if sig[k][0] is not None else "n/a"),
        "Detail": sig[k][1]} for k in wts]),
        use_container_width=True, hide_index=True)

    if src.in_universe:
        st.caption("Peers/Branche & Dominanz: folgt bei der Thesis-"
                   "Integration (Phase 4, nur Universums-Werte).")


def render_earnings_real_tab(ticker: str, src) -> None:
    """Tab 5 — Sind die Gewinne echt? (Earnings Quality, Owner Earnings)."""
    cur = src.currency or "USD"

    # --- Earnings-Quality-Score ---
    sbc = _sbc(ticker)
    sbc_cfo = fm.safe_div(sbc.get("sbc"), sbc.get("cfo")) if sbc else None
    ng = _nongaap(ticker)
    eq = fm.earnings_quality(sbc_cfo, ng.get("categories"),
                             _SCORE["earnings_quality"])
    score, bands = eq["score"], eq["bands"]
    if score is not None:
        box = (st.success if score >= bands["strong"]
               else st.info if score >= bands["mixed"] else st.warning)
        v = ("hohe Qualitaet" if score >= bands["strong"]
             else "mittel" if score >= bands["mixed"]
             else "niedrig (viele Bereinigungen)")
        box(f"## {score}/100 — {v}")
        st.caption(f"{eq['n_ok']}/6 Dimensionen auswertbar. Hoeher = "
                   "sauberere, weniger bereinigte Gewinne.")
    if ng.get("categories") is None:
        st.caption(f"Kein Earnings-Exhibit auswertbar ({ng.get('error')}) — "
                   "nur SBC bewertet.")
    st.dataframe(pd.DataFrame([{
        "Dimension": lbl,
        "Score": f"{round(100 * sub)}/100" if sub is not None else "n/a",
        "Detail": det} for _k, lbl, sub, det in eq["rows"]]),
        use_container_width=True, hide_index=True)

    # --- Owner Earnings vs Nettogewinn vs FCF ---
    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    if ym:
        oe_series, method = fm.owner_earnings(ym)
        last = oe_series[-1]
        st.markdown("#### Owner Earnings vs Nettogewinn vs FCF")
        m = st.columns(3)
        m[0].metric("Owner Earnings", _money(last["oe"], cur),
                    help="Nettogewinn + D&A − Maintenance CapEx (Greenwald, "
                         f"{method})")
        m[1].metric("Nettogewinn", _money(last["ni"], cur))
        m[2].metric("Free Cash Flow", _money(last["fcf"], cur))
        if len(oe_series) >= 2:
            odf = pd.DataFrame([{
                "period_end": pd.to_datetime(o["period_end"]),
                "oe": o["oe"], "ni": o["ni"], "fcf": o["fcf"]}
                for o in oe_series])
            fig = go.Figure()
            fig.add_trace(go.Bar(name="Owner Earnings", x=odf["period_end"],
                                 y=odf["oe"], marker_color="#0F6E56"))
            fig.add_trace(go.Scatter(name="Nettogewinn", x=odf["period_end"],
                                     y=odf["ni"], mode="lines+markers",
                                     line=dict(color="#A32D2D", width=2)))
            fig.add_trace(go.Scatter(name="Free Cash Flow",
                                     x=odf["period_end"], y=odf["fcf"],
                                     mode="lines+markers",
                                     line=dict(color="#444441", width=2,
                                               dash="dot")))
            fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                              yaxis_title=cur, legend=dict(orientation="h",
                                                           y=-0.2),
                              hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

    st.caption("SBC quantitativ (SBC/operativer CF); Akquisitions-/"
               "Restrukturierungs-/Rechtsstreit-/Steuer-/Einmal-Add-backs "
               "aus dem Earnings-Exhibit. Owner Earnings = Cash-Realitaet "
               "vs. ausgewiesener Gewinn.")


def render_management_tab(ticker: str, src) -> None:
    """Tab 4 — Ist das Management gut? (Conviction, Tenure, Ownership,
    Turnover, Kapitalallokation, SBC/Verwaesserung)."""
    cur = src.currency or "USD"
    tx = _insider_tx(ticker)
    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    shares_out = None
    if ym:
        shares_out = ym[-1].get("shares_outstanding") \
            or ym[-1].get("diluted_shares")

    # --- Insider Conviction ---
    conv = fm.insider_conviction(tx, n_years=3,
                                 cfg=_SCORE["insider_conviction"]) if tx \
        else None
    if conv:
        box = (st.success if conv["label"] == "Bullisch"
               else st.warning if conv["label"] == "Bearisch" else st.info)
        lines = "  \n".join(f"{'➕' if p > 0 else '➖'} {n}: {p:+d}"
                            for n, p in conv["fired"]) or "keine Signale"
        box(f"**Insider-Signal: {conv['label']}** · Netto {conv['points']:+d}"
            f"  \n{lines}")
        if conv["routine"]:
            st.caption(f"{conv['routine']} Routine-Verkauf/e (10b5-1) "
                       "ausgeklammert.")

    # --- Tenure + Turnover ---
    df = pd.DataFrame(tx) if tx else pd.DataFrame()

    def _tenure(flag):
        if df.empty or flag not in df.columns:
            return None, None
        sub = df[df[flag] == True]  # noqa: E712
        if sub.empty:
            return None, None
        sub = sub.assign(_d=pd.to_datetime(sub["transaction_date"],
                                           errors="coerce"))
        cur_row = sub.sort_values("_d").iloc[-1]
        owner = cur_row["owner"]
        cik = cur_row.get("owner_cik") if "owner_cik" in sub.columns else None
        f_iso = _first_filing(ticker, owner, cik)
        if not f_iso:
            return owner, None
        yrs = (pd.Timestamp.utcnow()
               - pd.to_datetime(f_iso, utc=True, errors="coerce")).days \
            / 365.25
        return owner, yrs

    ceo_name, ceo_t = _tenure("is_ceo")
    cfo_name, cfo_t = _tenure("is_cfo")
    changes = _mgmt_changes(ticker)
    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=5 * 365)
    cdts = pd.to_datetime([c.get("filed_at") for c in changes],
                          errors="coerce", utc=True)
    turnover = int((cdts >= cutoff).sum()) if len(cdts) else 0

    tm = st.columns(4)
    tm[0].metric("CEO-Tenure",
                 f"{de_dec(ceo_t, 1)} J" if ceo_t is not None else "—")
    tm[1].metric("CFO-Tenure",
                 f"{de_dec(cfo_t, 1)} J" if cfo_t is not None else "—")
    tm[2].metric("Mgmt-Wechsel (5 J)", str(turnover),
                 help="8-K Item 5.02")
    tm[3].metric("Insider Käufer/Verkäufer",
                 f"{conv['n_buyers']}/{conv['n_sellers']}" if conv else "—")

    # --- Ownership-Struktur ---
    mgmt_pct = strat_pct = inst_pct = None
    if not df.empty and "shares_following" in df.columns and shares_out:
        held = df[df["shares_following"].notna()].copy()
        held["gid"] = (held["owner_cik"].fillna(held["owner"])
                       if "owner_cik" in held.columns else held["owner"])
        held = held.sort_values("transaction_date")
        agg = held.groupby("gid").agg(
            shares=("shares_following", "last"),
            off=("is_officer", "max") if "is_officer" in held else
            ("shares_following", "size"),
            dir=("is_director", "max") if "is_director" in held else
            ("shares_following", "size"),
            ten=("is_tenpct", "max") if "is_tenpct" in held else
            ("shares_following", "size"))
        mgmt_mask = agg["off"].astype(bool) | agg["dir"].astype(bool)
        mgmt_pct = fm.safe_div(float(agg.loc[mgmt_mask, "shares"].sum()),
                               shares_out)
        strat_pct = fm.safe_div(
            float(agg.loc[agg["ten"].astype(bool) & ~mgmt_mask,
                          "shares"].sum()), shares_out)
    bo = _beneficial(ticker)
    insider_pct = bo.get("group_pct")
    if insider_pct is None:
        insider_pct = mgmt_pct
    inst = _institutional(ticker)
    if inst.get("holdings") and shares_out:
        ih = pd.DataFrame(inst["holdings"])
        ih = ih[ih["shares"].notna()]
        if not ih.empty:
            ih["pdt"] = pd.to_datetime(ih["period"], errors="coerce")
            cq = ih[ih["pdt"] == ih["pdt"].max()] if ih["pdt"].notna().any() \
                else ih
            inst_pct = fm.safe_div(
                float(cq.drop_duplicates("manager")["shares"].sum()),
                shares_out)
    parts = [v for v in (insider_pct, inst_pct, strat_pct) if v is not None]
    strong = min(sum(parts), 1.0) if parts else None
    free = max(0.0, 1 - strong) if strong is not None else None
    om = st.columns(4)
    om[0].metric("Free Float", _pct(free) if free is not None else "—")
    om[1].metric("Institutionell", _pct(inst_pct))
    om[2].metric("Insider", _pct(insider_pct))
    om[3].metric("Strong Hands", _pct(strong) if strong is not None else "—",
                 help="Institutionell + Insider + Strategisch (Naeherung)")

    # --- Kapitalallokation + SBC/Verwaesserung ---
    if ym:
        last = ym[-1]
        def _a(v):
            return abs(v) if v is not None else None
        bb, dv = _a(last.get("buybacks")), _a(last.get("dividends"))
        cx, aq = _a(last.get("capex")), _a(last.get("acquisitions"))
        payout = fm.safe_div((bb or 0) + (dv or 0), last.get("fcf"))
        km = st.columns(4)
        km[0].metric("Rueckkaeufe", _money(bb, cur))
        km[1].metric("Dividenden", _money(dv, cur))
        km[2].metric("Reinvestition (CapEx)", _money(cx, cur))
        km[3].metric("Ausschuettungsquote",
                     _pct(payout) if payout is not None else "—",
                     help="(Rueckkauf + Dividende) / FCF")

        sbc = _sbc(ticker)
        sbc_cfo = fm.safe_div(sbc.get("sbc"), sbc.get("cfo")) if sbc else None
        adj = fm.split_adjust_shares(ym, _splits(ticker))
        sh = [(d["period_end"], d["diluted_shares"]) for d in adj
              if d.get("diluted_shares")]
        dil = None
        if len(sh) >= 2:
            yrs = (pd.to_datetime(sh[-1][0])
                   - pd.to_datetime(sh[0][0])).days / 365.25
            dil = fm.cagr(sh[0][1], sh[-1][1], yrs)
        sm = st.columns(2)
        sm[0].metric("SBC / operativer CF", _pct(sbc_cfo))
        sm[1].metric("Aktien p.a. (Verwaesserung)",
                     _pct(dil) if dil is not None else "—",
                     help="+ = Verwaesserung, − = Rueckkauf (split-bereinigt)")

    # --- FCF-Verwendung (Jahres-Trend) ---
    if len(ym) >= 2:
        with st.expander("FCF-Verwendung (Kapitalallokation, Verlauf)"):
            af = pd.DataFrame([{
                "period_end": pd.to_datetime(d["period_end"]),
                "Rueckkaeufe": _abs_or(d.get("buybacks")),
                "Dividenden": _abs_or(d.get("dividends")),
                "Reinvestition": _abs_or(d.get("capex")),
                "Akquisitionen": _abs_or(d.get("acquisitions")),
            } for d in ym])
            fig = go.Figure()
            for name, color in [("Rueckkaeufe", "#0F6E56"),
                                ("Dividenden", "#5DCAA5"),
                                ("Reinvestition", "#1D9E75"),
                                ("Akquisitionen", "#B4862B")]:
                fig.add_trace(go.Bar(name=name, x=af["period_end"],
                                     y=af[name], marker_color=color))
            fig.add_trace(go.Scatter(
                name="Free Cash Flow", x=af["period_end"],
                y=[d.get("fcf") for d in ym], mode="lines+markers",
                line=dict(color="#444441", width=2, dash="dot")))
            fig.update_layout(barmode="stack", height=320,
                              margin=dict(l=10, r=10, t=10, b=10),
                              yaxis_title=cur, legend=dict(orientation="h",
                                                           y=-0.2),
                              hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Mittelverwendung (positive Betraege) + FCF-Linie. "
                       "Zeigt, wie der freie Cashflow allokiert wird.")

    # --- Stock-based Compensation (Verlauf) ---
    sbc_rows = _sbc_hist(ticker, N_YEARS, PERIOD)
    if len(sbc_rows) >= 2:
        with st.expander("Stock-based Compensation (SBC) — Verlauf"):
            sdf = pd.DataFrame([{
                "period_end": pd.to_datetime(d["period_end"]),
                "sbc_rev": fm.safe_div(d.get("sbc"), d.get("revenue")),
                "sbc_cfo": fm.safe_div(d.get("sbc"), d.get("cfo")),
            } for d in sbc_rows])
            f1 = go.Figure()
            f1.add_trace(go.Scatter(
                x=sdf["period_end"], y=sdf["sbc_rev"] * 100,
                name="SBC / Umsatz", mode="lines+markers",
                line=dict(color="#A32D2D", width=2), connectgaps=False))
            f1.add_trace(go.Scatter(
                x=sdf["period_end"], y=sdf["sbc_cfo"] * 100,
                name="SBC / operativer CF", mode="lines+markers",
                line=dict(color="#B4862B", width=2), connectgaps=False))
            f1.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                             title="SBC-Belastung", yaxis_title="%",
                             legend=dict(orientation="h", y=-0.25),
                             hovermode="x unified")
            st.plotly_chart(f1, use_container_width=True)
            st.caption("SBC aus dem Cashflow-Statement. Hohe/steigende "
                       "SBC-Quote = wachsende nicht-zahlungswirksame "
                       "Verguetung.")

    st.caption("Tenure via fruehestes Insider-Filing (CIK). Ownership: "
               "DEF-14A (Insider) + 13F-Top-Sample (institutionell) + "
               "10%-Eigner (strategisch) — Anteile sind Untergrenzen. "
               "Conviction klammert 10b5-1-Routine-Verkaeufe aus.")


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

# =====================================================================
# Metadaten-Registry (Phase 3/4-Umbau, Schritt 1)
# ---------------------------------------------------------------------
# Die Seite wird aus dieser deklarativen Liste generiert: Reihenfolge,
# Sichtbarkeit und neue Kategorien sind reine Datenaenderungen. Die
# render-Funktionen bleiben Code; alles andere ist Metadaten. Spaetere
# Schritte (Navigator statt Tabs, Lazy-Expander, status/Badges) bauen auf
# dieser Struktur auf — der heutige Aufbau bleibt verhaltensgleich.
# =====================================================================

@dataclass
class Category:
    """Eine Kategorie (heute = ein Tab) der Unternehmens-Analyse."""
    id: str
    title: str                                  # Reiter-/Navigations-Label
    render: Callable[[str, object], None]       # render(ticker, src)
    question: Optional[str] = None              # "#### …"-Header; None = keiner
    desc: str = ""                              # Kurzbeschreibung (Phase 3+)
    err_label: str = ""                         # Klartext im Fehler-Fallback
    is_question: bool = False                   # zaehlt zur 6-Fragen-Scorecard
    status: str = "stable"                       # stable | beta | todo
    universe_only: bool = False                 # nur fuer Universums-Werte


def render_overview(ticker: str, src) -> None:
    """Ueberblick: Scorecard-Geruest + Datenbasis-Nachweis."""
    st.markdown("#### Gesamturteil")
    st.caption("Scorecard je Frage (Ampeln) folgt in Phase 3 — die "
               "Score-Logik wird aus den bestehenden Ad-Hoc-Modulen "
               "wiederverwendet.")
    sc = [{"Frage": c.question, "Bewertung": "— (Phase 3)"}
          for c in CATEGORIES if c.is_question]
    st.dataframe(sc, use_container_width=True, hide_index=True)

    st.markdown("#### Datenbasis")
    try:
        ih = _income(ticker, N_YEARS, PERIOD)
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


def render_portfolio(ticker: str, src) -> None:
    """Portfolio & Signale (nur Universums-Werte) — Geruest."""
    st.markdown("#### Portfolio & Signale")
    if src.in_universe:
        st.info("Folgt in Phase 3: Holdings, MtM, Thesis-Ampel, Signale, "
                "Termine, Screener-Links (nur fuer Universums-Werte).")
    else:
        st.caption(f"{src.ticker} ist nicht im Portfolio-Universum — kein "
                   "Portfolio-Kontext.")


CATEGORIES: list[Category] = [
    Category("overview", "Ueberblick", render_overview,
             err_label="Ueberblick"),
    Category("business", "1 Geschaeft", render_business,
             question="Ist das Geschaeft gut?",
             desc="Umsatzwachstum, Margen-Trend, ROIC/ROCE/ROE/ROA, "
                  "FCF-Marge, Umsatz/Mitarbeiter.",
             err_label="Geschaeft", is_question=True),
    Category("moat", "2 Burggraben", render_moat_tab,
             question="Hat das Unternehmen einen Burggraben?",
             desc="Moat-Score (Margen-Stabilitaet, ROIC-Stabilitaet, "
                  "F&E-Effizienz, Rueckkaeufe, Marktanteil) + Peers.",
             err_label="Burggraben", is_question=True),
    Category("balance", "3 Bilanz", render_balance_tab,
             question="Ist die Bilanz solide?",
             desc="Current/Quick Ratio, Net Debt, Debt/Equity, "
                  "Eigenkapitalquote, Goodwill-Anteil + Trend.",
             err_label="Bilanz", is_question=True),
    Category("management", "4 Management", render_management_tab,
             question="Ist das Management gut?",
             desc="Tenure, Ownership-Struktur, Turnover, Insider-Conviction, "
                  "Kapitalallokation, SBC/Verwaesserung.",
             err_label="Management", is_question=True),
    Category("earnings_real", "5 Gewinne echt", render_earnings_real_tab,
             question="Sind die Gewinne echt?",
             desc="Earnings-Quality-Score, GAAP vs non-GAAP, Owner Earnings "
                  "vs Nettogewinn vs FCF.",
             err_label="Gewinnqualitaet", is_question=True),
    Category("valuation", "6 Bewertung", render_valuation_tab,
             question="Ist die Bewertung attraktiv?",
             desc="EV, EV/FCF, Earnings Yield (EBIT/EV + klassisch), KGV, "
                  "Kurs.",
             err_label="Bewertung", is_question=True),
    Category("portfolio", "Portfolio & Signale", render_portfolio,
             err_label="Portfolio"),
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
_c1, _c2, _c3, _c4 = st.columns([1, 3, 1, 1])
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
PERIOD = "quarterly" if _c3.radio(
    "Darstellung", ["Jahr (10-K)", "Quartal (10-Q)"], horizontal=False,
    key="ana_period") == "Quartal (10-Q)" else "annual"
_XHOVER = "%b %Y" if PERIOD == "quarterly" else "%Y"
_unit = "Quartale" if PERIOD == "quarterly" else "Jahre"
N_YEARS = int(_c4.number_input(f"Anzahl ({_unit})", min_value=2,
                               max_value=40 if PERIOD == "quarterly" else 15,
                               value=8 if PERIOD == "quarterly" else 5,
                               step=1, key=f"ana_n_{PERIOD}"))

if not ticker:
    st.info("Ticker waehlen oder eingeben.")
    st.stop()

src = _resolve(ticker)
_badge = "🟢 DB" if src.income_source == "db" else "🟡 on-Demand"
st.markdown(
    f"### {src.ticker}{(' — ' + src.name) if src.name else ''}  \n"
    f"Datenquelle: **{_badge}**"
    f" · Darstellung: **{'Quartal (10-Q)' if PERIOD == 'quarterly' else 'Jahr (10-K)'}**"
    f"{'  · im Universum' if src.in_universe else ''}")
if PERIOD == "quarterly":
    st.caption("Quartalsmodus: GuV/Marge/EPS sind diskrete Quartalswerte, "
               "Bilanz sind Stichtagswerte. Cashflow-Posten (FCF, CFO, SBC) "
               "im 10-Q sind i.d.R. Year-to-Date — Renditen/FCF-Trends daher "
               "im Jahresmodus belastbarer.")

# ---- Tabs aus der Metadaten-Registry generieren ----
# (Verhaltensgleich zum bisherigen Aufbau; spaetere Schritte ersetzen
#  st.tabs durch einen Navigator und machen die Inhalte lazy.)
_tabs = st.tabs([c.title for c in CATEGORIES])
for _tab, _cat in zip(_tabs, CATEGORIES):
    with _tab:
        if _cat.question:
            st.markdown(f"#### {_cat.question}")
        try:
            _cat.render(ticker, src)
        except Exception as e:  # noqa: BLE001
            st.warning(f"{_cat.err_label} nicht ladbar: "
                       f"{e.__class__.__name__}: {e}")
