"""nova-lab Quality-Score-Batch CLI.

Berechnet den Gesamt-Qualitaets-Score (Shearn-5-Themen,
modules.dashboard.quality.overall_score) je Universums-Wert und persistiert
ihn nach ref_quality_score. Laufzeit-unkritisch (nachtfaehig); pro Wert
mehrere sec-api.io-Calls.

Subcommands:
    run [--all] [--limit N] [--symbols A,B] [--sleep S]
                          [--n-years N] [--period annual|quarterly]
        Universum laden, je Wert scoren, ref_quality_score aktualisieren.
        Default-Universum: Werte mit Fundamentaldaten
        (ref_fundamentals_latest); --all = alle aktiven ref_instruments.
    show [--limit N] [--min-score S]
        Vorberechnete Scores anzeigen (nach score desc).

Environment:
    LAB_DB_PATH        optional — default ~/nova_data/lab.duckdb
    NOVA_SEC_API_KEY   Pflicht fuer 'run' (sec-api.io).

Beispiele:
    python -m modules.quality_score run
    python -m modules.quality_score run --symbols AAPL,MSFT,NVDA
    python -m modules.quality_score run --all --sleep 0.2
    python -m modules.quality_score show --limit 20
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from datetime import datetime, timezone

import duckdb

from modules.common import dblock
from modules.dashboard import quality as ql


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SQL_DIR = pathlib.Path(__file__).parent / "sql"
SQL_FILES = sorted(SQL_DIR.glob("0*.sql"))

_THEME_COL = {
    "return_on_capital": "sub_return_on_capital",
    "balance_sheet":     "sub_balance_sheet",
    "stock_based_comp":  "sub_stock_based_comp",
    "gaap_vs_non_gaap":  "sub_gaap_vs_non_gaap",
    "insider":           "sub_insider",
}


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    for f in SQL_FILES:
        con.execute(f.read_text())


def _universe(con, *, all_instruments: bool) -> list[tuple[str, str]]:
    if all_instruments:
        rows = con.execute(
            "SELECT ref_instrument_id, symbol FROM ref_instruments "
            "WHERE active AND symbol IS NOT NULL ORDER BY symbol"
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT i.ref_instrument_id, i.symbol "
            "FROM ref_fundamentals_latest f "
            "JOIN ref_instruments i USING (ref_instrument_id) "
            "WHERE i.symbol IS NOT NULL ORDER BY i.symbol"
        ).fetchall()
    # dedupe (mehrere Listings je Wert moeglich)
    seen, out = set(), []
    for rid, sym in rows:
        if rid in seen:
            continue
        seen.add(rid)
        out.append((rid, sym))
    return out


def _upsert(con, rid, sym, res, *, n_years, period, error, now) -> None:
    subs = {r["key"]: r["sub"] for r in (res["rows"] if res else [])}
    con.execute("DELETE FROM ref_quality_score WHERE ref_instrument_id = ?",
                [rid])
    con.execute(
        "INSERT INTO ref_quality_score (ref_instrument_id, symbol, score, "
        "n_ok, sub_return_on_capital, sub_balance_sheet, sub_stock_based_comp, "
        "sub_gaap_vs_non_gaap, sub_insider, n_years, period, error, "
        "computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [rid, sym,
         (res["score"] if res else None),
         (res["n_ok"] if res else 0),
         subs.get("return_on_capital"), subs.get("balance_sheet"),
         subs.get("stock_based_comp"), subs.get("gaap_vs_non_gaap"),
         subs.get("insider"), n_years, period, error, now])


def cmd_run(args) -> int:
    if not os.environ.get("NOVA_SEC_API_KEY", "").strip():
        print("FEHLER: NOVA_SEC_API_KEY nicht gesetzt.", file=sys.stderr)
        return 2
    # Schema + Universum: kurzer gelockter RW-Block (Lesen ist hier ok).
    with dblock.rw_connection() as con:
        apply_schema(con)
        if args.symbols:
            wanted = {s.strip().upper() for s in args.symbols.split(",") if s}
            uni = [(rid, sym) for rid, sym
                   in _universe(con, all_instruments=True)
                   if (sym or "").upper() in wanted]
        else:
            uni = _universe(con, all_instruments=args.all)
    if args.limit:
        uni = uni[:args.limit]
    print(f"Quality-Score-Batch: {len(uni)} Werte · n_years="
          f"{args.n_years} · period={args.period}")

    ok = err = 0
    for i, (rid, sym) in enumerate(uni, 1):
        # Score berechnen — langsam (sec-api), KEIN Lock/DB.
        res, error = None, None
        try:
            res = ql.overall_score(sym, n_years=args.n_years,
                                   period=args.period)
        except Exception as e:  # noqa: BLE001
            error = f"{e.__class__.__name__}: {e}"
        # Persistieren — kurzer gelockter RW-Block.
        now = datetime.now(timezone.utc)
        with dblock.rw_connection() as con:
            _upsert(con, rid, sym, res, n_years=args.n_years,
                    period=args.period, error=error, now=now)
        if error:
            err += 1
            tag = "ERR"
        else:
            ok += 1
            tag = f"{res['score']}" if res["score"] is not None else "—"
        if i % 10 == 0 or i == len(uni):
            print(f"  [{i}/{len(uni)}] {sym}: {tag}")
        if args.sleep:
            time.sleep(args.sleep)
    print(f"Fertig: {ok} ok, {err} Fehler -> ref_quality_score.")
    return 0


def cmd_show(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = con.execute(
            "SELECT symbol, score, n_ok, computed_at FROM ref_quality_score "
            "WHERE (? IS NULL OR score >= ?) "
            "ORDER BY score DESC NULLS LAST, symbol LIMIT ?",
            [args.min_score, args.min_score, args.limit]).fetchall()
    finally:
        con.close()
    if not rows:
        print("Keine vorberechneten Scores (Batch schon gelaufen?).")
        return 0
    print(f"{'Symbol':<10}{'Score':>6}{'Themen':>8}  Stand")
    for sym, score, n_ok, ts in rows:
        print(f"{sym:<10}{(score if score is not None else '—'):>6}"
              f"{n_ok:>8}  {str(ts)[:19]}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m modules.quality_score")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="Universum scoren -> ref_quality_score")
    pr.add_argument("--all", action="store_true",
                    help="alle aktiven ref_instruments (statt nur "
                         "Fundamentaldaten-Universum)")
    pr.add_argument("--limit", type=int, default=0)
    pr.add_argument("--symbols", type=str, default="",
                    help="nur diese Symbole (kommagetrennt)")
    pr.add_argument("--sleep", type=float, default=0.0,
                    help="Pause (s) zwischen Werten (Rate-Limit)")
    pr.add_argument("--n-years", dest="n_years", type=int, default=5)
    pr.add_argument("--period", choices=("annual", "quarterly"),
                    default="annual")
    pr.set_defaults(func=cmd_run)

    ps = sub.add_parser("show", help="vorberechnete Scores anzeigen")
    ps.add_argument("--limit", type=int, default=30)
    ps.add_argument("--min-score", dest="min_score", type=int, default=None)
    ps.set_defaults(func=cmd_show)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
