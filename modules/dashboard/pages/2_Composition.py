"""Page 2 — Portfolio Composition (EUR-Aggregat).

Konsumiert:
  - v_mkt_holdings  (Allokation, HHI, Top-Positions, Korrelation)
  - v_mkt_portfolio (Portfolio-Views-Rubrik — Summen + Detail pro View)
  - mkt_quotes_daily (Korrelations-Matrix)

Strategie: alle Allokations-Metriken aggregieren in EUR (Stammwaehrung);
Top-Positions gruppiert nach `name` (statt symbol — fasst Multi-Class /
Multi-Exchange-Listings zusammen).
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from modules.dashboard.components.kpi import fmt_money
from modules.dashboard.db import run_query


st.title("🧩 Portfolio Composition")


# ---------- v_mkt_holdings laden + aggregieren ----------

try:
    mkt = run_query("SELECT * FROM v_mkt_holdings")
except Exception as e:  # noqa: BLE001
    st.error(f"View 'v_mkt_holdings' nicht verfuegbar: {e.__class__.__name__}")
    st.stop()

if mkt.empty:
    st.warning("Portfolio leer.")
    st.stop()

# Aggregation pro Position-Line (= ref_instrument_id)
agg = (
    mkt.groupby(
        ["ref_instrument_id", "symbol", "name", "asset_type", "currency"],
        dropna=False, as_index=False,
    )
    .agg(
        quantity   = ("quantity",       "sum"),
        cost_eur   = ("cost_total_eur", "sum"),
        mtm_eur    = ("mtm_eur",        "sum"),
        pnl_eur    = ("pnl_eur",        "sum"),
        mtm_native = ("mtm_native",     "sum"),
    )
)
total_mv_eur = float(agg["mtm_eur"].sum(skipna=True))
agg["weight"] = agg["mtm_eur"] / total_mv_eur if total_mv_eur > 0 else np.nan


# ---------- KPIs (EUR) ----------

weights = agg["weight"].dropna().sort_values(ascending=False)
n_pos   = len(weights)
hhi     = float((weights ** 2).sum()) if not weights.empty else 0.0
eff_n   = 1.0 / hhi if hhi > 0 else 0.0
top3    = float(weights.head(3).sum() * 100) if not weights.empty else 0.0
top5    = float(weights.head(5).sum() * 100) if not weights.empty else 0.0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total MV (EUR)", fmt_money(total_mv_eur, places=0))
k2.metric("Positions",      f"{n_pos}")
k3.metric("HHI",            f"{hhi:.3f}",
          help="0 = perfekt gleichverteilt; >0.25 = stark konzentriert")
k4.metric("Effective-N",    f"{eff_n:.1f}")
k5.metric("Top-3 / Top-5",  f"{top3:.1f}% / {top5:.1f}%")

st.divider()


# ---------- Allokation: Currency, Asset-Type, Name (Treemap) ----------

st.subheader("Allokation (EUR-Aggregat)")


def donut(series: pd.Series, title: str):
    s = series.dropna()
    if s.empty or s.sum() == 0:
        return None
    fig = px.pie(values=s.values, names=s.index, hole=0.55, title=title)
    fig.update_traces(
        textposition="inside", textinfo="percent+label",
        hovertemplate="%{label}: %{value:,.0f} EUR (%{percent})<extra></extra>",
    )
    fig.update_layout(showlegend=False, height=320,
                        margin=dict(l=10, r=10, t=40, b=10))
    return fig


c1, c2 = st.columns(2)

with c1:
    ccy_mv = agg.groupby(agg["currency"].fillna("unknown"))["mtm_eur"].sum()
    fig = donut(ccy_mv.sort_values(ascending=False), "Currency")
    if fig: st.plotly_chart(fig, use_container_width=True)
    else:   st.info("Keine Currency-Daten")

with c2:
    at_mv = agg.groupby(agg["asset_type"].fillna("unknown"))["mtm_eur"].sum()
    fig = donut(at_mv.sort_values(ascending=False), "Asset Type")
    if fig: st.plotly_chart(fig, use_container_width=True)
    else:   st.info("Keine Asset-Type-Daten")

# Treemap by Name — aggregiert Multi-Listing-Symbols zu einem Asset
by_name = (
    agg.assign(name=agg["name"].fillna("(unknown)"))
       .groupby("name", as_index=False)
       .agg(mtm_eur     = ("mtm_eur",    "sum"),
            cost_eur    = ("cost_eur",   "sum"),
            pnl_eur     = ("pnl_eur",    "sum"),
            n_lines     = ("symbol",     "nunique"),
            symbols     = ("symbol",
                            lambda s: ", ".join(sorted(set(s.dropna())))),
            currencies  = ("currency",
                            lambda s: ", ".join(sorted(set(s.dropna())))),
            asset_type  = ("asset_type", "first"))
)
by_name["weight_pct"] = by_name["mtm_eur"] / total_mv_eur * 100.0 if total_mv_eur > 0 else 0
by_name = by_name.sort_values("mtm_eur", ascending=False)

if not by_name.empty and by_name["mtm_eur"].sum() > 0:
    treemap = px.treemap(
        by_name, path=[px.Constant("Portfolio"), "asset_type", "name"],
        values="mtm_eur",
        custom_data=["weight_pct", "pnl_eur", "currencies"],
        color="pnl_eur", color_continuous_scale="RdYlGn",
        color_continuous_midpoint=0,
        title="Holdings nach Name (Asset-Type → Name, Flaeche = MV EUR, Farbe = PnL EUR)",
    )
    treemap.update_traces(
        textinfo="label+value+percent root",
        hovertemplate=(
            "<b>%{label}</b><br>"
            "MV: %{value:,.0f} EUR<br>"
            "Weight: %{customdata[0]:.2f}%<br>"
            "PnL: %{customdata[1]:,.0f} EUR<br>"
            "Currencies: %{customdata[2]}<extra></extra>"
        ),
    )
    treemap.update_layout(height=460, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(treemap, use_container_width=True)

st.divider()


# ---------- Top-15 Positions by Weight (gruppiert nach Name) ----------

st.subheader("Top-15 Positions by Weight (gruppiert nach Name, EUR)")
top = by_name.head(15)
if not top.empty and top["mtm_eur"].sum() > 0:
    bar = px.bar(
        top.iloc[::-1], x="mtm_eur", y="name", orientation="h",
        hover_data={"weight_pct": ":.2f", "mtm_eur": ":,.0f",
                     "n_lines": True, "currencies": True},
        color="weight_pct", color_continuous_scale="Blues",
        labels={"mtm_eur": "MV (EUR)", "name": ""},
    )
    bar.update_layout(height=520, margin=dict(l=10, r=20, t=20, b=20),
                       coloraxis_showscale=False)
    st.plotly_chart(bar, use_container_width=True)
    st.caption("`n_lines` = wieviele Symbol-Listings zu diesem Namen aggregiert.")
else:
    st.info("Keine Positions mit MV > 0.")

st.divider()


# ---------- Portfolio Views (aus v_mkt_portfolio) ----------

st.subheader("Portfolio Views")

try:
    pv = run_query("SELECT * FROM v_mkt_portfolio")
except Exception as e:  # noqa: BLE001
    st.error(f"View 'v_mkt_portfolio' nicht verfuegbar: {e.__class__.__name__}")
    pv = pd.DataFrame()

if pv.empty:
    st.info("Keine Portfolio-Views definiert oder ohne Members. "
            "Pflege via `modules.db_edit` auf `list_portfolio_views` + "
            "`list_portfolio_view_members`.")
else:
    # Summen pro view_name. Member-Identitaet = (ref_instrument_id, broker)
    sums = (pv.groupby(["view_id", "view_name", "view_color"], dropna=False,
                        as_index=False)
              .agg(n_members   = ("ref_instrument_id", "count"),
                   cost_eur    = ("cost_total_eur",    "sum"),
                   mtm_eur     = ("mtm_eur",           "sum"),
                   pnl_eur     = ("pnl_eur",           "sum")))
    sums["pnl_pct"] = sums["pnl_eur"] / sums["cost_eur"].where(sums["cost_eur"] != 0) * 100.0
    sums = sums.sort_values("mtm_eur", ascending=False)

    st.markdown("**Summen pro View (EUR)**")
    st.dataframe(
        sums[["view_name", "n_members",
              "cost_eur", "mtm_eur", "pnl_eur", "pnl_pct"]],
        use_container_width=True, hide_index=True,
        column_config={
            "view_name":    st.column_config.TextColumn("View"),
            "n_members":    st.column_config.NumberColumn("# Members", format="%d"),
            "cost_eur":     st.column_config.NumberColumn("Cost (EUR)", format="%.0f"),
            "mtm_eur":      st.column_config.NumberColumn("MV (EUR)",   format="%.0f"),
            "pnl_eur":      st.column_config.NumberColumn("Δ (EUR)",    format="%.0f"),
            "pnl_pct":      st.column_config.NumberColumn("Δ %",        format="%.2f%%"),
        },
    )

    st.markdown("**Detailpositionen je View**")
    # Pro View ein Expander mit Detail-Tabelle
    for _, row in sums.iterrows():
        v_name = row["view_name"]
        v_id   = row["view_id"]
        header = (f"▸ {v_name}  ·  MV {fmt_money(row['mtm_eur'], places=0)} EUR  "
                  f"·  Δ {fmt_money(row['pnl_eur'], places=0)} EUR  "
                  f"({row['pnl_pct']:+.2f}%)  ·  {int(row['n_members'])} Members")
        with st.expander(header, expanded=False):
            members = (pv[pv["view_id"] == v_id]
                         .groupby(["ref_instrument_id", "broker", "symbol",
                                    "name", "asset_type", "currency"],
                                    dropna=False, as_index=False)
                         .agg(quantity   = ("quantity",       "sum"),
                              cost_eur   = ("cost_total_eur", "sum"),
                              mtm_eur    = ("mtm_eur",        "sum"),
                              pnl_eur    = ("pnl_eur",        "sum"))
                         .sort_values("mtm_eur", ascending=False))
            members["weight_in_view_pct"] = (
                members["mtm_eur"] / members["mtm_eur"].sum() * 100.0
                if members["mtm_eur"].sum() > 0 else 0
            )
            st.dataframe(
                members[["symbol", "broker", "name", "asset_type", "currency",
                         "quantity", "cost_eur", "mtm_eur", "pnl_eur",
                         "weight_in_view_pct"]],
                use_container_width=True, hide_index=True,
                column_config={
                    "symbol":     st.column_config.TextColumn("Symbol", width="small"),
                    "broker":     st.column_config.TextColumn("Broker", width="small"),
                    "name":       st.column_config.TextColumn("Name"),
                    "asset_type": st.column_config.TextColumn("Type", width="small"),
                    "currency":   st.column_config.TextColumn("CCY", width="small"),
                    "quantity":   st.column_config.NumberColumn(format="%.0f"),
                    "cost_eur":   st.column_config.NumberColumn("Cost (EUR)", format="%.0f"),
                    "mtm_eur":    st.column_config.NumberColumn("MV (EUR)",   format="%.0f"),
                    "pnl_eur":    st.column_config.NumberColumn("Δ (EUR)",    format="%.0f"),
                    "weight_in_view_pct": st.column_config.NumberColumn("Weight %", format="%.2f%%"),
                },
            )

st.divider()


# ---------- Korrelations-Matrix (90d Daily Returns) ----------

st.subheader("Korrelation (90d Daily Returns)")
since = date.today() - timedelta(days=95)
ids   = agg["ref_instrument_id"].dropna().unique().tolist()
if len(ids) >= 2:
    placeholders = ",".join(["?"] * len(ids))
    quote_hist = run_query(f"""
        WITH ranked AS (
            SELECT ref_instrument_id, ts, close, source,
                   ROW_NUMBER() OVER (PARTITION BY ref_instrument_id, ts
                                      ORDER BY CASE source WHEN 'ib' THEN 1 WHEN 'yfinance' THEN 2 ELSE 9 END) AS rk
            FROM mkt_quotes_daily
            WHERE ts >= ? AND ref_instrument_id IN ({placeholders})
        )
        SELECT ref_instrument_id, ts, close FROM ranked WHERE rk = 1
    """, (since, *ids))

    if not quote_hist.empty:
        sym_map = dict(zip(agg["ref_instrument_id"], agg["symbol"]))
        quote_hist["ts"] = pd.to_datetime(quote_hist["ts"])
        wide = (quote_hist
                .pivot(index="ts", columns="ref_instrument_id", values="close")
                .sort_index())
        rets = wide.pct_change().dropna(how="all")
        valid_cols = [c for c in rets.columns if rets[c].notna().sum() >= 30]
        if len(valid_cols) >= 2:
            corr = rets[valid_cols].corr().rename(columns=sym_map, index=sym_map)
            tri = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
            median_corr = float(tri.stack().median())
            st.caption(
                f"{len(valid_cols)} Positions × "
                f"{int(rets[valid_cols].notna().sum().mean())} mean obs. "
                f"Median pairwise corr: **{median_corr:.2f}**"
            )
            heat = px.imshow(corr.values, x=corr.columns, y=corr.index,
                              color_continuous_scale="RdBu_r", zmin=-1, zmax=1,
                              text_auto=".2f", aspect="auto")
            heat.update_layout(height=max(380, len(corr) * 32),
                                margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(heat, use_container_width=True)
        else:
            st.info("Zu wenig Observations.")
    else:
        st.info("Keine Quote-History in den letzten 90 Tagen.")
else:
    st.info("Weniger als 2 Positions.")
