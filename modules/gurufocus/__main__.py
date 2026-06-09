"""GuruFocus-Coverage-Spike.

Holt fuer ein paar Ticker die zentralen Endpoints und prueft, ob GuruFocus die
Datenpunkte liefert, die nova heute aus sec-api/yfinance bezieht — plus die
GuruFocus-Spezifika (GF-Value, Ranks, Guru-Portfolios). Legt die Roh-JSONs zur
manuellen Sichtung ab.

Aufruf (auf nova-hub, mit Token in ~/.nova_env):
    python -m modules.gurufocus probe AAPL MSFT KO
    python -m modules.gurufocus probe AAPL --out ~/nova_output/gurufocus

Es werden KEINE DB-/Dashboard-Aenderungen gemacht — reiner Lese-Spike.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .client import GuruFocusClient, GuruFocusError

# Datenpunkte, die nova heute nutzt -> als Suchbegriffe (Teil-String, case-insensitive)
# gegen die GuruFocus-Antworten gematcht. Mehrere Synonyme je Punkt.
_PROBE = {
    "Bewertung": ["pe ratio", "p/e", "pb ratio", "p/b", "ps ratio", "p/s",
                  "price-to-free-cash-flow", "pfcf", "ev-to-ebitda",
                  "ev2ebitda", "peg"],
    "Profitabilitaet": ["gross margin", "operating margin", "net margin",
                        "roe", "roa", "roic", "roce"],
    "Verschuldung/Liquiditaet": ["debt-to-equity", "debt2equity",
                                 "current ratio", "quick ratio",
                                 "interest coverage"],
    "Cash/Dividende": ["dividend yield", "payout", "free cash flow", "fcf"],
    "GuruFocus-Spezifika": ["gf value", "gf score", "financial strength",
                            "profitability rank", "predictability",
                            "valuation_and_quality"],
}
_ENDPOINTS = ["summary", "keyratios", "financials", "quote", "gurus", "insider"]


def _find(obj, term: str, path: str = "") -> tuple[str, object] | None:
    """Rekursiv ersten Key/Wert finden, dessen Key 'term' (Teilstring,
    case-insensitive) enthaelt. Returns (pfad, beispielwert) oder None."""
    t = term.lower()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if t in str(k).lower():
                # Key-Treffer auch bei Listen/Dicts (Zeitreihen) — zusammenfassen.
                sample = (f"list[{len(v)}]" if isinstance(v, list)
                          else "{…}" if isinstance(v, dict) else v)
                return (f"{path}.{k}".lstrip("."), sample)
        for k, v in obj.items():
            hit = _find(v, term, f"{path}.{k}")
            if hit:
                return hit
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            hit = _find(v, term, f"{path}[{i}]")
            if hit:
                return hit
    return None


def _count(obj) -> int:
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        for k in ("gurus", "data", "rows", "picks", "transactions"):
            if isinstance(obj.get(k), list):
                return len(obj[k])
    return 0


def cmd_probe(args) -> int:
    out = pathlib.Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    try:
        gf = GuruFocusClient()
    except GuruFocusError as e:
        print(e, file=sys.stderr)
        return 2

    for sym in args.tickers:
        print(f"\n=== {sym} ===")
        bundle = {}
        for ep in _ENDPOINTS:
            try:
                data = getattr(gf, ep)(sym)
                bundle[ep] = data
                (out / f"{sym}_{ep}.json").write_text(
                    json.dumps(data, indent=2)[:2_000_000])
                top = (list(data.keys())[:8] if isinstance(data, dict)
                       else f"list[{len(data)}]")
                print(f"  [{ep}] ok  top-keys: {top}")
            except GuruFocusError as e:
                print(f"  [{ep}] FEHLER: {e}", file=sys.stderr)

        # Coverage gegen unsere Datenpunkte
        print("  -- Abdeckung --")
        for group, terms in _PROBE.items():
            hits = []
            for term in terms:
                for ep, data in bundle.items():
                    h = _find(data, term)
                    if h:
                        hits.append(term)
                        break
            print(f"    {group}: {len(hits)}/{len(terms)}  "
                  f"{'· '.join(hits) if hits else '— nichts gefunden'}")
        # Historie-Tiefe (financials annuals)
        fin = bundle.get("financials") or {}
        yrs = _find(fin, "fiscal year")
        print(f"    Historie (Fiscal Year-Treffer): {yrs[1] if yrs else '—'}")
        print(f"    Gurus: {_count(bundle.get('gurus'))} · "
              f"Insider: {_count(bundle.get('insider'))}")
    print(f"\nRoh-JSON unter: {out}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m modules.gurufocus")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("probe", help="Abdeckung fuer Ticker pruefen")
    pr.add_argument("tickers", nargs="+")
    pr.add_argument("--out", default="~/nova_output/gurufocus")
    pr.set_defaults(func=cmd_probe)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
