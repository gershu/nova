"""nova-lab db_edit CLI — generisches DuckDB-Table <-> Excel Sync.

Workflow:
    1. python -m modules.db_edit export <table>     -> xlsx im ~/nova_output/db_edit/
    2. Benutzer editiert xlsx in Excel / Numbers / etc.
    3. python -m modules.db_edit load <xlsx> [--mode insert|truncate] [--dry-run]

Defaults:
    Mode    insert    INSERT OR REPLACE pro Zeile (Append/Update via PK)
            truncate  DELETE FROM <table> + INSERT, mit automatischem Backup
                       in <table>__bkp_<ts>

WICHTIG: Bei 'truncate' wird der Inhalt VOR dem DELETE in eine
'<table>__bkp_<YYYYMMDDTHHMMSS>' Tabelle gesnapshottet — Datenverlust
nicht moeglich, alte Daten ueber 'DROP TABLE bkp_...' nach Bedarf entfernen.

Subcommands:
    export <table> [--output <path>]
    load <xlsx> [--table <name>] [--mode insert|truncate] [--dry-run]
    list-tables [--include-views]
    schema <table>

Beispiele:
    python -m modules.db_edit list-tables
    python -m modules.db_edit schema list_portfolio_views
    python -m modules.db_edit export list_portfolio_views
    python -m modules.db_edit load ~/nova_output/db_edit/list_portfolio_views_*.xlsx \\
                                   --mode truncate --dry-run
    python -m modules.db_edit load ~/nova_output/db_edit/list_portfolio_views_*.xlsx \\
                                   --mode truncate
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from datetime import datetime, timezone

import duckdb

from .exporter import export_to_xlsx
from .loader import load_from_xlsx
from .schema_introspect import get_table_info, list_user_tables


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
DEFAULT_OUTPUT_DIR = pathlib.Path.home() / "nova_output" / "db_edit"


# ---------- export ----------

def cmd_export(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        info = get_table_info(con, args.table)
        if info is None:
            print(f"FEHLER: Tabelle '{args.table}' existiert nicht in {DB_PATH}.", file=sys.stderr)
            print(f"        Tipp: python -m modules.db_edit list-tables", file=sys.stderr)
            return 64

        if args.output:
            out_path = pathlib.Path(args.output)
        else:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out_path = DEFAULT_OUTPUT_DIR / f"{args.table}_{ts}.xlsx"

        print(f"==> Export '{args.table}' -> {out_path}")
        n, path = export_to_xlsx(con, args.table, out_path)
        print(f"    {n} rows, {len(info.columns)} columns")
        print(f"    PK: {', '.join(info.pk_columns) or '(none)'}")
        print(f"    Output: {path}")
        return 0
    finally:
        con.close()


# ---------- load ----------

def cmd_load(args) -> int:
    xlsx_path = pathlib.Path(args.xlsx).expanduser()
    if not xlsx_path.is_file():
        print(f"FEHLER: xlsx '{xlsx_path}' existiert nicht.", file=sys.stderr)
        return 64

    con = duckdb.connect(str(DB_PATH))
    try:
        print(f"==> Load '{xlsx_path.name}' -> DB '{DB_PATH}'")
        print(f"    Mode: {args.mode}  Dry-Run: {args.dry_run}")
        if args.table:
            print(f"    Table-Override: {args.table}")
        stats = load_from_xlsx(con, xlsx_path,
                                table_override=args.table,
                                mode=args.mode,
                                dry_run=args.dry_run)
        print()
        print(f"    Table              : {stats.table_name}")
        print(f"    DB rows before     : {stats.n_db_rows_before:,}")
        print(f"    Excel rows         : {stats.n_excel_rows:,}")
        if stats.backup_table:
            print(f"    Backup-Snapshot    : {stats.backup_table}")
        if stats.dry_run:
            print(f"    (DRY-RUN: no DB changes)")
        else:
            print(f"    Rows inserted/upserted: {stats.n_inserted:,}")
            print(f"    DB rows after      : {stats.n_db_rows_after:,}")
        if stats.warnings:
            print(f"\n    Warnings ({len(stats.warnings)}):")
            for w in stats.warnings[:10]:
                print(f"      ! {w}")
            if len(stats.warnings) > 10:
                print(f"      ... ({len(stats.warnings) - 10} more)")
        return 0
    except ValueError as e:
        print(f"\nFEHLER: {e}", file=sys.stderr)
        return 65
    finally:
        con.close()


# ---------- list-tables ----------

def cmd_list_tables(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        tabs = list_user_tables(con, include_views=args.include_views)
        if not tabs:
            print("(keine Tabellen)")
            return 0
        print(f"==> {len(tabs)} Tabellen in {DB_PATH}:")
        print()
        print(f"  {'table_name':<40s} {'rows':>10s}")
        print(f"  {'-'*40} {'-'*10}")
        for name, n in tabs:
            print(f"  {name:<40s} {n:>10,}")
        return 0
    finally:
        con.close()


# ---------- schema ----------

def cmd_schema(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        info = get_table_info(con, args.table)
        if info is None:
            print(f"FEHLER: Tabelle '{args.table}' existiert nicht.", file=sys.stderr)
            return 64

        n = con.execute(f'SELECT COUNT(*) FROM "{args.table}"').fetchone()[0]
        print(f"==> Schema '{args.table}'  ({n:,} rows)")
        print()
        print(f"  {'name':<30s} {'type':<18s} {'pk':<3s} {'notnull':<7s} default")
        print(f"  {'-'*30} {'-'*18} {'-'*3} {'-'*7} {'-'*30}")
        for c in info.columns:
            print(f"  {c.name:<30s} {c.db_type:<18s} "
                  f"{'PK' if c.is_pk else '':<3s} "
                  f"{'YES' if c.notnull else '':<7s} "
                  f"{c.default or ''}")
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("export", help="DB-Table -> xlsx")
    pe.add_argument("table")
    pe.add_argument("--output", default=None, help="Override output path")

    pl = sub.add_parser("load", help="xlsx -> DB-Table")
    pl.add_argument("xlsx")
    pl.add_argument("--table", default=None,
                     help="Tabelle (sonst aus __meta__-Sheet oder erstem Sheet-Name)")
    pl.add_argument("--mode", choices=["insert", "truncate"], default="insert",
                     help="insert=UPSERT (default), truncate=DELETE+INSERT mit Backup")
    pl.add_argument("--dry-run", action="store_true",
                     help="Zeigt Aktion ohne Commit")

    plt = sub.add_parser("list-tables", help="Alle Tabellen + Row-Counts")
    plt.add_argument("--include-views", action="store_true")

    ps = sub.add_parser("schema", help="Schema einer Tabelle")
    ps.add_argument("table")

    args = p.parse_args()

    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    dispatch = {
        "export":      cmd_export,
        "load":        cmd_load,
        "list-tables": cmd_list_tables,
        "schema":      cmd_schema,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
