"""nova-lab Screener-CSP — findet Cash-Secured-Put-Kandidaten.

Subcommands:
    init                    Legt 'csp_universe' Watchlist an (leer)
    run                     Scan ausfuehren, Top-N -> system_recommendations + CSV

Konfig (3-Tier):
  Tier 3 — JSON via NOVA_PARAMS_FILE:
    {
      "watchlist_universe":     "csp_universe",     // Watchlist mit Underlyings
      "min_dte":                25,                  // 25-50 Tage Laufzeit
      "max_dte":                50,
      "buffer_min_pct":         5,                   // Strike 5-15% unter Spot
      "buffer_max_pct":         15,
      "min_annualized_yield":   8.0,                 // % p.a. auf Cash-Collateral
      "max_spread_pct":         25,                  // bid/ask spread vs mid
      "expirations_per_symbol": 2,                   // 2 naechste Expirations pro Symbol
      "top_n_per_symbol":       1,                   // 1 Best-Strike pro Symbol
      "top_n_overall":          20                   // max 20 in system_recommendations
    }
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import sys
from datetime import date, datetime, timezone

import duckdb

from .earnings import (
    get_next_earnings_dates,
    refresh_earnings_for_universe,
)
from .engine import (
    CSPCandidate,
    FilterConfig,
    ScoreConfig,
    passes_filter,
    score_candidate,
    score_components,
    select_top,
)
from .ib_options import IBOptionsClient
from .conviction import (
    conviction_score,
    format_conviction_notes,
    ConvictionResult,
)


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
OUTPUT_DIR = pathlib.Path.home() / "nova_output" / "lab_screener_csp"

UNIVERSE_WATCHLIST_DEFAULT = "csp_universe"
SYSTEM_REC_WATCHLIST       = "system_recommendations"
ADDED_BY_TAG               = "screener_csp"


def load_params(params_file_override: str | None = None) -> dict:
    """Loads params from JSON file. CLI --params-file overridet NOVA_PARAMS_FILE-env."""
    pf = params_file_override or os.environ.get("NOVA_PARAMS_FILE")
    if not pf:
        return {}
    p = pathlib.Path(pf).expanduser()
    if not p.is_file():
        print(f"[WARN] params file nicht gefunden: {p}", file=sys.stderr)
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        print(f"[WARN] params file ist kein gueltiges JSON: {e}", file=sys.stderr)
        return {}


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Schema kommt aus ingest + portfolio + watchlist."""
    for module_dir in ["ingest", "portfolio", "watchlist"]:
        sql_dir = pathlib.Path(__file__).parent.parent / module_dir / "sql"
        if sql_dir.is_dir():
            for sql_file in sorted(sql_dir.glob("0*.sql")):
                con.execute(sql_file.read_text())


# ---------- INIT ----------

def cmd_init(args) -> int:
    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        existing = con.execute(
            "SELECT 1 FROM list_watchlists WHERE watchlist_id = ?",
            [UNIVERSE_WATCHLIST_DEFAULT],
        ).fetchone()
        if existing:
            print(f"[INFO] Watchlist '{UNIVERSE_WATCHLIST_DEFAULT}' existiert bereits.")
        else:
            con.execute(
                """
                INSERT INTO list_watchlists (watchlist_id, name, description, origin)
                VALUES (?, 'CSP Universe',
                        'Underlyings die als Cash-Secured-Put-Targets in Frage kommen.',
                        'user')
                """,
                [UNIVERSE_WATCHLIST_DEFAULT],
            )
            print(f"==> Watchlist '{UNIVERSE_WATCHLIST_DEFAULT}' angelegt.")

        # system_recommendations sollte vom watchlist init existieren — sicherstellen
        existing = con.execute(
            "SELECT 1 FROM list_watchlists WHERE watchlist_id = ?",
            [SYSTEM_REC_WATCHLIST],
        ).fetchone()
        if not existing:
            con.execute(
                """
                INSERT INTO list_watchlists (watchlist_id, name, description, origin)
                VALUES (?, 'System-Empfehlungen',
                        'Automatisch generiert durch screener-Module.',
                        'system')
                """,
                [SYSTEM_REC_WATCHLIST],
            )
            print(f"==> Watchlist '{SYSTEM_REC_WATCHLIST}' angelegt.")

        print()
        print("==> Naechste Schritte:")
        print(f"    Underlyings hinzufuegen, z.B.:")
        print(f"      python -m modules.watchlist add IB:AAPL:USD --to {UNIVERSE_WATCHLIST_DEFAULT}")
        print(f"    Dann Scan starten:")
        print(f"      python -m modules.screener_csp run")
    finally:
        con.close()
    return 0


