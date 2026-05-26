"""TTM- und CAGR-Berechnung aus ref_income_statement-Historie.

Read-only-Helfer. Verwendet die per sec_filings backfill-all bereitgelegten
Quartals- und Jahresfakten je Instrument.

Konventionen:
  - "TTM" = Trailing Twelve Months. Bevorzugt: Summe der letzten 4 Quartale
    (period_months = 3). Fallback: juengster 10-K (period_months = 12).
  - CAGR-5J = (TTM_now / TTM_5y_ago)^(1/5) - 1.
  - Q-YoY = juengstes Quartal vs. Vorjahres-Quartal mit gleichem End-Monat.

Wenn die Datenlage zu duenn ist (< 4Q und kein 10-K, oder kein 5y-Ankerpunkt
findbar), liefern die Funktionen None — der Filter behandelt das als
'Kriterium nicht erfuellbar' (= Hard-Fail).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import pandas as pd


def _parse_date(d) -> date | None:
    if d is None:
        return None
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    try:
        return date.fromisoformat(str(d)[:10])
    except (ValueError, TypeError):
        return None


def latest_anchor(con: duckdb.DuckDBPyConnection,
                   ref_id: str) -> date | None:
    """Juengstes period_end fuer das Instrument."""
    row = con.execute(
        "SELECT MAX(period_end) FROM ref_income_statement "
        "WHERE ref_instrument_id = ?", [ref_id],
    ).fetchone()
    return _parse_date(row[0]) if row else None


def compute_ttm(con: duckdb.DuckDBPyConnection,
                 ref_id: str,
                 anchor: date) -> dict | None:
    """TTM-Summen am Anker-Datum.

    Liefert dict mit revenue, net_income, operating_income, gross_profit,
    operating_expense (gleiche Skala wie ein Jahresbericht) und der Quelle
    ('ttm_4q' | 'annual_10k'). Returns None, wenn weder 4 Quartale noch
    ein 10-K binnen 380 Tagen vor dem Anker liegen.
    """
    df = con.execute("""
        SELECT period_end, period_months, revenue, net_income,
               operating_income, gross_profit, operating_expense
        FROM ref_income_statement
        WHERE ref_instrument_id = ?
          AND period_end <= ?
          AND period_end > CAST(? AS DATE) - INTERVAL '380' DAY
        ORDER BY period_end DESC
    """, [ref_id, anchor, anchor]).df()
    if df.empty:
        return None

    # Bevorzugt: 4 Quartale aggregieren.
    q = df[df["period_months"] == 3].head(4)
    if len(q) == 4:
        return {
            "revenue":          float(q["revenue"].sum(skipna=True))
                                 if q["revenue"].notna().all() else None,
            "net_income":       float(q["net_income"].sum(skipna=True))
                                 if q["net_income"].notna().all() else None,
            "operating_income": float(q["operating_income"].sum(skipna=True))
                                 if q["operating_income"].notna().all() else None,
            "gross_profit":     float(q["gross_profit"].sum(skipna=True))
                                 if q["gross_profit"].notna().all() else None,
            "operating_expense": float(q["operating_expense"].sum(skipna=True))
                                  if q["operating_expense"].notna().all() else None,
            "anchor_end":       str(q["period_end"].max())[:10],
            "source":           "ttm_4q",
        }
    # Fallback: juengstes 10-K.
    a = df[df["period_months"] == 12].head(1)
    if not a.empty:
        r = a.iloc[0]
        def _f(v): return float(v) if pd.notna(v) else None
        return {
            "revenue":          _f(r["revenue"]),
            "net_income":       _f(r["net_income"]),
            "operating_income": _f(r["operating_income"]),
            "gross_profit":     _f(r["gross_profit"]),
            "operating_expense": _f(r["operating_expense"]),
            "anchor_end":       str(r["period_end"])[:10],
            "source":           "annual_10k",
        }
    return None


def _cagr(now: float | None, then: float | None,
          years: float = 5.0) -> float | None:
    if now is None or then is None or then <= 0 or now <= 0:
        return None
    return (now / then) ** (1 / years) - 1


def compute_cagr_5y(con: duckdb.DuckDBPyConnection,
                     ref_id: str,
                     anchor: date) -> dict | None:
    """5J-CAGR fuer Revenue/Net-Income/Operating-Income (TTM-zu-TTM).

    Bei zu duenner Historie ist der 5y-Anker None -> Felder None.
    """
    now_ttm = compute_ttm(con, ref_id, anchor)
    if not now_ttm:
        return None
    five_y = anchor - timedelta(days=int(5 * 365.25))
    then_ttm = compute_ttm(con, ref_id, five_y)
    if not then_ttm:
        return {
            "revenue_cagr_5y":     None,
            "net_income_cagr_5y":  None,
            "op_income_cagr_5y":   None,
            "now_anchor":          now_ttm["anchor_end"],
            "then_anchor":         None,
        }
    return {
        "revenue_cagr_5y":    _cagr(now_ttm["revenue"], then_ttm["revenue"]),
        "net_income_cagr_5y": _cagr(now_ttm["net_income"], then_ttm["net_income"]),
        "op_income_cagr_5y":  _cagr(now_ttm["operating_income"],
                                     then_ttm["operating_income"]),
        "now_anchor":         now_ttm["anchor_end"],
        "then_anchor":        then_ttm["anchor_end"],
    }


def compute_q_yoy(con: duckdb.DuckDBPyConnection,
                   ref_id: str) -> dict | None:
    """YoY-Wachstum des juengsten Quartals (3-Monats-Fakt).

    Vergleicht das juengste period_months=3-Fact mit dem aus dem Vorjahr
    (Toleranz +/- 60 Tage um -365d).
    """
    df = con.execute("""
        SELECT period_end, revenue, net_income, operating_income
        FROM ref_income_statement
        WHERE ref_instrument_id = ? AND period_months = 3
        ORDER BY period_end DESC LIMIT 10
    """, [ref_id]).df()
    if len(df) < 2:
        return None
    df["period_end"] = pd.to_datetime(df["period_end"])
    latest = df.iloc[0]
    target = latest["period_end"] - pd.Timedelta(days=365)
    df["diff_days"] = (df["period_end"] - target).dt.days.abs()
    prior_candidates = df.iloc[1:][df.iloc[1:]["diff_days"] < 60]
    if prior_candidates.empty:
        return None
    prior = prior_candidates.iloc[0]

    def yoy(now, then):
        if pd.isna(now) or pd.isna(then) or then == 0:
            return None
        return float(now) / float(then) - 1

    return {
        "revenue_q_yoy":     yoy(latest["revenue"],     prior["revenue"]),
        "net_income_q_yoy":  yoy(latest["net_income"],  prior["net_income"]),
        "op_income_q_yoy":   yoy(latest["operating_income"],
                                  prior["operating_income"]),
        "latest_q_end":      str(latest["period_end"].date()),
        "prior_q_end":       str(prior["period_end"].date()),
    }


def compute_trends(con: duckdb.DuckDBPyConnection,
                    ref_id: str,
                    anchor: date,
                    cagr: dict | None,
                    qyoy: dict | None) -> dict:
    """Stufe-2-Flags: Beschleunigung, Margenausweitung, Profitabilitaets-Trend.

    Vergleicht TTM jetzt vs. TTM vor 12 Monaten fuer Margen-Ausweitung.
    """
    flags: dict[str, bool | None] = {
        "revenue_accelerating": None,
        "margin_expanding":     None,
        "profit_improving":     None,
    }

    # Revenue beschleunigt: Q-YoY > 5J-CAGR.
    if qyoy and cagr and qyoy.get("revenue_q_yoy") is not None \
            and cagr.get("revenue_cagr_5y") is not None:
        flags["revenue_accelerating"] = (
            qyoy["revenue_q_yoy"] > cagr["revenue_cagr_5y"])

    # Margen-Ausweitung: op_margin_TTM_now vs op_margin_TTM_vor_1J.
    now_ttm = compute_ttm(con, ref_id, anchor)
    prior_ttm = compute_ttm(con, ref_id,
                             anchor - timedelta(days=365))
    if now_ttm and prior_ttm \
            and now_ttm.get("operating_income") and now_ttm.get("revenue") \
            and prior_ttm.get("operating_income") and prior_ttm.get("revenue"):
        m_now   = now_ttm["operating_income"]   / now_ttm["revenue"]
        m_prior = prior_ttm["operating_income"] / prior_ttm["revenue"]
        flags["margin_expanding"] = m_now > m_prior
        flags["op_margin_ttm"]    = m_now
        flags["op_margin_ttm_prior"] = m_prior

    # Profit verbessert: TTM-Net-Income > TTM-Net-Income vor 1J.
    if now_ttm and prior_ttm \
            and now_ttm.get("net_income") is not None \
            and prior_ttm.get("net_income") is not None:
        flags["profit_improving"] = (
            now_ttm["net_income"] > prior_ttm["net_income"])

    return flags
