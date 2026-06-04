"""Thesis-Cockpit — Unternehmens-Analyse pro Name.

Buendelt fuer ein einzelnes Instrument alle Bausteine, die fuer einen
Buy-&-Hold- / CSP-Anlagestil die Kauf- bzw. Halte-Entscheidung stuetzen:
Finanzkennzahlen, Wachstum & Momentum, Chart, Branchen-Einordnung
(Dominanz), Earnings-Termin, News sowie Signale (Empfehlungen + Alerts).

Read-only — konsumiert ref_fundamentals_latest, v_mkt_holdings,
mkt_quotes_daily, ref_earnings_calendar, ref_sa_articles, sig_* sowie die
Watchlists. Keine Schreibzugriffe.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.dashboard.components.format import de_dec, de_int
from modules.dashboard.db import run_query, table_exists


st.title("🔬 Thesis-Cockpit")
st.caption("Eine Seite pro Name — die Bausteine deiner Kauf- und "
           "Halte-Entscheidung an einem Ort.")


# ---------- Formatierungs-Helfer ----------

def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and pd.isna(x))


def _ratio(x, places: int = 2) -> str:
    """Verhaeltniszahl, deutsch:  23.455 -> '23,46'."""
    return de_dec(x, places) if not _missing(x) else "—"


def _pct(x, places: int = 1) -> str:
    """Bruchteil -> Prozent:  0.36572 -> '36,6 %'."""
    if _missing(x):
        return "—"
    return de_dec(float(x) * 100.0, places) + " %"


def _pct_raw(x, places: int = 2) -> str:
    """Bereits in Prozent geliefert:  3.83 -> '3,83 %'."""
    if _missing(x):
        return "—"
    return de_dec(float(x), places) + " %"


def _fmt_cap(v) -> str:
    if _missing(v):
        return "—"
    v = float(v)
    if abs(v) >= 1e12:
        return f"{de_dec(v / 1e12, 2)} Bio"
    if abs(v) >= 1e9:
        return f"{de_dec(v / 1e9, 1)} Mrd"
    return f"{de_int(v / 1e6)} Mio"


def _fmt_money_big(v, currency: str = "USD") -> str:
    """GuV-Betrag, deutsch:  75200000000 -> '75,20 Mrd USD'."""
    if _missing(v):
        return "—"
    a = abs(float(v))
    if a >= 1e9:
        s = f"{de_dec(v / 1e9, 2)} Mrd"
    elif a >= 1e6:
        s = f"{de_dec(v / 1e6, 0)} Mio"
    else:
        s = de_int(v)
    return f"{s} {currency}".strip()


# ---------- Universum waehlen ----------

_WATCHLISTS = {
    "Kaufkandidaten": "buy_candidates",
    "CSP-Universe":   "csp_universe",
    "Beobachtung":    "observation",
    "Value-Picks":    "value_picks",
}

uni_choice = st.radio(
    "Universum",
    ["Portfolio", *_WATCHLISTS.keys(), "Alle (Fundamentaldaten)"],
    horizontal=True,
    key="thesis_universe",
)

if uni_choice == "Portfolio":
    uni = run_query(
        "SELECT ref_instrument_id, symbol, name, SUM(mtm_eur) AS sort_mv "
        "FROM v_mkt_holdings GROUP BY 1, 2, 3 ORDER BY sort_mv DESC NULLS LAST")
elif uni_choice == "Alle (Fundamentaldaten)":
    uni = run_query(
        "SELECT f.ref_instrument_id, i.symbol, i.name "
        "FROM ref_fundamentals_latest f "
        "LEFT JOIN ref_instruments i USING (ref_instrument_id) "
        "ORDER BY i.symbol")
else:
    uni = run_query(
        "SELECT m.ref_instrument_id, i.symbol, i.name "
        "FROM list_watchlist_members m "
        "LEFT JOIN ref_instruments i USING (ref_instrument_id) "
        "WHERE m.watchlist_id = ? ORDER BY i.symbol",
        (_WATCHLISTS[uni_choice],))

if uni.empty:
    st.info(f"Universum „{uni_choice}“ ist leer.")
    st.stop()

uni = uni.drop_duplicates("ref_instrument_id").reset_index(drop=True)
_opts = uni["ref_instrument_id"].tolist()
_label = {
    r["ref_instrument_id"]: f"{r['symbol'] or r['ref_instrument_id']} — {r['name'] or ''}"
    for _, r in uni.iterrows()
}

ref_id = st.selectbox(
    f"Instrument ({len(_opts)} im Universum)",
    _opts, format_func=lambda x: _label.get(x, x), key="thesis_instrument")

_row_uni = uni[uni["ref_instrument_id"] == ref_id].iloc[0]
symbol = _row_uni["symbol"] or ref_id
name   = _row_uni["name"] or ""


# ---------- Stammdaten laden ----------

fund_all = run_query("SELECT * FROM ref_fundamentals_latest")
fund = fund_all[fund_all["ref_instrument_id"] == ref_id]
f = fund.iloc[0] if not fund.empty else None

held = run_query(
    "SELECT * FROM v_mkt_holdings WHERE ref_instrument_id = ?", (ref_id,))
port_total = run_query("SELECT SUM(mtm_eur) AS t FROM v_mkt_holdings")
port_mv = float(port_total["t"].iloc[0]) if not port_total.empty \
    and pd.notna(port_total["t"].iloc[0]) else 0.0


# ---------- Header ----------

st.divider()
st.subheader(f"{symbol} — {name}")

sector   = (f["sector"]   if f is not None else None) or "—"
industry = (f["industry"] if f is not None else None) or "—"
_cap     = f["market_cap"] if f is not None else None
_stand   = f["ts"] if f is not None else None
st.caption(
    f"🏷 {sector}  ·  {industry}  ·  Market Cap {_fmt_cap(_cap)} "
    f"{(f['country'] or '') if f is not None else ''}"
    + (f"  ·  Fundamentaldaten-Stand {str(_stand)[:10]}"
       if _stand is not None else "  ·  keine Fundamentaldaten"))

if held.empty:
    st.caption("📌 Nicht im Portfolio — Beobachtung / Kandidat.")
else:
    qty   = float(held["quantity"].sum())
    mv    = float(held["mtm_eur"].sum(skipna=True))
    cost  = float(held["cost_total_eur"].sum(skipna=True))
    pnl   = float(held["pnl_eur"].sum(skipna=True))
    weight = (mv / port_mv * 100.0) if port_mv else None
    h1, h2, h3, h4, h5 = st.columns(5)
    h1.metric("Menge",            de_int(qty))
    h2.metric("Marktwert (EUR)",  de_int(mv))
    h3.metric("Einstand (EUR)",   de_int(cost))
    h4.metric("Δ unreal. (EUR)",  de_int(pnl),
              delta=f"{pnl / cost * 100:+.1f} %" if cost else None)
    h5.metric("Portfolio-Gewicht",
              f"{de_dec(weight, 1)} %" if weight is not None else "—",
              help="Anteil am Gesamt-Marktwert (EUR).")


# ---------- Thesis-Ampel ----------

st.divider()
st.markdown("##### Thesis-Ampel")

# 1-Jahres-Kursrendite vorab fuer die Ampel
_px_hist = run_query("""
    WITH ranked AS (
        SELECT ts, close, source,
               ROW_NUMBER() OVER (PARTITION BY ts ORDER BY
                   CASE source WHEN 'ib' THEN 1 WHEN 'yfinance' THEN 2
                               ELSE 9 END) AS rk
        FROM mkt_quotes_daily WHERE ref_instrument_id = ?
    )
    SELECT ts, close FROM ranked WHERE rk = 1 ORDER BY ts
