"""yfinance-Adapter fuer Fundamentals.

Quellen:
    Ticker.info             — most ratios (market_cap, P/E, ROE, margins, ...)
    Ticker.financials       — Annual income-statement (Revenue, Net Income, EBITDA)
    Ticker.balance_sheet    — Annual balance-sheet (Debt, Equity, Cash, ...)
    Ticker.cashflow         — Annual cashflow (Op Cashflow, CapEx -> FCF)

Symbol-Mapping: ref_instruments.symbol ist meistens IB-localSymbol ohne
Suffix. yfinance braucht oft '.DE'/'.PA'/'.L' fuer non-US. Wir versuchen
2-3 Spellings; falls keines hits, returnen wir Fundamentals mit
`notes=['no yfinance match']` plus filled_count=0.

Defensiv: yfinance.Ticker.* kann KeyError/TypeError/HTTPError werfen wenn
Yahoo intern was umstellt. Jeder Section-Block ist getrennt try/except.
"""

from __future__ import annotations

import json
import warnings
from datetime import date
from typing import Optional

from .base import Fundamentals, FundamentalsAdapter


# Currency -> typische yfinance-Suffixe (in Priority-Order versuchen).
CCY_SUFFIX_HINTS: dict[str, list[str]] = {
    "EUR":  [".DE", ".PA", ".AS", ".MI", ".VI"],   # XETRA, Paris, Amsterdam, Milan, Vienna
    "GBP":  [".L"],
    "CHF":  [".SW"],
    "NOK":  [".OL"],
    "SEK":  [".ST"],
    "DKK":  [".CO"],
    "USD":  [],     # plain ticker
    "CAD":  [".TO"],
    "JPY":  [".T"],
    "HKD":  [".HK"],
}


def _candidate_tickers(symbol: str, currency: str) -> list[str]:
    """Probier-Reihenfolge: original, dann Dot/Dash-Variante (BRK.B -> BRK-B
    fuer S&P-Class-B-Shares), dann currency-passende Suffixe."""
    cands: list[str] = [symbol]
    # yfinance benutzt fuer S&P-Class-B Shares Dash statt Punkt (BRK-B, BF-B).
    # Wir versuchen die Dash-Variante als Fallback.
    if "." in symbol:
        dashed = symbol.replace(".", "-")
        if dashed not in cands:
            cands.append(dashed)
        # Symbol enthaelt schon Punkt -> Suffix-Probing skippen (waere doppelt).
        return cands
    for sfx in CCY_SUFFIX_HINTS.get(currency.upper(), []):
        cands.append(symbol + sfx)
    return cands


def _safe_float(v) -> Optional[float]:
    """yfinance gibt manchmal 'Infinity', 'N/A', None — alles zu None normalisieren."""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):   # NaN / inf
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> Optional[int]:
    f = _safe_float(v)
    return int(f) if f is not None else None


def _get_row(df, *candidates) -> Optional[list]:
    """Erste matching row im DataFrame zurueckgeben (yfinance benamt
    Felder mal 'Total Revenue', mal 'TotalRevenue' — try several)."""
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.index:
            return df.loc[c].tolist()
    return None


def _cagr(series: list, years: int) -> Optional[float]:
    """CAGR aus geordneter Liste [latest, year-1, year-2, ...].

    Defensive: erfordert series-Endpunkte gleichen Sign + nicht-null +
    genug Punkte. Bei 5y wollen wir mindestens index 5 (= 6 Datenpunkte).
    """
    if series is None or len(series) <= years:
        return None
    a, b = series[0], series[years]
    a, b = _safe_float(a), _safe_float(b)
    if a is None or b is None or a == 0 or b == 0:
        return None
    if (a > 0) != (b > 0):     # Vorzeichenwechsel -> CAGR nicht definiert
        return None
    try:
        return ((a / b) ** (1.0 / years)) - 1.0
    except (ValueError, ZeroDivisionError):
        return None


