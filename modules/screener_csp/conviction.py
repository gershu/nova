"""Conviction-Score: Value-Investor-Lens als Multiplier auf den CSP-Score.

Idee: ein CSP-Strike ist nur dann wirklich gut wenn man das Underlying
*auch halten will*, falls es assigned wird. Conviction misst: wie sehr
will ich diesen Titel? Composite aus 5 Dimensionen:

    Profitability   (ROE)
    Solidity        (Debt/Equity + Interest-Coverage Bonus)
    Valuation       (FCF-Yield + PE-Penalty)
    Growth          (Revenue-CAGR-5y)
    Margin          (Operating-Margin)

Null-Toleranz: wenn ein Sub-Score-Input fehlt (yfinance liefert NULL),
wird die Gewichtung der vorhandenen Komponenten renormalisiert. Heisst:
ein Underlying mit nur 3 von 5 Dimensionen abgedeckt wird nicht
systematisch benachteiligt — wir wissen halt weniger.

Score-Range: [0.0, 1.0]
    0.0   nicht-haltenswert nach Value-Kriterien (Loss-making, hoch verschuldet, teuer)
    0.5   neutral / mixed
    1.0   Buffett-Schueler-Approval

Verwendung in screener_csp/__main__.py:
    score(candidate) -> traditional_score
    final_score = traditional_score * conviction(fundamentals_row)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ---------- Threshold-Mappings ----------
# Jede Sub-Score-Funktion: input -> [0.0, 1.0], None -> None (= ignore).
# Linear-interpoliert zwischen den drei Stuetzpunkten.

def _interp(v: Optional[float], lo: float, mid: float, hi: float,
            lo_s: float = 0.0, mid_s: float = 0.5, hi_s: float = 1.0) -> Optional[float]:
    """Piecewise-linear interpolation. Stuetzpunkte (lo,lo_s) (mid,mid_s) (hi,hi_s)."""
    if v is None:
        return None
    if v <= lo:
        return lo_s
    if v >= hi:
        return hi_s
    if v <= mid:
        return lo_s + (v - lo) / (mid - lo) * (mid_s - lo_s) if mid > lo else mid_s
    return mid_s + (v - mid) / (hi - mid) * (hi_s - mid_s) if hi > mid else mid_s


def profitability_score(roe: Optional[float]) -> Optional[float]:
    """ROE 0% -> 0.0, 10% -> 0.5, 20%+ -> 1.0. Negative ROE -> 0.0."""
    return _interp(roe, 0.0, 0.10, 0.20)


def solidity_score(debt_to_equity: Optional[float],
                   interest_coverage: Optional[float] = None) -> Optional[float]:
    """D/E: 3.0+ -> 0.0, 1.0 -> 0.5, 0.0 -> 1.0  (reverse-mapping)
    Plus bonus +0.10 wenn interest_coverage > 5 (Cap auf 1.0).
    """
    if debt_to_equity is None and interest_coverage is None:
        return None
    base = None
    if debt_to_equity is not None:
        # reverse-mapping: low debt = high score
        if debt_to_equity <= 0:
            base = 1.0
        elif debt_to_equity >= 3.0:
            base = 0.0
        elif debt_to_equity <= 1.0:
            base = 1.0 - 0.5 * debt_to_equity   # 0->1.0, 1->0.5
        else:
            base = 0.5 - 0.25 * (debt_to_equity - 1.0)  # 1->0.5, 3->0.0
    if interest_coverage is not None and interest_coverage > 5:
        if base is None:
            base = 0.6  # Coverage allein als positiver Indikator
        else:
            base = min(1.0, base + 0.10)
    return base


def valuation_score(fcf_yield: Optional[float],
                    pe_ttm: Optional[float] = None) -> Optional[float]:
    """FCF-Yield: 0% -> 0.0, 5% -> 0.5, 10%+ -> 1.0.
    Penalty: PE > 30 -> -0.20 (cap auf 0.0).
    """
    if fcf_yield is None and pe_ttm is None:
        return None
    base = _interp(fcf_yield, 0.0, 0.05, 0.10) if fcf_yield is not None else None
    if pe_ttm is not None and pe_ttm > 30:
        penalty = min(0.30, (pe_ttm - 30) / 100.0 + 0.20)
        if base is None:
            base = max(0.0, 0.5 - penalty)
        else:
            base = max(0.0, base - penalty)
    return base


def growth_score(revenue_cagr_5y: Optional[float]) -> Optional[float]:
    """Revenue-CAGR-5y: <=0% -> 0.3 (nicht 0.0 — Stabilitaet hat Wert!), 5% -> 0.7, 10%+ -> 1.0."""
    if revenue_cagr_5y is None:
        return None
    if revenue_cagr_5y <= 0:
        return 0.3
    return _interp(revenue_cagr_5y, 0.0, 0.05, 0.10, lo_s=0.3, mid_s=0.7, hi_s=1.0)


def margin_score(operating_margin: Optional[float]) -> Optional[float]:
    """Op-Margin: 0% -> 0.0, 15% -> 0.5, 30%+ -> 1.0. Negative -> 0.0."""
    return _interp(operating_margin, 0.0, 0.15, 0.30)


# ---------- Composite ----------

WEIGHTS = {
    "profitability": 0.25,
    "solidity":      0.20,
    "valuation":     0.25,
    "growth":        0.15,
    "margin":        0.15,
}


@dataclass
class ConvictionResult:
    score:          float                # 0..1
    components:     dict[str, Optional[float]]   # raw sub-scores (None wenn input missing)
    used_weight:    float                # Summe Weights der vorhandenen Komponenten
    missing:        list[str]            # welche Komponenten fehlen


def conviction_score(fundamentals: dict) -> ConvictionResult:
    """Composite-Score aus einem fundamentals-row dict.

    Akzeptiert dict-like (DB-row, Fundamentals.to_db_dict(), pandas Series).
    Verwendet diese Spalten:
        roe, debt_to_equity, interest_coverage,
        fcf_yield, pe_ttm,
        revenue_cagr_5y, operating_margin
    """
    components: dict[str, Optional[float]] = {
        "profitability": profitability_score(_g(fundamentals, "roe")),
        "solidity":      solidity_score(_g(fundamentals, "debt_to_equity"),
                                         _g(fundamentals, "interest_coverage")),
        "valuation":     valuation_score(_g(fundamentals, "fcf_yield"),
                                          _g(fundamentals, "pe_ttm")),
        "growth":        growth_score(_g(fundamentals, "revenue_cagr_5y")),
        "margin":        margin_score(_g(fundamentals, "operating_margin")),
    }

    weighted_sum = 0.0
    used_weight = 0.0
    missing: list[str] = []
    for key, val in components.items():
        w = WEIGHTS[key]
        if val is None:
            missing.append(key)
            continue
        weighted_sum += val * w
        used_weight += w

    if used_weight == 0:
        # Total black-box — kein Score moeglich. Neutral 0.5 wuerde luegen, 0.0
        # waere ungerecht. Wir signalisieren das via score=0.5 + missing=alle.
        score = 0.5
    else:
        # Renormalisierung: durch das vorhandene Gewicht teilen, damit der Score
        # auf [0,1] bleibt unabhaengig davon wieviel Daten wir hatten.
        score = weighted_sum / used_weight

    # Cap defensively
    score = max(0.0, min(1.0, score))
    return ConvictionResult(
        score=score,
        components=components,
        used_weight=used_weight,
        missing=missing,
    )


def _g(d, key):
    """dict/Series/Row tolerant getter, NaN -> None."""
    if d is None:
        return None
    try:
        v = d[key]
    except (KeyError, TypeError, IndexError):
        try:
            v = getattr(d, key, None)
        except Exception:  # noqa: BLE001
            return None
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:   # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


# ---------- Format-Helper fuer notes ----------

def format_conviction_notes(result: ConvictionResult, max_components: int = 3) -> str:
    """Kompakter Notes-String fuer system_recommendations.notes.

    z.B. 'conviction=0.78 prof=0.85 solid=0.90 val=0.70 missing=margin'
    """
    parts = [f"conviction={result.score:.2f}"]
    abbrev = {"profitability": "prof", "solidity": "solid", "valuation": "val",
              "growth": "grow", "margin": "marg"}
    shown = 0
    for k, v in result.components.items():
        if v is None or shown >= max_components:
            continue
        parts.append(f"{abbrev[k]}={v:.2f}")
        shown += 1
    if result.missing:
        parts.append(f"missing={','.join(abbrev.get(m, m) for m in result.missing)}")
    return " ".join(parts)
