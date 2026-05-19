"""nova-lab instruments CLI — Stammdaten-Pflege fuer ref_instruments.

Wiederverwendet IBResolver + make_ref_instrument_id aus modules/portfolio.
KEINE Schema-Aenderungen — ref_instruments existiert bereits.

Subcommands:
    add --conid <id> [--asset-class STK|ETF|BOND|...]      IB-resolve via ConID + insert
    add --symbol <s> --exchange <e> --currency <c>          Legacy IB-resolve via symbol + insert
        [--isin <isin>]                                     ISIN-Disambiguierung wenn IB mehrere matches liefert
    show <ref_instrument_id>                                 Voller record
    list [--active|--inactive] [--asset-class <c>]          Tabelle, optional gefiltert
         [--currency <c>] [--source <src>]
    find <pattern>                                           ref_instrument_id-Suche per Symbol-Pattern
    update <ref_instrument_id> [--active true|false]        Metadaten-Update
        [--asset-class <c>] [--name "..."] [--notes "..."]

Beispiele:
    python -m modules.instruments add --conid 4815747
    python -m modules.instruments add --symbol VIX --exchange CBOE --currency USD
    python -m modules.instruments find AAPL
    python -m modules.instruments show IB:AAPL:USD
    python -m modules.instruments list --asset-class etf
    python -m modules.instruments update IB:QQQ:USD --asset-class etf
    python -m modules.instruments update IB:OLD:USD --active false

NICHT moeglich (per Design):
    - update --preferred-source (PK-Komponente, wuerde ref_instrument_id aendern)
    - update --symbol / --currency / --con_id (IB-master-keys, nicht editierbar)
    - delete (ein anderes Tool fuer zukunft, mit cascade-protection)
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

import duckdb

from ..portfolio._ib_resolver import IBResolver, ResolvedContract, SECTYPE_MAP
from ..portfolio.import_xlsx import (
    ASSET_TYPE_TO_CLASS,
    VALID_ASSET_CLASSES,
    make_ref_instrument_id,
)


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
PREFERRED_SOURCE = "IB"  # Konsistent mit portfolio.import_xlsx


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """ref_instruments lebt im ingest-Schema."""
    ingest_dir = pathlib.Path(__file__).parent.parent / "ingest" / "sql"
    if ingest_dir.is_dir():
        for f in sorted(ingest_dir.glob("0*.sql")):
            con.execute(f.read_text())


# ---------- ADD ----------

def _insert_or_warn(con: duckdb.DuckDBPyConnection, resolved: ResolvedContract,
                    user_class: str | None) -> int:
    """Insert ref_instruments aus ResolvedContract. 0=eingefuegt, 1=schon-da."""
    # Final asset_type: User-Override (asset_class IB-style) > IB-resolved
    if user_class:
        final_asset_type = SECTYPE_MAP.get(user_class.upper(), user_class.lower())
    else:
        final_asset_type = resolved.asset_type or "stock"

    final_currency = resolved.currency
    if not (resolved.symbol and final_currency):
        print(f"FEHLER: IB-resolve unvollstaendig (symbol={resolved.symbol!r}, currency={final_currency!r}).", file=sys.stderr)
        print(f"       Bei Bonds: --symbol = IB localSymbol (z.B. IBCID...) + --currency explizit setzen.", file=sys.stderr)
        return 2

    rid = make_ref_instrument_id(resolved.symbol, final_currency, PREFERRED_SOURCE)

    existing = con.execute(
        "SELECT 1 FROM ref_instruments WHERE ref_instrument_id = ?", [rid]
    ).fetchone()
    if existing:
        print(f"[INFO] {rid} existiert schon. Zum Aendern: 'update {rid} --asset-class ... --notes ...'")
        return 1

    con.execute(
        """
        INSERT INTO ref_instruments
            (ref_instrument_id, con_id, isin, symbol, currency, preferred_source,
             name, asset_type, exchange, active, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, true, 'added via instruments CLI')
        """,
        [
            rid, resolved.con_id, resolved.isin, resolved.symbol, final_currency,
            PREFERRED_SOURCE, resolved.name or resolved.symbol, final_asset_type,
            resolved.exchange or "",
        ],
    )
    print(f"==> {rid} hinzugefuegt:")
    print(f"    symbol     : {resolved.symbol}")
    print(f"    currency   : {final_currency}")
    print(f"    exchange   : {resolved.exchange or '—'}")
    print(f"    asset_type : {final_asset_type}")
    print(f"    con_id     : {resolved.con_id}")
    print(f"    isin       : {resolved.isin or '—'}")
    print(f"    name       : {resolved.name or '—'}")
    return 0


def cmd_add(con: duckdb.DuckDBPyConnection, args) -> int:
    if args.asset_class and args.asset_class.upper() not in VALID_ASSET_CLASSES:
        print(f"FEHLER: --asset-class '{args.asset_class}' ungueltig. Erlaubt: {sorted(VALID_ASSET_CLASSES)}", file=sys.stderr)
        return 64

    print(f"==> IB connect ...")
    try:
        with IBResolver() as ib:
            print(f"    client_id={ib.client_id} host={ib.host} port={ib.port}")
            if args.conid is not None:
                print(f"    resolve_by_conid {args.conid}")
                resolved = ib.resolve_by_conid(
                    args.conid,
                    sec_type=args.asset_class,
                    currency=args.currency,
                )
            else:
                print(f"    resolve_by_symbol {args.symbol}@{args.exchange or '?'} {args.currency or '?'}")
                resolved = ib.resolve_by_symbol(
                    symbol=args.symbol,
                    exchange=args.exchange,
                    currency=args.currency,
                    isin=args.isin,
                )
    except Exception as e:  # noqa: BLE001
        print(f"FEHLER: IB-Connect/Resolve: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1

    if resolved is None:
        print(f"FEHLER: IB hat keinen passenden Contract gefunden.", file=sys.stderr)
        return 1

    print(f"    [OK] resolved: {resolved.symbol or '—'} @ {resolved.exchange or 'SMART'} ({resolved.currency or '—'}) — {resolved.asset_type or 'stock'}")
    return _insert_or_warn(con, resolved, args.asset_class)


# ---------- SHOW ----------

def cmd_show(con: duckdb.DuckDBPyConnection, args) -> int:
    rid = args.ref_instrument_id
    row = con.execute(
        """
        SELECT ref_instrument_id, con_id, isin, symbol, currency, preferred_source,
               name, asset_type, exchange, active, notes, created_at, updated_at
        FROM ref_instruments WHERE ref_instrument_id = ?
        """,
        [rid],
    ).fetchone()
    if not row:
        print(f"FEHLER: {rid} nicht gefunden.", file=sys.stderr)
        return 64

    fields = [
        "ref_instrument_id", "con_id", "isin", "symbol", "currency", "preferred_source",
        "name", "asset_type", "exchange", "active", "notes", "created_at", "updated_at",
    ]
    print(f"==> {rid}")
    for k, v in zip(fields, row):
        print(f"    {k:<18s}: {v if v is not None else '—'}")

    # Plus: cross-references
    n_holdings = con.execute("SELECT count(*) FROM pos_holdings WHERE ref_instrument_id = ?", [rid]).fetchone()[0]
    n_quotes   = con.execute("SELECT count(*) FROM mkt_quotes_daily WHERE ref_instrument_id = ?", [rid]).fetchone()[0]
    try:
        watchlists = con.execute(
            "SELECT watchlist_id FROM list_watchlist_members WHERE ref_instrument_id = ? ORDER BY watchlist_id",
            [rid],
        ).fetchall()
    except duckdb.CatalogException:
        watchlists = []
    print()
    print(f"    cross-refs:")
    print(f"      pos_holdings    : {n_holdings} lots")
    print(f"      mkt_quotes_daily: {n_quotes} rows")
    print(f"      watchlists      : {', '.join(w[0] for w in watchlists) if watchlists else '—'}")
    return 0


# ---------- LIST ----------

def cmd_list(con: duckdb.DuckDBPyConnection, args) -> int:
    where = []
    params: list = []
    if args.active is True:
        where.append("active = true")
    elif args.active is False:
        where.append("active = false")
    if args.asset_class:
        where.append("lower(asset_type) = ?")
        params.append(args.asset_class.lower())
    if args.currency:
        where.append("upper(currency) = ?")
        params.append(args.currency.upper())
    if args.source:
        where.append("upper(preferred_source) = ?")
        params.append(args.source.upper())

    sql = """
        SELECT ref_instrument_id, symbol, asset_type, currency, exchange, active, con_id, isin
        FROM ref_instruments
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY symbol, ref_instrument_id"

    rows = con.execute(sql, params).fetchall()
    print(f"==> {len(rows)} instrumente" + (f" (filter: {' / '.join(where)})" if where else ""))
    if not rows:
        return 0
    print(f"{'ref_instrument_id':<28s} {'symbol':<10s} {'type':<8s} {'ccy':<4s} {'exch':<10s} {'active':<6s} {'con_id':>10s}  {'isin':<14s}")
    for r in rows:
        print(f"{r[0]:<28s} {(r[1] or '—'):<10s} {(r[2] or ''):<8s} {(r[3] or ''):<4s} {(r[4] or ''):<10s} {str(r[5]):<6s} {(str(r[6]) if r[6] else '—'):>10s}  {(r[7] or '—'):<14s}")
    return 0


