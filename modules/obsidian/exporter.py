"""Obsidian-Markdown-Primitives.

Pure-Function-Modul. Erzeugt:
  - Frontmatter (YAML)
  - Tabellen (GFM, Obsidian-konform)
  - Wiki-Links auf Ticker-Master-Files
  - Doc-Skeleton (write_doc)

Convention:
  - Wiki-Links auf Tickers IMMER via [[AAPL]] (Symbol-only, nicht ref_instrument_id).
    Master-File heisst `AAPL.md` damit Stefan's spaetere eigene Notes
    direkt mit Plain-Symbol referenzierbar sind.
  - Filenames: snake_case + ISO-Date wenn datums-tagging sinnvoll.
"""

from __future__ import annotations

import csv
import pathlib
from datetime import date, datetime
from typing import Any, Iterable, Optional


def frontmatter(d: dict) -> str:
    """YAML-Frontmatter aus dict. Werte werden flach serialisiert.

    Dataview-Plugin (wenn Stefan es spaeter installiert) liest das direkt;
    aber auch ohne Plugin ist YAML in Obsidian valide.
    """
    if not d:
        return ""
    lines = ["---"]
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, list):
            if not v:
                continue
            # Inline-Liste fuer kurze, Block-Liste fuer lange
            items_str = ", ".join(str(x) for x in v)
            if len(items_str) <= 80:
                lines.append(f"{k}: [{items_str}]")
            else:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
        elif isinstance(v, (date, datetime)):
            lines.append(f"{k}: {v.isoformat()}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {str(v).lower()}")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            s = str(v).replace("\n", " ").strip()
            # Quote-Werte mit Special-Chars (für Obsidian/YAML-Safety)
            if any(c in s for c in ":#&*!|>{}[]"):
                s = s.replace('"', '\\"')
                lines.append(f'{k}: "{s}"')
            else:
                lines.append(f"{k}: {s}")
    lines.append("---")
    return "\n".join(lines)


def ticker_link(symbol: str, label: str | None = None) -> str:
    """[[AAPL]] oder [[AAPL|Apple Inc.]]"""
    s = symbol.strip()
    if not s:
        return "—"
    if label and label.strip() and label.strip() != s:
        return f"[[{s}|{label.strip()}]]"
    return f"[[{s}]]"


def md_table(headers: list[str], rows: list[list[Any]], align: list[str] | None = None) -> str:
    """GFM-Tabelle. Werte werden str()-gewandelt; None -> '—'.

    align: 'l', 'r', 'c' pro Spalte. Default = links.
    """
    if not headers:
        return ""
    n_cols = len(headers)
    align = align or ["l"] * n_cols
    sep_map = {"l": ":---", "r": "---:", "c": ":---:"}
    sep = "| " + " | ".join(sep_map.get(a, ":---") for a in align[:n_cols]) + " |"

    def fmt(v):
        if v is None:
            return "—"
        if isinstance(v, float):
            if v != v:    # NaN
                return "—"
            # Integer-valued floats (e.g. CSV-parsed "1" -> 1.0) -> als int rendern
            if v.is_integer() and abs(v) < 1e15:
                return f"{int(v):,}" if abs(v) >= 1000 else str(int(v))
            if abs(v) >= 1000:
                return f"{v:,.2f}"
            return f"{v:.2f}"
        return str(v)

    lines = ["| " + " | ".join(headers) + " |", sep]
    for row in rows:
        # Pad zu n_cols
        padded = list(row) + [None] * (n_cols - len(row))
        lines.append("| " + " | ".join(fmt(c) for c in padded[:n_cols]) + " |")
    return "\n".join(lines)


def section(title: str, body: str, level: int = 2) -> str:
    """Markdown-Section: '## Title\\n\\n{body}\\n'."""
    return f"{'#' * level} {title}\n\n{body}\n"


