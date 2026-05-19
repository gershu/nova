"""Publisher — liest DuckDB-Tabellen + schreibt Obsidian-MD-Files.

Read-only auf nova-lab DB. Output-Pfad:
  Default: ~/nova_output/obsidian/
  Override via OBSIDIAN_VAULT_PATH env-var.

Vault-Layout:
    nova-lab/
    ├── _INDEX.md                          MOC — Links zu allen aktuellen Files
    ├── daily/
    │   ├── 2026-05-11.md                  Daily-Summary (Digest-Inhalt + Quick-Links)
    │   └── ...
    ├── csp/
    │   ├── 2026-05-11-csp-picks.md        Tages-Snapshot
    │   └── ...
    ├── value/
    │   ├── 2026-05-11-value-picks.md      Wochen-Snapshot (Sonntag-Run)
    │   └── ...
    ├── holdings/
    │   └── current.md                      Aktueller Portfolio-Stand
    ├── fundamentals/
    │   └── snapshot.md                     Universe-weite Latest-Fundamentals-Tabelle
    └── instruments/
        ├── AAPL.md                          Ticker-Master mit preserve-block
        ├── MSFT.md
        └── ...

Designed um idempotent zu sein: erneuter Run ueberschreibt aktuelle Files,
preserve-block in instruments/ behaelt Stefans eigene Notes.
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import duckdb

from .exporter import (
    csv_to_md_doc, frontmatter, md_table, section, ticker_link, write_doc,
    write_with_preserved_block, safe_filename,
)


# Root des nova-output-Layers (auf nova-hub). Hier liegen die CSVs.
NOVA_OUTPUT_ROOT = pathlib.Path.home() / "nova_output"


DEFAULT_VAULT = pathlib.Path.home() / "nova_output" / "obsidian"


@dataclass
class PublishStats:
    files_written: int = 0
    sections:      list[str] = None

    def __post_init__(self):
        self.sections = self.sections or []


# ---------- Helpers ----------

def _vault_path() -> pathlib.Path:
    p = os.environ.get("OBSIDIAN_VAULT_PATH")
    return pathlib.Path(p) if p else DEFAULT_VAULT


def _ref_symbol_map(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """ref_instrument_id -> symbol Lookup."""
    rows = con.execute("SELECT ref_instrument_id, symbol FROM ref_instruments").fetchall()
    return {r[0]: r[1] for r in rows}


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    rows = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()
    return rows is not None


# ---------- Sub-Publishers ----------

def publish_holdings(con: duckdb.DuckDBPyConnection, vault: pathlib.Path,
                      sym_map: dict[str, str]) -> Optional[pathlib.Path]:
    if not _table_exists(con, "pos_holdings"):
        return None
    rows = con.execute("""
        SELECT h.ref_instrument_id, i.symbol, i.name, i.asset_type, i.currency,
               SUM(h.quantity) AS qty, AVG(h.cost_per_share) AS avg_cost
        FROM pos_holdings h
        LEFT JOIN ref_instruments i USING (ref_instrument_id)
        GROUP BY h.ref_instrument_id, i.symbol, i.name, i.asset_type, i.currency
        ORDER BY i.symbol
    """).fetchall()
    if not rows:
        return None

    table_rows = [
        [ticker_link(r[1] or "?"), r[2] or "?", r[3] or "?", r[4] or "?",
         r[5], r[6]]
        for r in rows
    ]
    body = section("Holdings",
                   md_table(["Symbol", "Name", "Type", "CCY", "Qty", "Avg Cost"],
                            table_rows,
                            align=["l", "l", "l", "l", "r", "r"]))
    out_path = vault / "holdings" / "current.md"
    write_doc(out_path, {
        "title":         "Current Holdings",
        "type":          "portfolio",
        "last_updated":  date.today(),
        "n_positions":   len(rows),
        "tags":          ["portfolio", "holdings"],
    }, body)
    return out_path


def publish_csp_picks(con: duckdb.DuckDBPyConnection, vault: pathlib.Path,
                      sym_map: dict[str, str]) -> Optional[pathlib.Path]:
    """CSP-Picks aus list_watchlist_members 'system_recommendations'."""
    if not _table_exists(con, "list_watchlist_members"):
        return None
    rows = con.execute("""
        SELECT m.ref_instrument_id, i.symbol, i.name, m.notes, m.added_at
        FROM list_watchlist_members m
        LEFT JOIN ref_instruments i USING (ref_instrument_id)
        WHERE m.watchlist_id = 'system_recommendations'
          AND m.added_by = 'screener_csp'
        ORDER BY m.added_at DESC
    """).fetchall()
    if not rows:
        return None

    today = date.today()
    table_rows = [
        [ticker_link(r[1] or "?"), r[2] or "?", r[3] or "—"]
        for r in rows
    ]
    body = section(f"CSP-Picks ({today.isoformat()})",
                   md_table(["Symbol", "Name", "Notes (strike / yield / buffer / earnings / conviction)"],
                            table_rows,
                            align=["l", "l", "l"]))
    out_path = vault / "csp" / f"{today.isoformat()}-csp-picks.md"
    write_doc(out_path, {
        "title":        f"CSP-Picks {today.isoformat()}",
        "type":         "csp_picks",
        "run_date":     today,
        "n_picks":      len(rows),
        "tags":         ["csp", "screener", today.isoformat()],
    }, body)
    return out_path


def publish_value_picks(con: duckdb.DuckDBPyConnection, vault: pathlib.Path,
                        sym_map: dict[str, str]) -> Optional[pathlib.Path]:
    """Value-Picks aus sig_value_picks (letzter run)."""
    if not _table_exists(con, "sig_value_picks"):
        return None
    latest = con.execute("SELECT MAX(ts) FROM sig_value_picks").fetchone()
    if not latest or not latest[0]:
        return None
    latest_ts = latest[0]
    rows = con.execute("""
        SELECT p.rank, p.ref_instrument_id, i.symbol, i.name,
               p.composite_score, p.conviction_score, p.notes
        FROM sig_value_picks p
        LEFT JOIN ref_instruments i USING (ref_instrument_id)
        WHERE p.ts = ?
        ORDER BY p.rank
    """, [latest_ts]).fetchall()
    if not rows:
        return None

    table_rows = [
        [r[0], ticker_link(r[2] or "?"), r[3] or "?", r[4], r[5], r[6] or "—"]
        for r in rows
    ]
    body = section(f"Value-Picks ({latest_ts})",
                   md_table(["Rank", "Symbol", "Name", "Composite", "Conviction", "Notes"],
                            table_rows,
                            align=["r", "l", "l", "r", "r", "l"]))
    out_path = vault / "value" / f"{latest_ts.isoformat()}-value-picks.md"
    write_doc(out_path, {
        "title":     f"Value-Picks {latest_ts.isoformat()}",
        "type":      "value_picks",
        "run_date":  latest_ts,
        "n_picks":   len(rows),
        "tags":      ["value", "screener", "weekly", latest_ts.isoformat()],
    }, body)
    return out_path


def publish_fundamentals_snapshot(con: duckdb.DuckDBPyConnection, vault: pathlib.Path,
                                    sym_map: dict[str, str]) -> Optional[pathlib.Path]:
    """Latest Fundamentals fuer alle Universe-Member (ohne Sector/Industry)."""
    if not _table_exists(con, "ref_fundamentals_snapshot"):
        return None
    rows = con.execute("""
        SELECT f.ref_instrument_id, i.symbol,
               f.market_cap, f.pe_ttm, f.roe, f.fcf_yield,
               f.debt_to_equity, f.operating_margin, f.revenue_cagr_5y,
               f.ts
        FROM ref_fundamentals_latest f
        LEFT JOIN ref_instruments i USING (ref_instrument_id)
        ORDER BY f.market_cap DESC NULLS LAST
    """).fetchall()
    if not rows:
        return None

    table_rows = [
        [ticker_link(r[1] or "?"), r[2], r[3], r[4], r[5], r[6], r[7], r[8]]
        for r in rows
    ]
    body = section("Latest Fundamentals (Universe)",
                   md_table(
                       ["Symbol", "MCap", "PE", "ROE", "FCFY", "D/E", "OpMargin", "Rev5y"],
                       table_rows,
                       align=["l", "r", "r", "r", "r", "r", "r", "r"]))
    body += "\n*Daten via yfinance — weekly refresh. Pct-Werte sind decimals (0.35 = 35%).*\n"
    out_path = vault / "fundamentals" / "snapshot.md"
    write_doc(out_path, {
        "title":         "Fundamentals Snapshot",
        "type":          "fundamentals",
        "last_updated":  date.today(),
        "n_symbols":     len(rows),
        "tags":          ["fundamentals", "snapshot"],
    }, body)
    return out_path


def publish_daily_digest(con: duckdb.DuckDBPyConnection, vault: pathlib.Path,
                          sym_map: dict[str, str]) -> Optional[pathlib.Path]:
    """Aktuellster Tages-Digest als nova-lab-Daily-File.

    Versucht den existierenden digest-Output (~/nova_output/lab_digest/*.md)
    zu finden und in den Vault zu spiegeln. Falls Digest-Output anders strukturiert,
    fallback: minimaler Daily-Index mit Links zu csp/value/holdings.
    """
    today = date.today()
    out_path = vault / "daily" / f"{today.isoformat()}.md"

    digest_dir = pathlib.Path.home() / "nova_output" / "lab_digest"
    digest_text: Optional[str] = None
    if digest_dir.is_dir():
        # Latest digest-MD
        mds = sorted(digest_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if mds:
            digest_text = mds[0].read_text(encoding="utf-8")

    body_parts: list[str] = []
    if digest_text:
        # Strip eventuelles existing-frontmatter aus digest (wir setzen unseres)
        if digest_text.startswith("---"):
            end = digest_text.find("---", 3)
            if end > 0:
                digest_text = digest_text[end + 3:].lstrip()
        body_parts.append(digest_text.rstrip())

    body_parts.append("\n## Quick Links\n")
    body_parts.append(f"- [[{today.isoformat()}-csp-picks|CSP Picks heute]]")
    body_parts.append(f"- [[holdings/current|Current Holdings]]")
    body_parts.append(f"- [[fundamentals/snapshot|Fundamentals Snapshot]]")
    body_parts.append(f"- [[_INDEX|MOC]]")

    write_doc(out_path, {
        "title":     f"Daily {today.isoformat()}",
        "type":      "daily",
        "date":      today,
        "tags":      ["daily", today.isoformat()],
    }, "\n".join(body_parts))
    return out_path


def publish_instrument_files(con: duckdb.DuckDBPyConnection, vault: pathlib.Path,
                              sym_map: dict[str, str]) -> int:
    """Per-Ticker Master-File mit preserve-block fuer Stefans Notes.

    Universe: Holdings + value_picks + csp_picks (system_recommendations).
    """
    target_ids: set[str] = set()
    # Holdings
    if _table_exists(con, "pos_holdings"):
        target_ids.update(
            r[0] for r in con.execute("SELECT DISTINCT ref_instrument_id FROM pos_holdings").fetchall()
        )
    # Value-Picks (letzter run)
    if _table_exists(con, "sig_value_picks"):
        latest = con.execute("SELECT MAX(ts) FROM sig_value_picks").fetchone()
        if latest and latest[0]:
            target_ids.update(
                r[0] for r in con.execute(
                    "SELECT ref_instrument_id FROM sig_value_picks WHERE ts = ?", [latest[0]]
                ).fetchall()
            )
    # System-Recommendations (CSP)
    if _table_exists(con, "list_watchlist_members"):
        target_ids.update(
            r[0] for r in con.execute(
                "SELECT ref_instrument_id FROM list_watchlist_members "
                "WHERE watchlist_id = 'system_recommendations'"
            ).fetchall()
        )

    if not target_ids:
        return 0

    n = 0
    for rid in sorted(target_ids):
        symbol = sym_map.get(rid)
        if not symbol:
            continue
        path = vault / "instruments" / f"{symbol}.md"
        body = _build_instrument_body(con, rid, symbol)
        # Fundamentals-aware Frontmatter
        fm = _build_instrument_frontmatter(con, rid, symbol)
        write_with_preserved_block(path, fm, body)
        n += 1
    return n


def _build_instrument_frontmatter(con, rid: str, symbol: str) -> dict:
    fm = {
        "ticker":             symbol,
        "ref_instrument_id":  rid,
        "last_updated":       date.today(),
    }
    # Market-Cap aus fundamentals (Sector/Industry/Country wurden gedroppt).
    if _table_exists(con, "ref_fundamentals_snapshot"):
        row = con.execute("""
            SELECT market_cap
            FROM ref_fundamentals_latest WHERE ref_instrument_id = ?
        """, [rid]).fetchone()
        if row:
            fm["market_cap"] = row[0]

    tags = ["instrument"]
    if _table_exists(con, "pos_holdings"):
        h = con.execute("SELECT 1 FROM pos_holdings WHERE ref_instrument_id = ? LIMIT 1", [rid]).fetchone()
        if h:
            tags.append("holding")

    # Portfolio-Views als Obsidian-Tag-Hierarchie (view/core, view/sell_candidates).
    # Member-Identitaet seit SCD-2: (ref_instrument_id, broker). Tag wird gesetzt
    # wenn das Instrument bei IRGENDEINEM Broker in der View ist.
    if _table_exists(con, "list_portfolio_view_members"):
        view_rows = con.execute("""
            SELECT DISTINCT view_id
            FROM list_portfolio_view_members
            WHERE ref_instrument_id = ?
        """, [rid]).fetchall()
        for (vid,) in view_rows:
            tags.append(f"view/{vid}")

    fm["tags"] = tags
    return fm


def _build_instrument_body(con, rid: str, symbol: str) -> str:
    parts: list[str] = [f"# {symbol}"]

    # Latest Spot
    if _table_exists(con, "mkt_quotes_daily"):
        spot = con.execute("""
            WITH ranked AS (
                SELECT close, ts, source,
                       ROW_NUMBER() OVER (ORDER BY ts DESC,
                                          CASE source WHEN 'ib' THEN 1
                                                      WHEN 'yfinance' THEN 2 ELSE 9 END) AS rk
                FROM mkt_quotes_daily WHERE ref_instrument_id = ?
            )
            SELECT close, ts, source FROM ranked WHERE rk = 1
        """, [rid]).fetchone()
        if spot:
            parts.append(f"\n**Spot:** {spot[0]:.2f}   *(via {spot[2]}, {spot[1]})*\n")

    # Fundamentals-Table
    if _table_exists(con, "ref_fundamentals_snapshot"):
        f = con.execute("""
            SELECT pe_ttm, pb, fcf_yield, dividend_yield, roe, roic,
                   operating_margin, debt_to_equity, revenue_cagr_5y, ts
            FROM ref_fundamentals_latest WHERE ref_instrument_id = ?
        """, [rid]).fetchone()
        if f:
            rows = [
                ["P/E TTM",            f[0]],
                ["P/B",                f[1]],
                ["FCF Yield",          f[2]],
                ["Dividend Yield",     f[3]],
                ["ROE",                f[4]],
                ["ROIC",               f[5]],
                ["Operating Margin",   f[6]],
                ["Debt/Equity",        f[7]],
                ["Revenue CAGR 5y",    f[8]],
            ]
            parts.append(section("Fundamentals (Latest)",
                                  md_table(["Metric", "Value"], rows, align=["l", "r"])))
            parts.append(f"*Source: yfinance, ts={f[9]}*\n")

    # CSP-Picks history (last 30 days)
    if _table_exists(con, "list_watchlist_members"):
        # Vereinfacht — wir nehmen den system_recommendations Eintrag wenn vorhanden
        recs = con.execute("""
            SELECT notes, added_at FROM list_watchlist_members
            WHERE watchlist_id = 'system_recommendations' AND ref_instrument_id = ?
            ORDER BY added_at DESC LIMIT 5
        """, [rid]).fetchall()
        if recs:
            csp_rows = [[r[1].date() if hasattr(r[1], "date") else r[1], r[0]] for r in recs]
            parts.append(section("Recent CSP Picks",
                                  md_table(["Date", "Notes"], csp_rows, align=["l", "l"])))

    # Value-Picks history
    if _table_exists(con, "sig_value_picks"):
        vp = con.execute("""
            SELECT ts, rank, composite_score, notes FROM sig_value_picks
            WHERE ref_instrument_id = ?
            ORDER BY ts DESC LIMIT 5
        """, [rid]).fetchall()
        if vp:
            rows_vp = [[r[0], r[1], r[2], r[3] or "—"] for r in vp]
            parts.append(section("Recent Value-Picks",
                                  md_table(["Date", "Rank", "Composite", "Notes"], rows_vp,
                                            align=["l", "r", "r", "l"])))

    # Recent alerts
    if _table_exists(con, "sig_alerts"):
        al = con.execute("""
            SELECT ts, rule_name, direction, trigger_value, threshold
            FROM sig_alerts WHERE ref_instrument_id = ?
            ORDER BY ts DESC LIMIT 5
        """, [rid]).fetchall()
        if al:
            rows_al = [[r[0], r[1], r[2] or "—", r[3], r[4]] for r in al]
            parts.append(section("Recent Alerts",
                                  md_table(["Date", "Rule", "Dir", "Trigger", "Threshold"],
                                            rows_al,
                                            align=["l", "l", "l", "r", "r"])))

    # SA-Articles
    if _table_exists(con, "ref_sa_articles"):
        sa = con.execute("""
            SELECT a.ts, a.title, a.url
            FROM ref_sa_articles a
            JOIN ref_sa_article_symbols s ON s.article_id = a.article_id
            WHERE s.ref_instrument_id = ?
            ORDER BY a.ts DESC LIMIT 8
        """, [rid]).fetchall()
        if sa:
            rows_sa = [[r[0], (f"[{r[1]}]({r[2]})" if r[2] else r[1])] for r in sa]
            parts.append(section("Seeking Alpha Coverage",
                                  md_table(["Date", "Title"], rows_sa, align=["l", "l"])))

    # LLM-Briefings
    if _table_exists(con, "sig_value_briefings"):
        b = con.execute("""
            SELECT ts, model, summary FROM sig_value_briefings
            WHERE ref_instrument_id = ?
            ORDER BY ts DESC LIMIT 1
        """, [rid]).fetchone()
        if b:
            parts.append(section("LLM Brief",
                                  f"*Model: {b[1]}, ts: {b[0]}*\n\n{b[2] or '(no summary)'}\n"))

    return "\n".join(parts)


# publish_canonicals: ENTFERNT (Canonical-Layer wurde aus dem Schema gedroppt).
# Wenn Multi-Class-Aggregation wieder gebraucht wird, hier neu implementieren.


def publish_portfolio_views(con: duckdb.DuckDBPyConnection, vault: pathlib.Path,
                              sym_map: dict[str, str]) -> int:
    """Pro Portfolio-Sicht ein views/<view_id>.md mit Member-Tabelle.

    Member-Identitaet seit SCD-2: (ref_instrument_id, broker). Lot-Konzept
    entfaellt. Members ohne aktuellen Bestand (= position komplett verkauft)
    erscheinen mit qty = 0 und werden als 'stale' gezaehlt.
    """
    if not _table_exists(con, "list_portfolio_views"):
        return 0

    views = con.execute("""
        SELECT view_id, name, description, origin, color
        FROM list_portfolio_views WHERE active = TRUE
        ORDER BY view_id
    """).fetchall()
    if not views:
        return 0

    n_written = 0
    for vid, name, desc, origin, color in views:
        # Members: join auf v_pos_holdings (current state) + ref_instruments
        members = con.execute("""
            SELECT m.ref_instrument_id, m.broker, i.symbol, i.name, i.currency,
                   COALESCE(p.quantity, 0)        AS qty,
                   p.cost_per_share,
                   m.notes
            FROM list_portfolio_view_members m
            LEFT JOIN v_pos_holdings p
                   ON p.ref_instrument_id = m.ref_instrument_id
                  AND p.broker            = m.broker
            LEFT JOIN ref_instruments i ON i.ref_instrument_id = m.ref_instrument_id
            WHERE m.view_id = ?
            ORDER BY i.symbol NULLS LAST, m.broker
        """, [vid]).fetchall()

        # Latest spot pro instrument fuer member-Tabelle
        spot_map: dict[str, float] = {}
        if _table_exists(con, "mkt_quotes_daily") and members:
            ids = list({m[0] for m in members if m[0] is not None})
            placeholders = ",".join(["?"] * len(ids))
            spots = con.execute(f"""
                WITH ranked AS (
                    SELECT ref_instrument_id, close, ts, source,
                           ROW_NUMBER() OVER (PARTITION BY ref_instrument_id
                                              ORDER BY ts DESC,
                                                       CASE source WHEN 'ib' THEN 1
                                                                   WHEN 'yfinance' THEN 2 ELSE 9 END) AS rk
                    FROM mkt_quotes_daily WHERE ref_instrument_id IN ({placeholders})
                )
                SELECT ref_instrument_id, close FROM ranked WHERE rk = 1
            """, ids).fetchall()
            spot_map = {r[0]: r[1] for r in spots}

        # Members-Tuple: (ref_id, broker, sym, full_name, ccy, qty, cost, notes)
        valid_members = [m for m in members if m[5] > 0]
        held = len(valid_members)
        stale = len(members) - held
        if not members:
            body = f"# {name}\n\n*Keine Members in dieser Sicht.*\n"
        else:
            rows = []
            for ref_id, broker, sym, full_name, ccy, qty, cost, notes in members:
                spot = spot_map.get(ref_id) if ref_id else None
                mv = qty * spot if (qty and spot) else None
                rows.append([
                    ticker_link(sym) if sym else "(stale)",
                    broker or "?",
                    full_name or "?",
                    ccy or "?",
                    qty if qty else None,
                    spot,
                    mv,
                    notes or "—",
                ])
            body = f"# {name}\n\n"
            if desc:
                body += f"*{desc}*\n\n"
            body += md_table(
                ["Symbol", "Broker", "Name", "CCY", "Qty", "Spot", "MV", "Notes"],
                rows, align=["l", "l", "l", "l", "r", "r", "r", "l"])
            body += f"\n\n*{held}/{len(members)} Members aktuell im Portfolio.*"
            if stale > 0:
                body += f"  \n*{stale} stale Member(s) — Position komplett verkauft oder Member-Eintrag obsolet.*\n"
            else:
                body += "\n"

        fm = {
            "title":         name,
            "type":          "portfolio_view",
            "view_id":       vid,
            "origin":        origin,
            "color":         color,
            "n_members":     len(members),
            "n_held":        held,
            "last_updated":  date.today(),
            "tags":          ["portfolio_view", f"view/{vid}"],
        }
        out_path = vault / "views" / f"{vid}.md"
        write_doc(out_path, fm, body)
        n_written += 1
    return n_written


def publish_workload_csvs(
    con: duckdb.DuckDBPyConnection,
    vault: pathlib.Path,
    sym_map: dict[str, str],
    *,
    output_root: pathlib.Path | None = None,
    max_rows_per_csv: int = 200,
    keep_per_workload: int = 5,
) -> int:
    """Walks ~/nova_output/lab_*/ for CSVs, renders each als MD in vault/<workload>/full/.

    Strategie:
      - Pro lab_<workload>-Folder die `keep_per_workload` neuesten CSVs nehmen
        (sortiert nach mtime). Aelte CSVs ignorieren — vault wird nicht aufgeblaeht.
      - Schreibt nach vault/<workload>/full/<csv-stem>.md
      - Symbol/ref_instrument_id-Spalten werden zu Wiki-Links

    Args:
        output_root: Default ~/nova_output. Override fuer Tests.
        max_rows_per_csv: Cap pro File (Obsidian-friendliness).
        keep_per_workload: nur die juengsten N CSVs pro workload publizieren.

    Returns: Anzahl geschriebener Files.
    """
    root = output_root or NOVA_OUTPUT_ROOT
    if not root.is_dir():
        return 0

    n_written = 0
    # Iteriere lab_*-Folder
    for workload_dir in sorted(root.glob("lab_*")):
        if not workload_dir.is_dir():
            continue
        workload = workload_dir.name
        # Skip obsidian itself (avoid self-recursion if vault is under nova_output)
        if workload.startswith("obsidian"):
            continue
        # Alle CSVs in diesem Folder, sortiert nach mtime desc
        csvs = sorted(workload_dir.glob("*.csv"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not csvs:
            continue
        for csv_path in csvs[:keep_per_workload]:
            try:
                fm, body = csv_to_md_doc(
                    csv_path,
                    ref_id_to_symbol=sym_map,
                    max_rows=max_rows_per_csv,
                    title=f"{workload} — {csv_path.stem}",
                    extra_frontmatter={
                        "workload": workload,
                        "tags": [workload.replace("lab_", ""), "csv_full"],
                    },
                )
                out_path = vault / workload.replace("lab_", "") / "full" / f"{csv_path.stem}.md"
                write_doc(out_path, fm, body)
                n_written += 1
            except Exception as e:  # noqa: BLE001
                # Defensiv: ein bricht-CSV soll andere nicht killen
                print(f"    WARN: {csv_path.name}: {e.__class__.__name__}: {e}")
    return n_written


def publish_index(vault: pathlib.Path) -> Optional[pathlib.Path]:
    """MOC (Map of Content): _INDEX.md verlinkt alle aktuellen Files."""
    today = date.today().isoformat()
    parts = [
        f"# nova-lab Vault Index",
        "",
        f"*Generated: {today}*",
        "",
        "## Daily",
        f"- [[daily/{today}|Heute]]",
        "",
        "## Holdings + Snapshots",
        "- [[holdings/current|Current Holdings]]",
        "- [[fundamentals/snapshot|Fundamentals Snapshot]]",
        "",
        "## Latest Picks",
        f"- [[csp/{today}-csp-picks|CSP Picks {today}]]",
        "- *Value-Picks*: siehe value/-Ordner (weekly)",
        "",
        "## Per-Instrument",
        "Siehe `instruments/` — pro Ticker eine Master-Datei mit",
        "Fundamentals, Pick-History, Alerts, SA-Coverage, LLM-Brief.",
        "Eigene Notes unter `<!-- preserve-from-here -->` werden NICHT ueberschrieben.",
        "",
        "## Tag-Cloud",
        "- #portfolio  — Holdings + Composition",
        "- #csp        — CSP-Picks",
        "- #value      — Value-Screener-Picks",
        "- #holding    — meine aktuellen Positionen",
        "- #instrument — Ticker-Master",
        "",
    ]
    out = vault / "_INDEX.md"
    write_doc(out, {"title": "nova-lab Index", "type": "moc",
                     "last_updated": date.today()},
              "\n".join(parts))
    return out


# ---------- Main ----------

def publish_all(con: duckdb.DuckDBPyConnection, vault: Optional[pathlib.Path] = None) -> PublishStats:
    """Orchestrator — alle Sub-Publishers nacheinander."""
    vault = vault or _vault_path()
    sym_map = _ref_symbol_map(con)

    stats = PublishStats()
    for name, fn in [
        ("holdings",      lambda: publish_holdings(con, vault, sym_map)),
        ("csp_picks",     lambda: publish_csp_picks(con, vault, sym_map)),
        ("value_picks",   lambda: publish_value_picks(con, vault, sym_map)),
        ("fundamentals",  lambda: publish_fundamentals_snapshot(con, vault, sym_map)),
        ("daily_digest",  lambda: publish_daily_digest(con, vault, sym_map)),
    ]:
        try:
            p = fn()
            if p:
                stats.sections.append(f"{name} -> {p.relative_to(vault)}")
                stats.files_written += 1
        except Exception as e:  # noqa: BLE001
            stats.sections.append(f"{name}: FAIL ({e.__class__.__name__}: {e})")

    # Instruments — multiple files
    try:
        n = publish_instrument_files(con, vault, sym_map)
        stats.sections.append(f"instruments -> {n} files")
        stats.files_written += n
    except Exception as e:  # noqa: BLE001
        stats.sections.append(f"instruments: FAIL ({e.__class__.__name__}: {e})")

    # Portfolio-Views — multiple files
    try:
        n = publish_portfolio_views(con, vault, sym_map)
        stats.sections.append(f"views -> {n} files")
        stats.files_written += n
    except Exception as e:  # noqa: BLE001
        stats.sections.append(f"views: FAIL ({e.__class__.__name__}: {e})")

    # CSV-Rendering aus ~/nova_output/lab_*/ — multiple files
    try:
        n = publish_workload_csvs(con, vault, sym_map)
        stats.sections.append(f"csv_full -> {n} files")
        stats.files_written += n
    except Exception as e:  # noqa: BLE001
        stats.sections.append(f"csv_full: FAIL ({e.__class__.__name__}: {e})")

    # Index (immer)
    try:
        publish_index(vault)
        stats.sections.append("_INDEX.md")
        stats.files_written += 1
    except Exception as e:  # noqa: BLE001
        stats.sections.append(f"index: FAIL ({e.__class__.__name__}: {e})")

    return stats
