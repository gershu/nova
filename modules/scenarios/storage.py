"""DB-Helpers fuer saved scenarios.

Layered architecture:
- engine.py — pure-Python Shock/CSPCandidate-Logik (testbar isoliert)
- storage.py — DuckDB-CRUD fuer ref_scenarios + ref_scenario_shocks + sig_scenario_runs
- __main__.py — CLI orchestriert engine + storage
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import date, datetime, timezone

import duckdb

from .engine import Shock


SCHEMA_FILE = pathlib.Path(__file__).parent / "sql" / "0001_scenarios.sql"


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Laedt scenarios-Schema. Andere Schemas (ref_instruments, pos_holdings,
    mkt_quotes_daily, mkt_fx_daily, list_*) werden vom apply_scenario engine
    bereits vorausgesetzt — durch ingest+portfolio+watchlist-Module."""
    con.execute(SCHEMA_FILE.read_text())


# ---------- CRUD: ref_scenarios + ref_scenario_shocks ----------

def save_scenario(
    con: duckdb.DuckDBPyConnection,
    scenario_id: str,
    name: str,
    shocks: list[Shock],
    description: str | None = None,
    base_currency: str = "EUR",
    tags: str | None = None,
    overwrite: bool = False,
) -> bool:
    """Speichert ein neues Scenario. Returnt True wenn neu, False bei overwrite.
    Raises ValueError wenn scenario_id existiert und overwrite=False."""
    existing = con.execute(
        "SELECT 1 FROM ref_scenarios WHERE scenario_id = ?", [scenario_id]
    ).fetchone()

    if existing and not overwrite:
        raise ValueError(
            f"scenario_id '{scenario_id}' existiert schon. "
            f"--overwrite zum Ersetzen oder anderes ID waehlen."
        )

    if existing:
        # Voll-Sync: alte Shocks weg, neue rein, ref_scenarios updaten
        con.execute("DELETE FROM ref_scenario_shocks WHERE scenario_id = ?", [scenario_id])
        con.execute(
            """UPDATE ref_scenarios
               SET name=?, description=?, base_currency=?, tags=?, updated_at=current_timestamp
               WHERE scenario_id = ?""",
            [name, description, base_currency, tags, scenario_id],
        )
    else:
        con.execute(
            """INSERT INTO ref_scenarios
               (scenario_id, name, description, base_currency, tags)
               VALUES (?, ?, ?, ?, ?)""",
            [scenario_id, name, description, base_currency, tags],
        )

    for ix, sh in enumerate(shocks):
        con.execute(
            """INSERT INTO ref_scenario_shocks
               (scenario_id, shock_idx, target, target_value, pct_change)
               VALUES (?, ?, ?, ?, ?)""",
            [scenario_id, ix, sh.target, sh.value, sh.pct],
        )

    return not existing


def load_scenario(
    con: duckdb.DuckDBPyConnection,
    scenario_id: str,
) -> tuple[dict, list[Shock]] | None:
    """Returnt (meta, shocks) oder None wenn nicht gefunden."""
    meta_row = con.execute(
        """SELECT scenario_id, name, description, base_currency, tags, active, created_at, updated_at
           FROM ref_scenarios WHERE scenario_id = ?""",
        [scenario_id],
    ).fetchone()
    if not meta_row:
        return None

    meta = {
        "scenario_id":   meta_row[0],
        "name":          meta_row[1],
        "description":   meta_row[2],
        "base_currency": meta_row[3],
        "tags":          meta_row[4],
        "active":        meta_row[5],
        "created_at":    meta_row[6],
        "updated_at":    meta_row[7],
    }

    shock_rows = con.execute(
        """SELECT shock_idx, target, target_value, pct_change
           FROM ref_scenario_shocks WHERE scenario_id = ?
           ORDER BY shock_idx""",
        [scenario_id],
    ).fetchall()
    shocks = [Shock(target=r[1], value=r[2], pct=r[3]) for r in shock_rows]

    return meta, shocks


