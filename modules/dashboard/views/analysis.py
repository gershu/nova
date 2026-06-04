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

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from modules.dashboard import company_data as cd
from modules.dashboard import finmetrics as fm
from modules.dashboard import market as mkt
from modules.dashboard import scoring as sc
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
def _company_info(ticker: str):
    return mkt.company_info(ticker)


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

    # --- Verlauf ---
    df = pd.DataFrame([{
        "period_end": pd.to_datetime(d["period_end"]),
        "ppe": d.get("ppe_gross"),
        "capex": _abs_or(d.get("capex")),
        "employees": d.get("employees"),
        "rev_emp": fm.safe_div(d.get("revenue"), d.get("employees")),
    } for d in rows])
    st.markdown("#### Verlauf")
    t1, t2 = st.columns(2)
    f1 = go.Figure()
    f1.add_trace(go.Bar(name="PP&E", x=df["period_end"], y=df["ppe"],
                        marker_color="#0F6E56"))
    f1.add_trace(go.Bar(name="CapEx", x=df["period_end"], y=df["capex"],
                        marker_color="#1D9E75"))
    f1.update_layout(barmode="group", height=300,
                     margin=dict(l=10, r=10, t=30, b=10),
                     title=f"PP&E & CapEx ({cur})", yaxis_title=cur,
                     legend=dict(orientation="h", y=-0.2))
    t1.plotly_chart(f1, use_container_width=True)
    f2 = go.Figure(go.Bar(x=df["period_end"], y=df["employees"],
                          marker_color="#B4862B"))
    f2.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                     title="Mitarbeiter", yaxis_title="Anzahl")
    t2.plotly_chart(f2, use_container_width=True)
    if df["rev_emp"].notna().any():
        f3 = go.Figure(go.Scatter(
            x=df["period_end"], y=df["rev_emp"], mode="lines+markers",
            line=dict(color="#444441", width=2), connectgaps=False,
            hovertemplate="%{x|" + _XHOVER + "}<br>%{y:,.0f}<extra></extra>"))
        f3.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title=f"Umsatz / Mitarbeiter ({cur})",
                         yaxis_title=cur)
        st.plotly_chart(f3, use_container_width=True)
    if emp_last is None:
        st.caption("Mitarbeiterzahl nicht gefunden (dei:EntityNumberOfEmployees "
                   "fehlt, auch 10-K-Textextraktion ohne Treffer) — Komponente "
                   "ausgeklammert, Gewichte renormiert.")

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



def _series_at(m: dict, period_iso: str, tol_days: int = 45):
    """Wert aus {iso_date: value} am naechsten zu period_iso (<= tol_days).

    Fuer Zeitreihen (PP&E/Mitarbeiter via company-concept, Jahresend-Kurse),
    deren Stichtage nicht exakt auf das Bilanzdatum fallen. None, wenn nichts
    innerhalb der Toleranz liegt.
    """
    if not m:
        return None
    try:
        target = date.fromisoformat(str(period_iso)[:10])
    except (TypeError, ValueError):
        return None
    best_v = best_d = None
    for k, v in m.items():
        try:
            dk = date.fromisoformat(str(k)[:10])
        except (TypeError, ValueError):
            continue
        dd = abs((dk - target).days)
        if dd <= tol_days and (best_d is None or dd < best_d):
            best_d, best_v = dd, v
    return best_v

_STATUS_BADGE = {"stable": "", "beta": "  ·  🟡 beta", "todo": "  ·  🔴 todo"}


def _rep_label(title: str, status: str = "stable") -> str:
    return f"{title}{_STATUS_BADGE.get(status, '')}"


def _lazy_report(title: str, key: str, render_fn, *args,
                 status: str = "beta", expanded: bool = False) -> None:
    """Bericht als aufklappbarer Bereich; teure Daten erst auf Knopfdruck.

    Streamlit fuehrt Expander-Inhalte bei jedem Rerun aus (auch zugeklappt),
    daher wird das Laden ueber Button + session_state gated — die API-Calls
    feuern erst, wenn der Bericht wirklich angefordert wurde.
    """
    with st.expander(_rep_label(title, status), expanded=expanded):
        sk = f"lazy_{key}"
        if st.session_state.get(sk) or st.button("Bericht laden",
                                                 key=f"btn_{sk}"):
            st.session_state[sk] = True
            try:
                render_fn(*args)
            except Exception as e:  # noqa: BLE001
                st.warning(f"{title} nicht ladbar: "
                           f"{e.__class__.__name__}: {e}")
        else:
            st.caption("On-Demand — Button klicken (laedt mehrere API-Calls).")


def _render_re_eps_ev(ticker: str, src) -> None:
    """RE / EPS / Equity / FCF / EV — Verlauf (lazy, mehrere API-Calls)."""
    cur = src.currency or "USD"
    eh = _earnings_hist(ticker, N_YEARS, PERIOD)
    if len(eh) < 2:
        st.caption("Mind. 2 Perioden noetig.")
        return
    splits = _splits(ticker)
    yends = [str(d["period_end"])[:10] for d in eh]
    start = (pd.to_datetime(min(yends)) - pd.Timedelta(days=10)) \
        .date().isoformat()
    px = _prices(ticker, start, date.today().isoformat())

    def _mcap(d):
        sh = d.get("diluted_shares")
        if sh:
            sh *= fm.split_factor(splits, str(d["period_end"])[:10])
        c = _series_at(px, str(d["period_end"])[:10], tol_days=15)
        return (c * sh) if (c and sh) else None

    def _adj(d, key):
        v = d.get(key)
        return (v / fm.split_factor(splits, str(d["period_end"])[:10])
                if v is not None else None)

    def _row(d):
        m = _mcap(d)
        ev = (m + (d.get("net_debt") or 0.0)) if m is not None else None
        return {
            "pe": pd.to_datetime(d["period_end"]),
            "retained": d.get("retained_earnings"),
            "equity": d.get("equity"), "fcf": d.get("fcf"),
            "eps_b": _adj(d, "eps_basic"), "eps_d": _adj(d, "eps_diluted"),
            "ev": ev,
            "eyield": fm.safe_div(d.get("operating_income"), ev),
            "classic_ey": fm.safe_div(d.get("net_income"), m),
        }

    edf = pd.DataFrame([_row(d) for d in eh])

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

    # --- Earnings Yield (%) ---
    ey = pd.to_numeric(edf["eyield"], errors="coerce")
    cey = pd.to_numeric(edf["classic_ey"], errors="coerce")
    if ey.notna().any() or cey.notna().any():
        f6 = go.Figure()
        f6.add_trace(go.Scatter(x=edf["pe"], y=ey * 100.0, name="EBIT/EV",
                                mode="lines+markers",
                                line=dict(color="#B4862B", width=2),
                                connectgaps=False))
        f6.add_trace(go.Scatter(x=edf["pe"], y=cey * 100.0,
                                name="NI/MCap (klassisch)",
                                mode="lines+markers",
                                line=dict(color="#0F6E56", width=2),
                                connectgaps=False))
        f6.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                         title="Earnings Yield (%)", yaxis_title="%",
                         legend=dict(orientation="h", y=-0.3),
                         hovermode="x unified")
        st.plotly_chart(f6, use_container_width=True)

    # --- Rohwerte je Jahr (as-reported) ---
    ev_by = {str(d["period_end"])[:10]: v
             for d, v in zip(eh, edf["ev"].tolist())}
    with st.expander("Rohwerte je Jahr"):
        st.dataframe(pd.DataFrame([{
            "Periode": str(d["period_end"])[:10],
            "Gewinnruecklagen": _money(d.get("retained_earnings"), cur),
            "Eigenkapital": _money(d.get("equity"), cur),
            "Free Cash Flow": _money(d.get("fcf"), cur),
            "EV (approx.)": _money(ev_by.get(str(d["period_end"])[:10]), cur),
            "EPS unverw.": (de_dec(d["eps_basic"], 2)
                            if d.get("eps_basic") is not None else "—"),
            "EPS verw.": (de_dec(d["eps_diluted"], 2)
                          if d.get("eps_diluted") is not None else "—"),
        } for d in eh]), use_container_width=True, hide_index=True)

    st.caption("EPS-Chart und EV split-bereinigt; Earnings Yield: EBIT/EV "
               "(Greenblatt) + NI/Marktkap (klassisch). Rohwerte-Tabelle = "
               "as-reported je Filing; EV-Historie ist eine Naeherung "
               "(Jahresend-Kurs).")


def _render_fcf_alloc(ticker: str, src) -> None:
    """FCF-Verwendung (Kapitalallokation, Verlauf) — lazy."""
    cur = src.currency or "USD"
    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    if len(ym) < 2:
        st.caption("Mind. 2 Perioden noetig.")
        return
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


def _period_rows(ticker: str):
    """(inc, rows) der aktuellen Periodenwahl; rows nach 10-K/10-Q gefiltert,
    Fallback alle Zeilen. Gemeinsame Basis der Geschaeft-Reports."""
    inc = _income(ticker, N_YEARS, PERIOD)
    allrows = inc.get("rows") or []
    rows = [r for r in allrows
            if (r.get("form_type") or "").upper().startswith(
                "10-Q" if PERIOD == "quarterly" else "10-K")] or allrows
    return inc, rows


def _render_biz_growth_returns(ticker: str, src) -> None:
    """Geschaeft-Report: Umsatz-CAGR + ROIC/ROCE/ROE/ROA-Trendampel + FCF."""
    inc, rows = _period_rows(ticker)
    if not rows:
        st.info("Keine GuV-Daten verfuegbar.")
        return
    rev_pts = [(pd.to_datetime(r["period_end"]), r["revenue"]) for r in rows
               if r.get("revenue") is not None]
    rev_cagr = None
    if len(rev_pts) >= 2:
        yrs = (rev_pts[-1][0] - rev_pts[0][0]).days / 365.25
        rev_cagr = fm.cagr(rev_pts[0][1], rev_pts[-1][1], yrs)

    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    rets = [fm.returns_from_metrics(d) for d in ym]
    fcf_pts = [(pd.to_datetime(d["period_end"]),
                fm.safe_div(d.get("fcf"), d.get("revenue"))) for d in ym]
    fcf_margin_last = fcf_pts[-1][1] if fcf_pts else None

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


