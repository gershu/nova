"""nova-lab news_sa CLI — Seeking-Alpha-Mails via Gmail-IMAP einlesen.

Subcommands:
    init               Schema applizieren (idempotent)
    fetch              IMAP-Pull der nova-sa Mails -> ref_sa_articles
                       + verschiebt verarbeitete Mails nach nova-sa/processed
    show <symbol>      Letzte N SA-Artikel die ein Symbol erwaehnen
                       (--symbol kann ref_instrument_id ODER Plain-Ticker sein)
    list-recent        Top-N juengste Artikel insgesamt
    link               Manuelles symbol-mapping fuer einen Artikel

ENV erforderlich:
    GMAIL_IMAP_HOST     default imap.gmail.com
    GMAIL_IMAP_USER     deine.adresse@gmail.com
    GMAIL_IMAP_PASSWORD 16-Char-App-Password (myaccount.google.com -> Security)
    GMAIL_SA_LABEL      default 'nova-sa'

Beispiele:
    python -m modules.news_sa init
    python -m modules.news_sa fetch
    python -m modules.news_sa show AAPL
    python -m modules.news_sa list-recent --limit 20
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import uuid
from datetime import datetime, timezone

import duckdb

from .imap_adapter import GmailSAClient, ImapConfig, ParsedArticle


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SCHEMA_FILE = pathlib.Path(__file__).parent / "sql" / "0001_news_sa.sql"


def _ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA_FILE.read_text())


def _resolve_symbol_to_ref_ids(con: duckdb.DuckDBPyConnection, symbol: str) -> list[str]:
    """Mappet Plain-Ticker (AAPL) auf alle passenden ref_instrument_ids.

    Wenn symbol bereits im 'IB:X:Y'-Format ist, lookup direkt; sonst
    fuzzy-match auf ref_instruments.symbol.
    """
    if ":" in symbol:
        rows = con.execute(
            "SELECT ref_instrument_id FROM ref_instruments WHERE ref_instrument_id = ?",
            [symbol],
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT ref_instrument_id FROM ref_instruments WHERE symbol = ?",
            [symbol.upper()],
        ).fetchall()
    return [r[0] for r in rows]


# ---------- init ----------

def cmd_init(args) -> int:
    con = duckdb.connect(str(DB_PATH))
    try:
        _ensure_schema(con)
        print(f"==> Schema 0001_news_sa applizziert in {DB_PATH}")
        return 0
    finally:
        con.close()


# ---------- fetch ----------

def cmd_fetch(args) -> int:
    cfg = ImapConfig.from_env()
    run_id = os.environ.get(
        "NOVA_JOB_ID",
        f"adhoc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    )

    con = duckdb.connect(str(DB_PATH))
    try:
        _ensure_schema(con)

        # Audit-Start
        con.execute("""
            INSERT INTO audit_news_sa_runs (run_id, started_at, status)
            VALUES (?, current_timestamp, 'running')
            ON CONFLICT (run_id) DO NOTHING
        """, [run_id])

        print(f"==> nova-lab news_sa fetch  (label='{cfg.label}', user={cfg.user})")
        n_seen = n_parsed = n_moved = n_failed = 0
        processed_uids: list[str] = []
        try:
            with GmailSAClient(cfg) as client:
                uids = client.list_uids()
                n_seen = len(uids)
                print(f"    Mails im '{cfg.label}'-Label: {n_seen}")
                if not uids:
                    print(f"    Nichts zu tun.")
                    con.execute("""
                        UPDATE audit_news_sa_runs
                        SET completed_at=current_timestamp,
                            mails_seen=0, mails_parsed=0, mails_moved=0, mails_failed=0,
                            status='success'
                        WHERE run_id = ?
                    """, [run_id])
                    return 0

                for uid in uids:
                    art: ParsedArticle | None = None
                    try:
                        art = client.fetch_article(uid)
                    except Exception as e:  # noqa: BLE001
                        n_failed += 1
                        print(f"    [fail uid={uid}] parse-error: {e.__class__.__name__}: {e}")
                        continue
                    if art is None:
                        n_failed += 1
                        print(f"    [fail uid={uid}] no article")
                        continue
                    _persist_article(con, art, run_id)
                    processed_uids.append(uid)
                    n_parsed += 1
                    syms = ", ".join(s for s, _ in art.symbols) or "-"
                    print(f"    [{art.ts:%Y-%m-%d %H:%M}] {art.title[:70]:<70s} symbols={syms}")

                # Move to processed AFTER all DB writes — sodass bei DB-Fail Mails im Label bleiben
                if processed_uids:
                    n_moved = client.move_to_processed(processed_uids)
                    print(f"    -> {n_moved} Mails verschoben in '{cfg.processed_label}'")

        except ConnectionError as e:
            con.execute("""
                UPDATE audit_news_sa_runs
                SET completed_at=current_timestamp, status='failed', error_msg=?
                WHERE run_id = ?
            """, [str(e)[:500], run_id])
            print(f"FEHLER: {e}", file=sys.stderr)
            return 64

        # Audit-End
        status = "success" if n_failed == 0 else ("partial" if n_parsed > 0 else "failed")
        con.execute("""
            UPDATE audit_news_sa_runs
            SET completed_at=current_timestamp,
                mails_seen=?, mails_parsed=?, mails_moved=?, mails_failed=?, status=?
            WHERE run_id = ?
        """, [n_seen, n_parsed, n_moved, n_failed, status, run_id])

        print()
        print(f"==> Done. seen={n_seen} parsed={n_parsed} moved={n_moved} failed={n_failed}")
        return 0
    finally:
        con.close()


def _persist_article(con: duckdb.DuckDBPyConnection, art: ParsedArticle, run_id: str) -> None:
    """Upserts article + symbols. Idempotent via PK."""
    con.execute("""
        INSERT OR REPLACE INTO ref_sa_articles
            (article_id, source, ts, title, summary, url,
             imap_uid, raw_subject, raw_from, run_id)
        VALUES (?, 'seekingalpha', ?, ?, ?, ?, ?, ?, ?, ?)
    """, [art.article_id, art.ts, art.title, art.summary, art.url,
          art.imap_uid, art.raw_subject, art.raw_from, run_id])

    # Symbol-Mapping: ticker -> ref_instrument_id. Wenn unbekannt -> skip
    # (ref_instruments-Lookup ist der finale False-Positive-Filter — Acronyme
    # wie ETF/USA werden hier rausgefiltert weil sie kein Instrument haben).
    _CONF = {
        "subject":       1.00,   # (NYSE:AAPL) im subject — eindeutig
        "body":          0.90,   # (NYSE:AAPL) oder /symbol/AAPL/ im body
        "subject_paren": 0.85,   # (AAPL) im subject — wahrscheinlich Ticker
        "body_paren":    0.75,   # (AAPL) im body nach Datum — kontextuell
        "manual":        1.00,
    }
    for sym, extracted_from in art.symbols:
        ref_ids = _resolve_symbol_to_ref_ids(con, sym)
        for rid in ref_ids:
            con.execute("""
                INSERT OR REPLACE INTO ref_sa_article_symbols
                    (article_id, ref_instrument_id, extracted_from, confidence)
                VALUES (?, ?, ?, ?)
            """, [art.article_id, rid, extracted_from,
                  _CONF.get(extracted_from, 0.7)])


# ---------- show ----------

def cmd_show(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        ref_ids = _resolve_symbol_to_ref_ids(con, args.symbol)
        if not ref_ids:
            print(f"Kein Instrument mit Symbol '{args.symbol}' in ref_instruments.")
            return 0

        placeholders = ",".join(["?"] * len(ref_ids))
        rows = con.execute(f"""
            SELECT a.ts, a.title, a.summary, a.url, a.article_id,
                   STRING_AGG(DISTINCT s.ref_instrument_id, ', ') AS matched_ids
            FROM ref_sa_articles a
            JOIN ref_sa_article_symbols s ON s.article_id = a.article_id
            WHERE s.ref_instrument_id IN ({placeholders})
            GROUP BY a.ts, a.title, a.summary, a.url, a.article_id
            ORDER BY a.ts DESC
            LIMIT ?
        """, [*ref_ids, args.limit]).fetchall()

        if not rows:
            print(f"Keine SA-Artikel die '{args.symbol}' erwaehnen.")
            return 0

        print(f"==> {len(rows)} SA-Artikel zu {args.symbol} ({ref_ids}):")
        print()
        for ts, title, summary, url, aid, matched in rows:
            print(f"  [{ts:%Y-%m-%d %H:%M}] {title}")
            print(f"     {url or '(no URL)'}")
            if matched and "," in matched:
                print(f"     also: {matched}")
            print(f"     {(summary or '')[:240]}{'...' if summary and len(summary) > 240 else ''}")
            print()
        return 0
    finally:
        con.close()


# ---------- list-recent ----------

def cmd_list_recent(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = con.execute("""
            SELECT a.ts, a.title, a.url,
                   STRING_AGG(DISTINCT i.symbol, ', ') AS symbols
            FROM ref_sa_articles a
            LEFT JOIN ref_sa_article_symbols s ON s.article_id = a.article_id
            LEFT JOIN ref_instruments       i ON i.ref_instrument_id = s.ref_instrument_id
            GROUP BY a.ts, a.title, a.url, a.article_id
            ORDER BY a.ts DESC
            LIMIT ?
        """, [args.limit]).fetchall()

        if not rows:
            print("Keine Artikel in ref_sa_articles. Erst `fetch` laufen lassen.")
            return 0

        for ts, title, url, syms in rows:
            sym_str = syms or "-"
            print(f"  [{ts:%Y-%m-%d %H:%M}] [{sym_str}]  {title}")
            if url:
                print(f"     {url}")
        return 0
    finally:
        con.close()


# ---------- link ----------

def cmd_link(args) -> int:
    """Manuelles Symbol-Mapping fuer einen Artikel.

    Use-Case: Auto-Extract hat Symbol nicht erkannt, Stefan macht es nachträglich.
    """
    con = duckdb.connect(str(DB_PATH))
    try:
        # Article muss existieren
        exists = con.execute("SELECT 1 FROM ref_sa_articles WHERE article_id = ?",
                              [args.article_id]).fetchone()
        if not exists:
            print(f"FEHLER: article_id '{args.article_id}' nicht gefunden.", file=sys.stderr)
            return 64

        ref_ids = _resolve_symbol_to_ref_ids(con, args.symbol)
        if not ref_ids:
            print(f"FEHLER: Symbol '{args.symbol}' nicht in ref_instruments.", file=sys.stderr)
            return 64

        for rid in ref_ids:
            con.execute("""
                INSERT OR REPLACE INTO ref_sa_article_symbols
                    (article_id, ref_instrument_id, extracted_from, confidence)
                VALUES (?, ?, 'manual', 1.0)
            """, [args.article_id, rid])
        print(f"==> {args.article_id} verknuepft mit {ref_ids}")
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Schema applizieren")
    sub.add_parser("fetch", help="IMAP-Pull der nova-sa Mails")

    p_show = sub.add_parser("show", help="Artikel die ein Symbol erwaehnen")
    p_show.add_argument("symbol", help="Plain-Ticker (AAPL) oder ref_instrument_id (IB:AAPL:USD)")
    p_show.add_argument("--limit", type=int, default=10)

    p_lr = sub.add_parser("list-recent", help="Top-N juengste Artikel")
    p_lr.add_argument("--limit", type=int, default=20)

    p_link = sub.add_parser("link", help="Manuelles Symbol-Mapping")
    p_link.add_argument("article_id")
    p_link.add_argument("symbol")

    args = p.parse_args()

    if args.cmd != "init" and not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    dispatch = {
        "init":        cmd_init,
        "fetch":       cmd_fetch,
        "show":        cmd_show,
        "list-recent": cmd_list_recent,
        "link":        cmd_link,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
