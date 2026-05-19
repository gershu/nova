"""Base-Interface fuer Ingest-Sources (B-Phase).

Adapter empfangen ein `Instrument`-Objekt mit allem was sie zum Fetchen
brauchen — kein eigener DB-Lookup mehr im Adapter. Caller (modules/ingest/
__main__.py) baut die Instrument-Liste aus `ref_instruments` und reicht sie
durch.

DataFrames die Adapter zurueckgeben sind getaggt mit `ref_instrument_id`
(VARCHAR-PK aus '{SOURCE}:{SYMBOL}:{CURRENCY}'), nicht mit `symbol`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

import pandas as pd

# DataFrame-Schema das alle Adapter zurueckliefern muessen
DAILY_COLUMNS = ["ref_instrument_id", "ts", "open", "high", "low", "close", "adj_close", "volume"]


@dataclass(frozen=True)
class Instrument:
    """Was ein Adapter braucht um Quotes fuer ein Instrument zu holen.

    ref_instrument_id ist der stabile Identifier (siehe portfolio.import_xlsx
    .make_ref_instrument_id). Format: '{SOURCE}:{SYMBOL}:{CURRENCY}'.
    """
    ref_instrument_id: str
    symbol:            str           # IB localSymbol (oder yfinance ticker je nach Source)
    currency:          str
    asset_type:        str | None = None    # 'stock', 'etf', 'bond', ...
    con_id:            int | None = None    # IB Contract ID (cached)
    exchange:          str | None = None    # IB primaryExchange


@dataclass
class FetchResult:
    """Was eine Source pro Instrument zurueckliefert."""

    ref_instrument_id: str
    ok:                bool
    rows:              pd.DataFrame   # Schema: DAILY_COLUMNS, leer wenn skipped/failed
    error:             str | None = None
    skipped:           bool = False


class SourceAdapter(ABC):
    """Interface fuer jede Daten-Source. Wird in modules/ingest/__main__.py
    via Registry instantiiert."""

    name: str = "abstract"          # 'yfinance', 'ib' — landet in mkt_quotes_daily.source

    @abstractmethod
    def fetch_quotes_daily(
        self,
        instrument: Instrument,
        since: date,
        until: date,
    ) -> FetchResult:
        """Hole Daily-Quotes fuer ein Instrument im Date-Range [since, until] inkl."""
        ...

    def health_check(self) -> tuple[bool, str]:
        """Optional: pruefe ob Source erreichbar ist (Quota, API-Key, etc.).
        Default: ok. Adapter kann ueberschreiben."""
        return True, "ok"

    def close(self) -> None:
        """Optional: lifecycle-cleanup (Connection schliessen). Default no-op."""
        return None

    def bind_db(self, con) -> None:
        """Optional: existierende DuckDB-Connection vom Caller. Default no-op.
        Adapter koennen das nutzen wenn sie zusaetzliche Lookups brauchen
        (z.B. Conid-Cache). Vermeidet Connection-Config-Kollision in DuckDB."""
        return None
