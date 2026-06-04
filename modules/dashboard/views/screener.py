"""Screener-Dashboard — Quality-GARP-Pipeline interaktiv tunen.

Drei Bereiche:
  1. Run-Selector + Summary des gewaehlten Runs.
  2. Picks-Tabelle (Rang, Symbol, Sektor, Q/G/V-Score, Composite,
     Trend-Flags). Zeilen-Auswahl oeffnet Detail-Panel mit
     Kriterien-Pass/Fail + Metriken + LLM-Thesis (sofern vorhanden).
  3. Parameter-Tuning-Expander mit Slidern fuer alle Schwellen +
     Achsen-Gewichte. "Run starten" feuert einen neuen screen-Lauf an,
     "Analyse anstossen" pro Pick fuer Stufe 3.

Schreibende Ops laufen via subprocess `python -m modules.screener ...` —
das ist sauber isoliert und nutzt den gleichen Code-Pfad wie das CLI.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

import pandas as pd
import streamlit as st

from modules.dashboard import quality as ql
from modules.dashboard.components.format import de_dec, de_int
from modules.dashboard.db import run_query, table_exists


@st.cache_data(ttl=86400, show_spinner=False)
def _quality_score(symbol: str):
    """On-Demand Gesamt-Qualitaets-Score (Shearn, 0-100) — pro Ticker
    gecached. None bei Fehler/fehlenden Daten."""
    try:
        return ql.overall_score(symbol).get("score")
    except Exception:  # noqa: BLE001
        return None


st.title("🎯 Screener — Quality-GARP")
st.caption("Trichter: Stufe 1+2 regelbasiert, Stufe 3 on-demand mit lokalem "
           "LLM. Schwellen unten justierbar.")


# Vorhandensein der Tabellen pruefen — beim ersten Setup gibt es sie noch nicht.
if not table_exists("sig_screen_runs"):
    st.info("Screener-Schema noch nicht angelegt. Auf nova-hub:  \n"
            "`python -m modules.screener init`  \n"
            "`python -m modules.screener screen`")
    st.stop()


# ---------- Helfer ----------

def _fmt_score(v):
    # Achsen-/Composite-Scores 0..1 -> 0..100 (einheitlich mit den
    # Dashboard-Scores in der Unternehmens-Analyse).
    return f"{round(v * 100)}" if v is not None and pd.notna(v) else "—"


def _trend_chips(trends: dict) -> str:
    """„rev↑ marg↑ profit↑" o.ae., je nach Trend-Flags."""
    out = []
    label_for = {
        "revenue_accelerating": "rev",
        "margin_expanding":     "marg",
        "profit_improving":     "profit",
    }
    for k, v in trends.items():
        if k not in label_for:
            continue
        if v is True:
            out.append(label_for[k] + "↑")
        elif v is False:
            out.append(label_for[k] + "↓")
    return "  ".join(out) if out else "—"


def _run_subprocess(args: list[str], spinner_msg: str,
                     timeout_s: int = 300) -> tuple[bool, str]:
    """Subprozess starten, Output sammeln. Returns (success, output_text).

    NOVA_SEC_API_KEY etc. werden vom Daemon-Env durchgereicht.
    """
    with st.spinner(spinner_msg):
        try:
            res = subprocess.run(
                args, capture_output=True, text=True,
                timeout=timeout_s, env=os.environ.copy())
        except subprocess.TimeoutExpired:
            return False, f"Timeout nach {timeout_s}s."
    out = (res.stdout or "") + (res.stderr or "")
    return res.returncode == 0, out


# ---------- 1. Run-Selector + Summary ----------

runs_df = run_query("""
    SELECT run_id, ts, universe, n_candidates, n_passed
    FROM sig_screen_runs
    ORDER BY ts DESC LIMIT 30
""")

if runs_df.empty:
    st.warning("Noch kein Screener-Run vorhanden. Starte den ersten unten "
               "via 'Run starten', oder auf nova-hub mit "
               "`python -m modules.screener screen`.")
    selected_run = None
    selected_params: dict = {}