def _render_biz_roc(ticker: str, src) -> None:
    """Geschaeft-Report (aus Ad-Hoc): Return on Capital — Verdict, ROIC/ROCE/
    ROE/ROA-Trend-Chart, Komponenten (NOPAT / Inv. Kapital / eff. Steuer)."""
    cur = src.currency or "USD"
    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    if not ym:
        st.info("Keine Jahresdaten verfuegbar.")
        return
    rets = [fm.returns_from_metrics(d) for d in ym]
    rl = rets[-1]

    checks = sc.checks_returns(rets,
                               _SCORE["thresholds"]["return_on_capital"])
    if checks:
        passed = sum(1 for _, ok in checks if ok)
        r = passed / len(checks)
        box = st.success if r >= 0.75 else st.info if r >= 0.5 else st.warning
        verdict = ("hochwertig" if r >= 0.75 else "durchschnittlich"
                   if r >= 0.5 else "kapitalineffizient")
        lines = "  \n".join(f"{'✅' if ok else '❌'} {n}" for n, ok in checks)
        box(f"Kapitalrendite wirkt **{verdict}** — {passed}/{len(checks)}  \n"
            f"{lines}")

    if len(ym) >= 2:
        df = pd.DataFrame([
            dict(pe=pd.to_datetime(d["period_end"]),
                 **{k: rr[k] for k in ("roic", "roce", "roe", "roa")})
            for d, rr in zip(ym, rets)])
        fig = go.Figure()
        for name, col, color in [("ROIC", "roic", "#0F6E56"),
                                 ("ROCE", "roce", "#1D9E75"),
                                 ("ROE", "roe", "#5DCAA5"),
                                 ("ROA", "roa", "#B4862B")]:
            fig.add_trace(go.Scatter(
                x=df["pe"], y=pd.to_numeric(df[col], errors="coerce") * 100.0,
                mode="lines+markers", name=name,
                line=dict(color=color, width=2), connectgaps=False,
                hovertemplate=f"%{{x|{_XHOVER}}}<br>{name}: "
                              "%{y:.1f}%<extra></extra>"))
        fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title="%", legend=dict(orientation="h",
                                                       y=-0.2),
                          hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

    last = ym[-1]
    with st.expander("Komponenten (letzte Periode)"):
        st.dataframe(pd.DataFrame([
            {"Posten": "NOPAT", "Wert": _money(rl.get("nopat"), cur)},
            {"Posten": "Investiertes Kapital",
             "Wert": _money(rl.get("inv_cap"), cur)},
            {"Posten": "Eff. Steuersatz", "Wert": _pct(rl.get("eff_tax"))},
            {"Posten": "Operatives Ergebnis",
             "Wert": _money(last.get("operating_income"), cur)},
            {"Posten": "Nettogewinn", "Wert": _money(last.get("net_income"),
                                                     cur)},
            {"Posten": "Eigenkapital", "Wert": _money(last.get("equity"),
                                                      cur)},
            {"Posten": "Bilanzsumme", "Wert": _money(last.get("total_assets"),
                                                     cur)},
        ]), use_container_width=True, hide_index=True)
    st.caption("ROIC = NOPAT/(Schulden+EK−Cash); ROCE = op. Ergebnis/"
               "(Bilanzsumme−kurzfr. Verbindl.); Stichtags-Bilanzwerte, "
               "eff. Steuersatz mit Fallback 21 %.")


def _render_biz_margins(ticker: str, src) -> None:
    """Geschaeft-Report: Brutto-/Operative/Nettomarge im Zeitverlauf."""
    _, rows = _period_rows(ticker)
    msr = fm.margin_series(rows)
    df = pd.DataFrame([{
        "period_end": pd.to_datetime(x["period_end"]),
        "Bruttomarge": (x["gross"] * 100 if x["gross"] is not None else None),
        "Operative Marge": (x["operating"] * 100
                            if x["operating"] is not None else None),
        "Nettomarge": (x["net"] * 100 if x["net"] is not None else None),
    } for x in msr])
    if len(df) < 2:
        st.caption("Mind. 2 Perioden noetig.")
        return
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
                      yaxis_title="%", legend=dict(orientation="h", y=-0.2),
                      hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)


def _render_biz_revenue(ticker: str, src) -> None:
    """Geschaeft-Report: Umsatzverlauf (Balken)."""
    cur = src.currency or "USD"
    _, rows = _period_rows(ticker)
    rdf = pd.DataFrame([{"period_end": pd.to_datetime(r["period_end"]),
                         "revenue": r.get("revenue")} for r in rows])
    if len(rdf) < 2:
        st.caption("Mind. 2 Perioden noetig.")
        return
    fig2 = go.Figure(go.Bar(x=rdf["period_end"], y=rdf["revenue"],
                            marker_color="#0F6E56",
                            hovertemplate="%{x|" + _XHOVER + "}<br>%{y:,.0f}"
                                          "<extra></extra>"))
    fig2.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                       title=f"Umsatz ({cur})", yaxis_title=cur)
    st.plotly_chart(fig2, use_container_width=True)


def _report_physical(ticker: str, src) -> None:
    """Adapter: Physical Growth nimmt (ticker, cur) -> einheitliche Signatur."""
    _render_physical(ticker, src.currency or "USD")


@st.cache_data(ttl=3600, show_spinner=False)
def _segments(ticker: str):
    return cd.revenue_segments(ticker)


_GUV_PAL = ["#0F6E56", "#1D9E75", "#5DCAA5", "#9FE1CB",
            "#3B6D11", "#639922", "#97C459", "#C0DD97"]


def _render_guv_umsatz(ticker: str, src) -> None:
    """Umsatz & GuV — Wachstum + Segment-Stacked-Bar (Achsenwahl)."""
    cur = src.currency or "USD"
    _, rows = _period_rows(ticker)
    if not rows:
        st.info("Keine GuV-Daten verfuegbar.")
        return
    hist = pd.DataFrame(rows)
    hist["period_end"] = pd.to_datetime(hist["period_end"])
    hist = hist.sort_values("period_end")
    is_q = PERIOD == "quarterly"
    lag = 4 if is_q else 1

    seg_hist = _guv_segments(ticker, set(hist["period_end"]))
    seg_axis = _guv_axis_picker(seg_hist, ticker)

    # --- Wachstums-Kennzahlen ---
    rser = hist[hist["revenue"].notna()].sort_values("period_end")
    rv = rser["revenue"].astype(float).tolist()
    rp = rser["period_end"].tolist()

    def _grow(c, b):
        return (c / b - 1.0) if (c is not None and b not in (None, 0)) else None

    last = rv[-1] if rv else None
    seq = _grow(last, rv[-2]) if len(rv) >= 2 else None
    yoy = _grow(last, rv[-1 - lag]) if len(rv) > lag else None
    cagr = None
    if len(rv) >= 2 and rv[0] > 0 and last and last > 0:
        yrs = (rp[-1] - rp[0]).days / 365.25
        if yrs >= 1.0:
            cagr = (last / rv[0]) ** (1.0 / yrs) - 1.0

    mc = st.columns(3)
    mc[0].metric("Umsatz (letzte Periode)",
                 _money(last, cur) if last is not None else "—",
                 delta=(f"{'+' if seq >= 0 else ''}{_pct(seq)} ggü. Vorquartal"
                        if (is_q and seq is not None) else None))
    mc[1].metric("Wachstum YoY", _pct(yoy) if yoy is not None else "—")
    mc[2].metric("CAGR p.a.", _pct(cagr) if cagr is not None else "—")

    # --- Umsatz-Verlauf (Segment-Stacked-Bar oder einfacher Balken) ---
    if seg_axis is not None:
        seg_sel = seg_hist[seg_hist["axis"] == seg_axis].copy()
        periods = sorted(seg_sel["period_end"].unique())
        if periods:
            mem_tot = (seg_sel.groupby("member_label")["value"].sum()
                       .sort_values(ascending=False))
            members = mem_tot.index.tolist()
            pivot = (seg_sel.pivot_table(index="period_end",
                                         columns="member_label",
                                         values="value", aggfunc="first")
                     .reindex(periods)[members])
            rev_by_p = hist.set_index("period_end")["revenue"].reindex(periods)
            other = rev_by_p - pivot.sum(axis=1)
            fig = go.Figure()
            for i, m in enumerate(members):
                fig.add_trace(go.Bar(
                    name=m, x=pivot.index, y=pivot[m],
                    marker_color=_GUV_PAL[i % len(_GUV_PAL)],
                    hovertemplate=f"%{{x|%Y-%m-%d}}<br>{m}: "
                                  "%{y:,.0f}<extra></extra>"))
            if other.notna().any() and (other.fillna(0) > 1).any():
                fig.add_trace(go.Bar(
                    name="Sonstige", x=other.index, y=other.values,
                    marker_color="#B4B2A9",
                    hovertemplate="%{x|%Y-%m-%d}<br>Sonstige: "
                                  "%{y:,.0f}<extra></extra>"))
            fig.update_layout(barmode="stack", height=380,
                              margin=dict(l=10, r=10, t=10, b=10),
                              legend=dict(orientation="h", y=-0.18),
                              yaxis_title=f"Umsatz ({cur})",
                              hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Keine Segment-Daten in dieser Achse.")
    else:
        fig = go.Figure(go.Bar(
            x=hist["period_end"], y=hist["revenue"], marker_color=_GUV_PAL[0],
            hovertemplate="%{x|%Y-%m-%d}<br>Umsatz: %{y:,.0f}<extra></extra>"))
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title=f"Umsatz ({cur})")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Noch keine Segment-Aufschluesselung verfuegbar.")


def _render_guv_margen(ticker: str, src) -> None:
    """Umsatz & GuV — Margen-Trend (inkl. F&E- und effektiver Steuerquote)."""
    _, rows = _period_rows(ticker)
    mt = pd.DataFrame(rows)
    if mt.empty:
        st.caption("Keine GuV-Daten verfuegbar.")
        return
    mt["period_end"] = pd.to_datetime(mt["period_end"])
    mt = mt[mt["revenue"].notna()].sort_values("period_end")
    if len(mt) < 2:
        st.caption("Mind. 2 Perioden noetig.")
        return
    rev_s = mt["revenue"].astype(float)

    def _mg(col):
        s = mt[col].astype(float) / rev_s * 100.0
        s[rev_s <= 0] = float("nan")
        return s

    ptx = mt["pretax_income"].astype(float)
    eff_tax = mt["tax_expense"].astype(float) / ptx * 100.0
    eff_tax[ptx <= 0] = float("nan")
    fig = go.Figure()
    for name, ser, col in [
            ("Bruttomarge", _mg("gross_profit"), "#0F6E56"),
            ("Operative Marge", _mg("operating_income"), "#1D9E75"),
            ("Nettomarge", _mg("net_income"), "#5DCAA5"),
            ("F&E-Quote", _mg("rd_expense"), "#A32D2D"),
            ("Steuerquote", eff_tax, "#B4862B")]:
        fig.add_trace(go.Scatter(
            x=mt["period_end"], y=ser, name=name, mode="lines+markers",
            line=dict(color=col, width=2), connectgaps=False,
            hovertemplate=f"%{{x|%Y-%m-%d}}<br>{name}: "
                          "%{y:.1f}%<extra></extra>"))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", y=-0.2), yaxis_title="%",
                      hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Margen = Anteil am Umsatz; Steuerquote = Steuern / "
               "Vorsteuerergebnis (effektiv).")


def _render_guv_kosten(ticker: str, src) -> None:
    """Umsatz & GuV — absolute Kostenaufteilung (Stacked Bar)."""
    cur = src.currency or "USD"
    _, rows = _period_rows(ticker)
    kt = pd.DataFrame(rows)
    if kt.empty:
        st.caption("Keine GuV-Daten verfuegbar.")
        return
    kt["period_end"] = pd.to_datetime(kt["period_end"])
    kt = kt.sort_values("period_end")
    cogs = kt["cost_of_revenue"].astype(float)
    rd = kt["rd_expense"].astype(float)
    sga = kt["sga_expense"].astype(float)
    opex = kt["operating_expense"].astype(float)
    opinc = kt["operating_income"].astype(float)
    rest = (opex - rd.fillna(0) - sga.fillna(0)).clip(lower=0)
    bands = [("Herstellkosten", cogs, "#A32D2D"), ("F&E", rd, "#C75B5B"),
             ("Vertrieb & Verwaltung", sga, "#E08A8A"),
             ("Uebriger Betriebsaufwand", rest, "#B4B2A9"),
             ("Operatives Ergebnis", opinc, "#1D9E75")]
    if not any(s.notna().any() and (s.fillna(0) != 0).any()
               for _, s, _c in bands):
        st.caption("Keine Kostenpositionen verfuegbar.")
        return
    fig = go.Figure()
    for name, ser, col in bands:
        fig.add_trace(go.Bar(
            name=name, x=kt["period_end"], y=ser, marker_color=col,
            hovertemplate=f"%{{x|%Y-%m-%d}}<br>{name}: "
                          "%{y:,.0f}<extra></extra>"))
    fig.update_layout(barmode="stack", height=360,
                      margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", y=-0.2),
                      yaxis_title=f"Aufwand / Ergebnis ({cur})",
                      hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Umsatz = Herstellkosten + Betriebsaufwand + Operatives "
               f"Ergebnis. Bandhoehe in {cur}; Summe je Balken = Umsatz.")


