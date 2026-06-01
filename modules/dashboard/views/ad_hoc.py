"""Ad-Hoc Analysis — Qualitaetspruefung beliebiger Aktien on-Demand.

Frei waehlbarer Ticker (muss NICHT im persistierten Universum sein). Die
Daten werden bei Bedarf direkt von sec-api.io gezogen und NICHT in der
DuckDB gespeichert. Ein In-Memory-Cache (st.cache_data) verhindert nur
unnoetige Doppelaufrufe innerhalb der Session.

Themen nach Michael Shearn, "The Investment Checklist". Start: Bilanz-
staerke (Balance Sheet). Weitere Themen (ROC, Insider, SBC, GAAP vs
non-GAAP) folgen schrittweise.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.dashboard.components.format import _missing, de_dec, de_int
from modules.sec_filings.client import (
    SecApiError, fetch_balance_sheet_from_filing, find_filings,
)


# ---------- Formatierung ----------

def _money(v, cur: str = "USD") -> str:
    if _missing(v):
        return "—"
    a = abs(float(v))
    if a >= 1e9:
        return f"{de_dec(v / 1e9, 2)} Mrd {cur}"
    if a >= 1e6:
        return f"{de_dec(v / 1e6, 1)} Mio {cur}"
    return f"{de_int(v)} {cur}"


def _ratio(v, places: int = 2) -> str:
    return "—" if _missing(v) else de_dec(v, places)


def _pct(v, places: int = 1) -> str:
    return "—" if _missing(v) else de_dec(float(v) * 100.0, places) + " %"


def _div(a, b):
    if _missing(a) or _missing(b) or float(b) == 0:
        return None
    return float(a) / float(b)


# ---------- Daten-Load (cached, keine Persistierung) ----------

@st.cache_data(ttl=3600, show_spinner=False)
def _load_balance(ticker: str, n_years: int):
    """Juengstes Filing (Snapshot) + letzte N 10-K (Trend). Dataclasses."""
    latest_f = find_filings(ticker, n=1, forms=("10-Q", "10-K"))
    annuals = find_filings(ticker, n=n_years, forms=("10-K",))
    latest = (fetch_balance_sheet_from_filing(latest_f[0])
              if latest_f else None)
    hist = []
    for f in annuals:
        bs = fetch_balance_sheet_from_filing(f)
        if bs is not None:
            hist.append(bs)
    return latest, hist


# ---------- Verdict-Heuristik ----------

def _verdict(bs):
    checks = []
    cr = _div(bs.assets_current, bs.liabilities_current)
    if cr is not None:
        checks.append(("Current Ratio > 1,5", cr > 1.5))
    if bs.net_debt is not None:
        checks.append(("Netto-Cash (Net Debt < 0)", bs.net_debt < 0))
    de = _div(bs.total_debt, bs.equity)
    if de is not None:
        checks.append(("Debt/Equity < 0,5", de < 0.5))
    eq = _div(bs.equity, bs.total_assets)
    if eq is not None:
        checks.append(("Eigenkapitalquote > 40 %", eq > 0.40))
    return checks


# =====================================================================
# Seite
# =====================================================================

st.title("🧭 Ad-Hoc Analysis")
st.caption(
    "Qualitaetspruefung beliebiger Aktien nach Shearn, *The Investment "
    "Checklist*. Daten on-Demand von sec-api.io — keine Speicherung.")

_topic = st.selectbox(
    "Thema",
    ["Balance Sheet — Bilanzstaerke",
     "Return on Capital (in Arbeit)",
     "Insider Sales / Buys (in Arbeit)",
     "Stock-based Compensation (in Arbeit)",
     "GAAP vs non-GAAP (in Arbeit)"],
)

_c1, _c2 = st.columns([3, 1])
_ticker = _c1.text_input(
    "Ticker (US-gelistet, EDGAR)", value="",
    placeholder="z. B. AAPL, MSFT, NVDA").strip().upper()
_n_years = _c2.number_input("Jahre (10-K)", min_value=2, max_value=10,
                            value=5, step=1)
_go = st.button("Analysieren", type="primary", disabled=not _ticker)

if _topic != "Balance Sheet — Bilanzstaerke":
    st.info("Dieses Thema ist noch in Arbeit. Aktuell verfuegbar: "
            "Balance Sheet — Bilanzstaerke.")
    st.stop()

if not _go and not _ticker:
    st.stop()

if not _ticker:
    st.stop()

# --- Daten laden ---
try:
    with st.spinner(f"Lade SEC-Filings fuer {_ticker} …"):
        _latest, _hist = _load_balance(_ticker, int(_n_years))
except SecApiError as e:
    st.error(f"sec-api.io: {e}")
    st.stop()
except Exception as e:  # noqa: BLE001
    st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
    st.stop()

if _latest is None:
    st.warning(
        f"Kein verwertbares 10-K/10-Q mit Bilanz fuer **{_ticker}** "
        "gefunden. Tippfehler? Nicht US-gelistet? ETFs/ADRs haben oft "
        "keine XBRL-Bilanz.")
    st.stop()

_cur = _latest.currency or "USD"

# --- Kopf ---
st.markdown(
    f"### {_ticker} — Bilanzstaerke  \n"
    f"Stichtag **{str(_latest.period_end)[:10]}** "
    f"({_latest.form_type}) · eingereicht {str(_latest.filed_at)[:10]}")

# --- Verdict ---
_checks = _verdict(_latest)
if _checks:
    _passed = sum(1 for _, ok in _checks if ok)
    _r = _passed / len(_checks)
    _lines = "  \n".join(
        f"{'✅' if ok else '❌'} {name}" for name, ok in _checks)
    _msg = f"**{_passed}/{len(_checks)} Kriterien erfuellt**  \n{_lines}"
    if _r >= 0.75:
        st.success("Bilanz wirkt **stark**  \n" + _msg)
    elif _r >= 0.5:
        st.info("Bilanz wirkt **solide / gemischt**  \n" + _msg)
    else:
        st.warning("Bilanz wirkt **schwach**  \n" + _msg)

# --- Kennzahlen-Kacheln ---
_cr = _div(_latest.assets_current, _latest.liabilities_current)
_inv = _latest.inventory or 0.0
_quick = _div((_latest.assets_current or 0.0) - _inv,
              _latest.liabilities_current)
_de = _div(_latest.total_debt, _latest.equity)
_eqr = _div(_latest.equity, _latest.total_assets)
_intang = (_latest.goodwill or 0.0) + (_latest.intangibles or 0.0)
_intang_pct = _div(_intang, _latest.total_assets)

_m = st.columns(3)
_m[0].metric("Current Ratio", _ratio(_cr),
             help="Umlaufvermoegen / kurzfr. Verbindlichkeiten")
_m[1].metric("Quick Ratio", _ratio(_quick),
             help="(Umlaufvermoegen − Vorraete) / kurzfr. Verbindlichkeiten")
_m[2].metric("Debt / Equity", _ratio(_de),
             help="Gesamtverschuldung / Eigenkapital")
_m2 = st.columns(3)
_nd = _latest.net_debt
_m2[0].metric("Net Debt" if (_nd or 0) >= 0 else "Net Cash",
              _money(abs(_nd) if _nd is not None else None, _cur),
              help="Gesamtverschuldung − (Cash + kurzfr. Anlagen)")
_m2[1].metric("Eigenkapitalquote", _pct(_eqr),
              help="Eigenkapital / Bilanzsumme")
_m2[2].metric("Goodwill + Intangibles", _pct(_intang_pct),
              help="Anteil immaterieller Posten an der Bilanzsumme")

# --- Trend ueber die Jahre ---
if len(_hist) >= 2:
    _rows = []
    for bs in sorted(_hist, key=lambda b: b.period_end or ""):
        _rows.append({
            "period_end": pd.to_datetime(bs.period_end),
            "current_ratio": _div(bs.assets_current, bs.liabilities_current),
            "debt_to_equity": _div(bs.total_debt, bs.equity),
            "net_debt": bs.net_debt,
            "equity_ratio": _div(bs.equity, bs.total_assets),
        })
    _df = pd.DataFrame(_rows)

    st.markdown("#### Trend (10-K, jaehrlich)")
    _t1, _t2 = st.columns(2)

    _f1 = go.Figure()
    _f1.add_trace(go.Scatter(
        x=_df["period_end"], y=_df["current_ratio"],
        mode="lines+markers", name="Current Ratio",
        line=dict(color="#0F6E56", width=2)))
    _f1.add_trace(go.Scatter(
        x=_df["period_end"], y=_df["debt_to_equity"],
        mode="lines+markers", name="Debt/Equity",
        line=dict(color="#A32D2D", width=2)))
    _f1.update_layout(
        height=300, margin=dict(l=10, r=10, t=30, b=10),
        title="Current Ratio & Debt/Equity",
        legend=dict(orientation="h", y=-0.2), hovermode="x unified")
    _t1.plotly_chart(_f1, use_container_width=True)

    _nd_col = ["#1D9E75" if (v is not None and v < 0) else "#A32D2D"
               for v in _df["net_debt"]]
    _f2 = go.Figure(go.Bar(
        x=_df["period_end"], y=_df["net_debt"], marker_color=_nd_col,
        hovertemplate="%{x|%Y-%m-%d}<br>Net Debt: %{y:,.0f}<extra></extra>"))
    _f2.update_layout(
        height=300, margin=dict(l=10, r=10, t=30, b=10),
        title=f"Net Debt ({_cur}) — gruen = Netto-Cash",
        yaxis_title=_cur)
    _t2.plotly_chart(_f2, use_container_width=True)

# --- Rohwerte ---
with st.expander("Bilanz-Rohwerte (zuletzt)"):
    _raw = {
        "Cash & Equivalents": _latest.cash,
        "Kurzfristige Anlagen": _latest.short_term_invest,
        "Umlaufvermoegen": _latest.assets_current,
        "Kurzfr. Verbindlichkeiten": _latest.liabilities_current,
        "Bilanzsumme": _latest.total_assets,
        "Gesamtverbindlichkeiten": _latest.total_liabilities,
        "Eigenkapital": _latest.equity,
        "Langfr. Schulden": _latest.long_term_debt,
        "Kurzfr. Schulden": _latest.current_debt,
        "Vorraete": _latest.inventory,
        "Goodwill": _latest.goodwill,
        "Immaterielle (o. Goodwill)": _latest.intangibles,
    }
    _tbl = pd.DataFrame(
        [{"Posten": k, "Wert": _money(v, _cur)} for k, v in _raw.items()])
    st.dataframe(_tbl, use_container_width=True, hide_index=True)
    if _latest.warnings:
        st.caption("Hinweise: " + " · ".join(_latest.warnings))

st.caption(
    "Quelle: sec-api.io (XBRL-to-JSON). On-Demand geladen, nicht "
    "persistiert. Kennzahlen aus konsolidierten US-GAAP-Bilanzposten; "
    "fehlende XBRL-Tags fuehren zu '—'.")
