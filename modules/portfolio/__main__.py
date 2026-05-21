"""Portfolio-Show: aktuelle Bewertung + P&L pro Holding (B-Phase Schema).

Aufruf:
    python -m modules.portfolio              # Stdout-Tabelle
    python -m modules.portfolio --csv        # zusaetzlich CSV in nova_output

Joins:
    pos_holdings -> ref_instruments (via ref_instrument_id)
                 -> mkt_quotes_daily (latest <=ts, via ref_instrument_id + source)

Konfig (3-Tier):
    Tier 1 — Defaults (source = lower(ref.preferred_source), ts = max in DB)
    Tier 2 — Env: LAB_DB_PATH, NOVA_JOB_ID
    Tier 3 — JSON via NOVA_PARAMS_FILE:
      {
        "source": "ib",          // optional override, default = ref.preferred_source
        "ts":     "2026-05-02"   // optional, default = MAX(ts)
      }

Output (mit --csv):
    ~/nova_output/portfolio/portfolio_<YYYY-MM-DD>.csv
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import sys
from datetime import date, datetime, timezone

import duckdb

DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
OUTPUT_DIR = pathlib.Path.home() / "nova_output" / "portfolio"


def load_params() -> dict:
    pf = os.environ.get("NOVA_PARAMS_FILE")
    if not pf:
        return {}
    p = pathlib.Path(pf)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def resolve_ts(con: duckdb.DuckDBPyConnection, params: dict, source: str | None) -> date | None:
    if params.get("ts"):
        return date.fromisoformat(params["ts"])
    if source:
        row = con.execute(
            "SELECT MAX(ts) FROM mkt_quotes_daily WHERE source = ?",
            [source],
        ).fetchone()
    else:
        row = con.execute("SELECT MAX(ts) FROM mkt_quotes_daily").fetchone()
    return row[0] if row and row[0] else None


def query_portfolio(
    con: duckdb.DuckDBPyConnection,
    params: dict,
    ts: date | None,
) -> list[dict]:
    """Joine pos_holdings × ref_instruments × latest-quote.

    Wenn 'source' in params explizit gesetzt: nur diese Quote-Source.
    Sonst: pro Instrument die ref_instruments.preferred_source (lowercase).
    """
    explicit_source = params.get("source")

    # Quote-Filter: explicit source vs per-instrument preferred_source.
    # Wir matchen case-insensitive (mkt_quotes_daily.source ist lowercase wie 'ib'/'yfinance',
    # ref_instruments.preferred_source ist uppercase wie 'IB').
    if explicit_source:
        source_filter_sql = "AND lower(q.source) = lower(?)"
        source_params = [explicit_source]
    else:
        source_filter_sql = "AND lower(q.source) = lower(r.preferred_source)"
        source_params = []

    rows = con.execute(
        f"""
        WITH ranked AS (
            SELECT
                q.ref_instrument_id,
                q.ts,
                q.close,
                q.source,
                ROW_NUMBER() OVER (
                    PARTITION BY q.ref_instrument_id
                    ORDER BY q.ts DESC
                ) AS rn
            FROM mkt_quotes_daily q
            JOIN ref_instruments r ON r.ref_instrument_id = q.ref_instrument_id
            WHERE (? IS NULL OR q.ts <= ?)
              {source_filter_sql}
        ),
        latest AS (
            SELECT ref_instrument_id, ts AS quote_ts, close AS last_close, source AS quote_source
            FROM ranked WHERE rn = 1
        )
        SELECT
            h.ref_instrument_id,
            r.con_id,
            r.isin,
            r.symbol,
            r.exchange,
            r.name,
            r.asset_type,
            r.preferred_source,
            h.quantity,
            h.cost_per_share,
            h.currency,
            h.acquired_at,
            h.valid_from,
            h.broker,
            h.account,
            l.last_close,
            l.quote_ts,
            l.quote_source
        FROM pos_holdings h
        LEFT JOIN ref_instruments r ON r.ref_instrument_id = h.ref_instrument_id
        LEFT JOIN latest          l ON l.ref_instrument_id = h.ref_instrument_id
        WHERE h.valid_to IS NULL
        ORDER BY h.currency NULLS LAST, r.symbol, h.acquired_at
        """,
        [ts, ts, *source_params],
    ).fetchall()

    cols = [
        "ref_instrument_id",
        "con_id", "isin", "symbol", "exchange", "name", "asset_type", "preferred_source",
        "quantity", "cost_per_share", "currency", "acquired_at", "valid_from",
        "broker", "account",
        "last_close", "quote_ts", "quote_source",
    ]
    return [dict(zip(cols, r)) for r in rows]


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def fmt_num(v: float | None, places: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:,.{places}f}"


def fmt_signed(v: float | None, places: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:+,.{places}f}"


def render_groups(holdings: list[dict]) -> tuple[str, list[dict]]:
    by_ccy: dict[str, list[dict]] = {}
    for h in holdings:
        ccy = h["currency"] or "?"
        by_ccy.setdefault(ccy, []).append(h)

    lines: list[str] = []
    csv_rows: list[dict] = []

    for ccy in sorted(by_ccy.keys()):
        group = by_ccy[ccy]
        lines.append("")
        lines.append(f"=== {ccy} — {len(group)} Lots ===")
        lines.append(
            f"{'Symbol':<10} {'ISIN':<14} {'Broker':<12} "
            f"{'Qty':>10} {'Cost':>10} {'Last':>10} {'PnL%':>8} {'Value':>14} {'Cost-Basis':>14}"
        )
        sub_value = 0.0
        sub_cost = 0.0
        for h in group:
            qty = h["quantity"] or 0.0
            cost = h["cost_per_share"]
            last = h["last_close"]
            value = qty * last if last is not None else None
            cost_basis = qty * cost if cost is not None else None
            pnl_pct = (last / cost - 1) * 100 if (last is not None and cost not in (None, 0)) else None
            sym = h["symbol"] or "—"
            broker = (h["broker"] or "")[:12]

            lines.append(
                f"{sym:<10} {h['isin'] or '—':<14} {broker:<12} "
                f"{fmt_num(qty, 0):>10} "
                f"{fmt_num(cost):>10} "
                f"{fmt_num(last):>10} "
                f"{fmt_pct(pnl_pct):>8} "
                f"{fmt_num(value):>14} "
                f"{fmt_num(cost_basis):>14}"
            )

            if value is not None:
                sub_value += value
            if cost_basis is not None:
                sub_cost += cost_basis

            csv_rows.append({
                "currency":          ccy,
                "ref_instrument_id": h["ref_instrument_id"],
                "symbol":            sym,
                "isin":              h["isin"],
                "exchange":          h["exchange"],
                "broker":            h["broker"],
                "account":           h["account"],
                "acquired_at":       h["acquired_at"],
                "valid_from":        h["valid_from"],
                "asset_type":        h["asset_type"],
                "quantity":          qty,
                "cost_per_share":    cost,
                "last_close":        last,
                "quote_ts":          h["quote_ts"],
                "quote_source":      h["quote_source"],
                "pnl_pct":           pnl_pct,
                "value":             value,
                "cost_basis":        cost_basis,
            })

        sub_pnl_pct = (sub_value / sub_cost - 1) * 100 if sub_cost else None
        sub_pnl_abs = sub_value - sub_cost
        lines.append(
            f"{'':>59} {'TOTAL':>8} "
            f"{fmt_num(sub_value):>14} {fmt_num(sub_cost):>14}"
        )
        lines.append(
            f"{'':>59} "
            f"PnL {fmt_pct(sub_pnl_pct)}  ({fmt_signed(sub_pnl_abs)} {ccy})"
        )

    return "\n".join(lines), csv_rows


def latest_fx_to_base(
    con: duckdb.DuckDBPyConnection,
    base: str = "EUR",
    ts: date | None = None,
) -> dict[str, float]:
    """Returnt {currency_from -> rate_to_base} mit der letzten verfuegbaren Rate.
    EUR -> EUR ist immer 1.0 synthetisch.
    Returnt nur Currencies fuer die wir auch eine Rate haben (sonst NULL → fehlt im dict)."""
    rates: dict[str, float] = {base: 1.0}
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT currency_from, ts, rate,
                   ROW_NUMBER() OVER (PARTITION BY currency_from ORDER BY ts DESC) AS rn
            FROM mkt_fx_daily
            WHERE currency_to = ?
              AND (? IS NULL OR ts <= ?)
        )
        SELECT currency_from, rate FROM ranked WHERE rn = 1
        """,
        [base, ts, ts],
    ).fetchall()
    for ccy, rate in rows:
        if ccy and rate is not None:
            rates[ccy] = float(rate)
    return rates


