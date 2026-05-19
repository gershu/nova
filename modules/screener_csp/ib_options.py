"""IB Option-Chain Fetcher fuer den CSP-Screener.

Wiederverwendet das Connection-Pattern aus modules/portfolio/_ib_resolver.py,
aber mit eigener Client-ID (15) damit es nicht mit ingest (11), portfolio (12)
oder ib_check (7) kollidiert.

Konfiguration via gleichen ENV-Vars wie andere IB-Module:
  IB_GATEWAY_HOST   (default: 127.0.0.1)
  IB_GATEWAY_PORT   (default: 4001)
  IB_SCREENER_CLIENT_ID  (default: 15; fallback IB_CLIENT_ID)
  IB_MARKET_DATA_TYPE    (default: 2 = frozen, fuer EOD-Screening ausreichend)
  IB_REQUEST_TIMEOUT     (default: 30, etwas hoeher als sonst weil
                          option-chain-queries laenger dauern koennen)
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class OptionQuote:
    """Snapshot eines Option-Contracts."""
    symbol:     str
    expiration: str        # YYYYMMDD
    strike:     float
    right:      str        # 'P' fuer put, 'C' fuer call
    bid:        float | None
    ask:        float | None
    last:       float | None
    volume:     int | None
    open_int:   int | None
    iv:         float | None


class IBOptionsClient:
    """Fetcht Option-Chains und Snapshot-Quotes via IB.

    Nutzung:
        with IBOptionsClient() as ib:
            chain = ib.fetch_chain_params(symbol='AAPL', exchange='NASDAQ', currency='USD', con_id=265598)
            quotes = ib.fetch_put_quotes(chain.underlying, expirations, strikes, currency='USD')
    """

    def __init__(self) -> None:
        self.host = os.environ.get("IB_GATEWAY_HOST", "127.0.0.1")
        self.port = int(os.environ.get("IB_GATEWAY_PORT", 4001))
        cid_raw = os.environ.get("IB_SCREENER_CLIENT_ID") or os.environ.get("IB_CLIENT_ID") or "15"
        self.client_id = int(cid_raw)
        self.market_data_type = int(os.environ.get("IB_MARKET_DATA_TYPE", 2))
        self.timeout = int(os.environ.get("IB_REQUEST_TIMEOUT", 30))
        self._ib = None

    def __enter__(self) -> "IBOptionsClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self):
        try:
            from ib_async import IB
        except ImportError as e:
            raise RuntimeError("ib_async nicht installiert.") from e
        self._ib = IB()
        try:
            self._ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.timeout)
        except (TimeoutError, ConnectionError, OSError) as e:
            self._ib = None
            raise ConnectionError(
                f"IB connect failed: {e.__class__.__name__}: {e}\n"
                f"  config: host={self.host} port={self.port} client_id={self.client_id}\n"
                f"  hints:\n"
                f"    - Gateway laeuft? nc -zv {self.host} {self.port}\n"
                f"    - Stuck-Connection? IB_SCREENER_CLIENT_ID=<andere>"
            ) from e
        try:
            self._ib.reqMarketDataType(self.market_data_type)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            try:
                self._ib.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._ib = None

    # ---------- Resolution ----------

    def resolve_underlying(self, symbol: str, exchange: str | None, currency: str, con_id: int | None):
        """Returns qualified Contract or None."""
        from ib_async import Contract, Stock
        if con_id:
            c = Contract(conId=int(con_id), exchange="SMART")
        else:
            c = Stock(symbol, exchange or "SMART", currency)
        try:
            details = self._ib.reqContractDetails(c)
        except Exception:  # noqa: BLE001
            return None
        if not details:
            return None
        return details[0].contract

    def fetch_chain_params(self, underlying):
        """Returns (expirations_set, strikes_set, exchange) or None.
        Nutzt reqSecDefOptParams — billig, gibt verfuegbare Strikes/Expirations
        OHNE Quotes."""
        if underlying is None or not underlying.conId:
            return None
        try:
            chains = self._ib.reqSecDefOptParams(
                underlyingSymbol=underlying.symbol,
                futFopExchange="",
                underlyingSecType=underlying.secType,
                underlyingConId=underlying.conId,
            )
        except Exception:  # noqa: BLE001
            return None
        if not chains:
            return None
        # Pick chain mit meisten Strikes (typisch SMART-Aggregator)
        chain = max(chains, key=lambda c: len(c.strikes))
        return {
            "expirations": sorted(chain.expirations),
            "strikes":     sorted(chain.strikes),
            "exchange":    chain.exchange,
            "trading_class": chain.tradingClass,
        }

    # ---------- Quotes ----------

    def fetch_put_quotes(
        self,
        underlying,
        expirations: list[str],
        strikes: list[float],
        currency: str,
        chain_exchange: str = "SMART",
        trading_class: str | None = None,
        chunk_size: int = 30,
    ) -> list[OptionQuote]:
        """Holt bid/ask/iv/volume Snapshots fuer alle (exp, strike) Kombinationen.

        Chunked reqTickers — 30 Contracts pro Batch um Pacing-Limits zu schonen
        und noch praktikable Round-Trip-Zeiten zu haben.
        """
        from ib_async import Option

        # Bauen aller Option-Contracts (puts only)
        candidates = []
        for exp in expirations:
            for strike in strikes:
                opt = Option(
                    symbol=underlying.symbol,
                    lastTradeDateOrContractMonth=exp,
                    strike=float(strike),
                    right="P",
                    exchange=chain_exchange,
                    currency=currency,
                )
                if trading_class:
                    opt.tradingClass = trading_class
                candidates.append(opt)

        # Qualify in Chunks
        qualified = []
        for i in range(0, len(candidates), chunk_size):
            batch = candidates[i:i+chunk_size]
            try:
                q = self._ib.qualifyContracts(*batch)
                qualified.extend([c for c in q if c.conId])
            except Exception:  # noqa: BLE001
                continue

        if not qualified:
            return []

        # Snapshot in Chunks via reqTickers
        out: list[OptionQuote] = []
        for i in range(0, len(qualified), chunk_size):
            batch = qualified[i:i+chunk_size]
            try:
                tickers = self._ib.reqTickers(*batch, regulatorySnapshot=False)
            except Exception:  # noqa: BLE001
                continue
            for t in tickers:
                if t.contract is None:
                    continue
                # Bid/Ask/Last sind float; reqMktData gibt -1 oder None bei keinem Quote.
                bid = t.bid if t.bid and t.bid > 0 else None
                ask = t.ask if t.ask and t.ask > 0 else None
                last = t.last if t.last and t.last > 0 else None
                vol = int(t.volume) if t.volume and t.volume > 0 else None
                # impliedVolatility ist auf Ticker direkt (modelGreeks bei Greeks)
                iv = None
                if hasattr(t, "modelGreeks") and t.modelGreeks is not None:
                    mg = t.modelGreeks
                    iv = mg.impliedVol if hasattr(mg, "impliedVol") and mg.impliedVol and mg.impliedVol > 0 else None
                out.append(OptionQuote(
                    symbol=t.contract.symbol,
                    expiration=t.contract.lastTradeDateOrContractMonth,
                    strike=float(t.contract.strike),
                    right=t.contract.right,
                    bid=bid,
                    ask=ask,
                    last=last,
                    volume=vol,
                    open_int=None,  # IB liefert OI nicht standardmaessig; spaeter ergaenzbar
                    iv=iv,
                ))
        return out
