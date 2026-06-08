"""LLM-Job-Queue — reine DB-Helfer (DuckDB). Single-Writer-Annahme: genau
ein Worker schreibt den Status; daher kein Locking noetig."""

from __future__ import annotations

import json
import pathlib
import uuid
from datetime import datetime, timezone

import duckdb

SQL_DIR = pathlib.Path(__file__).parent / "sql"
SQL_FILES = sorted(SQL_DIR.glob("0*.sql"))


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    for f in SQL_FILES:
        con.execute(f.read_text())


def _now():
    return datetime.now(timezone.utc)


def enqueue(con, kind: str, *, ref_instrument_id=None, payload=None,
            priority: int = 100, input_hash=None) -> str | None:
    """Job anlegen. Dedupe: existiert fuer (kind, ref_instrument_id) bereits
    ein offener Job (pending/running), wird None zurueckgegeben."""
    if ref_instrument_id is not None:
        dup = con.execute(
            "SELECT 1 FROM llm_jobs WHERE kind = ? AND ref_instrument_id = ? "
            "AND status IN ('pending','running') LIMIT 1",
            [kind, ref_instrument_id]).fetchone()
        if dup:
            return None
    jid = uuid.uuid4().hex
    now = _now()
    con.execute(
        "INSERT INTO llm_jobs (job_id, kind, ref_instrument_id, payload_json, "
        "priority, input_hash, status, attempts, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,'pending',0,?,?)",
        [jid, kind, ref_instrument_id, json.dumps(payload or {}), priority,
         input_hash, now, now])
    return jid


def claim(con) -> dict | None:
    """Naechsten pending-Job (nach priority, created_at) auf 'running' setzen
    und zurueckgeben. None, wenn die Queue leer ist."""
    row = con.execute(
        "SELECT job_id, kind, ref_instrument_id, payload_json, input_hash, "
        "attempts FROM llm_jobs WHERE status = 'pending' "
        "ORDER BY priority, created_at LIMIT 1").fetchone()
    if not row:
        return None
    jid = row[0]
    con.execute("UPDATE llm_jobs SET status='running', attempts=attempts+1, "
                "updated_at=? WHERE job_id=?", [_now(), jid])
    return {"job_id": jid, "kind": row[1], "ref_instrument_id": row[2],
            "payload": json.loads(row[3] or "{}"), "input_hash": row[4],
            "attempts": (row[5] or 0) + 1}


def complete(con, job_id: str, result: str | None) -> None:
    con.execute("UPDATE llm_jobs SET status='done', result=?, error=NULL, "
                "updated_at=? WHERE job_id=?", [result, _now(), job_id])


def fail(con, job_id: str, error: str) -> None:
    con.execute("UPDATE llm_jobs SET status='error', error=?, updated_at=? "
                "WHERE job_id=?", [error[:2000], _now(), job_id])


def requeue(con, job_id: str, note: str | None = None) -> None:
    """Job zurueck auf 'pending' (z.B. bei transientem LLM-Ausfall) — wird
    beim naechsten Lauf erneut versucht."""
    con.execute("UPDATE llm_jobs SET status='pending', error=?, updated_at=? "
                "WHERE job_id=?", [note, _now(), job_id])


def reset(con, *, errors: bool = True, stuck: bool = False) -> int:
    """'error' (und optional haengende 'running') Jobs auf 'pending' setzen.
    Returnt Anzahl betroffener Zeilen."""
    states = (["error"] if errors else []) + (["running"] if stuck else [])
    if not states:
        return 0
    ph = ",".join("?" for _ in states)
    n = con.execute(f"SELECT COUNT(*) FROM llm_jobs WHERE status IN ({ph})",
                    states).fetchone()[0]
    con.execute(f"UPDATE llm_jobs SET status='pending', error=NULL, "
                f"updated_at=? WHERE status IN ({ph})", [_now(), *states])
    return int(n)


def counts(con) -> dict:
    rows = con.execute(
        "SELECT status, COUNT(*) FROM llm_jobs GROUP BY status").fetchall()
    return {s: n for s, n in rows}
