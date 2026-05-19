"""Alert-Kontext-Section: LLM-Erklaerungen aus sig_alert_explanations.

Joint sig_alerts mit sig_alert_explanations + ref_instruments.
Wenn keine Erklaerungen vorhanden: leere Section (skipped vom main).
"""

from __future__ import annotations

from datetime import date

import duckdb


SENTIMENT_MARKERS = {
    "positive": "[+]",
    "negative": "[-]",
    "neutral":  "[=]",
}


def render(con: duckdb.DuckDBPyConnection, ts: date, min_confidence: float = 0.0) -> str:
    """Returnt Markdown-Section. min_confidence filtert Erklaerungen aus
    bei denen das Modell selbst zu unsicher war."""
    # Direkt aus sig_alert_explanations lesen — PK ist (ref_instrument_id,
    # rule_name, direction, ts), also genau 1 Row pro Alert-Bedingung.
    # Frueherer JOIN ueber sig_alerts produzierte 1:N-Duplikate weil sig_alerts
    # mehrere run_ids pro Bedingung haben kann (mehrere Monitor-Runs/Tag).
    try:
        rows = con.execute(
            """
            SELECT
                COALESCE(r.symbol, e.ref_instrument_id) AS display_symbol,
                e.rule_name,
                e.direction,
                e.explanation,
                e.sentiment,
                e.confidence,
                e.news_count,
                e.news_used,
                e.model
            FROM sig_alert_explanations e
            LEFT JOIN ref_instruments r ON r.ref_instrument_id = e.ref_instrument_id
            WHERE e.ts = ?
              AND COALESCE(e.confidence, 0) >= ?
            ORDER BY display_symbol, e.rule_name, e.direction
            """,
            [ts, min_confidence],
        ).fetchall()
    except duckdb.CatalogException:
        # sig_alert_explanations existiert noch nicht (alert_explainer nie gelaufen)
        return ""

    if not rows:
        return ""

    lines = ["## Alert-Kontext (LLM)"]
    lines.append(f"_Aus sig_alert_explanations, min confidence {min_confidence}._")
    lines.append("")

    current_sym = None
    for sym, rule, direction, explanation, sentiment, conf, n_count, n_used, model in rows:
        if sym != current_sym:
            lines.append(f"### {sym}")
            current_sym = sym

        marker = SENTIMENT_MARKERS.get((sentiment or "").lower(), "")
        rule_label = f"{rule}" + (f" ({direction})" if direction else "")
        meta = []
        if conf is not None:
            meta.append(f"conf {conf:.0%}")
        if n_count is not None:
            meta.append(f"news {n_used or 0}/{n_count}")
        meta_str = f"  _({', '.join(meta)})_" if meta else ""

        lines.append(f"- **{rule_label}** {marker}{meta_str}")
        if explanation:
            for para in explanation.split("\n"):
                if para.strip():
                    lines.append(f"  > {para.strip()}")

    return "\n".join(lines)
