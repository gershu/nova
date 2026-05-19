"""Read-only DuckDB-Helper fuer Streamlit-Pages.

Connections sind KURZLEBIG (per Query frisch geoeffnet + geschlossen).
Hintergrund: DuckDB-File-Locking erlaubt entweder mehrere read-only-Connections
ODER einen read-write-Prozess — nicht gemischt. Wenn das Streamlit-Daemon
eine permanente read-only-Connection halten wuerde, koennte parallel laufender
db_edit (read_write) die Datei nicht mehr lock-en und Updates schlagen fehl.

Strategie:
  - Jede Query oeffnet eine frische read_only-Connection (Open-Cost ~10ms).
  - Connection wird sofort nach dem Query geschlossen.
  - Streamlit @st.cache_data cached weiterhin die Query-Results (DataFrame),
    nicht die Connection.

Caller-Konvention:
  - Bevorzugt: `with connection() as con:` — explizit close.
  - Backward-compat: `get_connection()` gibt frische Connection zurueck,
    Caller MUSS selbst close() rufen.
"""

from __future__ import annotations

import os
import pathlib
from contextlib import contextmanager

import duckdb
import streamlit as st


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)


def _ensure_db() -> None:
    if not DB_PATH.is_file():
        st.error(f"DB nicht gefunden: {DB_PATH}")
        st.stop()


def _connect() -> duckdb.DuckDBPyConnection:
    """Frische read-only Connection. KEIN Caching — Caller schliesst."""
    _ensure_db()
    return duckdb.connect(str(DB_PATH), read_only=True)


@contextmanager
def connection():
    """Context-Manager fuer kurzlebige Read-Only-Connection.

    Beispiel:
        with connection() as con:
            df = con.execute("SELECT * FROM v_mkt_holdings").df()
    """
    con = _connect()
    try:
        yield con
    finally:
        con.close()


def get_connection() -> duckdb.DuckDBPyConnection:
    """Frische read-only Connection. Caller MUSS selbst close() rufen.

    Wenn moeglich `connection()` als Context-Manager bevorzugen.
    """
    return _connect()


@st.cache_data(ttl=60, show_spinner=False)
def run_query(sql: str, params: tuple | None = None):
    """Cache-bare Query-Wrapper. Verwendet kurzlebige Connection.

    Streamlit cached das DataFrame-Ergebnis fuer 60s — die Connection wird
    sofort geschlossen, kein File-Lock auf der DB. Bei DB-Change ist der
    Cache moeglicherweise stale; F5 oder Streamlit "Clear cache" hilft.
    """
    with connection() as con:
        if params:
            return con.execute(sql, list(params)).df()
        return con.execute(sql).df()


def table_exists(name: str) -> bool:
    with connection() as con:
        row = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchone()
    return row is not None
