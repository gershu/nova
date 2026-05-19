"""Scenario engine — Forward-Shocks auf aktuelles Portfolio.

Reine Read-Only-Analyse. Liest pos_holdings + mkt_quotes_daily +
mkt_fx_daily + ref_instruments. Schreibt nichts.

Shock-Komposition:
  Price-Shocks (symbol/watchlist/asset_class) sind alternativ — most-specific
  wins. Reihenfolge: symbol > watchlist > asset_class.
  Currency-Shocks sind orthogonal — werden ZUSAETZLICH angewandt
  (Price-Shock im Local-Layer, FX-Shock im Conversion-Layer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import duckdb


# ---------- Datentypen ----------

@dataclass(frozen=True)
class Holding:
    holding_id:        str
    ref_instrument_id: str
    symbol:            str | None
    asset_type:        str | None
    currency:          str
    quantity:          float
    cost_per_share:    float | None
    last_close:        float | None      # local currency
    quote_ts:          date | None


@dataclass(frozen=True)
class Shock:
    target: str   # 'symbol', 'currency', 'asset_class', 'watchlist'
    value: str    # symbol-name, ccy, internal asset-type ('stock','etf'), watchlist-id
    pct:    float # -0.25 fuer -25%

    def __post_init__(self) -> None:
        valid_targets = {"symbol", "currency", "asset_class", "watchlist"}
        if self.target not in valid_targets:
            raise ValueError(f"Shock.target must be one of {valid_targets}, got {self.target!r}")


@dataclass
class HoldingValuation:
    holding:             Holding
    value_local_before:  float | None
    value_local_after:   float | None
    fx_rate_before:      float | None
    fx_rate_after:       float | None
    value_base_before:   float | None
    value_base_after:    float | None
    applied_shocks:      list[str] = field(default_factory=list)

    @property
    def delta_base(self) -> float | None:
        if self.value_base_before is None or self.value_base_after is None:
            return None
        return self.value_base_after - self.value_base_before

    @property
    def delta_pct(self) -> float | None:
        if self.value_base_before is None or self.value_base_after is None or self.value_base_before == 0:
            return None
        return (self.value_base_after / self.value_base_before - 1) * 100


@dataclass
class ScenarioResult:
    base_currency:        str
    quote_ts:             date | None
    shocks:               list[Shock]
    valuations:           list[HoldingValuation]
    base_total_before:    float
    base_total_after:     float
    base_currency_totals: dict[str, dict]      # ccy -> {before_local, after_local, before_base, after_base}
    missing_quotes:       int
    missing_fx:           list[str]            # currencies ohne fx-rate

    @property
    def delta_abs(self) -> float:
        return self.base_total_after - self.base_total_before

    @property
    def delta_pct(self) -> float:
        if self.base_total_before == 0:
            return 0.0
        return (self.base_total_after / self.base_total_before - 1) * 100


# ---------- Loader ----------

def load_holdings_with_latest_quote(
    con: duckdb.DuckDBPyConnection,
    ts: date | None = None,
) -> list[Holding]:
    """Fuer jedes pos_holdings-row: latest mkt_quotes_daily.close fuer
    instrument unter <=ts (oder global latest wenn ts=None)."""
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT q.ref_instrument_id, q.ts, q.close,
                   ROW_NUMBER() OVER (PARTITION BY q.ref_instrument_id ORDER BY q.ts DESC) AS rn
            FROM mkt_quotes_daily q
            WHERE (? IS NULL OR q.ts <= ?)
        ),
        latest AS (SELECT ref_instrument_id, ts AS quote_ts, close AS last_close FROM ranked WHERE rn = 1)
        SELECT
            h.holding_id, h.ref_instrument_id, r.symbol, r.asset_type,
            h.currency, h.quantity, h.cost_per_share,
            l.last_close, l.quote_ts
        FROM pos_holdings h
        LEFT JOIN ref_instruments r ON r.ref_instrument_id = h.ref_instrument_id
        LEFT JOIN latest          l ON l.ref_instrument_id = h.ref_instrument_id
        ORDER BY h.currency, r.symbol
        """,
        [ts, ts],
    ).fetchall()
    return [
        Holding(
            holding_id=r[0], ref_instrument_id=r[1], symbol=r[2], asset_type=r[3],
            currency=r[4], quantity=r[5] or 0.0, cost_per_share=r[6],
            last_close=r[7], quote_ts=r[8],
        )
        for r in rows
    ]


