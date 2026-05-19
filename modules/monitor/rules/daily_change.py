"""Daily-Change-Rule (B-Phase): Alert wenn |close_today / close_yesterday - 1| > threshold.

Params:
  threshold: float in % (z.B. 5.0 fuer +/-5%)
"""

from __future__ import annotations

import json
from datetime import date

import duckdb

from ...ingest.sources.base import Instrument
from .base import Alert, Rule


class DailyChangePctRule(Rule):
    name = "daily_change_pct"

    def evaluate(
        self,
        con: duckdb.DuckDBPyConnection,
        instrument: Instrument,
        ts: date,
        source: str,
    ) -> list[Alert]:
        threshold = float(self.params.get("threshold", 5.0))

        rows = con.execute(
            """
            SELECT ts, close
            FROM mkt_quotes_daily
            WHERE ref_instrument_id = ? AND source = ? AND ts <= ?
            ORDER BY ts DESC
            LIMIT 2
            """,
            [instrument.ref_instrument_id, source, ts],
        ).fetchall()

        if len(rows) < 2:
            return []

        ts_today, close_today = rows[0]
        _, close_prev = rows[1]
        if close_prev is None or close_prev == 0:
            return []

        change_pct = (close_today / close_prev - 1.0) * 100.0
        if abs(change_pct) < threshold:
            return []

        return [
            Alert(
                ref_instrument_id=instrument.ref_instrument_id,
                rule_name=self.name,
                ts=ts_today,
                direction="up" if change_pct > 0 else "down",
                trigger_value=round(change_pct, 4),
                threshold=threshold,
                details=json.dumps({
                    "close_today": close_today,
                    "close_prev":  close_prev,
                    "symbol":      instrument.symbol,
                }),
            )
        ]
