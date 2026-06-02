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


# ---- Earnings Quality ----

EQ_CATS = {
    "acquisition":   (["Akquisitionskosten"], "Akquisitionskosten"),
    "restructuring": (["Restrukturierung"], "Restrukturierungen"),
    "litigation":    (["Rechtsstreit/Settlement"], "Rechtsstreitigkeiten"),
    "tax":           (["Steueranpassungen"], "Steuertricks"),
    "one_time":      (["Einmaleffekte", "Wertminderung"], "Einmaleffekte"),
}


def _cat_subscore(categories: dict, labels) -> float:
    total = sum((categories or {}).get(k, 0) for k in labels)
    if total == 0:
        return 1.0
    return 0.5 if total == 1 else 0.0


def earnings_quality(sbc_cfo, categories, eqcfg: dict) -> dict:
    """Earnings-Quality-Score: SBC quantitativ + 5 Add-back-Kategorien.

    sbc_cfo: SBC/operativer Cashflow (oder None). categories: {label:count}
    aus dem Earnings-Exhibit (oder None, wenn kein Exhibit). Returns
    {score|None, rows:[(label, sub, detail)], bands, n_ok}.
    """
    wts = eqcfg["weights"]
    sbc_t = eqcfg["sbc_thresholds"]
    rows = []
    sbc_sub, sbc_det = None, "keine Daten"
    if sbc_cfo is not None:
        sbc_sub = (1.0 if sbc_cfo <= sbc_t["clean"]
                   else 0.0 if sbc_cfo > sbc_t["heavy"] else 0.5)
        sbc_det = f"SBC/op. CF {sbc_cfo * 100:.1f}%"
    rows.append(("sbc", "SBC (Aktienverguetung)", sbc_sub, sbc_det))
    for key, (labels, label) in EQ_CATS.items():
        if categories is None:
            rows.append((key, label, None, "kein Exhibit"))
        else:
            cnt = sum(categories.get(x, 0) for x in labels)
            rows.append((key, label, _cat_subscore(categories, labels),
                         "nicht erwaehnt" if cnt == 0
                         else f"{cnt}x erwaehnt (Add-back)"))
    num = den = 0.0
    for key, _lbl, sub, _d in rows:
        if sub is not None:
            num += sub * wts[key]; den += wts[key]
    score = round(100 * num / den) if den else None
    n_ok = sum(1 for _k, _l, s, _d in rows if s is not None)
    return {"score": score, "rows": rows, "bands": eqcfg["bands"],
            "n_ok": n_ok}


def owner_earnings(rows: list[dict]):
    """Owner Earnings (Buffett) je Jahr: NI + D&A − Maintenance CapEx.

    Maintenance CapEx per Greenwald: Kapitalintensitaet (PP&E/Umsatz,
    Fallback CapEx/Umsatz) × Umsatzzuwachs = Wachstums-CapEx; Maintenance
    = Gesamt-CapEx − Wachstums-CapEx. Returns (series, method).
    """
    def _abs(v):
        return abs(v) if v is not None else None
    ppe_r = [(d["ppe_gross"] / d["revenue"]) for d in rows
             if d.get("ppe_gross") is not None and d.get("revenue")]
    cap_r = [(_abs(d.get("capex")) / d["revenue"]) for d in rows
             if d.get("capex") is not None and d.get("revenue")]
    if ppe_r:
        intensity, method = sum(ppe_r) / len(ppe_r), "PP&E/Umsatz"
    elif cap_r:
        intensity, method = sum(cap_r) / len(cap_r), "CapEx/Umsatz"
    else:
        intensity, method = None, "—"
    out, prev_rev = [], None
    for d in rows:
        ni, da = d.get("net_income"), d.get("dep_amort")
        cx, rev = _abs(d.get("capex")), d.get("revenue")
        maint = None
        if cx is not None:
            if intensity is not None and prev_rev is not None \
                    and rev is not None:
                growth = intensity * max(0.0, rev - prev_rev)
                maint = min(cx, max(0.0, cx - growth))
            else:
                maint = cx
        oe = (ni + (da or 0.0) - maint
              if (ni is not None and maint is not None) else None)
        out.append({"period_end": d["period_end"], "oe": oe, "ni": ni,
                    "fcf": d.get("fcf"), "maint": maint})
        prev_rev = rev
    return out, method


def insider_conviction(tx: list[dict], n_years: int, cfg: dict) -> dict:
    """Insider Conviction Score aus flachen Form-4-Transaktionen.

    Gewichtet CEO-/CFO-Kauf, Cluster, Erstkauf; zieht nur *bedeutende*
    (nicht-10b5-1) Verkaeufe ab. Returns Summary inkl. label/points/fired.
    """
    import datetime as _dt
    cutoff = (_dt.date.today()
              - _dt.timedelta(days=int(n_years) * 365)).isoformat()
    rows = [t for t in tx if (t.get("transaction_date") or "") >= cutoff]
    buys = [t for t in rows if t.get("code") == "P"]
    sells = [t for t in rows if t.get("code") == "S"]
    buy_val = sum((t.get("value") or 0) for t in buys)
    sell_val = sum((t.get("value") or 0) for t in sells)
    n_buyers = len({t.get("owner") for t in buys})
    n_sellers = len({t.get("owner") for t in sells})
    ceo_buy = any(t.get("is_ceo") for t in buys)
    cfo_buy = any(t.get("is_cfo") for t in buys)
    cm = cfg["cluster_buyers_min"]
    cluster = n_buyers >= cm
    first = set()
    for t in buys:
        sf, sh = t.get("shares_following"), t.get("shares")
        if sf is not None and sh is not None and (sf - sh) <= 0.05 * max(sf, 1):
            first.add(t.get("owner"))
    first_buyers = len(first)
    pct = cfg["meaningful_sell_pct"]
    meaningful = routine = 0
    for t in sells:
        if t.get("planned"):
            routine += 1; continue
        sf, sh = t.get("shares_following"), t.get("shares")
        frac = (sh / (sh + sf)) if (sf is not None and sh is not None
                                    and (sh + sf) > 0) else None
        if frac is None or frac >= pct:
            meaningful += 1
    w = cfg["weights"]
    pts, fired = 0, []
    if ceo_buy:
        pts += w["ceo_buy"]; fired.append(("CEO-Kauf", w["ceo_buy"]))
    if cfo_buy:
        pts += w["cfo_buy"]; fired.append(("CFO-Kauf", w["cfo_buy"]))
    if cluster:
        pts += w["cluster_buy"]
        fired.append((f"Cluster ({n_buyers})", w["cluster_buy"]))
    if first_buyers > 0:
        pts += w["first_buy"]
        fired.append((f"Erstkauf ({first_buyers})", w["first_buy"]))
    if meaningful > 0:
        pts -= w["meaningful_sell"]
        fired.append((f"Bedeutender Verkauf ({meaningful})",
                      -w["meaningful_sell"]))
    sg = cfg["signal"]
    label = ("Bullisch" if pts >= sg["bullish_min"]
             else "Bearisch" if pts <= sg["bearish_max"] else "Neutral")
    return {"label": label, "points": pts, "buy_val": buy_val,
            "sell_val": sell_val, "n_buyers": n_buyers,
            "n_sellers": n_sellers, "ceo_buy": ceo_buy, "cfo_buy": cfo_buy,
            "cluster": cluster, "first_buyers": first_buyers,
            "meaningful": meaningful, "routine": routine, "fired": fired,
            "df_rows": rows}


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