class YFinanceFundamentalsAdapter(FundamentalsAdapter):
    name = "yfinance"

    def fetch(
        self,
        ref_instrument_id: str,
        symbol: str,
        currency: str,
        run_id: Optional[str] = None,
    ) -> Fundamentals:
        try:
            import yfinance as yf
        except ImportError as e:
            raise RuntimeError("yfinance nicht installiert (pip install yfinance).") from e

        fund = Fundamentals(
            ref_instrument_id=ref_instrument_id,
            source=self.name,
            ts=date.today().isoformat(),
            run_id=run_id,
        )

        # 1) Ticker aufloesen — probiere mehrere Suffixe.
        ticker_obj = None
        used_ticker = None
        info = {}
        for cand in _candidate_tickers(symbol, currency):
            try:
                t = yf.Ticker(cand)
                # yfinance lazy-loaded: info erstmals zugreifen, dann sehen wir
                # ob's was zurueckgibt. Leerer dict / nur fehlende keys = miss.
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    i = t.info or {}
                if i.get("regularMarketPrice") is not None or i.get("marketCap") is not None:
                    ticker_obj = t
                    used_ticker = cand
                    info = i
                    break
            except Exception as e:  # noqa: BLE001
                fund.notes.append(f"candidate '{cand}' failed: {e.__class__.__name__}")
                continue

        if ticker_obj is None:
            fund.notes.append(f"no yfinance match for symbol='{symbol}' currency='{currency}'")
            return fund

        fund.notes.append(f"yf ticker: {used_ticker}")

        # 2) info -> Identity + most ratios.
        fund.sector              = info.get("sector")
        fund.industry            = info.get("industry")
        fund.country             = info.get("country")
        fund.employees           = _safe_int(info.get("fullTimeEmployees"))
        fund.market_cap          = _safe_float(info.get("marketCap"))
        fund.enterprise_value    = _safe_float(info.get("enterpriseValue"))
        fund.shares_outstanding  = _safe_float(info.get("sharesOutstanding"))

        fund.pe_ttm     = _safe_float(info.get("trailingPE"))
        fund.pe_forward = _safe_float(info.get("forwardPE"))
        fund.pb         = _safe_float(info.get("priceToBook"))
        fund.ps_ttm     = _safe_float(info.get("priceToSalesTrailing12Months"))
        fund.ev_ebitda  = _safe_float(info.get("enterpriseToEbitda"))
        fund.ev_sales   = _safe_float(info.get("enterpriseToRevenue"))
        fund.peg_ratio  = _safe_float(info.get("pegRatio") or info.get("trailingPegRatio"))

        fund.roe = _safe_float(info.get("returnOnEquity"))
        fund.roa = _safe_float(info.get("returnOnAssets"))
        # roic kommt nicht aus info — wird unten aus financials geschaetzt.

        fund.gross_margin     = _safe_float(info.get("grossMargins"))
        fund.operating_margin = _safe_float(info.get("operatingMargins"))
        fund.net_margin       = _safe_float(info.get("profitMargins"))

        fund.debt_to_equity = _safe_float(info.get("debtToEquity"))
        if fund.debt_to_equity is not None and fund.debt_to_equity > 5:
            # yfinance gibt das oft in % (z.B. 150 statt 1.5). Heuristik:
            # Werte > 5 sind unrealistisch als Ratio, also durch 100 teilen.
            fund.debt_to_equity = fund.debt_to_equity / 100.0
        fund.current_ratio  = _safe_float(info.get("currentRatio"))
        fund.quick_ratio    = _safe_float(info.get("quickRatio"))

        fund.dividend_yield     = _safe_float(info.get("dividendYield"))
        fund.payout_ratio       = _safe_float(info.get("payoutRatio"))
        fund.dividend_per_share = _safe_float(info.get("dividendRate"))

        # FCF + abgeleitete Metriken.
        op_cf  = _safe_float(info.get("operatingCashflow"))
        fcf    = _safe_float(info.get("freeCashflow"))
        if fcf is not None and fund.market_cap and fund.market_cap > 0:
            fund.fcf_yield = fcf / fund.market_cap
            fund.p_fcf     = fund.market_cap / fcf if fcf > 0 else None

        # 3) Annual financials / balance-sheet / cashflow — fuer CAGR + derived.
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fin = ticker_obj.financials      # income statement
                bs  = ticker_obj.balance_sheet
                cf  = ticker_obj.cashflow
        except Exception as e:  # noqa: BLE001
            fund.notes.append(f"financials fetch failed: {e.__class__.__name__}: {e}")
            fin = bs = cf = None

        # Revenue + EBITDA + Net Income time-series (latest first)
        revenue = _get_row(fin, "Total Revenue", "TotalRevenue", "Revenue")
        netinc  = _get_row(fin, "Net Income", "NetIncome", "Net Income Common Stockholders")
        ebitda  = _get_row(fin, "EBITDA", "Normalized EBITDA")
        opincome = _get_row(fin, "Operating Income", "OperatingIncome")
        intexp  = _get_row(fin, "Interest Expense", "InterestExpense")

        # Balance-Sheet items (latest)
        total_debt   = _get_row(bs, "Total Debt", "TotalDebt", "Long Term Debt")
        cash         = _get_row(bs, "Cash And Cash Equivalents", "CashAndCashEquivalents", "Cash")
        total_equity = _get_row(bs, "Stockholders Equity", "Total Stockholder Equity", "TotalStockholderEquity")
        invested_capital = _get_row(bs, "Invested Capital", "InvestedCapital")

        # Cashflow items
        op_cf_ts  = _get_row(cf, "Operating Cash Flow", "Total Cash From Operating Activities")
        capex_ts  = _get_row(cf, "Capital Expenditure", "CapitalExpenditures")

        # net_margin fallback aus financials wenn info leer
        if fund.net_margin is None and revenue and netinc and revenue[0] not in (None, 0):
            fund.net_margin = _safe_float(netinc[0]) / _safe_float(revenue[0]) if _safe_float(revenue[0]) else None

        # fcf_margin
        if revenue and op_cf_ts and capex_ts:
            try:
                fcf_latest = _safe_float(op_cf_ts[0]) + _safe_float(capex_ts[0])  # capex ist negativ in yf
                rev_latest = _safe_float(revenue[0])
                if fcf_latest is not None and rev_latest and rev_latest > 0:
                    fund.fcf_margin = fcf_latest / rev_latest
                # Update fcf_yield falls info leer war
                if fund.fcf_yield is None and fund.market_cap and fund.market_cap > 0 and fcf_latest is not None:
                    fund.fcf_yield = fcf_latest / fund.market_cap
                    if fcf_latest > 0:
                        fund.p_fcf = fund.market_cap / fcf_latest
            except (TypeError, ValueError):
                pass

        # roic (approx): operating_income * (1-tax) / (debt + equity).
        # Wir nehmen 0.25 als pauschalen tax-rate-Fallback — grob genug fuer Screening.
        try:
            if opincome and total_debt and total_equity:
                op_inc = _safe_float(opincome[0])
                debt   = _safe_float(total_debt[0]) or 0.0
                eq     = _safe_float(total_equity[0]) or 0.0
                if op_inc is not None and (debt + eq) > 0:
                    fund.roic = op_inc * 0.75 / (debt + eq)
        except (TypeError, ValueError, IndexError):
            pass

        # net_debt_to_ebitda
        try:
            if total_debt and ebitda:
                debt = _safe_float(total_debt[0]) or 0.0
                cash_v = _safe_float(cash[0]) if cash else 0.0
                ebitda_v = _safe_float(ebitda[0])
                if ebitda_v and ebitda_v != 0:
                    fund.net_debt_to_ebitda = (debt - (cash_v or 0)) / ebitda_v
        except (TypeError, ValueError, IndexError):
            pass

        # interest_coverage
        try:
            if opincome and intexp:
                op = _safe_float(opincome[0])
                ie = _safe_float(intexp[0])
                if op is not None and ie and ie != 0:
                    fund.interest_coverage = op / abs(ie)
        except (TypeError, ValueError, IndexError):
            pass

        # 4) CAGRs (5y) — wenn weniger als 6 Datenpunkte da, return None
        fund.revenue_cagr_5y = _cagr(revenue, 5)
        # eps_cagr — yfinance hat keine direkte EPS-Reihe; aus net_income / shares
        if netinc and fund.shares_outstanding:
            eps_series = [(_safe_float(n) / fund.shares_outstanding) if _safe_float(n) is not None else None
                          for n in netinc]
            fund.eps_cagr_5y = _cagr(eps_series, 5)
        if op_cf_ts and capex_ts and len(op_cf_ts) == len(capex_ts):
            fcf_series = []
            for o, c in zip(op_cf_ts, capex_ts):
                of, cf_v = _safe_float(o), _safe_float(c)
                if of is None or cf_v is None:
                    fcf_series.append(None)
                else:
                    fcf_series.append(of + cf_v)
            fund.fcf_cagr_5y = _cagr(fcf_series, 5)

        # dividend_cagr — Ticker.dividends ist Time-Series, aber von dividend per share
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                divs = ticker_obj.dividends
            if divs is not None and len(divs) >= 6:
                # Sum per Jahr, dann CAGR auf annual.
                annual = divs.groupby(divs.index.year).sum().sort_index(ascending=False)
                if len(annual) > 5:
                    fund.dividend_cagr_5y = _cagr(annual.tolist(), 5)
        except Exception:  # noqa: BLE001
            pass

        # 5) Raw payload — gestripped, key fields only damit DB nicht explodiert.
        try:
            keep = {k: info.get(k) for k in (
                "shortName", "longName", "sector", "industry", "country",
                "marketCap", "enterpriseValue", "sharesOutstanding",
                "trailingPE", "forwardPE", "priceToBook", "priceToSalesTrailing12Months",
                "enterpriseToEbitda", "enterpriseToRevenue", "pegRatio",
                "returnOnEquity", "returnOnAssets",
                "grossMargins", "operatingMargins", "profitMargins",
                "debtToEquity", "currentRatio", "quickRatio",
                "dividendYield", "payoutRatio", "dividendRate",
                "freeCashflow", "operatingCashflow",
                "fullTimeEmployees", "regularMarketPrice", "currency",
            ) if info.get(k) is not None}
            keep["_used_ticker"] = used_ticker
            fund.payload_json = json.dumps(keep, default=str)
        except Exception:  # noqa: BLE001
            fund.payload_json = None

        return fund