def _render_guv_sankey(ticker: str, src) -> None:
    """Umsatz & GuV — GuV-Struktur (Sankey) einer waehlbaren Periode."""
    cur = src.currency or "USD"
    _, rows = _period_rows(ticker)
    if not rows:
        st.info("Keine GuV-Daten verfuegbar.")
        return
    hist = pd.DataFrame(rows)
    hist["period_end"] = pd.to_datetime(hist["period_end"])
    hist = hist.sort_values("period_end")
    seg_hist = _guv_segments(ticker, set(hist["period_end"]))
    seg_axis = _guv_axis_picker(seg_hist, ticker, key_suffix="_sankey")

    per_opts = list(hist["period_end"])

    def _per_lbl(ts):
        row = hist[hist["period_end"] == ts].iloc[0]
        return f"{str(ts)[:10]} · {row.get('form_type') or '—'}"

    sel_per = st.selectbox("Periode (Sankey)", per_opts,
                           index=len(per_opts) - 1, format_func=_per_lbl,
                           key=f"guv_sankey_per_{ticker}")
    r = hist[hist["period_end"] == sel_per].iloc[0]
    seg_curr = (seg_hist[seg_hist["period_end"] == sel_per]
                if not seg_hist.empty else pd.DataFrame())
    st.markdown(f"##### GuV-Struktur — {str(sel_per)[:10]}")

    seg_rows = []
    if seg_axis is not None and not seg_curr.empty:
        cur_ax = seg_curr[seg_curr["axis"] == seg_axis]
        seg_rows = [(rr["member_label"] or rr["member"], float(rr["value"]))
                    for _, rr in cur_ax.iterrows()]

    def _g(col):
        v = r[col]
        return None if _missing(v) else float(v)

    rev, cogs, gross = _g("revenue"), _g("cost_of_revenue"), _g("gross_profit")
    rd, sga, opex = _g("rd_expense"), _g("sga_expense"), _g("operating_expense")
    opinc, other = _g("operating_income"), _g("other_income")
    ptax, tax, net = _g("pretax_income"), _g("tax_expense"), _g("net_income")

    GREEN, RED, GRAY = "#3B6D11", "#A32D2D", "#444441"
    GL, RL, SL = "rgba(99,153,34,0.45)", "rgba(225,75,74,0.40)", \
        "rgba(29,158,117,0.45)"
    labels, colors, idx = [], [], {}

    def _node(key, name, val, color, *, force=False):
        if val is None and not force:
            return
        idx[key] = len(labels)
        labels.append(f"{name}<br>{_money(val, cur)}")
        colors.append(color)

    _node("rev", "Umsatz", rev, GRAY, force=True)
    for i, (lbl, val) in enumerate(seg_rows):
        _node(f"s{i}", lbl, val, "#0F6E56")
    _node("cogs", "Herstellkosten", cogs, RED)
    _node("gross", "Bruttogewinn", gross, GREEN)
    _node("opex", "Betriebsaufwand", opex, RED)
    _node("rd", "F&E", rd, RED)
    _node("sga", "Vertrieb & Verwaltung", sga, RED)
    _node("opinc", "Operatives Ergebnis", opinc, GREEN)
    has_other = other is not None and other > 0
    if has_other:
        _node("other", "Sonstiges Ergebnis", other, GREEN)
    has_ptax = ptax is not None
    if has_ptax:
        _node("ptax", "Vorsteuerergebnis", ptax, GREEN)
    _node("tax", "Steuern", tax, RED)
    _node("net", "Nettogewinn", net, GREEN, force=True)

    S, T, V, LC = [], [], [], []

    def _link(a, b, val, color):
        if val is None or val <= 0 or a not in idx or b not in idx:
            return
        S.append(idx[a]); T.append(idx[b]); V.append(val); LC.append(color)

    for i, (_, val) in enumerate(seg_rows):
        _link(f"s{i}", "rev", val, SL)
    _link("rev", "cogs", cogs, RL)
    _link("rev", "gross", gross, GL)
    _link("gross", "opex", opex, RL)
    _link("gross", "opinc", opinc, GL)
    _link("opex", "rd", rd, RL)
    _link("opex", "sga", sga, RL)
    if has_ptax:
        _link("opinc", "ptax", opinc, GL)
        if has_other:
            _link("other", "ptax", other, GL)
        _link("ptax", "tax", tax, RL)
        _link("ptax", "net", net, GL)
    else:
        _link("opinc", "tax", tax, RL)
        _link("opinc", "net", net, GL)

    if not V:
        st.info("GuV-Zeilen unvollstaendig — kein Sankey moeglich.")
        return
    h = min(900, max(440, len(labels) * 46))
    fig = go.Figure(go.Sankey(
        arrangement="snap",
        textfont=dict(color="#10231A", size=13,
                      family="Arial, sans-serif"),
        node=dict(label=labels, color=colors, pad=26, thickness=16,
                  line=dict(color="white", width=1)),
        link=dict(source=S, target=T, value=V, color=LC)))
    fig.update_layout(height=h, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)

    def _ratio_of(num, den):
        return (float(num) / float(den)
                if (num is not None and den not in (None, 0)
                    and not _missing(num) and not _missing(den)) else None)

    tax_base = ptax if ptax else ((net + tax) if (net is not None
                                                  and tax is not None) else None)
    st.markdown("**Ergebnisqualitaet**")
    qc = st.columns(5)
    qc[0].metric("Bruttomarge", _pct(_ratio_of(gross, rev)))
    qc[1].metric("Operative Marge", _pct(_ratio_of(opinc, rev)))
    qc[2].metric("Nettomarge", _pct(_ratio_of(net, rev)))
    qc[3].metric("F&E-Quote", _pct(_ratio_of(rd, rev)))
    qc[4].metric("Steuerquote", _pct(_ratio_of(tax, tax_base)))
    st.caption(f"{r.get('form_type') or 'Filing'} · Periode "
               f"{str(sel_per)[:10]} · Betraege wie berichtet ({cur}); "
               "Bandbreite proportional zum Betrag.")


def _guv_segments(ticker: str, valid_periods: set) -> pd.DataFrame:
    """Segment-Historie (source-agnostisch), gefiltert auf die Perioden der
    aktuellen Darstellung (PERIOD)."""
    seg = _segments(ticker)
    df = pd.DataFrame(seg.get("rows") or [])
    if df.empty:
        return df
    df["period_end"] = pd.to_datetime(df["period_end"])
    return df[df["period_end"].isin(valid_periods)]


def _guv_axis_picker(seg_hist: pd.DataFrame, ticker: str, key_suffix=""):
    """Achsen-Auswahl (radio) fuer die Umsatz-Aufschluesselung; None ohne
    Segmente."""
    if seg_hist.empty:
        return None
    try:
        from modules.sec_filings.client import (AXIS_LABELS as _AX_LBL,
                                                _humanize as _hum)
    except Exception:  # noqa: BLE001
        _AX_LBL, _hum = {}, (lambda s: s)
    axes = list(dict.fromkeys(seg_hist["axis"].tolist()))
    known = list(_AX_LBL.keys())
    axes.sort(key=lambda a: (known.index(a) if a in known else 99, a))
    opts = {_AX_LBL.get(a, _hum(a)): a for a in axes}
    if len(opts) > 1:
        chosen = st.radio("Umsatz-Aufschluesselung", list(opts.keys()),
                          horizontal=True,
                          key=f"guv_axis{key_suffix}_{ticker}")
    else:
        chosen = list(opts.keys())[0]
    return opts[chosen]


def _render_bal_snapshot(ticker: str, src) -> None:
    """Bilanz-Report: Soliditaets-Check + Kennzahlen (juengster Stichtag)."""
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

    # Verdict via zentrale Kriterien (gemeinsam mit Gesamt-Score)
    checks = sc.checks_balance(bs, _SCORE["thresholds"]["balance_sheet"])
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


def _render_bal_trend(ticker: str, src) -> None:
    """Bilanz-Report: Verlauf Current Ratio / Debt-Equity / Net Debt."""
    cur = src.currency or "USD"
    hist = _balance_hist(ticker, N_YEARS, PERIOD)
    if len(hist) < 2:
        st.caption("Mind. 2 Perioden noetig.")
        return
    bdf = pd.DataFrame([{
        "period_end": pd.to_datetime(b.period_end),
        "current_ratio": fm.safe_div(b.assets_current,
                                     b.liabilities_current),
        "debt_to_equity": fm.safe_div(b.total_debt, b.equity),
        "net_debt": b.net_debt,
    } for b in hist])
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


def _render_val_metrics(ticker: str, src) -> None:
    """Bewertungs-Report: EV, EV/FCF, KGV, Earnings Yields, Marktkap."""
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

    st.caption("Bewertung mit aktuellem Marktpreis (yfinance) und letzter "
               "Jahres-GuV/-Bilanz. EV/FCF & Yields wie im Earnings-Modul.")


def _render_moat_score(ticker: str, src) -> None:
    """Burggraben-Report: gewichteter Moat-Score aus 6 Signalen."""
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


def _render_eq_score(ticker: str, src) -> None:
    """Gewinne-echt-Report: Earnings-Quality-Score (SBC + Add-backs)."""
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
    st.caption("SBC quantitativ (SBC/operativer CF); Akquisitions-/"
               "Restrukturierungs-/Rechtsstreit-/Steuer-/Einmal-Add-backs "
               "aus dem Earnings-Exhibit.")


