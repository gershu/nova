"""Sell-Candidates-Section: rendert die 'sell_candidates' Portfolio-View
mit aktueller Bewertung jeder Position.

Quelle: list_portfolio_view_members WHERE view_id='sell_candidates'
        + pos_holdings (SCD-2, current rows) + mkt_quotes_daily.

Member-Identitaet seit SCD-2-Migration: (ref_instrument_id, broker).

Empty-Section (== None) wenn:
  - Schema portfolio_core nicht migriert
  - View existiert nicht
  - View hat keine Members (oder keine die aktuell gehalten werden)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import duckdb


VIEW_ID = "sell_candidates"


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()
    return row is not None


def render(con: duckdb.DuckDBPyConnection, ts: date | None = None) -> Optional[str]:
    """Returnt Markdown-Section oder None wenn nichts zu zeigen."""
    if not _table_exists(con, "list_portfolio_view_members"):
        return None

    # Join: view-member -> current holding -> instrument; aggregiert pro
    # Wertpapier (mehrere Broker-Positionen eines Symbols rollen zusammen).
    rows = con.execute("""
        WITH latest_spot AS (
            WITH ranked AS (
                SELECT ref_instrument_id, close, ts, source,
                       ROW_NUMBER() OVER (PARTITION BY ref_instrument_id
                                          ORDER BY ts DESC,
                                                   CASE source WHEN 'ib' THEN 1
                                                               WHEN 'yfinance' THEN 2 ELSE 9 END) AS rk
                FROM mkt_quotes_daily
            )
            SELECT ref_instrument_id, close FROM ranked WHERE rk = 1
        ),
        view_holdings AS (
            SELECT h.ref_instrument_id, h.quantity, h.cost_per_share,
                   m.notes
            FROM list_portfolio_view_members m
            JOIN pos_holdings h
              ON h.ref_instrument_id = m.ref_instrument_id
             AND h.broker            = m.broker
            WHERE m.view_id = ? AND h.valid_to IS NULL
        )
        SELECT vh.ref_instrument_id, i.symbol, i.name, i.currency,
               SUM(vh.quantity) AS qty,
               AVG(vh.cost_per_share) AS avg_cost,
               q.close AS spot,
               STRING_AGG(DISTINCT vh.notes, ' | ') AS notes
        FROM view_holdings vh
        LEFT JOIN ref_instruments i USING (ref_instrument_id)
        LEFT JOIN latest_spot q USING (ref_instrument_id)
        GROUP BY vh.ref_instrument_id, i.symbol, i.name, i.currency, q.close
        HAVING SUM(vh.quantity) > 0
        ORDER BY i.symbol
    """, [VIEW_ID]).fetchall()

    if not rows:
        return None   # View leer oder keine Members im Portfolio

    parts = ["", "## Sell Candidates", ""]
    parts.append(f"_{len(rows)} Position(en) in 'Sell Candidates' Sicht — bei "
                  f"opportunistischem Moment trimmen._")
    parts.append("")
    parts.append("| Symbol | CCY | Qty | Avg Cost | Spot | P&L % | Notes |")
    parts.append("|:---|:---|---:|---:|---:|---:|:---|")
    for ref_id, sym, name, ccy, qty, avg_cost, spot, notes in rows:
        pnl_pct = None
        if avg_cost and spot and avg_cost > 0:
            pnl_pct = (spot / avg_cost - 1.0) * 100.0
        sym_str = sym or ref_id
        qty_str = f"{qty:,.0f}" if qty else "—"
        cost_str = f"{avg_cost:,.2f}" if avg_cost else "—"
        spot_str = f"{spot:,.2f}" if spot else "—"
        pnl_str = f"{pnl_pct:+.2f}%" if pnl_pct is not None else "—"
        parts.append(f"| {sym_str} | {ccy or '?'} | {qty_str} | {cost_str} | "
                      f"{spot_str} | {pnl_str} | {notes or '—'} |")
    parts.append("")
    return "\n".join(parts)
