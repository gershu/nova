"""Page 9 — Database Browser (read-only).

Inventar als klickbare Tabelle (single-row select). Zeilenauswahl
oeffnet darunter die Detail-Ansicht mit zwei Tabs:
  - Daten:  SELECT * FROM <obj> LIMIT 100
  - SQL:    Live aus DuckDB; fuer Views formatiert (depth-aware
            pretty-printer); fuer Tables rekonstruiert aus PRAGMA
            table_info + duckdb_indexes.

Read-only — kein Edit-Pfad. Edit via modules.db_edit oder direkt SQL.
"""

from __future__ import annotations

import re

import streamlit as st

from modules.dashboard.db import connection, run_query


# ---------- SQL-Formatter ----------

_MAJOR_KW = [
    "LEFT OUTER JOIN", "RIGHT OUTER JOIN", "FULL OUTER JOIN",
    "LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN", "INNER JOIN", "JOIN",
    "SELECT DISTINCT", "SELECT",
    "FROM", "WHERE",
    "GROUP BY", "ORDER BY", "HAVING", "LIMIT", "OFFSET",
    "UNION ALL", "UNION", "INTERSECT", "EXCEPT",
    "WITH",
]
_KW_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in _MAJOR_KW) + r")\b", re.I)


def format_view_sql(sql: str, indent: str = "  ") -> str:
    """Depth-aware Pretty-Printer fuer DuckDB-View-DDLs.

    - Major-Keywords (SELECT/FROM/JOIN/WHERE/...) auf eigene Zeile.
    - SELECT-Liste: Kommas auf top-level (depth=0) bekommen Newline + Indent.
    - Innerhalb von Parens (CTE, Window, Subquery, CASE): kompakt bleibt.
    """
    if not sql:
        return sql
    s = re.sub(r"\s+", " ", sql.strip().rstrip(";"))
    m = re.match(r"^(CREATE(?:\s+OR\s+REPLACE)?\s+VIEW\s+\S+)\s+AS\s+", s, re.I)
    header, body = (m.group(1).strip(), s[m.end():]) if m else ("", s)

    out:    list[str] = []
    cur:    list[str] = []
    depth = 0
    in_keyword_block = False
    in_select_list   = False

    def flush() -> None:
        nonlocal cur
        if cur:
            line = "".join(cur).strip()
            if line:
                prefix = indent if in_keyword_block else ""
                out.append(prefix + line)
            cur = []

    i = 0
    while i < len(body):
        if depth == 0:
            mk = _KW_RE.match(body, i)
            # Word-boundary davor: Anfang, Whitespace, oder schliessende Klammer
            # (CTE-Body endet mit `)` und dann kommt direkt SELECT).
            if mk and (i == 0 or body[i - 1] in " )"):
                kw = mk.group(1).upper()
                flush()
                out.append(kw)
                in_keyword_block = True
                in_select_list   = kw.startswith("SELECT")
                i = mk.end()
                while i < len(body) and body[i] == " ":
                    i += 1
                continue

        ch = body[i]
        if ch == "(":
            depth += 1; cur.append(ch); i += 1
        elif ch == ")":
            depth -= 1; cur.append(ch); i += 1
        elif ch == "," and depth == 0 and in_select_list:
            cur.append(",")
            flush()
            i += 1
            while i < len(body) and body[i] == " ":
                i += 1
        else:
            cur.append(ch); i += 1

    flush()
    body_out = "\n".join(out)
    return f"{header} AS\n{body_out};" if header else body_out + ";"


# ---------- DDL-Helpers ----------

