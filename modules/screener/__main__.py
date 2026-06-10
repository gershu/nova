"""nova-lab screener CLI — Quality-GARP-Screening.

Subcommands:
    init [--universe-yaml PATH]
        Lese config/screener_quality_universe.yaml -> ref_instruments +
        Watchlist 'quality_universe'. Idempotent.
    screen [--params-file PATH]
        Stufe 1+2: KPI-Filter + Trend-Berechnung ueber das Universum.
        Persistiert nach sig_screen_runs + sig_screen_picks.
    show [--run-id ID] [--limit N]
        Picks eines Runs (default: letzter Run) anzeigen.
    analyze SYMBOL [--run-id ID] [--model NAME] [--no-news]
        Stufe 3 (on-demand): laed Pick + 10-K-Texte + News, ruft lokales
        LLM, persistiert Thesis in sig_screen_thesis. Laufzeit-unkritisch.

Environment:
    LAB_DB_PATH        optional — default ~/nova_data/lab.duckdb
    NOVA_PARAMS_FILE   optional — JSON mit FilterConfig-Override

Beispiele:
    python -m modules.screener init
    python -m modules.screener screen
    python -m modules.screener screen --params-file ~/jobs/screen_v2.json
    python -m modules.screener show
    python -m modules.screener show --run-id adhoc-...
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import uuid
from dataclasses import fields as dc_fields
from datetime import datetime, timezone

import duckdb

from modules.screener_value.universe import (
    UniverseMember, load_universe, ref_instrument_id_for,
)
from . import UNIVERSE_WATCHLIST
from .filter import (
    FilterConfig, ScreenCandidate, evaluate, serialize_candidate,
    config_to_dict,
)
from .history import (
    latest_anchor, compute_cagr_5y, compute_q_yoy, compute_trends,
)


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
DEFAULT_YAML = (pathlib.Path(__file__).parent.parent.parent
                / "config" / "screener_quality_universe.yaml")
SQL_DIR = pathlib.Path(__file__).parent / "sql"
SQL_FILES = sorted(SQL_DIR.glob("0*.sql"))
ADDED_BY_TAG = "screener.init"


def apply_schema(con: duckdb.DuckDBPyConnection, *, verbose: bool = False) -> None:
    """sql/0*.sql idempotent anwenden (CREATE TABLE IF NOT EXISTS)."""
    for f in SQL_FILES:
        con.execute(f.read_text())
        if verbose:
            print(f"    ✓ {f.name}")


def _load_params(args) -> FilterConfig:
    """FilterConfig laden: Defaults <- NOVA_PARAMS_FILE <- --params-file.

    Erlaubt JSON mit beliebigen FilterConfig-Feldern; unbekannte Felder
    werden ignoriert (mit Warning).
    """
    cfg = FilterConfig()
    paths = []
    env_p = os.environ.get("NOVA_PARAMS_FILE", "").strip()
    if env_p:
        paths.append(pathlib.Path(env_p))
    if getattr(args, "params_file", None):
        paths.append(pathlib.Path(args.params_file))
    valid = {f.name for f in dc_fields(cfg)}
    for p in paths:
        if not p.is_file():
            print(f"WARN: params-file fehlt: {p}", file=sys.stderr)
            continue
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            print(f"FEHLER: params-file ungueltig {p}: {e}", file=sys.stderr)
            return cfg
        for k, v in data.items():
            if k in valid:
                setattr(cfg, k, v)
            else:
                print(f"WARN: unbekannter Parameter '{k}' in {p}",
                      file=sys.stderr)
    return cfg


# ---------- init ----------

def cmd_init(args) -> int:
    """Quality-Universe-YAML -> ref_instruments + Watchlist."""
    yaml_path = pathlib.Path(args.universe_yaml) if args.universe_yaml \
        else DEFAULT_YAML
    members = load_universe(yaml_path)
    print(f"==> Universe geladen: {len(members)} Symbole aus {yaml_path.name}")

    con = duckdb.connect(str(DB_PATH))
    try:
        apply_schema(con)

        # 1. Watchlist anlegen (idempotent)
        con.execute("""
            INSERT INTO list_watchlists (watchlist_id, name, description, origin)
            VALUES (?, ?, ?, 'system')
            ON CONFLICT (watchlist_id) DO NOTHING
        """, [UNIVERSE_WATCHLIST,
              "Quality-Universe (Screener)",
              "Kuratierte ~100 Qualitaets-Compounder aus "
              "config/screener_quality_universe.yaml."])

        # 2. ref_instruments + watchlist_members je Member
        n_new_inst, n_new_member, n_skipped = 0, 0, 0
        for m in members:
            rid = ref_instrument_id_for(m)
            existed = con.execute(
                "SELECT 1 FROM ref_instruments WHERE ref_instrument_id = ?",
                [rid],
            ).fetchone()
            if not existed:
                con.execute("""
                    INSERT INTO ref_instruments
                        (ref_instrument_id, symbol, currency, name, asset_type,
                         preferred_source, exchange, active, notes)
                    VALUES (?, ?, ?, ?, 'stock', 'yfinance',
                            'NASDAQ/NYSE', TRUE, ?)
                """, [rid, m.symbol, m.currency, m.name,
                      f"auto-added from {UNIVERSE_WATCHLIST}; "
                      f"sector={m.sector or '-'}"])
                n_new_inst += 1
            else:
                n_skipped += 1

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

        print(f"    ref_instruments     : +{n_new_inst} neu, "
              f"{n_skipped} schon vorhanden")
        print(f"    watchlist members   : +{n_new_member} neu "
              f"(in {UNIVERSE_WATCHLIST})")
        print()
        print("==> Next steps:")
        print("    1. python -m modules.fundamentals refresh-all "
              "(Fundamentals-Snapshot fuer neue Namen)")
        print(f"    2. python -m modules.sec_filings backfill-all "
              f"--watchlist {UNIVERSE_WATCHLIST} --quarters 20")
        print("       (GuV-Historie fuer das Universum)")
        return 0
    finally:
        con.close()


# ---------- screen ----------

def cmd_screen(args) -> int:
    """Stufe 1+2 ueber das Quality-Universum laufen lassen."""
    cfg = _load_params(args)
    run_id = (f"adhoc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
              f"-{uuid.uuid4().hex[:6]}")
    started = datetime.now(timezone.utc).replace(tzinfo=None)

    con = duckdb.connect(str(DB_PATH))
    try:
        apply_schema(con)

        # Universe + Fundamentals zusammen ziehen (1 Query, 1 Pass).
        df = con.execute(f"""
            SELECT m.ref_instrument_id, i.symbol, i.name,
                   f.sector, f.* EXCLUDE (ref_instrument_id, sector)
            FROM list_watchlist_members m
            JOIN ref_instruments i USING (ref_instrument_id)
            LEFT JOIN ref_fundamentals_latest f USING (ref_instrument_id)
            WHERE m.watchlist_id = ?
            ORDER BY i.symbol
        """, [UNIVERSE_WATCHLIST]).df()
        n_candidates = len(df)
        if n_candidates == 0:
            print(f"FEHLER: Watchlist '{UNIVERSE_WATCHLIST}' leer. "
                  "'init' zuerst.", file=sys.stderr)
            return 64

        print(f"==> screen (run_id={run_id}) — {n_candidates} Kandidaten "
              f"aus {UNIVERSE_WATCHLIST}")

        candidates: list[ScreenCandidate] = []
        n_no_fund = 0
        for _, r in df.iterrows():
            ref_id = r["ref_instrument_id"]
            fundamentals = r.to_dict()
            if fundamentals.get("market_cap") is None:
                n_no_fund += 1     # keine Fundamentals -> wird unten gefiltert
            anchor = latest_anchor(con, ref_id)
            cagr = compute_cagr_5y(con, ref_id, anchor) if anchor else None
            qyoy = compute_q_yoy(con, ref_id)
            trends = compute_trends(con, ref_id, anchor, cagr, qyoy) \
                if anchor else {}
            cand = evaluate(
                ref_id, r["symbol"], r["name"], r.get("sector"),
                fundamentals, cagr, qyoy, trends, cfg)
            candidates.append(cand)

        # Ranking: nur Hard-Filter-Passers, sortiert nach composite desc.
        passers = [c for c in candidates if c.hard_filter_passes
                   and c.composite_score >= cfg.min_composite_score]
        passers.sort(key=lambda c: c.composite_score, reverse=True)
        top = passers[:cfg.top_n]

        print(f"    Kandidaten ohne Fundamentals: {n_no_fund}")
        print(f"    Hard-Filter + Min-Score bestanden: {len(passers)} / "
              f"{n_candidates}")
        print(f"    Top-{cfg.top_n} fuer Persistenz: {len(top)}")

        # Persistenz
        con.execute("""
            INSERT INTO sig_screen_runs
                (run_id, ts, universe, params_json,
                 n_candidates, n_passed, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [run_id, started, UNIVERSE_WATCHLIST,
              json.dumps(config_to_dict(cfg), default=float),
              n_candidates, len(passers),
              f"top_n={cfg.top_n}; no_fund={n_no_fund}"])

        for rank, c in enumerate(top, start=1):
            s = serialize_candidate(c)
            con.execute("""
                INSERT INTO sig_screen_picks
                    (run_id, ref_instrument_id, rank, symbol, name, sector,
                     market_cap, quality_score, growth_score, value_score,
                     composite_score, hard_filter_passes,
                     criteria_detail_json, trend_flags_json, metrics_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [run_id, s["ref_instrument_id"], rank, s["symbol"],
                  s["name"], s["sector"], s["market_cap"],
                  s["quality_score"], s["growth_score"], s["value_score"],
                  s["composite_score"], s["hard_filter_passes"],
                  s["criteria_detail_json"], s["trend_flags_json"],
                  s["metrics_json"]])

        # Kurz-Vorschau
        print()
        print(f"  {'#':>3s}  {'sym':<8s}  {'Q':<5s}  {'G':<5s}  "
              f"{'V':<5s}  {'comp':<5s}  sector")
        print(f"  {'-'*3}  {'-'*8}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  "
              f"{'-'*20}")
        for rank, c in enumerate(top[:15], start=1):
            print(f"  {rank:>3d}  {c.symbol:<8s}  "
                  f"{c.quality_score:.2f}  {c.growth_score:.2f}  "
                  f"{c.value_score:.2f}  {c.composite_score:.2f}  "
                  f"{c.sector or '-'}")
        if len(top) > 15:
            print(f"  ... ({len(top) - 15} weitere)")
        return 0
    finally:
        con.close()


# ---------- show ----------

def cmd_show(args) -> int:
    """Picks eines Runs anzeigen (default: juengster)."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        if args.run_id:
            run_id = args.run_id
        else:
            row = con.execute(
                "SELECT run_id FROM sig_screen_runs ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            if not row:
                print("(keine Runs vorhanden)")
                return 0
            run_id = row[0]

        meta = con.execute(
            "SELECT ts, universe, n_candidates, n_passed, params_json "
            "FROM sig_screen_runs WHERE run_id = ?", [run_id]).fetchone()
        if not meta:
            print(f"FEHLER: run_id '{run_id}' unbekannt.", file=sys.stderr)
            return 64
        ts, universe, n_cand, n_pass, params_json = meta
        params = json.loads(params_json)
        print(f"==> {run_id}")
        print(f"    {ts} · {universe} · {n_cand} Kandidaten, "
              f"{n_pass} bestanden")
        print(f"    Gewichte: Q={params.get('weight_quality')} "
              f"G={params.get('weight_growth')} "
              f"V={params.get('weight_value')}")

        picks = con.execute("""
            SELECT rank, symbol, sector, quality_score, growth_score,
                   value_score, composite_score, trend_flags_json
            FROM sig_screen_picks WHERE run_id = ?
            ORDER BY rank LIMIT ?
        """, [run_id, args.limit]).fetchall()
        if not picks:
            print("  (keine Picks)")
            return 0
        print()
        print(f"  {'#':>3s}  {'sym':<8s}  {'Q':<5s}  {'G':<5s}  "
              f"{'V':<5s}  {'comp':<5s}  {'sector':<22s}  trends")
        print(f"  {'-'*3}  {'-'*8}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  "
              f"{'-'*22}  {'-'*30}")
        for rk, sym, sec, q, g, v, comp, tj in picks:
            trends = json.loads(tj) if tj else {}
            tstr = " ".join(
                k.replace("_", "")[:3] + ("+" if val else "−")
                for k, val in trends.items() if isinstance(val, bool))
            print(f"  {rk:>3d}  {sym:<8s}  {q:.2f}  {g:.2f}  {v:.2f}  "
                  f"{comp:.2f}  {(sec or '-'):<22s}  {tstr}")
        return 0
    finally:
        con.close()


# ---------- analyze ----------

def _load_pick(con: duckdb.DuckDBPyConnection,
                ref_id: str, run_id: str | None) -> tuple[str, dict] | None:
    """(run_id, pick-row-dict) aus letztem oder gegebenem Run."""
    if run_id is None:
        row = con.execute("""
            SELECT p.run_id FROM sig_screen_picks p
            JOIN sig_screen_runs r ON r.run_id = p.run_id
            WHERE p.ref_instrument_id = ?
            ORDER BY r.ts DESC LIMIT 1
        """, [ref_id]).fetchone()
        if not row:
            return None
        run_id = row[0]
    pick = con.execute("""
        SELECT * FROM sig_screen_picks
        WHERE run_id = ? AND ref_instrument_id = ?
    """, [run_id, ref_id]).df()
    if pick.empty:
        return None
    return run_id, pick.iloc[0].to_dict()


def _latest_10k_acc(con: duckdb.DuckDBPyConnection,
                     ref_id: str) -> tuple[str, str] | None:
    """(accession_no, period_end) des juengsten 10-K. Fallback auf 10-Q."""
    for form in ("10-K", "10-Q"):
        row = con.execute("""
            SELECT accession_no, period_end FROM ref_income_statement
            WHERE ref_instrument_id = ? AND form_type = ?
              AND accession_no IS NOT NULL
            ORDER BY period_end DESC LIMIT 1
        """, [ref_id, form]).fetchone()
        if row and row[0]:
            return (row[0], str(row[1])[:10])
    return None


def _recent_news(con: duckdb.DuckDBPyConnection,
                  ref_id: str, n: int = 12) -> list[dict]:
    df = con.execute("""
        SELECT a.ts, a.title, a.summary, a.url
        FROM ref_sa_article_symbols s
        JOIN ref_sa_articles a USING (article_id)
        WHERE s.ref_instrument_id = ?
        ORDER BY a.ts DESC LIMIT ?
    """, [ref_id, n]).df()
    if df.empty:
        return []
    df["ts"] = df["ts"].astype(str)
    return df.to_dict(orient="records")


def cmd_analyze(args) -> int:
    """Stufe-3-LLM-Bewertung fuer ein Symbol (on-demand)."""
    # Lazy-Import: analyzer hat llm-Dependencies. (Filing-Volltext via sec-api
    # entfaellt seit Pfad A — Stufe 3 nutzt News + Kennzahlen.)
    from .analyzer import (
        AnalyzerInput, build_prompt, call_llm, parse_response,
        SYSTEM_PROMPT,
    )

    # Symbol auf ref_instrument_id mappen.
    con_ro = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        row = con_ro.execute(
            "SELECT ref_instrument_id, symbol, name FROM ref_instruments "
            "WHERE upper(symbol) = upper(?) LIMIT 1", [args.symbol],
        ).fetchone()
    finally:
        con_ro.close()
    if not row:
        print(f"FEHLER: '{args.symbol}' nicht in ref_instruments.",
              file=sys.stderr)
        return 64
    ref_id, symbol, name = row

    con = duckdb.connect(str(DB_PATH))
    try:
        apply_schema(con)

        # 1. Pick aus Screener-Run laden.
        loaded = _load_pick(con, ref_id, args.run_id)
        if not loaded:
            print(f"FEHLER: kein Screener-Pick fuer {symbol} gefunden. "
                  "Erst 'screen' laufen lassen.", file=sys.stderr)
            return 64
        run_id, pick = loaded
        print(f"==> analyze {symbol} (ref={ref_id})  run={run_id}")

        # 2. Filing-Volltext (Item 1/1A/7) entfaellt mit der sec-api-
        #    Stilllegung (Pfad A). Stufe 3 bewertet News + Kennzahlen.
        business = risk = mda = ""
        filing_form, filing_period = None, None
        acc = _latest_10k_acc(con, ref_id)
        if acc:
            acc_no, filing_period = acc
            row_ft = con.execute(
                "SELECT form_type FROM ref_income_statement "
                "WHERE accession_no=?", [acc_no]).fetchone()
            filing_form = "10-K" if (row_ft and row_ft[0] == "10-K") else "10-Q"
            print(f"    Filing: {filing_form} per {filing_period} "
                  f"(acc={acc_no}) — Volltext entfaellt (sec-api stillgelegt)")

        # 3. News.
        news = [] if args.no_news else _recent_news(con, ref_id)
        print(f"    News: {len(news)} Items")

        # 4. Prompt bauen.
        metrics = json.loads(pick["metrics_json"]) if pick.get(
            "metrics_json") else {}
        crit_detail = json.loads(pick["criteria_detail_json"]) if pick.get(
            "criteria_detail_json") else []
        trends = json.loads(pick["trend_flags_json"]) if pick.get(
            "trend_flags_json") else {}

        inp = AnalyzerInput(
            symbol=symbol, name=name, sector=pick.get("sector"),
            market_cap=pick.get("market_cap"),
            metrics=metrics, criteria_detail=crit_detail, trends=trends,
            axis_scores={
                "quality_score":   pick.get("quality_score"),
                "growth_score":    pick.get("growth_score"),
                "value_score":     pick.get("value_score"),
                "composite_score": pick.get("composite_score"),
            },
            business_text=business, risk_text=risk, mda_text=mda,
            news_items=news, filing_form=filing_form,
            filing_period=filing_period,
        )
        prompt = build_prompt(inp)
        print(f"    Prompt:  {len(prompt):,} chars")

        # 5. LLM-Call.
        print(f"    LLM-Call (Modell: {args.model or 'default'}) ...")
        try:
            resp = call_llm(prompt, system=SYSTEM_PROMPT, model=args.model)
        except Exception as e:  # noqa: BLE001
            print(f"FEHLER: LLM-Call: {e}", file=sys.stderr)
            return 65
        print(f"    LLM done in {resp.duration_s:.1f}s "
              f"({resp.eval_count} tok, {resp.tps:.1f} tok/s)")

        # 6. Parsen + persistieren.
        parsed = parse_response(resp.text)
        if "_parse_error" in parsed:
            print(f"    WARN: parse-error: {parsed['_parse_error']}",
                  file=sys.stderr)
        ts = datetime.now(timezone.utc).replace(tzinfo=None)
        con.execute("""
            INSERT INTO sig_screen_thesis
              (run_id, ref_instrument_id, ts, llm_model, verdict,
               growth_score_llm, value_score_llm, conviction_score,
               thesis_text, risks_json, citations_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [run_id, ref_id, ts, resp.model,
              parsed.get("verdict"),
              parsed.get("growth_score_llm"),
              parsed.get("value_score_llm"),
              parsed.get("conviction_score"),
              parsed.get("thesis_text"),
              json.dumps(parsed.get("risks", []), default=str),
              json.dumps({"classification": parsed.get("classification"),
                          "moat_assessment": parsed.get("moat_assessment"),
                          "warnings": parsed.get("_warn", [])},
                         default=str)])

        # 7. Kurz-Anzeige.
        print()
        print(f"==> Verdikt: {parsed.get('verdict', '?')}  "
              f"Conviction: {parsed.get('conviction_score', '?')}  "
              f"Klasse: {parsed.get('classification', '?')}")
        print(f"    Growth-Score: {parsed.get('growth_score_llm', '?')}  "
              f"Value-Score: {parsed.get('value_score_llm', '?')}")
        if parsed.get("thesis_text"):
            print(f"\n    Thesis: {parsed['thesis_text']}")
        if parsed.get("risks"):
            print(f"\n    Risiken:")
            for r in parsed["risks"]:
                print(f"      - {r.get('risk', '?')}  "
                      f"[{r.get('citation', '?')}]")
        if parsed.get("moat_assessment"):
            print(f"\n    Moat: {parsed['moat_assessment']}")
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init",
        help="Quality-Universum-YAML -> ref_instruments + Watchlist")
    p_init.add_argument("--universe-yaml", default=None,
        help=f"YAML-Pfad (default: {DEFAULT_YAML.name})")

    p_screen = sub.add_parser("screen",
        help="Stufe 1+2: KPI-Filter + Trend-Berechnung")
    p_screen.add_argument("--params-file", default=None,
        help="JSON mit FilterConfig-Override (sonst NOVA_PARAMS_FILE / Defaults)")

    p_show = sub.add_parser("show", help="Picks eines Runs anzeigen")
    p_show.add_argument("--run-id", default=None,
        help="Spezifischer Run (default: juengster)")
    p_show.add_argument("--limit", type=int, default=30)

    p_an = sub.add_parser("analyze",
        help="Stufe 3 (on-demand): LLM-Bewertung mit 10-K-Auszuegen")
    p_an.add_argument("symbol")
    p_an.add_argument("--run-id", default=None,
        help="Spezifischer Run (default: juengster mit Pick)")
    p_an.add_argument("--model", default=None,
        help="Ollama-Modell (default: ENV LLM_DEFAULT_MODEL bzw. qwen2.5:14b)")
    p_an.add_argument("--no-news", action="store_true",
        help="News-Block weglassen (kuerzerer Prompt)")

    args = p.parse_args()
    if args.cmd != "init" and not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}. 'init' zuerst.",
              file=sys.stderr)
        return 64

    dispatch = {
        "init":    cmd_init,
        "screen":  cmd_screen,
        "show":    cmd_show,
        "analyze": cmd_analyze,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
