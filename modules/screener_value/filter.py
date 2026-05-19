"""Value-Filter — Composite-Score + Hard-Filter auf Universe.

Zwei-Stage:
  1. Hard-Filter: per-Kriterium min/max-Thresholds (P/E < X, ROE > Y, ...).
     Wer NICHT alle vorhandenen Werte einhaelt, faellt raus. Wenn ein Wert
     fehlt (yfinance-Luecke), wird das Kriterium fuer diesen Ticker
     uebersprungen — kein Hart-Fail wegen Null-Daten.
  2. Composite-Score: re-uses screener_csp/conviction.py.
     Sortiert die Ueberlebenden nach final-Score.

Output: ScoredCandidate-Liste, sortiert (best zuerst).

Filter-Konfig kommt vom CLI (params-JSON oder defaults).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from modules.screener_csp.conviction import (
    ConvictionResult,
    conviction_score,
)


@dataclass
class FilterConfig:
    """Hard-Filter-Schwellen. None = Kriterium deaktiviert."""

    # Quality
    min_roe:                Optional[float] = 0.10   # 10% ROE minimum
    min_operating_margin:   Optional[float] = 0.08   # 8% op-margin
    min_revenue_cagr_5y:    Optional[float] = -0.02  # nicht stark schrumpfend

    # Valuation
    max_pe_ttm:             Optional[float] = 35.0   # nicht hyped
    min_fcf_yield:          Optional[float] = 0.02   # mindestens 2% FCF-Yield

    # Solidity
    max_debt_to_equity:     Optional[float] = 2.5
    max_net_debt_to_ebitda: Optional[float] = 5.0

    # Composite-Score Hard-Gate (zusaetzlich zu per-Kriterium)
    min_composite_score:    float = 0.40

    # Size: kleine Werte raus (manchmal exotisch / illiquide)
    min_market_cap:         Optional[float] = 5_000_000_000  # 5 Mrd

    # Sektoren-Whitelist/-Blacklist (None = no filter)
    sector_blacklist:       list[str] = field(default_factory=list)


@dataclass
class ScoredCandidate:
    ref_instrument_id:  str
    symbol:             str
    name:               Optional[str]
    sector:             Optional[str]
    market_cap:         Optional[float]
    conviction_result:  ConvictionResult
    composite_score:    float
    hard_filter_passes: bool
    reject_reasons:     list[str] = field(default_factory=list)


def _check_one(value: Optional[float], threshold: Optional[float],
               kind: str, name: str) -> Optional[str]:
    """Returns reject-reason-string wenn Kriterium versagt, sonst None."""
    if threshold is None or value is None:
        return None
    if kind == "min" and value < threshold:
        return f"{name} {value:.3f} < {threshold:.3f}"
    if kind == "max" and value > threshold:
        return f"{name} {value:.3f} > {threshold:.3f}"
    return None


def evaluate(fundamentals_row: dict, cfg: FilterConfig) -> ScoredCandidate:
    """Bewerte ein einzelnes Symbol gegen die Filter-Config.

    fundamentals_row: dict-like mit ref_fundamentals_latest-Spalten +
                       symbol, name, sector, market_cap.
    """
    sym = fundamentals_row.get("symbol", "?")
    rid = fundamentals_row.get("ref_instrument_id", "?")

    rejects: list[str] = []

    # Per-Kriterium Hard-Filter
    checks = [
        (fundamentals_row.get("roe"),                cfg.min_roe,                "min", "roe"),
        (fundamentals_row.get("operating_margin"),   cfg.min_operating_margin,   "min", "op_margin"),
        (fundamentals_row.get("revenue_cagr_5y"),    cfg.min_revenue_cagr_5y,    "min", "rev_cagr_5y"),
        (fundamentals_row.get("pe_ttm"),             cfg.max_pe_ttm,             "max", "pe_ttm"),
        (fundamentals_row.get("fcf_yield"),          cfg.min_fcf_yield,          "min", "fcf_yield"),
        (fundamentals_row.get("debt_to_equity"),     cfg.max_debt_to_equity,     "max", "d_e"),
        (fundamentals_row.get("net_debt_to_ebitda"), cfg.max_net_debt_to_ebitda, "max", "nd_ebitda"),
        (fundamentals_row.get("market_cap"),         cfg.min_market_cap,         "min", "mcap"),
    ]
    for value, thr, kind, name in checks:
        reason = _check_one(_to_float(value), thr, kind, name)
        if reason:
            rejects.append(reason)

    # Sektor-Blacklist
    sec = fundamentals_row.get("sector")
    if sec and cfg.sector_blacklist and sec in cfg.sector_blacklist:
        rejects.append(f"sector '{sec}' blacklisted")

    # Composite-Score via Conviction
    conv = conviction_score(fundamentals_row)
    composite = conv.score
    if composite < cfg.min_composite_score:
        rejects.append(f"composite {composite:.3f} < {cfg.min_composite_score:.3f}")

    return ScoredCandidate(
        ref_instrument_id  = rid,
        symbol             = sym,
        name               = fundamentals_row.get("name"),
        sector             = sec,
        market_cap         = _to_float(fundamentals_row.get("market_cap")),
        conviction_result  = conv,
        composite_score    = composite,
        hard_filter_passes = len(rejects) == 0,
        reject_reasons     = rejects,
    )


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def rank_candidates(candidates: list[ScoredCandidate],
                    top_n: int = 30,
                    sector_diversification: bool = True,
                    per_sector_cap: int = 4) -> list[ScoredCandidate]:
    """Top-N selection mit optionalem Sector-Cap.

    sector_diversification=True heisst: max `per_sector_cap` Picks pro Sector
    bevor andere Sektoren ueberhaupt eine Chance bekommen. Verhindert dass
    Top-30 zu 80% Tech sind.
    """
    passes = [c for c in candidates if c.hard_filter_passes]
    passes_sorted = sorted(passes, key=lambda c: c.composite_score, reverse=True)

    if not sector_diversification:
        return passes_sorted[:top_n]

    selected: list[ScoredCandidate] = []
    per_sector: dict[str, int] = {}
    overflow: list[ScoredCandidate] = []   # falls per_sector_cap erschoepft
    for c in passes_sorted:
        sec = c.sector or "Unknown"
        if per_sector.get(sec, 0) < per_sector_cap:
            selected.append(c)
            per_sector[sec] = per_sector.get(sec, 0) + 1
            if len(selected) >= top_n:
                break
        else:
            overflow.append(c)
    # Wenn Top-N noch nicht voll: aus overflow auffuellen (sektor-blind)
    for c in overflow:
        if len(selected) >= top_n:
            break
        selected.append(c)
    return selected[:top_n]
