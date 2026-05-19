"""nova-lab Value-Screener — S&P 500 nach Value-Kriterien filtern.

Hybrid-Mode (Pfad 3 aus dem Beurteilungs-Doc):
  - filter:        weekly automatic via lab_screener_value daemon (Sonntag 22:30)
  - llm-deepdive:  on-demand, pro Symbol, fasst Fundamentals + News via LLM
  - show:          aktuelle Top-N anzeigen
  - init:          Universe-YAML -> ref_instruments + 'sp500_universe' Watchlist
                   (einmalig; lab_fundamentals daemon nimmt sich der dann automatisch
                   weekly an)

Konfig (3-Tier wie alle anderen Module):
  Tier 3 JSON (NOVA_PARAMS_FILE):
    {
      "min_roe":                0.10,
      "max_pe_ttm":             35.0,
      "min_fcf_yield":          0.02,
      "max_debt_to_equity":     2.5,
      "max_net_debt_to_ebitda": 5.0,
      "min_market_cap":         5000000000,
      "min_composite_score":    0.40,
      "min_revenue_cagr_5y":   -0.02,
      "min_operating_margin":   0.08,
      "sector_blacklist":       [],
      "top_n":                  30,
      "sector_diversification": true,
      "per_sector_cap":         4
    }

Beispiele:
  python -m modules.screener_value init
  python -m modules.screener_value filter
  python -m modules.screener_value filter --params-file ~/jobs/value_strict.json
  python -m modules.screener_value llm-deepdive IB:AAPL:USD
  python -m modules.screener_value show
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import sys
import uuid
from datetime import date, datetime, timezone

import duckdb

from .filter import FilterConfig, ScoredCandidate, evaluate, rank_candidates
from .universe import UniverseMember, load_universe, ref_instrument_id_for


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
OUTPUT_DIR = pathlib.Path.home() / "nova_output" / "lab_screener_value"
SCHEMA_FILE = pathlib.Path(__file__).parent / "sql" / "0001_value_screener.sql"
UNIVERSE_WATCHLIST = "sp500_universe"
PICKS_WATCHLIST = "value_picks"
ADDED_BY_TAG = "screener_value"

# Cross-Schema Pfade (fundamentals + watchlist Schemas — wir lesen daraus)
SCHEMA_DIRS = [
    pathlib.Path(__file__).parent.parent / "ingest"       / "sql",
    pathlib.Path(__file__).parent.parent / "portfolio"    / "sql",
    pathlib.Path(__file__).parent.parent / "watchlist"    / "sql",
    pathlib.Path(__file__).parent.parent / "fundamentals" / "sql",
    pathlib.Path(__file__).parent                          / "sql",
]


def _ensure_schemas(con: duckdb.DuckDBPyConnection) -> None:
    """Applies all dependent schemas idempotent.

    Glob '0*.sql' bewusst — excludes legacy seed_*.sql files (e.g. ingest/sql/
    seed_symbols.sql ist von der alten pre-B-Phase 'symbols'-Tabelle).
    """
    for d in SCHEMA_DIRS:
        if not d.is_dir():
            continue
        for sql_file in sorted(d.glob("0*.sql")):
            con.execute(sql_file.read_text())


def _load_params(args) -> dict:
    pf = getattr(args, "params_file", None)
    if not pf:
        return {}
    p = pathlib.Path(pf)
    if not p.is_file():
        print(f"FEHLER: --params-file {p} nicht gefunden.", file=sys.stderr)
        sys.exit(64)
    return json.loads(p.read_text())


# ---------- init ----------

def cmd_init(args) -> int:
    """Universe-YAML -> ref_instruments + sp500_universe Watchlist."""
    members = load_universe(args.universe_yaml)
    print(f"==> Universe geladen: {len(members)} Symbole")

    con = duckdb.connect(str(DB_PATH))
    try:
        _ensure_schemas(con)

        # 1. Watchlist anlegen
        con.execute("""
            INSERT INTO list_watchlists (watchlist_id, name, description, origin)
            VALUES (?, ?, ?, 'system')
            ON CONFLICT (watchlist_id) DO NOTHING
        """, [UNIVERSE_WATCHLIST, "S&P 500 Universe",
              "Statisch aus config/universe_sp500.yaml — Pflege manuell."])

        # 2. ref_instruments + watchlist_members
        n_new_inst, n_new_member = 0, 0
        for m in members:
            rid = ref_instrument_id_for(m)
            existed = con.execute(
                "SELECT 1 FROM ref_instruments WHERE ref_instrument_id = ?", [rid]
            ).fetchone()
            if not existed:
                con.execute("""
                    INSERT INTO ref_instruments
                        (ref_instrument_id, symbol, currency, name, asset_type,
                         preferred_source, exchange, active, notes)
                    VALUES (?, ?, ?, ?, 'stock', 'yfinance', 'NASDAQ/NYSE', TRUE, ?)
                """, [rid, m.symbol, m.currency, m.name,
                      f"auto-added from {UNIVERSE_WATCHLIST}; sector={m.sector or '-'}"])
                n_new_inst += 1

            existed_m = con.execute("""
                SELECT 1 FROM list_watchlist_members
                WHERE watchlist_id = ? AND ref_instrument_id = ?
            """, [UNIVERSE_WATCHLIST, rid]).fetchone()
            if not existed_m:
                con.execute("""
                    INSERT INTO list_watchlist_members
                        (watchlist_id, ref_instrument_id, added_by, notes)
                    VALUES (?, ?, ?, ?)
                """, [UNIVERSE_WATCHLIST, rid, ADDED_BY_TAG,
                      f"sector={m.sector or '-'}; name={m.name}"])
                n_new_member += 1

        print(f"    ref_instruments     : +{n_new_inst} neu")
        print(f"    watchlist members   : +{n_new_member} neu (in {UNIVERSE_WATCHLIST})")

        # picks-Watchlist auch anlegen (leer, wird vom filter befuellt)
        con.execute("""
            INSERT INTO list_watchlists (watchlist_id, name, description, origin)
            VALUES (?, ?, ?, 'system')
            ON CONFLICT (watchlist_id) DO NOTHING
        """, [PICKS_WATCHLIST, "Value-Screener Top-Picks",
              "Wochentlich befuellt von screener_value filter."])

        print(f"    {PICKS_WATCHLIST} watchlist sichergestellt.")
        print()
        print("==> Next steps:")
        print(f"    1. lab_fundamentals refresh-all  (jetzt oder am Sonntag-Daemon)")
        print(f"    2. screener_value filter         (nach Fundamentals)")
        return 0
    finally:
        con.close()


# ---------- filter ----------

def cmd_filter(args) -> int:
    params = _load_params(args)

    cfg = FilterConfig(
        min_roe                = params.get("min_roe", 0.10),
        min_operating_margin   = params.get("min_operating_margin", 0.08),
        min_revenue_cagr_5y    = params.get("min_revenue_cagr_5y", -0.02),
        max_pe_ttm             = params.get("max_pe_ttm", 35.0),
        min_fcf_yield          = params.get("min_fcf_yield", 0.02),
        max_debt_to_equity     = params.get("max_debt_to_equity", 2.5),
        max_net_debt_to_ebitda = params.get("max_net_debt_to_ebitda", 5.0),
        min_market_cap         = params.get("min_market_cap", 5_000_000_000),
        min_composite_score    = params.get("min_composite_score", 0.40),
        sector_blacklist       = list(params.get("sector_blacklist", [])),
    )
    top_n         = int(params.get("top_n", 30))
    sector_diverse = bool(params.get("sector_diversification", True))
    per_sector_cap = int(params.get("per_sector_cap", 4))

    run_id = os.environ.get(
        "NOVA_JOB_ID",
        f"adhoc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    )

    con = duckdb.connect(str(DB_PATH))
    try:
        _ensure_schemas(con)

        # Audit-Start
        con.execute("""
            INSERT INTO audit_value_screener_runs (run_id, started_at, status)
            VALUES (?, current_timestamp, 'running')
            ON CONFLICT (run_id) DO NOTHING
        """, [run_id])

        # Universe = members der sp500_universe-Watchlist (NICHT direkt YAML —
        # so respektieren wir spaetere Editierungen via watchlist-CLI).
        members = con.execute(f"""
            SELECT m.ref_instrument_id, r.symbol, r.name
            FROM list_watchlist_members m
            JOIN ref_instruments r ON r.ref_instrument_id = m.ref_instrument_id
            WHERE m.watchlist_id = ? AND r.active = TRUE
            ORDER BY r.symbol
        """, [UNIVERSE_WATCHLIST]).fetchall()
        if not members:
            print(f"FEHLER: Watchlist '{UNIVERSE_WATCHLIST}' ist leer.", file=sys.stderr)
            print(f"        Erst `python -m modules.screener_value init` ausfuehren.", file=sys.stderr)
            con.execute("UPDATE audit_value_screener_runs SET status='failed', "
                         "error_msg='empty universe' WHERE run_id = ?", [run_id])
            return 64

        ids = [m[0] for m in members]
        sym_name_map = {m[0]: (m[1], m[2]) for m in members}

        # Fundamentals laden
        placeholders = ",".join(["?"] * len(ids))
        fund_rows = con.execute(f"""
            SELECT *
            FROM ref_fundamentals_latest
            WHERE ref_instrument_id IN ({placeholders})
        """, ids).fetchall()
        fund_cols = [d[0] for d in con.description]
        fund_map = {row[fund_cols.index("ref_instrument_id")]: dict(zip(fund_cols, row))
                    for row in fund_rows}

        print(f"==> nova-lab screener_value")
        print(f"    universe          : {UNIVERSE_WATCHLIST}  ({len(members)} symbols)")
        print(f"    fundamentals-hits : {len(fund_map)}/{len(members)}")
        print(f"    filter cfg        : roe>={cfg.min_roe} pe<={cfg.max_pe_ttm} "
              f"fcfy>={cfg.min_fcf_yield} d/e<={cfg.max_debt_to_equity} "
              f"composite>={cfg.min_composite_score}")
        print(f"    top-n             : {top_n} (sector-diverse={sector_diverse}, per-sector-cap={per_sector_cap})")
        print()

        # Evaluate alle
        scored: list[ScoredCandidate] = []
        for rid in ids:
            row = fund_map.get(rid)
            if row is None:
                # Kein Fundamental-Snapshot -> nicht bewertbar, skip
                continue
            symbol, name = sym_name_map.get(rid, (rid, None))
            row.setdefault("symbol", symbol)
            row.setdefault("name", name)
            scored.append(evaluate(row, cfg))

        passing = [s for s in scored if s.hard_filter_passes]
        print(f"    evaluated         : {len(scored)}")
        print(f"    pass hard-filter  : {len(passing)}")

        ranked = rank_candidates(scored, top_n=top_n,
                                  sector_diversification=sector_diverse,
                                  per_sector_cap=per_sector_cap)
        print(f"    final top-N       : {len(ranked)}")
        print()

        criteria_json = json.dumps({
            "min_roe": cfg.min_roe, "max_pe_ttm": cfg.max_pe_ttm,
            "min_fcf_yield": cfg.min_fcf_yield,
            "max_debt_to_equity": cfg.max_debt_to_equity,
            "min_composite_score": cfg.min_composite_score,
            "min_market_cap": cfg.min_market_cap,
            "top_n": top_n, "sector_diversification": sector_diverse,
        }, default=str)

        # Persist: sig_value_picks (history)
        today = date.today()
        for rank, c in enumerate(ranked, start=1):
            note = (f"composite={c.composite_score:.2f} "
                    f"sector={c.sector or '-'} mcap={(c.market_cap or 0)/1e9:.1f}B")
            con.execute("""
                INSERT OR REPLACE INTO sig_value_picks
                    (run_id, ref_instrument_id, ts,
                     composite_score, conviction_score, hard_filter_passes,
                     rank, criteria_json, notes)
                VALUES (?, ?, ?, ?, ?, TRUE, ?, ?, ?)
            """, [run_id, c.ref_instrument_id, today, c.composite_score,
                  c.conviction_result.score, rank, criteria_json, note])

        # Voll-Sync value_picks watchlist (nur unsere added_by-Entries)
        con.execute("""
            DELETE FROM list_watchlist_members
            WHERE watchlist_id = ? AND added_by = ?
        """, [PICKS_WATCHLIST, ADDED_BY_TAG])
        for rank, c in enumerate(ranked, start=1):
            note = (f"rank={rank} composite={c.composite_score:.2f} "
                    f"sector={c.sector or '-'} mcap={(c.market_cap or 0)/1e9:.1f}B")
            con.execute("""
                INSERT OR REPLACE INTO list_watchlist_members
                    (watchlist_id, ref_instrument_id, added_by, notes)
                VALUES (?, ?, ?, ?)
            """, [PICKS_WATCHLIST, c.ref_instrument_id, ADDED_BY_TAG, note])

        # CSV-Dump
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = OUTPUT_DIR / f"value_screener_{today.isoformat()}_{run_id}.csv"
        with csv_path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["rank", "symbol", "ref_instrument_id", "sector",
                              "composite_score", "market_cap_b", "passes",
                              "reject_reasons"])
            # write ALL evaluated (auch fails) fuer Audit/Drilldown
            for rank, c in enumerate(sorted(scored, key=lambda x: -x.composite_score), 1):
                writer.writerow([
                    rank if c.hard_filter_passes else "",
                    c.symbol, c.ref_instrument_id, c.sector or "",
                    f"{c.composite_score:.3f}",
                    f"{(c.market_cap or 0)/1e9:.2f}",
                    "yes" if c.hard_filter_passes else "no",
                    "; ".join(c.reject_reasons),
                ])

        # Audit-End
        con.execute("""
            UPDATE audit_value_screener_runs
            SET completed_at=current_timestamp,
                universe_size=?, fundamentals_hits=?,
                candidates=?, picked=?, status='success'
            WHERE run_id = ?
        """, [len(members), len(fund_map), len(passing), len(ranked), run_id])

        print(f"    csv               : {csv_path}")
        print()
        if ranked:
            print(f"    Top {min(10, len(ranked))} Picks:")
            for rank, c in enumerate(ranked[:10], start=1):
                print(f"      {rank:>2d}. {c.symbol:<8s} composite={c.composite_score:.2f} "
                      f"sector={c.sector or '-':<22s} ({c.name or ''})")
        return 0
    finally:
        con.close()


# ---------- llm-deepdive ----------

def cmd_llm_deepdive(args) -> int:
    """On-demand LLM-Strukturierer fuer ein Symbol."""
    con = duckdb.connect(str(DB_PATH))
    try:
        _ensure_schemas(con)
        row = con.execute("""
            SELECT * FROM ref_fundamentals_latest
            WHERE ref_instrument_id = ?
        """, [args.ref_instrument_id]).fetchone()
        if row is None:
            print(f"FEHLER: Keine Fundamentals fuer {args.ref_instrument_id}.", file=sys.stderr)
            print(f"        Erst `lab_fundamentals refresh {args.ref_instrument_id}` laufen lassen.")
            return 64
        cols = [d[0] for d in con.description]
        fundamentals = dict(zip(cols, row))

        meta = con.execute("""
            SELECT symbol, name FROM ref_instruments WHERE ref_instrument_id = ?
        """, [args.ref_instrument_id]).fetchone()
        symbol, name = (meta[0], meta[1]) if meta else (args.ref_instrument_id, None)
    finally:
        con.close()

    # News holen
    from modules.llm.news_yfinance import fetch_news_yfinance
    news = fetch_news_yfinance(symbol, max_n=8, name=name, augment_with_rss=True)

    # Seeking-Alpha-Artikel zum Symbol (letzte 30 Tage) — optional, fail-soft.
    sa_articles: list[dict] = []
    try:
        sa_con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            sa_rows = sa_con.execute("""
                SELECT a.ts, a.title, a.summary, a.url
                FROM ref_sa_articles a
                JOIN ref_sa_article_symbols s ON s.article_id = a.article_id
                WHERE s.ref_instrument_id = ?
                  AND a.ts >= current_timestamp - INTERVAL '30 days'
                ORDER BY a.ts DESC
                LIMIT 6
            """, [args.ref_instrument_id]).fetchall()
            sa_articles = [{"ts": r[0], "title": r[1], "summary": r[2], "url": r[3]}
                            for r in sa_rows]
        finally:
            sa_con.close()
    except duckdb.CatalogException:
        # ref_sa_articles existiert nicht (Schema noch nicht migriert)
        pass

    # LLM-Brief
    from .llm_brief import generate_brief
    print(f"==> LLM-Deepdive: {symbol} ({name or '-'})")
    print(f"    News: {len(news)} items (yf + optional RSS), SA: {len(sa_articles)} articles")
    print(f"    Modell: {args.model or 'default'}")
    print()

    result = generate_brief(symbol, name or symbol, fundamentals, news,
                             sa_articles=sa_articles, model=args.model)
    if result.error:
        print(f"FEHLER: {result.error}", file=sys.stderr)
        return 65

    print(f"--- Summary ---")
    print(f"  {result.summary or '(none)'}")
    print()
    print(f"--- Strengths ({len(result.strengths)}) ---")
    for s in result.strengths:
        print(f"  + {s}")
    print()
    print(f"--- Red Flags ({len(result.red_flags)}) ---")
    for r in result.red_flags:
        print(f"  - {r}")
    print()
    print(f"    {result.fundamentals_used} fundamentals, {result.news_count} news, "
          f"{result.sa_count} SA, {result.eval_tokens or '?'} tokens, "
          f"{result.duration_s or 0:.1f}s, model={result.model}")

    # Persist
    if not args.no_persist:
        con = duckdb.connect(str(DB_PATH))
        try:
            run_id = os.environ.get("NOVA_JOB_ID",
                                      f"adhoc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
            con.execute("""
                INSERT OR REPLACE INTO sig_value_briefings
                    (ref_instrument_id, ts, model, summary, strengths, red_flags,
                     fundamentals_used, news_count, eval_tokens, duration_s, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [args.ref_instrument_id, date.today(), result.model or "?",
                  result.summary,
                  "\n".join(result.strengths),
                  "\n".join(result.red_flags),
                  result.fundamentals_used, result.news_count,
                  result.eval_tokens, result.duration_s, run_id])
        finally:
            con.close()
        print(f"    Persisted -> sig_value_briefings")
    return 0