def list_scenarios(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Returns sorted list of scenarios + shock-counts + last-run-info."""
    rows = con.execute(
        """
        SELECT
            s.scenario_id, s.name, s.base_currency, s.tags, s.active,
            (SELECT COUNT(*) FROM ref_scenario_shocks sh WHERE sh.scenario_id = s.scenario_id) AS shock_count,
            (SELECT COUNT(*) FROM sig_scenario_runs r WHERE r.scenario_id = s.scenario_id) AS run_count,
            (SELECT MAX(ts) FROM sig_scenario_runs r WHERE r.scenario_id = s.scenario_id) AS last_run_ts,
            (SELECT delta_pct FROM sig_scenario_runs r WHERE r.scenario_id = s.scenario_id ORDER BY ts DESC LIMIT 1) AS last_delta_pct
        FROM ref_scenarios s
        ORDER BY s.scenario_id
        """
    ).fetchall()
    return [
        {
            "scenario_id":     r[0],
            "name":            r[1],
            "base_currency":   r[2],
            "tags":            r[3],
            "active":          r[4],
            "shock_count":     r[5],
            "run_count":       r[6],
            "last_run_ts":     r[7],
            "last_delta_pct":  r[8],
        }
        for r in rows
    ]


def delete_scenario(con: duckdb.DuckDBPyConnection, scenario_id: str, *, also_runs: bool = False) -> bool:
    """Returnt True wenn was geloescht wurde. Bei also_runs=True auch sig_scenario_runs
    fuer dieses Scenario (sonst bleibt run-history erhalten als historischer Audit)."""
    existed = con.execute("SELECT 1 FROM ref_scenarios WHERE scenario_id = ?", [scenario_id]).fetchone()
    if not existed:
        return False
    con.execute("DELETE FROM ref_scenario_shocks WHERE scenario_id = ?", [scenario_id])
    con.execute("DELETE FROM ref_scenarios WHERE scenario_id = ?", [scenario_id])
    if also_runs:
        con.execute("DELETE FROM sig_scenario_runs WHERE scenario_id = ?", [scenario_id])
    return True


# ---------- Run-history: sig_scenario_runs ----------

def persist_run(
    con: duckdb.DuckDBPyConnection,
    scenario_id: str,
    ts: date | None,
    base_currency: str,
    portfolio_total_before: float,
    portfolio_total_after: float,
    holdings_count: int,
    affected_count: int,
    nova_run_id: str | None = None,
) -> str:
    """Persistiert einen scenario-run. Returnt run_id."""
    run_id = str(uuid.uuid4())
    delta_abs = portfolio_total_after - portfolio_total_before
    delta_pct = (
        (portfolio_total_after / portfolio_total_before - 1) * 100
        if portfolio_total_before else 0.0
    )
    if ts is None:
        ts = date.today()
    con.execute(
        """INSERT INTO sig_scenario_runs
           (run_id, scenario_id, ts, base_currency,
            portfolio_total_before, portfolio_total_after, delta_abs, delta_pct,
            holdings_count, affected_count, nova_run_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            run_id, scenario_id, ts, base_currency,
            portfolio_total_before, portfolio_total_after, delta_abs, delta_pct,
            holdings_count, affected_count, nova_run_id, datetime.now(timezone.utc),
        ],
    )
    return run_id


def history(
    con: duckdb.DuckDBPyConnection,
    scenario_id: str,
    limit: int = 30,
) -> list[dict]:
    """Run-history sortiert nach ts desc, neueste zuerst."""
    rows = con.execute(
        """
        SELECT run_id, ts, base_currency,
               portfolio_total_before, portfolio_total_after,
               delta_abs, delta_pct,
               holdings_count, affected_count, created_at
        FROM sig_scenario_runs
        WHERE scenario_id = ?
        ORDER BY ts DESC, created_at DESC
        LIMIT ?
        """,
        [scenario_id, limit],
    ).fetchall()
    return [
        {
            "run_id":                   r[0],
            "ts":                       r[1],
            "base_currency":            r[2],
            "portfolio_total_before":   r[3],
            "portfolio_total_after":    r[4],
            "delta_abs":                r[5],
            "delta_pct":                r[6],
            "holdings_count":           r[7],
            "affected_count":           r[8],
            "created_at":               r[9],
        }
        for r in rows
    ]
