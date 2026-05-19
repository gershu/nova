"""Alerts-Section (B-Phase): alle Alerts vom heutigen Tag, gruppiert nach
Symbol. Liest sig_alerts, joint mit ref_instruments für Display-Symbol."""

from __future__ import annotations

from datetime import date

import duckdb


def render(con: duckdb.DuckDBPyConnection, ts: date) -> str:
    rows = con.execute(
        """
        SELECT DISTINCT
            COALESCE(r.symbol, a.ref_instrument_id) AS display_symbol,
            a.rule_name,
            a.direction,
            a.trigger_value,
            a.threshold
        FROM sig_alerts a
        LEFT JOIN ref_instruments r ON r.ref_instrument_id = a.ref_instrument_id
        WHERE a.ts = ?
        ORDER BY display_symbol, a.rule_name, a.direction, a.trigger_value
        """,
        [ts],
    ).fetchall()

    if not rows:
        return "## Alerts heute\n\n_Keine Alerts._"

    lines = ["## Alerts heute", ""]
    current_sym = None
    for sym, rule, direction, value, thresh in rows:
        if sym != current_sym:
            lines.append(f"### {sym}")
            current_sym = sym
        bits = [f"**{rule}**"]
        if direction:
            bits.append(direction)
        if value is not None:
            if rule == "daily_change_pct":
                bits.append(f"{value:+.2f}%")
            elif rule == "volume_spike":
                bits.append(f"{value:.2f}x")
            elif rule in ("52w_high", "52w_low"):
                bits.append(f"close {value:.2f}")
            else:
                bits.append(f"value={value}")
        if thresh is not None:
            bits.append(f"(threshold {thresh})")
        lines.append(f"- {' '.join(bits)}")
    return "\n".join(lines)
