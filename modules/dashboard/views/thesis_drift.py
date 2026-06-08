"""Thesis-Drift-Monitor — „These unter Druck".

Cross-Portfolio-Sicht, die Holdings flaggt, deren Investment-These zuletzt
unter Druck geraten ist. Kombiniert zwei vorberechnete Signale:

  * ref_filing_change : juengste Filing-Aenderung mit Impact = negativ
    (vom filing-watcher, LLM).
  * ref_quality_score : schwacher Gesamt-Score (< 40) bzw. gemischt (< 70);
    zusaetzlich „gefallen seit LLM-Review" via ref_quality_narrative.score
    (Score-Stand der letzten Einordnung) als Referenz — es gibt (noch) keine
    Score-Historie, daher dieser Proxy.

Nur Portfolio-Positionen (pos_holdings, offen). Read-only. Heuristik, kein
Anlageurteil.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from modules.dashboard.db import run_query, table_exists


st.title("⚠ Thesis-Drift-Monitor")
st.caption("Portfolio-Holdings, deren These zuletzt unter Druck geriet — "
           "negative Filing-Aenderung oder schwacher/gefallener Q-Score. "
           "Heuristik, kein Anlageurteil.")

_STRONG, _WEAK, _DROP = 70, 40, 5


# ---------- Existence ----------

if not table_exists("pos_holdings"):
    st.warning("Tabelle `pos_holdings` existiert nicht — kein Portfolio-"
               "Kontext.")
    st.stop()
if not (table_exists("ref_filing_change") or table_exists("ref_quality_score")):
    st.warning("Weder `ref_filing_change` noch `ref_quality_score` vorhanden — "
               "keine Drift-Signale. Erst filing-watcher / quality_score "
               "laufen lassen.")
    st.stop()


# ---------- Filter ----------

c1, c2 = st.columns([1.3, 2])
with c1:
    days = st.selectbox("Filing-Zeitraum", [30, 60, 90, 180, 365], index=2,
                        format_func=lambda d: f"letzte {d} Tage")
with c2:
    show_all = st.checkbox("Alle Holdings zeigen (auch ohne Drift)",
                           value=False)

since = (date.today() - timedelta(days=int(days))).isoformat()


# ---------- Daten laden ----------

hold = run_query(
    "SELECT DISTINCT h.ref_instrument_id, i.symbol, i.name "
    "FROM pos_holdings h "
    "LEFT JOIN ref_instruments i ON i.ref_instrument_id = h.ref_instrument_id "
    "WHERE h.valid_to IS NULL", None)
if hold is None or hold.empty:
    st.info("Keine offenen Portfolio-Positionen.")
    st.stop()

df = hold.copy()

# Q-Score (+ narrative-Referenz)
if table_exists("ref_quality_score"):
    qs = run_query("SELECT ref_instrument_id, score, n_ok "
                   "FROM ref_quality_score", None)
    df = df.merge(qs, on="ref_instrument_id", how="left")
else:
    df["score"] = pd.NA
    df["n_ok"] = pd.NA

if table_exists("ref_quality_narrative"):
    nr = run_query("SELECT ref_instrument_id, score AS narr_score, red_flag, "
                   "narrative, generated_at AS narr_at "
                   "FROM ref_quality_narrative", None)
    df = df.merge(nr, on="ref_instrument_id", how="left")
else:
    for col in ("narr_score", "red_flag", "narrative", "narr_at"):
        df[col] = pd.NA

# Negative Filings im Fenster -> count + jeweils juengster Eintrag
if table_exists("ref_filing_change"):
    nf = run_query(
        "SELECT ref_instrument_id, form, period, summary, generated_at "
        "FROM ref_filing_change WHERE lower(impact) = 'negativ' "
        "AND generated_at >= ? ORDER BY generated_at DESC", (since,))
else:
    nf = None

if nf is not None and not nf.empty:
    g = nf.groupby("ref_instrument_id")
    agg = g.size().rename("neg_count").to_frame()
    latest = g.first()[["form", "period", "summary", "generated_at"]]
    latest = latest.rename(columns={
        "form": "neg_form", "period": "neg_period",
        "summary": "neg_summary", "generated_at": "neg_at"})
    agg = agg.join(latest).reset_index()
    df = df.merge(agg, on="ref_instrument_id", how="left")
else:
    df["neg_count"] = 0
    for col in ("neg_form", "neg_period", "neg_summary", "neg_at"):
        df[col] = pd.NA

df["neg_count"] = df["neg_count"].fillna(0).astype(int)


# ---------- Drift-Bewertung je Holding ----------

def _num(v):
    return None if pd.isna(v) else float(v)


def _reasons(r) -> tuple[list[str], int]:
    """(Reason-Chips, Severity). Severity sortiert die Liste."""
    out, sev = [], 0
    if r["neg_count"] > 0:
        out.append(f"🔴 {int(r['neg_count'])}× negative Filing-Aenderung")
        sev += 3 + int(r["neg_count"])
    score, narr = _num(r["score"]), _num(r["narr_score"])
    if score is not None:
        if score < _WEAK:
            out.append(f"🔴 Q-Score schwach ({int(score)})")
            sev += 3
        elif score < _STRONG:
            out.append(f"🟠 Q-Score gemischt ({int(score)})")
            sev += 1
        if narr is not None and (narr - score) >= _DROP:
            out.append(f"🟠 Q-Score gefallen ({int(narr)}→{int(score)} "
                       "seit LLM-Review)")
            sev += 2
    if isinstance(r.get("red_flag"), str) and r["red_flag"]:
        sev += 1  # Red-Flag verschaerft, eigener Chip im Detail
    return out, sev


df[["_reasons", "_sev"]] = df.apply(
    lambda r: pd.Series(_reasons(r)), axis=1)
flagged = df[df["_reasons"].map(len) > 0].copy()
flagged = flagged.sort_values(["_sev", "score"],
                              ascending=[False, True])


# ---------- KPI-Header ----------

k1, k2, k3, k4 = st.columns(4)
k1.metric("Holdings", f"{len(df)}")
k2.metric("⚠ unter Druck", f"{len(flagged)}")
k3.metric("negat. Filing", int((df["neg_count"] > 0).sum()))
k4.metric(f"Q-Score < {_WEAK}",
          int((pd.to_numeric(df["score"], errors="coerce") < _WEAK).sum()))
st.divider()


# ---------- Liste der Holdings unter Druck ----------

target = df if show_all else flagged
if target.empty:
    st.success("Keine Holding unter Druck im gewaehlten Zeitraum. 🎉")
else:
    if show_all:
        target = target.sort_values(["_sev", "score"],
                                    ascending=[False, True])
    for _, r in target.iterrows():
        sym = r["symbol"] or r["ref_instrument_id"]
        score = _num(r["score"])
        badge = (f"  ·  Q-Score **{int(score)}**" if score is not None
                 else "  ·  Q-Score —")
        reasons = r["_reasons"]
        st.markdown(f"**{sym}** — {r['name'] or ''}{badge}")
        if reasons:
            st.markdown("  ".join(reasons))
        else:
            st.caption("keine Drift-Signale")
        # Detail: juengste negative Filing-Summary + Red-Flag/Narrative
        with st.expander("Detail", expanded=False):
            if r["neg_count"] > 0 and isinstance(r.get("neg_summary"), str):
                st.markdown(f"**Negative Filing-Aenderung** — "
                            f"`{r['neg_form']}` {r['neg_period'] or ''} "
                            f"({str(r['neg_at'])[:16]})")
                st.write(r["neg_summary"])
                st.divider()
            if isinstance(r.get("red_flag"), str) and r["red_flag"]:
                st.markdown(f"⚠ **Red Flag (LLM):** {r['red_flag']}")
            if isinstance(r.get("narrative"), str) and r["narrative"]:
                st.markdown(f"🧠 {r['narrative']}")
                st.caption(f"Q-Score-Einordnung, Stand "
                           f"{str(r.get('narr_at'))[:16]}.")
            if (r["neg_count"] == 0
                    and not (isinstance(r.get("red_flag"), str)
                             and r["red_flag"])
                    and not (isinstance(r.get("narrative"), str)
                             and r["narrative"])):
                st.caption("Keine weiteren Details hinterlegt.")
        st.divider()

st.caption("Signale vorberechnet: filing-watcher (Impact) + quality_score "
           "(Gesamt-Score) + LLM-Einordnung. 'Gefallen' = aktueller Score "
           "unter dem Stand der letzten LLM-Einordnung (kein Zeitreihen-"
           "Verlauf vorhanden).")
