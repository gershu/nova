"""Volume-Anomalies-Section (B-Phase): Symbole mit volume > threshold * SMA(volume, lookback).

Unabhaengig von der monitor-Regel — gibt auch Symbole zurueck, die kein
Alert getriggert haben (z.B. wenn ratio knapp unter Alert-Schwellwert liegt)."""

from __future__ import annotations

from datetime import date

import duckdb


def render(
    con: duckdb.DuckDBPyConnection,
    source: str,
    ts: date,
    threshold: float = 1.5,
    lookback: int = 30,
) -> str:
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT q.ref_instrument_id, q.ts, q.volume,
                   ROW_NUMBER() OVER (PARTITION BY q.ref_instrument_id ORDER BY q.ts DESC) AS rn
            FROM mkt_quotes_daily q
            WHERE q.source = ? AND q.ts <= ?
        ),
        today AS (SELECT ref_instrument_id, volume AS vol_today FROM ranked WHERE rn = 1),
        avg AS   (SELECT ref_instrument_id, AVG(volume) AS avg_vol
                  FROM ranked WHERE rn BETWEEN 2 AND ?
                  GROUP BY ref_instrument_id)
        SELECT COALESCE(r.symbol, t.ref_instrument_id) AS display_symbol,
               t.vol_today,
               a.avg_vol,
               t.vol_today / a.avg_vol AS ratio
        FROM today t
        JOIN avg a USING (ref_instrument_id)
        LEFT JOIN ref_instruments r ON r.ref_instrument_id = t.ref_instrument_id
        WHERE a.avg_vol > 0 AND t.vol_today / a.avg_vol >= ?
        ORDER BY ratio DESC
        """,
        [source, ts, lookback + 1, threshold],
    ).fetchall()

    if not rows:
        return f"## Volume-Auffaelligkeiten (≥{threshold}x SMA{lookback})\n\n_Keine._"

    lines = [f"## Volume-Auffaelligkeiten (≥{threshold}x SMA{lookback})", ""]
    for sym, vol, avg_vol, ratio in rows:
        lines.append(f"- **{sym}** {ratio:.2f}x  (volume {int(vol):,} vs avg {int(avg_vol):,})")
    return "\n".join(lines)
