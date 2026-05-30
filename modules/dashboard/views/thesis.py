"""Thesis-Cockpit — Unternehmens-Analyse pro Name.

Buendelt fuer ein einzelnes Instrument alle Bausteine, die fuer einen
Buy-&-Hold- / CSP-Anlagestil die Kauf- bzw. Halte-Entscheidung stuetzen:
Finanzkennzahlen, Wachstum & Momentum, Chart, Branchen-Einordnung
(Dominanz), Earnings-Termin, News sowie Signale (Empfehlungen + Alerts).

Read-only — konsumiert ref_fundamentals_latest, v_mkt_holdings,
mkt_quotes_daily, ref_earnings_calendar, ref_sa_articles, sig_* sowie die
Watchlists. Keine Schreibzugriffe.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.dashboard.components.format import de_dec, de_int
from modules.dashboard.db import run_query, table_exists


st.title("🔬 Thesis-Cockpit")
st.caption("Eine Seite pro Name — die Bausteine deiner Kauf- und "
           "Halte-Entscheidung an einem Ort.")


# ---------- Formatierungs-Helfer ----------

def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and pd.isna(x))


def _ratio(x, places: int = 2) -> str:
    """Verhaeltniszahl, deutsch:  23.455 -> '23,46'."""
    return de_dec(x, places) if not _missing(x) else "—"


def _pct(x, places: int = 1) -> str:
    """Bruchteil -> Prozent:  0.36572 -> '36,6 %'."""
    if _missing(x):
        return "—"
    return de_dec(float(x) * 100.0, places) + " %"


def _pct_raw(x, places: int = 2) -> str:
    """Bereits in Prozent geliefert:  3.83 -> '3,83 %'."""
    if _missing(x):
        return "—"
    return de_dec(float(x), places) + " %"


def _fmt_cap(v) -> str:
    if _missing(v):
        return "—"
    v = float(v)
    if abs(v) >= 1e12:
        return f"{de_dec(v / 1e12, 2)} Bio"
    if abs(v) >= 1e9:
        return f"{de_dec(v / 1e9, 1)} Mrd"
    return f"{de_int(v / 1e6)} Mio"


def _fmt_money_big(v, currency: str = "USD") -> str:
    """GuV-Betrag, deutsch:  75200000000 -> '75,20 Mrd USD'."""
    if _missing(v):
        return "—"
    a = abs(float(v))
    if a >= 1e9:
        s = f"{de_dec(v / 1e9, 2)} Mrd"
    elif a >= 1e6:
        s = f"{de_dec(v / 1e6, 0)} Mio"
    else:
        s = de_int(v)
    return f"{s} {currency}".strip()


_FMT = {"ratio": _ratio, "pct": _pct, "pct_raw": _pct_raw}


# Kennzahl-Gruppen: (Spalte, Label, Format-Art)
_VALUATION = [
    ("pe_ttm",     "KGV (TTM)",        "ratio"),
    ("pe_forward", "KGV (Forward)",    "ratio"),
    ("peg_ratio",  "PEG-Ratio",        "ratio"),
    ("pb",         "Kurs / Buchwert",  "ratio"),
    ("ps_ttm",     "Kurs / Umsatz",    "ratio"),
    ("p_fcf",      "Kurs / FCF",       "ratio"),
    ("ev_ebitda",  "EV / EBITDA",      "ratio"),
    ("ev_sales",   "EV / Umsatz",      "ratio"),
]
_PROFIT = [
    ("gross_margin",     "Bruttomarge",            "pct"),
    ("operating_margin", "Operative Marge",        "pct"),
    ("net_margin",       "Nettomarge",             "pct"),
    ("fcf_margin",       "FCF-Marge",              "pct"),
    ("roe",              "Eigenkapitalrendite",    "pct"),
    ("roa",              "Gesamtkapitalrendite",   "pct"),
    ("roic",             "ROIC",                   "pct"),
]
_LEVERAGE = [
    ("debt_to_equity",     "Verschuldungsgrad",         "ratio"),
    ("net_debt_to_ebitda", "Nettoschulden / EBITDA",    "ratio"),
    ("current_ratio",      "Liquiditaetsgrad 3",        "ratio"),
    ("quick_ratio",        "Liquiditaetsgrad 2",        "ratio"),
    ("interest_coverage",  "Zinsdeckungsgrad",          "ratio"),
]
_CASHDIV = [
    ("fcf_yield",          "FCF-Rendite",          "pct"),
    ("dividend_yield",     "Dividendenrendite",    "pct_raw"),
    ("payout_ratio",       "Ausschuettungsquote",  "pct"),
    ("dividend_per_share", "Dividende je Aktie",   "ratio"),
]
_GROWTH = [
    ("revenue_cagr_5y",  "Umsatz-CAGR (5 J)",     "pct"),
    ("eps_cagr_5y",      "EPS-CAGR (5 J)",        "pct"),
    ("fcf_cagr_5y",      "FCF-CAGR (5 J)",        "pct"),
    ("dividend_cagr_5y", "Dividenden-CAGR (5 J)", "pct"),
]


# ---------- Universum waehlen ----------

_WATCHLISTS = {
    "Kaufkandidaten": "buy_candidates",
    "CSP-Universe":   "csp_universe",
    "Beobachtung":    "observation",
    "Value-Picks":    "value_picks",
}

uni_choice = st.radio(
    "Universum",
    ["Portfolio", *_WATCHLISTS.keys(), "Alle (Fundamentaldaten)"],
    horizontal=True,
    key="thesis_universe",
)

if uni_choice == "Portfolio":
    uni = run_query(
        "SELECT ref_instrument_id, symbol, name, SUM(mtm_eur) AS sort_mv "
        "FROM v_mkt_holdings GROUP BY 1, 2, 3 ORDER BY sort_mv DESC NULLS LAST")
elif uni_choice == "Alle (Fundamentaldaten)":
    uni = run_query(
        "SELECT f.ref_instrument_id, i.symbol, i.name "
        "FROM ref_fundamentals_latest f "
        "LEFT JOIN ref_instruments i USING (ref_instrument_id) "
        "ORDER BY i.symbol")
else:
    uni = run_query(
        "SELECT m.ref_instrument_id, i.symbol, i.name "
        "FROM list_watchlist_members m "
        "LEFT JOIN ref_instruments i USING (ref_instrument_id) "
        "WHERE m.watchlist_id = ? ORDER BY i.symbol",
        (_WATCHLISTS[uni_choice],))

if uni.empty:
    st.info(f"Universum „{uni_choice}“ ist leer.")
    st.stop()

uni = uni.drop_duplicates("ref_instrument_id").reset_index(drop=True)
_opts = uni["ref_instrument_id"].tolist()
_label = {
    r["ref_instrument_id"]: f"{r['symbol'] or r['ref_instrument_id']} — {r['name'] or ''}"
    for _, r in uni.iterrows()
}

ref_id = st.selectbox(
    f"Instrument ({len(_opts)} im Universum)",
    _opts, format_func=lambda x: _label.get(x, x), key="thesis_instrument")

_row_uni = uni[uni["ref_instrument_id"] == ref_id].iloc[0]
symbol = _row_uni["symbol"] or ref_id
name   = _row_uni["name"] or ""


# ---------- Stammdaten laden ----------

fund_all = run_query("SELECT * FROM ref_fundamentals_latest")
fund = fund_all[fund_all["ref_instrument_id"] == ref_id]
f = fund.iloc[0] if not fund.empty else None

held = run_query(
    "SELECT * FROM v_mkt_holdings WHERE ref_instrument_id = ?", (ref_id,))
port_total = run_query("SELECT SUM(mtm_eur) AS t FROM v_mkt_holdings")
port_mv = float(port_total["t"].iloc[0]) if not port_total.empty \
    and pd.notna(port_total["t"].iloc[0]) else 0.0


# ---------- Header ----------

st.divider()
st.subheader(f"{symbol} — {name}")

sector   = (f["sector"]   if f is not None else None) or "—"
industry = (f["industry"] if f is not None else None) or "—"
_cap     = f["market_cap"] if f is not None else None
_stand   = f["ts"] if f is not None else None
st.caption(
    f"🏷 {sector}  ·  {industry}  ·  Market Cap {_fmt_cap(_cap)} "
    f"{(f['country'] or '') if f is not None else ''}"
    + (f"  ·  Fundamentaldaten-Stand {str(_stand)[:10]}"
       if _stand is not None else "  ·  keine Fundamentaldaten"))

if held.empty:
    st.caption("📌 Nicht im Portfolio — Beobachtung / Kandidat.")
else:
    qty   = float(held["quantity"].sum())
    mv    = float(held["mtm_eur"].sum(skipna=True))
    cost  = float(held["cost_total_eur"].sum(skipna=True))
    pnl   = float(held["pnl_eur"].sum(skipna=True))
    weight = (mv / port_mv * 100.0) if port_mv else None
    h1, h2, h3, h4, h5 = st.columns(5)
    h1.metric("Menge",            de_int(qty))
    h2.metric("Marktwert (EUR)",  de_int(mv))
    h3.metric("Einstand (EUR)",   de_int(cost))
    h4.metric("Δ unreal. (EUR)",  de_int(pnl),
              delta=f"{pnl / cost * 100:+.1f} %" if cost else None)
    h5.metric("Portfolio-Gewicht",
              f"{de_dec(weight, 1)} %" if weight is not None else "—",
              help="Anteil am Gesamt-Marktwert (EUR).")


# ---------- Thesis-Ampel ----------

st.divider()
st.markdown("##### Thesis-Ampel")

# 1-Jahres-Kursrendite vorab fuer die Ampel
_px_hist = run_query("""
    WITH ranked AS (
        SELECT ts, close, source,
               ROW_NUMBER() OVER (PARTITION BY ts ORDER BY
                   CASE source WHEN 'ib' THEN 1 WHEN 'yfinance' THEN 2
                               ELSE 9 END) AS rk
        FROM mkt_quotes_daily WHERE ref_instrument_id = ?
    )
    SELECT ts, close FROM ranked WHERE rk = 1 ORDER BY ts