def _render_eq_gaap(ticker: str, src) -> None:
    """Gewinne-echt-Report: GAAP vs non-GAAP — Earnings-Exhibit (aus Ad-Hoc).

    Heuristische Textanalyse des juengsten Earnings-8-K (Item 2.02, Exhibit
    99); non-GAAP-Kennzahlen sind nicht im XBRL strukturiert.
    """
    ng = _nongaap(ticker)
    if ng.get("categories") is None and ng.get("mentions") is None:
        st.info(f"Kein Earnings-8-K (Item 2.02) mit Exhibit 99 fuer "
                f"{src.ticker} gefunden ({ng.get('error') or '—'}).")
        return
    if ng.get("filed_at"):
        st.caption(f"Quelle: Earnings-8-K, eingereicht "
                   f"**{str(ng['filed_at'])[:10]}**.")

    cats = ng.get("categories") or {}
    mentions = ng.get("mentions") or 0
    n_cat = len(cats)
    chk = sc.checks_gaap(mentions, ng.get("adds_back_sbc"), n_cat,
                         _SCORE["thresholds"]["gaap_vs_non_gaap"])
    aggressive = not all(ok for _, ok in chk)
    if not cats and mentions == 0:
        st.success("Reporting wirkt **konservativ / transparent** — keine "
                   "non-GAAP-Add-backs erkannt.")
    elif aggressive:
        st.warning("Reporting wirkt **aggressiv** — viele Add-backs bzw. SBC "
                   "herausgerechnet.")
    else:
        st.info("Reporting wirkt **moderat**.")

    m = st.columns(3)
    m[0].metric("Non-GAAP-Erwaehnungen", str(mentions))
    m[1].metric("Anpassungs-Kategorien", str(n_cat))
    m[2].metric("SBC herausgerechnet?",
                "Ja" if ng.get("adds_back_sbc") else "Nein",
                help="Add-back von Aktienverguetung ist der klassische "
                     "Aggressivitaets-Marker (echte, wiederkehrende Kosten).")

    if cats:
        st.markdown("**Gefundene Anpassungs-Kategorien**")
        cat_df = (pd.DataFrame([{"Kategorie": k, "Treffer": v}
                                for k, v in cats.items()])
                  .sort_values("Treffer", ascending=False))
        fig = go.Figure(go.Bar(
            x=cat_df["Treffer"], y=cat_df["Kategorie"], orientation="h",
            marker_color=["#A32D2D" if k.startswith("Aktienverguetung")
                          else "#1D9E75" for k in cat_df["Kategorie"]]))
        fig.update_layout(height=max(220, 40 * len(cat_df)),
                          margin=dict(l=10, r=10, t=10, b=10),
                          xaxis_title="Erwaehnungen")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Keine bekannten Anpassungs-Kategorien erkannt — entweder "
                "rein GAAP berichtet oder ungewohnte Formulierung.")

    if ng.get("amounts"):
        with st.expander("Betraege nahe 'non-GAAP' (heuristisch, ungeprueft)"):
            st.write(", ".join(ng["amounts"]))
            st.caption("Reine Textnaehe-Suche — keine Zuordnung zu GAAP/"
                       "non-GAAP-Zeilen. Nur grobe Orientierung.")
    if ng.get("link"):
        st.caption(f"Original-Filing: {ng['link']}")


def _render_owner_earnings(ticker: str, src) -> None:
    """Gewinne-echt-Report: Owner Earnings vs Nettogewinn vs FCF."""
    cur = src.currency or "USD"
    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    if not ym:
        st.caption("Keine Jahresdaten verfuegbar.")
        return
    oe_series, method = fm.owner_earnings(ym)
    last = oe_series[-1]
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
    st.caption("Owner Earnings = Cash-Realitaet vs. ausgewiesener Gewinn.")


def _render_sbc_full(ticker: str, src) -> None:
    """SBC-Report (aus Ad-Hoc): Verdict + Kennzahlen + Trend + Rohwerte.

    SBC aus dem Cashflow-Statement; Verwaesserung = CAGR der (split-
    bereinigten) verwaesserten Aktien.
    """
    cur = src.currency or "USD"
    rows = _sbc_hist(ticker, N_YEARS, PERIOD)
    if not rows:
        st.info("Keine 10-K mit SBC-/Cashflow-Daten verfuegbar.")
        return
    adj = fm.split_adjust_shares(rows, _splits(ticker))
    last = rows[-1]
    st.caption(f"Letzte Periode {str(last['period_end'])[:10]} "
               f"({last.get('form_type') or '—'}) · {len(rows)} Perioden "
               "geladen.")
    if last.get("sbc") is None:
        st.info("Kein SBC-Tag (ShareBasedCompensation) im juengsten "
                "Cashflow-Statement gefunden — manche Firmen weisen es nur "
                "im Anhang aus.")

    sbc = last.get("sbc")
    sbc_rev = fm.safe_div(sbc, last.get("revenue"))
    sbc_cfo = fm.safe_div(sbc, last.get("cfo"))
    sh = [(d["period_end"], d["diluted_shares"]) for d in adj
          if d.get("diluted_shares")]
    dil = None
    if len(sh) >= 2:
        yrs = (pd.to_datetime(sh[-1][0]) - pd.to_datetime(sh[0][0])).days \
            / 365.25
        dil = fm.cagr(sh[0][1], sh[-1][1], yrs)

    checks = sc.checks_sbc(sbc_rev, sbc_cfo, dil,
                           _SCORE["thresholds"]["stock_based_comp"])
    r = sc.subscore(checks)
    if r is not None:
        passed = sum(1 for _, ok in checks if ok)
        box = st.success if r >= 0.75 else st.info if r >= 0.5 else st.warning
        verdict = ("gering verwaessernd (hohe Qualitaet)" if r >= 0.75
                   else "moderat" if r >= 0.5 else "stark verwaessernd")
        box(f"SBC-Belastung wirkt **{verdict}** — "
            f"{passed}/{len(checks)} Kriterien erfuellt.")

    m = st.columns(4)
    m[0].metric("SBC (letzte Periode)", _money(sbc, cur))
    m[1].metric("SBC / Umsatz", _pct(sbc_rev))
    m[2].metric("SBC / operativer CF", _pct(sbc_cfo))
    m[3].metric("Aktien p.a.", _pct(dil) if dil is not None else "—",
                help="CAGR der verwaesserten Aktien (+ = Verwaesserung, "
                     "− = Rueckkauf; split-bereinigt)")

    if len(adj) >= 2:
        df = pd.DataFrame([{
            "period_end": pd.to_datetime(d["period_end"]),
            "sbc_rev": fm.safe_div(d.get("sbc"), d.get("revenue")),
            "sbc_cfo": fm.safe_div(d.get("sbc"), d.get("cfo")),
            "diluted_shares": d.get("diluted_shares")} for d in adj])
        t1, t2 = st.columns(2)
        f1 = go.Figure()
        f1.add_trace(go.Scatter(
            x=df["period_end"], y=df["sbc_rev"] * 100.0, mode="lines+markers",
            name="SBC / Umsatz", line=dict(color="#A32D2D", width=2),
            connectgaps=False))
        f1.add_trace(go.Scatter(
            x=df["period_end"], y=df["sbc_cfo"] * 100.0, mode="lines+markers",
            name="SBC / operativer CF", line=dict(color="#B4862B", width=2),
            connectgaps=False))
        f1.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title="SBC-Belastung", yaxis_title="%",
                         legend=dict(orientation="h", y=-0.25),
                         hovermode="x unified")
        t1.plotly_chart(f1, use_container_width=True)
        f2 = go.Figure(go.Scatter(
            x=df["period_end"], y=df["diluted_shares"], mode="lines+markers",
            name="Verwaesserte Aktien", line=dict(color="#0F6E56", width=2),
            connectgaps=False))
        f2.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title="Verwaesserte Aktien (split-bereinigt)",
                         yaxis_title="Aktien")
        t2.plotly_chart(f2, use_container_width=True)

    with st.expander("SBC-Rohwerte je Periode"):
        st.dataframe(pd.DataFrame([{
            "Periode": str(d["period_end"])[:10],
            "SBC": _money(d.get("sbc"), cur),
            "Operativer CF": _money(d.get("cfo"), cur),
            "Umsatz": _money(d.get("revenue"), cur),
            "Nettogewinn": _money(d.get("net_income"), cur),
            "Verw. Aktien": (de_dec(d["diluted_shares"], 0)
                             if d.get("diluted_shares") else "—"),
        } for d in rows]), use_container_width=True, hide_index=True)
    st.caption("SBC aus dem Cashflow-Statement (ShareBasedCompensation, "
               "nicht-zahlungswirksamer Zuschlag). Verwaesserung als CAGR der "
               "gewichteten verwaesserten Aktien — split-bereinigt.")


def _render_mgmt_conviction(ticker: str, src) -> None:
    """Management-Report: Insider-Conviction-Signal + CEO/CFO-Tenure +
    Mgmt-Turnover + Kaeufer/Verkaeufer."""
    tx = _insider_tx(ticker)
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
    st.caption("Tenure via fruehestes Insider-Filing (CIK). Conviction "
               "klammert 10b5-1-Routine-Verkaeufe aus.")


def _render_mgmt_insider_tx(ticker: str, src) -> None:
    """Management-Report: Insider Sales / Buys (Form 3/4/5) — Transaktions-
    detail (aus Ad-Hoc): Buy/Sell-Wert, Conviction-Komponenten, Netto-Flow
    je Monat, letzte Markt-Trades."""
    tx = _insider_tx(ticker)
    if not tx:
        st.info("Keine Insider-Filings (Form 3/4/5) verfuegbar.")
        return
    years = N_YEARS if PERIOD == "annual" else max(1, N_YEARS // 4)
    cutoff = (date.today() - timedelta(days=int(years) * 365)).isoformat()
    cur = src.currency or "USD"
    df = pd.DataFrame(tx)
    if "transaction_date" in df.columns:
        df = df[df["transaction_date"] >= cutoff]
    if df.empty or "code" not in df.columns:
        st.info("Keine Transaktionen im Zeitraum.")
        return
    buys, sells = df[df["code"] == "P"], df[df["code"] == "S"]
    buy_val = float(buys["value"].fillna(0).sum()) if "value" in buys else 0.0
    sell_val = float(sells["value"].fillna(0).sum()) if "value" in sells else 0.0
    n_buyers = int(buys["owner"].nunique()) if "owner" in buys else 0
    n_sellers = int(sells["owner"].nunique()) if "owner" in sells else 0

    ic = _SCORE["insider_conviction"]
    cm, pct = ic["cluster_buyers_min"], ic["meaningful_sell_pct"]

    def _flag(d, c):
        return (d[c] if c in d.columns
                else pd.Series([False] * len(d), index=d.index))

    ceo_buys = buys[_flag(buys, "is_ceo") == True] if not buys.empty else buys
    cfo_buys = buys[_flag(buys, "is_cfo") == True] if not buys.empty else buys
    cluster = n_buyers >= cm
    first_buyers = 0
    if not buys.empty and "shares_following" in buys.columns:
        fb = buys[buys["shares_following"].notna()].copy()
        if not fb.empty:
            prior = fb["shares_following"] - fb["shares"].fillna(0)
            fb = fb[prior <= 0.05 * fb["shares_following"].clip(lower=1)]
            first_buyers = int(fb["owner"].nunique())
    routine_sells = meaningful_sells = 0
    if not sells.empty:
        planned = _flag(sells, "planned").fillna(False)
        routine_sells = int(planned.sum())
        if "shares_following" in sells.columns:
            pre = sells["shares"].fillna(0) + sells["shares_following"].fillna(0)
            frac = sells["shares"] / pre.where(pre > 0)
        else:
            frac = pd.Series([None] * len(sells), index=sells.index)
        big = frac.isna() | (frac >= pct)
        meaningful_sells = int((~planned & big).sum())

    st.caption(f"Lookback {years} J · {len(df)} Transaktionen.")
    a = st.columns(3)
    a[0].metric("Insider-Käufe (Wert)", _money(buy_val, cur))
    a[1].metric("Insider-Verkäufe (Wert)", _money(sell_val, cur))
    a[2].metric("Netto", _money(buy_val - sell_val, cur))
    m = st.columns(4)
    m[0].metric("CEO / CFO-Kauf",
                f"{'✓' if len(ceo_buys) else '–'} / "
                f"{'✓' if len(cfo_buys) else '–'}")
    m[1].metric("Cluster-Kauf", f"{n_buyers} Kaeufer" + (" ✓" if cluster
                                                         else ""),
                help=f"≥ {cm} verschiedene Kaeufer = Cluster")
    m[2].metric("Erstkaeufe", str(first_buyers))
    m[3].metric("Bedeutende Verkaeufe", str(meaningful_sells))
    if routine_sells:
        st.caption(f"{routine_sells} Routine-Verkauf/e (10b5-1) "
                   "ausgeklammert.")

    ps = df[df["code"].isin(["P", "S"])].copy()
    if not ps.empty:
        ps["month"] = pd.to_datetime(ps["transaction_date"]).dt.to_period(
            "M").dt.to_timestamp()
        ps["signed"] = ps.apply(
            lambda r: (r["value"] or 0) * (1 if r["code"] == "P" else -1),
            axis=1)
        monthly = ps.groupby("month")["signed"].sum().reset_index()
        st.markdown("**Netto Insider-Flow je Monat (P − S)**")
        colors = ["#1D9E75" if v >= 0 else "#A32D2D"
                  for v in monthly["signed"]]
        fig = go.Figure(go.Bar(
            x=monthly["month"], y=monthly["signed"], marker_color=colors,
            hovertemplate="%{x|%Y-%m}<br>Netto: %{y:,.0f}<extra></extra>"))
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title=cur, bargap=0.2)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Letzte Markt-Transaktionen (P/S)**")
    show = ps.sort_values("transaction_date", ascending=False).head(25) \
        if not ps.empty else ps
    if show.empty:
        st.caption("Keine offenen Markt-Trades (P/S) im Zeitraum.")
    else:
        try:
            from modules.sec_filings.client import INSIDER_CODE_LABELS as _CL
        except Exception:  # noqa: BLE001
            _CL = {}
        tbl = pd.DataFrame({
            "Datum": show["transaction_date"],
            "Person": show["owner"],
            "Funktion": (show["relationship"] if "relationship" in show.columns
                         else "—"),
            "Art": show["code"].map(_CL).fillna(show["code"]),
            "Stueck": show["shares"].map(
                lambda v: de_dec(v, 0) if not _missing(v) else "—"),
            "Preis": show["price"].map(
                lambda v: _money(v, cur) if not _missing(v) else "—"),
            "Wert": show["value"].map(
                lambda v: _money(v, cur) if not _missing(v) else "—"),
        })
        st.dataframe(tbl, use_container_width=True, hide_index=True)
    st.caption("Markt-Trades Code P (Kauf) / S (Verkauf); Awards/Ausuebungen "
               "ausgeblendet. Routine-Verkaeufe (10b5-1) markiert/"
               "ausgeklammert.")


