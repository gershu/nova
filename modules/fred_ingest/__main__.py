"""nova-lab fred_ingest CLI — Economic Series via FRED-API.

Subcommands:
    init                    Apply SQL-Schema + Default-Series-Seed (idempotent)
    list                    Aktive Series + last-fetched-ts + #rows
    add-series <id>         Neue Series in ref_economic_series anlegen
    fetch <id> [--since]    Ein Series-Update (incremental wenn since fehlt)
    fetch-all               Alle aktiven Series — fuer den Daily-Daemon
    show <id> [--limit N]   Letzte N Observations einer Series

Environment:
    NOVA_FRED_API_KEY       Pflicht — via ~/.nova_env oder Shell
    LAB_DB_PATH             optional — default ~/nova_data/lab.duckdb

Beispiele:
    python -m modules.fred_ingest init
    python -m modules.fred_ingest fetch-all
    python -m modules.fred_ingest show VIXCLS --limit 20
    python -m modules.fred_ingest add-series UNRATE --category macro
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import duckdb

from .client import FredApiError, Observation, fetch_observations, fetch_series_metadata


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SQL_DIR = pathlib.Path(__file__).parent / "sql"
SQL_FILES = sorted(SQL_DIR.glob("0*.sql"))

DEFAULT_HISTORY_YEARS = 5


# ---------- init ----------

def cmd_init(args) -> int:
    if not SQL_FILES:
        print(f"FEHLER: keine SQL-Files in {SQL_DIR}", file=sys.stderr)
        return 64
    con = duckdb.connect(str(DB_PATH))
    try:
        print(f"==> Applying {len(SQL_FILES)} SQL-Files in {DB_PATH}")
        for f in SQL_FILES:
            try:
                con.execute(f.read_text())
                print(f"    ✓ {f.name}")
            except Exception as e:  # noqa: BLE001
                print(f"    ✗ {f.name}: {e.__class__.__name__}: {e}", file=sys.stderr)
                return 65
        n = con.execute("SELECT count(*) FROM ref_economic_series WHERE active").fetchone()[0]
        print(f"\n==> {n} active Series in ref_economic_series")
        return 0
    finally:
        con.close()


# ---------- list ----------

def cmd_list(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = con.execute("""
            SELECT s.series_id, s.name, s.category, s.units, s.frequency,
                   s.active,
                   (SELECT count(*) FROM mkt_economic_series m WHERE m.series_id = s.series_id) AS n_rows,
                   (SELECT max(ts)  FROM mkt_economic_series m WHERE m.series_id = s.series_id) AS last_ts
            FROM ref_economic_series s
            ORDER BY s.active DESC, s.category, s.series_id
        """).fetchall()
        if not rows:
            print("Keine Series konfiguriert. 'init' ausfuehren.")
            return 0
        print(f"==> {len(rows)} Series konfiguriert:")
        print()
        print(f"  {'series_id':<14s} {'cat':<10s} {'units':<8s} {'freq':<8s} "
              f"{'act':<4s} {'rows':>8s}  {'last_ts':<12s}  {'name'}")
        print(f"  {'-'*14} {'-'*10} {'-'*8} {'-'*8} {'-'*4} {'-'*8}  {'-'*12}  {'-'*30}")
        for sid, name, cat, units, freq, act, n_rows, last_ts in rows:
            act_s = "✓" if act else "—"
            print(f"  {sid:<14s} {(cat or ''):<10s} {(units or ''):<8s} "
                  f"{(freq or ''):<8s} {act_s:<4s} {n_rows:>8d}  "
                  f"{str(last_ts) if last_ts else '—':<12s}  {name}")
        return 0
    finally:
        con.close()


# ---------- add-series ----------

def cmd_add_series(args) -> int:
    sid = args.series_id
    con = duckdb.connect(str(DB_PATH))
    try:
        exists = con.execute(
            "SELECT 1 FROM ref_economic_series WHERE series_id = ?", [sid]
        ).fetchone()
        if exists:
            print(f"Series '{sid}' bereits in ref_economic_series.", file=sys.stderr)
            return 64

        # Metadata von FRED holen wenn Name/Units nicht explizit angegeben
        meta = {}
        if not args.name or not args.units or not args.frequency:
            try:
                print(f"==> Hole Metadaten fuer '{sid}' von FRED...")
                meta = fetch_series_metadata(sid)
            except FredApiError as e:
                print(f"WARN: FRED-Metadata fetch fail: {e}", file=sys.stderr)

        name      = args.name      or meta.get("title", sid)
        units     = args.units     or meta.get("units", "")
        frequency = args.frequency or meta.get("frequency_short", "").lower() or "daily"
        notes     = args.notes     or meta.get("notes", "")

        con.execute("""
            INSERT INTO ref_economic_series
                (series_id, name, description, category, units, frequency, source, active, notes)
            VALUES (?, ?, ?, ?, ?, ?, 'fred', TRUE, ?)
        """, [sid, name, None, args.category, units, frequency, notes])
        print(f"✓ Added: {sid} | {name} | {args.category} | {units} | {frequency}")
        print(f"  Naechster Step: python -m modules.fred_ingest fetch {sid}")
        return 0
    finally:
        con.close()


# ---------- fetch ----------

def _do_fetch(con, sid: str, since: date | None) -> tuple[int, int]:
    """Returns (n_inserted, n_skipped)."""
    obs = fetch_observations(sid, since=since)
    if not obs:
        return (0, 0)
    n_inserted, n_skipped = 0, 0
    for o in obs:
        # INSERT OR IGNORE — bei doppeltem PK skip
        before = con.execute(
            "SELECT count(*) FROM mkt_economic_series WHERE series_id=? AND ts=? AND source='fred'",
            [sid, o.ts],
        ).fetchone()[0]
        if before:
            n_skipped += 1
            continue
        con.execute("""
            INSERT INTO mkt_economic_series (series_id, ts, value, source)
            VALUES (?, ?, ?, 'fred')
        """, [sid, o.ts, o.value])
        n_inserted += 1
    return (n_inserted, n_skipped)


def cmd_fetch(args) -> int:
    sid = args.series_id
    con = duckdb.connect(str(DB_PATH))
    try:
        # Series existiert in ref_economic_series?
        row = con.execute(
            "SELECT name, active FROM ref_economic_series WHERE series_id = ?", [sid]
        ).fetchone()
        if not row:
            print(f"FEHLER: Series '{sid}' nicht in ref_economic_series.", file=sys.stderr)
            print(f"        Erst hinzufuegen: python -m modules.fred_ingest add-series {sid}",
                  file=sys.stderr)
            return 64

        # Since: --since-Arg, sonst letzter ts, sonst 5y-Default
        since: date | None
        if args.since:
            since = date.fromisoformat(args.since)
        else:
            last_ts = con.execute(
                "SELECT max(ts) FROM mkt_economic_series WHERE series_id = ?", [sid]
            ).fetchone()[0]
            if last_ts:
                since = last_ts  # incremental — FRED liefert ab incl. since
            else:
                since = date.today() - timedelta(days=365 * DEFAULT_HISTORY_YEARS)

        print(f"==> Fetching '{sid}' since {since}")
        try:
            n_ins, n_skip = _do_fetch(con, sid, since)
        except FredApiError as e:
            print(f"✗ FRED-Fail: {e}", file=sys.stderr)
            return 65
        print(f"    {n_ins} new rows · {n_skip} skipped (already present)")
        return 0
    finally:
        con.close()


# ---------- fetch-all ----------

def cmd_fetch_all(args) -> int:
    run_id = f"fred-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    started_at = datetime.now(timezone.utc)
    con = duckdb.connect(str(DB_PATH))
    try:
        active = con.execute(
            "SELECT series_id, name FROM ref_economic_series WHERE active ORDER BY series_id"
        ).fetchall()
        if not active:
            print("Keine aktiven Series. Erst 'init' / 'add-series' ausfuehren.")
            return 0
        # last_ts pro Series UPFRONT holen — vermeidet DuckDB-statistics-Cache-Bug
        # der bei post-INSERT-Subquery in der Schleife auftrat.
        last_ts_map: dict[str, date | None] = {sid: None for sid, _ in active}
        for sid, max_ts in con.execute("""
            SELECT series_id, max(ts) FROM mkt_economic_series GROUP BY series_id
        """).fetchall():
            last_ts_map[sid] = max_ts

        print(f"==> fetch-all: {len(active)} active series  (run_id={run_id})")
        total_ins, total_skip = 0, 0
        n_ok, n_fail = 0, 0
        for sid, name in active:
            last_ts = last_ts_map.get(sid)
            since = last_ts if last_ts else (date.today() - timedelta(days=365 * DEFAULT_HISTORY_YEARS))
            try:
                n_ins, n_skip = _do_fetch(con, sid, since)
                total_ins  += n_ins
                total_skip += n_skip
                n_ok += 1
                print(f"    ✓ {sid:<14s} {n_ins:>5d} new / {n_skip:>5d} skipped")
            except FredApiError as e:
                n_fail += 1
                print(f"    ✗ {sid:<14s} FAIL: {e}", file=sys.stderr)

        finished_at = datetime.now(timezone.utc)
        status = "ok" if n_fail == 0 else ("partial" if n_ok > 0 else "fail")
        con.execute("""
            INSERT INTO audit_fred_ingest_runs
                (run_id, started_at, finished_at, series_count, rows_inserted, rows_skipped, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [run_id, started_at, finished_at, len(active), total_ins, total_skip, status])

        print(f"\n==> Summary: {n_ok} OK · {n_fail} FAIL · {total_ins} rows inserted · status={status}")
        return 0 if n_fail == 0 else 1
    finally:
        con.close()


