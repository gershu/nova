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


def split_factor(splits: dict, period_iso: str) -> float:
    """Kumulierter Split-Faktor NACH period_iso (Anpassung auf heute)."""
    f = 1.0
    for d, r in (splits or {}).items():
        if d > period_iso:
            f *= r
    return f


def split_adjust_shares(rows: list[dict], splits: dict) -> list[dict]:
    """diluted_shares je Periode split-bereinigt (neue Dicts, Input unberuehrt)."""
    if not splits:
        return rows
    out = []
    for d in rows:
        sh = d.get("diluted_shares")
        if sh:
            out.append(dict(d, diluted_shares=sh * split_factor(
                splits, str(d.get("period_end"))[:10])))
        else:
            out.append(d)
    return out


def moat_signals(rows: list[dict], t: dict) -> dict:
    """Sechs Moat-Teilsignale -> {name: (score|None, detail)}.

    rows: Jahres-Metriken (diluted_shares idealerweise split-bereinigt),
    t: thresholds-dict (config moat.thresholds).
    """
    import statistics
    dates = [_to_date(d["period_end"]) for d in rows]
    span = ((dates[-1] - dates[0]).days / 365.25) if len(dates) >= 2 else 0.0
    out: dict = {}

    gm = [(d["gross_profit"] / d["revenue"]) for d in rows
          if d.get("gross_profit") is not None and d.get("revenue")]
    if len(gm) >= 2:
        slope_pp = (gm[-1] - gm[0]) * 100
        gt = t["gross_margin"]
        sc = (1.0 if slope_pp >= gt["improve_pp"]
              else 0.5 if slope_pp >= gt["stable_pp"] else 0.0)
        out["gross_margin_trend"] = (sc, f"Marge {gm[-1] * 100:.0f}%, "
                                     f"Δ {slope_pp:+.1f} pp")
    else:
        out["gross_margin_trend"] = (None, "zu wenig Daten")

    roics = [returns_from_metrics(d)["roic"] for d in rows]
    roics = [r for r in roics if r is not None]
    if len(roics) >= 2:
        mean = statistics.fmean(roics)
        cv = (statistics.pstdev(roics) / abs(mean)) if mean else 9.9
        rt = t["roic_stability"]
        sc = (1.0 if (mean >= rt["mean_min"] and cv <= rt["cv_max"])
              else 0.5 if (mean >= rt["mean_min"] * 0.6
                           and cv <= rt["cv_max"] * 1.8) else 0.0)
        out["roic_stability"] = (sc, f"Ø {mean * 100:.0f}%, CV {cv:.2f}")
    else:
        out["roic_stability"] = (None, "zu wenig Daten")

    last = rows[-1]
    fm_ = safe_div(last.get("fcf"), last.get("revenue"))
    if fm_ is not None:
        ft = t["fcf_margin"]
        sc = 1.0 if fm_ >= ft["high"] else 0.5 if fm_ >= ft["mid"] else 0.0
        out["fcf_margin"] = (sc, f"{fm_ * 100:.1f}% vom Umsatz")
    else:
        out["fcf_margin"] = (None, "keine FCF-/Umsatzdaten")

    rd_sum = sum(d["rd_expense"] for d in rows if d.get("rd_expense"))
    rev_first, rev_last = rows[0].get("revenue"), last.get("revenue")
    if rd_sum and rev_first is not None and rev_last is not None:
        eff = (rev_last - rev_first) / rd_sum
        et = t["rnd_efficiency"]
        sc = 1.0 if eff >= et["high"] else 0.5 if eff >= et["mid"] else 0.0
        out["rnd_efficiency"] = (sc, f"{eff:.1f}x Umsatz/F&E")
    else:
        out["rnd_efficiency"] = (None, "keine F&E ausgewiesen")

    rev_cagr = cagr(rev_first, rev_last, span)
    if rev_cagr is not None:
        mt = t["market_share_proxy"]
        sc = (1.0 if rev_cagr >= mt["rev_cagr_high"]
              else 0.5 if rev_cagr >= mt["rev_cagr_mid"] else 0.0)
        out["market_share_proxy"] = (sc, f"Umsatz-CAGR {rev_cagr * 100:.1f}% "
                                     "(Proxy)")
    else:
        out["market_share_proxy"] = (None, "zu wenig Daten")

    sh = [d.get("diluted_shares") for d in rows if d.get("diluted_shares")]
    sh_cagr = (cagr(sh[0], sh[-1], span) if len(sh) >= 2 else None)
    if sh_cagr is not None:
        bt = t["buybacks"]
        sc = (1.0 if sh_cagr <= bt["shrink_cagr"]
              else 0.0 if sh_cagr >= bt["dilute_cagr"] else 0.5)
        out["buybacks"] = (sc, f"Aktien {sh_cagr * 100:+.1f}% p.a.")
    else:
        out["buybacks"] = (None, "zu wenig Daten")
    return out


def _to_date(s):
    import datetime as _dt
    return _dt.date.fromisoformat(str(s)[:10])


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
