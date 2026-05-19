"""nova-lab watchlist CLI (B-Phase Phase W1).

Verwaltet list_watchlists + list_watchlist_members. Many-to-many — ein
Instrument kann auf mehreren Listen sein. 'in_portfolio' ist KEINE
Watchlist sondern wird via v_relevant_instruments-View ausgeleitet.

Subcommands:
    init                                       Default-Listen anlegen
    lists                                      Alle Watchlists + member-counts
    show <watchlist_id>                        Members einer Watchlist
    add <ref_instrument_id> --to <wl>          Add to watchlist
    remove <ref_instrument_id> --from <wl>     Remove
    where <ref_instrument_id>                  Welche Listen enthalten dieses Inst
    find <symbol_pattern>                      ref_instrument_id-Suche per Symbol
    relevant                                   v_relevant_instruments-View dump

Beispiele:
    python -m modules.watchlist init
    python -m modules.watchlist add IB:AAPL:USD --to buy_candidates --notes "earnings 2026Q3"
    python -m modules.watchlist find AAPL
    python -m modules.watchlist where IB:AAPL:USD
    python -m modules.watchlist show buy_candidates
    python -m modules.watchlist remove IB:AAPL:USD --from buy_candidates
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
SCHEMA_FILE = pathlib.Path(__file__).parent / "sql" / "0001_watchlist.sql"


# ---------- Defaults ----------

DEFAULT_WATCHLISTS = [
    {
        "watchlist_id": "buy_candidates",
        "name":         "Kaufkandidaten",
        "description":  "Instrumente die du potenziell kaufen willst (manuell gepflegt).",
        "origin":       "user",
    },
    {
        "watchlist_id": "observation",
        "name":         "Beobachtung",
        "description":  "Reine Beobachtung ohne konkrete Kaufabsicht.",
        "origin":       "user",
    },
    {
        "watchlist_id": "system_recommendations",
        "name":         "System-Empfehlungen",
        "description":  "Vom Screener / LLM-Modulen empfohlen, nicht user-gepflegt.",
        "origin":       "system",
    },
]


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Laedt portfolio-Schema (fuer pos_holdings das v_relevant_instruments
    referenziert) + watchlist-Schema."""
    portfolio_dir = pathlib.Path(__file__).parent.parent / "portfolio" / "sql"
    if portfolio_dir.is_dir():
        for f in sorted(portfolio_dir.glob("0*.sql")):
            con.execute(f.read_text())
    # Plus ingest fuer ref_instruments
    ingest_dir = pathlib.Path(__file__).parent.parent / "ingest" / "sql"
    if ingest_dir.is_dir():
        for f in sorted(ingest_dir.glob("0*.sql")):
            con.execute(f.read_text())
    con.execute(SCHEMA_FILE.read_text())


# ---------- Commands ----------

def cmd_init(con: duckdb.DuckDBPyConnection, args) -> int:
    new_count = 0
    for wl in DEFAULT_WATCHLISTS:
        existing = con.execute(
            "SELECT 1 FROM list_watchlists WHERE watchlist_id = ?",
            [wl["watchlist_id"]],
        ).fetchone()
        if existing:
            print(f"  [SKIP] {wl['watchlist_id']} existiert schon")
            continue
        con.execute(
            "INSERT INTO list_watchlists (watchlist_id, name, description, origin) VALUES (?, ?, ?, ?)",
            [wl["watchlist_id"], wl["name"], wl["description"], wl["origin"]],
        )
        new_count += 1
        print(f"  [NEW]  {wl['watchlist_id']:24s}  {wl['name']}  ({wl['origin']})")
    print(f"==> done: {new_count} neue Listen, {len(DEFAULT_WATCHLISTS)-new_count} bereits vorhanden")
    return 0


def cmd_lists(con: duckdb.DuckDBPyConnection, args) -> int:
    rows = con.execute(
        """
        SELECT
            w.watchlist_id, w.name, w.origin, w.active,
            COUNT(m.ref_instrument_id) AS members,
            w.created_at
        FROM list_watchlists w
        LEFT JOIN list_watchlist_members m ON m.watchlist_id = w.watchlist_id
        GROUP BY w.watchlist_id, w.name, w.origin, w.active, w.created_at
        ORDER BY w.watchlist_id
        """
    ).fetchall()
    if not rows:
        print("Keine Watchlists. 'init' laufen lassen.")
        return 0
    print(f"{'watchlist_id':<26s} {'name':<24s} {'origin':<8s} {'active':<6s} {'members':>8s}")
    for r in rows:
        print(f"{r[0]:<26s} {r[1]:<24s} {r[2]:<8s} {str(r[3]):<6s} {r[4]:>8d}")
    return 0


