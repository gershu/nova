"""Page 2 — Action Items.

Konsolidiert die verstreuten Signal-Quellen in eine priorisierte Sicht —
die "Was liegt heute an"-Seite des Entscheidungs-Assistenten.

Quellen:
  - sig_market_setups   — aktive Trading-Setups (modules.setup)
  - sig_alerts          — Portfolio-Alerts (nur gehaltene Positionen)
  - sig_alert_explanations — LLM-Erklaerungen zu den Alerts
  - list_watchlist_members — CSP-Screener-Picks als Opportunities

Sortierung durchgehend nach Severity (critical -> warning -> info).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from modules.dashboard.db import run_query, table_exists


st.title("🎯 Action Items")

_SEV_ICON  = {"critical": "🔴", "warning": "🟠", "info": "🟢"}
_SEV_ORDER = {"critical": 1, "warning": 2, "info": 3}


# ---------- Daten laden ----------

# Recommendations (latest ts) — die abgeleitete Quintessenz
recs = pd.DataFrame()
if table_exists("sig_recommendations"):
    latest_rec = run_query("SELECT max(ts) AS ts FROM sig_recommendations")
    if not latest_rec.empty and pd.notna(latest_rec.iloc[0]["ts"]):
        recs = run_query("""
            SELECT rec_id, ts, category, symbol, action, priority, title, rationale
            FROM sig_recommendations WHERE ts = ?
        """, (str(latest_rec.iloc[0]["ts"]),))

# Setups (latest ts)
setups = pd.DataFrame()
if table_exists("sig_market_setups"):
    latest = run_query("SELECT max(ts) AS ts FROM sig_market_setups")
    if not latest.empty and pd.notna(latest.iloc[0]["ts"]):
        setups = run_query("""
            SELECT setup_name, severity, category, summary, ts
            FROM sig_market_setups WHERE ts = ?
        """, (str(latest.iloc[0]["ts"]),))

# Portfolio-Alerts (letzte 7 Tage, nur gehaltene Positionen)
alerts = pd.DataFrame()
if table_exists("sig_alerts"):
    since = (date.today() - timedelta(days=7)).isoformat()
    alerts = run_query("""
        SELECT a.ts, a.ref_instrument_id, i.symbol, i.name,
               a.rule_name, a.direction, a.trigger_value, a.threshold,
               e.explanation, e.sentiment
        FROM sig_alerts a
        LEFT JOIN ref_instruments i USING (ref_instrument_id)
        LEFT JOIN sig_alert_explanations e
               ON e.ref_instrument_id = a.ref_instrument_id
              AND e.rule_name         = a.rule_name
              AND e.direction         = COALESCE(a.direction, '')
              AND e.ts                = a.ts
        WHERE a.ts >= ?
          AND a.ref_instrument_id IN (
              SELECT DISTINCT ref_instrument_id FROM pos_holdings WHERE valid_to IS NULL
          )
        ORDER BY a.ts DESC, a.ref_instrument_id
    """, (since,))

# CSP-Opportunities
csp = pd.DataFrame()
if table_exists("list_watchlist_members"):
    csp = run_query("""
        SELECT r.symbol, r.name, m.notes, m.added_at
        FROM list_watchlist_members m
        LEFT JOIN ref_instruments r USING (ref_instrument_id)
        WHERE m.watchlist_id = 'system_recommendations'
          AND m.added_by     = 'screener_csp'
        ORDER BY m.added_at DESC
    """)


# ---------- KPI-Header ----------

n_recs     = len(recs)
n_critical = int((setups["severity"] == "critical").sum()) if not setups.empty else 0
n_warning  = int((setups["severity"] == "warning").sum())  if not setups.empty else 0
n_info     = int((setups["severity"] == "info").sum())     if not setups.empty else 0
n_alerts   = len(alerts)
n_csp      = len(csp)

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("💡 Empfehlungen",     n_recs)
k2.metric("🔴 Critical-Setups",  n_critical)
k3.metric("🟠 Warning-Setups",   n_warning)
k4.metric("🟢 Info-Setups",      n_info)
k5.metric("Portfolio-Alerts (7d)", n_alerts)
k6.metric("CSP-Opportunities",   n_csp)

if n_recs == 0 and n_critical == 0 and n_warning == 0 and n_alerts == 0:
    st.success("Keine dringenden Action-Items — ruhige Lage.")

st.divider()


# ---------- Empfehlungen (LLM-Recommendation-Layer) ----------

st.subheader("💡 Empfehlungen")
if recs.empty:
    st.info("Keine Empfehlungen. `python -m modules.llm.recommendations run` "
            "ausfuehren — oder es ist tatsaechlich nichts vorzuschlagen.")
else:
    _PRIO_ICON  = {"high": "🔴", "medium": "🟠", "low": "🟢"}
    _PRIO_ORDER = {"high": 1, "medium": 2, "low": 3}
    r = recs.copy()
    r["_ord"] = r["priority"].map(_PRIO_ORDER).fillna(9)
    r = r.sort_values(["_ord", "rec_id"])
    st.caption(f"Stand: {str(r.iloc[0]['ts'])} · "
               f"LLM-abgeleitet aus Setups + Alerts + Portfolio-Zustand · "
               f"Vorschläge, keine Order")
    for _, row in r.iterrows():
        icon = _PRIO_ICON.get(row["priority"], "·")
        tgt  = f"  ·  **{row['symbol']}**" if row["symbol"] else ""
        st.markdown(
            f"{icon} `{row['action']}`{tgt}  ·  _{row['category'] or ''}_  \n"
            f"**{row['title'] or ''}**  \n"
            f"{row['rationale'] or ''}"
        )

st.divider()


# ---------- Markt-Setups ----------

st.subheader("Markt-Setups")
if setups.empty:
    st.info("Keine aktiven Setups. `python -m modules.setup run` ausfuehren — "
            "oder es ist tatsaechlich nichts aktiv.")
else:
    s = setups.copy()
    s["_ord"] = s["severity"].map(_SEV_ORDER).fillna(9)
    s = s.sort_values(["_ord", "setup_name"])
    setup_ts = str(s.iloc[0]["ts"])
    st.caption(f"Stand: {setup_ts}")
    for _, row in s.iterrows():
        icon = _SEV_ICON.get(row["severity"], "·")
        st.markdown(
            f"{icon} **{row['setup_name']}**  ·  _{row['category'] or ''}_  \n"
            f"{row['summary'] or ''}"
        )

st.divider()


# ---------- Portfolio-Alerts ----------

st.subheader("Portfolio-Alerts (letzte 7 Tage)")
if alerts.empty:
    st.info("Keine Alerts auf gehaltenen Positionen in den letzten 7 Tagen.")
else:
    _sent_icon = {"negative": "🔴", "neutral": "⚪", "positive": "🟢"}
    # Sortierung: negatives Sentiment zuerst, dann nach Datum
    alerts = alerts.copy()
    alerts["_sent_ord"] = alerts["sentiment"].map(
        {"negative": 1, "neutral": 2, "positive": 3}).fillna(2)
    alerts = alerts.sort_values(["_sent_ord", "ts"], ascending=[True, False])
    for _, a in alerts.iterrows():
        icon = _sent_icon.get(str(a["sentiment"] or "").lower(), "·")
        sym  = a["symbol"] or a["ref_instrument_id"]
        direction = f" {a['direction']}" if a["direction"] else ""
        trig = a["trigger_value"]
        thr  = a["threshold"]
        trig_str = ""
        if pd.notna(trig) and pd.notna(thr):
            trig_str = f"  ·  {trig:,.2f} vs {thr:,.2f}"
        st.markdown(
            f"{icon} **{sym}**  ·  `{a['rule_name']}`{direction}  ·  {a['ts']}{trig_str}"
        )
        if a["explanation"]:
            with st.expander("LLM-Erklaerung", expanded=False):
                st.markdown(a["explanation"])

st.divider()


# ---------- CSP-Opportunities ----------

st.subheader("Opportunities — CSP-Screener")

_PAIR_RE = re.compile(r"(\w+)=([^\s)]+)")


def _parse_notes(notes: str) -> dict[str, str]:
    if not notes:
        return {}
    return {m.group(1): m.group(2).rstrip(")") for m in _PAIR_RE.finditer(notes)}


if csp.empty:
    st.info("Keine CSP-Picks. Daily-Refresh via `lab_screener_csp`.")
else:
    rows = []
    for _, r in csp.iterrows():
        kv = _parse_notes(r["notes"] or "")
        def _f(key):
            v = kv.get(key)
            try:
                return float(v.rstrip("%")) if v else None
            except (ValueError, AttributeError):
                return None
        rows.append({
            "symbol":     r["symbol"],
            "name":       r["name"],
            "strike":     _f("strike"),
            "expiration": kv.get("exp"),
            "dte":        int(kv["dte"]) if kv.get("dte", "").isdigit() else None,
            "ann_yield":  _f("ann_yield"),
            "buffer":     _f("buffer"),
            "conviction": _f("conviction"),
        })
    df = pd.DataFrame(rows).sort_values("ann_yield", ascending=False)
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={
            "symbol":     st.column_config.TextColumn("Symbol", width="small"),
            "name":       st.column_config.TextColumn("Name"),
            "strike":     st.column_config.NumberColumn("Strike", format="%.2f"),
            "expiration": st.column_config.TextColumn("Expiry"),
            "dte":        st.column_config.NumberColumn("DTE", format="%d"),
            "ann_yield":  st.column_config.NumberColumn("Yield p.a.", format="%.1f %%"),
            "buffer":     st.column_config.NumberColumn("Buffer", format="%.1f %%"),
            "conviction": st.column_config.NumberColumn("Conviction", format="%.2f"),
        },
    )
    st.caption("Detailansicht + Filter: Seite **CSP Picks**.")
