"""KPI-Cards (Metric-Tiles) fuer Dashboard-Pages."""

from __future__ import annotations

import streamlit as st


def kpi_row(items: list[dict]) -> None:
    """Rendert eine Zeile mit st.metric Tiles.

    items: liste von dicts mit keys:
        label   — Display-Text
        value   — formatierter String
        delta   — optional (z.B. '+1.2%')
        help    — optional Tooltip
    """
    if not items:
        return
    cols = st.columns(len(items))
    for col, item in zip(cols, items):
        with col:
            st.metric(
                label = item.get("label", ""),
                value = item.get("value", "—"),
                delta = item.get("delta"),
                help  = item.get("help"),
            )


def fmt_money(v, currency: str = "", places: int = 0) -> str:
    if v is None:
        return "—"
    try:
        if abs(v) >= 1_000_000_000:
            return f"{v/1e9:,.{places}f}B {currency}".strip()
        if abs(v) >= 1_000_000:
            return f"{v/1e6:,.{places}f}M {currency}".strip()
        return f"{v:,.{places}f} {currency}".strip()
    except (TypeError, ValueError):
        return str(v)


def fmt_pct(v, places: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{v*100:+.{places}f}%"
    except (TypeError, ValueError):
        return str(v)