# ---------- FIND ----------

def cmd_find(con: duckdb.DuckDBPyConnection, args) -> int:
    pattern = args.symbol_pattern.upper()
    rows = con.execute(
        """
        SELECT ref_instrument_id, symbol, name, asset_type, currency, exchange
        FROM ref_instruments
        WHERE upper(symbol) LIKE ? OR upper(ref_instrument_id) LIKE ? OR upper(coalesce(name,'')) LIKE ?
        ORDER BY symbol
        LIMIT 30
        """,
        [f"%{pattern}%", f"%{pattern}%", f"%{pattern}%"],
    ).fetchall()
    if not rows:
        print(f"Kein Match fuer '{args.symbol_pattern}'.")
        return 0
    print(f"==> {len(rows)} match(es) fuer '{args.symbol_pattern}':")
    print(f"{'ref_instrument_id':<28s} {'symbol':<12s} {'type':<8s} {'ccy':<4s} {'exch':<10s} name")
    for r in rows:
        print(f"{r[0]:<28s} {(r[1] or '—'):<12s} {(r[3] or ''):<8s} {(r[4] or ''):<4s} {(r[5] or ''):<10s} {r[2] or ''}")
    return 0


# ---------- UPDATE ----------

def cmd_update(con: duckdb.DuckDBPyConnection, args) -> int:
    rid = args.ref_instrument_id

    existing = con.execute("SELECT active, asset_type, name, notes FROM ref_instruments WHERE ref_instrument_id = ?", [rid]).fetchone()
    if not existing:
        print(f"FEHLER: {rid} nicht gefunden.", file=sys.stderr)
        return 64

    sets: list[str] = []
    params: list = []
    changes: list[str] = []

    if args.active is not None:
        sets.append("active = ?")
        params.append(args.active)
        changes.append(f"active: {existing[0]} -> {args.active}")

    if args.asset_class is not None:
        ac = args.asset_class.upper()
        if ac not in VALID_ASSET_CLASSES:
            print(f"FEHLER: --asset-class '{ac}' ungueltig. Erlaubt: {sorted(VALID_ASSET_CLASSES)}", file=sys.stderr)
            return 64
        new_atype = SECTYPE_MAP.get(ac, ac.lower())
        sets.append("asset_type = ?")
        params.append(new_atype)
        changes.append(f"asset_type: {existing[1]} -> {new_atype}  (asset_class={ac})")

    if args.name is not None:
        sets.append("name = ?")
        params.append(args.name)
        changes.append(f"name: {existing[2]!r} -> {args.name!r}")

    if args.notes is not None:
        sets.append("notes = ?")
        params.append(args.notes)
        changes.append(f"notes: {existing[3]!r} -> {args.notes!r}")

    if not sets:
        print("FEHLER: nichts zu aendern. Mindestens ein --field=value angeben.", file=sys.stderr)
        return 64

    sets.append("updated_at = current_timestamp")
    params.append(rid)

    con.execute(
        f"UPDATE ref_instruments SET {', '.join(sets)} WHERE ref_instrument_id = ?",
        params,
    )
    print(f"==> {rid} updated:")
    for c in changes:
        print(f"    {c}")
    return 0