# ---------- RUN ----------

def cmd_run(args) -> int:
    params = load_params(getattr(args, "params_file", None))
    universe_id = params.get("watchlist_universe", UNIVERSE_WATCHLIST_DEFAULT)

    cfg = FilterConfig(
        min_dte              = int(params.get("min_dte", 25)),
        max_dte              = int(params.get("max_dte", 50)),
        buffer_min_pct       = float(params.get("buffer_min_pct", 5.0)),
        buffer_max_pct       = float(params.get("buffer_max_pct", 15.0)),
        min_annualized_yield = float(params.get("min_annualized_yield", 8.0)),
        max_spread_pct       = float(params.get("max_spread_pct", 25.0)),
        min_bid              = float(params.get("min_bid", 0.05)),
        require_iv           = bool(params.get("require_iv", False)),
        avoid_earnings       = bool(params.get("avoid_earnings", True)),
    )
    earnings_max_age_days = int(params.get("earnings_max_age_days", 7))
    # ScoreConfig aus optionalem 'score'-Sub-Dict — alle Felder optional
    score_overrides = params.get("score", {}) or {}
    scfg = ScoreConfig(**{
        k: float(v) for k, v in score_overrides.items()
        if k in ScoreConfig.__dataclass_fields__
    })
    expirations_per_symbol = int(params.get("expirations_per_symbol", 2))
    top_n_per_symbol = int(params.get("top_n_per_symbol", 1))
    top_n_overall    = int(params.get("top_n_overall", 20))

    # Value-Filter (Conviction-Multiplier aus ref_fundamentals_latest).
    # use_conviction: ob conviction.score in den final-score multipliziert wird.
    # min_conviction: Hard-Gate. 0.0 = soft-only (multiplier ohne Filter).
    use_conviction = bool(params.get("use_conviction", True))
    min_conviction = float(params.get("min_conviction", 0.0))

    run_id = os.environ.get(
        "NOVA_JOB_ID",
        f"adhoc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    )

    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_schema(con)

        # 1. Universe laden — nur stocks/etfs (nicht bonds/funds/etc.)
        universe = con.execute(
            """
            SELECT m.ref_instrument_id, r.symbol, r.con_id, r.exchange, r.currency, r.asset_type
            FROM list_watchlist_members m
            JOIN ref_instruments r ON r.ref_instrument_id = m.ref_instrument_id
            WHERE m.watchlist_id = ?
              AND r.active = true
              AND r.asset_type IN ('stock', 'etf')
            ORDER BY r.symbol
            """,
            [universe_id],
        ).fetchall()

        if not universe:
            print(f"FEHLER: Watchlist '{universe_id}' ist leer oder existiert nicht.", file=sys.stderr)
            print(f"       Erst init und Underlyings hinzufuegen:", file=sys.stderr)
            print(f"         python -m modules.screener_csp init", file=sys.stderr)
            print(f"         python -m modules.watchlist add IB:AAPL:USD --to {universe_id}", file=sys.stderr)
            return 64

        # 2. Latest spot pro Underlying aus mkt_quotes_daily
        ids = [u[0] for u in universe]
        placeholders = ",".join(["?"] * len(ids))
        spot_rows = con.execute(
            f"""
            WITH ranked AS (
                SELECT ref_instrument_id, ts, close,
                       ROW_NUMBER() OVER (PARTITION BY ref_instrument_id ORDER BY ts DESC) AS rn
                FROM mkt_quotes_daily
                WHERE ref_instrument_id IN ({placeholders})
            )
            SELECT ref_instrument_id, close FROM ranked WHERE rn = 1
            """,
            ids,
        ).fetchall()
        spots = {r[0]: r[1] for r in spot_rows}

        # 2b. Conviction-Lookup (Value-Filter) — eine Query, alle Underlyings.
        # Wird per (ref_instrument_id -> ConvictionResult) gemappt. Wenn die
        # Tabelle fehlt (Schema 0001_fundamentals.sql nicht geladen) oder leer
        # ist, returnt das einen leeren Map und der Conviction-Multiplier wird
        # zum Neutralwert (0.5 / 1.0 abhaengig vom CLI-Flag).
        conviction_map: dict[str, ConvictionResult] = {}
        if use_conviction:
            try:
                fund_rows = con.execute(
                    f"""
                    SELECT ref_instrument_id,
                           roe, debt_to_equity, interest_coverage,
                           fcf_yield, pe_ttm,
                           revenue_cagr_5y, operating_margin
                    FROM ref_fundamentals_latest
                    WHERE ref_instrument_id IN ({placeholders})
                    """,
                    ids,
                ).fetchall()
                fund_cols = ["ref_instrument_id", "roe", "debt_to_equity",
                             "interest_coverage", "fcf_yield", "pe_ttm",
                             "revenue_cagr_5y", "operating_margin"]
                for row in fund_rows:
                    d = dict(zip(fund_cols, row))
                    conviction_map[d["ref_instrument_id"]] = conviction_score(d)
            except duckdb.CatalogException as e:
                print(f"    [WARN] ref_fundamentals_latest fehlt — Schema 0001_fundamentals.sql nicht geladen? ({e})", file=sys.stderr)
                print(f"           Value-Filter inaktiv fuer diesen run.", file=sys.stderr)
                use_conviction = False

        print("==> nova-lab screener_csp")
        print(f"    universe       : {universe_id}  ({len(universe)} underlyings)")
        print(f"    DTE range      : {cfg.min_dte}-{cfg.max_dte} Tage")
        print(f"    OTM buffer     : {cfg.buffer_min_pct:.1f}-{cfg.buffer_max_pct:.1f}%")
        print(f"    min yield      : {cfg.min_annualized_yield:.1f}% p.a.")
        print(f"    max spread     : {cfg.max_spread_pct:.1f}%")
        print(f"    top-n (overall): {top_n_overall}, per-symbol: {top_n_per_symbol}")
        print(f"    score config   : yield_full_credit={scfg.yield_full_credit_pct}%  excess_factor={scfg.yield_excess_factor}  buffer_weight={scfg.buffer_weight}")
        if use_conviction:
            cov = len(conviction_map)
            print(f"    value-filter   : on   conviction-coverage={cov}/{len(universe)}  min={min_conviction:.2f}")
        else:
            print(f"    value-filter   : off")
        print(f"    avoid_earnings : {cfg.avoid_earnings}  (cache max_age_days={earnings_max_age_days})")
        print(f"    output         : system_recommendations + CSV")
        print(f"    run_id         : {run_id}")
        print()

        # 2.5 Earnings refresh (skip when fresh)
        print("==> earnings calendar refresh (yfinance) ...")
        earn_universe = [(u[0], u[1]) for u in universe]
        earn_stats = refresh_earnings_for_universe(con, earn_universe, max_age_days=earnings_max_age_days)
        print(f"    fetched={earn_stats['fetched']} skipped_fresh={earn_stats['skipped_fresh']} no_data={earn_stats['no_data']} errors={earn_stats['errors']}")
        earnings_lookup = get_next_earnings_dates(con, [u[0] for u in universe])
        print(f"    {len(earnings_lookup)} underlyings haben bekannte zukuenftige earnings")
        print()

        # 3. Pro Underlying: chain + quotes
        all_candidates: list[CSPCandidate] = []
        all_evaluated: list[dict] = []   # auch nicht-passing fuer CSV
        skipped: list[tuple[str, str]] = []
        today = date.today()

        with IBOptionsClient() as ib:
            print(f"    IB connected client_id={ib.client_id}")
            print()
            for ix, (rid, symbol, con_id, exchange, currency, atype) in enumerate(universe, start=1):
                spot = spots.get(rid)
                if spot is None:
                    skipped.append((symbol, "kein spot in DB (ingest gelaufen?)"))
                    print(f"    [{ix:>2}/{len(universe)}] {symbol:<8s} SKIP — kein spot")
                    continue

                # Resolve underlying
                underlying = ib.resolve_underlying(symbol, exchange, currency, con_id)
                if underlying is None:
                    skipped.append((symbol, "underlying-resolve fail"))
                    print(f"    [{ix:>2}/{len(universe)}] {symbol:<8s} SKIP — underlying nicht resolvable")
                    continue

                # Chain params
                chain = ib.fetch_chain_params(underlying)
                if chain is None or not chain["expirations"] or not chain["strikes"]:
                    skipped.append((symbol, "no option chain"))
                    print(f"    [{ix:>2}/{len(universe)}] {symbol:<8s} SKIP — keine option chain")
                    continue

                # Filter expirations to DTE range
                valid_exps = []
                for exp_str in chain["expirations"]:
                    try:
                        exp_date = date(int(exp_str[:4]), int(exp_str[4:6]), int(exp_str[6:8]))
                    except (ValueError, IndexError):
                        continue
                    dte = (exp_date - today).days
                    if cfg.min_dte <= dte <= cfg.max_dte:
                        valid_exps.append((exp_str, exp_date, dte))
                valid_exps = valid_exps[:expirations_per_symbol]

                if not valid_exps:
                    skipped.append((symbol, f"keine exp in DTE-range {cfg.min_dte}-{cfg.max_dte}"))
                    print(f"    [{ix:>2}/{len(universe)}] {symbol:<8s} SKIP — keine exp in DTE-range")
                    continue

                # Filter strikes to OTM buffer range
                strike_min = spot * (1 - cfg.buffer_max_pct / 100)
                strike_max = spot * (1 - cfg.buffer_min_pct / 100)
                valid_strikes = [s for s in chain["strikes"] if strike_min <= s <= strike_max]

                if not valid_strikes:
                    skipped.append((symbol, f"keine strikes in OTM {cfg.buffer_min_pct}-{cfg.buffer_max_pct}% (spot={spot:.2f})"))
                    print(f"    [{ix:>2}/{len(universe)}] {symbol:<8s} SKIP — keine strikes in OTM-range")
                    continue

                # Fetch quotes
                exp_strs_only = [e[0] for e in valid_exps]
                try:
                    quotes = ib.fetch_put_quotes(
                        underlying, exp_strs_only, valid_strikes, currency,
                        chain_exchange=chain.get("exchange") or "SMART",
                        trading_class=chain.get("trading_class"),
                    )
                except Exception as e:  # noqa: BLE001
                    skipped.append((symbol, f"quote fetch failed: {e.__class__.__name__}: {e}"))
                    print(f"    [{ix:>2}/{len(universe)}] {symbol:<8s} FAIL quote-fetch: {e}")
                    continue

                # Build candidates
                exp_lookup = {e[0]: (e[1], e[2]) for e in valid_exps}
                viable_count = 0
                for q in quotes:
                    if q.bid is None:
                        continue
                    exp_meta = exp_lookup.get(q.expiration)
                    if exp_meta is None:
                        continue
                    exp_date, dte = exp_meta
                    cand = CSPCandidate(
                        ref_instrument_id  = rid,
                        symbol             = symbol,
                        spot               = float(spot),
                        expiration         = exp_date,
                        days_to_expiration = dte,
                        strike             = q.strike,
                        bid                = q.bid,
                        ask                = q.ask,
                        last               = q.last,
                        volume             = q.volume,
                        iv                 = q.iv,
                        currency           = currency,
                        next_earnings_date = earnings_lookup.get(rid),
                    )
                    ok, reject = passes_filter(cand, cfg)
                    sc = score_components(cand, scfg)
                    conv = conviction_map.get(rid)
                    conviction_val = conv.score if conv else (0.5 if use_conviction else 1.0)
                    final_score = sc.total * conviction_val if use_conviction else sc.total
                    # Hard-Gate (nur wenn min_conviction > 0 gesetzt).
                    if use_conviction and min_conviction > 0 and conviction_val < min_conviction:
                        ok = False
                        if not reject:
                            reject = f"conviction {conviction_val:.2f} < {min_conviction:.2f}"
                    all_evaluated.append({
                        "symbol":             symbol,
                        "ref_instrument_id":  rid,
                        "currency":           currency,
                        "spot":               spot,
                        "expiration":         exp_date.isoformat(),
                        "dte":                dte,
                        "strike":             cand.strike,
                        "bid":                cand.bid,
                        "ask":                cand.ask,
                        "iv":                 cand.iv,
                        "volume":             cand.volume,
                        "buffer_pct":         cand.buffer_pct,
                        "spread_pct":         cand.spread_pct,
                        "annualized_yield":   cand.annualized_yield_pct,
                        "score_yield":        sc.yield_score,
                        "score_buffer":       sc.buffer_bonus,
                        "score_spread_pen":   sc.spread_penalty,
                        "score_traditional":  sc.total,
                        "conviction":         conviction_val if use_conviction else None,
                        "score":              final_score,
                        "next_earnings_date": cand.next_earnings_date.isoformat() if cand.next_earnings_date else "",
                        "crosses_earnings":   cand.crosses_earnings,
                        "passes":             ok,
                        "reject_reason":      reject or "",
                    })
                    if ok:
                        all_candidates.append(cand)
                        viable_count += 1

                print(f"    [{ix:>2}/{len(universe)}] {symbol:<8s} spot={spot:>8.2f}  exp={len(valid_exps)} strikes={len(valid_strikes)} -> {len(quotes)} quotes, {viable_count} viable")

        # 4. Top-Auswahl. score_key injiziert Conviction-Multiplier in den Sort.
        if use_conviction:
            def _final_score(c):
                conv = conviction_map.get(c.ref_instrument_id)
                conv_val = conv.score if conv else 0.5
                return score_candidate(c, scfg) * conv_val
            top = select_top(all_candidates, top_n_per_symbol=top_n_per_symbol,
                              top_n_overall=top_n_overall, scfg=scfg, score_key=_final_score)
        else:
            top = select_top(all_candidates, top_n_per_symbol=top_n_per_symbol,
                              top_n_overall=top_n_overall, scfg=scfg)

        # 5. system_recommendations Voll-Sync (nur unsere added_by-Eintraege)
        con.execute(
            "DELETE FROM list_watchlist_members WHERE watchlist_id = ? AND added_by = ?",
            [SYSTEM_REC_WATCHLIST, ADDED_BY_TAG],
        )
        for c in top:
            note = (
                f"strike={c.strike:.2f} exp={c.expiration.isoformat()} dte={c.days_to_expiration} "
                f"bid={c.bid:.2f} ann_yield={c.annualized_yield_pct:.1f}% "
                f"buffer={c.buffer_pct:.1f}% (spot={c.spot:.2f})"
            )
            if c.next_earnings_date:
                note += f" next_earn={c.next_earnings_date.isoformat()}"
            if use_conviction:
                conv = conviction_map.get(c.ref_instrument_id)
                if conv:
                    note += f" {format_conviction_notes(conv)}"
                else:
                    note += " conviction=n/a"
            con.execute(
                """
                INSERT OR REPLACE INTO list_watchlist_members
                (watchlist_id, ref_instrument_id, added_by, notes)
                VALUES (?, ?, ?, ?)
                """,
                [SYSTEM_REC_WATCHLIST, c.ref_instrument_id, ADDED_BY_TAG, note],
            )

        # 6. Persistierung: alle evaluierten Strikes in mkt_options_snapshot
        #    (Side-Effect — erlaubt historische Premium-/IV-Trend-Analysen).
        snapshot_ts = date.today()
        snapshot_rows = 0
        try:
            for ev in all_evaluated:
                con.execute(
                    """
                    INSERT OR REPLACE INTO mkt_options_snapshot
                        (ref_instrument_id, expiration, strike, "right", ts, source,
                         bid, ask, last, volume, open_int, iv,
                         underlying_spot, dte, run_id)
                    VALUES (?, ?, ?, 'P', ?, 'ib', ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    [
                        ev["ref_instrument_id"],
                        date.fromisoformat(ev["expiration"]),
                        float(ev["strike"]),
                        snapshot_ts,
                        ev.get("bid"), ev.get("ask"), None,           # 'last' nicht im evaluated-dict
                        ev.get("volume"), ev.get("iv"),
                        ev.get("spot"), int(ev.get("dte", 0)),
                        run_id,
                    ],
                )
                snapshot_rows += 1
        except duckdb.CatalogException as e:
            print(f"    [WARN] mkt_options_snapshot fehlt — Schema 0004_options.sql nicht geladen? ({e})", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"    [WARN] snapshot-persist exception: {e.__class__.__name__}: {e}", file=sys.stderr)

        # 7. CSV-Export — alle evaluierten (auch rejected) fuer Audit
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        csv_file = OUTPUT_DIR / f"csp_{date.today().isoformat()}_{run_id}.csv"
        with csv_file.open("w", newline="") as f:
            if all_evaluated:
                writer = csv.DictWriter(f, fieldnames=list(all_evaluated[0].keys()))
                writer.writeheader()
                writer.writerows(all_evaluated)

        # 7. Summary
        print()
        print(f"==> done")
        print(f"    underlyings scanned : {len(universe)}")
        print(f"    skipped             : {len(skipped)}")
        print(f"    quotes evaluated    : {len(all_evaluated)}")
        print(f"    viable (post-filter): {len(all_candidates)}")
        print(f"    top selected        : {len(top)}  -> system_recommendations (added_by={ADDED_BY_TAG})")
        print(f"    snapshot persisted  : {snapshot_rows} rows -> mkt_options_snapshot")
        print(f"    csv                 : {csv_file}")
        if top:
            print()
            print("    Top-Empfehlungen (Score = yield_pkt + buffer_bonus - spread_penalty):")
            for c in top[:10]:
                sc = score_components(c, scfg)
                earn_s = f"  earn={c.next_earnings_date.isoformat()}" if c.next_earnings_date else ""
                print(
                    f"      {c.symbol:<8s} strike={c.strike:>8.2f}  exp={c.expiration.isoformat()}  bid={c.bid:>5.2f}  "
                    f"yield={c.annualized_yield_pct:>5.1f}%  buffer={c.buffer_pct:>4.1f}%  "
                    f"score={sc.total:>6.2f}  ({sc.yield_score:>5.1f}+{sc.buffer_bonus:>4.1f}-{sc.spread_penalty:>4.1f}){earn_s}"
                )
        if skipped:
            print()
            print("    Skips:")
            for sym, reason in skipped[:10]:
                print(f"      {sym:<8s} — {reason}")

    finally:
        con.close()

    return 0


# ---------- Main ----------

def cmd_refresh_earnings(args) -> int:
    """Manueller Refresh: holt earnings fuer alle csp_universe-Mitglieder
    unabhaengig von Cache-Alter. Nuetzlich bei Yfinance-Updates oder vor
    wichtigen Earnings-Wochen."""
    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        rows = con.execute(
            """
            SELECT m.ref_instrument_id, r.symbol
            FROM list_watchlist_members m
            JOIN ref_instruments r ON r.ref_instrument_id = m.ref_instrument_id
            WHERE m.watchlist_id = ?
              AND r.active = true
            ORDER BY r.symbol
            """,
            [UNIVERSE_WATCHLIST_DEFAULT],
        ).fetchall()
        if not rows:
            print(f"FEHLER: Watchlist '{UNIVERSE_WATCHLIST_DEFAULT}' ist leer.", file=sys.stderr)
            return 64
        print(f"==> Refreshing earnings fuer {len(rows)} underlyings (verbose) ...")
        # max_age_days=0 erzwingt frischen fetch
        stats = refresh_earnings_for_universe(con, [(r[0], r[1]) for r in rows], max_age_days=0, verbose=True)
        print()
        print(f"==> done: fetched={stats['fetched']} no_data={stats['no_data']} errors={stats['errors']}")

        # Show next earnings per symbol
        lookup = get_next_earnings_dates(con, [r[0] for r in rows])
        print()
        print("==> Naechste Earnings (zukuenftig):")
        if not lookup:
            print("    (keine bekannten earnings in DB)")
        else:
            for rid, ed in sorted(lookup.items(), key=lambda x: x[1]):
                sym = next((r[1] for r in rows if r[0] == rid), rid)
                dte = (ed - date.today()).days
                print(f"    {sym:<10s} {ed.isoformat()}  ({dte:+d} days)")
    finally:
        con.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="nova-lab CSP-Screener")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Default-Watchlist 'csp_universe' anlegen")

    p_run = sub.add_parser("run", help="Scan ausfuehren -> system_recommendations + CSV")
    p_run.add_argument("--params-file", dest="params_file", default=None,
                       help="JSON-params (overridet NOVA_PARAMS_FILE env)")

    p_refr = sub.add_parser("refresh-earnings", help="Manueller earnings-refresh fuer csp_universe")
    p_refr.add_argument("--params-file", dest="params_file", default=None,
                        help="JSON-params (overridet NOVA_PARAMS_FILE env)")

    args = parser.parse_args()

    dispatcher = {
        "init":             cmd_init,
        "run":              cmd_run,
        "refresh-earnings": cmd_refresh_earnings,
    }
    return dispatcher[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