""", (ref_id,))

_returns: dict[str, float] = {}
_last_px = _last_dt = None
if not _px_hist.empty:
    _px_hist["ts"] = pd.to_datetime(_px_hist["ts"])
    _s = _px_hist.set_index("ts")["close"].dropna()
    if not _s.empty:
        _last_px, _last_dt = float(_s.iloc[-1]), _s.index[-1]
        for _k, _d in {"1 Mon": 30, "3 Mon": 91, "6 Mon": 182,
                       "1 Jahr": 365, "3 Jahre": 1095}.items():
            _prior = _s[_s.index <= _last_dt - timedelta(days=_d)]
            if not _prior.empty and _prior.iloc[-1]:
                _returns[_k] = _last_px / float(_prior.iloc[-1]) - 1.0

a1, a2, a3, a4, a5 = st.columns(5)
if f is not None:
    a1.metric("Bewertung (KGV fwd)", _ratio(f["pe_forward"]),
              help=f"PEG-Ratio: {_ratio(f['peg_ratio'])}")
    a2.metric("Nettomarge", _pct(f["net_margin"]))
    _roic = f["roic"] if not _missing(f["roic"]) else f["roe"]
    a3.metric("Kapitalrendite",
              _pct(f["roic"]) if not _missing(f["roic"]) else _pct(f["roe"]),
              help="ROIC — falls leer, ersatzweise Eigenkapitalrendite.")
    a4.metric("Schulden / EBITDA", _ratio(f["net_debt_to_ebitda"]),
              help="Nettoverschuldung im Verhaeltnis zum EBITDA.")
else:
    for _c in (a1, a2, a3, a4):
        _c.metric("—", "—")
_r1y = _returns.get("1 Jahr")
a5.metric("Kurs (1 Jahr)",
          _ratio(_last_px) if _last_px is not None else "—",
          delta=f"{_r1y * 100:+.1f} %" if _r1y is not None else None)


# ---------- Tabs ----------

t_screen = st.tabs(["Screener"])[0]


# --- Screener ---
with t_screen:
    from modules.dashboard.views import _screener_detail
    _screener_detail.render(ref_id, symbol)