else:
    _labels = {
        f"{r['ts'].strftime('%Y-%m-%d %H:%M')}  ·  "
        f"{r['n_passed']:>3d}/{r['n_candidates']:>3d}  ·  {r['run_id']}":
            r["run_id"]
        for _, r in runs_df.iterrows()
    }
    _chosen_label = st.selectbox("Run", list(_labels.keys()), index=0)
    selected_run = _labels[_chosen_label]

    _r = runs_df[runs_df["run_id"] == selected_run].iloc[0]
    selected_params = json.loads(
        run_query("SELECT params_json FROM sig_screen_runs WHERE run_id=?",
                  (selected_run,)).iloc[0]["params_json"])

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Kandidaten",  de_int(_r["n_candidates"]))
    k2.metric("Bestanden",   de_int(_r["n_passed"]))
    k3.metric("Universum",   _r["universe"] or "—")
    k4.metric("Lauf",        _r["ts"].strftime("%Y-%m-%d %H:%M"))


# ---------- 2. Picks-Tabelle + Detail-Panel ----------

if selected_run:
    picks = run_query("""
        SELECT rank, symbol, name, sector, market_cap,
               quality_score, growth_score, value_score, composite_score,
               trend_flags_json, criteria_detail_json, metrics_json,
               ref_instrument_id
        FROM sig_screen_picks
        WHERE run_id = ?
        ORDER BY rank
    """, (selected_run,))

    if picks.empty:
        st.info("Dieser Run hat keine Picks. Schwellen evtl. zu streng.")
    else:
        st.divider()
        st.subheader(f"Picks — {len(picks)} Survivors")

        # Trend-Chip + Score-Spalten fuer die Anzeige aufbereiten.
        display = picks.copy()
        display["trends"] = display["trend_flags_json"].apply(
            lambda j: _trend_chips(json.loads(j) if j else {}))
        display["MV (Mrd)"] = display["market_cap"].apply(
            lambda v: de_dec(v / 1e9, 1) if pd.notna(v) else "—")

        # --- Optional: on-Demand Gesamt-Qualitaets-Score (Shearn) ---
        _q1, _q2 = st.columns([2, 1])
        _q_on = _q1.toggle(
            "Gesamt-Qualitaets-Score laden (on-Demand, SEC-Filings)",
            key="sk_qscore_on",
            help="Berechnet je Pick den 5-Themen-Score (Shearn-Checkliste) "
                 "aus SEC-Filings — langsam, pro Ticker 24 h gecached. "
                 "Ergaenzt das schnelle Q/G/V-Composite aus Fundamentaldaten.")
        _min_q = _q2.slider("Min Q-Score", 0, 100, 0, 5, key="sk_minq",
                            disabled=not _q_on)
        cols = ["rank", "symbol", "name", "sector", "MV (Mrd)",
                "quality_score", "growth_score", "value_score",
                "composite_score", "trends"]
        if _q_on:
            with st.spinner(f"Berechne Gesamt-Qualitaets-Score fuer "
                            f"{len(display)} Picks …"):
                display["qscore"] = [
                    (_quality_score(s) if s else None)
                    for s in display["symbol"]]
            if _min_q > 0:
                keep = display["qscore"].fillna(-1) >= _min_q
                display, picks = display[keep], picks[keep]
            display = display.reset_index(drop=True)
            picks = picks.reset_index(drop=True)
            cols.insert(9, "qscore")  # vor "trends"
            st.caption(f"{len(display)} Picks mit Q-Score ≥ {_min_q}.")

        _colcfg = {
            "rank": st.column_config.NumberColumn("#", width="small"),
            "symbol": st.column_config.TextColumn("Symbol", width="small"),
            "name": st.column_config.TextColumn("Name"),
            "sector": st.column_config.TextColumn("Sektor"),
            "MV (Mrd)": st.column_config.TextColumn("MV (Mrd)", width="small"),
            "quality_score": "Quality", "growth_score": "Growth",
            "value_score": "Value", "composite_score": "Composite",
            "qscore": st.column_config.NumberColumn("Q-Score", width="small"),
            "trends": st.column_config.TextColumn("Trends", width="small"),
        }
        _evt = st.dataframe(
            display[cols].style.format({
                "quality_score": _fmt_score, "growth_score": _fmt_score,
                "value_score": _fmt_score, "composite_score": _fmt_score,
            }),
            use_container_width=True, height=460, hide_index=True,
            on_select="rerun", selection_mode="single-row",
            key="screener_picks_table", column_config=_colcfg)

        st.caption(
            "Scores 0–100 = Anteil erfuellter Kriterien je Achse × 100. "
            "Quality: ROIC, Brutto-/Nettomarge, Net-Debt/EBITDA · "
            "Growth: Umsatz-/Gewinn-CAGR (5 J), Umsatz-QoQ · "
            "Value: PEG, FCF-Rendite, KGV (fwd). Composite = gewichteter "
            "Mittel der drei Achsen (Gewichte je Run, siehe Tuning unten). "
            "Quelle: ref_fundamentals (yfinance-Snapshot) — unterscheidet "
            "sich vom on-Demand Gesamt-Qualitaets-Score (Shearn-Checkliste) "
            "in der Unternehmens-Analyse.")

        # --- Zeilen-Klick: Sprung in die Unternehmens-Analyse ---
        # Der vollstaendige per-Name-Blick (Ueberblick/Kennzahlen, GuV,
        # Geschaeft, Burggraben/Branche, Bilanz, Management, Gewinne,
        # Bewertung, Portfolio/Signale) lebt zentral in der
        # Unternehmens-Analyse. So vermeiden wir doppelte Pflege.
        _sel = _evt.selection["rows"]
        if _sel:
            _row = picks.iloc[_sel[0]]
            st.session_state["ana_mode"] = "Freitext"
            st.session_state["ana_free"] = str(_row["symbol"] or "").upper()
            st.switch_page("views/analysis.py")


