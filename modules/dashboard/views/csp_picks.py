"""CSP Picks.

Konsumiert: list_watchlist_members WHERE watchlist_id = 'system_recommendations'
AND added_by = 'screener_csp'.

Notes-Feld ist 'key=value'-Pair-Format aus screener_csp:
  strike=390.00 exp=2026-06-05 dte=26 bid=3.60 ann_yield=13.0%
  buffer=6.1% (spot=415.12) next_earn=2026-08-01 conviction=0.78 ...
"""

from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from modules.dashboard.db import run_query, table_exists


st.title("📞 CSP Picks")
st.caption("Cash-Secured-Put-Kandidaten aus dem täglichen screener_csp-Run.")


SCREENER_TAG         = "screener_csp"
SYSTEM_REC_WATCHLIST = "system_recommendations"

_PAIR_RE = re.compile(r"(\w+)=([^\s)]+)")


def _parse_notes(notes: str) -> dict[str, str]:
    if not notes:
        return {}
    out: dict[str, str] = {}
    for m in _PAIR_RE.finditer(notes):
        out[m.group(1)] = m.group(2).rstrip(")")
    return out


def _to_float(v: str | None) -> float | None:
    if v is None or v == "—":
        return None
    try:
        return float(v.rstrip("%"))
    except (ValueError, AttributeError):
        return None


# ---------- Daten laden ----------

if not table_exists("list_watchlist_members"):
    st.warning("Tabelle `list_watchlist_members` existiert nicht — Watchlist-Modul "
               "noch nicht migriert?")
    st.stop()

rows = run_query(
    """
    SELECT m.ref_instrument_id, r.symbol, r.name, r.currency, r.exchange,
           m.notes, m.added_at
    FROM list_watchlist_members m
    LEFT JOIN ref_instruments r USING (ref_instrument_id)
    WHERE m.watchlist_id = ? AND m.added_by = ?
    ORDER BY m.added_at DESC
    """,
    (SYSTEM_REC_WATCHLIST, SCREENER_TAG),
)

if rows.empty:
    st.info(f"Keine CSP-Picks in der Watchlist `{SYSTEM_REC_WATCHLIST}` "
            f"(added_by = `{SCREENER_TAG}`).")
    st.info("Daily-Refresh: `~/nova/scripts/lab_screener_csp_daily.sh` oder "
            "ad-hoc `python -m modules.screener_csp run`.")
    st.stop()


# ---------- Notes parsen ----------

parsed = []
for _, r in rows.iterrows():
    kv = _parse_notes(r["notes"] or "")
    parsed.append({
        "symbol":      r["symbol"] or r["ref_instrument_id"],
        "name":        r["name"],
        "currency":    r["currency"],
        "exchange":    r["exchange"],
        "strike":      _to_float(kv.get("strike")),
        "expiration":  kv.get("exp"),
        "dte":         int(kv["dte"]) if kv.get("dte", "").isdigit() else None,
        "bid":         _to_float(kv.get("bid")),
        "ann_yield":   _to_float(kv.get("ann_yield")),
        "buffer":      _to_float(kv.get("buffer")),
        "spot":        _to_float(kv.get("spot")),
        "next_earn":   kv.get("next_earn"),
        "conviction":  _to_float(kv.get("conviction")),
        "added_at":    r["added_at"],
    })

df = pd.DataFrame(parsed)


# ---------- KPI-Header ----------

latest_added = pd.to_datetime(df["added_at"]).max()
n_picks      = len(df)
avg_yield    = float(df["ann_yield"].mean(skipna=True)) if df["ann_yield"].notna().any() else 0.0
median_dte   = float(df["dte"].median(skipna=True))     if df["dte"].notna().any()       else 0.0
median_conv  = float(df["conviction"].median(skipna=True)) if df["conviction"].notna().any() else 0.0

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("# Picks",        f"{n_picks}")
k2.metric("⌀ Yield p.a.",   f"{avg_yield:.1f} %")
k3.metric("Median DTE",     f"{median_dte:.0f} Tage")
k4.metric("Median Conviction", f"{median_conv:.2f}")
k5.metric("Letztes Update", f"{latest_added.strftime('%Y-%m-%d')}"
           if pd.notna(latest_added) else "—")

st.divider()


# ---------- Filter ----------

f1, f2, f3 = st.columns(3)
with f1:
    min_yield = st.slider("Min Yield p.a. (%)", 0, 50, 0)
with f2:
    max_dte = st.slider("Max DTE", 0, 90, 90)
with f3:
    search = st.text_input("Filter (Symbol / Name)", "", placeholder="z.B. AAPL")

view = df.copy()
if min_yield > 0:
    view = view[view["ann_yield"] >= min_yield]
view = view[view["dte"].fillna(999) <= max_dte]
if search:
    mask = view.astype(str).apply(
        lambda r: r.str.contains(search, case=False, na=False)).any(axis=1)
    view = view[mask]

view = view.sort_values("ann_yield", ascending=False)


# ---------- Tabelle ----------

st.dataframe(
    view,
    use_container_width=True, height=560, hide_index=True,
    column_config={
        "symbol":     st.column_config.TextColumn("Symbol",   width="small"),
        "name":       st.column_config.TextColumn("Name",     width="medium"),
        "currency":   st.column_config.TextColumn("CCY",      width="small"),
        "exchange":   st.column_config.TextColumn("Exchange", width="small"),
        "strike":     st.column_config.NumberColumn("Strike", format="%.2f"),
        "expiration": st.column_config.TextColumn("Expiry"),
        "dte":        st.column_config.NumberColumn("DTE",       format="%d"),
        "bid":        st.column_config.NumberColumn("Bid",       format="%.2f"),
        "ann_yield":  st.column_config.NumberColumn("Yield p.a.", format="%.1f %%"),
        "buffer":     st.column_config.NumberColumn("Buffer",     format="%.1f %%"),
        "spot":       st.column_config.NumberColumn("Spot",       format="%.2f"),
        "next_earn":  st.column_config.TextColumn("Next Earnings"),
        "conviction": st.column_config.NumberColumn("Conviction", format="%.2f"),
        "added_at":   st.column_config.DatetimeColumn("Added", format="YYYY-MM-DD HH:mm"),
    },
)

st.caption(f"Sortiert nach Yield p.a. desc. {len(view)} von {n_picks} Picks angezeigt. "
           f"Daily-Refresh: `lab_screener_csp`. "
           f"CSV-Details in `~/nova_output/lab_screener_csp/`.")
