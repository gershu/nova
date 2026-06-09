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
from datetime import datetime, timezone

from .client import GuruFocusClient, GuruFocusError

SQL_DIR = pathlib.Path(__file__).parent / "sql"

_GF_COLS = ["gf_score", "gf_value", "price_to_gf_value", "gf_valuation",
            "rank_financial_strength", "rank_profitability", "rank_growth",
            "rank_balancesheet", "predictability", "fscore", "zscore",
            "mscore", "moat_score", "roic", "wacc"]

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


def _gf_symbol(sym: str) -> str:
    return (sym or "").strip().upper().replace("_", ".")


def cmd_ingest_scores(args) -> int:
    """GuruFocus GF-Score/Raenge je Universums-Wert -> ref_gf_score.
    sec-/GuruFocus-Calls lock-frei; Upserts kurz unter dem Schreib-Lock."""
    import duckdb  # noqa: F401
    from modules.common import dblock
    from . import adapter, provider
    from .client import GuruFocusClient, GuruFocusError

    if not provider.available():
        print("Kein GURUFOCUS_TOKEN gesetzt.", file=sys.stderr)
        return 2
    with dblock.rw_connection() as con:
        for f in sorted(SQL_DIR.glob("0*.sql")):
            con.execute(f.read_text())
        uni = con.execute(
            "SELECT DISTINCT i.ref_instrument_id, i.symbol, i.name "
            "FROM ref_fundamentals_latest f JOIN ref_instruments i "
            "USING (ref_instrument_id) WHERE i.symbol IS NOT NULL "
            "ORDER BY i.symbol").fetchall()
    if args.limit:
        uni = uni[:args.limit]

    seen, n_ok, n_err = set(), 0, 0
    gf = GuruFocusClient()
    try:
        for rid, sym, name in uni:
            norm = _gf_symbol(sym)
            if norm in seen:
                continue
            seen.add(norm)
            now = datetime.now(timezone.utc)
            try:
                q = adapter.quality_snapshot(gf.summary(norm))  # lock-frei
                err = None
            except GuruFocusError as e:
                q, err = {}, str(e)[:200]
            with dblock.rw_connection() as con:
                con.execute("DELETE FROM ref_gf_score WHERE "
                            "ref_instrument_id=?", [rid])
                con.execute(
                    "INSERT INTO ref_gf_score (ref_instrument_id, symbol, "
                    "name, sector, " + ", ".join(_GF_COLS) + ", error, "
                    "computed_at) VALUES (" + ",".join("?" * (4 + len(_GF_COLS)
                                                              + 2)) + ")",
                    [rid, sym, q.get("name") or name, q.get("sector")]
                    + [q.get(c) for c in _GF_COLS] + [err, now])
            if err:
                n_err += 1
                print(f"  ✗ {sym}: {err}", file=sys.stderr)
            else:
                n_ok += 1
                print(f"  ✓ {sym}: GF {q.get('gf_score')} · "
                      f"{q.get('gf_valuation') or '—'}")
            if args.sleep:
                import time
                time.sleep(args.sleep)
    finally:
        gf.close()
    print(f"ref_gf_score: {n_ok} ok, {n_err} Fehler.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m modules.gurufocus")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("probe", help="Abdeckung fuer Ticker pruefen")
    pr.add_argument("tickers", nargs="+")
    pr.add_argument("--out", default="~/nova_output/gurufocus")
    pr.set_defaults(func=cmd_probe)
    pi = sub.add_parser("ingest-scores",
                        help="GF-Score/Raenge je Universums-Wert -> ref_gf_score")
    pi.add_argument("--limit", type=int, default=0)
    pi.add_argument("--sleep", type=float, default=0.0)
    pi.set_defaults(func=cmd_ingest_scores)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