def cmd_show(con: duckdb.DuckDBPyConnection, args) -> int:
    wl_id = args.watchlist_id
    wl = con.execute("SELECT name, origin FROM list_watchlists WHERE watchlist_id = ?", [wl_id]).fetchone()
    if not wl:
        print(f"FEHLER: Watchlist '{wl_id}' existiert nicht. Verfuegbar:", file=sys.stderr)
        for r in con.execute("SELECT watchlist_id FROM list_watchlists ORDER BY watchlist_id").fetchall():
            print(f"  {r[0]}", file=sys.stderr)
        return 64

    rows = con.execute(
        """
        SELECT m.ref_instrument_id, r.symbol, r.name, r.asset_type, r.currency,
               m.added_at, m.added_by, m.notes
        FROM list_watchlist_members m
        LEFT JOIN ref_instruments r ON r.ref_instrument_id = m.ref_instrument_id
        WHERE m.watchlist_id = ?
        ORDER BY r.symbol, m.ref_instrument_id
        """,
        [wl_id],
    ).fetchall()
    print(f"==> Watchlist '{wl_id}'  ({wl[0]}, origin={wl[1]})")
    print(f"    {len(rows)} members")
    if not rows:
        return 0
    print()
    print(f"{'ref_instrument_id':<28s} {'symbol':<10s} {'type':<8s} {'ccy':<4s}  {'added':<19s} {'by':<8s}  {'notes'}")
    for r in rows:
        ts_s = r[5].strftime("%Y-%m-%d %H:%M:%S") if r[5] else ""
        print(f"{r[0]:<28s} {(r[1] or '—'):<10s} {(r[3] or ''):<8s} {(r[4] or ''):<4s}  {ts_s:<19s} {(r[6] or ''):<8s}  {r[7] or ''}")
    return 0


def cmd_add(con: duckdb.DuckDBPyConnection, args) -> int:
    rid = args.ref_instrument_id
    wl_id = args.watchlist_id

    # Validate watchlist exists
    if not con.execute("SELECT 1 FROM list_watchlists WHERE watchlist_id = ?", [wl_id]).fetchone():
        print(f"FEHLER: Watchlist '{wl_id}' existiert nicht. 'init' oder 'lists' pruefen.", file=sys.stderr)
        return 64

    # Validate instrument exists (warn but allow — User koennte Watchlist VOR portfolio-import pflegen)
    if not con.execute("SELECT 1 FROM ref_instruments WHERE ref_instrument_id = ?", [rid]).fetchone():
        print(f"[WARN] ref_instrument_id '{rid}' existiert nicht in ref_instruments.")
        print(f"       Watchlist-Eintrag wird trotzdem geschrieben (kommt vielleicht spaeter via portfolio-import).")

    existing = con.execute(
        "SELECT added_at FROM list_watchlist_members WHERE watchlist_id = ? AND ref_instrument_id = ?",
        [wl_id, rid],
    ).fetchone()
    if existing:
        print(f"[INFO] {rid} ist bereits auf '{wl_id}' (seit {existing[0]}). Skip.")
        return 0

    con.execute(
        """
        INSERT INTO list_watchlist_members (watchlist_id, ref_instrument_id, added_by, notes)
        VALUES (?, ?, 'cli', ?)
        """,
        [wl_id, rid, args.notes],
    )
    print(f"==> {rid} zur Watchlist '{wl_id}' hinzugefuegt.")
    return 0


def cmd_remove(con: duckdb.DuckDBPyConnection, args) -> int:
    rid = args.ref_instrument_id
    wl_id = args.watchlist_id
    deleted = con.execute(
        "DELETE FROM list_watchlist_members WHERE watchlist_id = ? AND ref_instrument_id = ? RETURNING 1",
        [wl_id, rid],
    ).fetchall()
    if not deleted:
        print(f"[INFO] {rid} war NICHT auf '{wl_id}'. Nichts zu tun.")
        return 0
    print(f"==> {rid} von Watchlist '{wl_id}' entfernt.")
    return 0


def cmd_where(con: duckdb.DuckDBPyConnection, args) -> int:
    rid = args.ref_instrument_id
    rows = con.execute(
        """
        SELECT m.watchlist_id, w.name, m.added_at, m.added_by, m.notes
        FROM list_watchlist_members m
        JOIN list_watchlists w ON w.watchlist_id = m.watchlist_id
        WHERE m.ref_instrument_id = ?
        ORDER BY m.added_at
        """,
        [rid],
    ).fetchall()

    # Plus check ob im Portfolio
    in_portfolio = con.execute(
        "SELECT count(*) FROM pos_holdings WHERE ref_instrument_id = ?",
        [rid],
    ).fetchone()[0]

    print(f"==> {rid}")
    if in_portfolio:
        print(f"    PORTFOLIO: ja ({in_portfolio} lots)")
    else:
        print(f"    PORTFOLIO: nein")
    if not rows:
        print(f"    WATCHLISTS: keine")
        return 0
    print(f"    WATCHLISTS:")
    for r in rows:
        ts_s = r[2].strftime("%Y-%m-%d") if r[2] else ""
        print(f"      - {r[0]:<26s} ({r[1]})  added {ts_s} by {r[3] or '?'}  {r[4] or ''}")
    return 0


