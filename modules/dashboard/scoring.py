"""Zentrale, reine Score-Kriterien — Single Source fuer Report-Verdict UND
Gesamt-Qualitaets-Score.

Jede ``checks_*``-Funktion liefert eine Liste ``[(label, passed_bool), …]``.
Beide Aufrufer — der pro-Thema-Verdict in der View und der gewichtete
Gesamt-Score — rufen exakt dieselbe Funktion auf, damit pass/fail nie
auseinanderlaufen. Reines Modul (kein Streamlit). Schwellen kommen als dict
(aus modules.dashboard.score_config), nicht hartkodiert.
"""

from __future__ import annotations

from modules.dashboard import finmetrics as fm


def subscore(checks):
    """Anteil erfuellter Kriterien (0..1) oder None, wenn keine Daten."""
    if not checks:
        return None
    return sum(1 for _, ok in checks if ok) / len(checks)


def _de(v, places: int = 1) -> str:
    """Deutsche Dezimaldarstellung fuer Schwellen-Labels: 1.5 -> '1,5'."""
    return f"{float(v):.{places}f}".replace(".", ",")


def _pc(v, places: int = 0) -> str:
    """Bruchteil -> Prozent-Label: 0.15 -> '15 %'."""
    return _de(float(v) * 100.0, places) + " %"


# ---- Balance Sheet (Bilanzstaerke) ----
def checks_balance(bs, thr) -> list:
    """bs: BalanceSheet-Dataclass; thr: thresholds['balance_sheet']."""
    if bs is None:
        return []
    out = []
    cr = fm.safe_div(bs.assets_current, bs.liabilities_current)
    if cr is not None:
        out.append((f"Current Ratio > {_de(thr['current_ratio_min'], 1)}",
                    cr > thr["current_ratio_min"]))
    if bs.net_debt is not None:
        out.append(("Netto-Cash (Net Debt < 0)", bs.net_debt < 0))
    de = fm.safe_div(bs.total_debt, bs.equity)
    if de is not None:
        out.append((f"Debt/Equity < {_de(thr['debt_to_equity_max'], 1)}",
                    de < thr["debt_to_equity_max"]))
    eqr = fm.safe_div(bs.equity, bs.total_assets)
    if eqr is not None:
        out.append((f"Eigenkapitalquote > {_pc(thr['equity_ratio_min'], 0)}",
                    eqr > thr["equity_ratio_min"]))
    return out


# ---- Return on Capital ----
def checks_returns(rets, thr) -> list:
    """rets: Liste von returns_from_metrics-dicts; thr:
    thresholds['return_on_capital']."""
    if not rets:
        return []
    rl = rets[-1]
    out = []
    if rl.get("roic") is not None:
        out.append((f"ROIC > {_pc(thr['roic_min'], 0)}",
                    rl["roic"] > thr["roic_min"]))
    if rl.get("roe") is not None:
        out.append((f"ROE > {_pc(thr['roe_min'], 0)}",
                    rl["roe"] > thr["roe_min"]))
    if rl.get("roa") is not None:
        out.append((f"ROA > {_pc(thr['roa_min'], 0)}",
                    rl["roa"] > thr["roa_min"]))
    all_roic = [r.get("roic") for r in rets if r.get("roic") is not None]
    if len(all_roic) >= 2:
        out.append(("ROIC durchgehend positiv",
                    all(x > 0 for x in all_roic)))
    return out


# ---- Stock-based Compensation ----
def checks_sbc(sbc_rev, sbc_cfo, dil, thr) -> list:
    """Vorberechnete Quoten; thr: thresholds['stock_based_comp']."""
    out = []
    if sbc_rev is not None:
        out.append((f"SBC < {_pc(thr['sbc_to_revenue_max'], 0)} vom Umsatz",
                    sbc_rev < thr["sbc_to_revenue_max"]))
    if sbc_cfo is not None:
        out.append((f"SBC < {_pc(thr['sbc_to_cfo_max'], 0)} vom operativen "
                    "Cashflow", sbc_cfo < thr["sbc_to_cfo_max"]))
    if dil is not None:
        out.append((f"Aktienzahl ≤ +{_pc(thr['dilution_cagr_max'], 0)} p.a. "
                    "(kaum Verwaesserung)", dil <= thr["dilution_cagr_max"]))
    return out


# ---- GAAP vs non-GAAP ----
def checks_gaap(mentions, adds_back_sbc, n_categories, thr) -> list:
    """thr: thresholds['gaap_vs_non_gaap']."""
    return [
        (f"Non-GAAP-Nutzung moderat (< {thr['mentions_max']} Erwaehnungen)",
         (mentions or 0) < thr["mentions_max"]),
        ("SBC NICHT herausgerechnet", not adds_back_sbc),
        (f"≤ {thr['categories_max']} Anpassungskategorien",
         (n_categories or 0) <= thr["categories_max"]),
    ]


# ---- Insider (Markt-Trades-Aggregat) ----
def checks_insider(buy_val, sell_val, n_buyers, n_sellers, thr) -> list:
    """thr: thresholds['insider']."""
    cm = thr["cluster_buyers_min"]
    return [
        ("Netto-Insiderkaeufe (Wert)", (buy_val - sell_val) > 0),
        ("Mehr Kaeufer als Verkaeufer", n_buyers > n_sellers),
        (f"Cluster-Kauf (>= {cm} Kaeufer)", n_buyers >= cm),
    ]
