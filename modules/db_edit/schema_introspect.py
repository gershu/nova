"""DuckDB-Schema-Introspect: liest Spalten + PK fuer beliebige Tabelle.

PRAGMA table_info(<table>) gibt zurueck:
    cid | name | type | notnull | dflt_value | pk
DuckDB pk-Spalte ist 0 fuer non-PK, 1 fuer PK (Position waere bei Multi-Col
ueblicherweise via duckdb_constraints() Funktion).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

import duckdb


@dataclass
class ColumnInfo:
    name:        str
    db_type:     str                    # raw DuckDB type string
    notnull:     bool
    default:     Optional[str]
    is_pk:       bool


@dataclass
class TableInfo:
    table_name:  str
    columns:     list[ColumnInfo] = field(default_factory=list)

    @property
    def pk_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.is_pk]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column_by_name(self, name: str) -> Optional[ColumnInfo]:
        for c in self.columns:
            if c.name.lower() == name.lower():
                return c
        return None


def get_table_info(con: duckdb.DuckDBPyConnection, table: str) -> Optional[TableInfo]:
    """Returns TableInfo oder None wenn Tabelle nicht existiert."""
    # Quote table name to allow reserved words
    try:
        rows = con.execute(f'PRAGMA table_info("{table}")').fetchall()
    except duckdb.CatalogException:
        return None
    if not rows:
        return None
    cols: list[ColumnInfo] = []
    for r in rows:
        # (cid, name, type, notnull, dflt_value, pk)
        cols.append(ColumnInfo(
            name    = r[1],
            db_type = r[2],
            notnull = bool(r[3]),
            default = r[4],
            is_pk   = bool(r[5]),
        ))
    return TableInfo(table_name=table, columns=cols)


def list_user_tables(con: duckdb.DuckDBPyConnection,
                      *, include_views: bool = False) -> list[tuple[str, int]]:
    """Returns list[(table_name, row_count)]. Sortiert nach Name.

    include_views=False filtert Views raus (man editiert keine Views).
    """
    where_clause = "table_type = 'BASE TABLE'"
    if include_views:
        where_clause = "table_type IN ('BASE TABLE', 'VIEW')"
    names = con.execute(f"""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main' AND {where_clause}
        ORDER BY table_name
    """).fetchall()
    out: list[tuple[str, int]] = []
    for (t,) in names:
        try:
            n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception:  # noqa: BLE001
            n = -1
        out.append((t, int(n)))
    return out


# ---------- Type-Coercion ----------

def coerce_value(value: Any, db_type: str) -> Any:
    """Wandle Excel-Cell-Value in DB-passenden Python-Typ.

    Excel-Cells koennen sein: str, int, float, bool, datetime, None.
    DuckDB-Types werden grob normalisiert (case-insensitive substring match).
    NaN, None und leere Strings -> None.
    """
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    if isinstance(value, float) and value != value:   # NaN
        return None

    t = (db_type or "").upper()

    # INTEGER family
    if any(x in t for x in ("INT", "BIGINT", "SMALLINT", "TINYINT")):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            raise ValueError(f"Cannot coerce {value!r} ({type(value).__name__}) to INTEGER")

    # DOUBLE / REAL / FLOAT / NUMERIC
    if any(x in t for x in ("DOUBLE", "FLOAT", "REAL", "NUMERIC", "DECIMAL")):
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Cannot coerce {value!r} to DOUBLE")

    # BOOLEAN
    if "BOOL" in t:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("true", "1", "yes", "y", "t"):
                return True
            if v in ("false", "0", "no", "n", "f"):
                return False
        raise ValueError(f"Cannot coerce {value!r} to BOOLEAN")

    # DATE / TIMESTAMP
    if "TIMESTAMP" in t or "DATETIME" in t:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
        if isinstance(value, str):
            return datetime.fromisoformat(value.strip())
        raise ValueError(f"Cannot coerce {value!r} to TIMESTAMP")
    if "DATE" in t:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            return date.fromisoformat(value.strip())
        raise ValueError(f"Cannot coerce {value!r} to DATE")

    # VARCHAR / TEXT / default -> str
    return str(value)


def is_truncatable(table: str) -> bool:
    """Defensive: System-Views/-Tabellen sind NICHT truncatable.

    Stefan editiert pflegt z.B. ref_*-Stammdaten oder list_*-Sichten —
    technische audit_*-Logs sollten nicht via Excel truncated werden.
    """
    return not table.startswith("audit_")
