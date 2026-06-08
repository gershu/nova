"""Portfolio-Wochen-Digest.

Konsumiert: ref_portfolio_digest (vorberechnet vom Job-Worker, Producer
`python -m modules.llm.jobs enqueue-digest`). Je offener Position ein kurzer
LLM-Wochenueberblick aus Q-Score + juengster Filing-Aenderung + Red-Flag.

Read-only. Heuristische Synthese, kein Anlageurteil.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from modules.dashboard.db import run_query, table_exists


st.title("📰 Portfolio-Wochen-Digest")
st.caption("Kurzer, vorberechneter Stand je Holding — Qualitaet, juengste "
           "Filing-Aenderung, Red-Flag. Heuristik, kein Anlageurteil.")

_STRONG, _WEAK = 70, 40


# ---------- Existence ----------

if not table_exists("ref_portfolio_digest"):
    st.warning("Tabelle `ref_portfolio_digest` existiert nicht.")
    st.info("Producer: `python -m modules.llm.jobs enqueue-digest` · "
            "Worker: `python -m modules.llm.jobs worker --once`")
    st.stop()

df = run_query(
    "SELECT d.ref_instrument_id, d.symbol, i.name, d.digest, d.score, "
    "d.model, d.generated_at "
    "FROM ref_portfolio_digest d "
    "LEFT JOIN ref_instruments i ON i.ref_instrument_id = d.ref_instrument_id",
    None)

if df is None or df.empty:
    st.info("Noch keine Digests erzeugt. Producer + Worker laufen lassen.")
    st.stop()


# ---------- Filter / Sortierung ----------

c1, c2 = st.columns([1.6, 2])
with c1:
    sort_by = st.radio("Sortierung", ["Schwächste zuerst", "Neueste zuerst",
                                      "Symbol"], horizontal=True)
with c2:
    search = st.text_input("Suche (Symbol / Name / Text)", "",
                           placeholder="z.B. AAPL")

view = df.copy()
if search:
    m = view.astype(str).apply(
        lambda r: r.str.contains(search, case=False, na=False)).any(axis=1)
    view = view[m]

if sort_by == "Schwächste zuerst":
    view = view.sort_values("score", ascending=True, na_position="first")
elif sort_by == "Neueste zuerst":
    view = view.sort_values("generated_at", ascending=False)
else:
    view = view.sort_values("symbol")


# ---------- KPI-Header ----------

scores = pd.to_numeric(view["score"], errors="coerce")
oldest = pd.to_datetime(view["generated_at"], errors="coerce").min()
age_txt = "—"
if pd.notna(oldest):
    days = (datetime.now() - oldest.to_pydatetime()).days
    age_txt = f"{days}d" if days else "heute"

k1, k2, k3, k4 = st.columns(4)
k1.metric("Holdings", f"{len(view)}")
k2.metric("Ø Q-Score", f"{scores.mean():.0f}" if scores.notna().any() else "—")
k3.metric(f"< {_WEAK} (schwach)", int((scores < _WEAK).sum()))
k4.metric("Ältester Digest", age_txt)

st.caption(f"{len(view)} von {len(df)} Digests.")
st.divider()


# ---------- Karten je Holding ----------

def _badge(score) -> str:
    if pd.isna(score):
        return "⚪ Q —"
    s = int(score)
    pic = "🟢" if s >= _STRONG else "🟠" if s >= _WEAK else "🔴"
    return f"{pic} Q {s}"


for _, r in view.iterrows():
    sym = r["symbol"] or r["ref_instrument_id"]
    with st.container(border=True):
        h1, h2 = st.columns([3, 1])
        h1.markdown(f"**{sym}** — {r['name'] or ''}")
        h2.markdown(_badge(r["score"]))
        st.write(r["digest"] or "_(kein Text)_")
        st.caption(f"{r['model'] or 'LLM'} · Stand "
                   f"{str(r['generated_at'])[:16]}")
