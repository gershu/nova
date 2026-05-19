"""Common DuckDB read-only helper fuer Notebook-Analysen.

Verwendung in einer Notebook-Zelle:

    from modules.analysis._db import connect, df
    con = connect()                                          # read-only
    holdings = df(con, "SELECT * FROM pos_holdings")
    con.close()

Oder als Context-Manager:

    from modules.analysis._db import session
    with session() as con:
        holdings = df(con, "SELECT * FROM pos_holdings")

Defaults sind so gewaehlt, dass Notebooks portabel sind:
- LAB_DB_PATH env-var ueberschreibt den Default
- Default-Pfad ~/nova_data/lab.duckdb (= hub-Layout, gilt auch wenn
  Notebook lokal mit gemounteter DB ausgefuehrt wird).
"""

from __future__ import annotations

import contextlib
import os
import pathlib
from typing import Any, Sequence

import duckdb


DEFAULT_DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)


def connect(path: pathlib.Path | str | None = None) -> duckdb.DuckDBPyConnection:
    """Oeffnet eine read-only Verbindung zur lab-DB.

    Notebooks haben *nie* einen DB-Write-Use-Case — wir sind reine
    Konsumenten der von ingest/portfolio/monitor/screener_csp persistierten
    Daten.
    """
    p = pathlib.Path(path) if path else DEFAULT_DB_PATH
    if not p.is_file():
        raise FileNotFoundError(
            f"DuckDB nicht gefunden unter {p}. "
            f"Setze LAB_DB_PATH oder pruefe Mount auf nova-hub."
        )
    return duckdb.connect(str(p), read_only=True)


@contextlib.contextmanager
def session(path: pathlib.Path | str | None = None):
    """Context-Manager-Variante — schliesst Connection sauber."""
    con = connect(path)
    try:
        yield con
    finally:
        con.close()


def df(con: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] | None = None):
    """Query -> pandas.DataFrame Konvenienz-Wrapper.

    DuckDB hat .df() schon eingebaut; dieser Wrapper macht nur das
    params-Handling konsistent und gibt eine bessere Fehlermeldung
    wenn die Tabelle fehlt.
    """
    try:
        if params is not None:
            return con.execute(sql, list(params)).df()
        return con.execute(sql).df()
    except duckdb.CatalogException as e:
        raise RuntimeError(
            f"Schema/Tabelle fehlt — Migrations geladen? Original: {e}"
        ) from e


def list_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Alle Basis-Tabellen (kein View) — fuer schnelle Notebook-Inspektion."""
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' "
        "ORDER BY table_name"
    ).fetchall()
    return [r[0] for r in rows]


def row_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Row-Count pro Tabelle — Smoke-Check 'ist die DB ueberhaupt populiert'."""
    out: dict[str, int] = {}
    for t in list_tables(con):
        try:
            n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            out[t] = int(n)
        except Exception:  # noqa: BLE001
            out[t] = -1
    return out
