"""Earnings-Calendar fetch + lookup helpers.

Quelle: yfinance.Ticker(symbol).calendar — Free, gute US-Stock-Coverage,
spaerlich bei DACH (egal — DACH-Underlyings haben eh kaum liquide options).

Persistiert in ref_earnings_calendar mit 7-Tage-Cache (refresh nur wenn
last_fetch > 7d alt). Reduziert yfinance-Last + erlaubt offline-Operation.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import duckdb


def _fetch_earnings_yfinance(symbol: str) -> list[date]:
    """Returns list of upcoming earnings-dates fuer Symbol. Leer bei keinen Daten.

    yfinance.calendar liefert dict (neuere Version) oder DataFrame (alt).
    Defensiv beide Cases handeln.
    """
    try:
        import yfinance as yf
    except ImportError:
        return []

    try:
        t = yf.Ticker(symbol)
        cal = t.calendar
    except Exception:  # noqa: BLE001
        return []

    if not cal:
        return []

    out: list[date] = []
    if isinstance(cal, dict):
        raw = cal.get("Earnings Date") or cal.get("earningsDate") or []
        if raw is None:
            return []
        if not isinstance(raw, (list, tuple)):
            raw = [raw]
        for x in raw:
            if isinstance(x, datetime):
                out.append(x.date())
            elif isinstance(x, date):
                out.append(x)
    # DataFrame-Variante (sehr alte yfinance) waere hier zu ergaenzen — selten.

    return out


def refresh_earnings_for_universe(
    con: duckdb.DuckDBPyConnection,
    universe: list[tuple[str, str]],   # [(ref_instrument_id, symbol), ...]
    max_age_days: int = 7,
    sleep_s: float = 0.2,
    verbose: bool = False,
) -> dict:
    """Fetch earnings fuer jedes Universe-Item wenn stale.
    Returnt stats-Dict {fetched, skipped_fresh, no_data, errors}."""
    stats = {"fetched": 0, "skipped_fresh": 0, "no_data": 0, "errors": 0}
    today = date.today()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    historical_floor = today - timedelta(days=30)  # Earnings aelter werden ignoriert

    for rid, symbol in universe:
        # Freshness-Check
        last = con.execute(
            "SELECT MAX(fetched_at) FROM ref_earnings_calendar WHERE ref_instrument_id = ?",
            [rid],
        ).fetchone()
        if last and last[0]:
            last_dt = last[0] if isinstance(last[0], datetime) else datetime.combine(last[0], datetime.min.time())
            # tz-aware compare
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if last_dt >= cutoff:
                stats["skipped_fresh"] += 1
                if verbose:
                    print(f"      [FRESH] {symbol}: last fetch {last_dt.date()}")
                continue

        try:
            dates = _fetch_earnings_yfinance(symbol)
        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            if verbose:
                print(f"      [ERR]   {symbol}: {e.__class__.__name__}")
            continue

        if not dates:
            stats["no_data"] += 1
            if verbose:
                print(f"      [NONE]  {symbol}: keine Earnings-Daten in yfinance")
            # Update fetched_at trotzdem mit Sentinel, damit wir nicht jedes Mal neu fragen
            # Verwende ein historisches sentinel-date (zB 1970-01-01) damit es nicht in queries auftaucht
            try:
                con.execute(
                    """INSERT OR REPLACE INTO ref_earnings_calendar
                       (ref_instrument_id, earnings_date, source, fetched_at)
                       VALUES (?, ?, 'yfinance-empty', ?)""",
                    [rid, date(1970, 1, 1), datetime.now(timezone.utc)],
                )
            except Exception:  # noqa: BLE001
                pass
            time.sleep(sleep_s)
            continue

        # Insert/replace dates >= historical_floor
        for ed in dates:
            if ed < historical_floor:
                continue
            con.execute(
                """INSERT OR REPLACE INTO ref_earnings_calendar
                   (ref_instrument_id, earnings_date, source, fetched_at)
                   VALUES (?, ?, 'yfinance', ?)""",
                [rid, ed, datetime.now(timezone.utc)],
            )
        stats["fetched"] += 1
        if verbose:
            print(f"      [OK]    {symbol}: {len(dates)} earnings dates -> {dates[0]}...")
        time.sleep(sleep_s)

    return stats


def get_next_earnings_dates(
    con: duckdb.DuckDBPyConnection,
    ref_instrument_ids: list[str],
    today: date | None = None,
) -> dict[str, date]:
    """Returns {ref_instrument_id: next_earnings_date} fuer naechstes earnings >= today.
    Missing key bedeutet: keine bekannte zukuenftige earnings (entweder None in
    yfinance, oder Cache zeigt sentinel 1970-01-01 = explicit leer)."""
    if today is None:
        today = date.today()
    if not ref_instrument_ids:
        return {}
    placeholders = ",".join(["?"] * len(ref_instrument_ids))
    rows = con.execute(
        f"""
        SELECT ref_instrument_id, MIN(earnings_date) AS next_earn
        FROM ref_earnings_calendar
        WHERE ref_instrument_id IN ({placeholders})
          AND earnings_date >= ?
          AND source != 'yfinance-empty'
        GROUP BY ref_instrument_id
        """,
        [*ref_instrument_ids, today],
    ).fetchall()
    return {r[0]: r[1] for r in rows}
