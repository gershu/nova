"""Page 8 — Decision Journal.

Schliesst den Feedback-Loop des Entscheidungs-Assistenten: was hat der
Recommendation-Layer vorgeschlagen (sig_recommendations) — und was wurde
daraus entschieden + wie ist es ausgegangen (sig_decision_journal).

Hybrid-Erfassung: nova schlaegt anhand Symbol + Zeitfenster passende
Trades vor, der Anleger bestaetigt, erfasst Status + Begruendung und traegt
spaeter das Outcome nach.

Schreibzugriffe laufen ueber modules.decision_journal.store (kurzlebige
read-write Connection) — dieselbe Datenschicht wie das CLI.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.dashboard.db import connection
from modules.decision_journal import store


st.title("📓 Decision Journal")

_STATUS_LABEL = {
    "pending":       "○ offen",
    "acted_full":    "✓ umgesetzt",
    "acted_partial": "◐ teilweise",
    "declined":      "✗ verworfen",
    "expired":       "⌛ verfallen",
}
_OUTCOME_LABEL = {"good": "🟢 gut", "neutral": "⚪ neutral", "poor": "🔴 schlecht"}


# ---------- Voraussetzungen ----------

with connection() as con:
    have_recs    = store.table_exists(con, "sig_recommendations")
    have_journal = store.table_exists(con, "sig_decision_journal")

if not have_recs:
    st.info("Keine `sig_recommendations` — erst "
            "`python -m modules.llm.recommendations run` ausfuehren.")
    st.stop()

if not have_journal:
    st.warning("Das Decision-Journal ist auf dieser DB noch nicht initialisiert.")
    if st.button("Journal jetzt initialisieren"):
        try:
            with store.connect(read_only=False) as con:
                store.apply_schema(con)
            st.success("Schema angelegt.")
            st.rerun()
        except store.JournalError as e:
            st.error(str(e))
    st.caption("Alternativ per CLI: `python -m modules.decision_journal init`")
    st.stop()


# ---------- Daten laden (eine Connection fuer alle Reads) ----------

with connection() as con:
    stats   = store.journal_stats(con)
    recs    = store.list_recommendations(con)
    journal = store.get_journal(con)
    # Trade-Vorschlaege fuer noch offene Recommendations vorab sammeln
    suggestions: dict[int, pd.DataFrame] = {}
    if not recs.empty:
        for _, r in recs.iterrows():
            is_open = pd.isna(r["status"]) or r["status"] == "pending"
            if is_open:
                suggestions[int(r["rec_id"])] = store.suggest_trades(
                    con, r["ref_instrument_id"], str(r["rec_ts"]))


# ---------- KPI-Header ----------

good    = stats["by_outcome"].get("good", 0)
neutral = stats["by_outcome"].get("neutral", 0)
poor    = stats["by_outcome"].get("poor", 0)
ft      = stats["follow_through_pct"]

k1, k2, k3, k4 = st.columns(4)
k1.metric("Recommendations", stats["n_recs_total"])
k2.metric("Journalisiert",   stats["n_journaled"])
k3.metric("Follow-Through",  f"{ft:.0f} %" if ft is not None else "—")
k4.metric("Outcome 🟢/⚪/🔴", f"{good}/{neutral}/{poor}")

st.divider()


# ---------- Offene Entscheidungen ----------

st.subheader("Offene Entscheidungen")

open_recs = recs[recs["status"].isna() | (recs["status"] == "pending")] \
    if not recs.empty else pd.DataFrame()

if recs.empty:
    st.info("Keine Recommendations am juengsten Tag.")
elif open_recs.empty:
    st.success("Alle Recommendations des juengsten Tags sind erfasst.")
else:
    st.caption(f"Recommendations vom {recs.iloc[0]['rec_ts']} — "
               f"{len(open_recs)} offen. Trade-Vorschlaege aus einem "
               f"{store.SUGGEST_WINDOW_DAYS}-Tage-Fenster.")
    _prio = {"high": "🔴", "medium": "🟠", "low": "🟢"}
    for _, r in open_recs.iterrows():
        rid = int(r["rec_id"])
        sym = f" · {r['symbol']}" if r["symbol"] else ""
        head = f"{_prio.get(r['priority'], '·')} #{rid} [{r['action']}]{sym} — {r['title'] or ''}"
        with st.expander(head, expanded=False):
            if r["rationale"]:
                st.caption(r["rationale"])

            cand = suggestions.get(rid, pd.DataFrame())
            trade_opts: dict[str, dict] = {}
            if not cand.empty:
                for _, t in cand.iterrows():
                    key = (f"{t['ts']} · {t['side']} {t['quantity']:,.0f} "
                           f"@ {t['price']:,.2f} {t['currency']} · "
                           f"{t['broker']} (lot {int(t['trade_lot'])})")
                    trade_opts[key] = {
                        "ref_instrument_id": t["ref_instrument_id"],
                        "broker":            t["broker"],
                        "trade_lot":         int(t["trade_lot"]),
                    }
            elif r["ref_instrument_id"]:
                st.caption("Keine passenden Trades im Zeitfenster gefunden.")
            else:
                st.caption("Portfolio-/marktweite Recommendation — kein Instrument.")

            with st.form(f"log_{r['rec_ts']}_{rid}"):
                status = st.selectbox(
                    "Entscheidung", store.VALID_STATUS,
                    format_func=lambda s: _STATUS_LABEL.get(s, s),
                    key=f"st_{rid}")
                linked_keys = (
                    st.multiselect("Verknuepfte Trades", list(trade_opts.keys()),
                                   key=f"tr_{rid}")
                    if trade_opts else [])
                rationale = st.text_area(
                    "Begruendung — warum so entschieden?", key=f"ra_{rid}")
                submitted = st.form_submit_button("Entscheidung speichern")

            if submitted:
                try:
                    store.upsert_decision(
                        str(r["rec_ts"]), r["rec_model"], rid,
                        status=status,
                        rationale=rationale or None,
                        linked_trades=[trade_opts[k] for k in linked_keys] or None)
                    st.success("Entscheidung gespeichert.")
                    st.rerun()
                except store.JournalError as e:
                    st.error(str(e))

st.divider()


# ---------- Outcome ausstehend ----------

st.subheader("Outcome ausstehend")

if journal.empty:
    st.info("Noch keine Eintraege im Journal.")
else:
    pending_oc = journal[(journal["status"] != "pending")
                         & (journal["outcome"].isna())]
    if pending_oc.empty:
        st.success("Alle entschiedenen Recommendations sind bewertet.")
    else:
        st.caption(f"{len(pending_oc)} Entscheidung(en) ohne Outcome-Bewertung.")
        for _, j in pending_oc.iterrows():
            rid = int(j["rec_id"])
            sym = f" · {j['rec_symbol']}" if j["rec_symbol"] else ""
            head = (f"{j['rec_ts']} #{rid} [{j['rec_action'] or '—'}]{sym} — "
                    f"{j['rec_title'] or ''}  ({_STATUS_LABEL.get(j['status'], j['status'])})")
            with st.expander(head, expanded=False):
                if j["rationale"]:
                    st.caption(f"Begruendung: {j['rationale']}")
                with st.form(f"assess_{j['rec_ts']}_{rid}"):
                    outcome = st.selectbox(
                        "Outcome", store.VALID_OUTCOME,
                        format_func=lambda o: _OUTCOME_LABEL.get(o, o),
                        key=f"oc_{rid}")
                    pnl = st.number_input(
                        "Outcome-PnL EUR (optional)", value=0.0, step=100.0,
                        format="%.2f", key=f"pnl_{rid}")
                    note = st.text_area(
                        "Outcome-Notiz — war es die richtige Entscheidung?",
                        key=f"ocn_{rid}")
                    submitted = st.form_submit_button("Outcome speichern")
                if submitted:
                    try:
                        store.assess_outcome(
                            str(j["rec_ts"]), j["rec_model"], rid,
                            outcome=outcome,
                            pnl_eur=(pnl if pnl != 0.0 else None),
                            note=note or None)
                        st.success("Outcome gespeichert.")
                        st.rerun()
                    except store.JournalError as e:
                        st.error(str(e))

st.divider()


# ---------- Journal-Uebersicht ----------

st.subheader("Journal")

if journal.empty:
    st.info("Das Journal ist leer.")
else:
    view = journal.copy()
    view["trades"] = view["linked_trades"].apply(
        lambda raw: len(store.parse_linked_trades(raw)))
    view["status"]  = view["status"].map(lambda s: _STATUS_LABEL.get(s, s))
    view["outcome"] = view["outcome"].map(
        lambda o: _OUTCOME_LABEL.get(o, "") if pd.notna(o) else "")
    cols = ["rec_ts", "rec_id", "rec_action", "rec_symbol", "rec_title",
            "status", "decided_at", "trades", "outcome", "outcome_pnl_eur"]
    st.dataframe(
        view[cols], use_container_width=True, hide_index=True,
        column_config={
            "rec_ts":          st.column_config.TextColumn("Datum", width="small"),
            "rec_id":          st.column_config.NumberColumn("#", width="small"),
            "rec_action":      st.column_config.TextColumn("Action", width="small"),
            "rec_symbol":      st.column_config.TextColumn("Symbol", width="small"),
            "rec_title":       st.column_config.TextColumn("Titel"),
            "status":          st.column_config.TextColumn("Status"),
            "decided_at":      st.column_config.TextColumn("Entschieden", width="small"),
            "trades":          st.column_config.NumberColumn("Trades", width="small"),
            "outcome":         st.column_config.TextColumn("Outcome"),
            "outcome_pnl_eur": st.column_config.NumberColumn("PnL EUR", format="%.0f"),
        },
    )
    st.caption("Erfassung + Bewertung in den Abschnitten oben — oder per "
               "`python -m modules.decision_journal`.")
