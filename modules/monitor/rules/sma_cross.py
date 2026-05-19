"""SMA-Cross-Rule (B-Phase): Alert wenn SMA(short) den SMA(long) heute kreuzt.

Golden Cross: gestern SMA_short < SMA_long, heute SMA_short > SMA_long  (bullish)
Death Cross : gestern SMA_short > SMA_long, heute SMA_short < SMA_long  (bearish)

Params:
  short: int (default 10)
  long:  int (default 50)
"""

from __future__ import annotations

import json
from datetime import date

import duckdb

from ...ingest.sources.base import Instrument
from .base import Alert, Rule


class SmaCrossRule(Rule):
    name = "sma_cross"

    def evaluate(
        self,
        con: duckdb.DuckDBPyConnection,
        instrument: Instrument,
        ts: date,
        source: str,
    ) -> list[Alert]:
        short_n = int(self.params.get("short", 10))
        long_n = int(self.params.get("long", 50))
        need = long_n + 1

        result = con.execute(
            """
            WITH last_n AS (
                SELECT ts, close,
                       ROW_NUMBER() OVER (ORDER BY ts DESC) AS rn_desc
                FROM mkt_quotes_daily
                WHERE ref_instrument_id = ? AND source = ? AND ts <= ?
                ORDER BY ts DESC
                LIMIT ?
            )
            SELECT
                ts,
                close,
                AVG(close) OVER (ORDER BY ts ROWS BETWEEN ? PRECEDING AND CURRENT ROW) AS sma_short,
                AVG(close) OVER (ORDER BY ts ROWS BETWEEN ? PRECEDING AND CURRENT ROW) AS sma_long
            FROM (SELECT ts, close FROM last_n ORDER BY ts ASC)
            ORDER BY ts DESC
            LIMIT 2
            """,
            [instrument.ref_instrument_id, source, ts, need, short_n - 1, long_n - 1],
        ).fetchall()

        if len(result) < 2:
            return []

        ts_today, close_today, sma_s_today, sma_l_today = result[0]
        _, _, sma_s_prev, sma_l_prev = result[1]

        if None in (sma_s_today, sma_l_today, sma_s_prev, sma_l_prev):
            return []

        if sma_s_prev <= sma_l_prev and sma_s_today > sma_l_today:
            return [Alert(
                ref_instrument_id=instrument.ref_instrument_id,
                rule_name=self.name,
                ts=ts_today,
                direction="golden",
                trigger_value=round(sma_s_today - sma_l_today, 4),
                threshold=None,
                details=json.dumps({
                    "short": short_n, "long": long_n,
                    "sma_short_today": round(sma_s_today, 4),
                    "sma_long_today":  round(sma_l_today, 4),
                    "close_today":     float(close_today),
                    "symbol":          instrument.symbol,
                }),
            )]
        if sma_s_prev >= sma_l_prev and sma_s_today < sma_l_today:
            return [Alert(
                ref_instrument_id=instrument.ref_instrument_id,
                rule_name=self.name,
                ts=ts_today,
                direction="death",
                trigger_value=round(sma_s_today - sma_l_today, 4),
                threshold=None,
                details=json.dumps({
                    "short": short_n, "long": long_n,
                    "sma_short_today": round(sma_s_today, 4),
                    "sma_long_today":  round(sma_l_today, 4),
                    "close_today":     float(close_today),
                    "symbol":          instrument.symbol,
                }),
            )]
        return []
