"""Superinvestoren — getrackte 13F-Portfolios + Quartalsveraenderungen.

Konsumiert: ref_superinvestor_holdings / ref_superinvestor_changes
(Ingest: python -m modules.superinvestors ingest).

WICHTIG (im UI sichtbar gehalten): 13F zeigt nur US-Long-Aktien + gelistete
Optionen — keine Shorts, kein Cash, kein Non-US, keine Netto-Exposure.
Put-Positionen sind baerisch. 45-Tage-Meldefrist -> Ideen-Quelle, kein Signal.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.dashboard.db import run_query, table_exists


st.title("🐳 Superinvestoren (13F)")
st.caption("Getrackte 13F-Filer: Portfolio + Quartalsveraenderungen. "
           "Achtung: 13F = nur US-Long-Aktien + gelistete Optionen, ohne "
           "Shorts/Cash/Non-US, 45 Tage verzoegert. Puts = baerisch. "
           "Ideen-Quelle, kein Anlageurteil.")

if not table_exists("ref_superinvestor_holdings"):
    st.warning("Tabelle `ref_superinvestor_holdings` fehlt.")
    st.info("Ingest: `python -m modules.superinvestors ingest`")
    st.stop()


def _usd(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:.2f} Mrd"
    if a >= 1e6:
        return f"${v / 1e6:.1f} Mio"
    return f"${v:,.0f}"


_PC = {"": "📈 Aktie", "Put": "🔻 Put", "Call": "🔺 Call"}

# Universums-Symbole (fuer Abgleich)
_uni = run_query("SELECT DISTINCT upper(symbol) AS s FROM ref_instruments "
                 "WHERE symbol IS NOT NULL", None)
UNIVERSE = set(_uni["s"].tolist()) if _uni is not None and not _uni.empty \
    else set()


# ---------- Manager-Wahl ----------

mgrs = run_query("SELECT DISTINCT manager_name FROM "
                 "ref_superinvestor_holdings ORDER BY manager_name", None)
if mgrs is None or mgrs.empty:
    st.info("Noch keine Daten — Ingest laufen lassen.")
    st.stop()

mgr = st.selectbox("Investor", mgrs["manager_name"].tolist())
per = run_query("SELECT period, MAX(filed_at) AS f FROM "
                "ref_superinvestor_holdings WHERE manager_name=? "
                "GROUP BY period ORDER BY period DESC LIMIT 1", (mgr,))
period = per["period"].iloc[0] if per is not None and not per.empty else None
filed = per["f"].iloc[0] if per is not None and not per.empty else None
st.caption(f"Periode {period} · gemeldet {str(filed)[:10]}")


# ---------- Portfolio ----------

hold = run_query(
    "SELECT ticker, name, value, shares, put_call FROM "
    "ref_superinvestor_holdings WHERE manager_name=? AND period=? "
    "ORDER BY value DESC NULLS LAST", (mgr, period))

if hold is not None and not hold.empty:
    long_v = hold[hold["put_call"] == ""]["value"].sum()
    put_v = hold[hold["put_call"] == "Put"]["value"].sum()
    call_v = hold[hold["put_call"] == "Call"]["value"].sum()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Positionen", f"{len(hold)}")
    k2.metric("Long-Aktien", _usd(long_v))
    k3.metric("Puts (notional)", _usd(put_v))
    k4.metric("Calls (notional)", _usd(call_v))
    if put_v and long_v and put_v > 0.5 * long_v:
        st.warning("Erhebliche PUT-Exposure — dieser Filer ist auf Teile des "
                   "Marktes baerisch positioniert. 13F-Longs nicht naiv als "
                   "Kaufideen lesen.")

    disp = hold.copy()
    disp["Typ"] = disp["put_call"].map(lambda x: _PC.get(x, x))
    disp["im Universum"] = disp["ticker"].map(
        lambda t: "✓" if (t or "").upper() in UNIVERSE else "")
    disp["Wert"] = disp["value"].map(_usd)
    st.dataframe(
        disp[["ticker", "name", "Typ", "Wert", "shares", "im Universum"]],
        use_container_width=True, hide_index=True,
        column_config={
            "ticker": st.column_config.TextColumn("Ticker", width="small"),
            "name": st.column_config.TextColumn("Name"),
            "shares": st.column_config.NumberColumn("Stück", format="%.0f"),
        })

st.divider()


# ---------- Veraenderungen QoQ ----------

st.subheader("Veraenderungen ggue. Vorquartal")
chg = run_query(
    "SELECT change_type, ticker, name, put_call, value_new, value_old "
    "FROM ref_superinvestor_changes WHERE manager_name=? AND period=?",
    (mgr, period))
if chg is None or chg.empty:
    st.caption("Keine Veraenderungsdaten (evtl. nur ein Quartal vorhanden).")
else:
    _ICON = {"NEW": "🟢 Neu", "ADD": "⬆ Aufgestockt",
             "TRIM": "⬇ Reduziert", "EXIT": "🔴 Verkauft"}
    cols = st.columns(4)
    for i, ct in enumerate(["NEW", "ADD", "TRIM", "EXIT"]):
        sub = chg[chg["change_type"] == ct].copy()
        with cols[i]:
            st.markdown(f"**{_ICON[ct]}** ({len(sub)})")
            sub = sub.sort_values("value_new" if ct != "EXIT" else "value_old",
                                  ascending=False, na_position="last")
            for _, r in sub.head(12).iterrows():
                pc = f" {r['put_call']}" if r["put_call"] else ""
                tk = r["ticker"] or (r["name"] or "—")[:14]
                st.markdown(f"`{tk}`{pc}")


st.divider()

# ---------- Global: New Buys quer ueber alle Manager ----------

st.subheader("Neue Käufe — alle getrackten Investoren (neuestes Quartal)")
nb = run_query(
    "SELECT manager_name, ticker, name, put_call, value_new "
    "FROM ref_superinvestor_changes WHERE change_type='NEW' "
    "AND period = (SELECT MAX(period) FROM ref_superinvestor_changes) "
    "ORDER BY value_new DESC NULLS LAST LIMIT 30", None)
if nb is None or nb.empty:
    st.caption("Keine neuen Käufe im jüngsten Quartal.")
else:
    nb = nb.copy()
    nb["Typ"] = nb["put_call"].map(lambda x: _PC.get(x, x))
    nb["Wert"] = nb["value_new"].map(_usd)
    nb["im Universum"] = nb["ticker"].map(
        lambda t: "✓" if (t or "").upper() in UNIVERSE else "")
    st.dataframe(nb[["manager_name", "ticker", "name", "Typ", "Wert",
                     "im Universum"]],
                 use_container_width=True, hide_index=True,
                 column_config={
                     "manager_name": st.column_config.TextColumn("Investor"),
                     "ticker": st.column_config.TextColumn("Ticker",
                                                           width="small"),
                     "name": st.column_config.TextColumn("Name")})
