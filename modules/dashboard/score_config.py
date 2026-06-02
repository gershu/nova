"""Gemeinsame Score-/Schwellen-Konfiguration (config/ad_hoc_score.yaml).

Single Source — bisher in views/ad_hoc.py dupliziert; die Analyse-View nutzt
sie ueber dieses Modul, Ad-Hoc zieht in Phase 4 nach. Reines Modul.
"""

from __future__ import annotations

import copy
import pathlib

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

_CFG_PATH = (pathlib.Path(__file__).resolve().parents[2]
             / "config" / "ad_hoc_score.yaml")

DEFAULTS = {
    "weights": {"return_on_capital": 0.30, "balance_sheet": 0.25,
                "stock_based_comp": 0.20, "gaap_vs_non_gaap": 0.15,
                "insider": 0.10},
    "bands": {"strong": 70, "mixed": 40},
    "thresholds": {
        "balance_sheet": {"current_ratio_min": 1.5, "debt_to_equity_max": 0.5,
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
        "cluster_buyers_min": 3, "meaningful_sell_pct": 0.20,
        "signal": {"bullish_min": 35, "bearish_max": -20},
    },
    "earnings_quality": {
        "weights": {"sbc": 0.25, "acquisition": 0.15, "restructuring": 0.15,
                    "litigation": 0.15, "tax": 0.15, "one_time": 0.15},
        "bands": {"strong": 70, "mixed": 40},
        "sbc_thresholds": {"clean": 0.05, "heavy": 0.15},
    },
    "physical_growth": {"weights": {"ppe": 0.4, "employees": 0.3,
                                    "capex": 0.3}},
    "management": {"smart_money": [
        "BERKSHIRE HATHAWAY", "BAILLIE GIFFORD", "FUNDSMITH", "AKRE CAPITAL",
        "RUANE", "TCI FUND", "LONE PINE", "PRIMECAP", "CAPITAL RESEARCH",
        "T. ROWE PRICE", "T ROWE PRICE", "MARKEL", "TWEEDY", "DODGE & COX"]},
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


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    try:
        if yaml is not None and _CFG_PATH.is_file():
            loaded = yaml.safe_load(_CFG_PATH.read_text()) or {}
            for sec, vals in loaded.items():
                if isinstance(vals, dict) and isinstance(cfg.get(sec), dict):
                    for k, v in vals.items():
                        if isinstance(v, dict) and isinstance(
                                cfg[sec].get(k), dict):
                            cfg[sec][k].update(v)
                        else:
                            cfg[sec][k] = v
                else:
                    cfg[sec] = vals
    except Exception:  # noqa: BLE001
        pass
    return cfg


CFG = load()
