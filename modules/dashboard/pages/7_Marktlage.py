"""Page 6 — Marktlage.

Konsumiert: ref_economic_series + mkt_economic_series (FRED-Daten).

Inhalt:
  - Hero: VIX aktueller Stand + 90d-Plot mit Z-Score-Band
  - Heatmap: alle aktiven Series mit Z-Score-Coloring (rot = Stress, gruen = Calm)
  - Korrelations-Matrix: pairwise Korrelation der Series (90d/365d-Toggle)
  - Portfolio-Korrelation: Daily-Returns v_mkt_holdings vs Daily-Changes der Series

Z-Score-Definition: rolling-window (90d default) Mean + Std → (value − mean) / std.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from modules.dashboard.db import run_query, table_exists


st.title("🌡 Marktlage")


# ---------- Existence-Check ----------

if not table_exists("ref_economic_series") or not table_exists("mkt_economic_series"):
    st.warning("Economic-Series-Tabellen fehlen — fred_ingest noch nicht initialisiert?")
    st.info("Init: `~/nova/workloads/lab_fred_ingest/run.sh init`")
    st.stop()


# ---------- Daten laden ----------

WINDOW_DAYS = 365
since = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()

series_meta = run_query("""
    SELECT series_id, name, category, units, frequency
    FROM ref_economic_series WHERE active
    ORDER BY category, series_id
""")
if series_meta.empty:
    st.info("Keine aktiven Series. Add via `fred_ingest add-series ...`.")
    st.stop()

data = run_query("""
    SELECT series_id, ts, value
    FROM mkt_economic_series
    WHERE source = 'fred' AND ts >= ?
    ORDER BY series_id, ts