def cmd_find(con: duckdb.DuckDBPyConnection, args) -> int:
    pattern = args.symbol_pattern.upper()
    rows = con.execute(
        """
        SELECT ref_instrument_id, symbol, name, asset_type, currency, exchange
        FROM ref_instruments
        WHERE upper(symbol) LIKE ?
           OR upper(ref_instrument_id) LIKE ?
        ORDER BY symbol
        LIMIT 30
        """,
        [f"%{pattern}%", f"%{pattern}%"],
    ).fetchall()
    if not rows:
        print(f"Kein Match fuer '{args.symbol_pattern}'.")
        return 0
    print(f"==> {len(rows)} match(es) fuer '{args.symbol_pattern}':")
    print(f"{'ref_instrument_id':<28s} {'symbol':<12s} {'type':<8s} {'ccy':<4s} {'exch':<10s} name")
    for r in rows:
        print(f"{r[0]:<28s} {(r[1] or '—'):<12s} {(r[3] or ''):<8s} {(r[4] or ''):<4s} {(r[5] or ''):<10s} {r[2] or ''}")
    return 0


def cmd_relevant(con: duckdb.DuckDBPyConnection, args) -> int:
    rows = con.execute(
        """
        SELECT v.ref_instrument_id, r.symbol, r.asset_type,
               STRING_AGG(DISTINCT v.source, ',' ORDER BY v.source) AS sources
        FROM v_relevant_instruments v
        LEFT JOIN ref_instruments r ON r.ref_instrument_id = v.ref_instrument_id
        GROUP BY v.ref_instrument_id, r.symbol, r.asset_type
        ORDER BY r.symbol, v.ref_instrument_id
        """
    ).fetchall()
    print(f"==> v_relevant_instruments — {len(rows)} unique instruments")
    if not rows:
        return 0
    print(f"{'ref_instrument_id':<28s} {'symbol':<12s} {'type':<8s}  sources")
    for r in rows:
        print(f"{r[0]:<28s} {(r[1] or '—'):<12s} {(r[2] or ''):<8s}  {r[3]}")
    return 0


# ---------- Main ----------

def main() -> int:
    parser = argparse.ArgumentParser(description="nova-lab watchlist CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Default-Listen anlegen")
    sub.add_parser("lists", help="Alle Watchlists + member-counts")

    p_show = sub.add_parser("show", help="Members einer Watchlist")
    p_show.add_argument("watchlist_id")

    p_add = sub.add_parser("add", help="Add instrument to watchlist")
    p_add.add_argument("ref_instrument_id")
    p_add.add_argument("--to", dest="watchlist_id", required=True, help="Target watchlist_id")
    p_add.add_argument("--notes", default=None)

    p_rm = sub.add_parser("remove", help="Remove instrument from watchlist")
    p_rm.add_argument("ref_instrument_id")
    p_rm.add_argument("--from", dest="watchlist_id", required=True, help="Source watchlist_id")

    p_where = sub.add_parser("where", help="Welche Listen enthalten dieses Instrument")
    p_where.add_argument("ref_instrument_id")

    p_find = sub.add_parser("find", help="ref_instrument_id-Suche per Symbol-Pattern")
    p_find.add_argument("symbol_pattern")

    sub.add_parser("relevant", help="v_relevant_instruments-View dump (Portfolio + alle Watchlists)")

    args = parser.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Read-only-Subcommands: keine writes -> erlaubt parallele Daemons/Writer.
    # Schema-Setup nur im write-mode, weil CREATE-Statements brauchen R/W.
    READ_ONLY_CMDS = {"lists", "show", "where", "find", "relevant"}
    is_ro = args.cmd in READ_ONLY_CMDS
    con = duckdb.connect(str(DB_PATH), read_only=is_ro)
    try:
        if not is_ro:
            ensure_schema(con)
        dispatcher = {
            "init":     cmd_init,
            "lists":    cmd_lists,
            "show":     cmd_show,
            "add":      cmd_add,
            "remove":   cmd_remove,
            "where":    cmd_where,
            "find":     cmd_find,
            "relevant": cmd_relevant,
        }
        return dispatcher[args.cmd](con, args)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