""", (ref_id,))

_returns: dict[str, float] = {}
_last_px = _last_dt = None
if not _px_hist.empty:
    _px_hist["ts"] = pd.to_datetime(_px_hist["ts"])
    _s = _px_hist.set_index("ts")["close"].dropna()
    if not _s.empty:
        _last_px, _last_dt = float(_s.iloc[-1]), _s.index[-1]
        for _k, _d in {"1 Mon": 30, "3 Mon": 91, "6 Mon": 182,
                       "1 Jahr": 365, "3 Jahre": 1095}.items():
            _prior = _s[_s.index <= _last_dt - timedelta(days=_d)]
            if not _prior.empty and _prior.iloc[-1]:
                _returns[_k] = _last_px / float(_prior.iloc[-1]) - 1.0

a1, a2, a3, a4, a5 = st.columns(5)
if f is not None:
    a1.metric("Bewertung (KGV fwd)", _ratio(f["pe_forward"]),
              help=f"PEG-Ratio: {_ratio(f['peg_ratio'])}")
    a2.metric("Nettomarge", _pct(f["net_margin"]))
    _roic = f["roic"] if not _missing(f["roic"]) else f["roe"]
    a3.metric("Kapitalrendite",
              _pct(f["roic"]) if not _missing(f["roic"]) else _pct(f["roe"]),
              help="ROIC — falls leer, ersatzweise Eigenkapitalrendite.")
    a4.metric("Schulden / EBITDA", _ratio(f["net_debt_to_ebitda"]),
              help="Nettoverschuldung im Verhaeltnis zum EBITDA.")
else:
    for _c in (a1, a2, a3, a4):
        _c.metric("—", "—")
_r1y = _returns.get("1 Jahr")
a5.metric("Kurs (1 Jahr)",
          _ratio(_last_px) if _last_px is not None else "—",
          delta=f"{_r1y * 100:+.1f} %" if _r1y is not None else None)


# ---------- Tabs ----------

t_kpi, t_growth, t_guv, t_chart, t_peers, t_news, t_sig, t_screen = st.tabs(
    ["Kennzahlen", "Wachstum & Momentum", "Umsatz & GuV", "Chart",
     "Branche & Dominanz", "Termine & News", "Signale", "Screener"])


def _metric_table(group: list[tuple[str, str, str]],
                   sector_df: pd.DataFrame) -> None:
    """Rendert eine Kennzahl-Gruppe mit Sektor-Median-Vergleich."""
    rows = []
    for col, label, kind in group:
        raw = f[col] if f is not None else None
        med = sector_df[col].median() if (col in sector_df
                                          and not sector_df.empty) else None
        fmt = _FMT[kind]
        marker = "—"
        if not _missing(raw) and not _missing(med):
            marker = "▲" if float(raw) > float(med) else (
                     "▼" if float(raw) < float(med) else "=")
        rows.append({
            "Kennzahl":      label,
            "Wert":          fmt(raw),
            "Sektor-Median": fmt(med),
            "vs.":           marker,
        })
    st.dataframe(
        pd.DataFrame(rows), use_container_width=True, hide_index=True,
        column_config={
            "Kennzahl":      st.column_config.TextColumn("Kennzahl"),
            "Wert":          st.column_config.TextColumn("Wert"),
            "Sektor-Median": st.column_config.TextColumn("Sektor-Median"),
            "vs.": st.column_config.TextColumn(
                "vs.", width="small",
                help="▲ ueber / ▼ unter / = auf Sektor-Median"),
        },
    )


# --- Kennzahlen ---
with t_kpi:
    if f is None:
        st.info("Keine Fundamentaldaten fuer dieses Instrument hinterlegt.")
    else:
        _sec = fund_all[fund_all["sector"] == f["sector"]] \
            if not _missing(f["sector"]) else fund_all.iloc[0:0]
        st.caption(f"Sektor-Median aus {len(_sec)} Unternehmen im Sektor "
                   f"„{f['sector'] or '—'}“.")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Bewertung**")
            _metric_table(_VALUATION, _sec)
            st.markdown("**Verschuldung & Liquiditaet**")
            _metric_table(_LEVERAGE, _sec)
        with c2:
            st.markdown("**Profitabilitaet**")
            _metric_table(_PROFIT, _sec)
            st.markdown("**Cash & Dividende**")
            _metric_table(_CASHDIV, _sec)


# --- Wachstum & Momentum ---
with t_growth:
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**Wachstum (5-Jahres-CAGR)**")
        if f is None:
            st.info("Keine Fundamentaldaten.")
        else:
            _grows = [{"Kennzahl": lbl, "Wert": _pct(f[col])}
                      for col, lbl, _ in _GROWTH]
            st.dataframe(pd.DataFrame(_grows), use_container_width=True,
                         hide_index=True)
            if all(_missing(f[c]) for c, _, _ in _GROWTH):
                st.caption("⚠ 5-Jahres-CAGR-Felder sind in "
                           "ref_fundamentals_latest noch nicht befuellt — "
                           "Kurs-Momentum rechts dient als Ersatz.")
    with g2:
        st.markdown("**Kurs-Momentum (annualisiert nicht — Periodenrendite)**")
        if not _returns:
            st.info("Keine Kurshistorie.")
        else:
            _mom = [{"Zeitraum": k, "Rendite": f"{v * 100:+.1f} %"}
                    for k, v in _returns.items()]
            st.dataframe(pd.DataFrame(_mom), use_container_width=True,
                         hide_index=True)
            if not held.empty and pd.notna(held["valid_from"].min()):
                st.caption(f"Im Bestand seit "
                           f"{str(held['valid_from'].min())[:10]}.")


# --- Umsatz & GuV ---
with t_guv:
    if not table_exists("ref_income_statement"):
        st.info("GuV-Daten noch nicht geladen. Modul einrichten:  \n"
                "`python -m modules.sec_filings init`  \n"
                f"`python -m modules.sec_filings fetch {symbol}`  \n"
                f"`python -m modules.sec_filings backfill {symbol}`  (Historie)")
    else:
        _hist_is = run_query("""
            SELECT period_end, form_type, currency, revenue, cost_of_revenue,
                   gross_profit, rd_expense, sga_expense, operating_expense,
                   operating_income, other_income, pretax_income,
                   tax_expense, net_income, filed_at
            FROM ref_income_statement
            WHERE ref_instrument_id = ?
            ORDER BY period_end
        """, (ref_id,))
        if _hist_is.empty:
            st.info(f"Keine GuV fuer {symbol} hinterlegt — "
                    f"`python -m modules.sec_filings fetch {symbol}` "
                    "ausfuehren.")
        else:
            _hist_is["period_end"] = pd.to_datetime(_hist_is["period_end"])
            _r   = _hist_is.iloc[-1]            # juengste Periode
            _cur = _r["currency"] or "USD"

            # Segmente: komplette Historie laden, daraus aktuelle Periode
            _seg_hist = pd.DataFrame()
            _seg_curr = pd.DataFrame()
            if table_exists("ref_revenue_segments"):
                _seg_hist = run_query("""
                    SELECT period_end, axis, member, member_label, value
                    FROM ref_revenue_segments
                    WHERE ref_instrument_id = ?
                    ORDER BY period_end, axis, value DESC
                """, (ref_id,))
                if not _seg_hist.empty:
                    _seg_hist["period_end"] = pd.to_datetime(
                        _seg_hist["period_end"])
                    _seg_curr = _seg_hist[
                        _seg_hist["period_end"] == _r["period_end"]]

            # Gemeinsame Achsen-Auswahl fuer Bar-Chart + Sankey
            _seg_axis = None
            if not _seg_hist.empty:
                from modules.sec_filings.client import (
                    AXIS_LABELS as _AX_LBL, _humanize as _hum)
                _axes = list(dict.fromkeys(_seg_hist["axis"].tolist()))
                _known = list(_AX_LBL.keys())
                _axes.sort(key=lambda a: (
                    _known.index(a) if a in _known else 99, a))
                _opts = {_AX_LBL.get(a, _hum(a)): a for a in _axes}
                _chosen = (st.radio("Umsatz-Aufschluesselung",
                                    list(_opts.keys()),
                                    horizontal=True,
                                    key=f"guv_axis_{ref_id}")
                           if len(_opts) > 1 else list(_opts.keys())[0])
                _seg_axis = _opts[_chosen]

            # Periodentyp-Filter — trennt 10-Q (Quartal) von 10-K (Jahr),
            # damit nicht Quartals- und Jahreswerte auf derselben Achse
            # gemischt werden.
            def _ptype(ft) -> str:
                s = str(ft or "").upper()
                if s.startswith("10-K"):
                    return "Jahr"
                if s.startswith("10-Q"):
                    return "Quartal"
                return "Sonstige"

            _hist_is["_ptype"] = _hist_is["form_type"].map(_ptype)
            _ptype_by_period = dict(zip(_hist_is["period_end"],
                                        _hist_is["_ptype"]))
            _avail_ptypes = [t for t in ("Quartal", "Jahr")
                             if (_hist_is["_ptype"] == t).any()]
            _ptype_sel = (
                st.radio("Periode", _avail_ptypes, horizontal=True,
                         key=f"guv_ptype_{ref_id}")
                if len(_avail_ptypes) > 1
                else (_avail_ptypes[0] if _avail_ptypes else "Quartal"))

            # ===================================================
            # Revenue Breakdown — Stacked Bar ueber alle Perioden
            # ===================================================
            st.markdown(f"##### Umsatz-Verlauf ({_cur})")

            # --- Wachstums-Kennzahlen zur gewaehlten Periode ---------------
            _rev_ser = (_hist_is[_hist_is["_ptype"] == _ptype_sel]
                        .sort_values("period_end"))
            _rv = _rev_ser["revenue"].astype(float).tolist()
            _rp = _rev_ser["period_end"].tolist()
            _is_q = _ptype_sel == "Quartal"
            _lag = 4 if _is_q else 1            # YoY-Versatz in Perioden

            def _grow(cur, base):
                if (cur is None or base is None
                        or _missing(cur) or _missing(base) or base == 0):
                    return None
                return float(cur) / float(base) - 1.0

            _last = _rv[-1] if _rv else None
            _seq = _grow(_last, _rv[-2]) if len(_rv) >= 2 else None
            _yoy = _grow(_last, _rv[-1 - _lag]) if len(_rv) > _lag else None
            _cagr = None
            if len(_rv) >= 2 and _rv[0] > 0 and _last and _last > 0:
                _yrs = (_rp[-1] - _rp[0]).days / 365.25
                if _yrs >= 1.0:
                    _cagr = (_last / _rv[0]) ** (1.0 / _yrs) - 1.0

            def _dlt(g, suffix):
                if g is None:
                    return None
                return f"{'+' if g >= 0 else ''}{_pct(g)} {suffix}"

            _mc1, _mc2, _mc3 = st.columns(3)
            _mc1.metric(
                "Umsatz (letzte Periode)",
                _fmt_money_big(_last, _cur) if _last is not None else "—",
                delta=_dlt(_seq, "ggü. Vorquartal") if _is_q else None)
            _mc2.metric("Wachstum YoY",
                        _pct(_yoy) if _yoy is not None else "—")
            _mc3.metric("CAGR p.a.",
                        _pct(_cagr) if _cagr is not None else "—")

            _PAL = ["#0F6E56", "#1D9E75", "#5DCAA5", "#9FE1CB",
                    "#3B6D11", "#639922", "#97C459", "#C0DD97"]
            if _seg_axis is not None:
                _seg_sel = _seg_hist[_seg_hist["axis"] == _seg_axis].copy()
                _seg_sel = _seg_sel[_seg_sel["period_end"].map(
                    _ptype_by_period).eq(_ptype_sel)]
                _periods = sorted(_seg_sel["period_end"].unique())
                if _periods:
                    _mem_totals = (_seg_sel.groupby("member_label")["value"]
                                   .sum().sort_values(ascending=False))
                    _members = _mem_totals.index.tolist()
                    _pivot = (_seg_sel
                              .pivot_table(index="period_end",
                                           columns="member_label",
                                           values="value",
                                           aggfunc="first")
                              .reindex(_periods)[_members])
                    _rev_by_p = (_hist_is.set_index("period_end")["revenue"]
                                 .reindex(_periods))
                    _other = _rev_by_p - _pivot.sum(axis=1)

                    _fig_bar = go.Figure()
                    for _i, _m in enumerate(_members):
                        _fig_bar.add_trace(go.Bar(
                            name=_m, x=_pivot.index, y=_pivot[_m],
                            marker_color=_PAL[_i % len(_PAL)],
                            hovertemplate=(f"%{{x|%Y-%m-%d}}<br>{_m}: "
                                           "%{y:,.0f}<extra></extra>"),
                        ))
                    if _other.notna().any() and (_other.fillna(0) > 1).any():
                        _fig_bar.add_trace(go.Bar(
                            name="Sonstige",
                            x=_other.index, y=_other.values,
                            marker_color="#B4B2A9",
                            hovertemplate=("%{x|%Y-%m-%d}<br>Sonstige: "
                                           "%{y:,.0f}<extra></extra>"),
                        ))
                    _fig_bar.update_layout(
                        barmode="stack", height=380,
                        margin=dict(l=10, r=10, t=10, b=10),
                        legend=dict(orientation="h", y=-0.18),
                        yaxis_title=f"Umsatz ({_cur})",
                        hovermode="x unified",
                    )
                    st.plotly_chart(_fig_bar, use_container_width=True)
                    if len(_periods) < 4:
                        st.caption(
                            f"Nur {len(_periods)} Periode(n) hinterlegt — "
                            "mehr Historie via "
                            f"`python -m modules.sec_filings backfill "
                            f"{symbol} --quarters 20`.")
                else:
                    st.info("Keine Segment-Daten in dieser Achse.")
            else:
                # Keine Segmente — einfacher Umsatz-Verlauf
                _hist_p = _hist_is[_hist_is["_ptype"] == _ptype_sel]
                _fig_bar = go.Figure(go.Bar(
                    x=_hist_p["period_end"],
                    y=_hist_p["revenue"],
                    marker_color=_PAL[0],
                    hovertemplate=("%{x|%Y-%m-%d}<br>Umsatz: "
                                   "%{y:,.0f}<extra></extra>"),
                ))
                _fig_bar.update_layout(
                    height=380, margin=dict(l=10, r=10, t=10, b=10),
                    yaxis_title=f"Umsatz ({_cur})")
                st.plotly_chart(_fig_bar, use_container_width=True)
                st.caption("Noch keine Segment-Aufschluesselung. Mehr "
                           "Historie + Segmente via "
                           f"`python -m modules.sec_filings backfill "
                           f"{symbol} --quarters 20`.")

            # ===================================================
            # Margen-Trend — Ergebnisqualitaet ueber die Perioden
            # ===================================================
            _mt = (_hist_is[_hist_is["_ptype"] == _ptype_sel]
                   .sort_values("period_end").copy())
            if len(_mt) >= 2:
                _rev_s = _mt["revenue"].astype(float)

                def _mg(col):
                    s = _mt[col].astype(float) / _rev_s * 100.0
                    s[_rev_s <= 0] = float("nan")
                    return s

                _ptx_s = _mt["pretax_income"].astype(float)
                _eff_tax = _mt["tax_expense"].astype(float) / _ptx_s * 100.0
                _eff_tax[_ptx_s <= 0] = float("nan")

                st.markdown("##### Margen-Trend")
                _figm = go.Figure()
                for _name, _ser, _col in [
                    ("Bruttomarge",     _mg("gross_profit"),     "#0F6E56"),
                    ("Operative Marge", _mg("operating_income"), "#1D9E75"),
                    ("Nettomarge",      _mg("net_income"),       "#5DCAA5"),
                    ("F&E-Quote",       _mg("rd_expense"),       "#A32D2D"),
                    ("Steuerquote",     _eff_tax,                "#B4862B"),
                ]:
                    _figm.add_trace(go.Scatter(
                        x=_mt["period_end"], y=_ser, name=_name,
                        mode="lines+markers",
                        line=dict(color=_col, width=2), connectgaps=False,
                        hovertemplate=(f"%{{x|%Y-%m-%d}}<br>{_name}: "
                                       "%{y:.1f}%<extra></extra>"),
                    ))
                _figm.update_layout(
                    height=320, margin=dict(l=10, r=10, t=10, b=10),
                    legend=dict(orientation="h", y=-0.2),
                    yaxis_title="%", hovermode="x unified")
                st.plotly_chart(_figm, use_container_width=True)
                st.caption("Margen = Anteil am Umsatz; Steuerquote = "
                           "Steuern / Vorsteuerergebnis (effektiv). "
                           "Stabile bzw. steigende Margen = hoehere "
                           "Ergebnisqualitaet.")

            st.divider()

            # ===================================================
            # Sankey — GuV-Struktur einer waehlbaren Periode
            # ===================================================
            _per_df = _hist_is.sort_values("period_end")
            _per_opts = list(_per_df["period_end"])

            def _per_lbl(ts):
                _row = _per_df[_per_df["period_end"] == ts].iloc[0]
                return f"{str(ts)[:10]} · {_row['_ptype']}"

            _sel_per = st.selectbox(
                "Periode (Sankey)", _per_opts,
                index=len(_per_opts) - 1, format_func=_per_lbl,
                key=f"guv_sankey_per_{ref_id}")
            _r = _per_df[_per_df["period_end"] == _sel_per].iloc[0]
            _seg_curr = (
                _seg_hist[_seg_hist["period_end"] == _sel_per]
                if not _seg_hist.empty else pd.DataFrame())

            st.markdown(
                f"##### GuV-Struktur — {str(_r['period_end'])[:10]}")
            _seg_rows: list[tuple[str, float]] = []
            if _seg_axis is not None and not _seg_curr.empty:
                _cur_ax = _seg_curr[_seg_curr["axis"] == _seg_axis]
                _seg_rows = [(r["member_label"] or r["member"],
                              float(r["value"]))
                             for _, r in _cur_ax.iterrows()]

            def _g(col):
                v = _r[col]
                return None if _missing(v) else float(v)

            _rev   = _g("revenue");           _cogs  = _g("cost_of_revenue")
            _gross = _g("gross_profit");       _rd    = _g("rd_expense")
            _sga   = _g("sga_expense");        _opex  = _g("operating_expense")
            _opinc = _g("operating_income");   _other = _g("other_income")
            _ptax  = _g("pretax_income");      _tax   = _g("tax_expense")
            _net   = _g("net_income")

            _GREEN, _RED, _GRAY = "#3B6D11", "#A32D2D", "#444441"
            _GL = "rgba(99,153,34,0.45)"
            _RL = "rgba(225,75,74,0.40)"

            _labels: list[str] = []
            _colors: list[str] = []
            _idx: dict[str, int] = {}

            def _node(key, name, val, color, *, force=False):
                if val is None and not force:
                    return
                _idx[key] = len(_labels)
                _labels.append(f"{name}<br>{_fmt_money_big(val, _cur)}")
                _colors.append(color)

            _node("rev",   "Umsatz",                _rev,   _GRAY, force=True)
            # Segment-Knoten (Umsatz-Quellen, fliessen in 'Umsatz')
            _SEG_FILL = "#0F6E56"
            for _i, (_lbl, _val) in enumerate(_seg_rows):
                _node(f"s{_i}", _lbl, _val, _SEG_FILL)
            _node("cogs",  "Herstellkosten",        _cogs,  _RED)
            _node("gross", "Bruttogewinn",          _gross, _GREEN)
            _node("opex",  "Betriebsaufwand",       _opex,  _RED)
            _node("rd",    "F&E",                   _rd,    _RED)
            _node("sga",   "Vertrieb & Verwaltung", _sga,   _RED)
            _node("opinc", "Operatives Ergebnis",   _opinc, _GREEN)
            _has_other = _other is not None and _other > 0
            if _has_other:
                _node("other", "Sonstiges Ergebnis", _other, _GREEN)
            _has_ptax = _ptax is not None
            if _has_ptax:
                _node("ptax", "Vorsteuerergebnis", _ptax, _GREEN)
            _node("tax", "Steuern",     _tax, _RED)
            _node("net", "Nettogewinn", _net, _GREEN, force=True)

            _S: list[int] = []
            _T: list[int] = []
            _V: list[float] = []
            _LC: list[str] = []

            def _link(a, b, val, color):
                if val is None or val <= 0 or a not in _idx or b not in _idx:
                    return
                _S.append(_idx[a]); _T.append(_idx[b])
                _V.append(val);     _LC.append(color)

            # Segment -> Umsatz (linke Auffaecherung)
            _SL = "rgba(29,158,117,0.45)"
            for _i, (_, _val) in enumerate(_seg_rows):
                _link(f"s{_i}", "rev", _val, _SL)
            _link("rev",   "cogs",  _cogs,  _RL)
            _link("rev",   "gross", _gross, _GL)
            _link("gross", "opex",  _opex,  _RL)
            _link("gross", "opinc", _opinc, _GL)
            _link("opex",  "rd",    _rd,    _RL)
            _link("opex",  "sga",   _sga,   _RL)
            if _has_ptax:
                _link("opinc", "ptax", _opinc, _GL)
                if _has_other:
                    _link("other", "ptax", _other, _GL)
                _link("ptax", "tax", _tax, _RL)
                _link("ptax", "net", _net, _GL)
            else:
                _link("opinc", "tax", _tax, _RL)
                _link("opinc", "net", _net, _GL)

            if not _V:
                st.info("GuV-Zeilen unvollstaendig — kein Sankey moeglich.")
            else:
                # Hoehe an Knotenzahl koppeln, damit Labels nicht ueberlappen
                _n_nodes = len(_labels)
                _sankey_h = min(900, max(440, _n_nodes * 46))
                _fig = go.Figure(go.Sankey(
                    arrangement="snap",
                    textfont=dict(color="#10231A", size=13, weight=600,
                                  family="Arial, sans-serif"),
                    node=dict(label=_labels, color=_colors,
                              pad=26, thickness=16,
                              line=dict(color="white", width=1)),
                    link=dict(source=_S, target=_T, value=_V, color=_LC),
                ))
                _fig.update_layout(height=_sankey_h,
                                   margin=dict(l=10, r=10, t=10, b=10))
                st.plotly_chart(_fig, use_container_width=True)

                # --- Ergebnisqualitaet: Margen aus den Sankey-Fluessen ----
                def _ratio_of(num, den):
                    if (num is None or den is None
                            or _missing(num) or _missing(den)
                            or float(den) == 0):
                        return None
                    return float(num) / float(den)

                _q_brutto = _ratio_of(_gross, _rev)
                _q_op = _ratio_of(_opinc, _rev)
                _q_net = _ratio_of(_net, _rev)
                _q_rd = _ratio_of(_rd, _rev)
                _tax_base = _ptax if _ptax else (
                    (_net + _tax) if (_net is not None and _tax is not None)
                    else None)
                _q_tax = _ratio_of(_tax, _tax_base)

                st.markdown("**Ergebnisqualitaet**")
                _qc = st.columns(5)
                _qc[0].metric("Bruttomarge",
                              _pct(_q_brutto) if _q_brutto is not None else "—",
                              help="Bruttogewinn / Umsatz")
                _qc[1].metric("Operative Marge",
                              _pct(_q_op) if _q_op is not None else "—",
                              help="Operatives Ergebnis / Umsatz")
                _qc[2].metric("Nettomarge",
                              _pct(_q_net) if _q_net is not None else "—",
                              help="Nettogewinn / Umsatz")
                _qc[3].metric("F&E-Quote",
                              _pct(_q_rd) if _q_rd is not None else "—",
                              help="F&E-Aufwand / Umsatz")
                _qc[4].metric("Steuerquote",
                              _pct(_q_tax) if _q_tax is not None else "—",
                              help="Steuern / Vorsteuerergebnis (effektiv)")

                st.caption(
                    f"{_r['form_type'] or 'Filing'} · Berichtsperiode "
                    f"{str(_r['period_end'])[:10]} · eingereicht "
                    f"{str(_r['filed_at'])[:10]} · Quelle sec-api.io. "
                    f"Betraege wie im Filing berichtet ({_cur}); "
                    f"Bandbreite proportional zum Betrag.")
                if any(v is None for v in (_rev, _gross, _opinc, _net)):
                    st.caption("⚠ Einzelne Kernzeilen fehlten im XBRL und "
                               "wurden ausgelassen.")


# --- Chart ---
with t_chart:
    if _px_hist.empty:
        st.info("Keine Kursdaten fuer dieses Instrument.")
    else:
        _tf = st.radio("Zeitfenster", ["90 Tage", "1 Jahr", "Max"],
                       horizontal=True, key="thesis_chart_tf")
        _days = {"90 Tage": 90, "1 Jahr": 365, "Max": 100_000}[_tf]
        _cut = _px_hist["ts"].max() - pd.Timedelta(days=_days)
        _view = _px_hist[_px_hist["ts"] >= _cut]
        if _view.empty:
            st.info("Keine Kursdaten im gewaehlten Fenster.")
        else:
            st.line_chart(_view.set_index("ts")["close"], height=340)
            st.caption(f"{len(_view)} Handelstage · Schlusskurs.")


# --- Branche & Dominanz ---
with t_peers:
    if f is None or _missing(f["sector"]):
        st.info("Kein Sektor hinterlegt — keine Branchen-Einordnung moeglich.")
    else:
        peers = fund_all[fund_all["sector"] == f["sector"]].copy()
        peers = peers.merge(
            run_query("SELECT ref_instrument_id, symbol, name "
                      "FROM ref_instruments"),
            on="ref_instrument_id", how="left")
        peers = peers.sort_values("market_cap", ascending=False,
                                  na_position="last").reset_index(drop=True)
        _rank = peers.index[peers["ref_instrument_id"] == ref_id]
        _sec_cap = peers["market_cap"].sum(skipna=True)
        _self_cap = float(f["market_cap"]) if not _missing(f["market_cap"]) \
            else None
        d1, d2, d3 = st.columns(3)
        d1.metric("Rang im Sektor",
                  f"{int(_rank[0]) + 1} / {len(peers)}" if len(_rank)
                  else f"— / {len(peers)}",
                  help=f"Nach Market Cap, Sektor „{f['sector']}“.")
        d2.metric("Sektor-Anteil",
                  f"{de_dec(_self_cap / _sec_cap * 100, 1)} %"
                  if _self_cap and _sec_cap else "—",
                  help="Market Cap im Verhaeltnis zur Summe aller "
                       "erfassten Sektor-Unternehmen.")
        d3.metric("Market Cap", _fmt_cap(_self_cap))

        _disp = peers[["ref_instrument_id", "symbol", "name", "market_cap",
                       "pe_forward", "net_margin", "roic"]].copy()
        _disp["net_margin"] = _disp["net_margin"] * 100.0
        _disp["roic"]       = _disp["roic"] * 100.0

        def _hl(r):
            on = r["ref_instrument_id"] == ref_id
            return ["background-color: #fff3cd" if on else ""] * len(r)

        st.dataframe(
            _disp.style.apply(_hl, axis=1).format({
                "market_cap": _fmt_cap,
                "pe_forward": lambda v: de_dec(v, 1) if not _missing(v) else "—",
                "net_margin": lambda v: de_dec(v, 1) if not _missing(v) else "—",
                "roic":       lambda v: de_dec(v, 1) if not _missing(v) else "—",
            }),
            use_container_width=True, hide_index=True,
            column_config={
                "ref_instrument_id": None,
                "symbol":     st.column_config.TextColumn("Symbol", width="small"),
                "name":       st.column_config.TextColumn("Name"),
                "market_cap": st.column_config.TextColumn("Market Cap"),
                "pe_forward": st.column_config.TextColumn("KGV fwd", width="small"),
                "net_margin": st.column_config.TextColumn("Nettom. %", width="small"),
                "roic":       st.column_config.TextColumn("ROIC %", width="small"),
            },
        )
        st.caption("Dominanz-Indikator: Rang + Sektor-Anteil messen die "
                   "relative Groesse — fuer Marktfuehrerschaft zusaetzlich "
                   "Margen und ROIC gegen die Peers lesen.")


# --- Termine & News ---
with t_news:
    st.markdown("**Naechster Earnings-Termin**")
    if not table_exists("ref_earnings_calendar"):
        st.info("Keine Earnings-Kalender-Tabelle vorhanden.")
    else:
        _ec = run_query(
            "SELECT earnings_date, source, fetched_at "
            "FROM ref_earnings_calendar WHERE ref_instrument_id = ?", (ref_id,))
        _valid = _ec[_ec["earnings_date"]
                     > pd.Timestamp("2000-01-01")] if not _ec.empty \
            else _ec
        if _valid.empty:
            st.info("Kein Earnings-Termin hinterlegt "
                    "(Kalender deckt nur wenige Namen ab).")
        else:
            _ed = pd.to_datetime(_valid["earnings_date"].iloc[0]).date()
            _delta = (_ed - date.today()).days
            _when = (f"in {_delta} Tagen" if _delta > 0
                     else ("heute" if _delta == 0
                           else f"vor {abs(_delta)} Tagen"))
            st.metric(f"{symbol} — Earnings", _ed.isoformat(), delta=_when,
                      delta_color="off")
            st.caption(f"Quelle: {_valid['source'].iloc[0]}.")

    st.divider()
    st.markdown("**News**")
    if not table_exists("ref_sa_articles"):
        st.info("Keine News-Tabellen vorhanden.")
    else:
        _news = run_query("""
            SELECT a.ts, a.title, a.summary, a.url, a.source
            FROM ref_sa_article_symbols s
            JOIN ref_sa_articles a USING (article_id)
            WHERE s.ref_instrument_id = ?
            ORDER BY a.ts DESC LIMIT 20
        """, (ref_id,))
        if _news.empty:
            st.info("Keine News zu diesem Instrument.")
        else:
            st.caption(f"{len(_news)} aktuellste Artikel.")
            for _, _a in _news.iterrows():
                _title = _a["title"] or "(ohne Titel)"
                if _a["url"]:
                    st.markdown(f"**[{_title}]({_a['url']})**")
                else:
                    st.markdown(f"**{_title}**")
                st.caption(f"{_a['ts']}  ·  {_a['source'] or ''}")
                if _a["summary"]:
                    st.write(_a["summary"])
                st.divider()


# --- Signale ---
with t_sig:
    st.markdown("**Empfehlungen**")
    if not table_exists("sig_recommendations"):
        st.info("Keine Recommendation-Tabelle vorhanden.")
    else:
        _sig = run_query("""
            SELECT ts, action, priority, category, title, rationale
            FROM sig_recommendations WHERE ref_instrument_id = ?
            ORDER BY ts DESC
        """, (ref_id,))
        if _sig.empty:
            st.info("Keine Empfehlungen zu diesem Instrument.")
        else:
            _pic = {"high": "🔴", "medium": "🟠", "low": "🟢"}
            for _, _r in _sig.iterrows():
                st.markdown(
                    f"{_pic.get(_r['priority'], '·')} `{_r['action']}`  ·  "
                    f"_{_r['category'] or ''}_  ·  {_r['ts']}  \n"
                    f"**{_r['title'] or ''}**  \n"
                    f"{_r['rationale'] or ''}")
                st.divider()

    st.markdown("**Alerts**")
    if not table_exists("sig_alerts"):
        st.info("Keine Alert-Tabelle vorhanden.")
    else:
        _al = run_query("""
            SELECT ts, rule_name, direction, trigger_value, threshold
            FROM sig_alerts WHERE ref_instrument_id = ?
            ORDER BY ts DESC LIMIT 50
        """, (ref_id,))
        if _al.empty:
            st.info("Keine Alerts zu diesem Instrument.")
        else:
            st.dataframe(
                _al.style.format({"trigger_value": de_dec,
                                  "threshold": de_dec}),
                use_container_width=True, hide_index=True,
                column_config={
                    "ts":            st.column_config.DateColumn("Datum"),
                    "rule_name":     st.column_config.TextColumn("Regel"),
                    "direction":     st.column_config.TextColumn("Richtung",
                                                                 width="small"),
                    "trigger_value": "Trigger",
                    "threshold":     "Schwelle",
                },
            )


# --- Screener ---
with t_screen:
    from modules.dashboard.views import _screener_detail
    _screener_detail.render(ref_id, symbol)
