"""Historical-replay engine — bewertet das HEUTIGE Portfolio mit
HISTORISCHEN Quotes + FX-Rates.

Kernidee: pos_holdings (current quantities, unveraenderlich) gegen
mkt_quotes_daily (zeitreihen) joinen, fuer jeden Tag im Window Total
in base currency berechnen. Daraus dann worst-day / worst-week /
max-drawdown ableiten.

Caveat: wenn Holdings nicht so weit zurueckreichen (z.B. Symbol erst
seit 6 Monaten ingested, aber Replay-Window = 2 Jahre), fehlen Quotes.
coverage_pct misst das pro Tag. Default-Filter: nur Tage mit
mindestens MIN_COVERAGE_PCT ausgewerteter Holdings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb


# Default: wenn weniger als 80% der Holdings am Tag X einen Quote hatten,
# wird der Tag verworfen — Time-Series sollte aussagekraeftig bleiben.
MIN_COVERAGE_PCT_DEFAULT = 80.0


@dataclass
class DailyValue:
    ts:           date
    total_base:   float | None
    priced_lots:  int
    total_lots:   int

    @property
    def coverage_pct(self) -> float:
        if self.total_lots == 0:
            return 0.0
        return (self.priced_lots / self.total_lots) * 100


@dataclass
class DayDelta:
    ts:              date
    prev_ts:         date
    total_before:    float
    total_after:     float
    delta_abs:       float
    delta_pct:       float
    coverage_pct:    float


@dataclass
class DrawdownInfo:
    peak_ts:           date
    peak_val:          float
    trough_ts:         date
    trough_val:        float
    drawdown_abs:      float
    drawdown_pct:      float
    days_peak_to_trough: int
    recovered_ts:      date | None
    days_to_recovery:  int | None


# ---------- Core: portfolio value series ----------

def portfolio_value_series(
    con: duckdb.DuckDBPyConnection,
    base_currency: str,
    from_ts: date,
    to_ts: date,
    source: str | None = None,
    min_coverage_pct: float = MIN_COVERAGE_PCT_DEFAULT,
) -> list[DailyValue]:
    """Returns one DailyValue pro Tag in [from_ts, to_ts] mit Quote-Daten.

    source=None: nutze fuer jedes Instrument seine preferred_source aus
    ref_instruments. Sonst: explicit (z.B. 'ib').
    """
    # Pro Instrument: source determinieren (preferred_source aus ref_instruments
    # oder explicit override)
    # Wir filtern mkt_quotes_daily auf source IN (preferred, override) — pick lower-ranked
    # zur Eindeutigkeit. Einfacher als Run: explicit source-Filter.

    if source:
        source_clause = "AND lower(q.source) = lower(?)"
        source_params: list = [source]
    else:
        # Nutze preferred_source per Instrument
        source_clause = "AND lower(q.source) = lower(r.preferred_source)"
        source_params = []

    sql = f"""
        WITH dates_in_range AS (
            SELECT DISTINCT q.ts
            FROM mkt_quotes_daily q
            JOIN ref_instruments r ON r.ref_instrument_id = q.ref_instrument_id
            WHERE q.ts BETWEEN ? AND ?
              {source_clause}
        ),
        quote_for_date AS (
            SELECT q.ref_instrument_id, q.ts, q.close
            FROM mkt_quotes_daily q
            JOIN ref_instruments r ON r.ref_instrument_id = q.ref_instrument_id
            WHERE q.ts BETWEEN ? AND ?
              {source_clause}
        ),
        latest_fx AS (
            SELECT currency_from, ts, rate,
                   ROW_NUMBER() OVER (PARTITION BY currency_from, ts ORDER BY source) AS rn
            FROM mkt_fx_daily
            WHERE currency_to = ? AND ts BETWEEN ? AND ?
        ),
        fx_for_date AS (
            SELECT currency_from, ts, rate FROM latest_fx WHERE rn = 1
        ),
        holding_values AS (
            SELECT
                d.ts,
                h.ref_instrument_id,
                h.currency,
                h.quantity * q.close * COALESCE(
                    fx.rate,
                    CASE WHEN upper(h.currency) = upper(?) THEN 1.0 ELSE NULL END
                ) AS value_base
            FROM dates_in_range d
            CROSS JOIN pos_holdings h
            LEFT JOIN quote_for_date q
              ON q.ref_instrument_id = h.ref_instrument_id AND q.ts = d.ts
            LEFT JOIN fx_for_date fx
              ON fx.currency_from = h.currency AND fx.ts = d.ts
        )
        SELECT
            ts,
            SUM(value_base) AS total_base,
            COUNT(value_base) AS priced_lots,
            COUNT(*) AS total_lots
        FROM holding_values
        GROUP BY ts
        ORDER BY ts
    """
    params: list = [from_ts, to_ts, *source_params,
                    from_ts, to_ts, *source_params,
                    base_currency, from_ts, to_ts,
                    base_currency]
    rows = con.execute(sql, params).fetchall()

    out: list[DailyValue] = []
    for r in rows:
        dv = DailyValue(
            ts=r[0],
            total_base=r[1],
            priced_lots=int(r[2] or 0),
            total_lots=int(r[3] or 0),
        )
        if dv.coverage_pct >= min_coverage_pct:
            out.append(dv)
    return out


# ---------- Derivations ----------

def day_deltas(series: list[DailyValue]) -> list[DayDelta]:
    """Day-over-day deltas. Series muss bereits coverage-gefiltert sein."""
    out: list[DayDelta] = []
    for i in range(1, len(series)):
        prev = series[i - 1]
        curr = series[i]
        if prev.total_base is None or curr.total_base is None or prev.total_base == 0:
            continue
        out.append(DayDelta(
            ts=curr.ts,
            prev_ts=prev.ts,
            total_before=prev.total_base,
            total_after=curr.total_base,
            delta_abs=curr.total_base - prev.total_base,
            delta_pct=(curr.total_base / prev.total_base - 1) * 100,
            coverage_pct=curr.coverage_pct,
        ))
    return out


def worst_days(series: list[DailyValue], top_n: int = 10) -> list[DayDelta]:
    deltas = day_deltas(series)
    deltas.sort(key=lambda d: d.delta_pct)
    return deltas[:top_n]


def worst_weeks(series: list[DailyValue], window: int = 5, top_n: int = 10) -> list[DayDelta]:
    """Rolling N-Trading-Day-Window — schlimmste Wochen."""
    out: list[DayDelta] = []
    for i in range(window, len(series)):
        prev = series[i - window]
        curr = series[i]
        if prev.total_base is None or curr.total_base is None or prev.total_base == 0:
            continue
        out.append(DayDelta(
            ts=curr.ts,
            prev_ts=prev.ts,
            total_before=prev.total_base,
            total_after=curr.total_base,
            delta_abs=curr.total_base - prev.total_base,
            delta_pct=(curr.total_base / prev.total_base - 1) * 100,
            coverage_pct=curr.coverage_pct,
        ))
    out.sort(key=lambda d: d.delta_pct)
    return out[:top_n]


def max_drawdown(series: list[DailyValue]) -> DrawdownInfo | None:
    """Peak-to-Trough Drawdown. Sucht innerhalb der Series."""
    if not series:
        return None

    # Filter series auf nicht-Null totals
    cleaned = [s for s in series if s.total_base is not None]
    if not cleaned:
        return None

    peak_ts = cleaned[0].ts
    peak_val = cleaned[0].total_base

    max_dd_pct = 0.0
    info: DrawdownInfo | None = None

    for s in cleaned:
        v = s.total_base
        if v > peak_val:
            peak_val = v
            peak_ts = s.ts
        if peak_val > 0:
            dd_pct = (v / peak_val - 1) * 100
            if dd_pct < max_dd_pct:
                max_dd_pct = dd_pct
                info = DrawdownInfo(
                    peak_ts=peak_ts,
                    peak_val=peak_val,
                    trough_ts=s.ts,
                    trough_val=v,
                    drawdown_abs=v - peak_val,
                    drawdown_pct=dd_pct,
                    days_peak_to_trough=(s.ts - peak_ts).days,
                    recovered_ts=None,
                    days_to_recovery=None,
                )

    # Recovery: nach trough_ts wieder auf peak_val erreicht?
    if info is not None:
        for s in cleaned:
            if s.ts > info.trough_ts and s.total_base >= info.peak_val:
                info = DrawdownInfo(
                    peak_ts=info.peak_ts,
                    peak_val=info.peak_val,
                    trough_ts=info.trough_ts,
                    trough_val=info.trough_val,
                    drawdown_abs=info.drawdown_abs,
                    drawdown_pct=info.drawdown_pct,
                    days_peak_to_trough=info.days_peak_to_trough,
                    recovered_ts=s.ts,
                    days_to_recovery=(s.ts - info.trough_ts).days,
                )
                break

    return info
