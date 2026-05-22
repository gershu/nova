"""Page 9 — Allokation.

Stellt die Ist-Allokation gegen die Ziel-Baender aus config/allocation.yaml.
Konsumiert sig_allocation (geschrieben vom taeglichen modules.allocation-Lauf).

Quelle der Wahrheit fuer die Policy ist das Investment Policy Statement
(docs/investment_policy_statement.md); die YAML wird daraus abgeleitet.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from modules.dashboard.components.kpi import fmt_money
from modules.dashboard.db import run_query, table_exists


st.title("⚖️ Allokation")

_STATUS_ICON = {
    "within": "🟢 im Band", "below": "🔴 unter Band",
    "above": "🔴 ueber Band", "unclassified": "⚪ ohne Klasse",
}
_STATUS_COLOR = {
    "within": "#2e9e5b", "below": "#d6453d",
    "above": "#d6453d", "unclassified": "#9aa0a6",
}


# ---------- Daten laden ----------

if not table_exists("sig_allocation"):
    st.info("Noch keine Allokations-Auswertung. "
            "`python -m modules.allocation init` und `run` ausfuehren — "
            "danach laeuft der taegliche Daemon (23:10 UTC).")
    st.stop()

latest = run_query("SELECT max(ts) AS ts FROM sig_allocation")
if latest.empty or pd.isna(latest.iloc[0]["ts"]):
    st.info("`sig_allocation` ist leer — `python -m modules.allocation run`.")
    st.stop()

ts = str(latest.iloc[0]["ts"])[:10]
alloc = run_query(
    "SELECT * FROM sig_allocation WHERE ts = ? ORDER BY target_pct DESC NULLS LAST, label",
    (ts,))


# ---------- KPI-Header ----------

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


# ---------- Drift-Chart ----------

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


# ---------- Ist-vs-Ziel-Tabelle ----------

st.subheader("Ist vs. Ziel")

tbl = alloc.copy()
tbl["band"] = tbl.apply(
    lambda r: (f"{r['min_pct']:.0f}–{r['max_pct']:.0f}%"
               if pd.notna(r["min_pct"]) else "—"), axis=1)
tbl["status"] = tbl["band_status"].map(lambda s: _STATUS_ICON.get(s, s))
st.dataframe(
    tbl[["label", "actual_pct", "target_pct", "band", "drift_pct",
         "actual_eur", "status"]],
    use_container_width=True, hide_index=True,
    column_config={
        "label":      st.column_config.TextColumn("Klasse"),
        "actual_pct": st.column_config.NumberColumn("Ist", format="%.1f %%"),
        "target_pct": st.column_config.NumberColumn("Ziel", format="%.0f %%"),
        "band":       st.column_config.TextColumn("Band"),
        "drift_pct":  st.column_config.NumberColumn("Drift", format="%+.1f"),
        "actual_eur": st.column_config.NumberColumn("Wert EUR", format="%.0f"),
        "status":     st.column_config.TextColumn("Status"),
    },
)

if (alloc["band_status"] == "unclassified").any():
    st.warning("Es gibt Holdings ohne Klassen-Zuordnung — "
               "`config/instrument_classes.yaml` ergaenzen.")


# ---------- Drift-Verlauf ----------

hist = run_query(
    "SELECT ts, label, drift_pct FROM sig_allocation WHERE drift_pct IS NOT NULL")
if not hist.empty and hist["ts"].nunique() >= 2:
    st.divider()
    st.subheader("Drift-Verlauf")
    pivot = hist.pivot_table(index="ts", columns="label", values="drift_pct")
    st.line_chart(pivot, height=320)
    st.caption("Drift je Klasse ueber die Zeit (Prozentpunkte vom Ziel).")
