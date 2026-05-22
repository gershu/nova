"""Portfolio Overview (EUR-Aggregat + Native pro Position).

Konsumiert: v_mkt_holdings (liefert mtm_native, mtm_eur, pnl_eur,
cost_total_eur, fx_rate_eur).

EUR ist Stammwaehrung. Cross-Currency-Summen aggregieren in EUR.
Native-Werte bleiben in der Positions-Tabelle sichtbar.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from modules.dashboard.components.format import de_dec, de_int
from modules.dashboard.components.kpi import fmt_money, fmt_pct, kpi_row
from modules.dashboard.db import run_query, table_exists


st.title("📈 Portfolio Overview")


# ---------- v_mkt_holdings laden ----------

try:
    mkt = run_query("SELECT * FROM v_mkt_holdings")
except Exception as e:  # noqa: BLE001
    st.error(f"View 'v_mkt_holdings' nicht verfuegbar: {e.__class__.__name__}")
    st.info("Initialisieren: `python -m modules.portfolio_core init`")
    st.stop()

if mkt.empty:
    st.warning("Portfolio leer.")
    st.stop()


# ---------- Aggregation pro Position ----------

agg = (
    mkt.groupby(
        ["ref_instrument_id", "symbol", "name", "asset_type", "currency", "broker"],
        dropna=False, as_index=False,
    )
    .agg(
        quantity        = ("quantity",          "sum"),
        cost_native     = ("cost_total_native", "sum"),
        mtm_native      = ("mtm_native",        "sum"),
        pnl_native      = ("pnl_native",        "sum"),
        cost_eur        = ("cost_total_eur",    "sum"),
        mtm_eur         = ("mtm_eur",           "sum"),
        pnl_eur         = ("pnl_eur",           "sum"),
        fx_rate_eur     = ("fx_rate_eur",       "last"),
        px_close        = ("px_close",          "last"),
        quote_ts        = ("quote_ts",          "max"),
        quote_source    = ("quote_source",      "last"),
        valid_from      = ("valid_from",        "min"),
    )
)
agg["avg_cost"]   = agg["cost_native"] / agg["quantity"].where(agg["quantity"] != 0)
agg["result_pct"] = agg["pnl_eur"]     / agg["cost_eur"].where(agg["cost_eur"] != 0) * 100.0


# ---------- KPI-Block: Portfolio Total in EUR ----------

st.subheader("Portfolio Total (EUR)")
total_mv_eur   = float(agg["mtm_eur"].sum(skipna=True))
total_cost_eur = float(agg["cost_eur"].sum(skipna=True))
total_pnl_eur  = float(agg["pnl_eur"].sum(skipna=True))
kpi_row([
    {"label": "Market Value (EUR)", "value": fmt_money(total_mv_eur, places=0)},
    {"label": "Cost Basis (EUR)",   "value": fmt_money(total_cost_eur, places=0)},
    {"label": "Unrealized PnL",
     "value": fmt_money(total_pnl_eur, places=0),
     "delta": fmt_pct(total_pnl_eur / total_cost_eur if total_cost_eur else None)},
    {"label": "Positions",          "value": f"{len(agg)}",
     "help": f"Pro (Instrument, Broker)."},
])


# ---------- Pro-Currency-Bucket (Native-Detail) ----------

ccy_groups = sorted(agg["currency"].dropna().unique().tolist())
with st.expander(f"Pro-Currency-Detail ({len(ccy_groups)} Buckets, Native + EUR)",
                  expanded=False):
    cc_df = (
        agg.groupby("currency", as_index=False)
           .agg(n_positions   = ("symbol",      "size"),
                mv_native     = ("mtm_native",  "sum"),
                mv_eur        = ("mtm_eur",     "sum"),
                pnl_native    = ("pnl_native",  "sum"),
                pnl_eur       = ("pnl_eur",     "sum"),
                fx_rate_eur   = ("fx_rate_eur", "first"))
           .sort_values("mv_eur", ascending=False)
    )
    st.dataframe(
        cc_df.style.format({
            "fx_rate_eur": lambda v: de_dec(v, 4),
            "mv_native": de_int, "mv_eur": de_int,
            "pnl_native": de_int, "pnl_eur": de_int,
        }),
        use_container_width=True, hide_index=True,
        column_config={
            "currency":     st.column_config.TextColumn("CCY", width="small"),
            "n_positions":  st.column_config.NumberColumn("# Pos", format="%d"),
            "fx_rate_eur":  "FX→EUR",
            "mv_native":    "MV (native)",
            "mv_eur":       "MV (EUR)",
            "pnl_native":   "PnL (native)",
            "pnl_eur":      "PnL (EUR)",
        },
    )

st.divider()


# ---------- Stale Quotes + No-Quote ----------

stale = agg[agg["quote_ts"].notna()].copy()
stale["days_stale"] = (pd.Timestamp.utcnow().normalize().tz_localize(None)
                       - pd.to_datetime(stale["quote_ts"])).dt.days
stale = stale[stale["days_stale"] > 5].sort_values("days_stale", ascending=False)
if not stale.empty:
    with st.expander(f"⚠ Stale Quotes ({len(stale)})", expanded=False):
        st.dataframe(
            stale[["symbol", "currency", "quote_ts", "days_stale", "quote_source"]],
            use_container_width=True, hide_index=True,
        )

no_quote = agg[agg["px_close"].isna()]
if not no_quote.empty:
    with st.expander(f"⚠ Keine Quote ({len(no_quote)})", expanded=False):
        st.dataframe(
            no_quote[["symbol", "currency", "quantity", "avg_cost"]]
                .style.format({"quantity": de_int, "avg_cost": de_dec}),
            use_container_width=True, hide_index=True,
        )


# ---------- MTM-Trend in EUR (90d) ----------

st.subheader("MTM-Trend (90d, EUR)")
LOOKBACK_DAYS = 90
since = date.today() - timedelta(days=LOOKBACK_DAYS)

# Pro Instrument: Quantity (konstant) + Currency
hq = (mkt.groupby(["ref_instrument_id", "currency"], as_index=False)
          .agg(quantity=("quantity", "sum")))

quote_hist = run_query("""
    WITH ranked AS (
        SELECT ref_instrument_id, ts, close, source,
               ROW_NUMBER() OVER (
                   PARTITION BY ref_instrument_id, ts
                   ORDER BY CASE source WHEN 'ib' THEN 1 WHEN 'yfinance' THEN 2 ELSE 9 END
               ) AS rk
        FROM mkt_quotes_daily
        WHERE ts >= ?
    )
    SELECT ref_instrument_id, ts, close FROM ranked WHERE rk = 1
