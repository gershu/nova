"""Allokation — Portfolio-Zusammensetzung in drei Tabs.

  - Struktur : deskriptive Allokation (Currency / Asset-Type), Treemap
               (umschaltbar Name / Klasse), Top-15, Holdings nach Klasse.
  - vs. Ziel : Ist-Allokation gegen die Ziel-Baender aus config/allocation.yaml
               (sig_allocation, geschrieben vom taeglichen modules.allocation).
  - Views    : benutzerdefinierte Portfolio-Views (v_mkt_portfolio).

Konsumiert: v_mkt_holdings, v_mkt_portfolio, sig_allocation sowie die
Klassen-Zuordnung aus config/instrument_classes.yaml + config/allocation.yaml.
Aggregation durchgehend in EUR (Stammwaehrung).
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
import yaml

from modules.dashboard.components.format import de_int
from modules.dashboard.components.kpi import fmt_money
from modules.dashboard.db import run_query, table_exists


st.title("⚖️ Allokation")

_CONFIG_DIR = pathlib.Path(__file__).resolve().parents[3] / "config"

_STATUS_ICON = {
    "within": "🟢 im Band", "below": "🔴 unter Band",
    "above": "🔴 ueber Band", "unclassified": "⚪ ohne Klasse",
}
_STATUS_COLOR = {
    "within": "#2e9e5b", "below": "#d6453d",
    "above": "#d6453d", "unclassified": "#9aa0a6",
}


@st.cache_data(ttl=300, show_spinner=False)
def _load_classification() -> tuple[dict, dict]:
    """({ref_instrument_id: class_key}, {class_key: label}) aus den Config-YAMLs."""
    cls_map: dict[str, str] = {}
    labels: dict[str, str] = {}
    cf = _CONFIG_DIR / "instrument_classes.yaml"
    if cf.is_file():
        data = yaml.safe_load(cf.read_text()) or {}
        for cls, ids in (data.get("classification") or {}).items():
            for rid in (ids or []):
                cls_map[rid] = cls
    af = _CONFIG_DIR / "allocation.yaml"
    if af.is_file():
        data = yaml.safe_load(af.read_text()) or {}
        for cls, spec in (data.get("target_allocation") or {}).items():
            labels[cls] = (spec or {}).get("label", cls)
    return cls_map, labels


def _donut(series: pd.Series, title: str):
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


# ---------- Shared: v_mkt_holdings einmal laden ----------

try:
    _mkt = run_query("SELECT * FROM v_mkt_holdings")
except Exception:  # noqa: BLE001
    _mkt = None


# ---------- Tab: Struktur ----------

def render_struktur() -> None:
    if _mkt is None:
        st.error("View `v_mkt_holdings` nicht verfuegbar. "
                 "Init: `python -m modules.portfolio_core init`")
        return
    if _mkt.empty:
        st.warning("Portfolio leer.")
        return

    cls_map, cls_labels = _load_classification()

    agg = (
        _mkt.groupby(
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
    agg["asset_class"] = agg["ref_instrument_id"].map(cls_map)
    agg["class_label"] = agg["asset_class"].map(
        lambda c: cls_labels.get(c, c) if isinstance(c, str) else "(ohne Klasse)")

    # --- KPIs ---
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

    # --- Allokation: Donuts ---
    st.subheader("Allokation (EUR-Aggregat)")
    c1, c2 = st.columns(2)
    with c1:
        ccy_mv = agg.groupby(agg["currency"].fillna("unknown"))["mtm_eur"].sum()
        fig = _donut(ccy_mv.sort_values(ascending=False), "Currency")
        if fig: st.plotly_chart(fig, use_container_width=True)
        else:   st.info("Keine Currency-Daten")
    with c2:
        at_mv = agg.groupby(agg["asset_type"].fillna("unknown"))["mtm_eur"].sum()
        fig = _donut(at_mv.sort_values(ascending=False), "Asset Type")
        if fig: st.plotly_chart(fig, use_container_width=True)
        else:   st.info("Keine Asset-Type-Daten")

    # --- by_name: Holdings konsolidiert ueber Multi-Listings ---
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
                asset_type  = ("asset_type",  "first"),
                class_label = ("class_label", "first"))
    )
    by_name["weight_pct"] = (by_name["mtm_eur"] / total_mv_eur * 100.0
                             if total_mv_eur > 0 else 0)
    by_name = by_name.sort_values("mtm_eur", ascending=False)

    st.divider()

    # --- Treemap (umschaltbar Name / Klasse) ---
    st.subheader("Treemap")
    mode = st.radio(
        "Gruppierung", ["Holdings nach Name", "Holdings nach Klasse"],
        horizontal=True, label_visibility="collapsed")

    if not by_name.empty and by_name["mtm_eur"].sum() > 0:
        if mode == "Holdings nach Name":
            path  = [px.Constant("Portfolio"), "asset_type", "name"]
            title = ("Holdings nach Name (Asset-Type → Name, "
                     "Flaeche = MV EUR, Farbe = PnL EUR)")
        else:
            path  = [px.Constant("Portfolio"), "class_label", "name"]
            title = ("Holdings nach Klasse (Klasse → Name, "
                     "Flaeche = MV EUR, Farbe = PnL EUR)")
        treemap = px.treemap(
            by_name, path=path, values="mtm_eur",
            custom_data=["weight_pct", "pnl_eur", "currencies"],
            color="pnl_eur", color_continuous_scale="RdYlGn",
            color_continuous_midpoint=0, title=title,
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
    else:
        st.info("Keine Positions mit MV > 0.")

    st.divider()

    # --- Top-15 ---
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

    # --- Holdings nach Klasse (Tabelle mit Teilsummen) ---
    st.subheader("Holdings nach Klasse")
    cls_tot = (by_name.groupby("class_label")["mtm_eur"].sum()
                      .sort_values(ascending=False))
    rows: list[dict] = []
    for cl in cls_tot.index:
        members = (by_name[by_name["class_label"] == cl]
                   .sort_values("mtm_eur", ascending=False))
        for _, m in members.iterrows():
            rows.append({"klasse": cl, "position": m["name"],
                         "asset_type": m["asset_type"],
                         "mtm_eur": m["mtm_eur"], "weight_pct": m["weight_pct"],
                         "pnl_eur": m["pnl_eur"]})
        rows.append({"klasse": cl, "position": "Σ Teilsumme", "asset_type": "",
                     "mtm_eur": float(members["mtm_eur"].sum()),
                     "weight_pct": float(members["weight_pct"].sum()),
                     "pnl_eur": float(members["pnl_eur"].sum())})
    rows.append({"klasse": "GESAMT", "position": "Σ Portfolio", "asset_type": "",
                 "mtm_eur": float(by_name["mtm_eur"].sum()),
                 "weight_pct": float(by_name["weight_pct"].sum()),
                 "pnl_eur": float(by_name["pnl_eur"].sum())})
    st.dataframe(
        pd.DataFrame(rows).style.format({"mtm_eur": de_int, "pnl_eur": de_int}),
        use_container_width=True, hide_index=True,
        column_config={
            "klasse":     st.column_config.TextColumn("Klasse"),
            "position":   st.column_config.TextColumn("Position"),
            "asset_type": st.column_config.TextColumn("Typ", width="small"),
            "mtm_eur":    "MV (EUR)",
            "weight_pct": st.column_config.NumberColumn("Anteil %",  format="%.2f%%"),
            "pnl_eur":    "PnL (EUR)",
        },
    )
    st.caption("Teilsummen je Klasse · Holdings nach Name gruppiert "
               "(Multi-Listings konsolidiert). Klassen-Zuordnung aus "
               "`config/instrument_classes.yaml`.")


# ---------- Tab: vs. Ziel ----------

def render_ziel() -> None:
    if not table_exists("sig_allocation"):
        st.info("Noch keine Allokations-Auswertung. "
                "`python -m modules.allocation init` und `run` ausfuehren — "
                "danach laeuft der taegliche Daemon (23:10 UTC).")
        return

    latest = run_query("SELECT max(ts) AS ts FROM sig_allocation")
    if latest.empty or pd.isna(latest.iloc[0]["ts"]):
        st.info("`sig_allocation` ist leer — `python -m modules.allocation run`.")
        return

    ts = str(latest.iloc[0]["ts"])[:10]
    alloc = run_query(
        "SELECT * FROM sig_allocation WHERE ts = ? "
        "ORDER BY target_pct DESC NULLS LAST, label", (ts,))

    total_eur = float(alloc["actual_eur"].sum())
    classed   = alloc[alloc["band_status"] != "unclassified"]
    n_within  = int((classed["band_status"] == "within").sum())
    n_below   = int((classed["band_status"] == "below").sum())
    n_above   = int((classed["band_status"] == "above").sum())

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Portfolio (EUR)", fmt_money(total_eur, places=0))
    k2.metric("🟢 im Band",      n_within)
    k3.metric("🔴 unter Band",   n_below)
    k4.metric("🔴 ueber Band",   n_above)
    st.caption(f"Stand: {ts}  ·  Policy-Anker: Investment Policy Statement")

    if n_below == 0 and n_above == 0:
        st.success("Alle Klassen innerhalb ihrer Ziel-Baender.")

    st.divider()

    st.subheader("Drift gegen Ziel")
    drift_df = alloc[alloc["drift_pct"].notna()].copy()
    if drift_df.empty:
        st.info("Keine Drift-Daten.")
    else:
        drift_df = drift_df.sort_values("drift_pct")
        fig = px.bar(
            drift_df, x="drift_pct", y="label", orientation="h",
            color="band_status", color_discrete_map=_STATUS_COLOR,
            custom_data=["actual_pct", "target_pct", "min_pct", "max_pct"],
            labels={"drift_pct": "Drift (Prozentpunkte vom Ziel)", "label": ""},
        )
        fig.update_traces(hovertemplate=(
            "<b>%{y}</b><br>"
            "Ist: %{customdata[0]:.1f}%  ·  Ziel: %{customdata[1]:.0f}%<br>"
            "Band: %{customdata[2]:.0f}–%{customdata[3]:.0f}%<br>"
            "Drift: %{x:+.1f} pp<extra></extra>"
        ))
        fig.add_vline(x=0, line_width=1, line_color="#888")
        fig.update_layout(height=340, showlegend=False,
                           margin=dict(l=10, r=20, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Balken nach rechts = ueber Ziel, nach links = unter Ziel. "
                   "Rot = ausserhalb des Toleranzbandes.")

    st.divider()

    st.subheader("Ist vs. Ziel")
    tbl = alloc.copy()
    tbl["band"] = tbl.apply(
        lambda r: (f"{r['min_pct']:.0f}–{r['max_pct']:.0f}%"
                   if pd.notna(r["min_pct"]) else "—"), axis=1)
    tbl["status"] = tbl["band_status"].map(lambda s: _STATUS_ICON.get(s, s))
    st.dataframe(
        tbl[["label", "actual_pct", "target_pct", "band", "drift_pct",
             "actual_eur", "status"]].style.format({"actual_eur": de_int}),
        use_container_width=True, hide_index=True,
        column_config={
            "label":      st.column_config.TextColumn("Klasse"),
            "actual_pct": st.column_config.NumberColumn("Ist", format="%.1f %%"),
            "target_pct": st.column_config.NumberColumn("Ziel", format="%.0f %%"),
            "band":       st.column_config.TextColumn("Band"),
            "drift_pct":  st.column_config.NumberColumn("Drift", format="%+.1f"),
            "actual_eur": "Wert EUR",
            "status":     st.column_config.TextColumn("Status"),
        },
    )
    if (alloc["band_status"] == "unclassified").any():
        st.warning("Es gibt Holdings ohne Klassen-Zuordnung — "
                   "`config/instrument_classes.yaml` ergaenzen.")

    hist = run_query(
        "SELECT ts, label, drift_pct FROM sig_allocation WHERE drift_pct IS NOT NULL")
    if not hist.empty and hist["ts"].nunique() >= 2:
        st.divider()
        st.subheader("Drift-Verlauf")
        pivot = hist.pivot_table(index="ts", columns="label", values="drift_pct")
        st.line_chart(pivot, height=320)
        st.caption("Drift je Klasse ueber die Zeit (Prozentpunkte vom Ziel).")


# ---------- Tab: Views ----------

def render_views() -> None:
    try:
        pv = run_query("SELECT * FROM v_mkt_portfolio")
    except Exception as e:  # noqa: BLE001
        st.error(f"View `v_mkt_portfolio` nicht verfuegbar: {e.__class__.__name__}")
        return

    if pv.empty:
        st.info("Keine Portfolio-Views definiert oder ohne Members. "
                "Pflege via `modules.db_edit` auf `list_portfolio_views` + "
                "`list_portfolio_view_members`.")
        return

    sums = (pv.groupby(["view_id", "view_name", "view_color"], dropna=False,
                        as_index=False)
              .agg(n_members   = ("ref_instrument_id", "count"),
                   cost_eur    = ("cost_total_eur",    "sum"),
                   mtm_eur     = ("mtm_eur",           "sum"),
                   pnl_eur     = ("pnl_eur",           "sum")))
    sums["pnl_pct"] = (sums["pnl_eur"]
                       / sums["cost_eur"].where(sums["cost_eur"] != 0) * 100.0)
    sums = sums.sort_values("mtm_eur", ascending=False)

    st.markdown("**Summen pro View (EUR)**")
    st.dataframe(
        sums[["view_name", "n_members", "cost_eur", "mtm_eur", "pnl_eur", "pnl_pct"]]
            .style.format({"cost_eur": de_int, "mtm_eur": de_int, "pnl_eur": de_int}),
        use_container_width=True, hide_index=True,
        column_config={
            "view_name":  st.column_config.TextColumn("View"),
            "n_members":  st.column_config.NumberColumn("# Members", format="%d"),
            "cost_eur":   "Cost (EUR)",
            "mtm_eur":    "MV (EUR)",
            "pnl_eur":    "Δ (EUR)",
            "pnl_pct":    st.column_config.NumberColumn("Δ %",        format="%.2f%%"),
        },
    )

    st.markdown("**Detailpositionen je View**")
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
                         "weight_in_view_pct"]]
                    .style.format({"quantity": de_int, "cost_eur": de_int,
                                   "mtm_eur": de_int, "pnl_eur": de_int}),
                use_container_width=True, hide_index=True,
                column_config={
                    "symbol":     st.column_config.TextColumn("Symbol", width="small"),
                    "broker":     st.column_config.TextColumn("Broker", width="small"),
                    "name":       st.column_config.TextColumn("Name"),
                    "asset_type": st.column_config.TextColumn("Type", width="small"),
                    "currency":   st.column_config.TextColumn("CCY", width="small"),
                    "quantity":   "Menge",
                    "cost_eur":   "Cost (EUR)",
                    "mtm_eur":    "MV (EUR)",
                    "pnl_eur":    "Δ (EUR)",
                    "weight_in_view_pct": st.column_config.NumberColumn("Weight %", format="%.2f%%"),
                },
            )


# ---------- Tabs ----------

tab_struktur, tab_ziel, tab_views = st.tabs(["Struktur", "vs. Ziel", "Views"])
with tab_struktur:
    render_struktur()
with tab_ziel:
    render_ziel()
with tab_views:
    render_views()