# ---------- 3. Parameter-Tuning + Run starten ----------

st.divider()

with st.expander("⚙️ Parameter tunen + neuen Lauf starten", expanded=False):
    # Defaults: aus dem ausgewaehlten Run, fallback auf FilterConfig-Defaults.
    DEF = {
        "min_roic": 0.12, "min_gross_margin": 0.35, "min_net_margin": 0.12,
        "max_net_debt_to_ebitda": 3.0,
        "min_revenue_cagr_5y": 0.08, "min_net_income_cagr_5y": 0.08,
        "min_revenue_q_yoy": 0.05,
        "max_peg_ratio": 2.0, "min_fcf_yield": 0.025,
        "max_pe_forward": 40.0,
        "min_market_cap": 5e9,
        "weight_quality": 0.40, "weight_growth": 0.35, "weight_value": 0.25,
        "top_n": 30, "min_composite_score": 0.50,
    }
    base = {**DEF, **selected_params}

    c_q, c_g, c_v = st.columns(3)

    with c_q:
        st.markdown("**Quality**")
        p_roic   = st.slider("ROIC ≥",          0.0, 0.40,
                              float(base["min_roic"]), 0.01,
                              key="sk_p_roic", format="%.2f")
        p_gm     = st.slider("Brutto­marge ≥", 0.0, 0.90,
                              float(base["min_gross_margin"]), 0.05,
                              key="sk_p_gm", format="%.2f")
        p_nm     = st.slider("Netto­marge ≥",   0.0, 0.50,
                              float(base["min_net_margin"]), 0.01,
                              key="sk_p_nm", format="%.2f")
        p_nde    = st.slider("Net Debt/EBITDA ≤", 0.0, 6.0,
                              float(base["max_net_debt_to_ebitda"]), 0.1,
                              key="sk_p_nde", format="%.1f")

    with c_g:
        st.markdown("**Growth**")
        p_rcagr  = st.slider("Revenue-CAGR 5J ≥", 0.0, 0.30,
                              float(base["min_revenue_cagr_5y"]), 0.01,
                              key="sk_p_rcagr", format="%.2f")
        p_ncagr  = st.slider("Net-Income-CAGR 5J ≥", 0.0, 0.40,
                              float(base["min_net_income_cagr_5y"]), 0.01,
                              key="sk_p_ncagr", format="%.2f")
        p_qyoy   = st.slider("Revenue Q-YoY ≥",  -0.10, 0.40,
                              float(base["min_revenue_q_yoy"]), 0.01,
                              key="sk_p_qyoy", format="%.2f")

    with c_v:
        st.markdown("**Valuation**")
        p_peg    = st.slider("PEG ≤",           0.5, 4.0,
                              float(base["max_peg_ratio"]), 0.1,
                              key="sk_p_peg", format="%.1f")
        p_fcfy   = st.slider("FCF-Rendite ≥",  0.0, 0.10,
                              float(base["min_fcf_yield"]), 0.005,
                              key="sk_p_fcfy", format="%.3f")
        p_pefw   = st.slider("KGV-Fwd ≤",       10.0, 80.0,
                              float(base["max_pe_forward"]), 1.0,
                              key="sk_p_pefw", format="%.0f")

    st.divider()
    c_w1, c_w2, c_w3, c_w4 = st.columns(4)
    p_wq = c_w1.slider("Gewicht Quality", 0.0, 1.0,
                        float(base["weight_quality"]), 0.05,
                        key="sk_p_wq", format="%.2f")
    p_wg = c_w2.slider("Gewicht Growth",  0.0, 1.0,
                        float(base["weight_growth"]),  0.05,
                        key="sk_p_wg", format="%.2f")
    p_wv = c_w3.slider("Gewicht Value",   0.0, 1.0,
                        float(base["weight_value"]),   0.05,
                        key="sk_p_wv", format="%.2f")
    p_mcap = c_w4.slider("Min Market Cap (Mrd)", 0.0, 100.0,
                          float(base["min_market_cap"]) / 1e9, 1.0,
                          key="sk_p_mcap", format="%.0f")

    c_t1, c_t2 = st.columns(2)
    p_minc = c_t1.slider("Min Composite-Score (0–100)", 0, 100,
                          int(round(float(base["min_composite_score"]) * 100)),
                          5, key="sk_p_minc")
    p_topn = c_t2.slider("Top-N speichern",   5, 100,
                          int(base["top_n"]), 1,
                          key="sk_p_topn")

    # Gewichts-Summe-Hinweis
    _w_sum = p_wq + p_wg + p_wv
    if abs(_w_sum - 1.0) > 0.001:
        st.caption(f"ℹ Achsen-Gewichte summieren zu {_w_sum:.2f} — "
                   "die Composite-Skala verschiebt sich entsprechend.")

    if st.button("🚀 Neuen Lauf starten", type="primary", key="sk_run_btn"):
        params = {
            "min_roic":                p_roic,
            "min_gross_margin":        p_gm,
            "min_net_margin":          p_nm,
            "max_net_debt_to_ebitda":  p_nde,
            "min_revenue_cagr_5y":     p_rcagr,
            "min_net_income_cagr_5y":  p_ncagr,
            "min_revenue_q_yoy":       p_qyoy,
            "max_peg_ratio":           p_peg,
            "min_fcf_yield":           p_fcfy,
            "max_pe_forward":          p_pefw,
            "min_market_cap":          p_mcap * 1e9,
            "weight_quality":          p_wq,
            "weight_growth":           p_wg,
            "weight_value":            p_wv,
            "min_composite_score":     p_minc / 100.0,
            "top_n":                   p_topn,
        }
        # Tmp-Params-File schreiben + screen aufrufen.
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as f:
            json.dump(params, f)
            tmp_path = f.name
        cmd = [sys.executable, "-m", "modules.screener", "screen",
               "--params-file", tmp_path]
        ok, out = _run_subprocess(cmd, "Screen laeuft …", timeout_s=180)
        try:
            pathlib.Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        st.code(out[-3000:] or "(kein Output)", language="text")
        if ok:
            st.success("Neuer Run angelegt.")
            st.cache_data.clear()
            st.rerun()
        else:
            st.error("Screen fehlgeschlagen — Log siehe oben.")
