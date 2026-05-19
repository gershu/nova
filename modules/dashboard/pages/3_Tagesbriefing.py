"""Page 3 — Tagesbriefing.

Konsumiert: sig_portfolio_briefings.
Zeigt das aktuellste LLM-erzeugte Portfolio-Briefing prominent (headline +
body + KPI-Snapshot + Sentiment) und bietet einen History-Selector fuer
aeltere Briefings.
"""

from __future__ import annotations

import streamlit as st

from modules.dashboard.components.kpi import fmt_money, fmt_pct
from modules.dashboard.db import run_query, table_exists


st.title("📝 Tagesbriefing")


# ---------- Existence Check ----------

if not table_exists("sig_portfolio_briefings"):
    st.warning("Tabelle `sig_portfolio_briefings` existiert nicht — Monitor-Modul "
               "noch nicht migriert?")
    st.info("Init: `python -m modules.monitor init`")
    st.stop()


# ---------- Inventar laden ----------

briefings = run_query("""
    SELECT ts, model, base_currency, portfolio_total,
           delta_abs_day, delta_pct_day,
           holdings_count, alerts_count,
           headline, body,
           sentiment, confidence,
           eval_tokens, duration_s, run_id, generated_at
    FROM sig_portfolio_briefings
    ORDER BY ts DESC, generated_at DESC
""")

if briefings.empty:
    st.info("Noch keine Briefings vorhanden.")
    st.info("Generieren: Daemon `lab_monitor` mit `--briefing` Flag, oder "
            "ad-hoc `python -m modules.monitor briefing --ts YYYY-MM-DD`.")
    st.stop()


# ---------- Selector: ts (latest preselected) ----------

ts_options = briefings["ts"].astype(str).unique().tolist()
selected_ts = st.selectbox(
    "Datum", ts_options, index=0,
    help=f"{len(ts_options)} Briefings in der DB.",
)

day_rows = briefings[briefings["ts"].astype(str) == selected_ts]
if len(day_rows) > 1:
    # Mehrere Modelle pro Tag — selectbox fuer Modell
    model_options = day_rows["model"].unique().tolist()
    selected_model = st.selectbox("Modell", model_options, index=0)
    rec = day_rows[day_rows["model"] == selected_model].iloc[0]
else:
    rec = day_rows.iloc[0]


# ---------- Sentiment-Badge ----------

_SENT_COLORS = {"positive": "🟢", "neutral": "⚪", "negative": "🔴"}
sent_icon = _SENT_COLORS.get(str(rec["sentiment"] or "").lower(), "⚪")
st.caption(f"{sent_icon} **{rec['sentiment'] or 'neutral'}** "
           f"· Confidence {float(rec['confidence'] or 0):.0%} "
           f"· Modell `{rec['model']}` "
           f"· Erzeugt {rec['generated_at']}")


# ---------- Headline ----------

if rec["headline"]:
    st.markdown(f"## {rec['headline']}")


# ---------- KPI-Snapshot ----------

base_ccy = rec["base_currency"] or "EUR"
k1, k2, k3, k4 = st.columns(4)
k1.metric(f"Portfolio ({base_ccy})",
          fmt_money(float(rec["portfolio_total"]) if rec["portfolio_total"] else 0,
                     places=0))
k2.metric("Δ vs Vortag",
          fmt_money(float(rec["delta_abs_day"]) if rec["delta_abs_day"] else 0,
                     places=0),
          delta=fmt_pct(float(rec["delta_pct_day"]) / 100.0
                          if rec["delta_pct_day"] is not None else None))
k3.metric("Holdings",
          f"{int(rec['holdings_count']) if rec['holdings_count'] else 0}")
k4.metric("Alerts heute",
          f"{int(rec['alerts_count']) if rec['alerts_count'] else 0}",
          help="Alerts auf gehaltenen Positionen am Briefing-Tag.")

st.divider()


# ---------- Body (Markdown) ----------

if rec["body"]:
    st.markdown(rec["body"])
else:
    st.info("(Kein Body — Briefing nur als Headline/KPI erzeugt.)")


# ---------- Footer: Metadata ----------

with st.expander("Audit-Metadata", expanded=False):
    cm1, cm2, cm3 = st.columns(3)
    cm1.metric("Eval-Tokens", f"{int(rec['eval_tokens']) if rec['eval_tokens'] else '—'}")
    cm2.metric("Duration (s)", f"{float(rec['duration_s'] or 0):.1f}")
    cm3.caption(f"run_id: `{rec['run_id']}`")


# ---------- History-Liste ----------

with st.expander(f"📜 Alle Briefings ({len(briefings)})", expanded=False):
    hist = briefings[["ts", "model", "sentiment", "delta_pct_day",
                       "alerts_count", "headline"]].copy()
    st.dataframe(
        hist, use_container_width=True, hide_index=True,
        column_config={
            "ts":            st.column_config.DateColumn("Datum"),
            "model":         st.column_config.TextColumn("Modell"),
            "sentiment":     st.column_config.TextColumn("Sentiment", width="small"),
            "delta_pct_day": st.column_config.NumberColumn("Δ Day %", format="%.2f%%"),
            "alerts_count":  st.column_config.NumberColumn("# Alerts", format="%d"),
            "headline":      st.column_config.TextColumn("Headline", width="large"),
        },
    )
