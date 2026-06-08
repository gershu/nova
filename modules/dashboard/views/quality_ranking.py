"""Qualitaets-Ranking — universumsweite Sicht auf den Gesamt-Qualitaets-Score.

Konsumiert: ref_quality_score (vorberechnet, `python -m modules.quality_score
run`) LEFT JOIN ref_instruments (Name) + ref_fundamentals_latest (Sektor) +
ref_quality_narrative (LLM Red-Flag/Einordnung). Score 0..100 = gewichteter
Anteil erfuellter Shearn-Kriterien ueber 5 Themen; sub_* sind die Teil-Scores
0..1 je Thema. Sortierbar = der zweite, qualitaetsgetriebene Screener.

Read-only.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.dashboard.db import run_query, table_exists


st.title("🏅 Qualitaets-Ranking")
st.caption("Gesamt-Qualitaets-Score (Shearn-5-Themen) ueber das ganze "
           "Universum — gewichteter Anteil erfuellter Kriterien. Heuristik, "
           "kein Anlageurteil.")


# ---------- Existence ----------

if not table_exists("ref_quality_score"):
    st.warning("Tabelle `ref_quality_score` existiert nicht.")
    st.info("Batch: `python -m modules.quality_score run`")
    st.stop()


# ---------- Daten laden ----------

_SUBS = [
    ("sub_return_on_capital", "ROC"),
    ("sub_balance_sheet",     "Bilanz"),
    ("sub_stock_based_comp",  "SBC"),
    ("sub_gaap_vs_non_gaap",  "GAAP"),
    ("sub_insider",           "Insider"),
]
_STRONG, _WEAK = 70, 40

has_fund = table_exists("ref_fundamentals_latest")
has_narr = table_exists("ref_quality_narrative")

sub_cols = ", ".join(f"q.{c}" for c, _ in _SUBS)
sel_sector = "f.sector AS sector" if has_fund else "NULL AS sector"
join_fund = ("LEFT JOIN ref_fundamentals_latest f "
             "ON f.ref_instrument_id = q.ref_instrument_id" if has_fund else "")
sel_narr = ("n.red_flag AS red_flag, n.narrative AS narrative, "
            "n.generated_at AS narr_at"
            if has_narr else
            "NULL AS red_flag, NULL AS narrative, NULL AS narr_at")
join_narr = ("LEFT JOIN ref_quality_narrative n "
             "ON n.ref_instrument_id = q.ref_instrument_id" if has_narr else "")

sql = f"""
    SELECT q.ref_instrument_id, i.symbol, i.name, {sel_sector},
           q.score, q.n_ok, {sub_cols},
           q.n_years, q.period, q.computed_at,
           {sel_narr}
    FROM ref_quality_score q
    LEFT JOIN ref_instruments i ON i.ref_instrument_id = q.ref_instrument_id
    {join_fund}
    {join_narr}
    WHERE q.score IS NOT NULL
    ORDER BY q.score DESC
