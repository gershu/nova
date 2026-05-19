"""52-Wochen-Hoch/-Tief-Rule (B-Phase): Alert wenn close_today == max/min(close, last 252).

Params:
  window: int (Trading-Days, default 252 ~= 1 Jahr)
"""

from __future__ import annotations

import json
from datetime import date

import duckdb

from ...ingest.sources.base import Instrument
from .base import Alert, Rule


class HighLow52WRule(Rule):
    name = "high_low_52w"

    def evaluate(
        self,
        con: duckdb.DuckDBPyConnection,
        instrument: Instrument,
        ts: date,
        source: str,
    ) -> list[Alert]:
        window = int(self.params.get("window", 252))

        rows = con.execute(
            """
            WITH last_n AS (
                SELECT ts, close
                FROM mkt_quotes_daily
                WHERE ref_instrument_id = ? AND source = ? AND ts <= ?
                ORDER BY ts DESC
                LIMIT ?
            )
            SELECT
                (SELECT ts FROM last_n ORDER BY ts DESC LIMIT 1)    AS ts_today,
                (SELECT close FROM last_n ORDER BY ts DESC LIMIT 1) AS close_today,
                MAX(close) AS max_close,
                MIN(close) AS min_close,
                COUNT(*)   AS n
            FROM last_n
            """,
            [instrument.ref_instrument_id, source, ts, window],
        ).fetchone()

        if not rows or rows[4] < 20:
            return []
        ts_today, close_today, max_close, min_close, n = rows
        if close_today is None:
            return []

        alerts = []
        if close_today >= max_close:
            alerts.append(Alert(
                ref_instrument_id=instrument.ref_instrument_id,
                rule_name="52w_high",
                ts=ts_today,
                direction="up",
                trigger_value=float(close_today),
                threshold=None,
                details=json.dumps({
                    "window_days": window,
                    "n_obs":       n,
                    "symbol":      instrument.symbol,
                }),
            ))
        if close_today <= min_close:
            alerts.append(Alert(
                ref_instrument_id=instrument.ref_instrument_id,
                rule_name="52w_low",
                ts=ts_today,
                direction="down",
                trigger_value=float(close_today),
                threshold=None,
                details=json.dumps({
                    "window_days": window,
                    "n_obs":       n,
                    "symbol":      instrument.symbol,
                }),
            ))
        return alerts
