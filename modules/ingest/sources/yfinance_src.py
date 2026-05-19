"""yfinance-Adapter — kostenlose EOD-Quotes via Yahoo Finance.

Quoten-Limits: Yahoo hat soft rate-limits (IP-basiert). ~2000 Requests/Stunde
funktionieren typischerweise.

File-Name 'yfinance_src.py' (nicht 'yfinance.py') verhindert Import-Konflikt
mit dem yfinance-Package selbst.

Nimmt Instrument.symbol als yfinance-Ticker. Wenn das Instrument unter
preferred_source='IB' registriert ist, ist symbol = IB localSymbol (z.B. 'SAP'
ohne '.DE'-Suffix). yfinance findet das dann meistens nicht.
TODO: Alias-Tabelle ref_instrument_aliases (yfinance_ticker je instrument).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .base import DAILY_COLUMNS, FetchResult, Instrument, SourceAdapter


class YFinanceAdapter(SourceAdapter):
    name = "yfinance"

    def fetch_quotes_daily(
        self,
        instrument: Instrument,
        since: date,
        until: date,
    ) -> FetchResult:
        # Lazy-import: nicht beim Modul-Load, sondern erst beim ersten Fetch.
        # Erlaubt IB-only-Setups ohne yfinance-Dependency.
        try:
            import yfinance as yf
        except ImportError as e:
            return FetchResult(
                ref_instrument_id=instrument.ref_instrument_id,
                ok=False,
                rows=pd.DataFrame(columns=DAILY_COLUMNS),
                error=f"yfinance not installed: {e}",
            )

        try:
            df = yf.Ticker(instrument.symbol).history(
                start=since.isoformat(),
                end=(until + timedelta(days=1)).isoformat(),
                auto_adjust=False,
                actions=False,
            )
            if df.empty:
                return FetchResult(
                    ref_instrument_id=instrument.ref_instrument_id,
                    ok=True,
                    rows=pd.DataFrame(columns=DAILY_COLUMNS),
                    skipped=True,
                )

            df = df.reset_index().rename(
                columns={
                    "Date": "ts",
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Adj Close": "adj_close",
                    "Volume": "volume",
                }
            )
            df["ref_instrument_id"] = instrument.ref_instrument_id
            df["ts"] = pd.to_datetime(df["ts"]).dt.date
            df = df[DAILY_COLUMNS]

            return FetchResult(
                ref_instrument_id=instrument.ref_instrument_id,
                ok=True,
                rows=df,
            )

        except Exception as e:  # noqa: BLE001
            return FetchResult(
                ref_instrument_id=instrument.ref_instrument_id,
                ok=False,
                rows=pd.DataFrame(columns=DAILY_COLUMNS),
                error=f"{e.__class__.__name__}: {e}",
            )