# ---------- show ----------

def cmd_show(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        # Latest filter-run
        latest_run = con.execute("""
            SELECT MAX(ts) FROM sig_value_picks
        """).fetchone()
        if not latest_run or not latest_run[0]:
            print("Noch kein filter-run vorhanden. Erst `screener_value filter` laufen lassen.")
            return 0
        latest_ts = latest_run[0]

        rows = con.execute("""
            SELECT p.rank, r.symbol, r.name, p.composite_score, p.conviction_score, p.notes,
                   p.ref_instrument_id
            FROM sig_value_picks p
            LEFT JOIN ref_instruments r USING (ref_instrument_id)
            WHERE p.ts = ?
            ORDER BY p.rank
        """, [latest_ts]).fetchall()

        print(f"==> Value-Picks vom {latest_ts}  ({len(rows)} Eintraege)")
        print()
        print(f"  {'rank':>4s}  {'symbol':<8s} {'composite':>10s}  {'name':<40s} ref_id")
        print(f"  {'-'*4}  {'-'*8} {'-'*10}  {'-'*40} {'-'*22}")
        for rank, symbol, name, comp, conv, note, rid in rows:
            print(f"  {rank:>4d}  {symbol:<8s} {comp:>10.3f}  {(name or '?'):<40s} {rid}")
            print(f"        notes: {note}")

        # Last briefings
        briefings = con.execute("""
            SELECT ref_instrument_id, ts, summary
            FROM sig_value_briefings
            ORDER BY ts DESC
            LIMIT 5
        """).fetchall()
        if briefings:
            print()
            print(f"==> Letzte LLM-Briefings ({len(briefings)}):")
            for rid, ts, summary in briefings:
                print(f"  [{ts}] {rid}: {(summary or '')[:120]}{'...' if summary and len(summary)>120 else ''}")
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Universe-YAML -> ref_instruments + watchlist")
    p_init.add_argument("--universe-yaml", default=None,
                         help="Pfad zur YAML (default: config/universe_sp500.yaml)")

    p_filter = sub.add_parser("filter", help="Apply value-filter + persist top-N")
    p_filter.add_argument("--params-file", default=None)

    p_deep = sub.add_parser("llm-deepdive", help="On-demand LLM-Brief fuer ein Symbol")
    p_deep.add_argument("ref_instrument_id")
    p_deep.add_argument("--model", default=None, help="Ollama-Modell-Override")
    p_deep.add_argument("--no-persist", action="store_true",
                         help="Nicht in sig_value_briefings schreiben (Dry-Run)")

    sub.add_parser("show", help="Letztes filter-Resultat + briefings anzeigen")

    args = p.parse_args()

    # init darf auch ohne existierende DB laufen (legt sie an)
    if args.cmd != "init" and not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    dispatch = {
        "init":         cmd_init,
        "filter":       cmd_filter,
        "llm-deepdive": cmd_llm_deepdive,
        "show":         cmd_show,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
