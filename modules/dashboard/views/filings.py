"""Filing-Aenderungen — globaler Feed.

Konsumiert: ref_filing_change (vom filing-watcher, modules.llm.jobs) LEFT JOIN
ref_instruments. 10-K/10-Q als GuV-Diff, 8-K als Text-Summary; je Zeile eine
LLM-Zusammenfassung mit Impact (positiv/neutral/negativ).

Default-Filter: nur Portfolio-Positionen (ref_instrument_id in pos_holdings).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from modules.dashboard.db import run_query, table_exists


st.title("🗞 Filing-Aenderungen")


# ---------- Existence ----------

if not table_exists("ref_filing_change"):
    st.warning("Tabelle `ref_filing_change` existiert nicht — filing-watcher "
               "noch nicht gelaufen?")
    st.info("Producer: `python -m modules.llm.jobs enqueue-filings` · "
            "Worker: `python -m modules.llm.jobs worker --once`")
    st.stop()


# ---------- Filter-UI ----------

f1, f2, f3 = st.columns([1.2, 1.4, 1.4])
with f1:
    days = st.selectbox("Zeitraum", [7, 14, 30, 90, 365], index=2,
                        format_func=lambda d: f"letzte {d} Tage")
with f2:
    only_portfolio = st.checkbox(
        "Nur Portfolio-Positionen", value=True,
        help="Filtert auf ref_instrument_id in pos_holdings (offen).")
with f3:
    only_relevant = st.checkbox(
        "Nur mit Impact (positiv/negativ)", value=False,
        help="Blendet neutrale und n/a (z.B. 8-K ohne Inhalt) aus.")

since = (date.today() - timedelta(days=int(days))).isoformat()

base = """
    SELECT c.generated_at, c.ref_instrument_id, i.symbol, i.name, i.currency,
           c.form, c.period, c.prior_period, c.impact, c.summary, c.model
    FROM ref_filing_change c
    LEFT JOIN ref_instruments i USING (ref_instrument_id)
    WHERE c.generated_at >= ?
"""
params: list = [since]
if only_portfolio and table_exists("pos_holdings"):
    base += (" AND c.ref_instrument_id IN (SELECT DISTINCT ref_instrument_id "
             "FROM pos_holdings WHERE valid_to IS NULL)")
if only_relevant:
    base += " AND lower(c.impact) IN ('positiv','negativ')"

rows = run_query(base + " ORDER BY c.generated_at DESC", tuple(params))

if rows is None or rows.empty:
    st.info(f"Keine Filing-Aenderungen in den letzten {days} Tagen "
            f"{'(Portfolio-only)' if only_portfolio else ''}.")
    st.stop()


# ---------- KPI-Header ----------

imp_low = rows["impact"].astype(str).str.lower()
n_total = len(rows)
n_today = int((rows["generated_at"].astype(str).str[:10]
               == date.today().isoformat()).sum())
n_inst = rows["ref_instrument_id"].nunique()

k1, k2, k3, k4 = st.columns(4)
k1.metric("Aenderungen", f"{n_total}")
k2.metric("Heute", f"{n_today}")
k3.metric("Unique Werte", f"{n_inst}")
k4.metric("Impact-Mix",
          f"🟢 {(imp_low == 'positiv').sum()}  "
          f"⚪ {(imp_low == 'neutral').sum()}  "
          f"🔴 {(imp_low == 'negativ').sum()}")

st.divider()


# ---------- Sekundaer-Filter: Form + Impact + Suche ----------

forms = sorted(rows["form"].dropna().unique().tolist())
impacts = sorted(rows["impact"].dropna().unique().tolist())

c1, c2, c3 = st.columns(3)
with c1:
    form_choice = st.multiselect("Form", forms, default=forms)
with c2:
    imp_choice = st.multiselect("Impact", impacts, default=impacts)
with c3:
    search = st.text_input("Filter (Symbol / Name / Text)", "",
                           placeholder="z.B. AAPL")

view = rows.copy()
if form_choice:
    view = view[view["form"].isin(form_choice)]
if imp_choice:
    view = view[view["impact"].isin(imp_choice)]
if search:
    mask = view.astype(str).apply(
        lambda r: r.str.contains(search, case=False, na=False)).any(axis=1)
    view = view[mask]

st.caption(f"{len(view)} Aenderungen angezeigt (von {n_total} im Zeitraum).")


# ---------- Liste: pro Aenderung ein Block, gruppiert nach Tag ----------

_IMPACT_PIC = {"positiv": "🟢", "negativ": "🔴", "neutral": "⚪", "n/a": "·"}

view = view.sort_values("generated_at", ascending=False)

current_day = None
for _, r in view.iterrows():
    day = str(r["generated_at"])[:10]
    if day != current_day:
        st.markdown(f"### {day}")
        current_day = day

    imp = str(r["impact"] or "n/a").lower()
    symbol = r["symbol"] or r["ref_instrument_id"]
    per = r["period"] or "—"
    head = (f"{_IMPACT_PIC.get(imp, '·')} **{symbol}**  ·  `{r['form']}` {per}")
    if r["prior_period"]:
        head += f"  (vs. {r['prior_period']})"
    st.markdown(head)
    if r["name"]:
        st.caption(f"{r['name']}  ·  {r['currency'] or ''}")
    if r["summary"]:
        st.write(r["summary"])
    st.caption(f"Impact: {imp}  ·  {r['model'] or 'LLM'}  ·  "
               f"{str(r['generated_at'])[:16]}")
    st.divider()


# ---------- Rohdaten-Tabelle ----------

with st.expander(f"Rohdaten-Tabelle ({len(view)} Zeilen)", expanded=False):
    show = ["generated_at", "symbol", "form", "period", "prior_period",
            "impact", "summary", "model"]
    st.dataframe(
        view[show], use_container_width=True, hide_index=True,
        column_config={
            "generated_at": st.column_config.DatetimeColumn("Erkannt"),
            "summary": st.column_config.TextColumn("Zusammenfassung",
                                                   width="large"),
        })
