"""nova-lab portfolio_core CLI — Portfolio-View-Schicht (minimal, 4 Core-Views).

Pflegt:
  - Tabellen list_portfolio_views + list_portfolio_view_members
  - Atomic-Views: v_latest_quote, v_latest_fx
  - Core-Views:   v_pos_holdings, v_mkt_holdings, v_list_portfolio, v_mkt_portfolio

Datenbank-Pflege (CRUD auf list_portfolio_views/-members) erfolgt via
modules.db_edit.

Subcommands:
    init                Apply 0001-0003 SQL-Files (idempotent)
    drop-legacy         Apply 0004_drop_legacy.sql (entfernt alte 3-Layer-View-
                        Schicht + Canonical-Layer; NACH Konsumenten-Umstellung)
    list                Listet portfolio_core-Views mit row-counts
    show <view>         Erste N rows einer View
    drop-all            Entfernt alle portfolio_core-Views (cleanup)

Beispiele:
    python -m modules.portfolio_core init
    python -m modules.portfolio_core show v_mkt_holdings --limit 20
    python -m modules.portfolio_core drop-legacy
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

import duckdb


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SQL_DIR = pathlib.Path(__file__).parent / "sql"

# 0001-0003 + 0005 in init; 0004 ist drop-legacy (separat aufrufen).
INIT_FILES = sorted(p for p in SQL_DIR.glob("0*.sql") if not p.name.startswith("0004"))
LEGACY_FILE = SQL_DIR / "0004_drop_legacy.sql"

# Module-eigene Views (explizit gelistet)
OWN_VIEWS = [
    "v_latest_quote",
    "v_latest_fx",
    "v_pos_holdings",
    "v_mkt_holdings",
    "v_list_portfolio",
    "v_mkt_portfolio",
    "v_pos_reconcile",
]


def _existing_own_views(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute("""
        SELECT table_name FROM information_schema.views
        WHERE table_schema = 'main' AND table_name = ANY(?)
        ORDER BY table_name
    """, [OWN_VIEWS]).fetchall()
    return [r[0] for r in rows]


# ---------- init ----------

def cmd_init(args) -> int:
    if not INIT_FILES:
        print(f"FEHLER: keine init-SQL-Files in {SQL_DIR}", file=sys.stderr)
        return 64
    con = duckdb.connect(str(DB_PATH))
    try:
        print(f"==> Applying {len(INIT_FILES)} SQL-Files in {DB_PATH}")
        for f in INIT_FILES:
            try:
                con.execute(f.read_text())
                print(f"    ✓ {f.name}")
            except Exception as e:  # noqa: BLE001
                print(f"    ✗ {f.name}: {e.__class__.__name__}: {e}", file=sys.stderr)
                return 65
        views = _existing_own_views(con)
        print(f"\n==> {len(views)} portfolio_core-Views aktiv:")
        for v in views:
            print(f"    {v}")
        return 0
    finally:
        con.close()


# ---------- drop-legacy ----------

def cmd_drop_legacy(args) -> int:
    if not LEGACY_FILE.is_file():
        print(f"FEHLER: {LEGACY_FILE} nicht gefunden.", file=sys.stderr)
        return 64
    if not args.yes:
        print(f"==> Migration: {LEGACY_FILE.name}")
        print(f"    Drops alte 3-Layer-View-Schicht + Canonical-Layer.")
        print(f"    Re-run mit --yes wenn alle Konsumenten umgestellt sind.")
        # Pre-Flight: zeige was gedroppt wuerde
        con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            sql_text = LEGACY_FILE.read_text()
            # Naiv: alle DROP-Targets extrahieren fuer Anzeige
            import re
            targets = re.findall(r"DROP\s+(VIEW|TABLE)\s+IF\s+EXISTS\s+(\w+);", sql_text, re.I)
            existing = []
            for kind, name in targets:
                check = con.execute("""
                    SELECT 1 FROM information_schema.tables WHERE table_name = ?
                    UNION
                    SELECT 1 FROM information_schema.views  WHERE table_name = ?
                """, [name, name]).fetchone()
                if check:
                    existing.append((kind, name))
            if not existing:
                print(f"    (nichts zu droppen — Migration bereits durchgefuehrt)")
            else:
                print(f"\n    Wuerde droppen ({len(existing)}):")
                for kind, name in existing:
                    print(f"      {kind:<5s} {name}")
        finally:
            con.close()
        return 0

    con = duckdb.connect(str(DB_PATH))
    try:
        print(f"==> Applying {LEGACY_FILE.name}")
        try:
            con.execute(LEGACY_FILE.read_text())
            print(f"    ✓ done")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"    ✗ {e.__class__.__name__}: {e}", file=sys.stderr)
            return 65
    finally:
        con.close()


# ---------- list ----------

def cmd_list(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        views = _existing_own_views(con)
        if not views:
            print("Keine portfolio_core-Views vorhanden. 'init' ausfuehren.")
            return 0
        print(f"==> {len(views)} portfolio_core-Views:")
        print()
        print(f"  {'view_name':<25s} {'rows':>10s}")
        print(f"  {'-'*25} {'-'*10}")
        for v in views:
            try:
                n = con.execute(f'SELECT COUNT(*) FROM "{v}"').fetchone()[0]
                print(f"  {v:<25s} {n:>10,}")
            except Exception as e:  # noqa: BLE001
                print(f"  {v:<25s} {'ERR':>10s}  ({e.__class__.__name__})")
        return 0
    finally:
        con.close()


# ---------- show ----------

def cmd_show(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        try:
            df = con.execute(f'SELECT * FROM "{args.view}" LIMIT {args.limit}').df()
        except duckdb.CatalogException:
            print(f"FEHLER: View '{args.view}' existiert nicht.", file=sys.stderr)
            print(f"        Verfuegbar: {', '.join(_existing_own_views(con))}", file=sys.stderr)
            return 64
        if df.empty:
            print(f"View '{args.view}' ist leer.")
            return 0
        print(f"==> {args.view}  (showing {len(df)} of N rows)")
        print()
        import pandas as pd
        with pd.option_context(
            "display.max_columns", None,
            "display.width", 200,
            "display.max_rows", args.limit,
            "display.float_format", lambda v: f"{v:,.2f}",
        ):
            print(df.to_string(index=False))
        return 0
    finally:
        con.close()


# ---------- drop-all ----------

def cmd_drop_all(args) -> int:
    con = duckdb.connect(str(DB_PATH))
    try:
        views = _existing_own_views(con)
        if not views:
            print("Keine views zu droppen.")
            return 0
        # Drop in Abhaengigkeits-Reihenfolge (Reports zuerst, dann Composed, dann Atomic)
        order = [
            "v_mkt_portfolio", "v_list_portfolio",
            "v_mkt_holdings",  "v_pos_holdings",
            "v_latest_fx",     "v_latest_quote",
        ]
        present = set(views)
        print(f"==> Droppe {len(views)} portfolio_core-Views:")
        for v in order:
            if v in present:
                con.execute(f'DROP VIEW IF EXISTS "{v}"')
                print(f"    dropped: {v}")
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Apply 0001-0003 SQL-Files (idempotent)")

    pdl = sub.add_parser("drop-legacy", help="Apply 0004_drop_legacy.sql")
    pdl.add_argument("--yes", action="store_true",
                      help="Tatsaechlich ausfuehren (default: dry-run).")

    sub.add_parser("list", help="Listet portfolio_core-Views + row-counts")

    ps = sub.add_parser("show", help="Erste N rows einer View")
    ps.add_argument("view")
    ps.add_argument("--limit", type=int, default=10)

    sub.add_parser("drop-all", help="Entfernt alle portfolio_core-Views")

    args = p.parse_args()
    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    dispatch = {
        "init":        cmd_init,
        "drop-legacy": cmd_drop_legacy,
        "list":        cmd_list,
        "show":        cmd_show,
        "drop-all":    cmd_drop_all,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
