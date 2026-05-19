"""nova-lab digest (B-Phase): erzeugt taeglichen Markdown-Report.

Komponiert vier Sections aus der DuckDB:
  - Watchlist-Status (alle aktiven Instrumente)
  - Alerts heute (aus sig_alerts)
  - Top-Movers (Up/Down)
  - Volume-Auffaelligkeiten

Output:  ~/nova_output/lab_digest/digest_<YYYY-MM-DD>.md

Aufruf:
  Lokal:    python -m modules.digest
  Via nova: ~/nova/scripts/nova_run.sh    lab_digest nova-hub --params-file <p.json>
            ~/nova/scripts/nova_submit.sh lab_digest nova-hub --params-file <p.json>

Konfig (3-Tier):
  Tier 3 — JSON via NOVA_PARAMS_FILE:
    {
      "source":   "ib",                         // optional, default 'ib'
      "ts":       "2026-05-02",                 // optional, default = max(ts) in DB
      "watchlist":"active",                     // optional
      "ref_instrument_ids": ["IB:AAPL:USD"],    // optional explicit
      "sections": ["watchlist_status", "alerts", "top_movers", "volume_anomalies"],
      "top_movers_n": 3,                        // optional
      "volume_threshold": 1.5                   // optional
    }
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import date, datetime, timezone

import duckdb

from .sections import alert_context as sec_alert_ctx
from .sections import alerts as sec_alerts
from .sections import csp_picks as sec_csp
from .sections import portfolio_briefing as sec_briefing
from .sections import sell_candidates as sec_sell
from .sections import top_movers as sec_top
from .sections import volume_anomalies as sec_vol
from .sections import watchlist_status as sec_wl

# ---------- Konfiguration ----------
DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
OUTPUT_DIR = pathlib.Path.home() / "nova_output" / "lab_digest"

ALL_SECTIONS = ["portfolio_briefing", "watchlist_status", "alerts", "alert_context", "top_movers", "volume_anomalies", "csp_picks", "sell_candidates"]


def load_params() -> dict:
    pf = os.environ.get("NOVA_PARAMS_FILE")
    if not pf:
        return {}
    p = pathlib.Path(pf)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        print(f"[WARN] params file ist kein gueltiges JSON: {e}", file=sys.stderr)
        return {}


def resolve_instrument_ids(con: duckdb.DuckDBPyConnection, params: dict) -> list[str]:
    """Liefert Liste von ref_instrument_ids fuer das Reporting-Universum."""
    if params.get("ref_instrument_ids"):
        return list(params["ref_instrument_ids"])
    if params.get("symbols"):
        syms = list(params["symbols"])
        placeholders = ",".join(["?"] * len(syms))
        rows = con.execute(
            f"SELECT ref_instrument_id FROM ref_instruments "
            f"WHERE symbol IN ({placeholders}) ORDER BY ref_instrument_id",
            syms,
        ).fetchall()
        return [r[0] for r in rows]
    watchlist = params.get("watchlist", "active")
    if watchlist == "active":
        rows = con.execute(
            "SELECT ref_instrument_id FROM ref_instruments WHERE active = true ORDER BY ref_instrument_id"
        ).fetchall()
        return [r[0] for r in rows]
    raise ValueError(f"Unbekannte watchlist '{watchlist}'.")


def resolve_ts(con: duckdb.DuckDBPyConnection, params: dict, source: str) -> date:
    if params.get("ts"):
        return date.fromisoformat(params["ts"])
    row = con.execute("SELECT MAX(ts) FROM mkt_quotes_daily WHERE source = ?", [source]).fetchone()
    if not row or not row[0]:
        raise ValueError("Keine Quotes in DB — ingest zuerst.")
    return row[0]


def main() -> int:
    params = load_params()
    source = params.get("source", "ib")
    sections = params.get("sections", ALL_SECTIONS)
    top_n = int(params.get("top_movers_n", 3))
    vol_thresh = float(params.get("volume_threshold", 1.5))

    run_id = os.environ.get(
        "NOVA_JOB_ID",
        f"adhoc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    )

    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        print(f"       digest braucht eine populated DB — erst ingest laufen lassen.", file=sys.stderr)
        return 64

    # digest ist pure read + schreibt nur MD-File — read_only erlaubt
    # parallele Schreiber (z.B. wenn Daemon-Sequence overlappt).
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        try:
            ref_ids = resolve_instrument_ids(con, params)
            ts = resolve_ts(con, params, source)
        except ValueError as e:
            print(f"FEHLER: {e}", file=sys.stderr)
            return 64

        print("==> nova-lab digest (B-Phase)")
        print(f"    source       : {source}")
        print(f"    ts           : {ts}")
        print(f"    instruments  : {len(ref_ids)}")
        print(f"    sections     : {', '.join(sections)}")
        print(f"    db           : {DB_PATH}")

        parts: list[str] = [
            f"# nova-lab Daily Digest — {ts.isoformat()}",
            f"_Generiert {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC, run_id `{run_id}`._",
        ]

        if "portfolio_briefing" in sections:
            briefing = sec_briefing.render(con, ts)
            if briefing:
                parts.append(briefing)
        if "watchlist_status" in sections:
            parts.append(sec_wl.render(con, source, ts, ref_ids))
        if "alerts" in sections:
            parts.append(sec_alerts.render(con, ts))
        if "alert_context" in sections:
            ctx = sec_alert_ctx.render(con, ts, min_confidence=float(params.get("alert_ctx_min_confidence", 0.0)))
            if ctx:
                parts.append(ctx)
        if "top_movers" in sections:
            parts.append(sec_top.render(con, source, ts, n=top_n))
        if "volume_anomalies" in sections:
            parts.append(sec_vol.render(con, source, ts, threshold=vol_thresh))
        if "csp_picks" in sections:
            csp = sec_csp.render(con, max_show=int(params.get("csp_max_show", 15)))
            if csp:
                parts.append(csp)
        if "sell_candidates" in sections:
            sc = sec_sell.render(con, ts)
            if sc:
                parts.append(sc)

        parts.append("---")
        parts.append(f"_Instrumente: {len(ref_ids)} aktiv_  |  _Source: `{source}`_  |  _Datum: {ts.isoformat()}_")

        digest_md = "\n\n".join(parts) + "\n"

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_file = OUTPUT_DIR / f"digest_{ts.isoformat()}.md"
        out_file.write_text(digest_md)

        print()
        print(f"==> done: {out_file}")
        print(f"    bytes: {len(digest_md):,}")

    finally:
        con.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