def render_eur_consolidation(
    holdings: list[dict],
    fx_rates: dict[str, float],
    base: str = "EUR",
) -> str:
    """Footer-Section: Multi-Currency-Konsolidierung in base."""
    by_ccy: dict[str, dict] = {}  # ccy -> {value_local, cost_local, value_base, cost_base, missing}
    for h in holdings:
        ccy = h["currency"] or "?"
        last = h["last_close"]
        cost = h["cost_per_share"]
        qty = h["quantity"] or 0.0
        value_local = qty * last if last is not None else 0.0
        cost_local = qty * cost if cost is not None else 0.0

        agg = by_ccy.setdefault(ccy, {
            "value_local": 0.0, "cost_local": 0.0,
            "value_base": 0.0,  "cost_base": 0.0,
            "missing_fx": False, "missing_quote": 0,
        })
        agg["value_local"] += value_local
        agg["cost_local"]  += cost_local

        rate = fx_rates.get(ccy)
        if rate is None:
            agg["missing_fx"] = True
        else:
            agg["value_base"] += value_local * rate
            agg["cost_base"]  += cost_local * rate

        if last is None:
            agg["missing_quote"] += 1

    lines = ["", f"=== Konsolidierung in {base} ==="]
    lines.append(
        f"{'Ccy':<6} {'Rate→'+base:<10} {'Value (local)':>16} {'Cost (local)':>16} "
        f"{'Value ('+base+')':>16} {'Cost ('+base+')':>16}"
    )

    total_value_base = 0.0
    total_cost_base = 0.0
    has_warnings = False

    for ccy in sorted(by_ccy.keys()):
        a = by_ccy[ccy]
        rate = fx_rates.get(ccy)
        rate_s = f"{rate:.4f}" if rate is not None else "—"

        if a["missing_fx"]:
            warn = " [no FX rate]"
            has_warnings = True
            value_base_s = "—"
            cost_base_s = "—"
        else:
            warn = ""
            total_value_base += a["value_base"]
            total_cost_base  += a["cost_base"]
            value_base_s = fmt_num(a["value_base"])
            cost_base_s  = fmt_num(a["cost_base"])

        if a["missing_quote"]:
            warn += f" [{a['missing_quote']} no quote]"
            has_warnings = True

        lines.append(
            f"{ccy:<6} {rate_s:<10} "
            f"{fmt_num(a['value_local']):>16} {fmt_num(a['cost_local']):>16} "
            f"{value_base_s:>16} {cost_base_s:>16}{warn}"
        )

    pnl_base = total_value_base - total_cost_base
    pnl_pct = (total_value_base / total_cost_base - 1) * 100 if total_cost_base else None

    lines.append("")
    lines.append(
        f"{'GRAND TOTAL':<6} {'':<10} "
        f"{'':>16} {'':>16} "
        f"{fmt_num(total_value_base):>16} {fmt_num(total_cost_base):>16} {base}"
    )
    lines.append(
        f"PnL: {fmt_pct(pnl_pct)}  ({fmt_signed(pnl_base)} {base})"
    )

    if has_warnings:
        lines.append("")
        lines.append("[no FX rate]: lab_ingest_fx noch nicht gelaufen oder pair fehlt — Total ohne diese Currency.")
        lines.append("[N no quote]: N Lots ohne aktuelle quote in DB — werden mit value=0 aggregiert.")

    return "\n".join(lines)