""", (since,))
if data.empty:
    st.warning("Keine Daten in den letzten 365 Tagen.")
    st.info("Initialer Pull: `~/nova/workloads/lab_fred_ingest/run.sh fetch-all`")
    st.stop()

data["ts"] = pd.to_datetime(data["ts"])
wide = data.pivot(index="ts", columns="series_id", values="value").sort_index()


# ---------- Z-Score-Berechnung ----------

def zscore(s: pd.Series, window: int = 90) -> pd.Series:
    """Rolling-window Z-Score. Erst valid wenn window-Days verfuegbar."""
    mean = s.rolling(window).mean()
    std  = s.rolling(window).std()
    return (s - mean) / std


zscore_90  = wide.apply(lambda c: zscore(c, 90))
zscore_365 = wide.apply(lambda c: zscore(c, 365))

# Aktuelle Werte + Z-Scores
latest_idx = wide.dropna(how="all").index[-1] if not wide.dropna(how="all").empty else None
if latest_idx is None:
    st.warning("Nicht genug Daten fuer Latest-Werte.")
    st.stop()

current = pd.DataFrame({
    "value":     wide.loc[latest_idx],
    "z_90":      zscore_90.loc[latest_idx],
    "z_365":     zscore_365.loc[latest_idx],
})
current = current.join(series_meta.set_index("series_id"), how="left")


# ---------- Hero: VIX ----------

if "VIXCLS" in wide.columns:
    st.subheader("CBOE Volatility Index (VIX)")
    vix_series = wide["VIXCLS"].dropna()
    vix_last   = float(vix_series.iloc[-1]) if len(vix_series) else float("nan")
    vix_z90    = float(zscore_90["VIXCLS"].iloc[-1]) if "VIXCLS" in zscore_90.columns else float("nan")
    vix_z365   = float(zscore_365["VIXCLS"].iloc[-1]) if "VIXCLS" in zscore_365.columns else float("nan")

    # Regime-Klassifikation (Stefan-Empfehlung in seed-notes):
    #   < 15 = Calm, 15-25 = Normal, 25-40 = Stress, > 40 = Panic
    if vix_last < 15:
        regime = "🟢 Calm"
    elif vix_last < 25:
        regime = "🟡 Normal"
    elif vix_last < 40:
        regime = "🟠 Stress"
    else:
        regime = "🔴 Panic"

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("VIX aktuell",   f"{vix_last:.2f}")
    k2.metric("Regime",        regime)
    k3.metric("Z-Score 90d",   f"{vix_z90:+.2f}σ"  if not np.isnan(vix_z90)  else "—")
    k4.metric("Z-Score 365d",  f"{vix_z365:+.2f}σ" if not np.isnan(vix_z365) else "—")

    # 90d-Plot
    vix_90d = vix_series.tail(90)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=vix_90d.index, y=vix_90d.values, mode="lines",
                               line=dict(color="#c62828", width=2),
                               fill="tozeroy", fillcolor="rgba(198,40,40,0.10)",
                               name="VIX"))
    # Regime-Bands
    for level, color, label in [(15, "rgba(76,175,80,0.10)",  "Calm < 15"),
                                  (25, "rgba(255,193,7,0.10)", "Normal 15-25"),
                                  (40, "rgba(255,87,34,0.10)", "Stress 25-40")]:
        fig.add_hline(y=level, line=dict(color="gray", width=1, dash="dot"),
                       annotation_text=label, annotation_position="top right")
    fig.update_layout(height=360, margin=dict(l=20, r=20, t=10, b=20),
                        yaxis_title="VIX", showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
    st.divider()


# ---------- Heatmap: alle Series, Z-Score 90d ----------

st.subheader("Series-Übersicht (Z-Score 90d)")

heat_df = current.reset_index()[["series_id", "name", "category", "value", "z_90", "z_365"]].copy()
heat_df = heat_df.sort_values("z_90", ascending=False, na_position="last")

st.dataframe(
    heat_df,
    use_container_width=True, hide_index=True,
    column_config={
        "series_id":  st.column_config.TextColumn("Series",   width="small"),
        "name":       st.column_config.TextColumn("Name"),
        "category":   st.column_config.TextColumn("Kat",      width="small"),
        "value":      st.column_config.NumberColumn("Aktuell", format="%.4f"),
        "z_90":       st.column_config.NumberColumn("Z 90d",  format="%+.2f"),
        "z_365":      st.column_config.NumberColumn("Z 365d", format="%+.2f"),
    },
)
st.caption("**Z-Score-Lesart:** |Z| ≥ 2 = signifikante Abweichung (rot=Stress wenn positiv, "
            "blau=Calm wenn negativ). Sortiert nach Z90d desc.")

# Bonus-Visualisierung: horizontaler Bar-Chart der Z-Scores
heat_plot = heat_df.dropna(subset=["z_90"]).sort_values("z_90")
if not heat_plot.empty:
    fig = px.bar(
        heat_plot, x="z_90", y="series_id", orientation="h",
        color="z_90", color_continuous_scale="RdYlBu_r",
        color_continuous_midpoint=0,
        labels={"z_90": "Z-Score 90d", "series_id": ""},
        hover_data={"name": True, "value": ":.4f", "z_365": ":.2f"},
    )
    fig.add_vline(x= 2, line=dict(color="red",  width=1, dash="dot"),
                  annotation_text="+2σ Stress",  annotation_position="top right")
    fig.add_vline(x=-2, line=dict(color="blue", width=1, dash="dot"),
                  annotation_text="-2σ Calm",    annotation_position="bottom left")
    fig.update_layout(height=max(220, len(heat_plot) * 40),
                        margin=dict(l=10, r=10, t=10, b=20),
                        coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)

st.divider()


# ---------- Korrelations-Matrix der Series ----------

st.subheader("Korrelations-Matrix (Daily-Changes)")

window_choice = st.radio("Zeitfenster", ["90d", "365d"], horizontal=True, index=0)
window_days = 90 if window_choice == "90d" else 365
corr_since  = (date.today() - timedelta(days=window_days + 5))

# Daily-changes (Diff) — fuer Rates/Spreads ist Diff sinnvoller als Pct-Change
wide_corr = wide.loc[wide.index >= pd.Timestamp(corr_since)]
changes = wide_corr.diff().dropna(how="all")

valid_cols: list[str] = [c for c in changes.columns if changes[c].notna().sum() >= 20]
if len(valid_cols) >= 2:
    corr = changes[valid_cols].corr()
    fig = px.imshow(
        corr.values, x=corr.columns, y=corr.index,
        color_continuous_scale="RdBu_r", zmin=-1, zmax=1,
        text_auto=".2f", aspect="auto",
    )
    fig.update_layout(height=max(380, len(corr) * 50),
                        margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"{len(valid_cols)} Series × "
                f"{int(changes[valid_cols].notna().sum().mean())} mean obs.")
else:
    st.info("Zu wenig gemeinsame Observations.")

st.divider()


# ---------- Portfolio-Korrelation ----------

st.subheader("Korrelation zum Portfolio")

if not table_exists("v_mkt_holdings"):
    st.info("`v_mkt_holdings` nicht verfuegbar — portfolio_core noch nicht initialisiert?")
    st.stop()

# Portfolio-Daily-MV in EUR ableiten aus historical quotes + heutigem Stand
# Pragmatischer Shortcut: Daily-Returns berechnen ueber mkt_quotes_daily der
# aktuellen Holdings. Wir ignorieren intraday-Transaktionen (Phase 1: SCD-2-
# Reconstruction zu komplex fuer den ersten Wurf).
ids_df = run_query("""
    SELECT DISTINCT ref_instrument_id, quantity, currency
    FROM v_mkt_holdings