"""
df = run_query(sql, None)

if df is None or df.empty:
    st.info("Noch keine berechneten Scores. Batch: "
            "`python -m modules.quality_score run`.")
    st.stop()

# Teil-Scores 0..1 -> 0..100 (NaN bleibt NaN)
for c, _ in _SUBS:
    df[c] = df[c] * 100.0


# ---------- Filter-UI ----------

f1, f2, f3, f4 = st.columns([1.4, 1.2, 1.6, 1.4])
with f1:
    min_score = st.slider("Min. Score", 0, 100, 0, step=5)
with f2:
    only_portfolio = st.checkbox(
        "Nur Portfolio", value=False,
        help="Filtert auf ref_instrument_id in pos_holdings (offen).")
with f3:
    sectors = sorted([s for s in df["sector"].dropna().unique().tolist()])
    sector_choice = st.multiselect("Sektor", sectors, default=[])
with f4:
    search = st.text_input("Suche (Symbol / Name)", "", placeholder="z.B. AAPL")

portfolio_ids = set()
if only_portfolio and table_exists("pos_holdings"):
    pf = run_query("SELECT DISTINCT ref_instrument_id FROM pos_holdings "
                   "WHERE valid_to IS NULL", None)
    if pf is not None and not pf.empty:
        portfolio_ids = set(pf["ref_instrument_id"].tolist())

view = df[df["score"] >= min_score].copy()
if only_portfolio:
    view = view[view["ref_instrument_id"].isin(portfolio_ids)]
if sector_choice:
    view = view[view["sector"].isin(sector_choice)]
if search:
    m = (view["symbol"].astype(str).str.contains(search, case=False, na=False)
         | view["name"].astype(str).str.contains(search, case=False, na=False))
    view = view[m]


# ---------- KPI-Header ----------

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Werte", f"{len(view)}")
k2.metric("Ø Score", f"{view['score'].mean():.0f}" if len(view) else "—")
k3.metric(f"≥ {_STRONG} (stark)", int((view["score"] >= _STRONG).sum()))
k4.metric(f"< {_WEAK} (schwach)", int((view["score"] < _WEAK).sum()))
k5.metric("mit Red-Flag",
          int(view["red_flag"].astype(str).str.len().gt(0).sum())
          if "red_flag" in view else 0)

stand = str(df["computed_at"].max())[:16]
st.caption(f"{len(view)} von {len(df)} Werten · Stand der Berechnung: {stand} "
           f"· Lookback {df['n_years'].iloc[0]}J / {df['period'].iloc[0]}.")
st.divider()


# ---------- Ranking-Tabelle (sortierbar, Row-Detail) ----------

show = ["symbol", "name", "sector", "score", "n_ok",
        *[c for c, _ in _SUBS], "red_flag"]
disp = view[["ref_instrument_id", *show]].copy()

colcfg = {
    "ref_instrument_id": None,
    "symbol":  st.column_config.TextColumn("Symbol", width="small"),
    "name":    st.column_config.TextColumn("Name"),
    "sector":  st.column_config.TextColumn("Sektor", width="medium"),
    "score":   st.column_config.ProgressColumn(
        "Score", min_value=0, max_value=100, format="%d"),
    "n_ok":    st.column_config.NumberColumn("Themen", width="small",
                                             help="auswertbare Themen (0..5)"),
    "red_flag": st.column_config.TextColumn("Red Flag (LLM)", width="large"),
}
for c, lbl in _SUBS:
    colcfg[c] = st.column_config.NumberColumn(lbl, format="%d", width="small",
                                              help=f"Teil-Score {lbl} (0..100)")

evt = st.dataframe(
    disp, use_container_width=True, hide_index=True,
    height=min(640, 48 + 34 * len(disp)),
    on_select="rerun", selection_mode="single-row", key="qrank_tbl",
    column_config=colcfg)

sel = evt.selection["rows"]
if sel:
    r = view.iloc[sel[0]]
    with st.expander(f"🔎 {r['symbol']} — {r['name'] or ''}", expanded=True):
        band = ("hohe Qualitaet" if r["score"] >= _STRONG
                else "gemischt" if r["score"] >= _WEAK else "schwach")
        st.markdown(f"### {int(r['score'])}/100 — {band}")
        if has_narr and isinstance(r.get("narrative"), str) and r["narrative"]:
            st.markdown(f"🧠 **LLM-Einordnung:** {r['narrative']}")
            if isinstance(r.get("red_flag"), str) and r["red_flag"]:
                st.markdown(f"⚠ **Red Flag:** {r['red_flag']}")
            st.caption(f"Vorberechnet, Stand {str(r.get('narr_at'))[:16]}.")
        bar = pd.DataFrame([{"Thema": lbl, "Score": r[c]}
                            for c, lbl in _SUBS])
        st.bar_chart(bar.set_index("Thema"), height=240)
        st.caption("Teil-Scores 0..100 je Thema. Fehlende Themen sind aus dem "
                   "Gesamt-Score ausgeklammert (Gewichte renormiert).")
