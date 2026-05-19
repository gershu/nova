"""Volume-Spike-Rule (B-Phase): Alert wenn volume_today / SMA(volume, lookback) > threshold.

Params:
  lookback:  int (Tage, default 30)
  threshold: float (Vielfaches des Durchschnitts, default 2.0)
"""

from __future__ import annotations

import json
from datetime import date

import duckdb

from ...ingest.sources.base import Instrument
from .base import Alert, Rule


class VolumeSpikeRule(Rule):
    name = "volume_spike"

    def evaluate(
        self,
        con: duckdb.DuckDBPyConnection,
        instrument: Instrument,
        ts: date,
        source: str,
    ) -> list[Alert]:
        lookback = int(self.params.get("lookback", 30))
        threshold = float(self.params.get("threshold", 2.0))

        rows = con.execute(
            """
            WITH last_n AS (
                SELECT ts, volume
                FROM mkt_quotes_daily
                WHERE ref_instrument_id = ? AND source = ? AND ts <= ?
                ORDER BY ts DESC
                LIMIT ?
            )
            SELECT
                (SELECT ts FROM last_n ORDER BY ts DESC LIMIT 1)            AS ts_today,
                (SELECT volume FROM last_n ORDER BY ts DESC LIMIT 1)        AS vol_today,
                AVG(volume) FILTER (WHERE ts < (SELECT MAX(ts) FROM last_n)) AS avg_prev
            FROM last_n
            """,
            [instrument.ref_instrument_id, source, ts, lookback + 1],
        ).fetchone()

        if not rows:
            return []
        ts_today, vol_today, avg_prev = rows
        if not vol_today or not avg_prev or avg_prev == 0:
            return []

        ratio = float(vol_today) / float(avg_prev)
        if ratio < threshold:
            return []

        return [
            Alert(
                ref_instrument_id=instrument.ref_instrument_id,
                rule_name=self.name,
                ts=ts_today,
                direction="up",
                trigger_value=round(ratio, 4),
                threshold=threshold,
                details=json.dumps({
                    "volume_today":    int(vol_today),
                    "avg_volume_prev": round(float(avg_prev), 1),
                    "lookback":        lookback,
                    "symbol":          instrument.symbol,
                }),
            )
        ]
