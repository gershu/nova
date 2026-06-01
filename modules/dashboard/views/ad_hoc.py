"""Ad-Hoc Analysis — Qualitaetspruefung beliebiger Aktien on-Demand.

Frei waehlbarer Ticker (muss NICHT im persistierten Universum sein). Die
Daten werden bei Bedarf direkt von sec-api.io gezogen und NICHT in der
DuckDB gespeichert. Ein In-Memory-Cache (st.cache_data) verhindert nur
unnoetige Doppelaufrufe innerhalb der Session.

Themen nach Michael Shearn, "The Investment Checklist":
  1. Balance Sheet — Bilanzstaerke
  2. Return on Capital — ROIC / ROCE / ROE / ROA
Weitere (Insider, SBC, GAAP vs non-GAAP) folgen schrittweise.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import pathlib
from datetime import date, timedelta

import streamlit as st
import yaml

from modules.dashboard.components.format import _missing, de_dec, de_int
from modules.sec_filings.client import (
    INSIDER_CODE_LABELS, SecApiError, analyze_non_gaap,
    fetch_balance_sheet_from_filing, fetch_earnings_history_from_filing,
    fetch_beneficial_ownership_detail, fetch_exhibit_text,
    fetch_institutional_holdings, fetch_insider_first_filing,
    fetch_insider_transactions,
    fetch_mgmt_changes, fetch_sbc_from_filing, fetch_statements_from_filing,
    fetch_year_metrics_from_filing, find_earnings_exhibits, find_filings,
    get_issuer_cik,
)


# ---------- Score-Konfiguration (config/ad_hoc_score.yaml) ----------

_SCORE_CFG_PATH = (pathlib.Path(__file__).resolve().parents[3]
                   / "config" / "ad_hoc_score.yaml")

_DEFAULT_SCORE_CFG = {
    "weights": {
        "return_on_capital": 0.30, "balance_sheet": 0.25,
        "stock_based_comp": 0.20, "gaap_vs_non_gaap": 0.15,
        "insider": 0.10,
    },
    "bands": {"strong": 70, "mixed": 40},
    "thresholds": {
        "balance_sheet": {"current_ratio_min": 1.5,
                          "debt_to_equity_max": 0.5,
                          "equity_ratio_min": 0.40},
        "return_on_capital": {"roic_min": 0.15, "roe_min": 0.15,
                              "roa_min": 0.06},
        "stock_based_comp": {"sbc_to_revenue_max": 0.05,
                             "sbc_to_cfo_max": 0.15,
                             "dilution_cagr_max": 0.01},
        "gaap_vs_non_gaap": {"mentions_max": 15, "categories_max": 3},
        "insider": {"cluster_buyers_min": 3},
    },
    "insider_conviction": {
        "weights": {"ceo_buy": 35, "cfo_buy": 25, "cluster_buy": 20,
                    "first_buy": 15, "meaningful_sell": 25},
        "cluster_buyers_min": 3,
        "meaningful_sell_pct": 0.20,
        "signal": {"bullish_min": 35, "bearish_max": -20},
    },
    "earnings_quality": {
        "weights": {"sbc": 0.25, "acquisition": 0.15, "restructuring": 0.15,
                    "litigation": 0.15, "tax": 0.15, "one_time": 0.15},
        "bands": {"strong": 70, "mixed": 40},
        "sbc_thresholds": {"clean": 0.05, "heavy": 0.15},
    },
    "management": {
        "smart_money": ["BERKSHIRE HATHAWAY", "BAILLIE GIFFORD", "FUNDSMITH",
                        "AKRE CAPITAL", "RUANE", "TCI FUND", "LONE PINE",
                        "PRIMECAP", "CAPITAL RESEARCH", "T. ROWE PRICE",
                        "T ROWE PRICE", "MARKEL", "TWEEDY", "DODGE & COX"],
    },
    "moat": {
        "weights": {"gross_margin_trend": 0.22, "roic_stability": 0.22,
                    "fcf_margin": 0.18, "rnd_efficiency": 0.13,
                    "market_share_proxy": 0.15, "buybacks": 0.10},
        "bands": {"strong": 70, "mixed": 40},
        "thresholds": {
            "gross_margin": {"improve_pp": 1.0, "stable_pp": -1.0},
            "roic_stability": {"mean_min": 0.12, "cv_max": 0.35},
            "fcf_margin": {"high": 0.15, "mid": 0.05},
            "rnd_efficiency": {"high": 1.5, "mid": 0.5},
            "market_share_proxy": {"rev_cagr_high": 0.10,
                                   "rev_cagr_mid": 0.03},
            "buybacks": {"shrink_cagr": -0.01, "dilute_cagr": 0.01},
        },
    },
}


def _load_score_cfg() -> dict:
    """YAML-Config laden und ueber die Defaults mergen (2 Ebenen tief)."""
    import copy
    cfg = copy.deepcopy(_DEFAULT_SCORE_CFG)
    try:
        if _SCORE_CFG_PATH.is_file():
            loaded = yaml.safe_load(_SCORE_CFG_PATH.read_text()) or {}
            for section, vals in loaded.items():
                if isinstance(vals, dict) and isinstance(
                        cfg.get(section), dict):
                    for k, v in vals.items():
                        if isinstance(v, dict) and isinstance(
                                cfg[section].get(k), dict):
                            cfg[section][k].update(v)
                        else:
                            cfg[section][k] = v
                else:
                    cfg[section] = vals
    except Exception:  # noqa: BLE001 — Defaults bleiben gueltig
        pass
    return cfg


SCORE_CFG = _load_score_cfg()


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


def _eps(v, cur: str = "USD") -> str:
    return "—" if _missing(v) else f"{de_dec(v, 2)} {cur}"


def _trend_ampel(vals, up: float = 0.5, down: float = -0.5):
    """Trend einer Quoten-Reihe (Bruchteile) -> (cur, slope_pp_p.a., emoji, dc).

    slope = lineare Regressionssteigung in %-Punkten je Periode. Ampel:
    >= up steigend (gruen), <= down fallend (rot), sonst stabil (gelb).
    """
    pts = [(i, v * 100.0) for i, v in enumerate(vals) if v is not None]
    if not pts:
        return None, None, "⚪", "off"
    cur = pts[-1][1] / 100.0
    if len(pts) < 2:
        return cur, None, "⚪", "off"
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    denom = sum((p[0] - mx) ** 2 for p in pts)
    slope = (sum((p[0] - mx) * (p[1] - my) for p in pts) / denom
             if denom else 0.0)
    if slope >= up:
        return cur, slope, "🟢", "normal"
    if slope <= down:
        return cur, slope, "🔴", "normal"
    return cur, slope, "🟡", "off"


def _verdict_box(checks, strong="stark", mixed="solide / gemischt",
                 weak="schwach", lead="Bilanz"):
    """Rendert eine Verdict-Box aus [(name, bool), ...]."""
    if not checks:
        return
    passed = sum(1 for _, ok in checks if ok)
    r = passed / len(checks)
    lines = "  \n".join(
        f"{'✅' if ok else '❌'} {name}" for name, ok in checks)
    msg = f"**{passed}/{len(checks)} Kriterien erfuellt**  \n{lines}"
    if r >= 0.75:
        st.success(f"{lead} wirkt **{strong}**  \n" + msg)
    elif r >= 0.5:
        st.info(f"{lead} wirkt **{mixed}**  \n" + msg)
    else:
        st.warning(f"{lead} wirkt **{weak}**  \n" + msg)


# ---------- Daten-Load (cached, keine Persistierung) ----------

@st.cache_data(ttl=3600, show_spinner=False)
def _load_balance(ticker: str, n_years: int):
    """Juengstes Filing (Snapshot) + letzte N 10-K (Trend)."""
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


@st.cache_data(ttl=3600, show_spinner=False)
def _load_returns(ticker: str, n_years: int):
    """Letzte N 10-K: je (GuV, Bilanz) aus einem XBRL-Call."""
    annuals = find_filings(ticker, n=n_years, forms=("10-K",))
    rows = []
    for f in annuals:
        inc, bs = fetch_statements_from_filing(f)
        if inc is not None and bs is not None:
            rows.append((inc, bs))
    rows.sort(key=lambda t: t[0].period_end or "")
    return rows


@st.cache_data(ttl=86400, show_spinner=False)
def _issuer_cik(ticker: str):
    """Konstante Emittenten-CIK (faengt Ticker-Umbenennungen ab)."""
    try:
        return get_issuer_cik(ticker)
    except Exception:  # noqa: BLE001
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _load_insider(ticker: str):
    """Flache Insider-Transaktionsliste (Form 3/4/5) — per issuer.cik."""
    return fetch_insider_transactions(ticker, n=300,
                                      issuer_cik=_issuer_cik(ticker))


@st.cache_data(ttl=3600, show_spinner=False)
def _load_mgmt_changes(ticker: str):
    """8-K Item 5.02 Filings (Management-Wechsel)."""
    return fetch_mgmt_changes(ticker, n=50)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_institutional(ticker: str):
    """Institutionelle 13F-Positionen (Best-effort, groesste Halter)."""
    try:
        return fetch_institutional_holdings(ticker, n=50)
    except Exception as e:  # noqa: BLE001
        return {"holdings": [], "error": f"{e.__class__.__name__}: {e}"}


@st.cache_data(ttl=86400, show_spinner=False)
def _load_beneficial(ticker: str):
    """Exakte Management-Beteiligung aus der DEF 14A (Gruppe) + Diagnose."""
    try:
        return fetch_beneficial_ownership_detail(ticker)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{e.__class__.__name__}: {e}", "group_pct": None,
                "group_shares": None}


@st.cache_data(ttl=86400, show_spinner=False)
def _load_first_filing(ticker: str, owner: str, owner_cik=None):
    """Fruehestes Insider-Filing einer Person (Tenure-Beginn)."""
    try:
        return fetch_insider_first_filing(ticker, owner, owner_cik,
                                          issuer_cik=_issuer_cik(ticker))
    except Exception:  # noqa: BLE001
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _load_gaap(ticker: str):
    """Juengstes Earnings-8-K Exhibit -> (meta, analyse, textlaenge)."""
    ex = find_earnings_exhibits(ticker, n=1)
    if not ex or not ex[0].get("exhibit_url"):
        return None, None
    text = fetch_exhibit_text(ex[0]["exhibit_url"])
    return ex[0], analyze_non_gaap(text)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_year_metrics(ticker: str, n_years: int):
    """Letzte N 10-K: komplette Jahres-Metriken (GuV+Bilanz+CF) je Jahr."""
    annuals = find_filings(ticker, n=n_years, forms=("10-K",))
    rows = []
    for f in annuals:
        d = fetch_year_metrics_from_filing(f)
        if d is not None:
            rows.append(d)
    rows.sort(key=lambda d: d.get("period_end") or "")
    return rows


@st.cache_data(ttl=3600, show_spinner=False)
def _load_earnings(ticker: str, n_years: int):
    """Letzte N 10-K: Gewinnruecklagen + EPS basic/diluted je Jahr."""
    annuals = find_filings(ticker, n=n_years, forms=("10-K",))
    rows = []
    for f in annuals:
        d = fetch_earnings_history_from_filing(f)
        if d is not None:
            rows.append(d)
    rows.sort(key=lambda d: d.get("period_end") or "")
    return rows


@st.cache_data(ttl=86400, show_spinner=False)
def _load_prices(ticker: str, start_iso: str, end_iso: str) -> dict:
    """Tages-Schlusskurse via yfinance -> {iso_date: close}. {} bei Fehler."""
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(start=start_iso, end=end_iso,
                                      auto_adjust=False)
        if h is None or h.empty:
            return {}
        return {str(idx.date()): float(row["Close"])
                for idx, row in h.iterrows()}
    except Exception:  # noqa: BLE001 — Marktdaten optional
        return {}


def _nearest_close(prices: dict, target_iso: str):
    """Schlusskurs am/letzten Handelstag <= target_iso (sonst frühester)."""
    if not prices:
        return None
    on_before = [d for d in prices if d <= target_iso]
    key = max(on_before) if on_before else min(prices)
    return prices.get(key)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_sbc(ticker: str, n_years: int):
    """Letzte N 10-K: SBC + Kontext je Jahr (dicts)."""
    annuals = find_filings(ticker, n=n_years, forms=("10-K",))
    rows = []
    for f in annuals:
        d = fetch_sbc_from_filing(f)
        if d is not None:
            rows.append(d)
    rows.sort(key=lambda d: d.get("period_end") or "")
    return rows


# ---------- Kennzahlen ----------

def _returns(inc, bs) -> dict:
    """ROE/ROA/ROCE/ROIC fuer ein Geschaeftsjahr (Stichtagswerte)."""
    roe = _div(inc.net_income, bs.equity)
    roa = _div(inc.net_income, bs.total_assets)

    cap_emp = None
    if bs.total_assets is not None and bs.liabilities_current is not None:
        cap_emp = bs.total_assets - bs.liabilities_current
    roce = _div(inc.operating_income, cap_emp)

    eff = _div(inc.tax_expense, inc.pretax_income)
    if eff is None or not (0.0 <= eff <= 0.6):
        eff = 0.21                      # US-Default, wenn unplausibel
    nopat = (inc.operating_income * (1 - eff)
             if inc.operating_income is not None else None)
    inv_cap = None
    if bs.total_debt is not None and bs.equity is not None:
        inv_cap = bs.total_debt + bs.equity - (bs.cash_and_sti or 0.0)
        if inv_cap <= 0:
            inv_cap = None
    roic = _div(nopat, inv_cap)
    return {"roe": roe, "roa": roa, "roce": roce, "roic": roic,
            "nopat": nopat, "inv_cap": inv_cap, "eff_tax": eff}


# ---------- Kriterien je Thema (Single Source fuer Verdict + Score) ----

def _checks_balance(latest) -> list:
    t = SCORE_CFG["thresholds"]["balance_sheet"]
    checks = []
    cr = _div(latest.assets_current, latest.liabilities_current)
    if cr is not None:
        checks.append((f"Current Ratio > {de_dec(t['current_ratio_min'], 1)}",
                       cr > t["current_ratio_min"]))
    if latest.net_debt is not None:
        checks.append(("Netto-Cash (Net Debt < 0)", latest.net_debt < 0))
    de = _div(latest.total_debt, latest.equity)
    if de is not None:
        checks.append((f"Debt/Equity < {de_dec(t['debt_to_equity_max'], 1)}",
                       de < t["debt_to_equity_max"]))
    eqr = _div(latest.equity, latest.total_assets)
    if eqr is not None:
        checks.append((f"Eigenkapitalquote > {_pct(t['equity_ratio_min'], 0)}",
                       eqr > t["equity_ratio_min"]))
    return checks


def _checks_returns(rows) -> list:
    t = SCORE_CFG["thresholds"]["return_on_capital"]
    inc_l, bs_l = rows[-1]
    rl = _returns(inc_l, bs_l)
    checks = []
    if rl["roic"] is not None:
        checks.append((f"ROIC > {_pct(t['roic_min'], 0)}",
                       rl["roic"] > t["roic_min"]))
    if rl["roe"] is not None:
        checks.append((f"ROE > {_pct(t['roe_min'], 0)}",
                       rl["roe"] > t["roe_min"]))
    if rl["roa"] is not None:
        checks.append((f"ROA > {_pct(t['roa_min'], 0)}",
                       rl["roa"] > t["roa_min"]))
    all_roic = [_returns(i, b)["roic"] for i, b in rows]
    all_roic = [x for x in all_roic if x is not None]
    if len(all_roic) >= 2:
        checks.append(("ROIC durchgehend positiv",
                       all(x > 0 for x in all_roic)))
    return checks


def _insider_aggregate(tx, n_years) -> dict:
    cutoff = (date.today() - timedelta(days=int(n_years) * 365)).isoformat()
    df = pd.DataFrame(tx)
    if not df.empty:
        df = df[df["transaction_date"] >= cutoff]
    buys = df[df["code"] == "P"] if not df.empty else df
    sells = df[df["code"] == "S"] if not df.empty else df
    return {
        "df": df,
        "buy_val": float(buys["value"].fillna(0).sum()) if not df.empty else 0.0,
        "sell_val": float(sells["value"].fillna(0).sum()) if not df.empty else 0.0,
        "n_buyers": buys["owner"].nunique() if not df.empty else 0,
        "n_sellers": sells["owner"].nunique() if not df.empty else 0,
        "buys": buys, "sells": sells,
    }


def _checks_insider(agg) -> list:
    cm = SCORE_CFG["thresholds"]["insider"]["cluster_buyers_min"]
    net = agg["buy_val"] - agg["sell_val"]
    return [
        ("Netto-Insiderkaeufe (Wert)", net > 0),
        ("Mehr Kaeufer als Verkaeufer", agg["n_buyers"] > agg["n_sellers"]),
        (f"Cluster-Kauf (>= {cm} Kaeufer)", agg["n_buyers"] >= cm),
    ]


def _sbc_metrics(rows) -> dict:
    last = rows[-1]
    sbc = last.get("sbc")
    sh = [(d["period_end"], d["diluted_shares"]) for d in rows
          if d.get("diluted_shares")]
    dil_cagr = None
    if len(sh) >= 2 and sh[0][1] and sh[0][1] > 0:
        try:
            yrs = (pd.to_datetime(sh[-1][0]) - pd.to_datetime(
                sh[0][0])).days / 365.25
            if yrs >= 1:
                dil_cagr = (sh[-1][1] / sh[0][1]) ** (1 / yrs) - 1
        except Exception:  # noqa: BLE001
            dil_cagr = None
    return {
        "last": last, "sbc": sbc,
        "sbc_rev": _div(sbc, last.get("revenue")),
        "sbc_cfo": _div(sbc, last.get("cfo")),
        "sbc_ni": _div(sbc, last.get("net_income")),
        "dil_cagr": dil_cagr,
    }


def _checks_sbc(metrics) -> list:
    t = SCORE_CFG["thresholds"]["stock_based_comp"]
    checks = []
    if metrics["sbc_rev"] is not None:
        checks.append((f"SBC < {_pct(t['sbc_to_revenue_max'], 0)} vom Umsatz",
                       metrics["sbc_rev"] < t["sbc_to_revenue_max"]))
    if metrics["sbc_cfo"] is not None:
        checks.append(
            (f"SBC < {_pct(t['sbc_to_cfo_max'], 0)} vom operativen Cashflow",
             metrics["sbc_cfo"] < t["sbc_to_cfo_max"]))
    if metrics["dil_cagr"] is not None:
        checks.append(
            (f"Aktienzahl ≤ +{_pct(t['dilution_cagr_max'], 0)} p.a. "
             "(kaum Verwaesserung)",
             metrics["dil_cagr"] <= t["dilution_cagr_max"]))
    return checks


def _checks_gaap(ana) -> list:
    t = SCORE_CFG["thresholds"]["gaap_vs_non_gaap"]
    return [
        (f"Non-GAAP-Nutzung moderat (< {t['mentions_max']} Erwaehnungen)",
         ana["mentions"] < t["mentions_max"]),
        ("SBC NICHT herausgerechnet", not ana["adds_back_sbc"]),
        (f"≤ {t['categories_max']} Anpassungskategorien",
         len(ana["categories"]) <= t["categories_max"]),
    ]


# =====================================================================
# Render-Funktionen je Thema
# =====================================================================

def render_balance(ticker, n_years):
    try:
        with st.spinner(f"Lade SEC-Filings fuer {ticker} …"):
            latest, hist = _load_balance(ticker, n_years)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if latest is None:
        st.warning(f"Kein verwertbares 10-K/10-Q mit Bilanz fuer "
                   f"**{ticker}** gefunden.")
        st.stop()

    cur = latest.currency or "USD"
    st.markdown(
        f"### {ticker} — Bilanzstaerke  \n"
        f"Stichtag **{str(latest.period_end)[:10]}** "
        f"({latest.form_type}) · eingereicht {str(latest.filed_at)[:10]}")

    cr = _div(latest.assets_current, latest.liabilities_current)
    de = _div(latest.total_debt, latest.equity)
    eqr = _div(latest.equity, latest.total_assets)
    _verdict_box(_checks_balance(latest), lead="Bilanz")

    inv = latest.inventory or 0.0
    quick = _div((latest.assets_current or 0.0) - inv,
                 latest.liabilities_current)
    intang = (latest.goodwill or 0.0) + (latest.intangibles or 0.0)
    m = st.columns(3)
    m[0].metric("Current Ratio", _ratio(cr),
                help="Umlaufvermoegen / kurzfr. Verbindlichkeiten")
    m[1].metric("Quick Ratio", _ratio(quick),
                help="(Umlaufvermoegen − Vorraete) / kurzfr. Verbindl.")
    m[2].metric("Debt / Equity", _ratio(de),
                help="Gesamtverschuldung / Eigenkapital")
    m2 = st.columns(3)
    nd = latest.net_debt
    m2[0].metric("Net Debt" if (nd or 0) >= 0 else "Net Cash",
                 _money(abs(nd) if nd is not None else None, cur),
                 help="Gesamtverschuldung − (Cash + kurzfr. Anlagen)")
    m2[1].metric("Eigenkapitalquote", _pct(eqr),
                 help="Eigenkapital / Bilanzsumme")
    m2[2].metric("Goodwill + Intangibles",
                 _pct(_div(intang, latest.total_assets)),
                 help="Anteil immaterieller Posten an der Bilanzsumme")

    if len(hist) >= 2:
        rows = [{
            "period_end": pd.to_datetime(bs.period_end),
            "current_ratio": _div(bs.assets_current, bs.liabilities_current),
            "debt_to_equity": _div(bs.total_debt, bs.equity),
            "net_debt": bs.net_debt,
        } for bs in sorted(hist, key=lambda b: b.period_end or "")]
        df = pd.DataFrame(rows)
        st.markdown("#### Trend (10-K, jaehrlich)")
        t1, t2 = st.columns(2)
        f1 = go.Figure()
        f1.add_trace(go.Scatter(x=df["period_end"], y=df["current_ratio"],
                                mode="lines+markers", name="Current Ratio",
                                line=dict(color="#0F6E56", width=2)))
        f1.add_trace(go.Scatter(x=df["period_end"], y=df["debt_to_equity"],
                                mode="lines+markers", name="Debt/Equity",
                                line=dict(color="#A32D2D", width=2)))
        f1.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title="Current Ratio & Debt/Equity",
                         legend=dict(orientation="h", y=-0.2),
                         hovermode="x unified")
        t1.plotly_chart(f1, use_container_width=True)
        nd_col = ["#1D9E75" if (v is not None and v < 0) else "#A32D2D"
                  for v in df["net_debt"]]
        f2 = go.Figure(go.Bar(
            x=df["period_end"], y=df["net_debt"], marker_color=nd_col,
            hovertemplate="%{x|%Y-%m-%d}<br>Net Debt: "
                          "%{y:,.0f}<extra></extra>"))
        f2.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title=f"Net Debt ({cur}) — gruen = Netto-Cash",
                         yaxis_title=cur)
        t2.plotly_chart(f2, use_container_width=True)

    with st.expander("Bilanz-Rohwerte (zuletzt)"):
        raw = {
            "Cash & Equivalents": latest.cash,
            "Kurzfristige Anlagen": latest.short_term_invest,
            "Umlaufvermoegen": latest.assets_current,
            "Kurzfr. Verbindlichkeiten": latest.liabilities_current,
            "Bilanzsumme": latest.total_assets,
            "Gesamtverbindlichkeiten": latest.total_liabilities,
            "Eigenkapital": latest.equity,
            "Langfr. Schulden": latest.long_term_debt,
            "Kurzfr. Schulden": latest.current_debt,
            "Vorraete": latest.inventory,
            "Goodwill": latest.goodwill,
            "Immaterielle (o. Goodwill)": latest.intangibles,
        }
        st.dataframe(
            pd.DataFrame([{"Posten": k, "Wert": _money(v, cur)}
                          for k, v in raw.items()]),
            use_container_width=True, hide_index=True)


def render_returns(ticker, n_years):
    try:
        with st.spinner(f"Lade {n_years} Jahresberichte fuer {ticker} …"):
            rows = _load_returns(ticker, n_years)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if not rows:
        st.warning(f"Keine verwertbaren 10-K mit GuV + Bilanz fuer "
                   f"**{ticker}** gefunden.")
        st.stop()

    inc_l, bs_l = rows[-1]
    cur = inc_l.currency or bs_l.currency or "USD"
    rl = _returns(inc_l, bs_l)
    st.markdown(
        f"### {ticker} — Return on Capital  \n"
        f"Letztes GJ **{str(inc_l.period_end)[:10]}** "
        f"({inc_l.form_type}) · {len(rows)} Jahre geladen")

    _verdict_box(_checks_returns(rows), strong="hochwertig",
                 mixed="durchschnittlich", weak="kapitalineffizient",
                 lead="Kapitalrendite")

    # Trendampel statt Momentaufnahme: aktueller Wert + Steigung pp/Jahr
    _allret = [_returns(i, b) for i, b in rows]
    _specs = [
        ("roic", "ROIC", "NOPAT / (Schulden + EK − Cash). "
         "NOPAT = operatives Ergebnis × (1 − eff. Steuersatz)"),
        ("roce", "ROCE", "Operatives Ergebnis / (Bilanzsumme − kurzfr. "
         "Verbindl.)"),
        ("roe", "ROE", "Nettogewinn / Eigenkapital"),
        ("roa", "ROA", "Nettogewinn / Bilanzsumme"),
    ]
    m = st.columns(4)
    for col, (key, label, helptext) in zip(m, _specs):
        cur, slope, emoji, dc = _trend_ampel([r[key] for r in _allret])
        col.metric(
            f"{emoji} {label}",
            _pct(cur) if cur is not None else "—",
            delta=(f"{slope:+.1f} pp/J" if slope is not None else None),
            delta_color=dc, help=helptext)
    st.caption("Trendampel: aktueller Wert + lineare Steigung in %-Punkten "
               "pro Jahr ueber den geladenen Zeitraum. 🟢 steigend · "
               "🟡 stabil (±0,5 pp/J) · 🔴 fallend · ⚪ zu wenig Historie.")

    if len(rows) >= 2:
        trend = [{
            "period_end": pd.to_datetime(i.period_end),
            **{k: _returns(i, b)[k] for k in ("roic", "roce", "roe", "roa")},
        } for i, b in rows]
        df = pd.DataFrame(trend)
        st.markdown("#### Trend (10-K, jaehrlich)")
        fig = go.Figure()
        for name, col, color in [
            ("ROIC", "roic", "#0F6E56"), ("ROCE", "roce", "#1D9E75"),
            ("ROE", "roe", "#5DCAA5"), ("ROA", "roa", "#B4862B"),
        ]:
            fig.add_trace(go.Scatter(
                x=df["period_end"], y=df[col] * 100.0,
                mode="lines+markers", name=name,
                line=dict(color=color, width=2), connectgaps=False,
                hovertemplate=(f"%{{x|%Y-%m-%d}}<br>{name}: "
                               "%{y:.1f}%<extra></extra>")))
        fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title="%", legend=dict(orientation="h",
                                                       y=-0.2),
                          hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Stichtags-Bilanzwerte (kein Mehrjahres-Durchschnitt); "
                   "ROIC mit effektivem Steuersatz, Fallback 21 % wenn "
                   "Vorsteuerergebnis fehlt/negativ.")

    with st.expander("Komponenten (letztes GJ)"):
        st.dataframe(pd.DataFrame([
            {"Posten": "NOPAT", "Wert": _money(rl["nopat"], cur)},
            {"Posten": "Investiertes Kapital", "Wert": _money(rl["inv_cap"],
                                                              cur)},
            {"Posten": "Eff. Steuersatz", "Wert": _pct(rl["eff_tax"])},
            {"Posten": "Operatives Ergebnis",
             "Wert": _money(inc_l.operating_income, cur)},
            {"Posten": "Nettogewinn", "Wert": _money(inc_l.net_income, cur)},
            {"Posten": "Eigenkapital", "Wert": _money(bs_l.equity, cur)},
            {"Posten": "Bilanzsumme", "Wert": _money(bs_l.total_assets, cur)},
        ]), use_container_width=True, hide_index=True)

    # ---- Owner Earnings + FCF-Verwendung (gemeinsamer Cashflow-Load) -----
    try:
        with st.spinner("Lade Cashflow-Details …"):
            cap = _load_year_metrics(ticker, n_years)
    except SecApiError as e:
        st.warning(f"Cashflow-Details nicht ladbar: {e}")
        cap = []
    except Exception:  # noqa: BLE001
        cap = []

    if not cap:
        st.info("Keine Cashflow-Daten gefunden.")
        return

    def _abs(v):
        return abs(v) if v is not None else None

    # ---- Owner Earnings (Buffett): NI + D&A − Maintenance CapEx ----------
    st.markdown("#### Owner Earnings (Buffett)")
    # Greenwald-Kapitalintensitaet: bevorzugt PP&E/Umsatz (streng), sonst
    # CapEx/Umsatz als Fallback. Wachstums-CapEx = Intensitaet × ΔUmsatz.
    _ppe_ratios = [(d["ppe_gross"] / d["revenue"]) for d in cap
                   if d.get("ppe_gross") is not None and d.get("revenue")]
    _capex_ratios = [(_abs(d.get("capex")) / d["revenue"]) for d in cap
                     if d.get("capex") is not None and d.get("revenue")]
    if _ppe_ratios:
        _intensity = sum(_ppe_ratios) / len(_ppe_ratios)
        _method = "PP&E/Umsatz"
        _ppe_net_used = any(d.get("ppe_is_net") for d in cap
                            if d.get("ppe_gross") is not None)
    elif _capex_ratios:
        _intensity = sum(_capex_ratios) / len(_capex_ratios)
        _method = "CapEx/Umsatz (Fallback)"
        _ppe_net_used = False
    else:
        _intensity = None
        _method = "—"
        _ppe_net_used = False

    oe_series = []
    _prev_rev = None
    for d in cap:
        ni, da = d.get("net_income"), d.get("dep_amort")
        cx, rev = _abs(d.get("capex")), d.get("revenue")
        maint = None
        if cx is not None:
            if _intensity is not None and _prev_rev is not None \
                    and rev is not None:
                growth = _intensity * max(0.0, rev - _prev_rev)
                maint = min(cx, max(0.0, cx - growth))
            else:
                maint = cx          # erstes Jahr: konservativ volle CapEx
        oe = (ni + (da or 0.0) - maint
              if (ni is not None and maint is not None) else None)
        oe_series.append({"period_end": pd.to_datetime(d["period_end"]),
                          "oe": oe, "ni": ni, "fcf": d.get("fcf"),
                          "maint": maint})
        _prev_rev = rev

    oel = oe_series[-1]
    oe_margin = _div(oel["oe"], cap[-1].get("revenue"))
    om = st.columns(4)
    om[0].metric("Owner Earnings", _money(oel["oe"], cur),
                 help="Nettogewinn + Abschreibungen (D&A) − Maintenance CapEx")
    om[1].metric("OE-Marge", _pct(oe_margin) if oe_margin is not None
                 else "—", help="Owner Earnings / Umsatz")
    om[2].metric("Abschreibungen (D&A)",
                 _money(cap[-1].get("dep_amort"), cur))
    om[3].metric("Maintenance CapEx (gesch.)", _money(oel["maint"], cur),
                 help="Greenwald-Schaetzung: Gesamt-CapEx − Wachstums-CapEx "
                      "(Ø CapEx/Umsatz × Umsatzzuwachs)")

    if len(oe_series) >= 2:
        odf = pd.DataFrame(oe_series)
        fig = go.Figure()
        fig.add_trace(go.Bar(name="Owner Earnings", x=odf["period_end"],
                             y=odf["oe"], marker_color="#0F6E56"))
        fig.add_trace(go.Scatter(name="Nettogewinn", x=odf["period_end"],
                                 y=odf["ni"], mode="lines+markers",
                                 line=dict(color="#A32D2D", width=2)))
        fig.add_trace(go.Scatter(name="Free Cash Flow", x=odf["period_end"],
                                 y=odf["fcf"], mode="lines+markers",
                                 line=dict(color="#444441", width=2,
                                           dash="dot")))
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title=cur, legend=dict(orientation="h",
                                                       y=-0.2),
                          hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
    _note_net = (" PP&E nur netto verfuegbar — Kapitalintensitaet leicht "
                 "unterschaetzt." if _ppe_net_used else "")
    st.caption(
        "Owner Earnings nach Buffett = Nettogewinn + Abschreibungen (D&A) "
        "− Maintenance CapEx. Maintenance CapEx wird nicht berichtet und "
        f"per Greenwald-Methode geschaetzt: Kapitalintensitaet ({_method}) "
        "× Umsatzzuwachs = Wachstums-CapEx; Maintenance = Gesamt-CapEx − "
        "Wachstums-CapEx. Erstes Jahr konservativ volle CapEx. Naeherung."
        + _note_net)

    # ---- FCF-Verwendung (Kapitalallokation) -----------------------------
    st.markdown("#### FCF-Verwendung (Kapitalallokation)")
    cl = cap[-1]
    bb, dv = _abs(cl.get("buybacks")), _abs(cl.get("dividends"))
    cx, aq = _abs(cl.get("capex")), _abs(cl.get("acquisitions"))
    fcf_l = cl.get("fcf")
    shareholder = (bb or 0) + (dv or 0)
    payout = _div(shareholder, fcf_l)

    mm = st.columns(4)
    mm[0].metric("Rueckkaeufe", _money(bb, cur))
    mm[1].metric("Dividenden", _money(dv, cur))
    mm[2].metric("Reinvestition (CapEx)", _money(cx, cur))
    mm[3].metric("Akquisitionen", _money(aq, cur))
    st.metric("Ausschuettungsquote (Rueckkauf + Dividende) / FCF",
              _pct(payout) if payout is not None else "—",
              help="Anteil des Free Cash Flow, der an Aktionaere zurueck "
                   "floss (letztes GJ)")

    if len(cap) >= 2:
        af = pd.DataFrame([{
            "period_end": pd.to_datetime(d["period_end"]),
            "Rueckkaeufe": _abs(d.get("buybacks")),
            "Dividenden": _abs(d.get("dividends")),
            "Reinvestition": _abs(d.get("capex")),
            "Akquisitionen": _abs(d.get("acquisitions")),
        } for d in cap])
        fig = go.Figure()
        for name, color in [("Rueckkaeufe", "#0F6E56"),
                            ("Dividenden", "#5DCAA5"),
                            ("Reinvestition", "#1D9E75"),
                            ("Akquisitionen", "#B4862B")]:
            fig.add_trace(go.Bar(
                name=name, x=af["period_end"], y=af[name],
                marker_color=color,
                hovertemplate=(f"%{{x|%Y-%m-%d}}<br>{name}: "
                               "%{y:,.0f}<extra></extra>")))
        fig.add_trace(go.Scatter(
            name="Free Cash Flow", x=af["period_end"],
            y=[d.get("fcf") for d in cap], mode="lines+markers",
            line=dict(color="#444441", width=2, dash="dot")))
        fig.update_layout(barmode="stack", height=340,
                          margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title=cur, legend=dict(orientation="h",
                                                       y=-0.2),
                          hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

    st.caption("Mittelverwendung aus dem Cashflow-Statement (Rueckkaeufe, "
               "Dividenden, CapEx, Akquisitionen) als positive Betraege; "
               "FCF-Linie zum Vergleich. Zeigt, wie das Management den "
               "freien Cashflow allokiert.")


def render_insider(ticker, n_years):
    try:
        with st.spinner(f"Lade Insider-Filings fuer {ticker} …"):
            tx = _load_insider(ticker)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if not tx:
        st.warning(f"Keine Insider-Filings (Form 3/4/5) fuer **{ticker}** "
                   "gefunden.")
        st.stop()

    agg = _insider_aggregate(tx, n_years)
    df = agg["df"]
    st.markdown(
        f"### {ticker} — Insider Buy / Sell  \n"
        f"Lookback **{int(n_years)} J** · {len(df)} Transaktionen "
        f"(Markt-Trades P/S davon hervorgehoben)")
    if df.empty:
        st.info("Keine Transaktionen im gewaehlten Zeitraum.")
        st.stop()

    buys, sells = agg["buys"], agg["sells"]
    buy_val, sell_val = agg["buy_val"], agg["sell_val"]
    net_val = buy_val - sell_val
    n_buyers, n_sellers = agg["n_buyers"], agg["n_sellers"]

    # ---- Insider Conviction Score ---------------------------------------
    ic = SCORE_CFG["insider_conviction"]
    w, cm = ic["weights"], ic["cluster_buyers_min"]
    pct, sigcfg = ic["meaningful_sell_pct"], ic["signal"]

    def _col(d, c):
        return d[c] if c in d.columns else False

    ceo_buys = buys[_col(buys, "is_ceo") == True] if not buys.empty else buys
    cfo_buys = buys[_col(buys, "is_cfo") == True] if not buys.empty else buys
    cluster = n_buyers >= cm

    # Erstkauf: Kauf, bei dem der Vorbestand ~0 war (shares_following−shares)
    first_buyers = 0
    if not buys.empty and "shares_following" in buys.columns:
        fb = buys[buys["shares_following"].notna()].copy()
        if not fb.empty:
            prior = fb["shares_following"] - fb["shares"].fillna(0)
            fb = fb[prior <= 0.05 * fb["shares_following"].clip(lower=1)]
            first_buyers = fb["owner"].nunique()

    # Bedeutender Verkauf: kein 10b5-1-Routineplan UND grosser Anteil
    routine_sells = meaningful_sells = 0
    if not sells.empty:
        planned = (sells["planned"].fillna(False)
                   if "planned" in sells.columns else False)
        routine_sells = int(planned.sum()) if hasattr(planned, "sum") else 0
        se = sells.copy()
        if "shares_following" in se.columns:
            pre = se["shares"].fillna(0) + se["shares_following"].fillna(0)
            frac = se["shares"] / pre.where(pre > 0)
        else:
            frac = pd.Series([None] * len(se), index=se.index)
        not_planned = ~(se["planned"].fillna(False)
                        if "planned" in se.columns else False)
        big = frac.isna() | (frac >= pct)
        meaningful_sells = int((not_planned & big).sum())

    pts = 0
    fired = []
    if len(ceo_buys) > 0:
        pts += w["ceo_buy"]; fired.append(("CEO-Kauf", w["ceo_buy"]))
    if len(cfo_buys) > 0:
        pts += w["cfo_buy"]; fired.append(("CFO-Kauf", w["cfo_buy"]))
    if cluster:
        pts += w["cluster_buy"]
        fired.append((f"Cluster-Kauf ({n_buyers} Kaeufer)", w["cluster_buy"]))
    if first_buyers > 0:
        pts += w["first_buy"]
        fired.append((f"Erstkauf ({first_buyers})", w["first_buy"]))
    if meaningful_sells > 0:
        pts -= w["meaningful_sell"]
        fired.append((f"Bedeutender Verkauf ({meaningful_sells})",
                      -w["meaningful_sell"]))
    conviction = max(0, sum(p for _, p in fired if p > 0))

    if pts >= sigcfg["bullish_min"]:
        sig_label, sig_fn = "Bullisch", st.success
    elif pts <= sigcfg["bearish_max"]:
        sig_label, sig_fn = "Bearisch", st.warning
    else:
        sig_label, sig_fn = "Neutral", st.info
    _lines = "  \n".join(
        f"{'➕' if p > 0 else '➖'} {name}: {p:+d}" for name, p in fired) \
        or "keine ausgepraegten Signale"
    sig_fn(f"**Insider-Signal: {sig_label}**  \nConviction {conviction} · "
           f"Netto-Punkte {pts:+d}  \n{_lines}")
    if routine_sells:
        st.caption(f"{routine_sells} Routine-Verkauf/e (10b5-1-Plan) "
                   "ausgeklammert — zaehlen nicht als bearish.")

    m = st.columns(4)
    m[0].metric("CEO / CFO-Kauf",
                f"{'✓' if len(ceo_buys) else '–'} / "
                f"{'✓' if len(cfo_buys) else '–'}",
                help="Markt-Kauf (Code P) durch CEO bzw. CFO im Zeitraum")
    m[1].metric("Cluster-Kauf",
                f"{n_buyers} Kaeufer" + (" ✓" if cluster else ""),
                help=f"≥ {cm} verschiedene Kaeufer = Cluster")
    m[2].metric("Erstkaeufe", str(first_buyers),
                help="Kauf mit ~0 Vorbestand (Initial-Position)")
    m[3].metric("Bedeutende Verkaeufe", str(meaningful_sells),
                help="Kein 10b5-1-Plan und grosser Anteil der Holdings")

    # Monatlicher Netto-Wert (nur P/S)
    ps = df[df["code"].isin(["P", "S"])].copy()
    if not ps.empty:
        ps["month"] = pd.to_datetime(ps["transaction_date"]).dt.to_period(
            "M").dt.to_timestamp()
        ps["signed"] = ps.apply(
            lambda r: (r["value"] or 0) * (1 if r["code"] == "P" else -1),
            axis=1)
        monthly = ps.groupby("month")["signed"].sum().reset_index()
        st.markdown("#### Netto Insider-Flow je Monat (P − S)")
        colors = ["#1D9E75" if v >= 0 else "#A32D2D"
                  for v in monthly["signed"]]
        fig = go.Figure(go.Bar(
            x=monthly["month"], y=monthly["signed"], marker_color=colors,
            hovertemplate="%{x|%Y-%m}<br>Netto: %{y:,.0f}<extra></extra>"))
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title="USD", bargap=0.2)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Letzte Markt-Transaktionen (P/S)")
    show = (df[df["code"].isin(["P", "S"])]
            .sort_values("transaction_date", ascending=False).head(25))
    if show.empty:
        st.caption("Keine offenen Markt-Trades (P/S) im Zeitraum — nur "
                   "Awards/Ausuebungen/Steuereinbehalte.")
    else:
        tbl = pd.DataFrame({
            "Datum": show["transaction_date"],
            "Person": show["owner"],
            "Funktion": show["relationship"],
            "Art": show["code"].map(INSIDER_CODE_LABELS).fillna(show["code"]),
            "Stueck": show["shares"].map(
                lambda v: de_int(v) if not _missing(v) else "—"),
            "Preis": show["price"].map(
                lambda v: _money(v) if not _missing(v) else "—"),
            "Wert": show["value"].map(lambda v: _money(v)),
        })
        st.dataframe(tbl, use_container_width=True, hide_index=True)

    st.caption(
        "Conviction Score gewichtet aussagekraeftige Signale (CEO-/CFO-"
        "Kauf, Cluster-Kauf, Erstkauf) und zieht nur *bedeutende* Verkaeufe "
        "ab. Routine-Verkaeufe ueber 10b5-1-Handelsplaene werden erkannt "
        "und ausgeklammert — so wird ein regelmaessig automatisch "
        "verkaufender CEO nicht faelschlich als bearish gewertet. Holding-"
        "Dauer ist nicht im Filing; 'bedeutender Verkauf' wird ueber "
        "10b5-1-Status + verkauften Anteil der Holdings approximiert. "
        "Gewichte/Schwellen in config/ad_hoc_score.yaml.")


def render_sbc(ticker, n_years):
    try:
        with st.spinner(f"Lade {n_years} Jahresberichte fuer {ticker} …"):
            rows = _load_sbc(ticker, n_years)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if not rows:
        st.warning(f"Keine 10-K mit SBC-/Cashflow-Daten fuer **{ticker}** "
                   "gefunden.")
        st.stop()

    last = rows[-1]
    cur = "USD"
    st.markdown(
        f"### {ticker} — Stock-based Compensation  \n"
        f"Letztes GJ **{str(last['period_end'])[:10]}** "
        f"({last['form_type']}) · {len(rows)} Jahre geladen")

    if last.get("sbc") is None:
        st.info("Kein SBC-Tag (ShareBasedCompensation) im juengsten "
                "Cashflow-Statement gefunden. Manche Firmen weisen es nur "
                "im Anhang aus.")

    _met = _sbc_metrics(rows)
    sbc = _met["sbc"]
    sbc_rev, sbc_cfo, dil_cagr = (_met["sbc_rev"], _met["sbc_cfo"],
                                  _met["dil_cagr"])
    _verdict_box(_checks_sbc(_met),
                 strong="gering verwaessernd (hohe Qualitaet)",
                 mixed="moderat", weak="stark verwaessernd",
                 lead="SBC-Belastung")

    m = st.columns(4)
    m[0].metric("SBC (letztes GJ)", _money(sbc, cur))
    m[1].metric("SBC / Umsatz", _pct(sbc_rev))
    m[2].metric("SBC / operativer CF", _pct(sbc_cfo))
    m[3].metric("Aktien p.a.", _pct(dil_cagr) if dil_cagr is not None
                else "—", help="CAGR der verwaesserten Aktien "
                                "(+ = Verwaesserung, − = Rueckkauf)")

    if len(rows) >= 2:
        df = pd.DataFrame([{
            "period_end": pd.to_datetime(d["period_end"]),
            "sbc_rev": _div(d.get("sbc"), d.get("revenue")),
            "sbc_cfo": _div(d.get("sbc"), d.get("cfo")),
            "diluted_shares": d.get("diluted_shares"),
        } for d in rows])
        st.markdown("#### Trend (10-K, jaehrlich)")
        t1, t2 = st.columns(2)
        f1 = go.Figure()
        f1.add_trace(go.Scatter(
            x=df["period_end"], y=df["sbc_rev"] * 100.0,
            mode="lines+markers", name="SBC / Umsatz",
            line=dict(color="#A32D2D", width=2), connectgaps=False))
        f1.add_trace(go.Scatter(
            x=df["period_end"], y=df["sbc_cfo"] * 100.0,
            mode="lines+markers", name="SBC / operativer CF",
            line=dict(color="#B4862B", width=2), connectgaps=False))
        f1.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title="SBC-Belastung", yaxis_title="%",
                         legend=dict(orientation="h", y=-0.25),
                         hovermode="x unified")
        t1.plotly_chart(f1, use_container_width=True)

        f2 = go.Figure(go.Scatter(
            x=df["period_end"], y=df["diluted_shares"],
            mode="lines+markers", name="Verwaesserte Aktien",
            line=dict(color="#0F6E56", width=2), connectgaps=False))
        f2.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title="Verwaesserte Aktien (Stk.)",
                         yaxis_title="Aktien")
        t2.plotly_chart(f2, use_container_width=True)

    with st.expander("SBC-Rohwerte je Jahr"):
        st.dataframe(pd.DataFrame([{
            "Jahr": str(d["period_end"])[:10],
            "SBC": _money(d.get("sbc"), cur),
            "Operativer CF": _money(d.get("cfo"), cur),
            "Umsatz": _money(d.get("revenue"), cur),
            "Nettogewinn": _money(d.get("net_income"), cur),
            "Verw. Aktien": (de_int(d["diluted_shares"])
                             if d.get("diluted_shares") else "—"),
        } for d in rows]), use_container_width=True, hide_index=True)

    st.caption("SBC aus dem Cashflow-Statement (ShareBasedCompensation, "
               "nicht-zahlungswirksamer Zuschlag). Verwaesserung als CAGR "
               "der gewichteten verwaesserten Aktien.")


def render_gaap(ticker, n_years):
    try:
        with st.spinner(f"Lade Earnings-8-K-Exhibit fuer {ticker} …"):
            meta, ana = _load_gaap(ticker)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if meta is None or ana is None:
        st.warning(
            f"Kein Earnings-8-K (Item 2.02) mit Exhibit 99 fuer "
            f"**{ticker}** gefunden. Manche Firmen melden Zahlen anders.")
        st.stop()

    st.markdown(
        f"### {ticker} — GAAP vs non-GAAP  \n"
        f"Quelle: Earnings-8-K, eingereicht **{str(meta['filed_at'])[:10]}**")

    cats = ana["categories"]
    _verdict_box(_checks_gaap(ana), strong="konservativ / transparent",
                 mixed="moderat", weak="aggressiv (viele Add-backs)",
                 lead="Reporting")

    m = st.columns(3)
    m[0].metric("Non-GAAP-Erwaehnungen", str(ana["mentions"]))
    m[1].metric("Anpassungs-Kategorien", str(len(cats)))
    m[2].metric("SBC herausgerechnet?",
                "Ja" if ana["adds_back_sbc"] else "Nein",
                help="Add-back von Aktienverguetung ist der klassische "
                     "Aggressivitaets-Marker (echte, wiederkehrende Kosten)")

    if cats:
        st.markdown("#### Gefundene Anpassungs-Kategorien")
        cat_df = (pd.DataFrame(
            [{"Kategorie": k, "Treffer": v} for k, v in cats.items()])
            .sort_values("Treffer", ascending=False))
        fig = go.Figure(go.Bar(
            x=cat_df["Treffer"], y=cat_df["Kategorie"], orientation="h",
            marker_color=["#A32D2D" if k.startswith("Aktienverguetung")
                          else "#1D9E75" for k in cat_df["Kategorie"]]))
        fig.update_layout(height=max(220, 40 * len(cat_df)),
                          margin=dict(l=10, r=10, t=10, b=10),
                          xaxis_title="Erwaehnungen")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Keine bekannten Anpassungs-Kategorien im Text erkannt — "
                "entweder rein GAAP berichtet oder ungewohnte Formulierung.")

    if ana["amounts"]:
        with st.expander("Beträge nahe 'non-GAAP' (heuristisch, ungeprueft)"):
            st.write(", ".join(ana["amounts"]))
            st.caption("Reine Textnaehe-Suche — keine Zuordnung zu GAAP/"
                       "non-GAAP-Zeilen. Nur als grobe Orientierung.")

    if meta.get("link"):
        st.caption(f"Original-Filing: {meta['link']}")
    st.caption("Heuristische Textanalyse des Earnings-Exhibits "
               "(Exhibit 99). Non-GAAP-Kennzahlen sind nicht im XBRL "
               "strukturiert — daher Stichwort-basiert. Add-back von SBC "
               "und vielen wiederkehrenden Posten gilt als Warnsignal fuer "
               "die Ergebnisqualitaet.")


def render_earnings(ticker, n_years):
    try:
        with st.spinner(f"Lade {n_years} Jahresberichte fuer {ticker} …"):
            rows = _load_earnings(ticker, n_years)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if not rows:
        st.warning(f"Keine 10-K mit Gewinnruecklagen/EPS fuer **{ticker}** "
                   "gefunden.")
        st.stop()

    last, first = rows[-1], rows[0]
    cur = "USD"
    st.markdown(
        f"### {ticker} — Gewinnruecklagen & EPS  \n"
        f"Letztes GJ **{str(last['period_end'])[:10]}** "
        f"({last['form_type']}) · {len(rows)} Jahre geladen")

    re_last = last.get("retained_earnings")
    epsb, epsd = last.get("eps_basic"), last.get("eps_diluted")
    re_first = first.get("retained_earnings")
    epsd_first = first.get("eps_diluted")
    eq_last, eq_first = last.get("equity"), first.get("equity")
    fcf_last = last.get("fcf")
    dil_gap = None
    if epsb not in (None, 0) and epsd is not None:
        dil_gap = (epsb - epsd) / abs(epsb)     # Verwaesserung basic->diluted

    # Enterprise Value (Marktdaten via yfinance): Jahresend-Kurs ×
    # verwaesserte Aktien + Net Debt. Graceful, wenn keine Kurse.
    year_ends = [str(d["period_end"])[:10] for d in rows]
    start_iso = (pd.to_datetime(min(year_ends)) - pd.Timedelta(days=10)
                 ).date().isoformat()
    end_iso = date.today().isoformat()
    prices = _load_prices(ticker, start_iso, end_iso)

    def _ev(d, target_iso):
        sh, nd = d.get("diluted_shares"), d.get("net_debt")
        px = _nearest_close(prices, target_iso)
        if px is None or sh in (None, 0):
            return None
        return px * sh + (nd or 0.0)

    ev_by_year = {ye: _ev(d, ye) for d, ye in zip(rows, year_ends)}
    ev_current = _ev(last, end_iso)        # mit aktuellstem Kurs

    checks = []
    if re_last is not None:
        checks.append(("Gewinnruecklagen positiv (kein Defizit)",
                       re_last > 0))
    if re_last is not None and re_first is not None:
        checks.append(("Gewinnruecklagen gestiegen", re_last > re_first))
    if eq_last is not None and eq_first is not None:
        checks.append(("Eigenkapital gestiegen", eq_last > eq_first))
    if fcf_last is not None:
        checks.append(("Free Cash Flow positiv", fcf_last > 0))
    if epsd is not None and epsd_first is not None:
        checks.append(("EPS (verwaessert) gestiegen", epsd > epsd_first))
    if dil_gap is not None:
        checks.append(("Geringe Verwaesserung (diluted ≥ 97 % basic)",
                       dil_gap <= 0.03))
    _verdict_box(checks, strong="wachsend / einbehaltend",
                 mixed="gemischt", weak="schrumpfend / verwaessernd",
                 lead="Gewinnentwicklung")

    m = st.columns(4)
    m[0].metric("Gewinnruecklagen", _money(re_last, cur))
    m[1].metric("EPS (unverwaessert)", _eps(epsb, cur))
    m[2].metric("EPS (verwaessert)", _eps(epsd, cur))
    m[3].metric("Verwaesserung basic→diluted",
                _pct(dil_gap) if dil_gap is not None else "—",
                help="(EPS basic − EPS diluted) / EPS basic")
    m2 = st.columns(3)
    m2[0].metric("Eigenkapital", _money(eq_last, cur),
                 help="Total Shareholders' Equity (Stichtag)")
    m2[1].metric("Free Cash Flow", _money(fcf_last, cur),
                 help="Operativer Cashflow − CapEx (letztes GJ)")
    m2[2].metric("Enterprise Value", _money(ev_current, cur),
                 help="Marktkap. (aktueller Kurs × verw. Aktien) + Net Debt. "
                      "Marktdaten via yfinance.")

    # EV/FCF + Earnings Yield (EBIT/EV) + klassisches Earnings Yield
    evfcf = (ev_current / fcf_last
             if (ev_current and fcf_last and fcf_last > 0) else None)
    ey = _div(last.get("operating_income"), ev_current)
    mcap_current = (ev_current - (last.get("net_debt") or 0.0)
                    if ev_current is not None else None)
    classic_ey = _div(last.get("net_income"), mcap_current)
    m3 = st.columns(3)
    m3[0].metric("EV / FCF",
                 f"{de_dec(evfcf, 1)}x" if evfcf is not None else "—",
                 help="Enterprise Value / Free Cash Flow (aktueller EV, "
                      "letztes GJ FCF). Niedriger = guenstiger.")
    m3[1].metric("Earnings Yield (EBIT/EV)",
                 _pct(ey) if ey is not None else "—",
                 help="Operatives Ergebnis / Enterprise Value (Greenblatt). "
                      "Hoeher = guenstiger.")
    m3[2].metric("Earnings Yield (klassisch)",
                 _pct(classic_ey) if classic_ey is not None else "—",
                 help="Nettogewinn / Marktkapitalisierung (inverses KGV). "
                      "Marktkap. = EV − Net Debt.")

    if len(rows) >= 2:
        df = pd.DataFrame([{
            "period_end": pd.to_datetime(d["period_end"]),
            "retained": d.get("retained_earnings"),
            "eps_basic": d.get("eps_basic"),
            "eps_diluted": d.get("eps_diluted"),
            "equity": d.get("equity"),
            "fcf": d.get("fcf"),
            "ev": ev_by_year.get(str(d["period_end"])[:10]),
            "evfcf": ((ev_by_year.get(str(d["period_end"])[:10]) / d["fcf"])
                      if (ev_by_year.get(str(d["period_end"])[:10])
                          and d.get("fcf") and d["fcf"] > 0) else None),
            "eyield": _div(d.get("operating_income"),
                           ev_by_year.get(str(d["period_end"])[:10])),
            "classic_ey": _div(
                d.get("net_income"),
                (ev_by_year.get(str(d["period_end"])[:10])
                 - (d.get("net_debt") or 0.0))
                if ev_by_year.get(str(d["period_end"])[:10]) is not None
                else None),
        } for d in rows])
        st.markdown("#### Verlauf (10-K, jaehrlich)")
        t1, t2 = st.columns(2)

        re_col = ["#1D9E75" if (v is not None and v >= 0) else "#A32D2D"
                  for v in df["retained"]]
        f1 = go.Figure(go.Bar(
            x=df["period_end"], y=df["retained"], marker_color=re_col,
            hovertemplate="%{x|%Y-%m-%d}<br>Gewinnruecklagen: "
                          "%{y:,.0f}<extra></extra>"))
        f1.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title=f"Gewinnruecklagen ({cur})", yaxis_title=cur)
        t1.plotly_chart(f1, use_container_width=True)

        f2 = go.Figure()
        f2.add_trace(go.Scatter(
            x=df["period_end"], y=df["eps_basic"], mode="lines+markers",
            name="EPS unverwaessert", line=dict(color="#0F6E56", width=2),
            connectgaps=False))
        f2.add_trace(go.Scatter(
            x=df["period_end"], y=df["eps_diluted"], mode="lines+markers",
            name="EPS verwaessert", line=dict(color="#B4862B", width=2),
            connectgaps=False))
        f2.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title=f"EPS ({cur})", yaxis_title=cur,
                         legend=dict(orientation="h", y=-0.25),
                         hovermode="x unified")
        t2.plotly_chart(f2, use_container_width=True)

        t3, t4 = st.columns(2)
        f3 = go.Figure(go.Bar(
            x=df["period_end"], y=df["equity"], marker_color="#1D9E75",
            hovertemplate="%{x|%Y-%m-%d}<br>Eigenkapital: "
                          "%{y:,.0f}<extra></extra>"))
        f3.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title=f"Eigenkapital ({cur})", yaxis_title=cur)
        t3.plotly_chart(f3, use_container_width=True)

        fcf_col = ["#1D9E75" if (v is not None and v >= 0) else "#A32D2D"
                   for v in df["fcf"]]
        f4 = go.Figure(go.Bar(
            x=df["period_end"], y=df["fcf"], marker_color=fcf_col,
            hovertemplate="%{x|%Y-%m-%d}<br>Free Cash Flow: "
                          "%{y:,.0f}<extra></extra>"))
        f4.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title=f"Free Cash Flow ({cur})", yaxis_title=cur)
        t4.plotly_chart(f4, use_container_width=True)

        if df["ev"].notna().any():
            f5 = go.Figure(go.Scatter(
                x=df["period_end"], y=df["ev"], mode="lines+markers",
                name="Enterprise Value", line=dict(color="#444441", width=2),
                connectgaps=False,
                hovertemplate="%{x|%Y-%m-%d}<br>EV: %{y:,.0f}<extra></extra>"))
            f5.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                             title=f"Enterprise Value ({cur}) — "
                                   "Jahresend-Kurs × Aktien + Net Debt",
                             yaxis_title=cur)
            st.plotly_chart(f5, use_container_width=True)
        else:
            st.info("Enterprise-Value-Verlauf nicht verfuegbar — keine "
                    "Kursdaten (yfinance) oder Aktienzahl fehlt.")

        if df["evfcf"].notna().any() or df["eyield"].notna().any():
            t5, t6 = st.columns(2)
            f6 = go.Figure(go.Scatter(
                x=df["period_end"], y=df["evfcf"], mode="lines+markers",
                name="EV/FCF", line=dict(color="#0F6E56", width=2),
                connectgaps=False,
                hovertemplate="%{x|%Y-%m-%d}<br>EV/FCF: "
                              "%{y:.1f}x<extra></extra>"))
            f6.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                             title="EV / FCF (x)", yaxis_title="x")
            t5.plotly_chart(f6, use_container_width=True)

            f7 = go.Figure()
            f7.add_trace(go.Scatter(
                x=df["period_end"], y=df["eyield"] * 100.0,
                mode="lines+markers", name="EBIT/EV",
                line=dict(color="#B4862B", width=2), connectgaps=False,
                hovertemplate="%{x|%Y-%m-%d}<br>EBIT/EV: "
                              "%{y:.1f}%<extra></extra>"))
            f7.add_trace(go.Scatter(
                x=df["period_end"], y=df["classic_ey"] * 100.0,
                mode="lines+markers", name="NI/MCap (klassisch)",
                line=dict(color="#0F6E56", width=2), connectgaps=False,
                hovertemplate="%{x|%Y-%m-%d}<br>NI/MCap: "
                              "%{y:.1f}%<extra></extra>"))
            f7.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                             title="Earnings Yield (%)", yaxis_title="%",
                             legend=dict(orientation="h", y=-0.25),
                             hovermode="x unified")
            t6.plotly_chart(f7, use_container_width=True)

    with st.expander("Rohwerte je Jahr"):
        st.dataframe(pd.DataFrame([{
            "Jahr": str(d["period_end"])[:10],
            "Gewinnruecklagen": _money(d.get("retained_earnings"), cur),
            "Eigenkapital": _money(d.get("equity"), cur),
            "Free Cash Flow": _money(d.get("fcf"), cur),
            "EV (approx.)": _money(ev_by_year.get(str(d["period_end"])[:10]),
                                   cur),
            "EPS unverw.": _eps(d.get("eps_basic"), cur),
            "EPS verw.": _eps(d.get("eps_diluted"), cur),
        } for d in rows]), use_container_width=True, hide_index=True)

    st.caption("Gewinnruecklagen, Eigenkapital als Bilanz-Stichtagswerte; "
               "Free Cash Flow = operativer Cashflow − CapEx; EPS aus der "
               "GuV. Enterprise Value mischt SEC-Fundamentaldaten mit "
               "Marktkursen (yfinance): EV = Jahresend-Kurs × verwaesserte "
               "Aktien + Net Debt — historische Werte sind eine Naeherung. "
               "Diese Kategorie fliesst nicht in den Gesamt-Score.")


# ---------- Moat-Score ----------

def _roic_year(d):
    """ROIC eines Jahres aus dem year-metrics-dict."""
    opinc, eq, td = d.get("operating_income"), d.get("equity"), \
        d.get("total_debt")
    if opinc is None or eq is None or td is None:
        return None
    eff = _div(d.get("tax_expense"), d.get("pretax_income"))
    if eff is None or not (0.0 <= eff <= 0.6):
        eff = 0.21
    inv = td + eq - (d.get("cash_and_sti") or 0.0)
    if inv <= 0:
        return None
    return (opinc * (1 - eff)) / inv


def _cagr(first, last, years):
    if first is None or last is None or first <= 0 or last <= 0 or years < 1:
        return None
    return (last / first) ** (1 / years) - 1


def _moat_signals(rows) -> dict:
    """Sechs Teil-Signale -> {name: (score|None, detail)}."""
    import statistics
    t = SCORE_CFG["moat"]["thresholds"]
    dates = [pd.to_datetime(d["period_end"]) for d in rows]
    span_yrs = ((dates[-1] - dates[0]).days / 365.25) if len(dates) >= 2 \
        else 0.0
    out: dict[str, tuple] = {}

    # 1) Gross-Margin-Trend
    gm = [(d["gross_profit"] / d["revenue"]) for d in rows
          if d.get("gross_profit") is not None and d.get("revenue")]
    if len(gm) >= 2:
        slope_pp = (gm[-1] - gm[0]) * 100
        gt = t["gross_margin"]
        sc = (1.0 if slope_pp >= gt["improve_pp"]
              else 0.5 if slope_pp >= gt["stable_pp"] else 0.0)
        out["gross_margin_trend"] = (
            sc, f"Marge {_pct(gm[-1], 0)}, Δ {slope_pp:+.1f} pp")
    else:
        out["gross_margin_trend"] = (None, "zu wenig Daten")

    # 2) ROIC-Stabilitaet
    roics = [r for r in (_roic_year(d) for d in rows) if r is not None]
    if len(roics) >= 2:
        mean = statistics.fmean(roics)
        cv = (statistics.pstdev(roics) / abs(mean)) if mean else 9.9
        rt = t["roic_stability"]
        if mean >= rt["mean_min"] and cv <= rt["cv_max"]:
            sc = 1.0
        elif mean >= rt["mean_min"] * 0.6 and cv <= rt["cv_max"] * 1.8:
            sc = 0.5
        else:
            sc = 0.0
        out["roic_stability"] = (sc, f"Ø {_pct(mean, 0)}, CV {de_dec(cv, 2)}")
    else:
        out["roic_stability"] = (None, "zu wenig Daten")

    # 3) FCF-Marge (letztes Jahr)
    last = rows[-1]
    fm = _div(last.get("fcf"), last.get("revenue"))
    if fm is not None:
        ft = t["fcf_margin"]
        sc = 1.0 if fm >= ft["high"] else 0.5 if fm >= ft["mid"] else 0.0
        out["fcf_margin"] = (sc, f"{_pct(fm, 1)} vom Umsatz")
    else:
        out["fcf_margin"] = (None, "keine FCF-/Umsatzdaten")

    # 4) R&D-Effizienz: Umsatzzuwachs je F&E-Dollar ueber die Periode
    rd_sum = sum(d["rd_expense"] for d in rows
                 if d.get("rd_expense"))
    rev_first, rev_last = rows[0].get("revenue"), last.get("revenue")
    if rd_sum and rev_first is not None and rev_last is not None:
        eff = (rev_last - rev_first) / rd_sum
        et = t["rnd_efficiency"]
        sc = 1.0 if eff >= et["high"] else 0.5 if eff >= et["mid"] else 0.0
        out["rnd_efficiency"] = (sc, f"{de_dec(eff, 1)}x Umsatz/F&E")
    else:
        out["rnd_efficiency"] = (None, "keine F&E ausgewiesen")

    # 5) Marktanteil-Proxy: Umsatz-CAGR
    rev_cagr = _cagr(rev_first, rev_last, span_yrs)
    if rev_cagr is not None:
        mt = t["market_share_proxy"]
        sc = (1.0 if rev_cagr >= mt["rev_cagr_high"]
              else 0.5 if rev_cagr >= mt["rev_cagr_mid"] else 0.0)
        out["market_share_proxy"] = (
            sc, f"Umsatz-CAGR {_pct(rev_cagr, 1)} (Proxy)")
    else:
        out["market_share_proxy"] = (None, "zu wenig Daten")

    # 6) Aktienrueckkaeufe: Aktien-CAGR (schrumpfend = gut)
    sh = [d.get("diluted_shares") for d in rows if d.get("diluted_shares")]
    sh_cagr = (_cagr(sh[0], sh[-1], span_yrs) if len(sh) >= 2 else None)
    if sh_cagr is not None:
        bt = t["buybacks"]
        sc = (1.0 if sh_cagr <= bt["shrink_cagr"]
              else 0.0 if sh_cagr >= bt["dilute_cagr"] else 0.5)
        out["buybacks"] = (sc, f"Aktien {_pct(sh_cagr, 1)} p.a.")
    else:
        out["buybacks"] = (None, "zu wenig Daten")

    return out


_MOAT_LABELS = {
    "gross_margin_trend": "Gross-Margin-Trend",
    "roic_stability":     "ROIC-Stabilitaet",
    "fcf_margin":         "FCF-Marge",
    "rnd_efficiency":     "R&D-Effizienz",
    "market_share_proxy": "Marktanteil (Proxy)",
    "buybacks":           "Aktienrueckkaeufe",
}


def render_moat(ticker, n_years):
    try:
        with st.spinner(f"Lade {n_years} Jahresberichte fuer {ticker} …"):
            rows = _load_year_metrics(ticker, n_years)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if len(rows) < 2:
        st.warning(f"Mind. 2 Jahresberichte noetig — fuer **{ticker}** nur "
                   f"{len(rows)} gefunden. Mehr Jahre waehlen?")
        st.stop()

    st.markdown(
        f"### {ticker} — Moat-Score  \n"
        f"{len(rows)} Geschaeftsjahre "
        f"({str(rows[0]['period_end'])[:4]}–{str(rows[-1]['period_end'])[:4]})")

    sig = _moat_signals(rows)
    wts = SCORE_CFG["moat"]["weights"]
    bands = SCORE_CFG["moat"]["bands"]

    num = den = 0.0
    for name, (sc, _d) in sig.items():
        if sc is not None:
            num += sc * wts.get(name, 0)
            den += wts.get(name, 0)
    if den == 0:
        st.error("Keine Moat-Signale auswertbar."); st.stop()
    score = round(100 * num / den)
    n_ok = sum(1 for _, (sc, _d) in sig.items() if sc is not None)

    if score >= bands["strong"]:
        st.success(f"## {score}/100 — breiter Moat")
    elif score >= bands["mixed"]:
        st.info(f"## {score}/100 — schmaler Moat")
    else:
        st.warning(f"## {score}/100 — kein klarer Moat")
    st.caption(f"Gewichteter Mittelwert ueber {n_ok}/6 auswertbare Signale "
               f"(fehlende ausgeklammert, Gewichte renormiert).")

    bar = pd.DataFrame([{
        "Signal": _MOAT_LABELS[k],
        "Score": round(100 * sig[k][0]) if sig[k][0] is not None else None,
        "Detail": sig[k][1],
    } for k in wts])
    fig = go.Figure(go.Bar(
        x=bar["Score"], y=bar["Signal"], orientation="h",
        marker_color=["#1D9E75" if (s is not None and s >= 70)
                      else "#B4862B" if (s is not None and s >= 40)
                      else "#A32D2D" if s is not None else "#CFCDC6"
                      for s in bar["Score"]],
        text=[f"{s}" if s is not None else "n/a" for s in bar["Score"]],
        textposition="auto",
        customdata=bar["Detail"],
        hovertemplate="%{y}: %{x}/100<br>%{customdata}<extra></extra>"))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis=dict(range=[0, 100], title="Teil-Score"))
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(pd.DataFrame([{
        "Signal": _MOAT_LABELS[k],
        "Gewicht": f"{int(wts[k] * 100)} %",
        "Score": (f"{round(100 * sig[k][0])}/100"
                  if sig[k][0] is not None else "n/a"),
        "Detail": sig[k][1],
    } for k in wts]), use_container_width=True, hide_index=True)

    st.caption("Moat-Score = gewichteter Mittelwert von sechs Teil-Signalen "
               "aus Mehrjahres-Fundamentaldaten. Marktanteil ist ein Proxy "
               "(Umsatz-CAGR), da echte Marktanteilsdaten nicht in SEC-"
               "Filings stehen. Heuristik, kein Anlageurteil; eigenstaendig, "
               "nicht im Gesamt-Score.")


# ---------- Management ----------

def render_management(ticker, n_years):
    try:
        with st.spinner(f"Lade Insider- & Filing-Daten fuer {ticker} …"):
            tx = _load_insider(ticker)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()
    if not tx:
        st.warning(f"Keine Insider-Filings fuer **{ticker}** — Management-"
                   "Kennzahlen nicht ableitbar.")
        st.stop()

    df = pd.DataFrame(tx)
    df["filed_dt"] = pd.to_datetime(df["filed_at"], errors="coerce", utc=True)
    df["txn_dt"] = pd.to_datetime(df["transaction_date"], errors="coerce")
    today = pd.Timestamp.utcnow()

    st.markdown(f"### {ticker} — Management")

    def _tenure(role_col):
        sub = df[df.get(role_col) == True] if role_col in df.columns else \
            df.iloc[0:0]
        if sub.empty:
            return None, None
        cur_row = sub.sort_values("txn_dt").iloc[-1]   # aktuellste Person
        cur = cur_row["owner"]
        cur_cik = cur_row.get("owner_cik") if "owner_cik" in sub.columns \
            else None
        # Gezielte Abfrage des fruehesten Filings (Fenster reicht oft nicht
        # weit genug zurueck); Fenster-Minimum als Fallback. CIK bevorzugt.
        first = df[df["owner"] == cur]["filed_dt"].min()
        f_iso = _load_first_filing(ticker, cur, cur_cik)
        if f_iso:
            f_dt = pd.to_datetime(f_iso, utc=True, errors="coerce")
            if pd.notna(f_dt) and (pd.isna(first) or f_dt < first):
                first = f_dt
        if pd.isna(first):
            return cur, None
        return cur, (today - first).days / 365.25

    ceo_name, ceo_ten = _tenure("is_ceo")
    cfo_name, cfo_ten = _tenure("is_cfo")

    # Insider Ownership: juengster Bestand je Person (gruppiert ueber CIK,
    # robuster als Name); Management = Officers/Direktoren getrennt von
    # 10%-Eignern. Nenner: ausstehende Aktien (Fallback verwaessert).
    held = df[df["shares_following"].notna()].copy()
    if "owner_cik" in held.columns:
        held["gid"] = held["owner_cik"].fillna(held["owner"])
    else:
        held["gid"] = held["owner"]
    held = held.sort_values("txn_dt")
    # je Person: juengster Bestand + Klassifikation + Name/Funktion
    agg = held.groupby("gid").agg(
        shares=("shares_following", "last"),
        owner=("owner", "last"),
        relationship=("relationship", "last"),
        is_mgmt=("is_officer", "max") if "is_officer" in held.columns
        else ("shares_following", "size"),
        is_dir=("is_director", "max") if "is_director" in held.columns
        else ("shares_following", "size"),
        is_10=("is_tenpct", "max") if "is_tenpct" in held.columns
        else ("shares_following", "size"),
    ) if not held.empty else None

    total_shares = None
    try:
        _ym = _load_year_metrics(ticker, max(1, int(n_years)))
        if _ym:
            total_shares = (_ym[-1].get("shares_outstanding")
                            or _ym[-1].get("diluted_shares"))
    except Exception:  # noqa: BLE001
        total_shares = None

    if agg is not None and not agg.empty:
        mgmt_mask = (agg["is_mgmt"].astype(bool) | agg["is_dir"].astype(bool))
        mgmt_shares = float(agg.loc[mgmt_mask, "shares"].sum())
        insider_shares = float(agg["shares"].sum())
    else:
        mgmt_shares = insider_shares = 0.0
    own_form4 = _div(mgmt_shares, total_shares)         # Form-4-Schaetzung
    ownership_all = _div(insider_shares, total_shares)  # alle Insider

    # Exakter Wert aus DEF 14A (Gruppe Direktoren+Officers), wenn parsbar
    bo = _load_beneficial(ticker)
    own_def14a = bo.get("group_pct") if bo else None
    ownership = own_def14a if own_def14a is not None else own_form4
    own_source = "DEF 14A" if own_def14a is not None else "Form 4 (Schaetzung)"

    # Management-Turnover: 8-K Item 5.02 im Zeitfenster
    try:
        changes = _load_mgmt_changes(ticker)
    except Exception:  # noqa: BLE001
        changes = []
    cutoff = today - pd.Timedelta(days=int(n_years) * 365)
    chg_dts = pd.to_datetime([c.get("filed_at") for c in changes],
                             errors="coerce", utc=True)
    turnover = int((chg_dts >= cutoff).sum()) if len(chg_dts) else 0
    per_year = turnover / max(1, int(n_years))

    checks = []
    if ceo_ten is not None:
        checks.append(("CEO-Tenure ≥ 5 Jahre", ceo_ten >= 5))
    if ownership is not None:
        checks.append(("Insider Ownership ≥ 5 %", ownership >= 0.05))
    checks.append((f"Management-Turnover ≤ 1/Jahr ({int(n_years)} J)",
                   per_year <= 1.0))
    _verdict_box(checks, strong="stabil / engagiert", mixed="durchschnittlich",
                 weak="instabil / wenig Eigenanteil", lead="Management")

    m = st.columns(4)
    m[0].metric("CEO-Tenure",
                f"{de_dec(ceo_ten, 1)} J" if ceo_ten is not None else "—",
                help=f"Seit fruehestem Insider-Filing{(' · ' + ceo_name) if ceo_name else ''}")
    m[1].metric("CFO-Tenure",
                f"{de_dec(cfo_ten, 1)} J" if cfo_ten is not None else "—",
                help=f"Seit fruehestem Insider-Filing{(' · ' + cfo_name) if cfo_name else ''}")
    m[2].metric(f"Insider Ownership ({own_source})",
                _pct(ownership) if ownership is not None else "—",
                help="Management-Beteiligung (Direktoren + Officers als "
                     "Gruppe). DEF 14A = exakte Proxy-Angabe; sonst Form-4-"
                     f"Schaetzung. Form-4-Wert: "
                     f"{_pct(own_form4) if own_form4 is not None else '—'} · "
                     f"alle Insider: "
                     f"{_pct(ownership_all) if ownership_all is not None else '—'}")
    m[3].metric(f"Mgmt-Wechsel ({int(n_years)} J)",
                str(turnover),
                delta=f"{de_dec(per_year, 1)}/Jahr", delta_color="off",
                help="8-K Item 5.02 (Abgang/Bestellung Direktoren/Officers)")

    if len(chg_dts):
        cdf = pd.DataFrame({"dt": chg_dts})
        cdf = cdf[cdf["dt"] >= cutoff]
        if not cdf.empty:
            cdf["Jahr"] = cdf["dt"].dt.year
            per = cdf.groupby("Jahr").size().reset_index(name="Wechsel")
            fig = go.Figure(go.Bar(x=per["Jahr"], y=per["Wechsel"],
                                   marker_color="#B4862B"))
            fig.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                              title="Management-Wechsel je Jahr (8-K 5.02)",
                              yaxis_title="Anzahl")
            st.plotly_chart(fig, use_container_width=True)

    if agg is not None and not agg.empty:
        with st.expander("Groesste Insider-Bestaende"):
            top = agg.sort_values("shares", ascending=False).head(12)
            st.dataframe(pd.DataFrame([{
                "Person": r["owner"],
                "Funktion": r["relationship"],
                "Typ": ("Management" if (r["is_mgmt"] or r["is_dir"])
                        else "10%-Eigner" if r["is_10"] else "—"),
                "Bestand (Stk.)": de_int(r["shares"]),
                "% ausstehend": (_pct(_div(r["shares"], total_shares))
                                 if total_shares else "—"),
            } for _gid, r in top.iterrows()]),
                use_container_width=True, hide_index=True)

    # ---- Institutionelle Eigentuemer (13F) ------------------------------
    st.markdown("#### Institutionelle Eigentuemer (13F)")
    inst = _load_institutional(ticker)
    ih = inst.get("holdings") if inst else []
    if not ih:
        st.info("Keine 13F-Positionen ermittelbar.")
        with st.expander("13F — Diagnose"):
            st.write({"Fehler": inst.get("error") if inst else "—"})
    else:
        idf = pd.DataFrame(ih)
        idf = idf[idf["shares"].notna() & (idf["shares"] > 0)]
        # je Manager die juengste Periode
        idf["pdt"] = pd.to_datetime(idf["period"], errors="coerce")
        idf = idf.sort_values("pdt")
        latest_period = idf["pdt"].max()
        cur_q = idf[idf["pdt"] == latest_period] if pd.notna(latest_period) \
            else idf
        cur_q = (cur_q.sort_values("shares", ascending=False)
                 .drop_duplicates("manager"))
        inst_shares = float(cur_q["shares"].sum())
        top10 = cur_q.head(10)
        top10_shares = float(top10["shares"].sum())

        sm_list = [s.upper() for s in
                   SCORE_CFG.get("management", {}).get("smart_money", [])]
        sm_mask = cur_q["manager"].str.upper().apply(
            lambda nm: any(s in nm for s in sm_list))
        smart_shares = float(cur_q.loc[sm_mask, "shares"].sum())

        inst_pct = _div(inst_shares, total_shares)
        top10_pct = _div(top10_shares, total_shares)
        smart_pct = _div(smart_shares, total_shares)

        im = st.columns(3)
        im[0].metric("Institutionell (Top-Sample)",
                     _pct(inst_pct) if inst_pct is not None else "—",
                     help=f"Σ {len(cur_q)} gemeldeter Positionen / "
                          "ausstehende Aktien (Untergrenze, keine "
                          "Vollaggregation)")
        im[1].metric("Top-10-Anteil",
                     _pct(top10_pct) if top10_pct is not None else "—",
                     help="10 groesste 13F-Halter / ausstehende Aktien")
        im[2].metric("Smart-Money-Anteil",
                     _pct(smart_pct) if smart_pct is not None else "—",
                     help="Langfrist-/Quality-Manager (config) / "
                          "ausstehende Aktien")

        # Trend: Top-Sample-Summe je Quartal
        if idf["pdt"].nunique() >= 2 and total_shares:
            per_q = (idf.sort_values("pdt")
                     .drop_duplicates(["manager", "pdt"])
                     .groupby("pdt")["shares"].sum().reset_index())
            per_q["pct"] = per_q["shares"] / total_shares * 100
            fig = go.Figure(go.Scatter(
                x=per_q["pdt"], y=per_q["pct"], mode="lines+markers",
                line=dict(color="#0F6E56", width=2),
                hovertemplate="%{x|%Y-%m}<br>%{y:.1f}%<extra></extra>"))
            fig.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10),
                              title="Institutioneller Anteil je Quartal "
                                    "(Top-Sample, %)", yaxis_title="%")
            st.plotly_chart(fig, use_container_width=True)

        with st.expander("Groesste institutionelle Halter (13F)"):
            st.dataframe(pd.DataFrame([{
                "Manager": r["manager"],
                "Aktien": de_int(r["shares"]),
                "% ausstehend": (_pct(_div(r["shares"], total_shares))
                                 if total_shares else "—"),
                "Smart Money": ("✓" if any(s in r["manager"].upper()
                                           for s in sm_list) else ""),
            } for _i, r in top10.iterrows()]),
                use_container_width=True, hide_index=True)
        st.caption("13F-Positionen sind eine Stichprobe der zuletzt "
                   "gemeldeten Halter (keine Vollaggregation aller Filer) — "
                   "Anteile daher Untergrenze. 13F meldet mit bis zu 45 "
                   "Tagen Verzug und nur Long-US-Positionen.")

    if bo and own_def14a is None:
        with st.expander("DEF-14A-Ownership — Diagnose"):
            st.write({
                "URL": bo.get("url"),
                "eingereicht": str(bo.get("filed_at"))[:10],
                "Textlaenge": bo.get("text_len"),
                "Fehler": bo.get("error"),
            })
            if bo.get("snippet"):
                st.caption("Textausschnitt um 'as a group':")
                st.code(bo["snippet"])

    bo_note = (f" Quelle DEF 14A vom {str(bo['filed_at'])[:10]}."
               if bo and bo.get("group_pct") is not None else "")
    st.caption("Tenure approximiert ueber das frueheste Insider-Filing der "
               "aktuellen Person (per CIK; Form 3 ≈ Eintritt als Insider) — "
               "nicht zwingend der Rollenbeginn. Insider Ownership: bevorzugt "
               "die exakte 'directors and officers as a group'-Zeile der "
               "DEF-14A-Beneficial-Ownership-Tabelle (inkl. indirekter "
               "Bestaende ueber Trusts/LLCs); faellt sonst auf die Form-4-"
               "Schaetzung (juengste Bestaende je CIK / ausstehende Aktien) "
               "zurueck." + bo_note + " Turnover = 8-K Item 5.02 im "
               "Zeitfenster. Eigenstaendig, nicht im Gesamt-Score.")


# ---------- Earnings Quality Score ----------

_EQ_CATS = {
    "acquisition":   (["Akquisitionskosten"], "Akquisitionskosten"),
    "restructuring": (["Restrukturierung"], "Restrukturierungen"),
    "litigation":    (["Rechtsstreit/Settlement"], "Rechtsstreitigkeiten"),
    "tax":           (["Steueranpassungen"], "Steuertricks"),
    "one_time":      (["Einmaleffekte", "Wertminderung"], "Einmaleffekte"),
}


def _cat_subscore(categories: dict, labels) -> float:
    """1.0 wenn keine Erwaehnung, 0.5 bei 1, 0.0 bei wiederkehrend (>=2)."""
    total = sum(categories.get(k, 0) for k in labels)
    if total == 0:
        return 1.0
    return 0.5 if total == 1 else 0.0


def render_quality(ticker, n_years):
    eq = SCORE_CFG["earnings_quality"]
    wts, bands = eq["weights"], eq["bands"]
    sbc_t = eq["sbc_thresholds"]

    # SBC quantitativ (juengstes 10-K)
    sbc_sub = None
    sbc_detail = "keine Daten"
    try:
        with st.spinner(f"Lade SBC/Cashflow fuer {ticker} …"):
            sbc_rows = _load_sbc(ticker, n_years)
    except Exception:  # noqa: BLE001
        sbc_rows = []
    if sbc_rows:
        _m = _sbc_metrics(sbc_rows)
        ratio = _m["sbc_cfo"]
        if ratio is not None:
            sbc_sub = (1.0 if ratio <= sbc_t["clean"]
                       else 0.0 if ratio > sbc_t["heavy"] else 0.5)
            sbc_detail = f"SBC/op. CF {_pct(ratio, 1)}"

    # Sonder-Add-backs aus dem Earnings-Exhibit
    try:
        with st.spinner("Lade Earnings-Exhibit …"):
            _meta, ana = _load_gaap(ticker)
    except Exception:  # noqa: BLE001
        _meta, ana = None, None
    cats = ana["categories"] if ana else None

    st.markdown(f"### {ticker} — Earnings Quality Score")

    rows = [("sbc", "SBC (Aktienverguetung)", sbc_sub, sbc_detail)]
    for key, (labels, label) in _EQ_CATS.items():
        if cats is None:
            rows.append((key, label, None, "kein Exhibit"))
        else:
            sub = _cat_subscore(cats, labels)
            cnt = sum(cats.get(x, 0) for x in labels)
            detail = ("nicht erwaehnt" if cnt == 0
                      else f"{cnt}× erwaehnt (Add-back)")
            rows.append((key, label, sub, detail))

    num = den = 0.0
    for key, _lbl, sub, _d in rows:
        if sub is not None:
            num += sub * wts[key]
            den += wts[key]
    if den == 0:
        st.error("Weder SBC- noch Exhibit-Daten verfuegbar."); st.stop()
    score = round(100 * num / den)
    n_ok = sum(1 for _k, _l, s, _d in rows if s is not None)

    if score >= bands["strong"]:
        st.success(f"## {score}/100 — hohe Ergebnisqualitaet")
    elif score >= bands["mixed"]:
        st.info(f"## {score}/100 — mittlere Qualitaet")
    else:
        st.warning(f"## {score}/100 — niedrige Qualitaet (viele "
                   "Bereinigungen)")
    st.caption(f"Gewichteter Mittelwert ueber {n_ok}/6 auswertbare "
               "Dimensionen (fehlende ausgeklammert, Gewichte renormiert). "
               "Hoeher = sauberere, weniger bereinigte Gewinne.")

    bar = pd.DataFrame([{
        "Dimension": lbl,
        "Score": round(100 * sub) if sub is not None else None,
        "Detail": det,
    } for _k, lbl, sub, det in rows])
    fig = go.Figure(go.Bar(
        x=bar["Score"], y=bar["Dimension"], orientation="h",
        marker_color=["#1D9E75" if (s is not None and s >= 70)
                      else "#B4862B" if (s is not None and s >= 40)
                      else "#A32D2D" if s is not None else "#CFCDC6"
                      for s in bar["Score"]],
        text=[f"{s}" if s is not None else "n/a" for s in bar["Score"]],
        textposition="auto", customdata=bar["Detail"],
        hovertemplate="%{y}: %{x}/100<br>%{customdata}<extra></extra>"))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis=dict(range=[0, 100], title="Teil-Score"))
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(pd.DataFrame([{
        "Dimension": lbl,
        "Gewicht": f"{int(wts[k] * 100)} %",
        "Score": f"{round(100 * sub)}/100" if sub is not None else "n/a",
        "Detail": det,
    } for k, lbl, sub, det in rows]), use_container_width=True,
        hide_index=True)

    st.caption("Earnings Quality = wie wenig die Gewinne von weichen "
               "Bereinigungen abhaengen. SBC quantitativ (SBC/operativer "
               "Cashflow); Akquisitionskosten, Restrukturierungen, "
               "Rechtsstreitigkeiten, Steuertricks und Einmaleffekte aus "
               "den Add-back-Kategorien des Earnings-Exhibits "
               "(Textheuristik). Wiederkehrende Add-backs senken den Score "
               "staerker. Eigenstaendig, nicht im Gesamt-Score.")


# ---------- Gesamt-Score ----------

# Anzeige-Label -> Config-Schluessel. Gewichte kommen aus SCORE_CFG.
_THEME_KEYS = {
    "Return on Capital": "return_on_capital",
    "Balance Sheet":     "balance_sheet",
    "Stock-based Comp.": "stock_based_comp",
    "GAAP vs non-GAAP":  "gaap_vs_non_gaap",
    "Insider":           "insider",
}
_SCORE_WEIGHTS = {label: SCORE_CFG["weights"][key]
                  for label, key in _THEME_KEYS.items()}


def _subscore(checks):
    """Anteil erfuellter Kriterien (0..1) oder None, wenn keine Daten."""
    if not checks:
        return None
    return sum(1 for _, ok in checks if ok) / len(checks)


def render_score(ticker, n_years):
    st.markdown(f"### {ticker} — Gesamt-Qualitaets-Score")

    def _safe(fn):
        try:
            return fn()
        except SecApiError:
            return None
        except Exception:  # noqa: BLE001
            return None

    def _balance():
        latest, _ = _load_balance(ticker, n_years)
        return _checks_balance(latest) if latest else None

    def _roc():
        rows = _load_returns(ticker, n_years)
        return _checks_returns(rows) if rows else None

    def _sbc():
        rows = _load_sbc(ticker, n_years)
        return _checks_sbc(_sbc_metrics(rows)) if rows else None

    def _gaap():
        _meta, ana = _load_gaap(ticker)
        return _checks_gaap(ana) if ana else None

    def _insider():
        tx = _load_insider(ticker)
        if not tx:
            return None
        return _checks_insider(_insider_aggregate(tx, n_years))

    loaders = {
        "Return on Capital": _roc,
        "Balance Sheet":     _balance,
        "Stock-based Comp.": _sbc,
        "GAAP vs non-GAAP":  _gaap,
        "Insider":           _insider,
    }

    with st.spinner(f"Werte alle 5 Themen fuer {ticker} aus …"):
        themes = {name: _safe(fn) for name, fn in loaders.items()}

    # Gewichteter Score ueber verfuegbare Themen (Renormierung)
    num = den = 0.0
    rows = []
    for name, checks in themes.items():
        w = _SCORE_WEIGHTS[name]
        sub = _subscore(checks)
        if sub is not None:
            num += sub * w
            den += w
        rows.append({"Thema": name, "checks": checks, "sub": sub, "w": w})

    if den == 0:
        st.error("Keine Themen lieferten Daten — Ticker pruefen.")
        st.stop()

    score = round(100 * num / den)
    n_ok = sum(1 for r in rows if r["sub"] is not None)
    _bands = SCORE_CFG["bands"]
    if score >= _bands["strong"]:
        st.success(f"## {score}/100 — hohe Qualitaet")
    elif score >= _bands["mixed"]:
        st.info(f"## {score}/100 — gemischt")
    else:
        st.warning(f"## {score}/100 — schwach")
    st.caption(f"Gewichteter Mittelwert ueber {n_ok}/5 auswertbare Themen "
               f"(fehlende ausgeklammert, Gewichte renormiert). "
               f"Lookback/Jahre: {int(n_years)}.")

    # Teil-Scores als Balken
    bar = pd.DataFrame([{
        "Thema": r["Thema"],
        "Score": round(100 * r["sub"]) if r["sub"] is not None else None,
        "Gewicht": r["w"],
    } for r in rows])
    fig = go.Figure(go.Bar(
        x=bar["Score"], y=bar["Thema"], orientation="h",
        marker_color=["#1D9E75" if (s is not None and s >= _bands["strong"])
                      else "#B4862B" if (s is not None and s >= _bands["mixed"])
                      else "#A32D2D" if s is not None else "#CFCDC6"
                      for s in bar["Score"]],
        text=[f"{s}" if s is not None else "n/a" for s in bar["Score"]],
        textposition="auto",
        hovertemplate="%{y}: %{x}/100<extra></extra>"))
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis=dict(range=[0, 100], title="Teil-Score"))
    st.plotly_chart(fig, use_container_width=True)

    # Detail je Thema
    for r in rows:
        w_pct = f"{int(r['w'] * 100)} %"
        if r["sub"] is None:
            st.markdown(f"**{r['Thema']}** · Gewicht {w_pct} — _keine "
                        f"Daten / nicht auswertbar_")
            continue
        with st.expander(
                f"{r['Thema']} · {round(100 * r['sub'])}/100 · "
                f"Gewicht {w_pct}"):
            for name, ok in r["checks"]:
                st.markdown(f"{'✅' if ok else '❌'} {name}")

    st.caption("Score = gewichteter Anteil erfuellter Qualitaets-Kriterien "
               "je Thema. Heuristik, kein Anlageurteil. Datenbasis: "
               "on-Demand sec-api.io, nicht persistiert.")


# =====================================================================
# Seite
# =====================================================================

st.title("🧭 Ad-Hoc Analysis")
st.caption(
    "Qualitaetspruefung beliebiger Aktien nach Shearn, *The Investment "
    "Checklist*. Daten on-Demand von sec-api.io — keine Speicherung.")

_TOPICS = {
    "★ Gesamt-Score (alle 5 Themen)": render_score,
    "★ Moat-Score (6 Faktoren)": render_moat,
    "★ Earnings Quality Score (6 Faktoren)": render_quality,
    "Balance Sheet — Bilanzstaerke": render_balance,
    "Return on Capital — ROIC / ROCE / ROE / ROA": render_returns,
    "Insider Sales / Buys — Form 3/4/5": render_insider,
    "Management — Tenure, Ownership, Turnover": render_management,
    "Stock-based Compensation — SBC & Verwaesserung": render_sbc,
    "Gewinnruecklagen, EPS, Equity, FCF & EV — Verlauf": render_earnings,
    "GAAP vs non-GAAP — Earnings-Exhibit": render_gaap,
}
_topic = st.selectbox("Thema", list(_TOPICS.keys()))

_yr_label = ("Lookback (Jahre)"
             if _topic.startswith("Insider") or _topic.startswith("Management")
             else "Jahre (10-K)")
_c1, _c2 = st.columns([3, 1])
_ticker = _c1.text_input(
    "Ticker (US-gelistet, EDGAR)", value="",
    placeholder="z. B. AAPL, MSFT, NVDA").strip().upper()
_n_years = _c2.number_input(_yr_label, min_value=1, max_value=10,
                            value=3 if _topic.startswith("Insider") else 5,
                            step=1)
st.button("Analysieren", type="primary", disabled=not _ticker)

_render = _TOPICS[_topic]
if _render is None:
    st.info("Dieses Thema ist noch in Arbeit.")
    st.stop()
if not _ticker:
    st.stop()

_render(_ticker, int(_n_years))

st.caption(
    "Quelle: sec-api.io (XBRL-to-JSON). On-Demand geladen, nicht "
    "persistiert. Kennzahlen aus konsolidierten US-GAAP-Posten; "
    "fehlende XBRL-Tags fuehren zu '—'.")
