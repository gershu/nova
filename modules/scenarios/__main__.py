"""nova-lab scenarios CLI — Forward-Shocks auf Portfolio.

Subcommands:
    shock --symbol <S> --pct <p>            Single-symbol shock
    shock --currency <CCY> --pct <p>        FX shock (alle Positionen in CCY)
    shock --asset-class <c> --pct <p>       Class-shock (alle stocks/etfs/bonds...)
    shock --watchlist <wl> --pct <p>        Watchlist-Mitglieder shocken

    run <spec.json>                          Multi-Shock von JSON-File

Globale Flags:
    --base <CCY>           default EUR
    --ts   <YYYY-MM-DD>    default = latest in DB
    --top  <N>             top-N affected positions in der Tabelle (default 10)

Beispiele:
    python -m modules.scenarios shock --symbol AAPL --pct -25
    python -m modules.scenarios shock --currency USD --pct -10
    python -m modules.scenarios shock --asset-class stock --pct -15
    python -m modules.scenarios shock --watchlist buy_candidates --pct -20

JSON-Spec-Format (fuer 'run'):
    {
      "name": "Tech crash + USD weakness",
      "base_currency": "EUR",
      "shocks": [
        {"target": "symbol",      "value": "AAPL", "pct": -25},
        {"target": "asset_class", "value": "stock", "pct": -10},
        {"target": "currency",    "value": "USD",  "pct": -10}
      ]
    }

Shock-Komposition: most-specific wins (symbol > watchlist > asset_class).
Currency-Shock ist orthogonal — wird IMMER zusaetzlich angewandt.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import date

import duckdb

from .engine import Shock, apply_scenario, render_text
from .storage import (
    delete_scenario,
    ensure_schema,
    history,
    list_scenarios,
    load_scenario,
    persist_run,
    save_scenario,
)


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)


def _shock_from_args(args) -> Shock:
    """Konstruiere einen einzelnen Shock aus den --xxx CLI-Args."""
    targets = {
        "symbol":      args.symbol,
        "currency":    args.currency,
        "asset_class": args.asset_class,
        "watchlist":   args.watchlist,
    }
    set_targets = {k: v for k, v in targets.items() if v is not None}
    if len(set_targets) != 1:
        raise SystemExit(
            "FEHLER: Genau EINES von --symbol / --currency / --asset-class / --watchlist setzen."
        )
    target, value = next(iter(set_targets.items()))
    pct_decimal = args.pct / 100.0      # CLI: -25  -> -0.25
    return Shock(target=target, value=value, pct=pct_decimal)


def _shocks_from_spec(path: pathlib.Path) -> tuple[list[Shock], str | None, str | None]:
    """Returns (shocks, name, base_currency_override).

    Tolerant gegen den haeufigen User-Typo '+3' (JSON-strict erlaubt nur
    '3' oder '-3'). Falls erste json.loads fehlschlaegt, wird '+' vor Zahlen
    entfernt und nochmal probiert (mit INFO-Hinweis).
    """
    import re
    raw = path.read_text()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Strip '+' wenn nach :, Whitespace, Komma oder [ und vor Ziffer/Punkt.
        # Tippfehler: "pct": +3  →  "pct": 3
        normalized = re.sub(r'(?<=[:\s,\[])\+(?=\d|\.)', '', raw)
        try:
            data = json.loads(normalized)
            print(
                "[INFO] '+' vor Zahl(en) im JSON entfernt — JSON-strict erlaubt nur '3' oder '-3'.",
                file=sys.stderr,
            )
        except json.JSONDecodeError as e:
            raise SystemExit(f"FEHLER: spec ist kein valid JSON: {e}\n       Datei: {path}")

    shocks = []
    for item in data.get("shocks", []):
        target = item.get("target")
        value  = item.get("value")
        pct    = item.get("pct")
        if target is None or value is None or pct is None:
            raise SystemExit(f"FEHLER: shock-spec braucht target/value/pct: {item!r}")
        # pct in spec-Files: dezimal-Punkt-prozent ('pct: -25' = -25%, NICHT -2500%)
        shocks.append(Shock(target=str(target), value=str(value), pct=float(pct) / 100.0))
    return shocks, data.get("name"), data.get("base_currency")


def _resolve_ts(args) -> date | None:
    if args.ts:
        return date.fromisoformat(args.ts)
    return None


def cmd_shock(args) -> int:
    shock = _shock_from_args(args)
    return _execute([shock], args.base, _resolve_ts(args), top_n=args.top, name=None)


def cmd_run(args) -> int:
    spec_path = pathlib.Path(args.spec).expanduser()
    if not spec_path.is_file():
        print(f"FEHLER: spec-file nicht gefunden: {spec_path}", file=sys.stderr)
        return 64
    shocks, name, base_override = _shocks_from_spec(spec_path)
    if not shocks:
        print(f"FEHLER: spec enthaelt keine shocks.", file=sys.stderr)
        return 64
    base = base_override or args.base
    return _execute(shocks, base, _resolve_ts(args), top_n=args.top, name=name)


def _execute(shocks: list[Shock], base: str, ts: date | None, top_n: int, name: str | None) -> int:
    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        n_holdings = con.execute("SELECT count(*) FROM pos_holdings WHERE valid_to IS NULL").fetchone()[0]
        if not n_holdings:
            print("FEHLER: Keine Holdings in pos_holdings — erst portfolio importieren.", file=sys.stderr)
            return 64
        result = apply_scenario(con, shocks, base_currency=base.upper(), ts=ts)
    finally:
        con.close()

    if name:
        print(f"Scenario: {name}")
    print(render_text(result, top_n=top_n))
    return 0


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base", default="EUR", help="Base currency for consolidation (default EUR)")
    p.add_argument("--ts", default=None, help="Quote-ts cutoff YYYY-MM-DD (default latest)")
    p.add_argument("--top", type=int, default=10, help="Top-N affected positions to show (default 10)")


def cmd_save(args) -> int:
    """save <scenario_id> <spec.json> — persistiert spec in DB."""
    spec_path = pathlib.Path(args.spec).expanduser()
    if not spec_path.is_file():
        print(f"FEHLER: spec-file nicht gefunden: {spec_path}", file=sys.stderr)
        return 64
    shocks, name_from_spec, base_from_spec = _shocks_from_spec(spec_path)
    if not shocks:
        print(f"FEHLER: spec enthaelt keine shocks.", file=sys.stderr)
        return 64

    name = args.name or name_from_spec or args.scenario_id
    base = args.base or base_from_spec or "EUR"

    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        try:
            new = save_scenario(
                con,
                scenario_id=args.scenario_id,
                name=name,
                shocks=shocks,
                description=args.description,
                base_currency=base.upper(),
                tags=args.tags,
                overwrite=args.overwrite,
            )
        except ValueError as e:
            print(f"FEHLER: {e}", file=sys.stderr)
            return 64
        action = "neu angelegt" if new else "ueberschrieben"
        print(f"==> Scenario '{args.scenario_id}' {action}")
        print(f"    name         : {name}")
        print(f"    base_currency: {base}")
        print(f"    shocks       : {len(shocks)}")
        for ix, sh in enumerate(shocks):
            print(f"      [{ix}] {sh.target:<12s} {sh.value:<20s} pct={sh.pct*100:+.2f}%")
    finally:
        con.close()
    return 0


def cmd_list(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        ensure_schema_readonly_attempt(con)
        items = list_scenarios(con)
    finally:
        con.close()
    if not items:
        print("Keine saved scenarios. 'save <id> <spec.json>' anlegen.")
        return 0
    print(f"==> {len(items)} saved scenarios")
    print(f"{'scenario_id':<28s} {'name':<28s} {'base':<5s} {'active':<6s} {'shocks':>6s} {'runs':>5s} {'last_run':<12s} {'last_Δ%':>8s}  tags")
    for s in items:
        last_run_s = s["last_run_ts"].isoformat() if s["last_run_ts"] else "—"
        last_pct_s = f"{s['last_delta_pct']:+.2f}%" if s["last_delta_pct"] is not None else "—"
        print(
            f"{s['scenario_id']:<28s} {(s['name'] or '')[:28]:<28s} "
            f"{(s['base_currency'] or 'EUR'):<5s} {str(s['active']):<6s} "
            f"{s['shock_count']:>6d} {s['run_count']:>5d} {last_run_s:<12s} {last_pct_s:>8s}  "
            f"{s['tags'] or ''}"
        )
    return 0


def cmd_show(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        ensure_schema_readonly_attempt(con)
        loaded = load_scenario(con, args.scenario_id)
        if loaded is None:
            print(f"FEHLER: scenario '{args.scenario_id}' nicht gefunden.", file=sys.stderr)
            return 64
        meta, shocks = loaded
        runs = history(con, args.scenario_id, limit=args.history_limit)
    finally:
        con.close()

    print(f"==> Scenario: {meta['scenario_id']}")
    print(f"    name          : {meta['name']}")
    if meta["description"]:
        print(f"    description   : {meta['description']}")
    print(f"    base_currency : {meta['base_currency']}")
    print(f"    tags          : {meta['tags'] or '—'}")
    print(f"    active        : {meta['active']}")
    print(f"    created_at    : {meta['created_at']}")
    print(f"    updated_at    : {meta['updated_at']}")
    print()
    print(f"    Shocks ({len(shocks)}):")
    for ix, sh in enumerate(shocks):
        print(f"      [{ix}] {sh.target:<12s} {sh.value:<20s} pct={sh.pct*100:+.2f}%")

    if runs:
        print()
        print(f"    Recent runs (top {len(runs)}):")
        print(f"      {'ts':<12s} {'before':>14s} {'after':>14s} {'Δ abs':>14s} {'Δ %':>8s}")
        for r in runs:
            print(
                f"      {r['ts'].isoformat():<12s} "
                f"{r['portfolio_total_before']:>14,.2f} {r['portfolio_total_after']:>14,.2f} "
                f"{r['delta_abs']:>+14,.2f} {r['delta_pct']:>+7.2f}%"
            )
    else:
        print()
        print(f"    (noch nie ausgefuehrt — 'run-saved {meta['scenario_id']}' starten)")
    return 0


def cmd_delete(args) -> int:
    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        deleted = delete_scenario(con, args.scenario_id, also_runs=args.also_runs)
    finally:
        con.close()
    if deleted:
        suffix = " plus alle runs" if args.also_runs else " (run-history bleibt erhalten)"
        print(f"==> Scenario '{args.scenario_id}' geloescht{suffix}.")
        return 0
    print(f"[INFO] Scenario '{args.scenario_id}' war nicht da. Nichts zu tun.")
    return 0


def cmd_run_saved(args) -> int:
    """run-saved <id> — fetched saved scenario, fuehrt aus, persistiert run."""
    con = duckdb.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        loaded = load_scenario(con, args.scenario_id)
        if loaded is None:
            print(f"FEHLER: scenario '{args.scenario_id}' nicht gefunden. 'list' anschauen.", file=sys.stderr)
            return 64
        meta, shocks = loaded

        n_holdings = con.execute("SELECT count(*) FROM pos_holdings WHERE valid_to IS NULL").fetchone()[0]
        if not n_holdings:
            print("FEHLER: pos_holdings ist leer.", file=sys.stderr)
            return 64

        ts = _resolve_ts(args)
        base = args.base.upper() if args.base else (meta["base_currency"] or "EUR")
        result = apply_scenario(con, shocks, base_currency=base, ts=ts)

        # Persist run
        nova_run_id = os.environ.get("NOVA_JOB_ID")
        affected = sum(1 for v in result.valuations if v.delta_base and abs(v.delta_base) > 0.01)
        run_id = persist_run(
            con,
            scenario_id=meta["scenario_id"],
            ts=result.quote_ts,
            base_currency=base,
            portfolio_total_before=result.base_total_before,
            portfolio_total_after=result.base_total_after,
            holdings_count=len(result.valuations),
            affected_count=affected,
            nova_run_id=nova_run_id,
        )
    finally:
        con.close()

    print(f"Scenario: {meta['name']} (saved as '{meta['scenario_id']}')")
    print(render_text(result, top_n=args.top))
    print()
    print(f"==> persisted as sig_scenario_runs.run_id = {run_id}")
    return 0


def cmd_history(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        ensure_schema_readonly_attempt(con)
        runs = history(con, args.scenario_id, limit=args.limit)
    finally:
        con.close()
    if not runs:
        print(f"Keine runs fuer scenario '{args.scenario_id}'.")
        return 0
    print(f"==> {len(runs)} runs fuer '{args.scenario_id}' (neueste zuerst)")
    print(f"{'ts':<12s} {'base':<5s} {'before':>14s} {'after':>14s} {'Δ abs':>14s} {'Δ %':>8s}")
    for r in runs:
        print(
            f"{r['ts'].isoformat():<12s} {r['base_currency']:<5s} "
            f"{r['portfolio_total_before']:>14,.2f} {r['portfolio_total_after']:>14,.2f} "
            f"{r['delta_abs']:>+14,.2f} {r['delta_pct']:>+7.2f}%"
        )
    return 0


def ensure_schema_readonly_attempt(con):
    """Read-only DBs koennen kein DDL — defensive try/pass."""
    try:
        ensure_schema(con)
    except duckdb.Error:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="nova-lab scenarios CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # ---------- ad-hoc (existing) ----------
    p_shock = sub.add_parser("shock", help="Single-shock ad-hoc (kein DB-write)")
    _add_common_args(p_shock)
    p_shock.add_argument("--symbol", help="Symbol-shock (z.B. AAPL)")
    p_shock.add_argument("--currency", help="Currency-shock (z.B. USD)")
    p_shock.add_argument("--asset-class", dest="asset_class", help="Class-shock (intern: stock/etf/bond/...)")
    p_shock.add_argument("--watchlist", help="Watchlist-shock (z.B. buy_candidates)")
    p_shock.add_argument("--pct", type=float, required=True, help="Prozent-Aenderung (z.B. -25 fuer -25%%)")

    p_run = sub.add_parser("run", help="Multi-Shock von JSON-spec-file (kein DB-write)")
    _add_common_args(p_run)
    p_run.add_argument("spec", help="Pfad zur JSON spec")

    # ---------- saved scenarios (new) ----------
    p_save = sub.add_parser("save", help="Scenario aus JSON-spec in DB persistieren")
    p_save.add_argument("scenario_id", help="slug, z.B. 'tech_crash_2026'")
    p_save.add_argument("spec", help="Pfad zur JSON spec")
    p_save.add_argument("--name", help="Display-Name (sonst aus spec.name oder scenario_id)")
    p_save.add_argument("--description", help="Optional Beschreibung")
    p_save.add_argument("--base", help="Base-Currency (sonst aus spec oder EUR)")
    p_save.add_argument("--tags", help="Comma-separated tags (z.B. 'stress,tech')")
    p_save.add_argument("--overwrite", action="store_true", help="Vorhandene scenario_id ersetzen")

    p_list = sub.add_parser("list", help="Alle saved scenarios + run-counts")

    p_show = sub.add_parser("show", help="Details + recent runs")
    p_show.add_argument("scenario_id")
    p_show.add_argument("--history-limit", type=int, default=10, dest="history_limit")

    p_delete = sub.add_parser("delete", help="Scenario loeschen")
    p_delete.add_argument("scenario_id")
    p_delete.add_argument("--also-runs", action="store_true", dest="also_runs",
                          help="Auch run-history loeschen (default: bleibt als Audit erhalten)")

    p_run_saved = sub.add_parser("run-saved", help="Saved scenario ausfuehren + persist run")
    _add_common_args(p_run_saved)
    p_run_saved.add_argument("scenario_id")

    p_history = sub.add_parser("history", help="Time-series der past runs")
    p_history.add_argument("scenario_id")
    p_history.add_argument("--limit", type=int, default=30)

    args = parser.parse_args()

    dispatcher = {
        "shock":      cmd_shock,
        "run":        cmd_run,
        "save":       cmd_save,
        "list":       cmd_list,
        "show":       cmd_show,
        "delete":     cmd_delete,
        "run-saved":  cmd_run_saved,
        "history":    cmd_history,
    }
    return dispatcher[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
