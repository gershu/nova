"""Screener-Detail-Helper — wird vom Thesis-Cockpit als Tab eingebettet.

Underscore-Prefix markiert das hier als Komponente, nicht als eigene
Streamlit-Seite (nav-Liste in app.py ist explizit; underscore-Files werden
nicht automatisch geladen).

Eingabe: ref_instrument_id (+ Symbol fuers Logging) und ein Streamlit-
Context. Ausgabe: render() schreibt direkt in den aktuellen Tab.

Inhalt der Komponente:
  - Run-Kontext + Achsen-Scores Q/G/V/Composite + Trend-Flags
  - Kriterien-Tabelle (Pass/Fail je Kriterium)
  - LLM-Thesis (Verdikt, Risiken, Moat) oder „Analyse anstossen"-Button

Wenn der Name nicht im juengsten Screener-Run als Pick aufgefuehrt ist,
zeigt die Komponente eine entsprechende Info — der LLM-Analyze-Button
verlangt einen vorhandenen Pick (run_id), weil die Thesis dort verankert
ist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pandas as pd
import streamlit as st

from modules.dashboard.components.format import de_dec
from modules.dashboard.db import run_query, table_exists


_GLYPH_VERDICT = {
    "BUY_CONVICTION": "🟢 BUY_CONVICTION",
    "WATCH":          "🟡 WATCH",
    "PASS":           "🔴 PASS",
}


def _latest_pick_for(ref_id: str) -> dict | None:
    """Juengster Pick fuer das Instrument inkl. Run-Metadaten."""
    if not table_exists("sig_screen_picks"):
        return None
    df = run_query("""
        SELECT p.*, r.ts AS run_ts, r.universe, r.n_passed, r.n_candidates
        FROM sig_screen_picks p
        JOIN sig_screen_runs r ON r.run_id = p.run_id
        WHERE p.ref_instrument_id = ?
        ORDER BY r.ts DESC LIMIT 1
    """, (ref_id,))
    return df.iloc[0].to_dict() if not df.empty else None


def _existing_thesis(ref_id: str, run_id: str) -> dict | None:
    if not table_exists("sig_screen_thesis"):
        return None
    df = run_query("""
        SELECT * FROM sig_screen_thesis
        WHERE ref_instrument_id = ? AND run_id = ?
        ORDER BY ts DESC LIMIT 1
    """, (ref_id, run_id))
    return df.iloc[0].to_dict() if not df.empty else None


def _run_subprocess(args: list[str], spinner_msg: str,
                     timeout_s: int = 900) -> tuple[bool, str]:
    with st.spinner(spinner_msg):
        try:
            res = subprocess.run(
                args, capture_output=True, text=True,
                timeout=timeout_s, env=os.environ.copy())
        except subprocess.TimeoutExpired:
            return False, f"Timeout nach {timeout_s}s."
    out = (res.stdout or "") + (res.stderr or "")
    return res.returncode == 0, out


def render(ref_id: str, symbol: str) -> None:
    """Screener-Tab fuer das gegebene Instrument rendern."""
    pick = _latest_pick_for(ref_id)

    if not pick:
        st.info(f"{symbol} ist in den bisherigen Screener-Runs nicht als "
                "Pick aufgetaucht. Lauf erst einen Screen mit passenden "
                "Schwellen oder pruefe die Hard-Filter (Sektor-Blacklist, "
                "Min-Market-Cap, Min-Composite).")
        return

    run_id      = pick["run_id"]
    run_ts      = pick.get("run_ts")
    rank        = pick.get("rank")
    n_passed    = pick.get("n_passed")
    n_cand      = pick.get("n_candidates")
    universe    = pick.get("universe")

    # --- Run-Kontext ---
    _run_ts_short = str(run_ts)[:16] if run_ts is not None else "—"
    st.caption(
        f"Pick #**{int(rank)}** im Run vom {_run_ts_short}  ·  "
        f"{n_passed}/{n_cand} bestanden  ·  Universum „{universe}"
        f"\"  ·  run_id `{run_id}`")

    # --- Achsen-Scores ---
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Quality",
              f"{pick['quality_score']:.2f}"
              if pd.notna(pick.get('quality_score')) else "—")
    k2.metric("Growth",
              f"{pick['growth_score']:.2f}"
              if pd.notna(pick.get('growth_score')) else "—")
    k3.metric("Value",
              f"{pick['value_score']:.2f}"
              if pd.notna(pick.get('value_score')) else "—")
    k4.metric("Composite",
              f"{pick['composite_score']:.2f}"
              if pd.notna(pick.get('composite_score')) else "—")

    # --- Trend-Flags ---
    trends = json.loads(pick["trend_flags_json"] or "{}") if pick.get(
        "trend_flags_json") else {}
    _bool_trends = {k: v for k, v in trends.items()
                     if isinstance(v, bool)}
    if _bool_trends:
        _trend_chips = " · ".join(
            f"{k.replace('_', ' ')}: "
            f"{'✓' if v else '✗'}"
            for k, v in _bool_trends.items())
        st.caption(f"**Stufe-2-Trends:** {_trend_chips}")

    st.divider()

    # --- Kriterien-Tabelle ---
    st.markdown("##### Stufe-1-Kriterien")
    crits = json.loads(pick["criteria_detail_json"] or "[]") if pick.get(
        "criteria_detail_json") else []
    if not crits:
        st.caption("Keine Kriterien-Details gespeichert.")
    else:
        df_c = pd.DataFrame([{
            "Achse":     c["axis"],
            "Kriterium": c["name"],
            "Wert":     (de_dec(c["value"], 4)
                         if c["value"] is not None else "—"),
            "Schwelle":  c["threshold"],
            "Status":    "✓" if c["passed"] else "✗",
        } for c in crits])
        st.dataframe(df_c, use_container_width=True, hide_index=True,
                     column_config={
                         "Status": st.column_config.TextColumn(
                             "Status", width="small"),
                     })

    st.divider()

    # --- LLM-Thesis ---
    st.markdown("##### LLM-Thesis")
    thesis = _existing_thesis(ref_id, run_id)
    if thesis is None:
        st.info("Noch keine LLM-Thesis fuer diesen Run. Klick erstellt sie — "
                "Laufzeit 1–3 Minuten (Sec-API + lokales LLM).")
        _no_news = st.checkbox(
            "News-Block weglassen (schneller)",
            value=False, key=f"thesis_sk_nonews_{ref_id}")
        if st.button(f"Analyse für {symbol} anstossen",
                      key=f"thesis_sk_analyze_{ref_id}", type="primary"):
            cmd = [sys.executable, "-m", "modules.screener",
                   "analyze", symbol, "--run-id", run_id]
            if _no_news:
                cmd.append("--no-news")
            ok, out = _run_subprocess(
                cmd, f"LLM analysiert {symbol} …")
            st.code(out[-3000:] or "(kein Output)", language="text")
            if ok:
                st.success("Thesis erstellt.")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error("Analyse fehlgeschlagen — Log siehe oben.")
        return

    # Existierende Thesis anzeigen.
    st.caption(f"{thesis.get('ts')} · {thesis.get('llm_model')}")
    v1, v2, v3 = st.columns(3)
    v1.metric("Verdikt",
              _GLYPH_VERDICT.get(thesis.get("verdict") or "",
                                  thesis.get("verdict") or "—"))
    v2.metric("Conviction",
              f"{thesis['conviction_score']:.0f}"
              if pd.notna(thesis.get('conviction_score')) else "—")
    cit = json.loads(thesis["citations_json"] or "{}") if thesis.get(
        "citations_json") else {}
    v3.metric("Klassifikation", cit.get("classification") or "—")

    if thesis.get("thesis_text"):
        st.markdown(f"**Thesis:** {thesis['thesis_text']}")

    risks = json.loads(thesis["risks_json"] or "[]") if thesis.get(
        "risks_json") else []
    if risks:
        st.markdown("**Risiken:**")
        for r in risks:
            st.markdown(
                f"- {r.get('risk', '?')}  _({r.get('citation', '?')})_")
    if cit.get("moat_assessment"):
        st.markdown(f"**Moat:** {cit['moat_assessment']}")

    if st.button("Neu analysieren",
                  key=f"thesis_sk_reanalyze_{ref_id}"):
        cmd = [sys.executable, "-m", "modules.screener",
               "analyze", symbol, "--run-id", run_id]
        ok, out = _run_subprocess(cmd, f"Neu-Analyse {symbol} …")
        st.code(out[-3000:] or "(kein Output)", language="text")
        if ok:
            st.cache_data.clear()
            st.rerun()
