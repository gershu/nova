"""CSP-Screener Scoring + Filter Logic.

Pure-Python, keine externen Dependencies. Testbar isoliert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class CSPCandidate:
    """Eine konkrete (Underlying, Strike, Expiration) Kombi mit Quote-Daten."""
    ref_instrument_id:  str
    symbol:             str
    spot:               float          # Underlying-Preis (Last-Close)
    expiration:         date
    days_to_expiration: int
    strike:             float
    bid:                float
    ask:                float | None
    last:               float | None
    volume:             int | None
    iv:                 float | None
    currency:           str
    next_earnings_date: date | None = None    # naechstes earnings >= today, None wenn nicht bekannt

    @property
    def crosses_earnings(self) -> bool:
        """True wenn earnings-Datum innerhalb der Option-Laufzeit liegt
        (also vor Expiration)."""
        if not self.next_earnings_date:
            return False
        return self.expiration >= self.next_earnings_date

    @property
    def annualized_yield_pct(self) -> float:
        """(bid * 100 / strike) * (365 / DTE) * 100. Annualisierte Rendite
        auf Cash-Collateral, in %."""
        if self.strike <= 0 or self.days_to_expiration <= 0:
            return 0.0
        return (self.bid / self.strike) * (365.0 / self.days_to_expiration) * 100.0

    @property
    def buffer_pct(self) -> float:
        """Wieviel % unter Spot — positiv = OTM-Schutz."""
        if self.spot <= 0:
            return 0.0
        return (1.0 - self.strike / self.spot) * 100.0

    @property
    def spread_pct(self) -> float:
        """Bid/Ask-Spread relativ zum Mid, in %.
        100 wenn ask fehlt (signalisiert: nicht handelbar)."""
        if self.ask is None or self.bid is None or self.bid <= 0:
            return 100.0
        mid = (self.bid + self.ask) / 2.0
        if mid <= 0:
            return 100.0
        return ((self.ask - self.bid) / mid) * 100.0

    @property
    def cash_collateral(self) -> float:
        """Cash der bei Assignment gebraucht wird (USD/EUR), pro Kontrakt."""
        return self.strike * 100.0  # 1 Kontrakt = 100 Shares

    @property
    def premium_per_contract(self) -> float:
        """Premium-Einnahme pro 1 Kontrakt verkauft."""
        return self.bid * 100.0


@dataclass
class ScoreConfig:
    """Konfigurierbare Gewichtung des Score. Defaults daempfen High-IV-Dominanz.

    yield-curve: full-credit bis yield_full_credit_pct (default 20%), dann
    diminishing-returns (excess * yield_excess_factor). Ueber yield_penalty_above
    aktiver Penalty (suspiciously high yield = wahrscheinlich Earnings/Distress).

    buffer-bonus: linear * buffer_weight bis soft-cap.
    spread-penalty: linear ueber threshold."""

    # Yield-curve
    yield_full_credit_pct: float = 20.0
    yield_excess_factor:   float = 0.3
    yield_penalty_above:   float = 50.0

    # Buffer
    buffer_weight:         float = 0.5
    buffer_soft_cap_pct:   float = 15.0

    # Spread
    spread_threshold_pct:  float = 5.0
    spread_penalty_factor: float = 0.5


@dataclass
class ScoreComponents:
    """Score zerlegt in seine Komponenten — fuer Transparenz im CSV/Output."""
    yield_score:    float
    buffer_bonus:   float
    spread_penalty: float

    @property
    def total(self) -> float:
        return self.yield_score + self.buffer_bonus - self.spread_penalty


def score_components(c: CSPCandidate, scfg: ScoreConfig | None = None) -> ScoreComponents:
    """Berechne Score-Komponenten einzeln. Liefert ScoreComponents-Objekt
    mit yield_score, buffer_bonus, spread_penalty + total."""
    if scfg is None:
        scfg = ScoreConfig()

    y = c.annualized_yield_pct
    if y >= scfg.yield_penalty_above:
        # Suspiciously hohe Rendite — aktiver Penalty
        yield_score = scfg.yield_full_credit_pct - (y - scfg.yield_penalty_above)
    elif y > scfg.yield_full_credit_pct:
        # Diminishing returns ueber threshold
        yield_score = scfg.yield_full_credit_pct + (y - scfg.yield_full_credit_pct) * scfg.yield_excess_factor
    else:
        yield_score = y

    buffer_bonus = (
        min(c.buffer_pct, scfg.buffer_soft_cap_pct) * scfg.buffer_weight
        if c.buffer_pct > 0 else 0.0
    )

    spread_penalty = max(0.0, c.spread_pct - scfg.spread_threshold_pct) * scfg.spread_penalty_factor

    return ScoreComponents(
        yield_score=yield_score,
        buffer_bonus=buffer_bonus,
        spread_penalty=spread_penalty,
    )


def score_candidate(c: CSPCandidate, scfg: ScoreConfig | None = None) -> float:
    """Composite-Score. Hoeher = besser. Wrapper um score_components.total."""
    return score_components(c, scfg).total


@dataclass
class FilterConfig:
    min_dte:                int = 25
    max_dte:                int = 50
    buffer_min_pct:         float = 5.0
    buffer_max_pct:         float = 15.0
    min_annualized_yield:   float = 8.0
    max_spread_pct:         float = 25.0
    min_bid:                float = 0.05    # Mindestpremium fuer "echt"
    require_iv:             bool = False    # bei True: IV muss vorhanden sein
    avoid_earnings:         bool = True     # bei True: option-Laufzeit darf kein earnings ueberspannen


def passes_filter(c: CSPCandidate, cfg: FilterConfig) -> tuple[bool, str | None]:
    """Returns (passes, reject_reason). Reject-reason fuer Audit-Output."""
    if c.days_to_expiration < cfg.min_dte:
        return False, f"DTE {c.days_to_expiration} < {cfg.min_dte}"
    if c.days_to_expiration > cfg.max_dte:
        return False, f"DTE {c.days_to_expiration} > {cfg.max_dte}"
    if c.buffer_pct < cfg.buffer_min_pct:
        return False, f"buffer {c.buffer_pct:.1f}% < {cfg.buffer_min_pct}%"
    if c.buffer_pct > cfg.buffer_max_pct:
        return False, f"buffer {c.buffer_pct:.1f}% > {cfg.buffer_max_pct}%"
    if c.bid < cfg.min_bid:
        return False, f"bid {c.bid} < {cfg.min_bid}"
    if c.annualized_yield_pct < cfg.min_annualized_yield:
        return False, f"yield {c.annualized_yield_pct:.1f}% < {cfg.min_annualized_yield}%"
    if c.spread_pct > cfg.max_spread_pct:
        return False, f"spread {c.spread_pct:.1f}% > {cfg.max_spread_pct}%"
    if cfg.require_iv and c.iv is None:
        return False, "IV missing"
    if cfg.avoid_earnings and c.crosses_earnings:
        return False, f"earnings {c.next_earnings_date.isoformat() if c.next_earnings_date else '?'} crosses exp {c.expiration.isoformat()}"
    return True, None


def select_top(
    candidates: list[CSPCandidate],
    top_n_per_symbol: int = 1,
    top_n_overall: int = 20,
    scfg: ScoreConfig | None = None,
    score_key=None,
) -> list[CSPCandidate]:
    """Sort by score desc, take top-N pro symbol bis top-N overall erreicht.

    score_key (optional): callable c -> float. Erlaubt Caller einen externen
    Scoring-Multiplier (z.B. Value-Conviction) anzulegen. Default = score_candidate(c, scfg).
    """
    key_fn = score_key if score_key is not None else (lambda c: score_candidate(c, scfg))
    sorted_cands = sorted(candidates, key=key_fn, reverse=True)
    per_sym: dict[str, int] = {}
    out: list[CSPCandidate] = []
    for c in sorted_cands:
        if per_sym.get(c.symbol, 0) >= top_n_per_symbol:
            continue
        out.append(c)
        per_sym[c.symbol] = per_sym.get(c.symbol, 0) + 1
        if len(out) >= top_n_overall:
            break
    return out
