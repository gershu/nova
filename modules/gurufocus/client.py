"""GuruFocus-API-Client (Spike).

Duenner, defensiver Wrapper um die GuruFocus-REST-API. Ziel des Spikes: pruefen,
ob GuruFocus die Daten liefert, die nova heute aus sec-api/yfinance zieht
(Fundamental-Historie, Bewertungs-Ratios, GF-Value/Ranks, Guru-Portfolios,
Insider) — BEVOR ueber eine sec-api-Abloesung entschieden wird.

Auth: Token-in-Pfad (GuruFocus-Konvention):
    https://api.gurufocus.com/public/user/<TOKEN>/<endpoint>
Token via ENV (3-Tier-Konvention):
    GURUFOCUS_TOKEN  (oder Fallback GF_TOKEN)

Bewusst KEINE feste Schema-Annahme: die Methoden liefern das rohe JSON; die
Auswertung (Coverage) passiert im probe-CLI per rekursiver Schluesselsuche.
"""

from __future__ import annotations

import os

import requests

BASE = "https://api.gurufocus.com/public/user"


class GuruFocusError(RuntimeError):
    """Basis fuer GuruFocus-API-Fehler."""


def _token() -> str:
    tok = os.environ.get("GURUFOCUS_TOKEN") or os.environ.get("GF_TOKEN")
    if not tok:
        raise GuruFocusError(
            "Kein GURUFOCUS_TOKEN (oder GF_TOKEN) gesetzt — in ~/.nova_env "
            "hinterlegen.")
    return tok


class GuruFocusClient:
    """Sync-Client. timeout/retries konservativ; rohes JSON zurueck."""

    def __init__(self, token: str | None = None, timeout_s: int = 30) -> None:
        self.token = token or _token()
        self.timeout_s = timeout_s
        self._session = requests.Session()

    def _get(self, path: str) -> dict | list:
        url = f"{BASE}/{self.token}/{path.lstrip('/')}"
        try:
            r = self._session.get(url, timeout=self.timeout_s)
        except requests.RequestException as e:
            raise GuruFocusError(f"Request {path} fehlgeschlagen: {e}") from e
        if r.status_code == 401:
            raise GuruFocusError("HTTP 401 — Token ungueltig / kein API-Zugang.")
        if r.status_code == 429:
            raise GuruFocusError("HTTP 429 — Rate-Limit erreicht.")
        if r.status_code != 200:
            raise GuruFocusError(f"HTTP {r.status_code} ({path}): "
                                 f"{r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise GuruFocusError(f"Kein JSON ({path}): {r.text[:200]}") from e

    # ---- Endpoints (Pfade nach GuruFocus-API-Konvention; defensiv) ----
    def summary(self, symbol: str):
        return self._get(f"stock/{symbol}/summary")

    def keyratios(self, symbol: str):
        return self._get(f"stock/{symbol}/keyratios")

    def financials(self, symbol: str):
        return self._get(f"stock/{symbol}/financials")

    def quote(self, symbol: str):
        return self._get(f"stock/{symbol}/quote")

    def gurus(self, symbol: str):
        return self._get(f"stock/{symbol}/gurus")

    def insider(self, symbol: str):
        return self._get(f"stock/{symbol}/insider")

    def close(self) -> None:
        self._session.close()
