"""Ad-Hoc Analysis — Qualitaetspruefung beliebiger Aktien on-Demand.

Frei waehlbarer Ticker (muss NICHT im persistierten Universum sein). Die
Daten werden bei Bedarf direkt von sec-api.io gezogen und NICHT in der
DuckDB gespeichert. Ein In-Memory-Cache (st.cache_data) verhindert nur
unnoetige Doppelaufrufe innerhalb der Session.

Themen nach Michael Shearn, "The Investment Checklist":
  1. Balance Sheet — Bilanzstaerke
  2. Return on Capital — ROIC / ROCE / ROE / ROA
Weitere (Insider, SBC, GAAP vs non-GAAP) folgen schrittweise.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules.dashboard.components.format import _missing, de_dec, de_int
from datetime import date, timedelta

from modules.sec_filings.client import (
    INSIDER_CODE_LABELS, SecApiError, analyze_non_gaap,
    fetch_balance_sheet_from_filing, fetch_exhibit_text,
    fetch_insider_transactions, fetch_sbc_from_filing,
    fetch_statements_from_filing, find_earnings_exhibits, find_filings,
)


# ---------- Formatierung ----------

def _money(v, cur: str = "USD") -> str:
    if _missing(v):
        return "—"
    a = abs(float(v))
    if a >= 1e9:
        return f"{de_dec(v / 1e9, 2)} Mrd {cur}"
    if a >= 1e6:
        return f"{de_dec(v / 1e6, 1)} Mio {cur}"
    return f"{de_int(v)} {cur}"


def _ratio(v, places: int = 2) -> str:
    return "—" if _missing(v) else de_dec(v, places)


def _pct(v, places: int = 1) -> str:
    return "—" if _missing(v) else de_dec(float(v) * 100.0, places) + " %"


def _div(a, b):
    if _missing(a) or _missing(b) or float(b) == 0:
        return None
    return float(a) / float(b)


def _verdict_box(checks, strong="stark", mixed="solide / gemischt",
                 weak="schwach", lead="Bilanz"):
    """Rendert eine Verdict-Box aus [(name, bool), ...]."""
    if not checks:
        return
    passed = sum(1 for _, ok in checks if ok)
    r = passed / len(checks)
    lines = "  \n".join(
        f"{'✅' if ok else '❌'} {name}" for name, ok in checks)
    msg = f"**{passed}/{len(checks)} Kriterien erfuellt**  \n{lines}"
    if r >= 0.75:
        st.success(f"{lead} wirkt **{strong}**  \n" + msg)
    elif r >= 0.5:
        st.info(f"{lead} wirkt **{mixed}**  \n" + msg)
    else:
        st.warning(f"{lead} wirkt **{weak}**  \n" + msg)


# ---------- Daten-Load (cached, keine Persistierung) ----------

@st.cache_data(ttl=3600, show_spinner=False)
def _load_balance(ticker: str, n_years: int):
    """Juengstes Filing (Snapshot) + letzte N 10-K (Trend)."""
    latest_f = find_filings(ticker, n=1, forms=("10-Q", "10-K"))
    annuals = find_filings(ticker, n=n_years, forms=("10-K",))
    latest = (fetch_balance_sheet_from_filing(latest_f[0])
              if latest_f else None)
    hist = []
    for f in annuals:
        bs = fetch_balance_sheet_from_filing(f)
        if bs is not None:
            hist.append(bs)
    return latest, hist


@st.cache_data(ttl=3600, show_spinner=False)
def _load_returns(ticker: str, n_years: int):
    """Letzte N 10-K: je (GuV, Bilanz) aus einem XBRL-Call."""
    annuals = find_filings(ticker, n=n_years, forms=("10-K",))
    rows = []
    for f in annuals:
        inc, bs = fetch_statements_from_filing(f)
        if inc is not None and bs is not None:
            rows.append((inc, bs))
    rows.sort(key=lambda t: t[0].period_end or "")
    return rows


@st.cache_data(ttl=3600, show_spinner=False)
def _load_insider(ticker: str):
    """Flache Insider-Transaktionsliste (Form 3/4/5)."""
    return fetch_insider_transactions(ticker, n=300)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_gaap(ticker: str):
    """Juengstes Earnings-8-K Exhibit -> (meta, analyse, textlaenge)."""
    ex = find_earnings_exhibits(ticker, n=1)
    if not ex or not ex[0].get("exhibit_url"):
        return None, None
    text = fetch_exhibit_text(ex[0]["exhibit_url"])
    return ex[0], analyze_non_gaap(text)


@st.cache_data(ttl=3600, show_spinner=False)
def _load_sbc(ticker: str, n_years: int):
    """Letzte N 10-K: SBC + Kontext je Jahr (dicts)."""
    annuals = find_filings(ticker, n=n_years, forms=("10-K",))
    rows = []
    for f in annuals:
        d = fetch_sbc_from_filing(f)
        if d is not None:
            rows.append(d)
    rows.sort(key=lambda d: d.get("period_end") or "")
    return rows


# ---------- Kennzahlen ----------

def _returns(inc, bs) -> dict:
    """ROE/ROA/ROCE/ROIC fuer ein Geschaeftsjahr (Stichtagswerte)."""
    roe = _div(inc.net_income, bs.equity)
    roa = _div(inc.net_income, bs.total_assets)

    cap_emp = None
    if bs.total_assets is not None and bs.liabilities_current is not None:
        cap_emp = bs.total_assets - bs.liabilities_current
    roce = _div(inc.operating_income, cap_emp)

    eff = _div(inc.tax_expense, inc.pretax_income)
    if eff is None or not (0.0 <= eff <= 0.6):
        eff = 0.21                      # US-Default, wenn unplausibel
    nopat = (inc.operating_income * (1 - eff)
             if inc.operating_income is not None else None)
    inv_cap = None
    if bs.total_debt is not None and bs.equity is not None:
        inv_cap = bs.total_debt + bs.equity - (bs.cash_and_sti or 0.0)
        if inv_cap <= 0:
            inv_cap = None
    roic = _div(nopat, inv_cap)
    return {"roe": roe, "roa": roa, "roce": roce, "roic": roic,
            "nopat": nopat, "inv_cap": inv_cap, "eff_tax": eff}


# =====================================================================
# Render-Funktionen je Thema
# =====================================================================

def render_balance(ticker, n_years):
    try:
        with st.spinner(f"Lade SEC-Filings fuer {ticker} …"):
            latest, hist = _load_balance(ticker, n_years)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if latest is None:
        st.warning(f"Kein verwertbares 10-K/10-Q mit Bilanz fuer "
                   f"**{ticker}** gefunden.")
        st.stop()

    cur = latest.currency or "USD"
    st.markdown(
        f"### {ticker} — Bilanzstaerke  \n"
        f"Stichtag **{str(latest.period_end)[:10]}** "
        f"({latest.form_type}) · eingereicht {str(latest.filed_at)[:10]}")

    checks = []
    cr = _div(latest.assets_current, latest.liabilities_current)
    if cr is not None:
        checks.append(("Current Ratio > 1,5", cr > 1.5))
    if latest.net_debt is not None:
        checks.append(("Netto-Cash (Net Debt < 0)", latest.net_debt < 0))
    de = _div(latest.total_debt, latest.equity)
    if de is not None:
        checks.append(("Debt/Equity < 0,5", de < 0.5))
    eqr = _div(latest.equity, latest.total_assets)
    if eqr is not None:
        checks.append(("Eigenkapitalquote > 40 %", eqr > 0.40))
    _verdict_box(checks, lead="Bilanz")

    inv = latest.inventory or 0.0
    quick = _div((latest.assets_current or 0.0) - inv,
                 latest.liabilities_current)
    intang = (latest.goodwill or 0.0) + (latest.intangibles or 0.0)
    m = st.columns(3)
    m[0].metric("Current Ratio", _ratio(cr),
                help="Umlaufvermoegen / kurzfr. Verbindlichkeiten")
    m[1].metric("Quick Ratio", _ratio(quick),
                help="(Umlaufvermoegen − Vorraete) / kurzfr. Verbindl.")
    m[2].metric("Debt / Equity", _ratio(de),
                help="Gesamtverschuldung / Eigenkapital")
    m2 = st.columns(3)
    nd = latest.net_debt
    m2[0].metric("Net Debt" if (nd or 0) >= 0 else "Net Cash",
                 _money(abs(nd) if nd is not None else None, cur),
                 help="Gesamtverschuldung − (Cash + kurzfr. Anlagen)")
    m2[1].metric("Eigenkapitalquote", _pct(eqr),
                 help="Eigenkapital / Bilanzsumme")
    m2[2].metric("Goodwill + Intangibles",
                 _pct(_div(intang, latest.total_assets)),
                 help="Anteil immaterieller Posten an der Bilanzsumme")

    if len(hist) >= 2:
        rows = [{
            "period_end": pd.to_datetime(bs.period_end),
            "current_ratio": _div(bs.assets_current, bs.liabilities_current),
            "debt_to_equity": _div(bs.total_debt, bs.equity),
            "net_debt": bs.net_debt,
        } for bs in sorted(hist, key=lambda b: b.period_end or "")]
        df = pd.DataFrame(rows)
        st.markdown("#### Trend (10-K, jaehrlich)")
        t1, t2 = st.columns(2)
        f1 = go.Figure()
        f1.add_trace(go.Scatter(x=df["period_end"], y=df["current_ratio"],
                                mode="lines+markers", name="Current Ratio",
                                line=dict(color="#0F6E56", width=2)))
        f1.add_trace(go.Scatter(x=df["period_end"], y=df["debt_to_equity"],
                                mode="lines+markers", name="Debt/Equity",
                                line=dict(color="#A32D2D", width=2)))
        f1.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title="Current Ratio & Debt/Equity",
                         legend=dict(orientation="h", y=-0.2),
                         hovermode="x unified")
        t1.plotly_chart(f1, use_container_width=True)
        nd_col = ["#1D9E75" if (v is not None and v < 0) else "#A32D2D"
                  for v in df["net_debt"]]
        f2 = go.Figure(go.Bar(
            x=df["period_end"], y=df["net_debt"], marker_color=nd_col,
            hovertemplate="%{x|%Y-%m-%d}<br>Net Debt: "
                          "%{y:,.0f}<extra></extra>"))
        f2.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title=f"Net Debt ({cur}) — gruen = Netto-Cash",
                         yaxis_title=cur)
        t2.plotly_chart(f2, use_container_width=True)

    with st.expander("Bilanz-Rohwerte (zuletzt)"):
        raw = {
            "Cash & Equivalents": latest.cash,
            "Kurzfristige Anlagen": latest.short_term_invest,
            "Umlaufvermoegen": latest.assets_current,
            "Kurzfr. Verbindlichkeiten": latest.liabilities_current,
            "Bilanzsumme": latest.total_assets,
            "Gesamtverbindlichkeiten": latest.total_liabilities,
            "Eigenkapital": latest.equity,
            "Langfr. Schulden": latest.long_term_debt,
            "Kurzfr. Schulden": latest.current_debt,
            "Vorraete": latest.inventory,
            "Goodwill": latest.goodwill,
            "Immaterielle (o. Goodwill)": latest.intangibles,
        }
        st.dataframe(
            pd.DataFrame([{"Posten": k, "Wert": _money(v, cur)}
                          for k, v in raw.items()]),
            use_container_width=True, hide_index=True)


def render_returns(ticker, n_years):
    try:
        with st.spinner(f"Lade {n_years} Jahresberichte fuer {ticker} …"):
            rows = _load_returns(ticker, n_years)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if not rows:
        st.warning(f"Keine verwertbaren 10-K mit GuV + Bilanz fuer "
                   f"**{ticker}** gefunden.")
        st.stop()

    inc_l, bs_l = rows[-1]
    cur = inc_l.currency or bs_l.currency or "USD"
    rl = _returns(inc_l, bs_l)
    st.markdown(
        f"### {ticker} — Return on Capital  \n"
        f"Letztes GJ **{str(inc_l.period_end)[:10]}** "
        f"({inc_l.form_type}) · {len(rows)} Jahre geladen")

    checks = []
    if rl["roic"] is not None:
        checks.append(("ROIC > 15 %", rl["roic"] > 0.15))
    if rl["roe"] is not None:
        checks.append(("ROE > 15 %", rl["roe"] > 0.15))
    if rl["roa"] is not None:
        checks.append(("ROA > 6 %", rl["roa"] > 0.06))
    _all_roic = [_returns(i, b)["roic"] for i, b in rows]
    _all_roic = [x for x in _all_roic if x is not None]
    if len(_all_roic) >= 2:
        checks.append(("ROIC durchgehend positiv",
                       all(x > 0 for x in _all_roic)))
    _verdict_box(checks, strong="hochwertig", mixed="durchschnittlich",
                 weak="kapitalineffizient", lead="Kapitalrendite")

    m = st.columns(4)
    m[0].metric("ROIC", _pct(rl["roic"]),
                help="NOPAT / (Schulden + EK − Cash). "
                     "NOPAT = operatives Ergebnis × (1 − eff. Steuersatz)")
    m[1].metric("ROCE", _pct(rl["roce"]),
                help="Operatives Ergebnis / (Bilanzsumme − kurzfr. Verbindl.)")
    m[2].metric("ROE", _pct(rl["roe"]),
                help="Nettogewinn / Eigenkapital")
    m[3].metric("ROA", _pct(rl["roa"]),
                help="Nettogewinn / Bilanzsumme")

    if len(rows) >= 2:
        trend = [{
            "period_end": pd.to_datetime(i.period_end),
            **{k: _returns(i, b)[k] for k in ("roic", "roce", "roe", "roa")},
        } for i, b in rows]
        df = pd.DataFrame(trend)
        st.markdown("#### Trend (10-K, jaehrlich)")
        fig = go.Figure()
        for name, col, color in [
            ("ROIC", "roic", "#0F6E56"), ("ROCE", "roce", "#1D9E75"),
            ("ROE", "roe", "#5DCAA5"), ("ROA", "roa", "#B4862B"),
        ]:
            fig.add_trace(go.Scatter(
                x=df["period_end"], y=df[col] * 100.0,
                mode="lines+markers", name=name,
                line=dict(color=color, width=2), connectgaps=False,
                hovertemplate=(f"%{{x|%Y-%m-%d}}<br>{name}: "
                               "%{y:.1f}%<extra></extra>")))
        fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title="%", legend=dict(orientation="h",
                                                       y=-0.2),
                          hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Stichtags-Bilanzwerte (kein Mehrjahres-Durchschnitt); "
                   "ROIC mit effektivem Steuersatz, Fallback 21 % wenn "
                   "Vorsteuerergebnis fehlt/negativ.")

    with st.expander("Komponenten (letztes GJ)"):
        st.dataframe(pd.DataFrame([
            {"Posten": "NOPAT", "Wert": _money(rl["nopat"], cur)},
            {"Posten": "Investiertes Kapital", "Wert": _money(rl["inv_cap"],
                                                              cur)},
            {"Posten": "Eff. Steuersatz", "Wert": _pct(rl["eff_tax"])},
            {"Posten": "Operatives Ergebnis",
             "Wert": _money(inc_l.operating_income, cur)},
            {"Posten": "Nettogewinn", "Wert": _money(inc_l.net_income, cur)},
            {"Posten": "Eigenkapital", "Wert": _money(bs_l.equity, cur)},
            {"Posten": "Bilanzsumme", "Wert": _money(bs_l.total_assets, cur)},
        ]), use_container_width=True, hide_index=True)


def render_insider(ticker, n_years):
    try:
        with st.spinner(f"Lade Insider-Filings fuer {ticker} …"):
            tx = _load_insider(ticker)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if not tx:
        st.warning(f"Keine Insider-Filings (Form 3/4/5) fuer **{ticker}** "
                   "gefunden.")
        st.stop()

    cutoff = (date.today() - timedelta(days=int(n_years) * 365)).isoformat()
    df = pd.DataFrame(tx)
    df = df[df["transaction_date"] >= cutoff]
    st.markdown(
        f"### {ticker} — Insider Buy / Sell  \n"
        f"Lookback **{int(n_years)} J** · {len(df)} Transaktionen "
        f"(Markt-Trades P/S davon hervorgehoben)")
    if df.empty:
        st.info("Keine Transaktionen im gewaehlten Zeitraum.")
        st.stop()

    buys = df[df["code"] == "P"]
    sells = df[df["code"] == "S"]
    buy_val = float(buys["value"].fillna(0).sum())
    sell_val = float(sells["value"].fillna(0).sum())
    net_val = buy_val - sell_val
    n_buyers = buys["owner"].nunique()
    n_sellers = sells["owner"].nunique()

    checks = [
        ("Netto-Insiderkaeufe (Wert)", net_val > 0),
        ("Mehr Kaeufer als Verkaeufer", n_buyers > n_sellers),
        ("Cluster-Kauf (>= 3 Kaeufer)", n_buyers >= 3),
    ]
    _verdict_box(checks, strong="bullisch (Insider kaufen)",
                 mixed="neutral / gemischt",
                 weak="bearisch (Insider verkaufen)",
                 lead="Insider-Signal")

    m = st.columns(4)
    m[0].metric("Kaeufe (Markt, P)", _money(buy_val),
                help=f"{len(buys)} Transaktionen · {n_buyers} Personen")
    m[1].metric("Verkaeufe (Markt, S)", _money(sell_val),
                help=f"{len(sells)} Transaktionen · {n_sellers} Personen")
    m[2].metric("Netto", _money(net_val),
                delta=("Kaufüberhang" if net_val > 0 else "Verkaufüberhang"))
    m[3].metric("Kaeufer / Verkaeufer", f"{n_buyers} / {n_sellers}")

    # Monatlicher Netto-Wert (nur P/S)
    ps = df[df["code"].isin(["P", "S"])].copy()
    if not ps.empty:
        ps["month"] = pd.to_datetime(ps["transaction_date"]).dt.to_period(
            "M").dt.to_timestamp()
        ps["signed"] = ps.apply(
            lambda r: (r["value"] or 0) * (1 if r["code"] == "P" else -1),
            axis=1)
        monthly = ps.groupby("month")["signed"].sum().reset_index()
        st.markdown("#### Netto Insider-Flow je Monat (P − S)")
        colors = ["#1D9E75" if v >= 0 else "#A32D2D"
                  for v in monthly["signed"]]
        fig = go.Figure(go.Bar(
            x=monthly["month"], y=monthly["signed"], marker_color=colors,
            hovertemplate="%{x|%Y-%m}<br>Netto: %{y:,.0f}<extra></extra>"))
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title="USD", bargap=0.2)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Letzte Markt-Transaktionen (P/S)")
    show = (df[df["code"].isin(["P", "S"])]
            .sort_values("transaction_date", ascending=False).head(25))
    if show.empty:
        st.caption("Keine offenen Markt-Trades (P/S) im Zeitraum — nur "
                   "Awards/Ausuebungen/Steuereinbehalte.")
    else:
        tbl = pd.DataFrame({
            "Datum": show["transaction_date"],
            "Person": show["owner"],
            "Funktion": show["relationship"],
            "Art": show["code"].map(INSIDER_CODE_LABELS).fillna(show["code"]),
            "Stueck": show["shares"].map(
                lambda v: de_int(v) if not _missing(v) else "—"),
            "Preis": show["price"].map(
                lambda v: _money(v) if not _missing(v) else "—"),
            "Wert": show["value"].map(lambda v: _money(v)),
        })
        st.dataframe(tbl, use_container_width=True, hide_index=True)

    st.caption("Nur P (Markt-Kauf) und S (Markt-Verkauf) gelten als "
               "diskretionaeres Signal. Awards (A), Ausuebungen (M), "
               "Steuereinbehalte (F), Schenkungen (G) sind ausgeklammert.")


def render_sbc(ticker, n_years):
    try:
        with st.spinner(f"Lade {n_years} Jahresberichte fuer {ticker} …"):
            rows = _load_sbc(ticker, n_years)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if not rows:
        st.warning(f"Keine 10-K mit SBC-/Cashflow-Daten fuer **{ticker}** "
                   "gefunden.")
        st.stop()

    last = rows[-1]
    cur = "USD"
    st.markdown(
        f"### {ticker} — Stock-based Compensation  \n"
        f"Letztes GJ **{str(last['period_end'])[:10]}** "
        f"({last['form_type']}) · {len(rows)} Jahre geladen")

    if last.get("sbc") is None:
        st.info("Kein SBC-Tag (ShareBasedCompensation) im juengsten "
                "Cashflow-Statement gefunden. Manche Firmen weisen es nur "
                "im Anhang aus.")

    sbc = last.get("sbc")
    sbc_rev = _div(sbc, last.get("revenue"))
    sbc_cfo = _div(sbc, last.get("cfo"))
    sbc_ni = _div(sbc, last.get("net_income"))

    # Verwaesserung: CAGR der verwaesserten Aktien ueber den Zeitraum
    _sh = [(d["period_end"], d["diluted_shares"]) for d in rows
           if d.get("diluted_shares")]
    dil_cagr = None
    if len(_sh) >= 2:
        first_sh, last_sh = _sh[0][1], _sh[-1][1]
        try:
            yrs = (pd.to_datetime(_sh[-1][0]) - pd.to_datetime(
                _sh[0][0])).days / 365.25
            if yrs >= 1 and first_sh > 0:
                dil_cagr = (last_sh / first_sh) ** (1 / yrs) - 1
        except Exception:  # noqa: BLE001
            dil_cagr = None

    checks = []
    if sbc_rev is not None:
        checks.append(("SBC < 5 % vom Umsatz", sbc_rev < 0.05))
    if sbc_cfo is not None:
        checks.append(("SBC < 15 % vom operativen Cashflow", sbc_cfo < 0.15))
    if dil_cagr is not None:
        checks.append(("Aktienzahl ≤ +1 % p.a. (kaum Verwaesserung)",
                       dil_cagr <= 0.01))
    _verdict_box(checks, strong="gering verwaessernd (hohe Qualitaet)",
                 mixed="moderat", weak="stark verwaessernd",
                 lead="SBC-Belastung")

    m = st.columns(4)
    m[0].metric("SBC (letztes GJ)", _money(sbc, cur))
    m[1].metric("SBC / Umsatz", _pct(sbc_rev))
    m[2].metric("SBC / operativer CF", _pct(sbc_cfo))
    m[3].metric("Aktien p.a.", _pct(dil_cagr) if dil_cagr is not None
                else "—", help="CAGR der verwaesserten Aktien "
                                "(+ = Verwaesserung, − = Rueckkauf)")

    if len(rows) >= 2:
        df = pd.DataFrame([{
            "period_end": pd.to_datetime(d["period_end"]),
            "sbc_rev": _div(d.get("sbc"), d.get("revenue")),
            "sbc_cfo": _div(d.get("sbc"), d.get("cfo")),
            "diluted_shares": d.get("diluted_shares"),
        } for d in rows])
        st.markdown("#### Trend (10-K, jaehrlich)")
        t1, t2 = st.columns(2)
        f1 = go.Figure()
        f1.add_trace(go.Scatter(
            x=df["period_end"], y=df["sbc_rev"] * 100.0,
            mode="lines+markers", name="SBC / Umsatz",
            line=dict(color="#A32D2D", width=2), connectgaps=False))
        f1.add_trace(go.Scatter(
            x=df["period_end"], y=df["sbc_cfo"] * 100.0,
            mode="lines+markers", name="SBC / operativer CF",
            line=dict(color="#B4862B", width=2), connectgaps=False))
        f1.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title="SBC-Belastung", yaxis_title="%",
                         legend=dict(orientation="h", y=-0.25),
                         hovermode="x unified")
        t1.plotly_chart(f1, use_container_width=True)

        f2 = go.Figure(go.Scatter(
            x=df["period_end"], y=df["diluted_shares"],
            mode="lines+markers", name="Verwaesserte Aktien",
            line=dict(color="#0F6E56", width=2), connectgaps=False))
        f2.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                         title="Verwaesserte Aktien (Stk.)",
                         yaxis_title="Aktien")
        t2.plotly_chart(f2, use_container_width=True)

    with st.expander("SBC-Rohwerte je Jahr"):
        st.dataframe(pd.DataFrame([{
            "Jahr": str(d["period_end"])[:10],
            "SBC": _money(d.get("sbc"), cur),
            "Operativer CF": _money(d.get("cfo"), cur),
            "Umsatz": _money(d.get("revenue"), cur),
            "Nettogewinn": _money(d.get("net_income"), cur),
            "Verw. Aktien": (de_int(d["diluted_shares"])
                             if d.get("diluted_shares") else "—"),
        } for d in rows]), use_container_width=True, hide_index=True)

    st.caption("SBC aus dem Cashflow-Statement (ShareBasedCompensation, "
               "nicht-zahlungswirksamer Zuschlag). Verwaesserung als CAGR "
               "der gewichteten verwaesserten Aktien.")


def render_gaap(ticker, n_years):
    try:
        with st.spinner(f"Lade Earnings-8-K-Exhibit fuer {ticker} …"):
            meta, ana = _load_gaap(ticker)
    except SecApiError as e:
        st.error(f"sec-api.io: {e}"); st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Unerwarteter Fehler: {e.__class__.__name__}: {e}")
        st.stop()

    if meta is None or ana is None:
        st.warning(
            f"Kein Earnings-8-K (Item 2.02) mit Exhibit 99 fuer "
            f"**{ticker}** gefunden. Manche Firmen melden Zahlen anders.")
        st.stop()

    st.markdown(
        f"### {ticker} — GAAP vs non-GAAP  \n"
        f"Quelle: Earnings-8-K, eingereicht **{str(meta['filed_at'])[:10]}**")

    cats = ana["categories"]
    checks = [
        ("Non-GAAP-Nutzung moderat (< 15 Erwaehnungen)",
         ana["mentions"] < 15),
        ("SBC NICHT herausgerechnet", not ana["adds_back_sbc"]),
        ("≤ 3 Anpassungskategorien", len(cats) <= 3),
    ]
    _verdict_box(checks, strong="konservativ / transparent",
                 mixed="moderat", weak="aggressiv (viele Add-backs)",
                 lead="Reporting")

    m = st.columns(3)
    m[0].metric("Non-GAAP-Erwaehnungen", str(ana["mentions"]))
    m[1].metric("Anpassungs-Kategorien", str(len(cats)))
    m[2].metric("SBC herausgerechnet?",
                "Ja" if ana["adds_back_sbc"] else "Nein",
                help="Add-back von Aktienverguetung ist der klassische "
                     "Aggressivitaets-Marker (echte, wiederkehrende Kosten)")

    if cats:
        st.markdown("#### Gefundene Anpassungs-Kategorien")
        cat_df = (pd.DataFrame(
            [{"Kategorie": k, "Treffer": v} for k, v in cats.items()])
            .sort_values("Treffer", ascending=False))
        fig = go.Figure(go.Bar(
            x=cat_df["Treffer"], y=cat_df["Kategorie"], orientation="h",
            marker_color=["#A32D2D" if k.startswith("Aktienverguetung")
                          else "#1D9E75" for k in cat_df["Kategorie"]]))
        fig.update_layout(height=max(220, 40 * len(cat_df)),
                          margin=dict(l=10, r=10, t=10, b=10),
                          xaxis_title="Erwaehnungen")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Keine bekannten Anpassungs-Kategorien im Text erkannt — "
                "entweder rein GAAP berichtet oder ungewohnte Formulierung.")

    if ana["amounts"]:
        with st.expander("Beträge nahe 'non-GAAP' (heuristisch, ungeprueft)"):
            st.write(", ".join(ana["amounts"]))
            st.caption("Reine Textnaehe-Suche — keine Zuordnung zu GAAP/"
                       "non-GAAP-Zeilen. Nur als grobe Orientierung.")

    if meta.get("link"):
        st.caption(f"Original-Filing: {meta['link']}")
    st.caption("Heuristische Textanalyse des Earnings-Exhibits "
               "(Exhibit 99). Non-GAAP-Kennzahlen sind nicht im XBRL "
               "strukturiert — daher Stichwort-basiert. Add-back von SBC "
               "und vielen wiederkehrenden Posten gilt als Warnsignal fuer "
               "die Ergebnisqualitaet.")


# =====================================================================
# Seite
# =====================================================================

st.title("🧭 Ad-Hoc Analysis")
st.caption(
    "Qualitaetspruefung beliebiger Aktien nach Shearn, *The Investment "
    "Checklist*. Daten on-Demand von sec-api.io — keine Speicherung.")

_TOPICS = {
    "Balance Sheet — Bilanzstaerke": render_balance,
    "Return on Capital — ROIC / ROCE / ROE / ROA": render_returns,
    "Insider Sales / Buys — Form 3/4/5": render_insider,
    "Stock-based Compensation — SBC & Verwaesserung": render_sbc,
    "GAAP vs non-GAAP — Earnings-Exhibit": render_gaap,
}
_topic = st.selectbox("Thema", list(_TOPICS.keys()))

_yr_label = ("Lookback (Jahre)" if _topic.startswith("Insider")
             else "Jahre (10-K)")
_c1, _c2 = st.columns([3, 1])
_ticker = _c1.text_input(
    "Ticker (US-gelistet, EDGAR)", value="",
    placeholder="z. B. AAPL, MSFT, NVDA").strip().upper()
_n_years = _c2.number_input(_yr_label, min_value=1, max_value=10,
                            value=3 if _topic.startswith("Insider") else 5,
                            step=1)
st.button("Analysieren", type="primary", disabled=not _ticker)

_render = _TOPICS[_topic]
if _render is None:
    st.info("Dieses Thema ist noch in Arbeit.")
    st.stop()
if not _ticker:
    st.stop()

_render(_ticker, int(_n_years))

st.caption(
    "Quelle: sec-api.io (XBRL-to-JSON). On-Demand geladen, nicht "
    "persistiert. Kennzahlen aus konsolidierten US-GAAP-Posten; "
    "fehlende XBRL-Tags fuehren zu '—'.")
