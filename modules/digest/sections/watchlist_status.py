"""Watchlist-Status Section (B-Phase): Tabelle pro Symbol mit Close, Δ Day,
Δ Week, Volumen vs SMA(30). Joint mkt_quotes_daily über ref_instrument_id
mit ref_instruments für Display-Symbol."""

from __future__ import annotations

from datetime import date

import duckdb


def render(con: duckdb.DuckDBPyConnection, source: str, ts: date, ref_instrument_ids: list[str]) -> str:
    if not ref_instrument_ids:
        return "## Watchlist-Status\n\n_Keine aktiven Instrumente._"

    placeholders = ",".join(["?"] * len(ref_instrument_ids))
    rows = con.execute(
        f"""
        WITH ranked AS (
            SELECT q.ref_instrument_id, q.ts, q.close, q.volume,
                   ROW_NUMBER() OVER (PARTITION BY q.ref_instrument_id ORDER BY q.ts DESC) AS rn
            FROM mkt_quotes_daily q
            WHERE q.source = ? AND q.ref_instrument_id IN ({placeholders}) AND q.ts <= ?
        ),
        today AS    (SELECT ref_instrument_id, ts, close, volume FROM ranked WHERE rn = 1),
        yesterday AS(SELECT ref_instrument_id, close FROM ranked WHERE rn = 2),
        week AS     (SELECT ref_instrument_id, close FROM ranked WHERE rn = 6),
        avg30 AS    (SELECT ref_instrument_id, AVG(volume) AS avg_vol FROM ranked WHERE rn BETWEEN 2 AND 31 GROUP BY ref_instrument_id)
        SELECT r.symbol,
               t.close,
               CASE WHEN y.close IS NULL OR y.close = 0 THEN NULL ELSE (t.close / y.close - 1) * 100 END AS d_day_pct,
               CASE WHEN w.close IS NULL OR w.close = 0 THEN NULL ELSE (t.close / w.close - 1) * 100 END AS d_week_pct,
               t.volume,
               a.avg_vol,
               CASE WHEN a.avg_vol IS NULL OR a.avg_vol = 0 THEN NULL ELSE t.volume / a.avg_vol END AS vol_ratio
        FROM today t
        LEFT JOIN yesterday y USING (ref_instrument_id)
        LEFT JOIN week      w USING (ref_instrument_id)
        LEFT JOIN avg30     a USING (ref_instrument_id)
        LEFT JOIN ref_instruments r ON r.ref_instrument_id = t.ref_instrument_id
        ORDER BY r.symbol
        """,
        [source, *ref_instrument_ids, ts],
    ).fetchall()

    lines = [
        "## Watchlist-Status",
        "",
        "| Symbol | Close   | Δ Day   | Δ Week  | Vol vs SMA(30) |",
        "|--------|---------|---------|---------|-----------------|",
    ]
    for r in rows:
        sym, close, d_day, d_week, _vol, _avg, vol_ratio = r
        sym_s = (sym or "—")[:8]
        close_s = f"{close:.2f}" if close is not None else "—"
        d_day_s = f"{d_day:+.2f}%" if d_day is not None else "—"
        d_week_s = f"{d_week:+.2f}%" if d_week is not None else "—"
        vol_s = f"{vol_ratio:.2f}x" if vol_ratio is not None else "—"
        lines.append(f"| {sym_s:<6} | {close_s:>7} | {d_day_s:>7} | {d_week_s:>7} | {vol_s:>15} |")
    return "\n".join(lines)
