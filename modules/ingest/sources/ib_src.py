"""IB-Adapter — EOD-Quotes via Interactive Brokers Gateway/TWS (B-Phase).

Konfiguration (3-Tier, ENV-Vars analog zu workloads/ib_check/ib_check.py):
  IB_GATEWAY_HOST       (default: nova-hub.local)
  IB_GATEWAY_PORT       (default: 4001)
  IB_INGEST_CLIENT_ID   (default: 11; fallback IB_CLIENT_ID; vermeidet Konflikt
                         mit ib_check=7, portfolio=12, csp_scanner=20)
  IB_MARKET_DATA_TYPE   (default: 2 = frozen — fuer EOD ausreichend)
  IB_REQUEST_TIMEOUT    (default: 15)

Connection-Lifecycle:
  __init__: parst Konfig, KEIN Connect.
  Erste fetch_quotes_daily: connect via ib_async, MarketDataType setzen.
  close(): disconnect (wird von main.py im finally-Block aufgerufen).

Contract-Resolution (asset-type-aware):
  Stocks/ETFs:  Contract(conId=N, exchange="SMART") — IB fuellt Rest aus
  Bonds:        Contract(conId=N, secType="BOND", exchange="SMART", currency=X)
  Pure-Symbol-Fallback wenn con_id fehlt (Stock-only).

Bar-Format (asset-type-aware via HISTDATA_SETTINGS):
  Stocks/ETFs:  whatToShow="TRADES",   useRTH=True
  Bonds/FX/CR:  whatToShow="MIDPOINT", useRTH=False (OTC-Markt, keine RTH)
  IB liefert kein adj_close — wir setzen adj_close = close.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime

import pandas as pd

from .base import DAILY_COLUMNS, FetchResult, Instrument, SourceAdapter


# Yahoo-style → IB-Exchange-Mapping
EXCHANGE_MAP = {
    "XETRA":     "IBIS",
    "FWB":       "FWB",
    "FRA":       "FWB",
    "FRANKFURT": "FWB",
}


# Pro asset_type: Settings fuer reqHistoricalData.
HISTDATA_SETTINGS: dict[str, tuple[str, bool]] = {
    "stock":   ("TRADES",   True),
    "etf":     ("TRADES",   True),
    "bond":    ("MIDPOINT", False),
    "fund":    ("TRADES",   True),
    "option":  ("TRADES",   False),
    "future":  ("TRADES",   False),
    "fx":      ("MIDPOINT", False),
    "crypto":  ("MIDPOINT", False),
    "index":   ("TRADES",   True),
    "warrant": ("TRADES",   True),
}


def _histdata_settings(asset_type: str | None) -> tuple[str, bool]:
    if not asset_type:
        return "TRADES", True
    return HISTDATA_SETTINGS.get(asset_type.lower(), ("TRADES", True))


class IBAdapter(SourceAdapter):
    name = "ib"

    def __init__(self) -> None:
        self.host = os.environ.get("IB_GATEWAY_HOST", "nova-hub.local")
        self.port = int(os.environ.get("IB_GATEWAY_PORT", 4001))
        cid_raw = os.environ.get("IB_INGEST_CLIENT_ID") or os.environ.get("IB_CLIENT_ID") or "11"
        self.client_id = int(cid_raw)
        self.market_data_type = int(os.environ.get("IB_MARKET_DATA_TYPE", 2))
        self.timeout = int(os.environ.get("IB_REQUEST_TIMEOUT", 15))

        self._ib = None

    # ---------- Connection / Lifecycle ----------

    def _ensure_connected(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            return
        try:
            from ib_async import IB
        except ImportError as e:
            raise RuntimeError("ib_async nicht installiert.") from e
        self._ib = IB()
        self._ib.connect(self.host, self.port, clientId=self.client_id, timeout=self.timeout)
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

    def health_check(self) -> tuple[bool, str]:
        try:
            self._ensure_connected()
            t = self._ib.reqCurrentTime()
            return True, f"connected, IB time {t}"
        except Exception as e:  # noqa: BLE001
            return False, f"{e.__class__.__name__}: {e}"

    # ---------- Contract Resolution ----------

    def _resolve_contract(self, instrument: Instrument):
        """Resolve Instrument zu IB Contract. None bei Fail.

        Fast-Path: instrument.con_id ist gesetzt (vom portfolio-Import gecached
        in ref_instruments.con_id).
        Fallback: pure-symbol-Resolve via Stock(symbol, exchange, currency) —
        nur fuer non-bonds sinnvoll.
        """
        from ib_async import Bond, Contract, Stock

        atype = (instrument.asset_type or "stock").lower()
        currency = instrument.currency or "USD"

        # Fast-Path via con_id
        if instrument.con_id:
            try:
                if atype == "bond":
                    c = Contract(
                        conId=instrument.con_id, secType="BOND",
                        exchange="SMART", currency=currency,
                    )
                else:
                    c = Contract(conId=instrument.con_id, exchange="SMART")
                details = self._ib.reqContractDetails(c)
                if details:
                    return details[0].contract
            except Exception:  # noqa: BLE001
                pass  # fall through to symbol-based resolve

        # Fallback ohne con_id: nur fuer non-bonds (Stock-Resolve)
        if atype == "bond":
            # Bonds ohne con_id sind kaum resolvbar
            return None

        candidates = []
        if instrument.exchange:
            ib_exch = EXCHANGE_MAP.get(instrument.exchange.upper(), instrument.exchange.upper())
            candidates.append(Stock(instrument.symbol, ib_exch, currency))
            candidates.append(
                Stock(instrument.symbol, "SMART", currency, primaryExchange=ib_exch)
            )
        candidates.append(Stock(instrument.symbol, "SMART", currency))

        for cand in candidates:
            try:
                details = self._ib.reqContractDetails(cand)
                if details:
                    return details[0].contract
            except Exception:  # noqa: BLE001
                continue
        return None

    # ---------- Fetch ----------

    @staticmethod
    def _duration_str(since: date, until: date) -> str:
        days = (until - since).days + 1
        if days <= 365:
            return f"{max(days, 1)} D"
        if days <= 365 * 5:
            return f"{(days // 7) + 1} W"
        return f"{(days // 365) + 1} Y"

    def fetch_quotes_daily(
        self,
        instrument: Instrument,
        since: date,
        until: date,
    ) -> FetchResult:
        """Defensiv-wrapped: jeder Fehler wird zu FetchResult(ok=False), damit
        ein einzelnes Symbol nicht den ganzen Run crashed."""
        try:
            return self._fetch_quotes_daily_inner(instrument, since, until)
        except Exception as e:  # noqa: BLE001
            import traceback
            tb_short = "".join(traceback.format_exception(e))[-400:]
            return FetchResult(
                ref_instrument_id=instrument.ref_instrument_id,
                ok=False,
                rows=pd.DataFrame(columns=DAILY_COLUMNS),
                error=f"unhandled in IB adapter: {e.__class__.__name__}: {e} | trace tail: {tb_short}",
            )

    def _fetch_quotes_daily_inner(
        self,
        instrument: Instrument,
        since: date,
        until: date,
    ) -> FetchResult:
        # Connect
        try:
            self._ensure_connected()
        except Exception as e:  # noqa: BLE001
            return FetchResult(
                ref_instrument_id=instrument.ref_instrument_id,
                ok=False,
                rows=pd.DataFrame(columns=DAILY_COLUMNS),
                error=f"IB connect failed: {e.__class__.__name__}: {e}",
            )

        # Contract
        try:
            contract = self._resolve_contract(instrument)
        except Exception as e:  # noqa: BLE001
            return FetchResult(
                ref_instrument_id=instrument.ref_instrument_id,
                ok=False,
                rows=pd.DataFrame(columns=DAILY_COLUMNS),
                error=f"contract resolution raised: {e.__class__.__name__}: {e}",
            )
        if contract is None:
            return FetchResult(
                ref_instrument_id=instrument.ref_instrument_id,
                ok=False,
                rows=pd.DataFrame(columns=DAILY_COLUMNS),
                error="contract not resolvable",
            )

        # Histdata-Settings nach asset_type
        what_to_show, use_rth = _histdata_settings(instrument.asset_type)

        try:
            end_dt = datetime.combine(until, datetime.min.time().replace(hour=23, minute=59, second=59))
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime=end_dt.strftime("%Y%m%d %H:%M:%S"),
                durationStr=self._duration_str(since, until),
                barSizeSetting="1 day",
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
            )
            time.sleep(0.1)  # Rate-Limit-Hygiene
        except Exception as e:  # noqa: BLE001
            return FetchResult(
                ref_instrument_id=instrument.ref_instrument_id,
                ok=False,
                rows=pd.DataFrame(columns=DAILY_COLUMNS),
                error=f"reqHistoricalData failed (whatToShow={what_to_show}, useRTH={use_rth}): {e.__class__.__name__}: {e}",
            )

        if not bars:
            return FetchResult(
                ref_instrument_id=instrument.ref_instrument_id,
                ok=True,
                rows=pd.DataFrame(columns=DAILY_COLUMNS),
                skipped=True,
            )

        rows = []
        for bar in bars:
            try:
                if isinstance(bar.date, datetime):
                    bar_date = bar.date.date()
                elif isinstance(bar.date, date):
                    bar_date = bar.date
                else:
                    bar_date = date.fromisoformat(str(bar.date))
            except Exception:  # noqa: BLE001
                continue
            if bar_date < since or bar_date > until:
                continue
            rows.append({
                "ref_instrument_id": instrument.ref_instrument_id,
                "ts":                bar_date,
                "open":              float(bar.open),
                "high":              float(bar.high),
                "low":                float(bar.low),
                "close":             float(bar.close),
                "adj_close":         float(bar.close),
                "volume":            int(bar.volume) if bar.volume else 0,
            })

        df = pd.DataFrame(rows, columns=DAILY_COLUMNS)
        if df.empty:
            return FetchResult(
                ref_instrument_id=instrument.ref_instrument_id,
                ok=True, rows=df, skipped=True,
            )
        return FetchResult(
            ref_instrument_id=instrument.ref_instrument_id,
            ok=True, rows=df,
        )