def load_fx_to_base(
    con: duckdb.DuckDBPyConnection,
    base: str,
    ts: date | None = None,
) -> dict[str, float]:
    """{currency_from -> rate_to_base} fuer letzte verfuegbare ts.
    EUR -> EUR ist immer 1.0 (synthetisch)."""
    rates: dict[str, float] = {base: 1.0}
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT currency_from, ts, rate,
                   ROW_NUMBER() OVER (PARTITION BY currency_from ORDER BY ts DESC) AS rn
            FROM mkt_fx_daily
            WHERE currency_to = ? AND (? IS NULL OR ts <= ?)
        )
        SELECT currency_from, rate FROM ranked WHERE rn = 1
        """,
        [base, ts, ts],
    ).fetchall()
    for ccy, rate in rows:
        if ccy and rate is not None:
            rates[ccy] = float(rate)
    return rates


def load_watchlist_members(con: duckdb.DuckDBPyConnection, watchlist_id: str) -> set[str]:
    """Returnt set of ref_instrument_ids in einer Watchlist. Leer wenn list fehlt."""
    try:
        rows = con.execute(
            "SELECT ref_instrument_id FROM list_watchlist_members WHERE watchlist_id = ?",
            [watchlist_id],
        ).fetchall()
    except duckdb.CatalogException:
        return set()
    return {r[0] for r in rows}


# ---------- Engine ----------

def _resolve_price_shock(
    holding: Holding,
    shocks: list[Shock],
    watchlist_memberships: dict[str, set[str]],
) -> tuple[float, str | None]:
    """Returns (pct_change, source_label). Most-specific wins:
    symbol > watchlist > asset_class. Currency shocks NICHT hier
    (orthogonal). 0.0 wenn nichts greift."""
    # symbol
    for s in shocks:
        if s.target == "symbol" and holding.symbol and s.value.upper() == holding.symbol.upper():
            return s.pct, f"symbol={s.value}"

    # watchlist (alle relevanten in Reihenfolge)
    for s in shocks:
        if s.target == "watchlist":
            members = watchlist_memberships.get(s.value, set())
            if holding.ref_instrument_id in members:
                return s.pct, f"watchlist={s.value}"

    # asset_class
    for s in shocks:
        if s.target == "asset_class" and holding.asset_type and s.value.lower() == holding.asset_type.lower():
            return s.pct, f"asset_class={s.value}"

    return 0.0, None


def _resolve_fx_shock(holding: Holding, shocks: list[Shock]) -> tuple[float, str | None]:
    """Currency-Shock fuer dieses Holding. 0.0 wenn keiner."""
    for s in shocks:
        if s.target == "currency" and holding.currency and s.value.upper() == holding.currency.upper():
            return s.pct, f"currency={s.value}"
    return 0.0, None


def apply_scenario(
    con: duckdb.DuckDBPyConnection,
    shocks: list[Shock],
    base_currency: str = "EUR",
    ts: date | None = None,
) -> ScenarioResult:
    """Hauptfunktion: laed alles, applied Shocks, returnt Result."""
    holdings = load_holdings_with_latest_quote(con, ts)
    fx_rates = load_fx_to_base(con, base_currency, ts)

    # Pre-load watchlist memberships fuer alle watchlist-shocks
    watchlist_ids = {s.value for s in shocks if s.target == "watchlist"}
    wl_memberships = {wl: load_watchlist_members(con, wl) for wl in watchlist_ids}

    valuations: list[HoldingValuation] = []
    missing_quotes = 0
    missing_fx_set: set[str] = set()
    ccy_totals: dict[str, dict] = {}

    base_total_before = 0.0
    base_total_after  = 0.0

    for h in holdings:
        # value_local
        if h.last_close is None:
            value_local_before = None
            missing_quotes += 1
        else:
            value_local_before = h.quantity * h.last_close

        # apply price shock
        price_pct, price_label = _resolve_price_shock(h, shocks, wl_memberships)
        if value_local_before is not None:
            value_local_after = value_local_before * (1 + price_pct)
        else:
            value_local_after = None

        # apply fx shock
        fx_rate_before = fx_rates.get(h.currency)
        fx_pct, fx_label = _resolve_fx_shock(h, shocks)
        if fx_rate_before is None and h.currency != base_currency:
            missing_fx_set.add(h.currency)
            fx_rate_after = None
        else:
            fx_rate_after = fx_rate_before * (1 + fx_pct) if fx_rate_before is not None else None

        # value_base
        if value_local_before is not None and fx_rate_before is not None:
            value_base_before = value_local_before * fx_rate_before
            base_total_before += value_base_before
        else:
            value_base_before = None

        if value_local_after is not None and fx_rate_after is not None:
            value_base_after = value_local_after * fx_rate_after
            base_total_after += value_base_after
        else:
            value_base_after = None

        applied = []
        if price_label:
            applied.append(price_label)
        if fx_label:
            applied.append(fx_label)

        valuations.append(HoldingValuation(
            holding=h,
            value_local_before=value_local_before,
            value_local_after=value_local_after,
            fx_rate_before=fx_rate_before,
            fx_rate_after=fx_rate_after,
            value_base_before=value_base_before,
            value_base_after=value_base_after,
            applied_shocks=applied,
        ))

        # Currency-totals
        ct = ccy_totals.setdefault(h.currency, {
            "before_local": 0.0, "after_local": 0.0,
            "before_base": 0.0,  "after_base": 0.0,
            "lots": 0,
        })
        ct["lots"] += 1
        if value_local_before is not None:
            ct["before_local"] += value_local_before
        if value_local_after is not None:
            ct["after_local"] += value_local_after
        if value_base_before is not None:
            ct["before_base"] += value_base_before
        if value_base_after is not None:
            ct["after_base"] += value_base_after

    return ScenarioResult(
        base_currency=base_currency,
        quote_ts=ts,
        shocks=shocks,
        valuations=valuations,
        base_total_before=base_total_before,
        base_total_after=base_total_after,
        base_currency_totals=ccy_totals,
        missing_quotes=missing_quotes,
        missing_fx=sorted(missing_fx_set),
    )


# ---------- Renderer ----------

def fmt_num(v: float | None, places: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:,.{places}f}"


def fmt_signed(v: float | None, places: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:+,.{places}f}"


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def render_text(result: ScenarioResult, *, top_n: int = 10) -> str:
    lines: list[str] = []
    lines.append(f"==> Scenario")
    lines.append(f"    base currency : {result.base_currency}")
    lines.append(f"    quote ts      : {result.quote_ts.isoformat() if result.quote_ts else '(latest)'}")
    lines.append(f"    holdings      : {len(result.valuations)}")
    lines.append(f"    shocks        : {len(result.shocks)}")
    for s in result.shocks:
        lines.append(f"      - {s.target:<12s} {s.value:<20s} pct={s.pct*100:+.2f}%")

    if result.missing_quotes:
        lines.append(f"    [WARN] {result.missing_quotes} holdings ohne quote — als 0 gewertet")
    if result.missing_fx:
        lines.append(f"    [WARN] FX-rates fehlen fuer: {', '.join(result.missing_fx)} — diese Positionen fehlen im Total")

    # Per-Currency Impact (in base)
    lines.append("")
    lines.append(f"=== Per-Currency Impact (base = {result.base_currency}) ===")
    lines.append(f"{'Ccy':<5s} {'Lots':>5s} {'Before-Local':>16s} {'After-Local':>16s} {'Before-Base':>16s} {'After-Base':>16s} {'Δ Base':>16s} {'Δ %':>9s}")
    for ccy in sorted(result.base_currency_totals.keys()):
        ct = result.base_currency_totals[ccy]
        delta_base = ct["after_base"] - ct["before_base"]
        delta_pct = ((ct["after_base"] / ct["before_base"] - 1) * 100) if ct["before_base"] else None
        lines.append(
            f"{ccy:<5s} {ct['lots']:>5d} "
            f"{fmt_num(ct['before_local']):>16s} {fmt_num(ct['after_local']):>16s} "
            f"{fmt_num(ct['before_base']):>16s} {fmt_num(ct['after_base']):>16s} "
            f"{fmt_signed(delta_base):>16s} {fmt_pct(delta_pct):>9s}"
        )

    # Top-N affected positions (by absolute delta in base)
    affected = [
        v for v in result.valuations
        if v.delta_base is not None and abs(v.delta_base) > 0.01
    ]
    affected.sort(key=lambda v: abs(v.delta_base or 0), reverse=True)

    lines.append("")
    lines.append(f"=== Top affected positions (top {min(top_n, len(affected))} of {len(affected)}) ===")
    if affected:
        lines.append(
            f"{'Symbol':<10s} {'Ccy':<4s} {'Qty':>10s} {'Last':>10s}->{'After':<10s} "
            f"{'Δ Local':>12s} {'Δ Base':>12s} {'Δ %':>8s}  Shocks"
        )
        for v in affected[:top_n]:
            sym = v.holding.symbol or v.holding.ref_instrument_id[:10]
            shocks_s = ", ".join(v.applied_shocks) or "—"
            lines.append(
                f"{sym:<10s} {v.holding.currency:<4s} "
                f"{fmt_num(v.holding.quantity, 0):>10s} "
                f"{fmt_num(v.holding.last_close):>10s}->{fmt_num(v.value_local_after / v.holding.quantity if v.value_local_after and v.holding.quantity else None):<10s} "
                f"{fmt_signed((v.value_local_after or 0) - (v.value_local_before or 0)):>12s} "
                f"{fmt_signed(v.delta_base):>12s} "
                f"{fmt_pct(v.delta_pct):>8s}  {shocks_s}"
            )
    else:
        lines.append("    (keine Position betroffen)")

    # Grand total
    lines.append("")
    lines.append(f"=== GRAND TOTAL ({result.base_currency}) ===")
    lines.append(f"    Before : {fmt_num(result.base_total_before):>14s} {result.base_currency}")
    lines.append(f"    After  : {fmt_num(result.base_total_after):>14s} {result.base_currency}")
    lines.append(f"    Δ      : {fmt_signed(result.delta_abs):>14s} {result.base_currency}  ({fmt_pct(result.delta_pct)})")

    return "\n".join(lines)
