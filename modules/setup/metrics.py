"""Setup-Metriken — jede Funktion holt einen einzelnen Skalar aus der DB.

Die YAML-Setup-Definitionen (config/setups.yaml) referenzieren diese
Metriken per Name. So bleibt die Konfiguration (Schwellwerte) tunebar,
waehrend die Datenbeschaffung kontrolliert + testbar im Code liegt.

Jede Metrik-Funktion: (con) -> float | None.
None bedeutet "Wert nicht verfuegbar" — der Detector behandelt eine
Bedingung mit None-Metrik als nicht-erfuellt (Setup triggert nicht).
"""

from __future__ import annotations

import duckdb


# ---------- Helpers ----------

def _latest_economic_value(con: duckdb.DuckDBPyConnection, series_id: str) -> float | None:
    """Letzter Wert einer FRED-Economic-Series."""
    try:
        row = con.execute("""
            SELECT value FROM mkt_economic_series
            WHERE series_id = ? AND source = 'fred'
            ORDER BY ts DESC LIMIT 1
        """, [series_id]).fetchone()
    except duckdb.Error:
        return None
    return float(row[0]) if row and row[0] is not None else None


# ---------- Markt-Metriken ----------

def vix_level(con: duckdb.DuckDBPyConnection) -> float | None:
    """Letzter VIXCLS-Wert."""
    return _latest_economic_value(con, "VIXCLS")


def vix_zscore_90(con: duckdb.DuckDBPyConnection) -> float | None:
    """VIXCLS Z-Score ueber rolling 90d-Fenster."""
    try:
        row = con.execute("""
            WITH w AS (
                SELECT value,
                       ROW_NUMBER() OVER (ORDER BY ts DESC) AS rk
                FROM mkt_economic_series
                WHERE series_id = 'VIXCLS' AND source = 'fred'
            ),
            stats AS (
                SELECT avg(value) AS m, stddev_samp(value) AS s
                FROM w WHERE rk <= 90
            ),
            latest AS (SELECT value FROM w WHERE rk = 1)
            SELECT (l.value - st.m) / NULLIF(st.s, 0)
            FROM latest l, stats st
        """).fetchone()
    except duckdb.Error:
        return None
    return float(row[0]) if row and row[0] is not None else None


def hy_spread(con: duckdb.DuckDBPyConnection) -> float | None:
    """ICE BofA US High-Yield Option-Adjusted Spread (%)."""
    return _latest_economic_value(con, "BAMLH0A0HYM2")


def yield_curve_2_10(con: duckdb.DuckDBPyConnection) -> float | None:
    """10Y-2Y Treasury-Spread (%). Negativ = invertiert."""
    return _latest_economic_value(con, "T10Y2Y")


# ---------- Portfolio / Risk-Metriken ----------

def max_position_weight_pct(con: duckdb.DuckDBPyConnection) -> float | None:
    """Hoechster Portfolio-Anteil einer Position, in % (EUR-basiert).

    Aggregiert pro ref_instrument_id (Broker-uebergreifend) — Konzentration
    ist ein Instrument-Risiko, nicht ein Broker-Risiko.
    """
    try:
        rows = con.execute("""
            SELECT ref_instrument_id, SUM(mtm_eur) AS mv
            FROM v_mkt_holdings
            WHERE mtm_eur IS NOT NULL
            GROUP BY ref_instrument_id
        """).fetchall()
    except duckdb.Error:
        return None
    if not rows:
        return None
    total = sum(r[1] for r in rows if r[1] is not None)
    if not total or total <= 0:
        return None
    return max(r[1] for r in rows if r[1] is not None) / total * 100.0


def stale_quote_count(con: duckdb.DuckDBPyConnection) -> float | None:
    """Anzahl gehaltener Instrumente mit Quote aelter als 5 Kalendertage."""
    try:
        row = con.execute("""
            SELECT count(*) FROM (
                SELECT ref_instrument_id
                FROM v_mkt_holdings
                WHERE quote_ts IS NULL
                   OR quote_ts < CURRENT_DATE - INTERVAL '5 days'
                GROUP BY ref_instrument_id
            )
        """).fetchone()
    except duckdb.Error:
        return None
    return float(row[0]) if row else None


# ---------- Registry ----------

METRICS = {
    "vix_level":               vix_level,
    "vix_zscore_90":           vix_zscore_90,
    "hy_spread":               hy_spread,
    "yield_curve_2_10":        yield_curve_2_10,
    "max_position_weight_pct": max_position_weight_pct,
    "stale_quote_count":       stale_quote_count,
}
