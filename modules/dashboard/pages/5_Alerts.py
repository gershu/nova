"""Page 5 — Alert Monitor.

Konsumiert: sig_alerts + sig_alert_explanations (LEFT JOIN auf gleichem PK-Shape).
Default-Filter: nur Alerts auf Portfolio-Positionen (= ref_instrument_id in pos_holdings).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from modules.dashboard.db import run_query, table_exists


st.title("🔔 Alert Monitor")


# ---------- Existence ----------

if not table_exists("sig_alerts"):
    st.warning("Tabelle `sig_alerts` existiert nicht — Monitor-Modul nicht migriert?")
    st.info("Init: `python -m modules.monitor init`")
    st.stop()


# ---------- Filter-UI ----------

f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.6, 1.2])
with f1:
    days = st.selectbox("Zeitraum", [1, 3, 7, 14, 30, 90], index=3,
                          format_func=lambda d: f"letzte {d} Tage")
with f2:
    only_portfolio = st.checkbox("Nur Portfolio-Positionen", value=True,
                                    help="Filtert auf ref_instrument_id in pos_holdings.")

since = (date.today() - timedelta(days=int(days))).isoformat()


# ---------- Daten laden ----------

base_join = """
    SELECT a.ts, a.run_id, a.ref_instrument_id, i.symbol, i.name, i.currency,
           a.rule_name, a.direction, a.trigger_value, a.threshold, a.details,
           e.explanation, e.sentiment, e.confidence, e.model    AS llm_model,
           e.generated_at AS llm_generated_at,
           a.created_at
    FROM sig_alerts a
    LEFT JOIN ref_instruments i USING (ref_instrument_id)
    LEFT JOIN sig_alert_explanations e
           ON e.ref_instrument_id = a.ref_instrument_id
          AND e.rule_name         = a.rule_name
          AND e.direction         = COALESCE(a.direction, '')
          AND e.ts                = a.ts
    WHERE a.ts >= ?
"""

params: list = [since]
if only_portfolio:
    base_join += " AND a.ref_instrument_id IN (SELECT DISTINCT ref_instrument_id FROM pos_holdings)"

alerts = run_query(base_join + " ORDER BY a.ts DESC, a.ref_instrument_id, a.rule_name", tuple(params))

if alerts.empty:
    st.info(f"Keine Alerts in den letzten {days} Tagen "
            f"{'(Portfolio-only)' if only_portfolio else ''}.")
    st.stop()


# ---------- KPI-Header ----------

n_total       = len(alerts)
n_today       = int((alerts["ts"].astype(str) == date.today().isoformat()).sum())
n_unique_inst = alerts["ref_instrument_id"].nunique()
n_with_llm    = int(alerts["explanation"].notna().sum())
sent_counts   = alerts["sentiment"].value_counts().to_dict()

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Alerts total",       f"{n_total}")
k2.metric("Heute",              f"{n_today}")
k3.metric("Unique Instruments", f"{n_unique_inst}")
k4.metric("Mit LLM-Erklaerung", f"{n_with_llm}")
k5.metric("Sentiment-Mix",
          f"🟢 {sent_counts.get('positive', 0)}  ⚪ {sent_counts.get('neutral', 0)}  🔴 {sent_counts.get('negative', 0)}")

st.divider()


# ---------- Filter: rule_name + sentiment ----------

rules = sorted(alerts["rule_name"].dropna().unique().tolist())
sentiments = sorted(alerts["sentiment"].dropna().unique().tolist())

c1, c2, c3 = st.columns(3)
with c1:
    rule_choice = st.multiselect("Regel", rules, default=rules)
with c2:
    sent_choice = st.multiselect("Sentiment", sentiments,
                                    default=sentiments if sentiments else [])
with c3:
    search = st.text_input("Filter (Symbol / Name / Text)", "", placeholder="z.B. AAPL")

view = alerts.copy()
if rule_choice:
    view = view[view["rule_name"].isin(rule_choice)]
if sent_choice:
    # NaN-Sentiment behalten, ausser explizit gefiltert wird
    view = view[view["sentiment"].isin(sent_choice) | view["sentiment"].isna()]
if search:
    mask = view.astype(str).apply(
        lambda r: r.str.contains(search, case=False, na=False)).any(axis=1)
    view = view[mask]

st.caption(f"{len(view)} Alerts angezeigt (von {n_total} im Zeitraum).")


# ---------- Liste: pro Alert ein Block ----------

_SENT_ICON = {"positive": "🟢", "neutral": "⚪", "negative": "🔴"}

# Group by ts (neuestes Datum zuerst) - Header pro Tag
view = view.sort_values(["ts", "ref_instrument_id", "rule_name"],
                          ascending=[False, True, True])

current_ts = None
for _, a in view.iterrows():
    ts_str = str(a["ts"])
    if ts_str != current_ts:
        st.markdown(f"### {ts_str}")
        current_ts = ts_str

    sent_icon = _SENT_ICON.get(str(a["sentiment"] or "").lower(), "·")
    direction = a["direction"] or "—"
    rule      = a["rule_name"]
    symbol    = a["symbol"] or a["ref_instrument_id"]
    name      = a["name"] or ""

    cols = st.columns([0.6, 1.4, 1, 1.6, 0.6])
    cols[0].markdown(f"**{symbol}**")
    cols[1].caption(f"{name}  ·  {a['currency'] or ''}")
    cols[2].markdown(f"`{rule}`  {direction}")
    trig = a["trigger_value"]
    thresh = a["threshold"]
    if pd.notna(trig) and pd.notna(thresh):
        cols[3].caption(f"trigger: **{trig:,.2f}**  vs threshold {thresh:,.2f}")
    elif pd.notna(trig):
        cols[3].caption(f"trigger: **{trig:,.2f}**")
    cols[4].markdown(sent_icon)

    if a["explanation"]:
        with st.expander("LLM-Erklaerung", expanded=False):
            st.markdown(a["explanation"])
            st.caption(
                f"Modell: `{a['llm_model']}`  ·  "
                f"Confidence {float(a['confidence'] or 0):.0%}  ·  "
                f"Generated {a['llm_generated_at']}"
            )

st.divider()


# ---------- Rohdaten-Tabelle (expandierbar) ----------

with st.expander(f"Rohdaten-Tabelle ({len(view)} Zeilen)", expanded=False):
    show_cols = ["ts", "symbol", "currency", "rule_name", "direction",
                 "trigger_value", "threshold", "sentiment", "confidence",
                 "explanation", "llm_model"]
    st.dataframe(
        view[show_cols], use_container_width=True, hide_index=True,
        column_config={
            "ts":            st.column_config.DateColumn("Datum"),
            "trigger_value": st.column_config.NumberColumn(format="%.2f"),
            "threshold":     st.column_config.NumberColumn(format="%.2f"),
            "confidence":    st.column_config.NumberColumn(format="%.0%"),
            "explanation":   st.column_config.TextColumn("LLM-Erklaerung", width="large"),
        },
    )