def write_csv(csv_rows: list[dict], target: pathlib.Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not csv_rows:
        target.write_text("")
        return
    with target.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
        writer.writeheader()
        for row in csv_rows:
            writer.writerow(row)


def main() -> int:
    args = sys.argv[1:]
    write_csv_flag = "--csv" in args

    params = load_params()
    explicit_source = params.get("source")

    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    # portfolio show ist pure read — read_only erlaubt parallel zu Importern.
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        n_holdings = con.execute("SELECT count(*) FROM pos_holdings WHERE valid_to IS NULL").fetchone()
        if not n_holdings or n_holdings[0] == 0:
            print("==> nova-lab portfolio")
            print("    Keine Holdings. Erst importieren:")
            print("      python -m modules.portfolio.template ~/nova_lab_input/portfolio.xlsx")
            print("      <Excel ausfuellen>")
            print("      python -m modules.portfolio.import_xlsx ~/nova_lab_input/portfolio.xlsx")
            return 0

        ts = resolve_ts(con, params, explicit_source.lower() if explicit_source else None)
        holdings = query_portfolio(con, params, ts)

        print("==> nova-lab portfolio (B-Phase schema)")
        print(f"    db          : {DB_PATH}")
        print(f"    quote ts    : {ts.isoformat() if ts else '(none — kein ingest gelaufen?)'}")
        print(f"    quote source: {explicit_source or 'per-instrument preferred_source'}")
        print(f"    lots        : {len(holdings)}")

        no_quote = [h for h in holdings if h["last_close"] is None]
        if no_quote:
            print(f"    [WARN] {len(no_quote)} Lot(s) ohne Quote in DB:")
            for h in no_quote[:5]:
                print(f"           {h['ref_instrument_id']}  → run lab_ingest fuer Backfill")

        text, csv_rows = render_groups(holdings)
        print(text)

        # EUR-Konsolidierung (Multi-Currency-Aggregation)
        base = (params.get("base") or "EUR").upper()
        fx_rates = latest_fx_to_base(con, base=base, ts=ts)
        eur_text = render_eur_consolidation(holdings, fx_rates, base=base)
        print(eur_text)

        # CSV erweitern um base-currency-Felder
        if write_csv_flag:
            for row in csv_rows:
                ccy = row.get("currency")
                rate = fx_rates.get(ccy)
                row["fx_rate_to_base"] = rate
                row[f"value_{base.lower()}"] = (
                    row["value"] * rate if (row.get("value") is not None and rate is not None) else None
                )
                row[f"cost_basis_{base.lower()}"] = (
                    row["cost_basis"] * rate if (row.get("cost_basis") is not None and rate is not None) else None
                )

        if write_csv_flag:
            ts_str = (ts.isoformat() if ts else date.today().isoformat())
            csv_path = OUTPUT_DIR / f"portfolio_{ts_str}.csv"
            write_csv(csv_rows, csv_path)
            print()
            print(f"==> CSV: {csv_path}")

    finally:
        con.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
