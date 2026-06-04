"""On-Demand Gesamt-Qualitaets-Score fuer EINEN Ticker (Shearn-5-Themen).

Single Source der Score-ORCHESTRIERUNG: welche Daten je Thema geladen werden
und welche Gewichte gelten. Die pass/fail-REGELN liegen zentral in
modules.dashboard.scoring, die Schwellen/Gewichte/Baender in
modules.dashboard.score_config.

Reines Modul (kein Streamlit) -> nutzbar in der Unternehmens-Analyse (View,
gecached) UND im Screener (Batch ueber die angezeigten Top-Picks).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from modules.dashboard import company_data as cd
from modules.dashboard import finmetrics as fm
from modules.dashboard import market as mkt
from modules.dashboard import scoring as sc
from modules.dashboard.score_config import CFG as _CFG


def _insider_years(n_years: int, period: str) -> int:
    return n_years if period == "annual" else max(1, n_years // 4)


def _ck_returns(ticker, n_years, period):
    ym = cd.year_metrics(ticker, n_years=n_years, period=period).get("rows") \
        or []
    rets = [fm.returns_from_metrics(d) for d in ym]
    return sc.checks_returns(rets, _CFG["thresholds"]["return_on_capital"])


def _ck_balance(ticker, n_years, period):
    return sc.checks_balance(cd.balance(ticker),
                             _CFG["thresholds"]["balance_sheet"])


def _ck_sbc(ticker, n_years, period):
    rows = cd.sbc_history(ticker, n_years=n_years, period=period)
    if not rows:
        return []
    last = rows[-1]
    sbc_rev = fm.safe_div(last.get("sbc"), last.get("revenue"))
    sbc_cfo = fm.safe_div(last.get("sbc"), last.get("cfo"))
    adj = fm.split_adjust_shares(rows, mkt.splits(ticker))
    sh = [(d["period_end"], d["diluted_shares"]) for d in adj
          if d.get("diluted_shares")]
    dil = None
    if len(sh) >= 2:
        yrs = (pd.to_datetime(sh[-1][0]) - pd.to_datetime(sh[0][0])).days \
            / 365.25
        if yrs >= 1:
            dil = fm.cagr(sh[0][1], sh[-1][1], yrs)
    return sc.checks_sbc(sbc_rev, sbc_cfo, dil,
                         _CFG["thresholds"]["stock_based_comp"])


def _ck_gaap(ticker, n_years, period):
    ng = cd.earnings_nongaap(ticker)
    if ng.get("categories") is None and ng.get("mentions") is None:
        return []
    return sc.checks_gaap(ng.get("mentions"), ng.get("adds_back_sbc"),
                          len(ng.get("categories") or {}),
                          _CFG["thresholds"]["gaap_vs_non_gaap"])


def _ck_insider(ticker, n_years, period):
    tx = cd.insider_tx(ticker)
    if not tx:
        return []
    years = _insider_years(n_years, period)
    cutoff = (date.today() - timedelta(days=int(years) * 365)).isoformat()
    df = pd.DataFrame(tx)
    if not df.empty and "transaction_date" in df.columns:
        df = df[df["transaction_date"] >= cutoff]
    buy_val = sell_val = 0.0
    n_buyers = n_sellers = 0
    if not df.empty and "code" in df.columns:
        buys, sells = df[df["code"] == "P"], df[df["code"] == "S"]
        if "value" in df.columns:
            buy_val = float(buys["value"].fillna(0).sum())
            sell_val = float(sells["value"].fillna(0).sum())
        if "owner" in df.columns:
            n_buyers = int(buys["owner"].nunique())
            n_sellers = int(sells["owner"].nunique())
    return sc.checks_insider(buy_val, sell_val, n_buyers, n_sellers,
                             _CFG["thresholds"]["insider"])


# (Anzeige-Name, Config-Gewichts-Schluessel, Kriterien-Funktion)
THEMES = [
    ("Return on Capital", "return_on_capital", _ck_returns),
    ("Balance Sheet", "balance_sheet", _ck_balance),
    ("Stock-based Comp.", "stock_based_comp", _ck_sbc),
    ("GAAP vs non-GAAP", "gaap_vs_non_gaap", _ck_gaap),
    ("Insider", "insider", _ck_insider),
]


def theme_checks(ticker: str, *, n_years: int = 5,
                 period: str = "annual") -> list:
    """[(name, key, checks), …] fuer die fuenf Themen; je Thema defensiv."""
    out = []
    for name, key, fn in THEMES:
        try:
            checks = fn(ticker, n_years, period)
        except Exception:  # noqa: BLE001
            checks = []
        out.append((name, key, checks))
    return out


def overall_score(ticker: str, *, n_years: int = 5,
                  period: str = "annual") -> dict:
    """Gewichteter 0-100-Gesamtscore (fehlende Themen renormiert).

    Returns {score:int|None, n_ok:int, bands:dict,
             rows:[{theme,key,sub,w,checks}, …]}.
    """
    weights, bands = _CFG["weights"], _CFG["bands"]
    rows = []
    num = den = 0.0
    for name, key, checks in theme_checks(ticker, n_years=n_years,
                                          period=period):
        w = weights.get(key, 0)
        sub = sc.subscore(checks)
        if sub is not None:
            num += sub * w
            den += w
        rows.append({"theme": name, "key": key, "sub": sub, "w": w,
                     "checks": checks})
    score = round(100 * num / den) if den else None
    n_ok = sum(1 for r in rows if r["sub"] is not None)
    return {"score": score, "n_ok": n_ok, "bands": bands, "rows": rows}