# ---------- show ----------

def cmd_show(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        meta = con.execute(
            "SELECT name, units, frequency FROM ref_economic_series WHERE series_id = ?",
            [args.series_id],
        ).fetchone()
        if not meta:
            print(f"FEHLER: Series '{args.series_id}' nicht in ref_economic_series.",
                  file=sys.stderr)
            return 64
        name, units, freq = meta
        print(f"==> {args.series_id}  ·  {name}  ·  {units}  ·  {freq}")
        df = con.execute("""
            SELECT ts, value FROM mkt_economic_series
            WHERE series_id = ? ORDER BY ts DESC LIMIT ?
        """, [args.series_id, args.limit]).df()
        if df.empty:
            print("(keine Daten)")
            return 0
        print()
        import pandas as pd
        with pd.option_context("display.max_rows", args.limit,
                                "display.float_format", lambda v: f"{v:>10,.4f}"):
            print(df.to_string(index=False))
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Apply SQL-Schema + Default-Series-Seed")
    sub.add_parser("list", help="Konfigurierte Series auflisten")

    p_add = sub.add_parser("add-series", help="Neue FRED-Series anlegen")
    p_add.add_argument("series_id")
    p_add.add_argument("--name",      default=None, help="(default: aus FRED-Metadata)")
    p_add.add_argument("--category",  default="macro",
                        choices=["volatility","rates","fx","credit","commodity","macro"])
    p_add.add_argument("--units",     default=None)
    p_add.add_argument("--frequency", default=None, choices=[None,"daily","weekly","monthly","quarterly"])
    p_add.add_argument("--notes",     default=None)

    p_fetch = sub.add_parser("fetch", help="Eine Series aktualisieren (incremental)")
    p_fetch.add_argument("series_id")
    p_fetch.add_argument("--since", default=None,
                          help="ISO-Datum YYYY-MM-DD; default = letztes vorhandenes ts oder 5y zurueck")

    sub.add_parser("fetch-all", help="Alle aktiven Series aktualisieren (Daemon-Modus)")

    p_show = sub.add_parser("show", help="Letzte N Observations einer Series")
    p_show.add_argument("series_id")
    p_show.add_argument("--limit", type=int, default=20)

    args = p.parse_args()
    if not DB_PATH.is_file() and args.cmd != "init":
        # init kann eine neue DB erstellen — alle anderen brauchen sie schon
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}. 'init' zuerst.", file=sys.stderr)
        return 64

    dispatch = {
        "init":       cmd_init,
        "list":       cmd_list,
        "add-series": cmd_add_series,
        "fetch":      cmd_fetch,
        "fetch-all":  cmd_fetch_all,
        "show":       cmd_show,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
