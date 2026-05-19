"""probe_ib — Feasibility-Check fuer IB-Fundamentals-Daten.

Zweck: VORAB pruefen welche der 5 Reuters-Fundamentals-Reports unsere
IB-Account-Subscription liefert — und in welcher Qualitaet pro Markt
(US-Large-Cap vs. DACH-Mid-Cap vs. ETF). Macht KEINE DB-writes, schreibt
weder ref_fundamentals_* noch sonstwas — pure Read-Probe.

Standard IB-Report-Types (Reuters Worldwide Fundamentals):
  ReportSnapshot       — Company snapshot (description, ratios, ownership)
  ReportsFinSummary    — Financial summary, ~12 Quartale rolling
  ReportsFinStatements — Full statements (income/balance/cashflow, 10y)
  RESC                 — Analyst consensus estimates
  CalendarReport       — Earnings calendar

Subscription-Hint: in IB Account-Management unter Settings -> Account ->
Market Data Subscriptions sollte "Reuters Worldwide Fundamentals" stehen.
Bei Pro/Active-Accounts oft schon enthalten. Ohne Sub bekommt man
Error-Code 430 ("fundamentals data is not available for this contract").

Aufruf:
    python -m modules.fundamentals.probe_ib                  # Default-Test-Set
    python -m modules.fundamentals.probe_ib --symbols AAPL,DBK
    python -m modules.fundamentals.probe_ib --xml-out /tmp/  # XML-Samples raus
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from dataclasses import dataclass, field
from typing import Optional


# Default-Probe-Set: bewusster Mix damit wir die Coverage-Lueckenstruktur sehen.
# US-Large (oft 100% coverage), DACH-Blue (oft 80%), DACH-Mid (typische Luecke),
# ETF (meist 0% — Fundamentals sind Equity-only).
DEFAULT_SYMBOLS = [
    # symbol, exchange,  currency, expectation
    ("AAPL",  "NASDAQ",  "USD",    "US large-cap — sollte voll funktionieren"),
    ("MSFT",  "NASDAQ",  "USD",    "US large-cap"),
    ("SAP",   "IBIS",    "EUR",    "DACH large-cap (XETRA)"),
    ("DBK",   "IBIS",    "EUR",    "DACH bank — manchmal limitiert"),
    ("VOW3",  "IBIS",    "EUR",    "DACH mid-cap — Coverage-Test"),
    ("VWRL",  "AEB",     "EUR",    "ETF — i.d.R. KEINE Fundamentals"),
]

REPORT_TYPES = [
    ("ReportSnapshot",       "Company snapshot"),
    ("ReportsFinSummary",    "Financial summary (~12 quarters)"),
    ("ReportsFinStatements", "Full statements (10y)"),
    ("RESC",                 "Analyst estimates"),
    ("CalendarReport",       "Earnings calendar"),
]


@dataclass
class ProbeRow:
    symbol:      str
    exchange:    str
    currency:    str
    expectation: str
    contract_ok: bool = False
    error_qual:  Optional[str] = None
    results:     dict[str, dict] = field(default_factory=dict)  # report_type -> {ok, size, err, snippet}


def _connect():
    """Eigene IB-Connection, Client-ID disjoint von ingest(11)/portfolio(12)/screener(15)/csp_scanner(20).

    25 = fundamentals_probe (one-off).
    """
    try:
        from ib_async import IB
    except ImportError as e:
        raise RuntimeError("ib_async nicht installiert. `pip install ib_async`.") from e

    host = os.environ.get("IB_GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("IB_GATEWAY_PORT", 4001))
    cid  = int(os.environ.get("IB_FUNDAMENTALS_CLIENT_ID", "25"))
    timeout = int(os.environ.get("IB_REQUEST_TIMEOUT", 15))

    ib = IB()
    try:
        ib.connect(host, port, clientId=cid, timeout=timeout)
    except (TimeoutError, ConnectionError, OSError) as e:
        raise ConnectionError(
            f"IB connect failed: {e.__class__.__name__}: {e}\n"
            f"  host={host} port={port} clientId={cid} timeout={timeout}s\n"
            f"  Quick checks:  nc -zv {host} {port}   |   curl -s {host}:{port}"
        ) from e
    return ib


def _qualify(ib, symbol: str, exchange: str, currency: str):
    """Try a few Stock-spelling-variants to get the most-specific contract."""
    from ib_async import Stock
    candidates = [
        Stock(symbol, "SMART", currency, primaryExchange=exchange),
        Stock(symbol, exchange, currency),
        Stock(symbol, "SMART", currency),
    ]
    for c in candidates:
        try:
            details = ib.reqContractDetails(c)
            if details:
                return details[0].contract, None
        except Exception as e:  # noqa: BLE001
            last_err = f"{e.__class__.__name__}: {e}"
            continue
    return None, f"no contract details (tried {len(candidates)} variants)"


def _fetch_report(ib, contract, report_type: str, timeout_s: int = 12) -> tuple[Optional[str], Optional[str]]:
    """reqFundamentalData mit Timeout. Returns (xml_or_None, error_or_None)."""
    try:
        # ib_async.reqFundamentalData ist synchron mit eigenem Timeout-Verhalten;
        # wir wrappen mit util.run() oder direkt asyncio falls noetig.
        # ib_async: `ib.reqFundamentalData(contract, report_type)` returnt str (XML).
        start = time.time()
        xml = ib.reqFundamentalData(contract, report_type)
        elapsed = time.time() - start
        if xml is None or (isinstance(xml, str) and not xml.strip()):
            return None, f"empty response (after {elapsed:.1f}s)"
        return xml, None
    except Exception as e:  # noqa: BLE001
        return None, f"{e.__class__.__name__}: {e}"


def _xml_snippet(xml: str, max_len: int = 200) -> str:
    """First non-trivial bytes — gives sense of structure ohne kompletten dump."""
    s = xml.strip()
    s = s.replace("\n", " ").replace("\r", "")
    return s[:max_len] + ("…" if len(s) > max_len else "")


def run_probe(symbols: list[tuple], xml_out: Optional[pathlib.Path] = None,
              report_timeout_s: int = 12) -> list[ProbeRow]:
    rows: list[ProbeRow] = []
    ib = _connect()
    try:
        print(f"==> Connected to IB ({ib.client.host}:{ib.client.port}, clientId={ib.client.clientId})")
        print(f"    Probing {len(symbols)} symbols × {len(REPORT_TYPES)} report-types...\n")

        for symbol, exchange, currency, expectation in symbols:
            row = ProbeRow(symbol=symbol, exchange=exchange, currency=currency, expectation=expectation)
            print(f"-- {symbol} ({exchange}, {currency}) — {expectation}")
            contract, qual_err = _qualify(ib, symbol, exchange, currency)
            if contract is None:
                row.error_qual = qual_err
                print(f"   FAIL: {qual_err}")
                rows.append(row)
                continue
            row.contract_ok = True
            print(f"   contract: conId={contract.conId} secType={contract.secType} "
                  f"primaryExch={contract.primaryExchange or '-'}")

            for rtype, descr in REPORT_TYPES:
                xml, err = _fetch_report(ib, contract, rtype, timeout_s=report_timeout_s)
                if xml is not None:
                    size = len(xml)
                    snippet = _xml_snippet(xml)
                    row.results[rtype] = {"ok": True, "size": size, "snippet": snippet, "err": None}
                    print(f"   {rtype:<22s} OK  {size:>7d}B  {snippet[:80]}")
                    if xml_out:
                        xml_out.mkdir(parents=True, exist_ok=True)
                        (xml_out / f"{symbol}_{rtype}.xml").write_text(xml)
                else:
                    row.results[rtype] = {"ok": False, "size": 0, "snippet": "", "err": err}
                    print(f"   {rtype:<22s} ERR {err}")
            rows.append(row)
            print()
    finally:
        try:
            ib.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return rows


def print_summary(rows: list[ProbeRow]) -> None:
    print("=" * 80)
    print("COVERAGE-MATRIX")
    print("=" * 80)
    header = f"{'symbol':<8s} {'mkt':<8s} " + " ".join(f"{r[0][:14]:<14s}" for r in REPORT_TYPES)
    print(header)
    print("-" * len(header))
    rtype_names = [r[0] for r in REPORT_TYPES]
    for row in rows:
        mkt = f"{row.exchange}/{row.currency}"
        if not row.contract_ok:
            cells = " ".join(f"{'no-contract':<14s}" for _ in rtype_names)
        else:
            cells = " ".join(
                f"{'OK' if row.results.get(rt, {}).get('ok') else 'FAIL':<14s}"
                for rt in rtype_names
            )
        print(f"{row.symbol:<8s} {mkt:<8s} {cells}")

    # Aggregat: pro Report-Type, wieviele Symbole haben OK
    print()
    print("Per-Report-Type Coverage (von erfolgreich qualifizierten Contracts):")
    qualified = [r for r in rows if r.contract_ok]
    if not qualified:
        print("   (keine — alle contract-resolutions sind fehlgeschlagen)")
        return
    for rtype, descr in REPORT_TYPES:
        ok_count = sum(1 for r in qualified if r.results.get(rtype, {}).get("ok"))
        pct = 100.0 * ok_count / len(qualified)
        print(f"   {rtype:<22s} {ok_count}/{len(qualified)}  ({pct:.0f}%)  — {descr}")

    # Error-Sample fuer haeufige Fehler
    errors = {}
    for r in qualified:
        for rt, res in r.results.items():
            if not res.get("ok") and res.get("err"):
                errors.setdefault(res["err"][:80], []).append(f"{r.symbol}/{rt}")
    if errors:
        print()
        print("Distinct error messages:")
        for msg, where in sorted(errors.items(), key=lambda kv: -len(kv[1])):
            print(f"   [{len(where):>2d}x]  {msg}")
            print(f"          {', '.join(where[:5])}{'…' if len(where) > 5 else ''}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", help="Komma-getrennt SYM:EXCH:CCY (z.B. 'AAPL:NASDAQ:USD,SAP:IBIS:EUR'). Default = Built-in-Mix.")
    p.add_argument("--xml-out", type=pathlib.Path,
                   help="Wenn gesetzt: alle erfolgreichen XML-Responses als Datei dort ablegen (zum offline-Inspizieren).")
    p.add_argument("--timeout", type=int, default=12, help="Pro-Report timeout in Sekunden (default 12).")
    args = p.parse_args()

    if args.symbols:
        syms = []
        for tok in args.symbols.split(","):
            parts = [p.strip() for p in tok.split(":")]
            if len(parts) == 1:
                syms.append((parts[0], "SMART", "USD", "user-supplied"))
            elif len(parts) == 3:
                syms.append((parts[0], parts[1], parts[2], "user-supplied"))
            else:
                print(f"FEHLER: ungültiges Symbol-Spec '{tok}' — Format SYM[:EXCH:CCY]", file=sys.stderr)
                return 2
    else:
        syms = DEFAULT_SYMBOLS

    try:
        rows = run_probe(syms, xml_out=args.xml_out, report_timeout_s=args.timeout)
    except ConnectionError as e:
        print(f"\nFEHLER: {e}", file=sys.stderr)
        return 64

    print()
    print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