def _render_mgmt_ownership(ticker: str, src) -> None:
    """Management-Report: Eigentuemerstruktur (Free Float / Institutionell /
    Insider / Strong Hands)."""
    tx = _insider_tx(ticker)
    df = pd.DataFrame(tx) if tx else pd.DataFrame()
    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    shares_out = None
    if ym:
        shares_out = ym[-1].get("shares_outstanding") \
            or ym[-1].get("diluted_shares")

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
    st.caption("Ownership: DEF-14A (Insider) + 13F-Top-Sample "
               "(institutionell) + 10%-Eigner (strategisch) — Anteile sind "
               "Untergrenzen.")


def _render_mgmt_detail(ticker: str, src) -> None:
    """Management-Report (aus Ad-Hoc): Stabilitaets-Verdict + Turnover je
    Jahr + groesste Insider-Bestaende. Ergaenzt Tenure/Ownership oben."""
    tx = _insider_tx(ticker)
    changes = _mgmt_changes(ticker)
    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    total_shares = ((ym[-1].get("shares_outstanding")
                     or ym[-1].get("diluted_shares")) if ym else None)
    years = N_YEARS if PERIOD == "annual" else max(1, N_YEARS // 4)
    today = pd.Timestamp.utcnow()
    df = pd.DataFrame(tx) if tx else pd.DataFrame()

    # CEO-Tenure (fruehestes Insider-Filing der aktuellen Person)
    ceo_ten = None
    if not df.empty and "is_ceo" in df.columns:
        sub = df[df["is_ceo"] == True]  # noqa: E712
        if not sub.empty:
            sub = sub.assign(_d=pd.to_datetime(sub["transaction_date"],
                                               errors="coerce"))
            cur_row = sub.sort_values("_d").iloc[-1]
            owner = cur_row["owner"]
            cik = (cur_row.get("owner_cik")
                   if "owner_cik" in sub.columns else None)
            f_iso = _first_filing(ticker, owner, cik)
            if f_iso:
                f_dt = pd.to_datetime(f_iso, utc=True, errors="coerce")
                if pd.notna(f_dt):
                    ceo_ten = (today - f_dt).days / 365.25

    # Insider-Ownership: DEF 14A bevorzugt, sonst Form-4-Schaetzung
    bo = _beneficial(ticker)
    own = bo.get("group_pct") if bo else None
    if own is None and not df.empty and "shares_following" in df.columns \
            and total_shares:
        held = df[df["shares_following"].notna()].copy()
        held["gid"] = (held["owner_cik"].fillna(held["owner"])
                       if "owner_cik" in held.columns else held["owner"])
        held = held.sort_values("transaction_date")
        a = held.groupby("gid").agg(
            shares=("shares_following", "last"),
            off=("is_officer", "max") if "is_officer" in held.columns
            else ("shares_following", "size"),
            dir=("is_director", "max") if "is_director" in held.columns
            else ("shares_following", "size"))
        mask = a["off"].astype(bool) | a["dir"].astype(bool)
        own = fm.safe_div(float(a.loc[mask, "shares"].sum()), total_shares)

    # Turnover (8-K Item 5.02) im Fenster
    cutoff = today - pd.Timedelta(days=int(years) * 365)
    chg_dts = pd.to_datetime([c.get("filed_at") for c in changes],
                             errors="coerce", utc=True)
    turnover = int((chg_dts >= cutoff).sum()) if len(chg_dts) else 0
    per_year = turnover / max(1, int(years))

    checks = []
    if ceo_ten is not None:
        checks.append(("CEO-Tenure ≥ 5 Jahre", ceo_ten >= 5))
    if own is not None:
        checks.append(("Insider Ownership ≥ 5 %", own >= 0.05))
    checks.append((f"Management-Turnover ≤ 1/Jahr ({years} J)",
                   per_year <= 1.0))
    passed = sum(1 for _, ok in checks if ok)
    r = passed / len(checks)
    box = st.success if r >= 0.75 else st.info if r >= 0.5 else st.warning
    verdict = ("stabil / engagiert" if r >= 0.75
               else "durchschnittlich" if r >= 0.5
               else "instabil / wenig Eigenanteil")
    lines = "  \n".join(f"{'✅' if ok else '❌'} {n}" for n, ok in checks)
    box(f"Management wirkt **{verdict}** — {passed}/{len(checks)}  \n{lines}")

    if len(chg_dts):
        cdf = pd.DataFrame({"dt": chg_dts})
        cdf = cdf[cdf["dt"] >= cutoff]
        if not cdf.empty:
            cdf["Jahr"] = cdf["dt"].dt.year
            per = cdf.groupby("Jahr").size().reset_index(name="Wechsel")
            fig = go.Figure(go.Bar(x=per["Jahr"], y=per["Wechsel"],
                                   marker_color="#B4862B"))
            fig.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                              title="Management-Wechsel je Jahr (8-K 5.02)",
                              yaxis_title="Anzahl")
            st.plotly_chart(fig, use_container_width=True)

    if not df.empty and "shares_following" in df.columns:
        held = df[df["shares_following"].notna()].copy()
        if not held.empty:
            held["gid"] = (held["owner_cik"].fillna(held["owner"])
                           if "owner_cik" in held.columns else held["owner"])
            held = held.sort_values("transaction_date")
            aggkw = {"shares": ("shares_following", "last"),
                     "owner": ("owner", "last")}
            for col, alias, how in [("relationship", "rel", "last"),
                                    ("is_officer", "off", "max"),
                                    ("is_director", "dir", "max"),
                                    ("is_tenpct", "ten", "max")]:
                if col in held.columns:
                    aggkw[alias] = (col, how)
            agg = held.groupby("gid").agg(**aggkw)
            cols = agg.columns
            with st.expander("Groesste Insider-Bestaende"):
                top = agg.sort_values("shares", ascending=False).head(12)
                st.dataframe(pd.DataFrame([{
                    "Person": r2["owner"],
                    "Funktion": (r2["rel"] if "rel" in cols else "—"),
                    "Typ": ("Management"
                            if ((r2.get("off") if "off" in cols else False)
                                or (r2.get("dir") if "dir" in cols else False))
                            else "10%-Eigner"
                            if ("ten" in cols and r2["ten"]) else "—"),
                    "Bestand (Stk.)": de_dec(r2["shares"], 0),
                    "% ausstehend": (_pct(fm.safe_div(r2["shares"],
                                                      total_shares))
                                     if total_shares else "—"),
                } for _g, r2 in top.iterrows()]),
                    use_container_width=True, hide_index=True)
    st.caption("Verdict aus CEO-Tenure, Insider-Ownership (DEF 14A bzw. "
               "Form-4-Schaetzung) und Management-Turnover (8-K 5.02). "
               "Tenure-/Ownership-Detail siehe Reports oben.")


def _render_mgmt_capital(ticker: str, src) -> None:
    """Management-Report: Kapitalallokation + SBC/Verwaesserung (letztes
    Jahr)."""
    cur = src.currency or "USD"
    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    if not ym:
        st.caption("Keine Jahresdaten verfuegbar.")
        return
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
class Report:
    """Ein einzelner Bericht (Unterpunkt) innerhalb einer Kategorie.

    render(ticker, src) zeichnet den Inhalt. lazy=True -> erst auf Knopfdruck
    laden (mehrere API-Calls); sonst direkt im (per default offenen) Expander.
    """
    id: str
    title: str
    render: Callable[[str, object], None]
    status: str = "stable"                      # stable | beta | todo
    lazy: bool = False
    expanded: bool = True


@dataclass
class Category:
    """Eine Kategorie (heute = ein Tab) der Unternehmens-Analyse.

    Entweder klassisch ueber render(ticker, src) ODER — bevorzugt — als Liste
    benannter Reports (reports). Hat eine Kategorie Reports, werden diese als
    aufklappbare Bereiche gerendert; render bleibt fuer noch nicht zerlegte
    Kategorien.
    """
    id: str
    title: str                                  # Reiter-/Navigations-Label
    render: Optional[Callable[[str, object], None]] = None
    question: Optional[str] = None              # "#### …"-Header; None = keiner
    desc: str = ""                              # Kurzbeschreibung (Phase 3+)
    err_label: str = ""                         # Klartext im Fehler-Fallback
    is_question: bool = False                   # zaehlt zur 6-Fragen-Scorecard
    status: str = "stable"                       # stable | beta | todo
    universe_only: bool = False                 # nur fuer Universums-Werte
    reports: list = field(default_factory=list)  # Unterpunkte (Report)


def _render_report(rep: Report, ticker: str, src) -> None:
    """Einen Report als aufklappbaren Bereich zeichnen (lazy oder direkt)."""
    if rep.lazy:
        _lazy_report(rep.title, f"{rep.id}_{ticker}", rep.render, ticker, src,
                     status=rep.status, expanded=rep.expanded)
        return
    with st.expander(_rep_label(rep.title, rep.status), expanded=rep.expanded):
        try:
            rep.render(ticker, src)
        except Exception as e:  # noqa: BLE001
            st.warning(f"{rep.title} nicht ladbar: "
                       f"{e.__class__.__name__}: {e}")