""", (since,))

# FX-Historie pro currency_from -> EUR
fx_hist = run_query("""
    WITH ranked AS (
        SELECT currency_from, ts, rate, source,
               ROW_NUMBER() OVER (
                   PARTITION BY currency_from, ts
                   ORDER BY CASE source WHEN 'ib' THEN 1 WHEN 'yfinance' THEN 2
                                        WHEN 'ecb' THEN 3 ELSE 9 END
               ) AS rk
        FROM mkt_fx_daily
        WHERE ts >= ? AND currency_to = 'EUR'
    )
    SELECT currency_from, ts, rate FROM ranked WHERE rk = 1
""", (since,))

if quote_hist.empty:
    st.info("Keine Quote-History in den letzten 90 Tagen.")
else:
    m = quote_hist.merge(hq, on="ref_instrument_id", how="inner")
    m["ts"]       = pd.to_datetime(m["ts"])
    fx_hist["ts"] = pd.to_datetime(fx_hist["ts"])
    fx_hist = fx_hist.rename(columns={"currency_from": "currency", "rate": "fx_rate"})
    m = m.merge(fx_hist, on=["currency", "ts"], how="left")
    # EUR-Native: fx_rate = 1.0
    m.loc[m["currency"] == "EUR", "fx_rate"] = 1.0
    # FX forward+backward fill innerhalb der Currency-Gruppe
    m["fx_rate"] = m.groupby("currency")["fx_rate"].ffill().bfill().fillna(1.0)
    m["mv_eur"]  = m["quantity"] * m["close"] * m["fx_rate"]

    daily = (m.groupby("ts")["mv_eur"].sum().sort_index()
              .asfreq("D").ffill().dropna())

    if len(daily) >= 2:
        peak   = daily.cummax()
        dd_pct = (daily - peak) / peak * 100.0
        mtm_today, mtm_prev = float(daily.iloc[-1]), float(daily.iloc[-2])
        delta_pct = (mtm_today / mtm_prev) - 1.0 if mtm_prev else None

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                              row_heights=[0.7, 0.3], vertical_spacing=0.04,
                              subplot_titles=("Portfolio MTM (EUR)", "Drawdown (%)"))
        fig.add_trace(go.Scatter(x=daily.index, y=daily.values, mode="lines",
                                   line=dict(color="#1f4e79", width=2),
                                   fill="tozeroy", fillcolor="rgba(31,78,121,0.08)",
                                   name="MV"), row=1, col=1)
        fig.add_trace(go.Scatter(x=dd_pct.index, y=dd_pct.values, mode="lines",
                                   line=dict(color="#c62828", width=1),
                                   fill="tozeroy", fillcolor="rgba(198,40,40,0.20)",
                                   name="Drawdown"), row=2, col=1)
        fig.update_layout(height=480, showlegend=False,
                            margin=dict(l=20, r=20, t=40, b=20))
        fig.update_yaxes(title_text="MV (EUR)", row=1, col=1)
        fig.update_yaxes(title_text="DD (%)",   row=2, col=1)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            f"Heute: {fmt_money(mtm_today, places=0)} EUR · "
            f"Δ Vortag: {fmt_pct(delta_pct) if delta_pct is not None else '—'} · "
            f"Max-Drawdown: {dd_pct.min():.2f} %"
        )
    else:
        st.info("Zu wenig Quote-Days (<2).")

st.divider()


# ---------- Positions-Tabelle (Native + EUR) ----------

st.subheader("Positions")

broker_groups = sorted(agg["broker"].dropna().unique().tolist())

f1, f2, f3 = st.columns([2, 2, 3])
with f1:
    ccy_choice = st.radio("Currency", ["Alle", *ccy_groups], horizontal=True)
with f2:
    broker_choice = st.radio("Broker", ["Alle", *broker_groups], horizontal=True)
with f3:
    search = st.text_input("Filter (Symbol / Name)", "", placeholder="z.B. AAPL")

display = agg.copy()
if ccy_choice != "Alle":
    display = display[display["currency"] == ccy_choice]
if broker_choice != "Alle":
    display = display[display["broker"] == broker_choice]
if search:
    mask = display.astype(str).apply(
        lambda r: r.str.contains(search, case=False, na=False)).any(axis=1)
    display = display[mask]
display = display.sort_values("mtm_eur", ascending=False)

# Summenzeile fuer die gefilterte Tabelle (EUR-Aggregat)
flt_mv   = float(display["mtm_eur"].sum(skipna=True))
flt_cost = float(display["cost_eur"].sum(skipna=True))
flt_pnl  = float(display["pnl_eur"].sum(skipna=True))
flt_pct  = (flt_pnl / flt_cost * 100.0) if flt_cost else None

s1, s2, s3, s4 = st.columns(4)
s1.metric("# Positions (gefiltert)", f"{len(display)}",
          help=f"von {len(agg)} insgesamt")
s2.metric("MV (EUR, gefiltert)",  fmt_money(flt_mv,   places=0))
s3.metric("Cost (EUR, gefiltert)", fmt_money(flt_cost, places=0))
s4.metric("Δ (EUR, gefiltert)",
          fmt_money(flt_pnl, places=0),
          delta=f"{flt_pct:+.2f}%" if flt_pct is not None else None)

_pos_event = st.dataframe(
    display.style.format({
        "quantity":    de_int,
        "avg_cost":    de_dec,
        "px_close":    de_dec,
        "fx_rate_eur": lambda v: de_dec(v, 4),
        "cost_native": de_int, "mtm_native": de_int, "pnl_native": de_int,
        "cost_eur":    de_int, "mtm_eur":    de_int, "pnl_eur":    de_int,
    }),
    use_container_width=True, height=520,
    on_select="rerun", selection_mode="single-row", key="positions_table",
    column_config={
        "ref_instrument_id": None,
        "asset_type":  st.column_config.TextColumn("type", width="small"),
        "currency":    st.column_config.TextColumn("ccy",  width="small"),
        "broker":      st.column_config.TextColumn("broker", width="small"),
        "quantity":    "Menge",
        "avg_cost":    "avg cost",
        "px_close":    "spot",
        "quote_ts":    st.column_config.DateColumn("spot ts"),
        "quote_source": st.column_config.TextColumn("src", width="small"),
        "fx_rate_eur": "FX→EUR",
        "cost_native": "cost (native)",
        "mtm_native":  "MV (native)",
        "pnl_native":  "Δ (native)",
        "cost_eur":    "cost (EUR)",
        "mtm_eur":     "MV (EUR)",
        "pnl_eur":     "Δ (EUR)",
        "result_pct":  st.column_config.NumberColumn("Δ %",           format="%.2f%%"),
        "valid_from":  st.column_config.DateColumn("seit",            format="YYYY-MM-DD"),
    },
)


# ---------- Positions-Detail (Zeilen-Auswahl) ----------

_sel = _pos_event.selection["rows"]
if not _sel:
    st.caption("↑ Zeile anklicken fuer Detail — Chart · News · Alerts · Signals.")
else:
    _row    = display.iloc[_sel[0]]
    _ref_id = _row["ref_instrument_id"]
    _sym    = _row["symbol"] or _ref_id
    _name   = _row["name"] or ""

    st.divider()
    st.subheader(f"🔎 {_sym} — {_name}")

    t_chart, t_news, t_alerts, t_signals = st.tabs(
        ["Chart", "News", "Alerts", "Signals"])

    # --- Chart: Schlusskurs-Linie ---
    with t_chart:
        _tf = st.radio("Zeitfenster", ["90 Tage", "1 Jahr", "Max"],
                       horizontal=True, key="pos_detail_tf")
        _days  = {"90 Tage": 90, "1 Jahr": 365, "Max": 100_000}[_tf]
        _since = (date.today() - timedelta(days=_days)).isoformat()
        _px = run_query("""
            WITH ranked AS (
                SELECT ts, close, source,
                       ROW_NUMBER() OVER (PARTITION BY ts ORDER BY
                           CASE source WHEN 'ib' THEN 1 WHEN 'yfinance' THEN 2
                                       ELSE 9 END) AS rk
                FROM mkt_quotes_daily
                WHERE ref_instrument_id = ? AND ts >= ?
            )
            SELECT ts, close FROM ranked WHERE rk = 1 ORDER BY ts
        """, (_ref_id, _since))
        if _px.empty:
            st.info("Keine Kursdaten fuer dieses Instrument.")
        else:
            _px["ts"] = pd.to_datetime(_px["ts"])
            st.line_chart(_px.set_index("ts")["close"], height=320)
            st.caption(f"{len(_px)} Handelstage · Schlusskurs in "
                       f"{_row['currency'] or '—'}.")

    # --- News ---
    with t_news:
        if not table_exists("ref_sa_articles"):
            st.info("Keine News-Tabellen vorhanden.")
        else:
            _news = run_query("""
                SELECT a.ts, a.title, a.summary, a.url, a.source
                FROM ref_sa_article_symbols s
                JOIN ref_sa_articles a USING (article_id)
                WHERE s.ref_instrument_id = ?
                ORDER BY a.ts DESC LIMIT 20
            """, (_ref_id,))
            if _news.empty:
                st.info("Keine News zu diesem Instrument.")
            else:
                st.caption(f"{len(_news)} aktuellste Artikel.")
                for _, _a in _news.iterrows():
                    _title = _a["title"] or "(ohne Titel)"
                    if _a["url"]:
                        st.markdown(f"**[{_title}]({_a['url']})**")
                    else:
                        st.markdown(f"**{_title}**")
                    st.caption(f"{_a['ts']}  ·  {_a['source'] or ''}")
                    if _a["summary"]:
                        st.write(_a["summary"])
                    st.divider()

    # --- Alerts ---
    with t_alerts:
        if not table_exists("sig_alerts"):
            st.info("Keine Alert-Tabelle vorhanden.")
        else:
            _al = run_query("""
                SELECT ts, rule_name, direction, trigger_value, threshold
                FROM sig_alerts WHERE ref_instrument_id = ?
                ORDER BY ts DESC LIMIT 50
            """, (_ref_id,))
            if _al.empty:
                st.info("Keine Alerts zu diesem Instrument.")
            else:
                st.dataframe(
                    _al.style.format({"trigger_value": de_dec,
                                      "threshold": de_dec}),
                    use_container_width=True, hide_index=True,
                    column_config={
                        "ts":            st.column_config.DateColumn("Datum"),
                        "rule_name":     st.column_config.TextColumn("Regel"),
                        "direction":     st.column_config.TextColumn("Richtung", width="small"),
                        "trigger_value": "Trigger",
                        "threshold":     "Schwelle",
                    },
                )

    # --- Signals: Recommendations fuer dieses Symbol ---
    with t_signals:
        if not table_exists("sig_recommendations"):
            st.info("Keine Recommendation-Tabelle vorhanden.")
        else:
            _sig = run_query("""
                SELECT ts, action, priority, category, title, rationale
                FROM sig_recommendations WHERE ref_instrument_id = ?
                ORDER BY ts DESC
            """, (_ref_id,))
            if _sig.empty:
                st.info("Keine Empfehlungen zu diesem Instrument.")
            else:
                _pic = {"high": "🔴", "medium": "🟠", "low": "🟢"}
                for _, _r in _sig.iterrows():
                    st.markdown(
                        f"{_pic.get(_r['priority'], '·')} `{_r['action']}`  ·  "
                        f"_{_r['category'] or ''}_  ·  {_r['ts']}  \n"
                        f"**{_r['title'] or ''}**  \n"
                        f"{_r['rationale'] or ''}")
                    st.divider()
