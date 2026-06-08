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
from datetime import datetime, timezone

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
                       "last_run_ts", "age_hours",
                       "metric", "detail"]].copy()
    display["overall"]    = display["overall"].map(_GLYPH)
    display["last_run_ts"] = display["last_run_ts"].map(_fmt_ts)
    display["age_hours"]   = display["age_hours"].map(_fmt_age)
    # metric (Erfolgs-Output) bevorzugt, sonst detail (Fehler-/Hinweis-Text).
    display["Metric / Hinweis"] = display["metric"].fillna("") \
        .where(display["metric"].notna(), display["detail"].fillna(""))
    display = display.drop(columns=["metric", "detail"])
    display.rename(columns={
        "title":       "Daemon",
        "schedule":    "Schedule",
        "overall":     "Status",
        "last_run_ts": "Letzter Lauf",
        "age_hours":   "Alter",
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

# ---------- LLM-Pipeline (Job-Queue + nova-w5) ----------

_STATUS_ORDER = ["pending", "running", "done", "error"]


def _ago(dt) -> str:
    """Relativzeit; tz-aware -> UTC, naive -> lokale Wanduhr (DuckDB liefert
    TIMESTAMP naiv in Lokalzeit)."""
    if dt is None:
        return "—"
    now = (datetime.now(timezone.utc) if getattr(dt, "tzinfo", None)
           else datetime.now())
    sec = int((now - dt).total_seconds())
    past, sec = sec >= 0, abs(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    s = " ".join(p for p in (f"{d}d" if d else "", f"{h}h" if h else "",
                             f"{m}m" if (m and not d) else "") if p) or "0m"
    return f"vor {s}" if past else f"in {s}"


def _gb(n) -> str:
    try:
        return f"{float(n) / 1e9:.1f} GB"
    except (TypeError, ValueError):
        return "?"


@st.cache_data(ttl=30, show_spinner=False)
def _llm_status() -> dict:
    """Queue-Zaehler (llm_jobs) + nova-w5 health/ps. Defensiv: fehlende
    Tabelle / LLM down werden abgefangen."""
    out: dict = {"have_table": False, "counts": {}, "matrix": [],
                 "oldest": None, "last_done": None, "err": None,
                 "host": None, "health_ok": None, "health_msg": "",
                 "models": []}
    con = get_connection()
    try:
        if con.execute("SELECT 1 FROM information_schema.tables "
                       "WHERE table_name='llm_jobs'").fetchone():
            out["have_table"] = True
            out["counts"] = dict(con.execute(
                "SELECT status, COUNT(*) FROM llm_jobs "
                "GROUP BY status").fetchall())
            out["matrix"] = [list(r) for r in con.execute(
                "SELECT kind, status, COUNT(*) FROM llm_jobs "
                "GROUP BY kind, status").fetchall()]
            out["oldest"] = con.execute("SELECT MIN(created_at) FROM llm_jobs "
                                        "WHERE status='pending'").fetchone()[0]
            out["last_done"] = con.execute("SELECT MAX(updated_at) FROM "
                                           "llm_jobs WHERE status='done'"
                                           ).fetchone()[0]
            e = con.execute(
                "SELECT updated_at, kind, ref_instrument_id, error "
                "FROM llm_jobs WHERE status='error' "
                "ORDER BY updated_at DESC LIMIT 1").fetchone()
            out["err"] = list(e) if e else None
    finally:
        con.close()
    try:
        from modules.llm.client import OllamaClient
        out["host"] = OllamaClient().host
        with OllamaClient() as llm:
            ok, msg = llm.health_check()
            out["health_ok"], out["health_msg"] = ok, msg
            if ok:
                out["models"] = [{
                    "name": m.get("name") or m.get("model") or "?",
                    "size": m.get("size"), "size_vram": m.get("size_vram"),
                    "expires_at": m.get("expires_at")} for m in llm.ps()]
    except Exception as e:  # noqa: BLE001
        out["health_ok"], out["health_msg"] = False, \
            f"{e.__class__.__name__}: {e}"
    return out


st.subheader("🧠 LLM-Pipeline")
L = _llm_status()

if not L["have_table"]:
    st.caption("Keine `llm_jobs`-Tabelle — Worker noch nicht gelaufen. "
               "Init: `python -m modules.llm.jobs worker --once`.")
else:
    c = L["counts"]
    j1, j2, j3, j4 = st.columns(4)
    j1.metric("pending", c.get("pending", 0))
    j2.metric("running", c.get("running", 0))
    j3.metric("done", c.get("done", 0))
    j4.metric("error", c.get("error", 0),
              delta=None if not c.get("error") else "prüfen",
              delta_color="inverse")
    st.caption(
        f"Ältester pending: {_fmt_ts(L['oldest'])} ({_ago(L['oldest'])})  ·  "
        f"letzter done: {_fmt_ts(L['last_done'])} ({_ago(L['last_done'])})")
    if L["err"]:
        ts, kind, rid, emsg = L["err"]
        st.warning(f"Letzter Fehler — {_fmt_ts(ts)} · `{kind}` {rid or ''}: "
                   f"{(emsg or '')[:160]}")
    if L["matrix"]:
        mdf = pd.DataFrame(L["matrix"], columns=["kind", "status", "n"])
        piv = (mdf.pivot_table(index="kind", columns="status", values="n",
                               aggfunc="sum", fill_value=0)
               .reindex(columns=_STATUS_ORDER, fill_value=0).reset_index())
        st.dataframe(piv, use_container_width=True, hide_index=True)

st.markdown(f"**nova-w5** (`{L.get('host') or '—'}`)")
if L["health_ok"]:
    st.success(f"erreichbar — {L['health_msg']}")
    if not L["models"]:
        st.caption("/api/ps: kein Modell resident (idle).")
    for m in L["models"]:
        when = ""
        exp = m.get("expires_at")
        if exp:
            try:
                dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                when = f"  ·  entladen {_ago(dt)}"
            except ValueError:
                pass
        st.caption(f"`{m['name']}`  ·  {_gb(m['size'])} "
                   f"(VRAM {_gb(m['size_vram'])}){when}")
else:
    st.error(f"nicht erreichbar — {L['health_msg']}")

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