# ---------- Main ----------

def _str_to_bool(s: str) -> bool:
    if s.lower() in ("true", "1", "yes", "y", "on", "ja"):
        return True
    if s.lower() in ("false", "0", "no", "n", "off", "nein"):
        return False
    raise argparse.ArgumentTypeError(f"erwarte true/false, got {s!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="nova-lab instruments CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # add
    p_add = sub.add_parser("add", help="Neues Instrument anlegen via IB-resolve")
    p_add.add_argument("--conid", type=int, help="IB ConID (sauberster Weg)")
    p_add.add_argument("--symbol", help="Symbol fuer legacy resolve_by_symbol")
    p_add.add_argument("--exchange", help="Exchange (XETRA, NASDAQ, ...) — empfohlen mit --symbol")
    p_add.add_argument("--currency", help="Currency (USD, EUR, ...) — empfohlen mit --symbol")
    p_add.add_argument("--isin", help="Optional ISIN fuer Disambiguierung")
    p_add.add_argument("--asset-class", dest="asset_class",
                       help=f"Optional IB-secType-Hint (STK, ETF, BOND, ...). Erlaubt: {sorted(VALID_ASSET_CLASSES)}")

    # show
    p_show = sub.add_parser("show", help="Voller record + cross-refs")
    p_show.add_argument("ref_instrument_id")

    # list
    p_list = sub.add_parser("list", help="Tabelle aller Instrumente, optional gefiltert")
    p_list.add_argument("--active", dest="active", action="store_const", const=True, default=None,
                        help="Nur aktive (default: alle)")
    p_list.add_argument("--inactive", dest="active", action="store_const", const=False,
                        help="Nur inaktive")
    p_list.add_argument("--asset-class", dest="asset_class", help="Filter (intern: stock/etf/bond/...)")
    p_list.add_argument("--currency", help="Filter")
    p_list.add_argument("--source", help="Filter preferred_source")

    # find
    p_find = sub.add_parser("find", help="Symbol/ID/name-Pattern-Suche")
    p_find.add_argument("symbol_pattern")

    # update
    p_upd = sub.add_parser("update", help="Metadaten editieren (active/asset-class/name/notes)")
    p_upd.add_argument("ref_instrument_id")
    p_upd.add_argument("--active", type=_str_to_bool, default=None,
                       help="true/false — toggle ingest-eligibility")
    p_upd.add_argument("--asset-class", dest="asset_class",
                       help=f"Override IB-classification. Erlaubt: {sorted(VALID_ASSET_CLASSES)}")
    p_upd.add_argument("--name", help="Override longName")
    p_upd.add_argument("--notes", help="Freitext")

    args = parser.parse_args()

    # Validate add args
    if args.cmd == "add":
        if args.conid is None and not args.symbol:
            parser.error("add braucht entweder --conid ODER --symbol")
        if args.conid is None and not (args.exchange and args.currency):
            parser.error("add --symbol braucht zusaetzlich --exchange + --currency")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Read-only-Subcommands: show/list/find. Add/update brauchen R/W.
    READ_ONLY_CMDS = {"show", "list", "find"}
    is_ro = args.cmd in READ_ONLY_CMDS
    con = duckdb.connect(str(DB_PATH), read_only=is_ro)
    try:
        if not is_ro:
            ensure_schema(con)
        dispatcher = {
            "add":    cmd_add,
            "show":   cmd_show,
            "list":   cmd_list,
            "find":   cmd_find,
            "update": cmd_update,
        }
        return dispatcher[args.cmd](con, args)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
