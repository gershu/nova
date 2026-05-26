"""Stufe-1-Filter + Scoring fuer den Quality-GARP-Screener.

FilterConfig haelt alle Schwellen + Achsen-Gewichte; parametrisierbar via
JSON-Params-File. evaluate() laeuft pro Instrument und liefert eine
ScreenCandidate mit:
  - quality_score / growth_score / value_score (0..1 = Anteil bestandener
    Kriterien je Achse — bewusst simpel, weil tuning-transparent)
  - composite_score (gewichtete Summe der Achsen)
  - hard_filter_passes (alle Hard-Filter wie market_cap, sector_blacklist
    bestanden?)
  - criteria_detail (pro Kriterium {value, threshold, passed, axis})
  - trends (Stufe-2-Flags)
  - metrics_used (Roh-Metriken — Material fuer Stage-3-Prompts)

Bewusste Design-Entscheidungen:
  - Bei fehlender Datenlage zaehlt das Kriterium als 'fail' (nicht 'pass'
    by default). Vorsichtsprinzip — kein Bonus fuer Datenluecken.
  - Composite = einfache gewichtete Achsen-Summe. Komplexere Scoring
    (z.B. z-Score gegen Sektor-Median) ist Phase D, wenn die Schwellen
    grob stimmen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict


@dataclass
class FilterConfig:
    # Quality / Profitabilitaet
    min_roic:                  float = 0.12      # ROIC ≥ 12 %
    min_gross_margin:          float = 0.35
    min_net_margin:            float = 0.12
    max_net_debt_to_ebitda:    float = 3.0       # niedriger ist besser

    # Growth (aus eigener Historie berechnet)
    min_revenue_cagr_5y:       float = 0.08
    min_net_income_cagr_5y:    float = 0.08
    min_revenue_q_yoy:         float = 0.05

    # Valuation (fair, nicht billig)
    max_peg_ratio:             float = 2.0
    min_fcf_yield:             float = 0.025
    max_pe_forward:            float = 40.0

    # Hard-Filter
    min_market_cap:            float = 5e9       # 5 Mrd EUR/USD
    sector_blacklist:          list[str] = field(default_factory=list)

    # Achsen-Gewichte fuer Composite-Score
    weight_quality:            float = 0.40
    weight_growth:             float = 0.35
    weight_value:              float = 0.25

    # Output
    top_n:                     int = 30
    min_composite_score:       float = 0.50


@dataclass
class Criterion:
    name:      str
    axis:      str               # 'quality' | 'growth' | 'value'
    value:     float | None
    threshold: float
    direction: str               # '>=' | '<='
    passed:    bool

    def to_dict(self) -> dict:
        return {
            "name":      self.name,
            "axis":      self.axis,
            "value":     self.value,
            "threshold": self.threshold,
            "direction": self.direction,
            "passed":    bool(self.passed),
        }


@dataclass
class ScreenCandidate:
    ref_instrument_id:  str
    symbol:             str
    name:               str
    sector:             str | None
    market_cap:         float | None

    quality_score:      float
    growth_score:       float
    value_score:        float
    composite_score:    float
    hard_filter_passes: bool

    criteria_detail:    list[Criterion]
    trends:             dict
    metrics_used:       dict


def _passes(value, threshold, direction) -> bool:
    """Pass-Logik: bei value=None gilt 'fail' (Vorsicht)."""
    if value is None:
        return False
    if direction == ">=":
        return value >= threshold
    if direction == "<=":
        return value <= threshold
    raise ValueError(f"unknown direction {direction!r}")


def _safe(d: dict | None, key: str):
    """Robust gegenueber None-dicts und fehlenden Keys."""
    if not d:
        return None
    v = d.get(key)
    return None if v is None else v


def evaluate(
    ref_instrument_id: str,
    symbol: str,
    name: str,
    sector: str | None,
    fundamentals: dict,         # row aus ref_fundamentals_latest als dict
    cagr: dict | None,          # output von history.compute_cagr_5y
    qyoy: dict | None,          # output von history.compute_q_yoy
    trends: dict,               # output von history.compute_trends
    config: FilterConfig,
) -> ScreenCandidate:
    """Ein Instrument scoren. Kein DB-Zugriff hier — alle Daten als Input."""

    # --- Roh-Metriken einsammeln (auch fuer LLM-Prompt) ---
    market_cap = _safe(fundamentals, "market_cap")

    roic               = _safe(fundamentals, "roic")
    gross_margin       = _safe(fundamentals, "gross_margin")
    net_margin         = _safe(fundamentals, "net_margin")
    net_debt_to_ebitda = _safe(fundamentals, "net_debt_to_ebitda")
    peg_ratio          = _safe(fundamentals, "peg_ratio")
    fcf_yield          = _safe(fundamentals, "fcf_yield")
    pe_forward         = _safe(fundamentals, "pe_forward")

    revenue_cagr_5y    = _safe(cagr, "revenue_cagr_5y")
    ni_cagr_5y         = _safe(cagr, "net_income_cagr_5y")
    revenue_q_yoy      = _safe(qyoy, "revenue_q_yoy")

    metrics_used = {
        "market_cap":          market_cap,
        "roic":                roic,
        "gross_margin":        gross_margin,
        "net_margin":          net_margin,
        "net_debt_to_ebitda":  net_debt_to_ebitda,
        "peg_ratio":           peg_ratio,
        "fcf_yield":           fcf_yield,
        "pe_forward":          pe_forward,
        "revenue_cagr_5y":     revenue_cagr_5y,
        "net_income_cagr_5y":  ni_cagr_5y,
        "revenue_q_yoy":       revenue_q_yoy,
        "cagr_now_anchor":     _safe(cagr, "now_anchor"),
        "cagr_then_anchor":    _safe(cagr, "then_anchor"),
    }

    # --- Kriterien je Achse ---
    crits: list[Criterion] = [
        # Quality
        Criterion("roic ≥ min",                "quality",
                  roic, config.min_roic, ">=",
                  _passes(roic, config.min_roic, ">=")),
        Criterion("gross_margin ≥ min",        "quality",
                  gross_margin, config.min_gross_margin, ">=",
                  _passes(gross_margin, config.min_gross_margin, ">=")),
        Criterion("net_margin ≥ min",          "quality",
                  net_margin, config.min_net_margin, ">=",
                  _passes(net_margin, config.min_net_margin, ">=")),
        Criterion("net_debt_to_ebitda ≤ max",  "quality",
                  net_debt_to_ebitda, config.max_net_debt_to_ebitda, "<=",
                  _passes(net_debt_to_ebitda,
                          config.max_net_debt_to_ebitda, "<=")),

        # Growth
        Criterion("revenue_cagr_5y ≥ min",     "growth",
                  revenue_cagr_5y, config.min_revenue_cagr_5y, ">=",
                  _passes(revenue_cagr_5y, config.min_revenue_cagr_5y, ">=")),
        Criterion("net_income_cagr_5y ≥ min",  "growth",
                  ni_cagr_5y, config.min_net_income_cagr_5y, ">=",
                  _passes(ni_cagr_5y, config.min_net_income_cagr_5y, ">=")),
        Criterion("revenue_q_yoy ≥ min",       "growth",
                  revenue_q_yoy, config.min_revenue_q_yoy, ">=",
                  _passes(revenue_q_yoy, config.min_revenue_q_yoy, ">=")),

        # Value
        Criterion("peg_ratio ≤ max",           "value",
                  peg_ratio, config.max_peg_ratio, "<=",
                  _passes(peg_ratio, config.max_peg_ratio, "<=")),
        Criterion("fcf_yield ≥ min",           "value",
                  fcf_yield, config.min_fcf_yield, ">=",
                  _passes(fcf_yield, config.min_fcf_yield, ">=")),
        Criterion("pe_forward ≤ max",          "value",
                  pe_forward, config.max_pe_forward, "<=",
                  _passes(pe_forward, config.max_pe_forward, "<=")),
    ]

    # --- Hard-Filter (eigenstaendig, vor Achsen-Score) ---
    cap_ok    = market_cap is not None and market_cap >= config.min_market_cap
    sector_ok = (not sector) or (sector not in config.sector_blacklist)
    hard_filter_passes = bool(cap_ok and sector_ok)

    # --- Achsen-Scores: Anteil bestandener Kriterien je Achse ---
    def axis_score(axis: str) -> float:
        axis_crits = [c for c in crits if c.axis == axis]
        if not axis_crits:
            return 0.0
        return sum(1 for c in axis_crits if c.passed) / len(axis_crits)

    q_score = axis_score("quality")
    g_score = axis_score("growth")
    v_score = axis_score("value")
    composite = (config.weight_quality * q_score
                 + config.weight_growth  * g_score
                 + config.weight_value   * v_score)

    return ScreenCandidate(
        ref_instrument_id=  ref_instrument_id,
        symbol=             symbol,
        name=               name,
        sector=             sector,
        market_cap=         market_cap,
        quality_score=      q_score,
        growth_score=       g_score,
        value_score=        v_score,
        composite_score=    composite,
        hard_filter_passes= hard_filter_passes,
        criteria_detail=    crits,
        trends=             trends or {},
        metrics_used=       metrics_used,
    )


def serialize_candidate(c: ScreenCandidate) -> dict:
    """Fuer DB-Persistenz: dict mit JSON-Strings fuer komplexe Felder."""
    return {
        "ref_instrument_id":   c.ref_instrument_id,
        "symbol":              c.symbol,
        "name":                c.name,
        "sector":              c.sector,
        "market_cap":          c.market_cap,
        "quality_score":       c.quality_score,
        "growth_score":        c.growth_score,
        "value_score":         c.value_score,
        "composite_score":     c.composite_score,
        "hard_filter_passes":  c.hard_filter_passes,
        "criteria_detail_json": json.dumps(
            [cr.to_dict() for cr in c.criteria_detail],
            default=float),
        "trend_flags_json":    json.dumps(c.trends, default=float),
        "metrics_json":        json.dumps(c.metrics_used, default=float),
    }


def config_to_dict(cfg: FilterConfig) -> dict:
    return asdict(cfg)
