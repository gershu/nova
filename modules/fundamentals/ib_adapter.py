"""IB-Fundamentals-Adapter — STUB.

Pfad 3 (Hybrid yfinance/IB): dieser Adapter existiert als Skeleton fuer
den Tag an dem Du eine "Reuters Worldwide Fundamentals"-Subscription
aktivierst (IB Client-Portal -> Settings -> Market Data Subscriptions).

Heutiger Stand: probe_ib.py meldete Error 10358 ("Fundamentals data is
not allowed") fuer AAPL — d.h. KEINE aktive Sub. fetch() raises
NotConfigured. yf_adapter ist alleinige active Source.

Was beim Aktivieren noetig waere:
  1. Subscription buchen (kostet ~$11/Monat, evtl. trading-volume-gated frei).
  2. Diese fetch()-Implementierung mit echtem reqFundamentalData + XML-Parser
     fuellen. ReportSnapshot enthaelt die meisten ratios, ReportsFinSummary
     liefert Quartals-history fuer CAGR-Berechnung.
  3. CLI-Flag `--source ib` aktivieren (default bleibt yfinance), oder
     auto-fallback: try IB first, dann yfinance.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from .base import Fundamentals, FundamentalsAdapter, NotConfigured


class IBFundamentalsAdapter(FundamentalsAdapter):
    name = "ib"

    # Wird vom CLI gesetzt — heute hart auf False, weil keine Sub. Sobald
    # die Sub aktiv ist + fetch() implementiert -> auf True umstellen, dann
    # entscheidet die CLI selbststaendig ob IB als primary genutzt werden kann.
    available: bool = False

    def fetch(
        self,
        ref_instrument_id: str,
        symbol: str,
        currency: str,
        run_id: Optional[str] = None,
    ) -> Fundamentals:
        # Heutiger Stand: nicht implementiert. Damit der Aufrufer entscheiden
        # kann (skippen vs. yfinance-fallback), raisen wir eine *spezifische*
        # Exception statt NotImplementedError.
        raise NotConfigured(
            "IB-Fundamentals nicht verfuegbar. Status:\n"
            "  - Reuters Worldwide Fundamentals Subscription NICHT aktiv\n"
            "    (probe_ib.py meldete Error 10358 / 'Fundamentals data is not allowed').\n"
            "  - Sub aktivieren: IB Client-Portal -> Settings -> Market Data Subscriptions\n"
            "    -> 'Reuters Worldwide Fundamentals' (~$11/Monat).\n"
            "  - Nach Aktivierung: in modules/fundamentals/ib_adapter.py die fetch()-\n"
            "    Methode mit echter Implementierung (reqFundamentalData + XML-Parser)\n"
            "    fuellen, dann available=True setzen.\n"
            f"  Bezogen auf: ref_instrument_id={ref_instrument_id} symbol={symbol}"
        )

    # Skeleton fuer die spaetere Implementierung. Bleibt auskommentiert bis
    # Sub kommt — verhindert Versuche aus Versehen einen leeren Pfad zu laufen.
    #
    # def _connect(self):
    #     from ib_async import IB, Stock
    #     ib = IB()
    #     ib.connect(host="127.0.0.1", port=4001, clientId=int(os.environ.get(
    #         "IB_FUNDAMENTALS_CLIENT_ID", "25")), timeout=15)
    #     return ib
    #
    # def _resolve_contract(self, ib, symbol, currency, exchange_hint=None):
    #     # Reuse modules.portfolio._ib_resolver.IBResolver pattern.
    #     ...
    #
    # def _parse_snapshot_xml(self, xml: str) -> dict:
    #     # ReportSnapshot enthaelt e.g. /ReportSnapshot/Ratios/Group@ID="Profitability"
    #     # mit RatioGroup-Elementen je metric.
    #     ...
