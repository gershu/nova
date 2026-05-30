"""Status-Reader: liest daemons.yaml und ermittelt je Daemon den aktuellen
Zustand aus DB / Prozessliste / Log-Datei.

Read-only. Kein Daemon, keine Schreibops. Wird von CLI, Dashboard-Page und
dem run-Subcommand des Health-Daemons benutzt.

Pruefstrategien — automatisch gewaehlt:
  - hat audit_table   -> juengste Zeile aus Audit-Tabelle: ts + status + metric
  - hat primary_table -> max(timestamp_column)
  - hat process_check -> pgrep -fl
  - hat port_check    -> lsof -i :PORT
  - hat log_path      -> mtime der Log-Datei
Bei Long-Running zaehlt process/port; Alter ist egal. Bei DB-/Log-Modi
wird das Alter gegen stale_after_hours geprueft.

overall-Status:
  fresh   = innerhalb von stale_after_hours, status != fail
  stale   = aelter als stale_after_hours
  failed  = letzter audit-status = 'fail' (oder 'partial' wenn man strenger ist)
  up      = long-running Prozess + ggf. Port erreichbar
  down    = long-running, aber Prozess weg oder Port zu
  unknown = Datenquelle nicht ermittelbar (Tabelle fehlt, Log fehlt, etc.)
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import duckdb
import yaml


DEFAULT_YAML = (pathlib.Path(__file__).parent.parent.parent
                / "config" / "daemons.yaml")


@dataclass
class DaemonStatus:
    label:           str
    title:           str
    group:           str
    schedule:        str

    last_run_ts:     datetime | None = None
    last_run_status: str | None = None       # 'ok'|'partial'|'fail'
    metric:          str | None = None       # human-readable
    process_running: bool | None = None
    port_open:       bool | None = None

    overall:         str = "unknown"          # fresh|stale|failed|up|down|unknown
    age_hours:       float | None = None      # nur fuer time-basierte Checks
    detail:          str | None = None        # menschenlesbarer Hinweis
    raw:             dict = field(default_factory=dict)


# ---------- YAML laden ----------

def load_manifest(path: pathlib.Path | None = None) -> dict:
    p = path or DEFAULT_YAML
    if not p.is_file():
        raise FileNotFoundError(f"daemons.yaml fehlt: {p}")
    return yaml.safe_load(p.read_text()) or {}


# ---------- Helfer ----------

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _age_hours(ts) -> float | None:
    """Stunden seit ts. Akzeptiert datetime, date oder ISO-String."""
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts[:19])
        except ValueError:
            return None
    elif isinstance(ts, date) and not isinstance(ts, datetime):
        # DATE -> datetime at midnight
        ts = datetime(ts.year, ts.month, ts.day)
    elif isinstance(ts, datetime) and ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    # Negative Alter (Zeit in der Zukunft) auf 0 klemmen — passiert bei
    # Timezone-Restungenauigkeit zwischen UTC und Local-Naive-Stempeln.
    return max(0.0, (_now() - ts).total_seconds() / 3600.0)


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).fetchone()
    return row is not None


def _pgrep(name: str) -> bool:
    if shutil.which("pgrep") is None:
        return False
    try:
        r = subprocess.run(["pgrep", "-fl", name],
                           capture_output=True, text=True, timeout=3)
        return r.returncode == 0 and bool(r.stdout.strip())
    except subprocess.SubprocessError:
        return False


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    if shutil.which("lsof") is not None:
        try:
            r = subprocess.run(
                ["lsof", "-nP", f"-iTCP@{host}:{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                return True
        except subprocess.SubprocessError:
            pass
    # Fallback via nc (BSD-Variante macOS, GNU-Variante Linux)
    for nc_opts in [["-G", "2"], ["-w", "2"]]:
        if shutil.which("nc") is None:
            return False
        try:
            r = subprocess.run(
                ["nc", "-z", *nc_opts, host, str(port)],
                capture_output=True, timeout=3)
            if r.returncode == 0:
                return True
        except subprocess.SubprocessError:
            pass
    return False


# ---------- Check-Modi ----------

def _check_audit(con, d, status: DaemonStatus) -> None:
    """Audit-Tabelle: juengste Zeile holen."""
    tbl = d["audit_table"]
    if not _table_exists(con, tbl):
        status.detail = f"audit-Tabelle {tbl} fehlt"
        return
    status_col = d.get("status_column", "status")
    metric_col = d.get("metric_column")
    cols = ["ts" if _has_col(con, tbl, "ts")
            else "finished_at" if _has_col(con, tbl, "finished_at")
            else "started_at"]
    cols.append(status_col if _has_col(con, tbl, status_col) else "NULL")
    if metric_col:
        cols.append(metric_col if _has_col(con, tbl, metric_col) else "NULL")
    sql = (f"SELECT {', '.join(cols)} FROM {tbl} "
           f"ORDER BY {cols[0]} DESC LIMIT 1")
    row = con.execute(sql).fetchone()
    if not row:
        status.detail = "audit-Tabelle leer"
        return
    status.last_run_ts     = row[0]
    status.last_run_status = row[1]
    if metric_col and len(row) > 2:
        status.metric = f"{row[2]} {metric_col}" if row[2] is not None else None


def _has_col(con, table, col) -> bool:
    return bool(con.execute(
        f"SELECT 1 FROM information_schema.columns "
        f"WHERE table_name=? AND column_name=?", [table, col]).fetchone())


def _check_primary(con, d, status: DaemonStatus) -> None:
    """primary_table: max(timestamp_column) als letzte Aktivitaet."""
    tbl = d["primary_table"]
    col = d.get("timestamp_column", "ts")
    if not _table_exists(con, tbl):
        status.detail = f"Tabelle {tbl} fehlt"
        return
    row = con.execute(f"SELECT MAX({col}), COUNT(*) FROM {tbl}").fetchone()
    if not row or row[0] is None:
        status.detail = f"{tbl} leer"
        return
    status.last_run_ts = row[0]
    status.metric = f"{row[1]} rows total"


def _check_log_path(d, status: DaemonStatus) -> None:
    p = pathlib.Path(d["log_path"])
    if not p.is_file():
        status.detail = f"Log {p} nicht vorhanden"
        return
    status.last_run_ts = datetime.fromtimestamp(p.stat().st_mtime)
    status.metric = f"{p.stat().st_size // 1024} KB log"


def _check_service(d, status: DaemonStatus) -> None:
    pname = d.get("process_check")
    port  = d.get("port_check")
    proc_ok = _pgrep(pname) if pname else None
    port_ok = _port_open(int(port)) if port else None
    status.process_running = proc_ok
    status.port_open = port_ok
    bits = []
    if pname:
        bits.append(f"proc {pname}={'up' if proc_ok else 'DOWN'}")
    if port:
        bits.append(f"port {port}={'open' if port_ok else 'CLOSED'}")
    status.metric = " · ".join(bits) if bits else None


# ---------- Hauptlogik ----------

def _classify(d: dict, status: DaemonStatus) -> None:
    """overall aus den eingesammelten Werten ableiten."""
    if d.get("process_check") or d.get("port_check"):
        proc_ok = status.process_running
        port_ok = status.port_open
        # required = was definiert ist
        ok = True
        if d.get("process_check") and not proc_ok:
            ok = False
        if d.get("port_check") and not port_ok:
            ok = False
        status.overall = "up" if ok else "down"
        return

    if status.last_run_ts is None:
        status.overall = "unknown"
        return

    status.age_hours = _age_hours(status.last_run_ts)
    stale_h = float(d.get("stale_after_hours", 26))
    if status.last_run_status == "fail":
        status.overall = "failed"
    elif status.age_hours is not None and status.age_hours > stale_h:
        status.overall = "stale"
    else:
        status.overall = "fresh"


def check_one(con: duckdb.DuckDBPyConnection, d: dict) -> DaemonStatus:
    status = DaemonStatus(
        label=    d["label"],
        title=    d.get("title", d["label"]),
        group=    d.get("group", "other"),
        schedule= d.get("schedule", ""),
    )
    try:
        if d.get("audit_table"):
            _check_audit(con, d, status)
        elif d.get("primary_table"):
            _check_primary(con, d, status)
        if d.get("process_check") or d.get("port_check"):
            _check_service(d, status)
        if status.last_run_ts is None and d.get("log_path"):
            _check_log_path(d, status)
    except Exception as e:  # noqa: BLE001
        status.detail = f"check error: {e.__class__.__name__}: {e}"
    _classify(d, status)
    return status


def check_all(con: duckdb.DuckDBPyConnection,
               manifest: dict | None = None) -> list[DaemonStatus]:
    """Pro Daemon ein DaemonStatus, in Manifest-Reihenfolge."""
    m = manifest or load_manifest()
    return [check_one(con, d) for d in m.get("daemons", [])]


def summary(statuses: list[DaemonStatus]) -> dict[str, int]:
    out = {"total": len(statuses),
           "fresh": 0, "stale": 0, "failed": 0,
           "up": 0, "down": 0, "unknown": 0}
    for s in statuses:
        out[s.overall] = out.get(s.overall, 0) + 1
    return out
