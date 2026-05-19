"""Top-Movers-Section (B-Phase): N staerkste Tagesgewinner und -verlierer.
Joint mkt_quotes_daily über ref_instrument_id mit ref_instruments für Display."""

from __future__ import annotations

from datetime import date

import duckdb


def render(con: duckdb.DuckDBPyConnection, source: str, ts: date, n: int = 3) -> str:
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT q.ref_instrument_id, q.ts, q.close,
                   ROW_NUMBER() OVER (PARTITION BY q.ref_instrument_id ORDER BY q.ts DESC) AS rn
            FROM mkt_quotes_daily q
            WHERE q.source = ? AND q.ts <= ?
        ),
        today AS    (SELECT ref_instrument_id, close AS close_today FROM ranked WHERE rn = 1),
        yesterday AS(SELECT ref_instrument_id, close AS close_prev  FROM ranked WHERE rn = 2)
        SELECT COALESCE(r.symbol, t.ref_instrument_id) AS display_symbol,
               t.close_today,
               y.close_prev,
               (t.close_today / y.close_prev - 1) * 100 AS d_day_pct
        FROM today t
        JOIN yesterday y USING (ref_instrument_id)
        LEFT JOIN ref_instruments r ON r.ref_instrument_id = t.ref_instrument_id
        WHERE y.close_prev > 0
        ORDER BY d_day_pct DESC
        """,
        [source, ts],
    ).fetchall()

    if not rows:
        return "## Top-Movers\n\n_Keine Daten fuer heute._"

    positives = [r for r in rows if r[3] > 0]
    negatives = [r for r in rows if r[3] < 0]
    ups = positives[:n]
    downs = list(reversed(negatives))[:n]

    lines = ["## Top-Movers", "", "**Top up:**"]
    if ups:
        for sym, close, _prev, dpct in ups:
            lines.append(f"- {sym} {dpct:+.2f}% (close {close:.2f})")
    else:
        lines.append("- _keine Gewinner heute_")
    lines.append("")
    lines.append("**Top down:**")
    if downs:
        for sym, close, _prev, dpct in downs:
            lines.append(f"- {sym} {dpct:+.2f}% (close {close:.2f})")
    else:
        lines.append("- _keine Verlierer heute_")
    return "\n".join(lines)
