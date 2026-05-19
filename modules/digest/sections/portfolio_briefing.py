"""Portfolio-Briefing Section: rendert sig_portfolio_briefings.

Wird OBEN im Digest gerendert (vor watchlist_status), damit der LLM-Brief
das erste ist was du beim Lesen siehst.
"""

from __future__ import annotations

from datetime import date

import duckdb


SENTIMENT_MARKERS = {
    "positive": "[+]",
    "negative": "[-]",
    "neutral":  "[=]",
}


def render(con: duckdb.DuckDBPyConnection, ts: date) -> str:
    """Returnt Markdown-Section. Nimmt das neueste Briefing fuer ts (egal welches Modell)."""
    try:
        row = con.execute(
            """
            SELECT headline, body, sentiment, confidence, model,
                   portfolio_total, delta_abs_day, delta_pct_day, base_currency,
                   alerts_count, holdings_count, generated_at
            FROM sig_portfolio_briefings
            WHERE ts = ?
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            [ts],
        ).fetchone()
    except duckdb.CatalogException:
        return ""

    if not row:
        return ""

    (headline, body, sentiment, confidence, model,
     total, delta_abs, delta_pct, base_ccy,
     alerts_count, holdings_count, generated_at) = row

    marker = SENTIMENT_MARKERS.get((sentiment or "").lower(), "")
    delta_s = f"{delta_abs:+,.2f} {base_ccy}  ({delta_pct:+.2f}%)" if delta_abs is not None and delta_pct is not None else "—"

    lines = [
        "## Tagesbriefing",
        "",
        f"**{headline or '(kein Headline)'}** {marker}",
        "",
    ]
    if body:
        lines.append(body)
        lines.append("")

    # Snapshot-Footer (klein, nuechtern)
    meta = []
    if total is not None:
        meta.append(f"Portfolio: {total:,.2f} {base_ccy}")
    if delta_abs is not None:
        meta.append(f"Tag: {delta_s}")
    meta.append(f"{holdings_count or 0} lots, {alerts_count or 0} alerts")
    conf_s = f"conf {confidence:.0%}" if confidence is not None else "conf —"
    meta.append(conf_s)
    meta.append(f"`{model}`")
    lines.append(f"_{ ' • '.join(meta) }_")

    return "\n".join(lines)
