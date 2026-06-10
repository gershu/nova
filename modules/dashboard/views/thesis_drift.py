"""Thesis-Drift-Monitor — „These unter Druck" (GuruFocus, Pfad A).

Cross-Portfolio-Sicht, die offene Holdings flaggt, deren Qualitaet/Bewertung
unter Druck steht — anhand des vorberechneten GuruFocus-GF-Scores
(ref_gf_score):

  * schwacher GF-Score (< 50) bzw. mittel (< 70)
  * deutliche Ueberbewertung (Kurs / GF-Value hoch)
  * schwache Bilanzstaerke (Rang) / kritischer Altman-Z-Score

Nur Portfolio-Positionen (pos_holdings, offen). Read-only. Heuristik, kein
Anlageurteil.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.dashboard.db import run_query, table_exists


st.title("⚠ Thesis-Drift-Monitor")
st.caption("Portfolio-Holdings unter Druck — schwacher/mittlerer GF-Score, "
           "Ueberbewertung oder schwache Bilanz. Quelle GuruFocus, Heuristik, "
           "kein Anlageurteil.")

_STRONG, _WEAK = 80, 50
_OVERVALUED = 1.3   # Kurs/GF-Value: >30 % ueber intrinsischem Wert

if not table_exists("pos_holdings"):
    st.warning("Tabelle `pos_holdings` existiert nicht — kein Portfolio-"
               "Kontext.")
    st.stop()
if not table_exists("ref_gf_score"):
    st.warning("Tabelle `ref_gf_score` fehlt — Batch `python -m "
               "modules.gurufocus ingest-scores` laufen lassen.")
    st.stop()


# ---------- Daten ----------

hold = run_query(
    "SELECT DISTINCT h.ref_instrument_id, i.symbol, i.name "
    "FROM pos_holdings h "
    "LEFT JOIN ref_instruments i ON i.ref_instrument_id = h.ref_instrument_id "
    "WHERE h.valid_to IS NULL", None)
if hold is None or hold.empty:
    st.info("Keine offenen Portfolio-Positionen.")
    st.stop()

gf = run_query(
    "SELECT ref_instrument_id, gf_score, price_to_gf_value, gf_valuation, "
    "rank_financial_strength, zscore FROM ref_gf_score", None)
df = hold.merge(gf, on="ref_instrument_id", how="left") if gf is not None \
    else hold


def _num(v):
    return None if pd.isna(v) else float(v)


def _reasons(r) -> tuple[list[str], int]:
    out, sev = [], 0
    score = _num(r.get("gf_score"))
    if score is not None:
        if score < _WEAK:
            out.append(f"🔴 GF-Score schwach ({int(score)})"); sev += 3
        elif score < _STRONG:
            out.append(f"🟠 GF-Score mittel ({int(score)})"); sev += 1
    p2 = _num(r.get("price_to_gf_value"))
    if p2 is not None and p2 >= _OVERVALUED:
        out.append(f"🟠 ueberbewertet (Kurs/GF-Value {p2:.2f})"); sev += 2
    fs = _num(r.get("rank_financial_strength"))
    if fs is not None and fs <= 4:
        out.append(f"🔴 Bilanzstaerke schwach (Rang {int(fs)}/10)"); sev += 2
    z = _num(r.get("zscore"))
    if z is not None and z < 1.8:
        out.append(f"🔴 Altman-Z kritisch ({z:.1f})"); sev += 3
    return out, sev


df[["_reasons", "_sev"]] = df.apply(
    lambda r: pd.Series(_reasons(r)), axis=1)
flagged = df[df["_reasons"].map(len) > 0].sort_values(
    ["_sev", "gf_score"], ascending=[False, True])


# ---------- Filter + KPI ----------

show_all = st.checkbox("Alle Holdings zeigen (auch ohne Drift)", value=False)
k1, k2, k3, k4 = st.columns(4)
k1.metric("Holdings", f"{len(df)}")
k2.metric("⚠ unter Druck", f"{len(flagged)}")
k3.metric(f"GF-Score < {_WEAK}",
          int((pd.to_numeric(df["gf_score"], errors="coerce") < _WEAK).sum()))
k4.metric("ueberbewertet",
          int((pd.to_numeric(df["price_to_gf_value"], errors="coerce")
               >= _OVERVALUED).sum()))
st.divider()


# ---------- Liste ----------

target = df.sort_values(["_sev", "gf_score"], ascending=[False, True]) \
    if show_all else flagged
if target.empty:
    st.success("Keine Holding unter Druck. 🎉")
else:
    for _, r in target.iterrows():
        sym = r["symbol"] or r["ref_instrument_id"]
        score = _num(r.get("gf_score"))
        badge = (f"  ·  GF-Score **{int(score)}**" if score is not None
                 else "  ·  GF-Score —")
        val = r.get("gf_valuation")
        st.markdown(f"**{sym}** — {r['name'] or ''}{badge}"
                    + (f"  ·  _{val}_" if isinstance(val, str) and val else ""))
        reasons = r["_reasons"]
        st.markdown("  ".join(reasons) if reasons else
                    "_keine Drift-Signale_")
        st.divider()

st.caption("Signale aus dem vorberechneten GuruFocus-GF-Score "
           "(ref_gf_score). Heuristik, kein Anlageurteil.")