# Performance-Fenster: (Label, Kalendertage; None = YTD). 1D nur Tabelle.
_PERF_WINDOWS = [("1D", 1), ("5D", 7), ("1M", 30), ("3M", 91),
                 ("YTD", None), ("1Y", 365), ("5Y", 1825)]
_CHART_WINDOWS = ["5D", "1M", "3M", "YTD", "1Y", "5Y"]


def _window_start(last_d: date, label: str, days):
    """Startdatum eines Performance-Fensters relativ zum letzten Handelstag."""
    if label == "YTD":
        return date(last_d.year, 1, 1)
    return last_d - timedelta(days=days)


def _perf_returns(px: dict) -> dict:
    """Rendite je Fenster aus {iso: close}. {label: pct|None}."""
    if not px:
        return {}
    items = sorted((date.fromisoformat(k), v) for k, v in px.items())
    dates = [d for d, _ in items]
    closes = [c for _, c in items]
    last_d, last_c = dates[-1], closes[-1]

    def close_on_or_before(target: date):
        best = None
        for d, c in zip(dates, closes):
            if d <= target:
                best = c
            else:
                break
        return best

    out = {}
    for label, days in _PERF_WINDOWS:
        ref = close_on_or_before(_window_start(last_d, label, days))
        out[label] = (last_c / ref - 1) if (ref and ref > 0) else None
    return out


def _render_ov_summary(ticker: str, src) -> None:
    """Ueberblick-Report: Unternehmen, Sektor, Market Cap, EV, KGV."""
    cur = src.currency or "USD"
    info = _company_info(ticker)
    name = src.name or info.get("name") or ticker
    sub = " · ".join(x for x in (info.get("sector"), info.get("industry"))
                     if x)
    st.markdown(f"**{name}**" + (f" — {sub}" if sub else ""))

    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    last = ym[-1] if ym else None
    price = _latest_price(ticker)
    shares = (last.get("shares_outstanding") or last.get("diluted_shares")) \
        if last else None
    mcap = (price * shares) if (price and shares) else info.get("market_cap")
    ev = (mcap + (last.get("net_debt") or 0.0)) if (mcap and last) else None
    pe = (mcap / last["net_income"]
          if (mcap and last and last.get("net_income")
              and last["net_income"] > 0) else None)

    m = st.columns(3)
    m[0].metric("Market Cap", _money(mcap, cur))
    m[1].metric("Enterprise Value", _money(ev, cur))
    m[2].metric("KGV (P/E)", f"{de_dec(pe, 1)}" if pe is not None else "—")
    if info.get("summary"):
        st.caption(info["summary"])


def _render_ov_performance(ticker: str, src) -> None:
    """Ueberblick-Report: Kurschart (Zeitraum-Auswahl) + Performance-Tabelle."""
    cur = src.currency or "USD"
    end_iso = date.today().isoformat()
    start_iso = (date.today() - timedelta(days=1900)).isoformat()
    px = _prices(ticker, start_iso, end_iso)
    if not px:
        st.caption("Keine Kursdaten verfuegbar.")
        return
    items = sorted((date.fromisoformat(k), v) for k, v in px.items())
    last_d = items[-1][0]

    # --- Kurs-Chart mit Zeitraum-Auswahl ---
    if hasattr(st, "segmented_control"):
        win = st.segmented_control("Zeitraum", _CHART_WINDOWS, default="1Y",
                                   key=f"ov_perf_win_{ticker}")
    else:
        win = st.radio("Zeitraum", _CHART_WINDOWS, index=4, horizontal=True,
                       key=f"ov_perf_win_{ticker}")
    win = win or "1Y"
    days = dict(_PERF_WINDOWS)[win]
    wstart = _window_start(last_d, win, days)
    sel = [(d, c) for d, c in items if d >= wstart]
    if len(sel) >= 2:
        pdf = pd.DataFrame({"d": [d for d, _ in sel],
                            "c": [c for _, c in sel]})
        up = sel[-1][1] >= sel[0][1]
        fig = go.Figure(go.Scatter(
            x=pdf["d"], y=pdf["c"], mode="lines",
            line=dict(color="#0F6E56" if up else "#A32D2D", width=1.5)))
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                          title=f"Kurs ({cur}, {win})", yaxis_title=cur)
        st.plotly_chart(fig, use_container_width=True)

    # --- Performance-Tabelle ---
    perf = _perf_returns(px)
    pt = pd.DataFrame([{"Zeitraum": lbl,
                        "Performance": (_pct(perf.get(lbl))
                                        if perf.get(lbl) is not None else "—")}
                       for lbl, _ in _PERF_WINDOWS])
    st.dataframe(pt, use_container_width=True, hide_index=True)
    st.caption("Kursrenditen ggü. letztem Handelstag; YTD ab Jahresanfang.")


def _render_ov_verdict(ticker: str, src) -> None:
    """Ueberblick-Report: Gesamturteil-Scorecard (Geruest)."""
    st.caption("Scorecard je Frage (Ampeln) folgt — die Score-Logik wird aus "
               "den Reports der Fragen-Kategorien abgeleitet.")
    sc = [{"Frage": c.question, "Bewertung": "— (geplant)"}
          for c in CATEGORIES if c.is_question]
    st.dataframe(sc, use_container_width=True, hide_index=True)


def _render_ov_datenbasis(ticker: str, src) -> None:
    """Ueberblick-Report: Datenbasis-Nachweis (Quelle, Perioden)."""
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


# ---- Kennzahlen vs. Sektor (aus Thesis-Cockpit; DB ref_fundamentals_latest) -
_KPI_VALUATION = [
    ("pe_ttm", "KGV (TTM)", "ratio"), ("pe_forward", "KGV (Forward)", "ratio"),
    ("peg_ratio", "PEG-Ratio", "ratio"), ("pb", "Kurs / Buchwert", "ratio"),
    ("ps_ttm", "Kurs / Umsatz", "ratio"), ("p_fcf", "Kurs / FCF", "ratio"),
    ("ev_ebitda", "EV / EBITDA", "ratio"), ("ev_sales", "EV / Umsatz", "ratio"),
]
_KPI_PROFIT = [
    ("gross_margin", "Bruttomarge", "pct"),
    ("operating_margin", "Operative Marge", "pct"),
    ("net_margin", "Nettomarge", "pct"), ("fcf_margin", "FCF-Marge", "pct"),
    ("roe", "Eigenkapitalrendite", "pct"),
    ("roa", "Gesamtkapitalrendite", "pct"), ("roic", "ROIC", "pct"),
]
_KPI_LEVERAGE = [
    ("debt_to_equity", "Verschuldungsgrad", "ratio"),
    ("net_debt_to_ebitda", "Nettoschulden / EBITDA", "ratio"),
    ("current_ratio", "Liquiditaetsgrad 3", "ratio"),
    ("quick_ratio", "Liquiditaetsgrad 2", "ratio"),
    ("interest_coverage", "Zinsdeckungsgrad", "ratio"),
]
_KPI_CASHDIV = [
    ("fcf_yield", "FCF-Rendite", "pct"),
    ("dividend_yield", "Dividendenrendite", "pct_raw"),
    ("payout_ratio", "Ausschuettungsquote", "pct"),
    ("dividend_per_share", "Dividende je Aktie", "ratio"),
]


def _kpi_fmt(kind: str, v) -> str:
    if _missing(v):
        return "—"
    if kind == "pct":
        return de_dec(float(v) * 100.0, 1) + " %"
    if kind == "pct_raw":
        return de_dec(float(v), 2) + " %"
    return de_dec(v, 2)


@st.cache_data(ttl=3600, show_spinner=False)
def _fundamentals_all():
    """Komplette ref_fundamentals_latest (DB) oder None — fuer Sektor-Median."""
    if _run_query is None:
        return None
    try:
        return _run_query("SELECT * FROM ref_fundamentals_latest", None)
    except Exception:  # noqa: BLE001
        return None


def _kpi_table(group, f, sec) -> None:
    rows = []
    for col, label, kind in group:
        raw = f.get(col)
        med = (sec[col].median() if (col in sec.columns and not sec.empty)
               else None)
        marker = "—"
        if not _missing(raw) and not _missing(med):
            marker = ("▲" if float(raw) > float(med)
                      else "▼" if float(raw) < float(med) else "=")
        rows.append({"Kennzahl": label, "Wert": _kpi_fmt(kind, raw),
                     "Sektor-Median": _kpi_fmt(kind, med), "vs.": marker})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 column_config={"vs.": st.column_config.TextColumn(
                     "vs.", width="small",
                     help="▲ ueber / ▼ unter / = auf Sektor-Median")})


def _render_ov_kennzahlen(ticker: str, src) -> None:
    """Ueberblick-Report: Kennzahlen vs. Sektor-Median (Universum, DB)."""
    if not (src.in_universe and src.ref_instrument_id):
        st.caption("Kennzahlen-Vergleich nur fuer Universums-Werte "
                   "(ref_fundamentals_latest).")
        return
    fa = _fundamentals_all()
    if fa is None or fa.empty:
        st.caption("Keine Fundamentaldaten verfuegbar.")
        return
    own = fa[fa["ref_instrument_id"] == src.ref_instrument_id]
    if own.empty:
        st.caption("Keine Fundamentaldaten fuer diesen Wert hinterlegt.")
        return
    f = own.iloc[0]
    sector = f["sector"] if "sector" in own.columns else None
    sec = fa[fa["sector"] == sector] if not _missing(sector) else fa.iloc[0:0]
    st.caption(f"Sektor-Median aus {len(sec)} Unternehmen im Sektor "
               f"„{sector or '—'}“.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Bewertung**")
        _kpi_table(_KPI_VALUATION, f, sec)
        st.markdown("**Verschuldung & Liquiditaet**")
        _kpi_table(_KPI_LEVERAGE, f, sec)
    with c2:
        st.markdown("**Profitabilitaet**")
        _kpi_table(_KPI_PROFIT, f, sec)
        st.markdown("**Cash & Dividende**")
        _kpi_table(_KPI_CASHDIV, f, sec)


def _fmt_cap(v) -> str:
    """Market Cap kompakt, ohne Waehrung: 1,23 Bio / 75,2 Mrd / 500 Mio."""
    if _missing(v):
        return "—"
    v = float(v)
    if abs(v) >= 1e12:
        return f"{de_dec(v / 1e12, 2)} Bio"
    if abs(v) >= 1e9:
        return f"{de_dec(v / 1e9, 1)} Mrd"
    return f"{de_dec(v / 1e6, 0)} Mio"


@st.cache_data(ttl=3600, show_spinner=False)
def _instruments():
    """ref_instruments (id, symbol, name) oder None — fuer Peer-Namen."""
    if _run_query is None:
        return None
    try:
        return _run_query("SELECT ref_instrument_id, symbol, name "
                          "FROM ref_instruments", None)
    except Exception:  # noqa: BLE001
        return None


