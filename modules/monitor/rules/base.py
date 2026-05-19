"""Base-Interface fuer monitor-Regeln (B-Phase).

Jede Regel implementiert evaluate(con, instrument, ts, source) und liefert
eine Liste von Alert-Records (kann leer sein wenn nichts triggert).
Berechnungen laufen idealerweise als DuckDB-SQL gegen mkt_quotes_daily.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

import duckdb

from ...ingest.sources.base import Instrument


@dataclass
class Alert:
    """Ein einzelner Alert. Wird von main.py in sig_alerts und CSV geschrieben."""

    ref_instrument_id: str
    rule_name:         str
    ts:                date
    direction:         str | None = None        # 'up', 'down', 'golden', 'death', None
    trigger_value:     float | None = None      # Mess-Wert der getriggert hat
    threshold:         float | None = None      # Schwellwert der Regel
    details:           str | None = None        # JSON-blob fuer regel-spezifische Felder


class Rule(ABC):
    """Interface fuer monitor-Regeln. Wird in __main__.py via Registry instantiiert."""

    name: str = "abstract"

    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def evaluate(
        self,
        con: duckdb.DuckDBPyConnection,
        instrument: Instrument,
        ts: date,
        source: str,
    ) -> list[Alert]:
        """Pruefe Regel fuer Instrument an Datum ts (i.d.R. heute).
        Returnt Liste von Alerts (leer wenn nichts triggert)."""
        ...