def _table_ddl(name: str) -> str:
    """CREATE TABLE rekonstruieren aus PRAGMA table_info + duckdb_indexes."""
    with connection() as con:
        cols     = con.execute(f'PRAGMA table_info("{name}")').fetchall()
        idx_rows = con.execute("""
            SELECT sql FROM duckdb_indexes()
            WHERE table_name = ? AND is_primary = FALSE
            ORDER BY index_name
        """, [name]).fetchall()
    if not cols:
        return f"-- Keine Spalten-Info fuer {name}"

    pk_cols = [c[1] for c in cols if c[5]]
    max_name = max(len(c[1]) for c in cols)
    line_parts: list[str] = []
    for c in cols:
        col_name, col_type, notnull, dflt = c[1], c[2], c[3], c[4]
        line = f'  "{col_name}"'.ljust(6 + max_name) + f" {col_type}"
        if notnull:
            line += " NOT NULL"
        if dflt is not None and str(dflt) != "":
            line += f" DEFAULT {dflt}"
        line_parts.append(line)
    if len(pk_cols) > 1:
        line_parts.append(f"  PRIMARY KEY ({', '.join(pk_cols)})")
    elif len(pk_cols) == 1:
        for i, c in enumerate(cols):
            if c[1] == pk_cols[0]:
                line_parts[i] += " PRIMARY KEY"

    ddl = f"CREATE TABLE {name} (\n" + ",\n".join(line_parts) + "\n);"
    if idx_rows:
        ddl += "\n\n-- Indexes:\n" + "\n".join(
            (r[0] if r[0].rstrip().endswith(";") else r[0] + ";")
            for r in idx_rows if r[0]
        )
    return ddl


def _view_ddl(name: str) -> str:
    with connection() as con:
        row = con.execute("""
            SELECT sql FROM duckdb_views()
            WHERE view_name = ? AND schema_name = 'main'
        """, [name]).fetchone()
    if not row or not row[0]:
        return f"-- Keine View-Definition fuer {name}"
    return format_view_sql(row[0])


# ---------- Page ----------

st.title("🗄 Nova Database")

inventory = run_query("""
    SELECT table_name, table_type
    FROM information_schema.tables
    WHERE table_schema = 'main'
    ORDER BY table_type, table_name
""")
if inventory.empty:
    st.warning("Keine Tabellen/Views in der DB.")
    st.stop()

# Row-counts cached
@st.cache_data(ttl=60, show_spinner=False)
def _row_counts(names: tuple[str, ...]) -> dict[str, int]:
    out: dict[str, int] = {}
    with connection() as c:
        for n in names:
            try:
                out[n] = int(c.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0])
            except Exception:  # noqa: BLE001
                out[n] = -1
    return out

inventory["rows"] = inventory["table_name"].map(
    _row_counts(tuple(inventory["table_name"].tolist()))
)
# Hint fuer den User: type-spalte lesbarer
inventory["kind"] = inventory["table_type"].map(
    {"BASE TABLE": "TABLE", "VIEW": "VIEW"}
).fillna(inventory["table_type"])

n_tables = int((inventory["table_type"] == "BASE TABLE").sum())
n_views  = int((inventory["table_type"] == "VIEW").sum())
st.caption(f"{n_tables} Tabellen · {n_views} Views · {len(inventory)} total — "
           f"Zeile anklicken fuer Detail-Ansicht")

# Klickbare Tabelle (single-row select)
display_df = inventory[["table_name", "kind", "rows"]].rename(
    columns={"table_name": "Name", "kind": "Type", "rows": "Rows"}
)
selection = st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    selection_mode="single-row",
    on_select="rerun",
    column_config={
        "Name": st.column_config.TextColumn(width="medium"),
        "Type": st.column_config.TextColumn(width="small"),
        "Rows": st.column_config.NumberColumn(format="%d"),
    },
)

selected_rows = selection.selection.rows if selection and hasattr(selection, "selection") else []
if not selected_rows:
    st.info("Eine Zeile auswaehlen fuer Daten + SQL.")
    st.stop()

row = display_df.iloc[selected_rows[0]]
selected = row["Name"]
kind     = row["Type"]
n_rows   = int(row["Rows"])

st.divider()
st.subheader(f"{selected}")
st.caption(f"{kind} · {n_rows:,} rows")

tab_data, tab_sql = st.tabs(["Daten", "SQL"])

with tab_data:
    try:
        df = run_query(f'SELECT * FROM "{selected}" LIMIT 100')
    except Exception as e:  # noqa: BLE001
        st.error(f"Query fehlgeschlagen: {e.__class__.__name__}: {e}")
        df = None
    if df is not None:
        if df.empty:
            st.info("Tabelle/View ist leer.")
        else:
            st.dataframe(df, use_container_width=True, height=560)

with tab_sql:
    try:
        ddl = _view_ddl(selected) if kind == "VIEW" else _table_ddl(selected)
        st.code(ddl, language="sql")
    except Exception as e:  # noqa: BLE001
        st.error(f"DDL-Rekonstruktion fehlgeschlagen: {e.__class__.__name__}: {e}")
