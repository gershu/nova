"""Shared IB-connection + contract-resolution helpers fuer Portfolio-Module.

Wird genutzt von:
  - import_xlsx.py    (resolve ConID -> ContractDetails beim Import)
  - resolve_conids.py (Migration alter Excel-Format -> ConID-Format)

Konfiguration via gleichen ENV-Vars wie ingest/sources/ib_src.py:
  IB_GATEWAY_HOST, IB_GATEWAY_PORT, IB_REQUEST_TIMEOUT
  IB_PORTFOLIO_CLIENT_ID (default 12) — disjunkt von:
    - 7  ib_check
    - 11 nova-lab ingest
    - 20 csp_scanner
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# IB secType -> internal asset_type
SECTYPE_MAP = {
    "STK":    "stock",
    "ETF":    "etf",
    "BOND":   "bond",
    "OPT":    "option",
    "FUT":    "future",
    "FOP":    "future_option",
    "IND":    "index",
    "CASH":   "fx",
    "CRYPTO": "crypto",
    "FUND":   "fund",
    "WAR":    "warrant",
    "BAG":    "combo",
    "CMDTY":  "commodity",
    "CFD":    "cfd",
}

# Yahoo-style -> IB-Exchange (von ib_src.py uebernommen)
EXCHANGE_MAP = {
    "XETRA":     "IBIS",
    "FWB":       "FWB",
    "FRA":       "FWB",
    "FRANKFURT": "FWB",
}


@dataclass
class ResolvedContract:
    """Resultat einer Contract-Resolution. Felder = was wir in DB schreiben."""
    con_id:     int
    symbol:     str        # localSymbol — fuer yfinance-Fallback geeignet
    exchange:   str        # primaryExchange (oder exchange falls primary leer)
    currency:   str
    asset_type: str        # 'stock', 'etf', etc.
    name:       str | None
    isin:       str | None  # aus secIdList wenn IB liefert


class IBResolver:
    """Verwaltet eine kurzlebige IB-Connection fuer Contract-Resolution.

    Nutzung:
        with IBResolver() as r:
            details = r.resolve_by_conid(265598)
            ...
    """

    def __init__(self) -> None:
        # Default 127.0.0.1: Gateway laeuft auf nova-hub, dieser Code laeuft
        # ebenfalls auf nova-hub (portfolio-Module sind hub-only). Cross-host
        # via ENV-Override (IB_GATEWAY_HOST=nova-hub.local fuer Worker).
        self.host = os.environ.get("IB_GATEWAY_HOST", "127.0.0.1")
        self.port = int(os.environ.get("IB_GATEWAY_PORT", 4001))
        cid_raw = (
            os.environ.get("IB_PORTFOLIO_CLIENT_ID")
            or os.environ.get("IB_CLIENT_ID")
            or "12"
        )
        self.client_id = int(cid_raw)
        self.timeout = int(os.environ.get("IB_REQUEST_TIMEOUT", 15))
        self._ib = None

    def __enter__(self) -> "IBResolver":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self):
        try:
            from ib_async import IB
        except ImportError as e:
            raise RuntimeError("ib_async nicht installiert.") from e
        self._ib = IB()
        try:
            self._ib.connect(
                self.host, self.port, clientId=self.client_id, timeout=self.timeout
            )
        except (TimeoutError, ConnectionError, OSError) as e:
            self._ib = None
            raise ConnectionError(
                f"IB connect failed: {e.__class__.__name__}: {e}\n"
                f"  config: host={self.host} port={self.port} "
                f"client_id={self.client_id} timeout={self.timeout}s\n"
                f"  hints:\n"
                f"    - Gateway laeuft? nc -zv {self.host} {self.port}\n"
                f"    - Stuck-Connection auf clientId={self.client_id}? "
                f"Override: IB_PORTFOLIO_CLIENT_ID=<andere> oder IB_CLIENT_ID=<andere>\n"
                f"    - Master-API-Client-ID in Gateway -> API -> Settings auf 0?\n"
                f"    - Bei Verdacht auf Stuck: Gateway neu starten (re-Approve auf Mobile)"
            ) from e
        return self._ib

    def close(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            try:
                self._ib.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._ib = None

    # ---------- Resolution ----------

    @staticmethod
    def _asset_type_from_details(details) -> str:
        sec_type = (details.contract.secType or "").upper()
        base = SECTYPE_MAP.get(sec_type)
        if base is None:
            # Unknown secType — log to stderr for diagnosis statt blind durchreichen.
            import sys
            print(
                f"[WARN] _ib_resolver: unbekannter IB secType={sec_type!r} fuer "
                f"con_id={details.contract.conId} symbol={details.contract.localSymbol!r}"
                f" — fallback to 'stock'",
                file=sys.stderr,
            )
            base = "stock"
        if base == "stock":
            stock_type = (getattr(details, "stockType", "") or "").upper()
            if stock_type == "ETF":
                return "etf"
        return base

    @staticmethod
    def _isin_from_details(details) -> str | None:
        # ContractDetails.secIdList ist Liste von TagValue (tag, value)
        sec_id_list = getattr(details, "secIdList", None) or []
        for tv in sec_id_list:
            tag = getattr(tv, "tag", "")
            if tag.upper() == "ISIN":
                return getattr(tv, "value", None)
        return None

    def _details_to_resolved(self, details) -> ResolvedContract:
        c = details.contract
        primary = c.primaryExchange or c.exchange or ""
        return ResolvedContract(
            con_id=int(c.conId),
            symbol=c.localSymbol or c.symbol,
            exchange=primary,
            currency=c.currency or "",
            asset_type=self._asset_type_from_details(details),
            name=getattr(details, "longName", None) or None,
            isin=self._isin_from_details(details),
        )

    def resolve_by_conid(
        self,
        con_id: int,
        sec_type: str | None = None,
        currency: str | None = None,
    ) -> ResolvedContract | None:
        """Resolve via ConID. Returns None bei keinem Match.

        sec_type/currency sind optionale Hints aus dem Excel (asset_class +
        currency-Spalte). Fuer Bonds ist secType-Hint quasi Pflicht — IB liefert
        sonst manchmal keine Details. Stocks/ETFs gehen auch ohne Hint.
        """
        if self._ib is None:
            raise RuntimeError("IBResolver: not connected (call connect() oder mit context-manager).")
        from ib_async import Contract

        # Probier-Reihenfolge:
        #   1. Mit User-Hints (falls gegeben) — am praezisesten
        #   2. Ohne Hints (Stock/ETF Standard)
        #   3. Mit secType=BOND als Auto-Fallback (haeufige Spezialitaet)
        candidates = []
        if sec_type:
            kwargs = {"conId": int(con_id), "secType": sec_type.upper(), "exchange": "SMART"}
            if currency:
                kwargs["currency"] = currency.upper()
            candidates.append(Contract(**kwargs))

        candidates.append(Contract(conId=int(con_id), exchange="SMART"))

        if not sec_type:
            # Bond-Auto-Fallback fuer den Fall dass secType-Hint fehlt
            kwargs = {"conId": int(con_id), "secType": "BOND", "exchange": "SMART"}
            if currency:
                kwargs["currency"] = currency.upper()
            candidates.append(Contract(**kwargs))

        for c in candidates:
            try:
                details = self._ib.reqContractDetails(c)
                if details:
                    return self._details_to_resolved(details[0])
            except Exception:  # noqa: BLE001
                continue
        return None

    def resolve_by_symbol(
        self,
        symbol: str,
        exchange: str | None = None,
        currency: str | None = None,
        isin: str | None = None,
    ) -> ResolvedContract | None:
        """Legacy resolve via symbol+exchange+currency. Fuer Migration-Helper.

        Wenn ISIN angegeben + mehrere Treffer: filtert auf ISIN-Match.
        """
        if self._ib is None:
            raise RuntimeError("IBResolver: not connected.")
        from ib_async import Stock

        currency = currency or "USD"
        candidates = []
        if exchange:
            ib_exch = EXCHANGE_MAP.get(exchange.upper(), exchange.upper())
            candidates.append(Stock(symbol, ib_exch, currency))
            candidates.append(Stock(symbol, "SMART", currency, primaryExchange=ib_exch))
        candidates.append(Stock(symbol, "SMART", currency))

        for cand in candidates:
            try:
                details_list = self._ib.reqContractDetails(cand)
            except Exception:  # noqa: BLE001
                continue
            if not details_list:
                continue

            # Wenn ISIN angegeben, filter auf Match
            if isin and len(details_list) > 1:
                matched = [
                    d for d in details_list
                    if (self._isin_from_details(d) or "").upper() == isin.upper()
                ]
                if matched:
                    return self._details_to_resolved(matched[0])

            return self._details_to_resolved(details_list[0])

        return None