""")
ids = ids_df["ref_instrument_id"].dropna().tolist()
if len(ids) < 1:
    st.info("Portfolio leer — Korrelation uebersprungen.")
    st.stop()

placeholders = ",".join(["?"] * len(ids))
hist = run_query(f"""
    WITH ranked AS (
        SELECT ref_instrument_id, ts, close, source,
               ROW_NUMBER() OVER (PARTITION BY ref_instrument_id, ts
                                  ORDER BY CASE source WHEN 'ib' THEN 1
                                                       WHEN 'yfinance' THEN 2 ELSE 9 END) AS rk
        FROM mkt_quotes_daily
        WHERE ts >= ? AND ref_instrument_id IN ({placeholders})
    )
    SELECT ref_instrument_id, ts, close FROM ranked WHERE rk = 1
""", (corr_since, *ids))

if hist.empty:
    st.info("Keine Quote-History fuer Portfolio.")
    st.stop()

hist["ts"] = pd.to_datetime(hist["ts"])
hist = hist.merge(ids_df, on="ref_instrument_id")
hist["mv"] = hist["quantity"] * hist["close"]
ptf_native = hist.groupby("ts")["mv"].sum().sort_index()
ptf_rets   = ptf_native.pct_change().rename("Portfolio")

# Series-Changes joinen
series_for_corr = changes[valid_cols] if len(valid_cols) >= 2 else changes
combined = pd.concat([ptf_rets, series_for_corr], axis=1)
combined = combined.dropna(how="any")
if len(combined) < 20:
    st.info("Zu wenig gemeinsame Tage zwischen Portfolio und Series.")
else:
    pcorr = combined.corr()["Portfolio"].drop("Portfolio").sort_values()
    fig = px.bar(
        pcorr.to_frame("corr").reset_index().rename(columns={"index": "Series"}),
        x="corr", y="Series", orientation="h",
        color="corr", color_continuous_scale="RdBu_r",
        color_continuous_midpoint=0,
        labels={"corr": f"Korrelation Portfolio vs Series ({window_choice})", "Series": ""},
    )
    fig.add_vline(x=0, line=dict(color="gray", width=1, dash="dot"))
    fig.update_layout(height=max(240, len(pcorr) * 40),
                        margin=dict(l=10, r=10, t=10, b=20),
                        coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"{len(combined)} gemeinsame Tage. "
                f"Portfolio-Returns sind native (kein FX-Adj), "
                f"Series sind Daily-Diff.")