def _render_moat_dominanz(ticker: str, src) -> None:
    """Burggraben-Report: Branche & Dominanz (Rang/Sektor-Anteil + Peers).

    Aus Thesis-Cockpit; Quelle ref_fundamentals_latest (Universum, DB).
    """
    if not (src.in_universe and src.ref_instrument_id):
        st.caption("Branchen-Einordnung nur fuer Universums-Werte "
                   "(ref_fundamentals_latest).")
        return
    fa = _fundamentals_all()
    if fa is None or fa.empty:
        st.caption("Keine Fundamentaldaten verfuegbar.")
        return
    own = fa[fa["ref_instrument_id"] == src.ref_instrument_id]
    if own.empty:
        st.caption("Keine Fundamentaldaten fuer diesen Wert hinterlegt.")
        return
    f = own.iloc[0]
    sector = f["sector"] if "sector" in own.columns else None
    if _missing(sector):
        st.info("Kein Sektor hinterlegt — keine Branchen-Einordnung moeglich.")
        return

    peers = fa[fa["sector"] == sector].copy()
    instr = _instruments()
    if instr is not None and not instr.empty:
        peers = peers.merge(instr, on="ref_instrument_id", how="left")
    peers = peers.sort_values("market_cap", ascending=False,
                              na_position="last").reset_index(drop=True)
    rank = peers.index[peers["ref_instrument_id"] == src.ref_instrument_id]
    sec_cap = peers["market_cap"].sum(skipna=True)
    self_cap = float(f["market_cap"]) if not _missing(f["market_cap"]) else None

    d = st.columns(3)
    d[0].metric("Rang im Sektor",
                f"{int(rank[0]) + 1} / {len(peers)}" if len(rank)
                else f"— / {len(peers)}",
                help=f"Nach Market Cap, Sektor „{sector}“.")
    d[1].metric("Sektor-Anteil",
                f"{de_dec(self_cap / sec_cap * 100, 1)} %"
                if (self_cap and sec_cap) else "—",
                help="Market Cap / Summe aller erfassten Sektor-Unternehmen.")
    d[2].metric("Market Cap", _fmt_cap(self_cap))

    cols = [c for c in ("symbol", "name", "market_cap", "pe_forward",
                        "net_margin", "roic") if c in peers.columns]
    disp = peers[["ref_instrument_id", *cols]].copy()
    if "net_margin" in disp.columns:
        disp["net_margin"] = disp["net_margin"] * 100.0
    if "roic" in disp.columns:
        disp["roic"] = disp["roic"] * 100.0

    def _hl(r):
        on = r["ref_instrument_id"] == src.ref_instrument_id
        return ["background-color: #fff3cd" if on else ""] * len(r)

    def _d1(v):
        return de_dec(v, 1) if not _missing(v) else "—"

    st.dataframe(
        disp.style.apply(_hl, axis=1).format({
            "market_cap": _fmt_cap, "pe_forward": _d1,
            "net_margin": _d1, "roic": _d1}),
        use_container_width=True, hide_index=True,
        column_config={
            "ref_instrument_id": None,
            "symbol": st.column_config.TextColumn("Symbol", width="small"),
            "name": st.column_config.TextColumn("Name"),
            "market_cap": st.column_config.TextColumn("Market Cap"),
            "pe_forward": st.column_config.TextColumn("KGV fwd", width="small"),
            "net_margin": st.column_config.TextColumn("Nettom. %",
                                                      width="small"),
            "roic": st.column_config.TextColumn("ROIC %", width="small"),
        })
    st.caption("Dominanz-Indikator: Rang + Sektor-Anteil messen die relative "
               "Groesse — fuer Marktfuehrerschaft zusaetzlich Margen und ROIC "
               "gegen die Peers lesen.")


@st.cache_data(ttl=1800, show_spinner=False)
def _db_by_instrument(sql: str, ref_id):
    """run_query(sql, (ref_id,)) defensiv — None bei fehlender Tabelle/Fehler."""
    if _run_query is None or not ref_id:
        return None
    try:
        return _run_query(sql, (ref_id,))
    except Exception:  # noqa: BLE001
        return None


def _render_ov_termine_news(ticker: str, src) -> None:
    """Ueberblick-Report: naechster Earnings-Termin + News (Universum, DB)."""
    if not (src.in_universe and src.ref_instrument_id):
        st.caption("Termine & News nur fuer Universums-Werte (DB).")
        return
    rid = src.ref_instrument_id
    st.markdown("**Naechster Earnings-Termin**")
    ec = _db_by_instrument(
        "SELECT earnings_date, source, fetched_at FROM ref_earnings_calendar "
        "WHERE ref_instrument_id = ?", rid)
    if ec is None:
        st.caption("Keine Earnings-Kalender-Tabelle vorhanden.")
    else:
        valid = (ec[ec["earnings_date"] > pd.Timestamp("2000-01-01")]
                 if not ec.empty else ec)
        if valid.empty:
            st.caption("Kein Earnings-Termin hinterlegt.")
        else:
            ed = pd.to_datetime(valid["earnings_date"].iloc[0]).date()
            delta = (ed - date.today()).days
            when = (f"in {delta} Tagen" if delta > 0
                    else "heute" if delta == 0 else f"vor {abs(delta)} Tagen")
            st.metric(f"{src.ticker} — Earnings", ed.isoformat(), delta=when,
                      delta_color="off")
            st.caption(f"Quelle: {valid['source'].iloc[0]}.")

    st.divider()
    st.markdown("**News**")
    news = _db_by_instrument(
        "SELECT a.ts, a.title, a.summary, a.url, a.source "
        "FROM ref_sa_article_symbols s JOIN ref_sa_articles a "
        "USING (article_id) WHERE s.ref_instrument_id = ? "
        "ORDER BY a.ts DESC LIMIT 20", rid)
    if news is None:
        st.caption("Keine News-Tabellen vorhanden.")
    elif news.empty:
        st.caption("Keine News zu diesem Instrument.")
    else:
        st.caption(f"{len(news)} aktuellste Artikel.")
        for _, a in news.iterrows():
            title = a["title"] or "(ohne Titel)"
            st.markdown(f"**[{title}]({a['url']})**" if a["url"]
                        else f"**{title}**")
            st.caption(f"{a['ts']}  ·  {a['source'] or ''}")
            if a["summary"]:
                st.write(a["summary"])
            st.divider()


def _render_pf_signale(ticker: str, src) -> None:
    """Portfolio-Report: Empfehlungen + Alerts (Universum, DB)."""
    if not (src.in_universe and src.ref_instrument_id):
        st.caption("Signale nur fuer Universums-Werte (DB).")
        return
    rid = src.ref_instrument_id
    st.markdown("**Empfehlungen**")
    sig = _db_by_instrument(
        "SELECT ts, action, priority, category, title, rationale "
        "FROM sig_recommendations WHERE ref_instrument_id = ? "
        "ORDER BY ts DESC", rid)
    if sig is None:
        st.caption("Keine Recommendation-Tabelle vorhanden.")
    elif sig.empty:
        st.caption("Keine Empfehlungen zu diesem Instrument.")
    else:
        pic = {"high": "🔴", "medium": "🟠", "low": "🟢"}
        for _, r in sig.iterrows():
            st.markdown(
                f"{pic.get(r['priority'], '·')} `{r['action']}`  ·  "
                f"_{r['category'] or ''}_  ·  {r['ts']}  \n"
                f"**{r['title'] or ''}**  \n{r['rationale'] or ''}")
            st.divider()

    st.markdown("**Alerts**")
    al = _db_by_instrument(
        "SELECT ts, rule_name, direction, trigger_value, threshold "
        "FROM sig_alerts WHERE ref_instrument_id = ? "
        "ORDER BY ts DESC LIMIT 50", rid)
    if al is None:
        st.caption("Keine Alert-Tabelle vorhanden.")
    elif al.empty:
        st.caption("Keine Alerts zu diesem Instrument.")
    else:
        st.dataframe(
            al.style.format({"trigger_value": de_dec, "threshold": de_dec}),
            use_container_width=True, hide_index=True,
            column_config={
                "ts": st.column_config.DateColumn("Datum"),
                "rule_name": st.column_config.TextColumn("Regel"),
                "direction": st.column_config.TextColumn("Richtung",
                                                         width="small"),
                "trigger_value": "Trigger", "threshold": "Schwelle"})


def _render_pf_overview(ticker: str, src) -> None:
    """Portfolio-Report: Holdings/MtM/Thesis-Ampel (Geruest)."""
    if src.in_universe:
        st.info("Folgt: Holdings, MtM, Thesis-Ampel, Termine, Screener-Links "
                "(nur fuer Universums-Werte).")
    else:
        st.caption(f"{src.ticker} ist nicht im Portfolio-Universum — kein "
                   "Portfolio-Kontext.")


# ---- Gesamt-Qualitaets-Score (aus Ad-Hoc): 5 gewichtete Themen ----
# Themen-Wrapper: laden die Daten und delegieren die pass/fail-Logik an das
# zentrale scoring-Modul (gemeinsame Single Source mit den Report-Verdicts).
def _gs_balance(ticker):
    return sc.checks_balance(_balance(ticker),
                             _SCORE["thresholds"]["balance_sheet"])


def _gs_returns(ticker):
    ym = _year_metrics(ticker, N_YEARS, PERIOD).get("rows") or []
    rets = [fm.returns_from_metrics(d) for d in ym]
    return sc.checks_returns(rets, _SCORE["thresholds"]["return_on_capital"])


def _gs_sbc(ticker):
    rows = _sbc_hist(ticker, N_YEARS, PERIOD)
    if not rows:
        return []
    last = rows[-1]
    sbc_rev = fm.safe_div(last.get("sbc"), last.get("revenue"))
    sbc_cfo = fm.safe_div(last.get("sbc"), last.get("cfo"))
    adj = fm.split_adjust_shares(rows, _splits(ticker))
    sh = [(d["period_end"], d["diluted_shares"]) for d in adj
          if d.get("diluted_shares")]
    dil = None
    if len(sh) >= 2:
        yrs = (pd.to_datetime(sh[-1][0]) - pd.to_datetime(sh[0][0])).days \
            / 365.25
        if yrs >= 1:
            dil = fm.cagr(sh[0][1], sh[-1][1], yrs)
    return sc.checks_sbc(sbc_rev, sbc_cfo, dil,
                         _SCORE["thresholds"]["stock_based_comp"])


def _gs_gaap(ticker):
    ng = _nongaap(ticker)
    if ng.get("categories") is None and ng.get("mentions") is None:
        return []
    return sc.checks_gaap(ng.get("mentions"), ng.get("adds_back_sbc"),
                          len(ng.get("categories") or {}),
                          _SCORE["thresholds"]["gaap_vs_non_gaap"])


