"""FRED-API-Client. Minimal-Wrapper um requests.

API-Docs: https://fred.stlouisfed.org/docs/api/fred/
Auth:     API-Key (kostenlos registrierbar), via NOVA_FRED_API_KEY env-var.
Rate-Limit: 120 requests/min — fuer 6 series-fetches kein Thema.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Iterator

import requests


FRED_BASE = "https://api.stlouisfed.org/fred"


class FredApiError(RuntimeError):
    """API-Aufruf fehlgeschlagen (HTTP-Fehler, Fehler-JSON, etc.)."""


@dataclass(frozen=True)
class Observation:
    ts:    date
    value: float


def _api_key() -> str:
    key = os.environ.get("NOVA_FRED_API_KEY", "").strip()
    if not key:
        raise FredApiError(
            "NOVA_FRED_API_KEY nicht gesetzt. "
            "Registrierung: https://fred.stlouisfed.org/docs/api/api_key.html"
        )
    return key


def fetch_observations(
    series_id: str,
    *,
    since: date | None = None,
    sleep_s: float = 0.4,
) -> list[Observation]:
    """Hole Time-Series fuer eine FRED-Series.

    since=None liefert die volle Historie (Stefans 5y-Default sollte vom
    Caller via since=today-5y kommen).
    """
    params: dict[str, str] = {
        "series_id":            series_id,
        "api_key":              _api_key(),
        "file_type":            "json",
        "observation_start":    (since.isoformat() if since else "1900-01-01"),
        "sort_order":           "asc",
    }
    url = f"{FRED_BASE}/series/observations"
    try:
        resp = requests.get(url, params=params, timeout=20)
    except requests.RequestException as e:
        raise FredApiError(f"FRED-Request failed: {e}") from e
    if resp.status_code != 200:
        raise FredApiError(
            f"FRED returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    payload = resp.json()
    if "error_code" in payload:
        raise FredApiError(
            f"FRED-API error {payload['error_code']}: {payload.get('error_message')}"
        )

    out: list[Observation] = []
    for r in payload.get("observations", []):
        raw_v = r.get("value", "")
        if raw_v == "." or raw_v == "":     # FRED-Sentinel fuer "kein Wert"
            continue
        try:
            v = float(raw_v)
            ts = date.fromisoformat(r["date"])
        except (KeyError, ValueError):
            continue
        out.append(Observation(ts=ts, value=v))

    # Rate-Limit-Schoner — nur relevant bei fetch-all (mehrere Series sequentiell)
    time.sleep(sleep_s)
    return out


def fetch_series_metadata(series_id: str) -> dict[str, str]:
    """FRED-Metadaten fuer add-series-Befehl (Name/Title/Units/Frequency)."""
    params: dict[str, str] = {
        "series_id":  series_id,
        "api_key":    _api_key(),
        "file_type":  "json",
    }
    url = f"{FRED_BASE}/series"
    try:
        resp = requests.get(url, params=params, timeout=10)
    except requests.RequestException as e:
        raise FredApiError(f"FRED metadata-Request failed: {e}") from e
    if resp.status_code != 200:
        raise FredApiError(
            f"FRED returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    payload = resp.json()
    series_list = payload.get("seriess", [])
    if not series_list:
        raise FredApiError(f"Series '{series_id}' not found in FRED.")
    s = series_list[0]
    return {
        "title":             s.get("title", series_id),
        "units":             s.get("units", ""),
        "frequency":         s.get("frequency", ""),
        "frequency_short":   s.get("frequency_short", ""),
        "notes":             s.get("notes", "")[:500] if s.get("notes") else "",
    }
