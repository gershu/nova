"""Reine Finanz-Rechenkerne fuer die vereinheitlichte Analyse-View.

Single Source fuer Kennzahlen-Logik (CAGR, Trendampel, Renditen), damit sie
nicht mehr in mehreren Views dupliziert wird. Kein Streamlit, keine I/O —
nur Zahlen rein/raus, isoliert testbar.
"""

from __future__ import annotations


def safe_div(a, b):
    if a is None or b is None:
        return None
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return None
    return a / b if b != 0 else None


def cagr(first, last, years):
    """Jaehrliche Wachstumsrate; None wenn nicht berechenbar."""
    if first is None or last is None or first <= 0 or last <= 0 \
            or years is None or years < 1:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def trend_ampel(vals, up: float = 0.5, down: float = -0.5):
    """Reihe (Bruchteile) -> (letzter_wert, slope_pp_p.a., emoji, delta_color).

    slope = lineare Regressionssteigung in %-Punkten je Periode. Ampel:
    >= up steigend, <= down fallend, sonst stabil; ⚪ bei < 2 Punkten.
    """
    pts = [(i, v * 100.0) for i, v in enumerate(vals) if v is not None]
    if not pts:
        return None, None, "⚪", "off"
    cur = pts[-1][1] / 100.0
    if len(pts) < 2:
        return cur, None, "⚪", "off"
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    denom = sum((p[0] - mx) ** 2 for p in pts)
    slope = (sum((p[0] - mx) * (p[1] - my) for p in pts) / denom
             if denom else 0.0)
    if slope >= up:
        return cur, slope, "🟢", "normal"
    if slope <= down:
        return cur, slope, "🔴", "normal"
    return cur, slope, "🟡", "off"


def returns_from_metrics(d: dict, tax_fallback: float = 0.21) -> dict:
    """ROIC/ROCE/ROE/ROA aus einem Jahres-Metrik-dict.

    Erwartete Keys: operating_income, pretax_income, tax_expense,
    net_income, equity, total_debt, cash_and_sti, total_assets,
    liabilities_current.
    """
    roe = safe_div(d.get("net_income"), d.get("equity"))
    roa = safe_div(d.get("net_income"), d.get("total_assets"))

    cap_emp = None
    ta, lc = d.get("total_assets"), d.get("liabilities_current")
    if ta is not None and lc is not None:
        cap_emp = ta - lc
    roce = safe_div(d.get("operating_income"), cap_emp)

    eff = safe_div(d.get("tax_expense"), d.get("pretax_income"))
    if eff is None or not (0.0 <= eff <= 0.6):
        eff = tax_fallback
    opinc = d.get("operating_income")
    nopat = opinc * (1 - eff) if opinc is not None else None
    inv = None
    td, eq = d.get("total_debt"), d.get("equity")
    if td is not None and eq is not None:
        inv = td + eq - (d.get("cash_and_sti") or 0.0)
        if inv <= 0:
            inv = None
    roic = safe_div(nopat, inv)
    return {"roic": roic, "roce": roce, "roe": roe, "roa": roa,
            "nopat": nopat, "inv_cap": inv, "eff_tax": eff}


def margin_series(rows: list[dict]) -> list[dict]:
    """Pro Periode Brutto-/operative/Netto-Marge (Anteil am Umsatz)."""
    out = []
    for r in rows:
        rev = r.get("revenue")
        out.append({
            "period_end": r.get("period_end"),
            "gross": safe_div(r.get("gross_profit"), rev),
            "operating": safe_div(r.get("operating_income"), rev),
            "net": safe_div(r.get("net_income"), rev),
        })
    return out