def _gs_insider(ticker):
    tx = _insider_tx(ticker)
    if not tx:
        return []
    years = N_YEARS if PERIOD == "annual" else max(1, N_YEARS // 4)
    cutoff = (date.today() - timedelta(days=int(years) * 365)).isoformat()
    df = pd.DataFrame(tx)
    if not df.empty and "transaction_date" in df.columns:
        df = df[df["transaction_date"] >= cutoff]
    buy_val = sell_val = 0.0
    n_buyers = n_sellers = 0
    if not df.empty and "code" in df.columns:
        buys, sells = df[df["code"] == "P"], df[df["code"] == "S"]
        if "value" in df.columns:
            buy_val = float(buys["value"].fillna(0).sum())
            sell_val = float(sells["value"].fillna(0).sum())
        if "owner" in df.columns:
            n_buyers = int(buys["owner"].nunique())
            n_sellers = int(sells["owner"].nunique())
    return sc.checks_insider(buy_val, sell_val, n_buyers, n_sellers,
                             _SCORE["thresholds"]["insider"])


_GS_THEMES = [
    ("Return on Capital", "return_on_capital", _gs_returns),
    ("Balance Sheet", "balance_sheet", _gs_balance),
    ("Stock-based Comp.", "stock_based_comp", _gs_sbc),
    ("GAAP vs non-GAAP", "gaap_vs_non_gaap", _gs_gaap),
    ("Insider", "insider", _gs_insider),
]


def _render_gesamt_score(ticker: str, src) -> None:
    """Gesamt-Qualitaets-Score (aus Ad-Hoc): gewichteter Mittelwert ueber
    fuenf Themen (fehlende ausgeklammert, Gewichte renormiert)."""
    weights, bands = _SCORE["weights"], _SCORE["bands"]
    rows = []
    num = den = 0.0
    for name, key, fn in _GS_THEMES:
        try:
            checks = fn(ticker)
        except Exception:  # noqa: BLE001
            checks = None
        w = weights.get(key, 0)
        sub = sc.subscore(checks)
        if sub is not None:
            num += sub * w
            den += w
        rows.append({"Thema": name, "checks": checks, "sub": sub, "w": w})

    if den == 0:
        st.info("Keine Themen lieferten Daten — Ticker pruefen.")
        return
    score = round(100 * num / den)
    n_ok = sum(1 for r in rows if r["sub"] is not None)
    box = (st.success if score >= bands["strong"]
           else st.info if score >= bands["mixed"] else st.warning)
    verdict = ("hohe Qualitaet" if score >= bands["strong"]
               else "gemischt" if score >= bands["mixed"] else "schwach")
    box(f"## {score}/100 — {verdict}")
    st.caption(f"Gewichteter Mittelwert ueber {n_ok}/5 auswertbare Themen "
               "(fehlende ausgeklammert, Gewichte renormiert).")

    bar = pd.DataFrame([{
        "Thema": r["Thema"],
        "Score": round(100 * r["sub"]) if r["sub"] is not None else None,
        "Gewicht": r["w"]} for r in rows])
    fig = go.Figure(go.Bar(
        x=bar["Score"], y=bar["Thema"], orientation="h",
        marker_color=["#1D9E75" if (s is not None and s >= bands["strong"])
                      else "#B4862B" if (s is not None and s >= bands["mixed"])
                      else "#A32D2D" if s is not None else "#CFCDC6"
                      for s in bar["Score"]],
        text=[f"{s}" if s is not None else "n/a" for s in bar["Score"]],
        textposition="auto", hovertemplate="%{y}: %{x}/100<extra></extra>"))
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis=dict(range=[0, 100], title="Teil-Score"))
    st.plotly_chart(fig, use_container_width=True)

    for r in rows:
        w_pct = f"{int(r['w'] * 100)} %"
        if r["sub"] is None:
            st.markdown(f"**{r['Thema']}** · Gewicht {w_pct} — _keine Daten / "
                        "nicht auswertbar_")
            continue
        with st.expander(f"{r['Thema']} · {round(100 * r['sub'])}/100 · "
                         f"Gewicht {w_pct}"):
            for name, ok in r["checks"]:
                st.markdown(f"{'✅' if ok else '❌'} {name}")
    st.caption("Score = gewichteter Anteil erfuellter Qualitaets-Kriterien je "
               "Thema. Heuristik, kein Anlageurteil.")


CATEGORIES: list[Category] = [
    Category("overview", "Ueberblick", err_label="Ueberblick",
             reports=[
                 Report("ov_summary", "Summary", _render_ov_summary),
                 Report("ov_kennzahlen", "Kennzahlen (vs. Sektor)",
                        _render_ov_kennzahlen),
                 Report("ov_performance", "Aktienperformance",
                        _render_ov_performance),
                 Report("ov_termine_news", "Termine & News",
                        _render_ov_termine_news),
                 Report("ov_verdict", "Gesamturteil", _render_ov_verdict),
                 Report("ov_datenbasis", "Datenbasis", _render_ov_datenbasis,
                        status="beta", expanded=False),
             ]),
    Category("guv", "Umsatz & GuV",
             question="Umsatz & GuV — Struktur und Qualitaet",
             desc="Umsatz-Wachstum + Segmente, Margen-Trend, absolute "
                  "Kostenaufteilung, GuV-Sankey (aus Thesis-Cockpit).",
             err_label="Umsatz & GuV",
             reports=[
                 Report("guv_umsatz", "Umsatz-Verlauf & Segmente",
                        _render_guv_umsatz),
                 Report("guv_margen", "Margen-Trend", _render_guv_margen),
                 Report("guv_kosten", "Kostenaufteilung (absolut)",
                        _render_guv_kosten),
                 Report("guv_sankey", "GuV-Struktur (Sankey)",
                        _render_guv_sankey, status="beta"),
             ]),
    Category("retained_ev", "Earnings, EPS, Equity, FCF & EV",
             question="Earnings, EPS, Equity, FCF & EV",
             desc="Bewertungskennzahlen (EV, EV/FCF, KGV, Earnings Yields) "
                  "plus Mehrjahres-Verlauf von Gewinnruecklagen, EPS (split-"
                  "bereinigt), Eigenkapital, Free Cash Flow und Enterprise "
                  "Value.",
             err_label="Earnings/EV",
             reports=[
                 Report("val_metrics", "Bewertungskennzahlen",
                        _render_val_metrics),
                 Report("re_eps_ev",
                        "Gewinnruecklagen, EPS, Equity, FCF & EV — Verlauf",
                        _render_re_eps_ev, status="beta", lazy=True,
                        expanded=False),
             ]),
    Category("business", "1 Geschaeft",
             question="Ist das Geschaeft gut?",
             desc="Umsatzwachstum, Margen-Trend, ROIC/ROCE/ROE/ROA, "
                  "FCF-Marge, Umsatz/Mitarbeiter.",
             err_label="Geschaeft", is_question=True,
             reports=[
                 Report("biz_growth", "Wachstum & Rendite",
                        _render_biz_growth_returns),
                 Report("biz_roc", "Return on Capital", _render_biz_roc),
                 Report("biz_margins", "Margen-Trend", _render_biz_margins),
                 Report("biz_revenue", "Umsatzverlauf", _render_biz_revenue),
                 Report("biz_phys",
                        "Physical Growth (PP&E, CapEx, Mitarbeiter)",
                        _report_physical, status="beta", lazy=True,
                        expanded=False),
             ]),
    Category("moat", "2 Burggraben",
             question="Hat das Unternehmen einen Burggraben?",
             desc="Moat-Score (Margen-Stabilitaet, ROIC-Stabilitaet, "
                  "F&E-Effizienz, Rueckkaeufe, Marktanteil) + Peers.",
             err_label="Burggraben", is_question=True,
             reports=[
                 Report("moat_score", "Moat-Score (Signale & Gewichtung)",
                        _render_moat_score),
                 Report("moat_dominanz", "Branche & Dominanz",
                        _render_moat_dominanz),
             ]),
    Category("balance", "3 Bilanz",
             question="Ist die Bilanz solide?",
             desc="Current/Quick Ratio, Net Debt, Debt/Equity, "
                  "Eigenkapitalquote, Goodwill-Anteil + Trend.",
             err_label="Bilanz", is_question=True,
             reports=[
                 Report("bal_snapshot", "Soliditaet & Kennzahlen",
                        _render_bal_snapshot),
                 Report("bal_trend", "Bilanz-Trend (Verlauf)",
                        _render_bal_trend),
             ]),
    Category("management", "4 Management",
             question="Ist das Management gut?",
             desc="Tenure, Ownership-Struktur, Turnover, Insider-Conviction, "
                  "Kapitalallokation, SBC/Verwaesserung.",
             err_label="Management", is_question=True,
             reports=[
                 Report("mgmt_conviction", "Insider-Signal & Tenure",
                        _render_mgmt_conviction),
                 Report("mgmt_insider_tx", "Insider Sales / Buys (Form 3/4/5)",
                        _render_mgmt_insider_tx),
                 Report("mgmt_ownership", "Ownership-Struktur",
                        _render_mgmt_ownership),
                 Report("mgmt_detail",
                        "Management — Stabilitaet & Insider-Bestaende",
                        _render_mgmt_detail),
             ]),
    Category("earnings_real", "5 Gewinne echt",
             question="Sind die Gewinne echt?",
             desc="Earnings-Quality-Score, GAAP vs non-GAAP, Owner Earnings "
                  "vs Nettogewinn vs FCF.",
             err_label="Gewinnqualitaet", is_question=True,
             reports=[
                 Report("eq_score", "Earnings-Quality-Score",
                        _render_eq_score),
                 Report("eq_gaap", "GAAP vs non-GAAP — Earnings-Exhibit",
                        _render_eq_gaap, status="beta", lazy=True,
                        expanded=False),
                 Report("owner_earnings",
                        "Owner Earnings vs Nettogewinn vs FCF",
                        _render_owner_earnings),
             ]),
    Category("sbc", "Stock-based Compensation",
             question="Stock-based Compensation & Verwaesserung",
             desc="SBC-Belastung (SBC/Umsatz, SBC/operativer CF), "
                  "Verwaesserung (Aktien-CAGR, split-bereinigt), Trend + "
                  "Rohwerte (aus Ad-Hoc).",
             err_label="Stock-based Compensation",
             reports=[
                 Report("sbc_full", "SBC & Verwaesserung", _render_sbc_full),
                 Report("mgmt_capital", "Kapitalallokation & Verwaesserung",
                        _render_mgmt_capital),
                 Report("mgmt_fcf", "FCF-Verwendung (Kapitalallokation, "
                        "Verlauf)", _render_fcf_alloc, status="beta",
                        lazy=True, expanded=False),
             ]),
    Category("portfolio", "Portfolio & Signale", err_label="Portfolio",
             reports=[
                 Report("pf_signale", "Signale & Alerts", _render_pf_signale),
                 Report("pf_overview", "Portfolio", _render_pf_overview,
                        status="beta", expanded=False),
             ]),
    Category("gesamt_score", "Gesamt Score",
             question="Gesamt-Qualitaets-Score",
             desc="Gewichteter Score ueber Return on Capital, Balance Sheet, "
                  "SBC, GAAP vs non-GAAP, Insider (aus Ad-Hoc).",
             err_label="Gesamt-Score",
             reports=[
                 Report("gesamt", "Gesamt-Qualitaets-Score",
                        _render_gesamt_score, status="beta", lazy=True,
                        expanded=False),
             ]),
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

# ---- Navigator aus der Metadaten-Registry ----
# Einzelauswahl statt st.tabs: pro Rerun laeuft NUR die aktive Kategorie
# (st.tabs rendert alle Tab-Inhalte bei jedem Rerun -> spuerbar langsamer).
# segmented_control wenn vorhanden, sonst horizontales Radio als Fallback.
_titles = [c.title for c in CATEGORIES]
if hasattr(st, "segmented_control"):
    _sel = st.segmented_control("Bereich", _titles, default=_titles[0],
                                key="ana_nav",
                                label_visibility="collapsed")
else:
    _sel = st.radio("Bereich", _titles, horizontal=True, key="ana_nav",
                    label_visibility="collapsed")
_cat = next((c for c in CATEGORIES if c.title == _sel), CATEGORIES[0])

if _cat.question:
    st.markdown(f"#### {_cat.question}")
if _cat.reports:
    for _rep in _cat.reports:
        _render_report(_rep, ticker, src)
elif _cat.render is not None:
    try:
        _cat.render(ticker, src)
    except Exception as e:  # noqa: BLE001
        st.warning(f"{_cat.err_label} nicht ladbar: "
                   f"{e.__class__.__name__}: {e}")
