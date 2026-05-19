"""CSP-Picks-Section: rendert system_recommendations-Watchlist-Eintraege
die vom screener_csp geschrieben wurden.

Quelle: list_watchlist_members WHERE watchlist_id='system_recommendations'
AND added_by='screener_csp'. Notes-Feld hat structured key=value Daten,
die hier geparst und in eine Markdown-Tabelle gerendert werden.

Beispiel notes:
  strike=390.00 exp=2026-06-05 dte=26 bid=3.60 ann_yield=13.0% buffer=6.1% (spot=415.12) next_earn=2026-08-01
"""

from __future__ import annotations

import re

import duckdb


SCREENER_TAG = "screener_csp"
SYSTEM_REC_WATCHLIST = "system_recommendations"


_PAIR_RE = re.compile(r"(\w+)=([^\s)]+)")


def _parse_notes(notes: str) -> dict[str, str]:
    """Parse Notes 'key=val key=val (spot=...)' format -> dict."""
    if not notes:
        return {}
    out: dict[str, str] = {}
    for m in _PAIR_RE.finditer(notes):
        key = m.group(1)
        val = m.group(2).rstrip(")")
        out[key] = val
    return out


def render(con: duckdb.DuckDBPyConnection, max_show: int = 15) -> str:
    """Returnt Markdown-Section. Leer wenn keine Empfehlungen vorhanden
    (Tabelle wird vom main.py geskipped wenn empty)."""
    try:
        rows = con.execute(
            """
            SELECT m.ref_instrument_id, r.symbol, r.name, r.currency,
                   m.notes, m.added_at
            FROM list_watchlist_members m
            LEFT JOIN ref_instruments r ON r.ref_instrument_id = m.ref_instrument_id
            WHERE m.watchlist_id = ? AND m.added_by = ?
            ORDER BY m.added_at DESC
            LIMIT ?
            """,
            [SYSTEM_REC_WATCHLIST, SCREENER_TAG, max_show],
        ).fetchall()
    except duckdb.CatalogException:
        return ""

    if not rows:
        return ""

    # Parse + sort by yield descending (sortierreihenfolge stabil)
    parsed_rows = []
    for rid, symbol, name, currency, notes, added_at in rows:
        kv = _parse_notes(notes or "")
        parsed_rows.append({
            "symbol":     symbol or rid,
            "currency":   currency or "",
            "strike":     kv.get("strike", "—"),
            "exp":        kv.get("exp", "—"),
            "dte":        kv.get("dte", "—"),
            "bid":        kv.get("bid", "—"),
            "yield":      kv.get("ann_yield", "—"),
            "buffer":     kv.get("buffer", "—"),
            "spot":       kv.get("spot", "—"),
            "earn":       kv.get("next_earn", "—"),
        })

    # Sort by yield desc (parse % from "13.0%" string)
    def yield_key(r):
        try:
            return float(r["yield"].rstrip("%"))
        except (ValueError, AttributeError):
            return -1.0
    parsed_rows.sort(key=yield_key, reverse=True)

    lines = [
        "## CSP-Empfehlungen (System-Screener)",
        "",
        f"_{len(parsed_rows)} Kandidat(en) aus screener_csp, sortiert nach annualisierter Rendite._",
        "",
        "| Symbol | Ccy | Strike | Exp | DTE | Bid | Yield p.a. | Buffer | Spot | Earnings |",
        "|--------|-----|--------|-----|-----|-----|------------|--------|------|----------|",
    ]
    for r in parsed_rows:
        lines.append(
            f"| {r['symbol']:<6} | {r['currency']:<3} | {r['strike']:>7} "
            f"| {r['exp']} | {r['dte']:>3} | {r['bid']:>5} "
            f"| {r['yield']:>7} | {r['buffer']:>6} | {r['spot']:>7} | {r['earn']:>10} |"
        )
    lines.append("")
    lines.append("_Daily-Refresh via `lab.screener_csp`. CSV-Detailansicht in `~/nova_output/lab_screener_csp/`._")

    return "\n".join(lines)
