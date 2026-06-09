"""GuruFocus-Provider — Client + Adapter, fuer die Dashboard-Datenschicht.

Duenne Convenience-Schicht: holt die Roh-Endpoints (Client) und mappt sie auf
nova-Strukturen (Adapter). Defensiv — GuruFocusError wird durchgereicht, der
Aufrufer faengt ab (z.B. Fallback auf yfinance).
"""

from __future__ import annotations

import os

from . import adapter
from .client import GuruFocusClient


def available() -> bool:
    """True, wenn ein GuruFocus-Token konfiguriert ist."""
    return bool(os.environ.get("GURUFOCUS_TOKEN") or os.environ.get("GF_TOKEN"))


def fundamentals(ticker: str) -> tuple[dict, dict]:
    """(f, med) — KPI-Werte + Industrie-Median (summary + keyratios)."""
    c = GuruFocusClient()
    try:
        return adapter.kpi_snapshot(c.summary(ticker), c.keyratios(ticker))
    finally:
        c.close()


def quality(ticker: str) -> dict:
    """GuruFocus-Qualitaets-Snapshot (GF-Score/Value/Raenge) je Wert."""
    c = GuruFocusClient()
    try:
        return adapter.quality_snapshot(c.summary(ticker))
    finally:
        c.close()


def metric_rows(ticker: str, n_years: int | None = None,
                *, quarterly: bool = False) -> list[dict]:
    """Mehrjahres-Metriken (year_metrics-Shape) aus financials (annual/quarterly)."""
    c = GuruFocusClient()
    try:
        return adapter.metric_rows(c.financials(ticker), n_years,
                                   quarterly=quarterly)
    finally:
        c.close()
