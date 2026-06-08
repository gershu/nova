"""Globaler Datei-Schreib-Lock fuer die DuckDB (Single-Writer-Koordination).

DuckDB erlaubt cross-process nur EINEN Read-Write-Prozess. Damit sich die
verschiedenen Schreiber (Batches, LLM-Worker, enqueue) nicht beim Oeffnen
einer RW-Connection in die Quere kommen, greift jeder Schreiber zuerst diesen
advisory File-Lock (fcntl.flock) und gibt ihn nach dem Schliessen frei.
Leser (Dashboard) nutzen read_only und nehmen den Lock NICHT.

Wichtig fuer Koexistenz mit dem Dashboard: RW-Connections moeglichst KURZ
halten (oeffnen -> schreiben -> schliessen). Langsame Arbeit (z.B.
LLM-Inferenz, sec-api-Fetches) gehoert NICHT in eine offene RW-Connection.

Lockdatei liegt neben der DB (LAB_DB_PATH) als .nova_write.lock.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib
import time


def db_path() -> str:
    return os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"))


def _lock_path() -> pathlib.Path:
    return pathlib.Path(db_path()).parent / ".nova_write.lock"


@contextlib.contextmanager
def write_lock(timeout: float = 60.0, poll: float = 0.5):
    """Exklusiver advisory Lock fuer die Dauer des with-Blocks.

    Wartet bis zu `timeout` Sekunden; danach TimeoutError. Aufrufer sollten
    den Block kurz halten.
    """
    p = _lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "w")
    deadline = time.time() + timeout
    try:
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    raise TimeoutError(
                        f"write_lock: Timeout nach {timeout}s ({p}) — anderer "
                        "Schreiber aktiv?")
                time.sleep(poll)
        yield
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


@contextlib.contextmanager
def rw_connection(timeout: float = 60.0):
    """Lock greifen + DuckDB read-write oeffnen, am Ende schliessen + freigeben.

    Den Block kurz halten (Claim/Persist), nicht ueber langsame I/O spannen.
    """
    import duckdb
    with write_lock(timeout=timeout):
        con = duckdb.connect(db_path())
        try:
            yield con
        finally:
            con.close()
