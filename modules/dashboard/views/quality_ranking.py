"""Qualitaets-Ranking — universumsweite GuruFocus-Sicht (Pfad A).

Konsumiert: ref_gf_score (vorberechnet, `python -m modules.gurufocus
ingest-scores`). GF-Score (0..100), GF-Value/Bewertung, Ränge (Financial
Strength / Profitability / Growth), Predictability, Piotroski-F / Altman-Z /
Beneish-M, Moat, ROIC vs. WACC. Sortierbar = qualitaetsgetriebener Screener.

Read-only. Heuristik, kein Anlageurteil.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.dashboard.db import run_query, table_exists


st.title("🏅 Qualitaets-Ranking (GuruFocus)")
st.caption("GF-Score + Ränge über das Universum. Heuristik, kein Anlageurteil.")

if not table_exists("ref_gf_score"):
    st.warning("Tabelle `ref_gf_score` existiert nicht.")
    st.info("Batch: `python -m modules.gurufocus ingest-scores`")
    st.stop()

_STRONG, _WEAK = 80, 50   # GF-Score-Bänder (0..100)

df = run_query(
    "SELECT symbol, name, sector, gf_score, gf_value, price_to_gf_value, "
    "gf_valuation, rank_financial_strength, rank_profitability, rank_growth, "
    "predictability, fscore, zscore, mscore, moat_score, roic, wacc, "
    "computed_at FROM ref_gf_score WHERE gf_score IS NOT NULL "
    "ORDER BY gf_score DESC", None)
if df is None or df.empty:
    st.info("Noch keine GF-Scores berechnet. Batch laufen lassen.")
    st.stop()


# ---------- Filter ----------

f1, f2, f3, f4 = st.columns([1.3, 1.2, 1.6, 1.4])
with f1:
    min_score = st.slider("Min. GF-Score", 0, 100, 0, step=5)
with f2:
    undervalued = st.checkbox("Nur unterbewertet", value=False,
                              help="price_to_gf_value < 1 (Kurs unter GF-Value).")
with f3:
    sectors = sorted([s for s in df["sector"].dropna().unique().tolist()])
    sector_choice = st.multiselect("Sektor", sectors, default=[])
with f4:
    search = st.text_input("Suche (Symbol / Name)", "", placeholder="z.B. AAPL")

view = df[df["gf_score"] >= min_score].copy()
if undervalued:
    view = view[view["price_to_gf_value"] < 1.0]
if sector_choice:
    view = view[view["sector"].isin(sector_choice)]
if search:
    m = (view["symbol"].astype(str).str.contains(search, case=False, na=False)
         | view["name"].astype(str).str.contains(search, case=False, na=False))
    view = view[m]


# ---------- KPI-Header ----------

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Werte", f"{len(view)}")
k2.metric("Ø GF-Score", f"{view['gf_score'].mean():.0f}" if len(view) else "—")
k3.metric(f"≥ {_STRONG}", int((view["gf_score"] >= _STRONG).sum()))
k4.metric(f"< {_WEAK}", int((view["gf_score"] < _WEAK).sum()))
k5.metric("unterbewertet",
          int((pd.to_numeric(view["price_to_gf_value"], errors="coerce")
               < 1.0).sum()))
stand = str(df["computed_at"].max())[:16]
st.caption(f"{len(view)} von {len(df)} Werten · Stand {stand} · Quelle GuruFocus.")
st.divider()


# ---------- Tabelle ----------

view["roic_wacc"] = view["roic"] - view["wacc"]   # Spread (Wertschöpfung)
show = ["symbol", "name", "sector", "gf_score", "gf_valuation",
        "price_to_gf_value", "rank_financial_strength", "rank_profitability",
        "rank_growth", "predictability", "fscore", "zscore", "moat_score",
        "roic_wacc"]
st.dataframe(
    view[show], use_container_width=True, hide_index=True,
    height=min(680, 48 + 34 * len(view)),
    column_config={
        "symbol":  st.column_config.TextColumn("Symbol", width="small"),
        "name":    st.column_config.TextColumn("Name"),
        "sector":  st.column_config.TextColumn("Sektor", width="medium"),
        "gf_score": st.column_config.ProgressColumn(
            "GF-Score", min_value=0, max_value=100, format="%d"),
        "gf_valuation": st.column_config.TextColumn("Bewertung", width="medium"),
        "price_to_gf_value": st.column_config.NumberColumn(
            "Kurs/GF-Value", format="%.2f"),
        "rank_financial_strength": st.column_config.NumberColumn(
            "Fin.Stärke", format="%d", help="Rang 1..10"),
        "rank_profitability": st.column_config.NumberColumn(
            "Profit.", format="%d", help="Rang 1..10"),
        "rank_growth": st.column_config.NumberColumn("Wachstum", format="%d"),
        "predictability": st.column_config.NumberColumn(
            "Predict.", format="%.1f", help="0..5"),
        "fscore": st.column_config.NumberColumn("F-Score", format="%d",
                                                help="Piotroski 0..9"),
        "zscore": st.column_config.NumberColumn("Z-Score", format="%.1f",
                                                help="Altman (>3 sicher)"),
        "moat_score": st.column_config.NumberColumn("Moat", format="%d"),
        "roic_wacc": st.column_config.NumberColumn(
            "ROIC−WACC", format="%.1f", help="Spread in %-Punkten (>0 = "
            "Wertschöpfung)"),
    })
st.caption("GF-Score = GuruFocus-Gesamtrang. ROIC−WACC > 0 schafft Wert. "
           "F-Score (Piotroski) / Z-Score (Altman) als Qualitäts-/Solvenz-"
           "Checks. Heuristik, kein Anlageurteil.")
