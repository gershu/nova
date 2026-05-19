"""Adapter-Contract fuer Fundamentals-Sources.

Pattern (analog zu modules/ingest/sources/base.py): die CLI ruft `fetch()`,
der Adapter liefert ein gefuelltes Fundamentals-dataclass. Persistierung
macht die CLI — nicht der Adapter. Damit ist der Adapter testbar ohne DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Protocol, runtime_checkable


@dataclass
class Fundamentals:
    """Field-Set deckt sich mit ref_fundamentals_snapshot-Spalten.

    Optional[float]/Optional[str] ueberall — yfinance liefert oft Teilmengen,
    speziell fuer DACH-Mid-Caps. NULL-Toleranz ist Feature, nicht Bug.
    """

    ref_instrument_id: str
    source:            str
    ts:                str               # ISO YYYY-MM-DD

    # Identity / Classification
    sector:             Optional[str]    = None
    industry:           Optional[str]    = None
    country:            Optional[str]    = None
    employees:          Optional[int]    = None
    market_cap:         Optional[float]  = None
    enterprise_value:   Optional[float]  = None
    shares_outstanding: Optional[float]  = None

    # Valuation
    pe_ttm:        Optional[float] = None
    pe_forward:    Optional[float] = None
    pb:            Optional[float] = None
    ps_ttm:        Optional[float] = None
    p_fcf:         Optional[float] = None
    ev_ebitda:     Optional[float] = None
    ev_sales:      Optional[float] = None
    peg_ratio:     Optional[float] = None

    # Quality / Profitability
    roe:              Optional[float] = None
    roa:              Optional[float] = None
    roic:             Optional[float] = None
    gross_margin:     Optional[float] = None
    operating_margin: Optional[float] = None
    net_margin:       Optional[float] = None
    fcf_margin:       Optional[float] = None

    # Solidity / Balance-Sheet
    debt_to_equity:     Optional[float] = None
    net_debt_to_ebitda: Optional[float] = None
    current_ratio:      Optional[float] = None
    quick_ratio:        Optional[float] = None
    interest_coverage:  Optional[float] = None

    # Cashflow + Dividends
    fcf_yield:          Optional[float] = None
    dividend_yield:     Optional[float] = None
    payout_ratio:       Optional[float] = None
    dividend_per_share: Optional[float] = None

    # Growth (5y CAGRs)
    revenue_cagr_5y:  Optional[float] = None
    eps_cagr_5y:      Optional[float] = None
    fcf_cagr_5y:      Optional[float] = None
    dividend_cagr_5y: Optional[float] = None

    # Audit
    payload_json: Optional[str] = None    # adapter-spezifisch
    run_id:       Optional[str] = None

    # Adapter-Notes — Sammelplatz fuer Warnings ("market_cap missing", etc.).
    # Werden NICHT persistiert, nur fuer CLI-Output.
    notes: list[str] = field(default_factory=list)

    def to_db_dict(self) -> dict:
        """Felder die in ref_fundamentals_snapshot landen.

        Excludes 'notes' (CLI-only) und konvertiert ts -> Python date wenn Caller will.
        """
        d = asdict(self)
        d.pop("notes", None)
        return d

    def filled_count(self) -> int:
        """Wieviele numerische Metriken sind populiert? Coverage-Heuristik."""
        numeric_fields = [
            "market_cap", "enterprise_value", "shares_outstanding",
            "pe_ttm", "pe_forward", "pb", "ps_ttm", "p_fcf",
            "ev_ebitda", "ev_sales", "peg_ratio",
            "roe", "roa", "roic",
            "gross_margin", "operating_margin", "net_margin", "fcf_margin",
            "debt_to_equity", "net_debt_to_ebitda", "current_ratio",
            "quick_ratio", "interest_coverage",
            "fcf_yield", "dividend_yield", "payout_ratio", "dividend_per_share",
            "revenue_cagr_5y", "eps_cagr_5y", "fcf_cagr_5y", "dividend_cagr_5y",
        ]
        return sum(1 for f in numeric_fields if getattr(self, f) is not None)


class NotConfigured(RuntimeError):
    """Raised vom IB-Adapter wenn die Reuters-Subscription nicht aktiv ist."""


@runtime_checkable
class FundamentalsAdapter(Protocol):
    """Pflicht-Interface fuer Source-Adapter.

    Implementer:
      - modules/fundamentals/yf_adapter.py  (yfinance)
      - modules/fundamentals/ib_adapter.py  (IB — stub bis Subscription kommt)
    """

    name: str   # Identifier fuer 'source'-Spalte: 'yfinance' / 'ib' / ...

    def fetch(
        self,
        ref_instrument_id: str,
        symbol: str,
        currency: str,
        run_id: Optional[str] = None,
    ) -> Fundamentals:
        """Holt + berechnet Fundamentals fuer ein Instrument.

        Raises:
            NotConfigured: Source nicht nutzbar (Subscription fehlt, etc.).
            ValueError:    Symbol nicht aufloesbar bei der Source.
        """
        ...