def write_doc(path: pathlib.Path, fm: dict | None, body: str) -> None:
    """Schreibt ein MD-File mit Frontmatter + Body. Erzeugt Parent-Dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    if fm:
        parts.append(frontmatter(fm))
        parts.append("")
    parts.append(body.rstrip())
    parts.append("")
    path.write_text("\n".join(parts), encoding="utf-8")


def write_with_preserved_block(path: pathlib.Path, fm: dict | None,
                                body: str, marker: str = "<!-- preserve-from-here -->") -> None:
    """Schreibt MD-File, behaelt alles nach dem `marker` aus dem existierenden File.

    Use-Case: Ticker-Master-Files (z.B. AAPL.md) — der nova-Block oben wird
    regeneriert, Stefans eigene Notes unter dem Marker bleiben.
    """
    preserved = ""
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        idx = existing.find(marker)
        if idx >= 0:
            preserved = existing[idx:]
    new_body = body.rstrip() + "\n\n" + marker + "\n"
    if preserved:
        # marker ist Teil von preserved, also nicht zweimal anhaengen
        new_body = body.rstrip() + "\n\n" + preserved
    write_doc(path, fm, new_body)


# ---------- CSV-to-MD ----------

# Spalten die zu Wiki-Links transformiert werden sollen.
_LINKABLE_SYMBOL_COLS = {"symbol"}
_LINKABLE_REF_ID_COLS = {"ref_instrument_id"}


def csv_to_md_doc(
    csv_path: pathlib.Path,
    *,
    ref_id_to_symbol: Optional[dict[str, str]] = None,
    max_rows: int = 200,
    title: Optional[str] = None,
    extra_frontmatter: Optional[dict] = None,
) -> tuple[dict, str]:
    """Liest CSV, produziert (frontmatter_dict, body_markdown).

    Wiki-Link-Enrichment:
      - Spalte 'symbol' -> [[VALUE]]
      - Spalte 'ref_instrument_id' -> [[symbol_aus_lookup]] (oder raw value wenn lookup leer)

    Row-Cap: max_rows. Mit Footer-Hinweis wenn truncated.

    Numeric-Detection: best-effort — wenn alle Werte einer Spalte float-parseable
    sind, wird die Spalte rechts-aligned.
    """
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV nicht gefunden: {csv_path}")

    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        body = "*Empty CSV.*\n"
        fm = {
            "title":      title or csv_path.stem,
            "type":       "csv_export",
            "source_csv": csv_path.name,
            "n_rows":     0,
        }
        if extra_frontmatter:
            fm.update(extra_frontmatter)
        return fm, body

    headers = rows[0]
    data_rows = rows[1:]
    n_total = len(data_rows)
    truncated = n_total > max_rows
    if truncated:
        data_rows = data_rows[:max_rows]

    # Column-index lookup
    idx_symbol_cols   = [i for i, h in enumerate(headers) if h.strip().lower() in _LINKABLE_SYMBOL_COLS]
    idx_ref_id_cols   = [i for i, h in enumerate(headers) if h.strip().lower() in _LINKABLE_REF_ID_COLS]

    # Numeric-Detection: parse Spalten zu floats wo moeglich
    numeric_mask = [True] * len(headers)
    for col_idx in range(len(headers)):
        if col_idx in idx_symbol_cols or col_idx in idx_ref_id_cols:
            numeric_mask[col_idx] = False
            continue
        for r in data_rows:
            if col_idx >= len(r):
                continue
            v = r[col_idx]
            if v == "" or v is None:
                continue
            try:
                float(v)
            except (ValueError, TypeError):
                numeric_mask[col_idx] = False
                break

    align = ["r" if numeric_mask[i] else "l" for i in range(len(headers))]

    # Transformiere Werte: parse numeric where applicable, wrap symbol/ref-id columns
    transformed: list[list[Any]] = []
    for r in data_rows:
        # Pad to header count
        padded = list(r) + [""] * (len(headers) - len(r))
        out_row: list[Any] = []
        for i, val in enumerate(padded[:len(headers)]):
            if i in idx_symbol_cols:
                s = (val or "").strip()
                out_row.append(ticker_link(s) if s else "—")
            elif i in idx_ref_id_cols:
                s = (val or "").strip()
                if ref_id_to_symbol and s in ref_id_to_symbol:
                    out_row.append(ticker_link(ref_id_to_symbol[s], label=s))
                else:
                    out_row.append(s or "—")
            elif numeric_mask[i]:
                if val == "" or val is None:
                    out_row.append(None)
                else:
                    try:
                        out_row.append(float(val))
                    except (ValueError, TypeError):
                        out_row.append(val)
            else:
                out_row.append(val if val != "" else "—")
        transformed.append(out_row)

    table = md_table(headers, transformed, align=align)

    fm = {
        "title":      title or csv_path.stem,
        "type":       "csv_export",
        "source_csv": csv_path.name,
        "n_rows":     n_total,
        "n_shown":    len(data_rows),
        "n_columns":  len(headers),
    }
    if extra_frontmatter:
        fm.update(extra_frontmatter)

    body_parts = [f"# {title or csv_path.stem}", "",
                   f"*Quelle: `{csv_path.name}`* ({n_total} rows × {len(headers)} cols)", ""]
    if truncated:
        body_parts.append(f"> Zeigt die ersten {max_rows} von {n_total} Zeilen. "
                           f"Volltext im CSV unter `~/nova_output/`.\n")
    body_parts.append(table)
    body_parts.append("")
    return fm, "\n".join(body_parts)


def safe_filename(s: str, max_len: int = 60) -> str:
    """Filename-tauglicher Stub. Behaelt nur safe-chars."""
    out = []
    for c in s:
        if c.isalnum() or c in "-_.":
            out.append(c)
        elif c in " /\\:":
            out.append("_")
        # Punkte und Klammern bleiben raus
    result = "".join(out).strip("_.")
    return result[:max_len] or "untitled"
