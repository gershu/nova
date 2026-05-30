"""Daemon-Health-Übersicht.

Liest config/daemons.yaml und prüft pro Daemon den aktuellen Zustand —
identisch zur CLI `python -m modules.health status`. Gruppiert dargestellt
mit Status-Chips; Klick auf eine Zeile öffnet ein Detail-Panel mit den
letzten 5 Audit-Runs + Log-Tail.

Read-only. Schreibt nichts — der historische Snapshot wird vom
de.gershu.nova.lab.health-Daemon (täglich) angelegt.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pandas as pd
import streamlit as st

from modules.dashboard.db import get_connection
from modules.health.reader import check_all, load_manifest


st.title("🩺 Daemon-Health")
st.caption("Wer hat geliefert, wer hängt — und seit wann?")


# ---------- Status sammeln ----------

@st.cache_data(ttl=30, show_spinner=False)
def _gather() -> tuple[list[dict], dict]:
    """Statuses + Summary als JSON-serialisierbare Dicts, fuer Cache."""
    con = get_connection()
    try:
        statuses = check_all(con)
    finally:
        con.close()
    rows = [{
        "label":           s.label,
        "title":           s.title,
        "group":           s.group,
        "schedule":        s.schedule,
        "overall":         s.overall,
        "last_run_ts":     s.last_run_ts,
        "last_run_status": s.last_run_status,
        "age_hours":       s.age_hours,
        "metric":          s.metric,
        "detail":          s.detail,
        "process_running": s.process_running,
        "port_open":       s.port_open,
    } for s in statuses]
    summary = {"total": len(statuses)}
    for r in rows:
        summary[r["overall"]] = summary.get(r["overall"], 0) + 1
    return rows, summary


rows, sm = _gather()
manifest = load_manifest()


# ---------- KPI-Header ----------

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Daemons gesamt", sm.get("total", 0))
k2.metric("✓ frisch / up",
          sm.get("fresh", 0) + sm.get("up", 0))
k3.metric("⚠ stale",  sm.get("stale", 0))
k4.metric("✗ failed/down",
          sm.get("failed", 0) + sm.get("down", 0))
k5.metric("? unknown", sm.get("unknown", 0))


# ---------- Hilfs-Formatter ----------

_GLYPH = {"fresh": "✓ frisch", "up": "✓ up",
          "stale": "⚠ stale", "failed": "✗ failed",
          "down": "✗ down", "unknown": "? unknown"}


def _fmt_age(h):
    if h is None or pd.isna(h):
        return "—"
    if h < 24:    return f"{int(h)}h"
    if h < 14*24: return f"{int(h/24)}d"
    return f"{int(h/24/7)}w"


def _fmt_ts(t):
    if t is None or pd.isna(t):
        return "—"
    return str(t)[:16]


_ORDER = {"failed": 0, "down": 1, "stale": 2, "unknown": 3,
          "fresh": 4, "up": 5}


# ---------- Tabellen pro Gruppe ----------

st.divider()

# Gruppen-Titel aus Manifest, sonst kapitalisiert
group_titles = {g["id"]: g.get("title", g["id"])
                for g in manifest.get("groups", [])}

df_all = pd.DataFrame(rows)
df_all["sort_key"] = df_all["overall"].map(lambda o: _ORDER.get(o, 99))
df_all = df_all.sort_values(["sort_key", "group", "title"])

for grp in df_all["group"].drop_duplicates().tolist():
    grp_df = df_all[df_all["group"] == grp].copy()
    st.subheader(group_titles.get(grp, grp.replace("_", " ").title()))

    display = grp_df[["title", "schedule", "overall",
                       "last_run_ts", "age_hours", "metric"]].copy()
    display["overall"]    = display["overall"].map(_GLYPH)
    display["last_run_ts"] = display["last_run_ts"].map(_fmt_ts)
    display["age_hours"]   = display["age_hours"].map(_fmt_age)
    display.rename(columns={
        "title":       "Daemon",
        "schedule":    "Schedule",
        "overall":     "Status",
        "last_run_ts": "Letzter Lauf",
        "age_hours":   "Alter",
        "metric":      "Metric / Hinweis",
    }, inplace=True)

    # Selection für Detail
    _evt = st.dataframe(
        display, use_container_width=True, hide_index=True,
        height=min(420, 48 + 36 * len(display)),
        on_select="rerun", selection_mode="single-row",
        key=f"health_tbl_{grp}",
        column_config={
            "Status":           st.column_config.TextColumn("Status",
                                                              width="small"),
            "Schedule":         st.column_config.TextColumn("Schedule",
                                                              width="medium"),
            "Alter":            st.column_config.TextColumn("Alter",
                                                              width="small"),
        })

    _sel = _evt.selection["rows"]
    if _sel:
        _row = grp_df.iloc[_sel[0]]
        label = _row["label"]
        with st.expander(f"🔎 Detail — {_row['title']} ({label})",
                          expanded=True):
            target = next((d for d in manifest.get("daemons", [])
                            if d["label"] == label), None)
            if target and target.get("audit_table"):
                st.markdown(f"**Letzte 5 Runs — {target['audit_table']}**")
                try:
                    con = get_connection()
                    try:
                        ts_col = "ts" if con.execute(
                            "SELECT 1 FROM information_schema.columns "
                            "WHERE table_name=? AND column_name='ts'",
                            [target["audit_table"]]).fetchone() \
                            else "finished_at"
                        audit_df = con.execute(
                            f"SELECT * FROM {target['audit_table']} "
                            f"ORDER BY {ts_col} DESC LIMIT 5").df()
                    finally:
                        con.close()
                    if not audit_df.empty:
                        st.dataframe(audit_df, use_container_width=True,
                                      hide_index=True)
                except Exception as e:  # noqa: BLE001
                    st.caption(f"Audit-Query fail: {e.__class__.__name__}: {e}")

            log_path = (target.get("log_path") if target else None) \
                or f"/Users/novaadm/Library/Logs/nova-{label.replace('.','-')}.log"
            p = pathlib.Path(log_path)
            if p.is_file():
                st.markdown(f"**Log-Tail** (`{log_path}`)")
                try:
                    r = subprocess.run(["tail", "-n", "30", log_path],
                                        capture_output=True, text=True,
                                        timeout=3)
                    st.code(r.stdout or "(leer)", language="text")
                except subprocess.SubprocessError:
                    st.caption("(tail failed)")
            else:
                st.caption(f"Kein Log unter {log_path}.")

st.divider()

# ---------- Historischer Trend ----------

st.subheader("Historie")
con = get_connection()
try:
    has_snap = con.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name='sig_health_snapshots'").fetchone()
    if not has_snap:
        st.caption("Noch keine Snapshots — der Daily-Daemon "
                   "`de.gershu.nova.lab.health` legt sie an.")
    else:
        snaps = con.execute("""
            SELECT ts, total_daemons, fresh_count, stale_count,
                   failed_count, down_count, unknown_count
            FROM sig_health_snapshots ORDER BY ts DESC LIMIT 30
        """).df()
        if snaps.empty:
            st.caption("Noch keine Snapshots gespeichert.")
        else:
            st.dataframe(snaps, use_container_width=True, hide_index=True)
finally:
    con.close()
