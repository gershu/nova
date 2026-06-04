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
    fetch_beneficial_ownership_detail, fetch_employee_counts_detail,
    fetch_exhibit_text, fetch_institutional_holdings,
    fetch_insider_first_filing, fetch_insider_transactions,
    fetch_mgmt_changes, fetch_sbc_from_filing, fetch_statements_from_filing,
    fetch_year_metrics_from_filing, fetch_concept_series,
    find_earnings_exhibits, find_filings, get_issuer_cik,
)
from modules.sec_filings.extractor import fetch_employees_from_filing


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
    "physical_growth": {
        "weights": {"ppe": 0.4, "employees": 0.3, "capex": 0.3},
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


def _abs_or(v):
    return abs(v) if v is not None else None


def _cagr(first, last, years):
    if first is None or last is None or first <= 0 or last <= 0 or years < 1:
        return None
    return (last / first) ** (1 / years) - 1


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


@st.cache_data(ttl=86400, show_spinner=False)
def _load_employees(ticker: str) -> dict:
    """Mitarbeiter-Zeitreihe (SEC company-concept) inkl. Diagnose."""
    try:
        return fetch_employee_counts_detail(_issuer_cik(ticker))
    except Exception as e:  # noqa: BLE001
        return {"map": {}, "error": f"{e.__class__.__name__}: {e}"}


def _series_at(m: dict, period_iso: str, tol_days: int = 45):
    """Wert einer {end_iso: val}-Reihe zum Stichtag (exakt, sonst naechster
    innerhalb tol_days)."""
    if not m:
        return None
    if period_iso in m:
        return m[period_iso]
    pe = pd.to_datetime(period_iso)
    best, bestdiff = None, 10 ** 9
    for d, v in m.items():
        diff = abs((pd.to_datetime(d) - pe).days)
        if diff < bestdiff:
            bestdiff, best = diff, v
    return best if bestdiff <= tol_days else None


@st.cache_data(ttl=86400, show_spinner=False)
def _load_ppe_series(ticker: str) -> dict:
    """PP&E-Zeitreihe (us-gaap) via company-concept — robuster als
    xbrl-to-json. Net bevorzugt (Bilanz-Face), sonst Gross."""
    cik = _issuer_cik(ticker)
    m = fetch_concept_series(cik, "us-gaap", "PropertyPlantAndEquipmentNet")
    if not m:
        m = fetch_concept_series(cik, "us-gaap",
                                 "PropertyPlantAndEquipmentGross")
    return m


@st.cache_data(ttl=86400, show_spinner=False)
def _load_emp_text(accession_no: str):
    """Mitarbeiterzahl aus 10-K Item 1 (Textextraktion). None bei Fehler."""
    try:
        return fetch_employees_from_filing(accession_no)
    except Exception:  # noqa: BLE001
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def _load_splits(ticker: str) -> dict:
    """Aktiensplits via yfinance -> {iso_date: ratio}. {} bei Fehler."""
    try:
        import yfinance as yf
        s = yf.Ticker(ticker).splits
        if s is None or len(s) == 0:
            return {}
        return {str(idx.date()): float(r) for idx, r in s.items()
                if r and r > 0}
    except Exception:  # noqa: BLE001
        return {}


def _split_factor(splits: dict, period_iso: str) -> float:
    """Kumulierter Split-Faktor NACH period_iso (Anpassung auf heute).

    z.B. 10:1-Split nach dem Stichtag -> Faktor 10: EPS/10, Aktien*10.
    """
    f = 1.0
    for d, r in (splits or {}).items():
        if d > period_iso:
            f *= r
    return f


def _split_adjust_shares(rows, ticker):
    """diluted_shares je Periode split-bereinigt (Kopie, Cache unberuehrt)."""
    splits = _load_splits(ticker)
    if not splits:
        return rows
    out = []
    for d in rows:
        sh = d.get("diluted_shares")
        if sh:
            out.append(dict(
                d, diluted_shares=sh * _split_factor(
                    splits, str(d.get("period_end"))[:10])))
        else:
            out.append(d)
    return out


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
        cv, slope, emoji, dc = _trend_ampel([r[key] for r in _allret])
        col.metric(
            f"{emoji} {label}",
            _pct(cv) if cv is not None else "—",
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
    splits = _load_splits(ticker)            # fuer Split-Bereinigung

    def _ev(d, target_iso):
        # Aktien split-bereinigt (yfinance-Kurse sind bereits bereinigt)
        sh, nd = d.get("diluted_shares"), d.get("net_debt")
        if sh is not None:
            sh *= _split_factor(splits, str(d["period_end"])[:10])
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
        def _adj_eps(d, key):
            v = d.get(key)
            if v is None:
                return None
            return v / _split_factor(splits, str(d["period_end"])[:10])

        df = pd.DataFrame([{
            "period_end": pd.to_datetime(d["period_end"]),
            "retained": d.get("retained_earnings"),
            "eps_basic": _adj_eps(d, "eps_basic"),
            "eps_diluted": _adj_eps(d, "eps_diluted"),
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

    if splits:
        st.caption("Hinweis: EPS-Chart und EV sind split-bereinigt "
                   f"({len(splits)} Split(s) erkannt); die Rohwerte-Tabelle "
                   "zeigt die as-reported-Werte des jeweiligen Filings.")
    st.caption("Gewinnruecklagen, Eigenkapital als Bilanz-Stichtagswerte; "
               "Free Cash Flow = operativer Cashflow − CapEx; EPS aus der "
               "GuV. Enterprise Value mischt SEC-Fundamentaldaten mit "
               "Marktkursen (yfinance): EV = Jahresend-Kurs × verwaesserte "
               "Aktien + Net Debt — historische Werte sind eine Naeherung. "
               "Diese Kategorie fliesst nicht in den Gesamt-Score.")


# =====================================================================
# Seite
# =====================================================================

st.title("🧭 Ad-Hoc Analysis")
st.caption(
    "Qualitaetspruefung beliebiger Aktien nach Shearn, *The Investment "
    "Checklist*. Daten on-Demand von sec-api.io — keine Speicherung.")

_TOPICS = {
    "Return on Capital — ROIC / ROCE / ROE / ROA": render_returns,
    "Gewinnruecklagen, EPS, Equity, FCF & EV — Verlauf": render_earnings,
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
