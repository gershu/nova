"""nova — Decision-Journal Datenschicht.

Von CLI (modules.decision_journal) UND der Dashboard-Page geteilt — eine
einzige Stelle fuer Schema, Lese- und Schreib-Logik.

Connection-Strategie wie modules.dashboard.db: kurzlebige Connections.
Lese-Funktionen bekommen eine Connection vom Caller. Schreib-Funktionen
oeffnen eine read-write Connection NUR fuer die Dauer des INSERT/UPDATE und
schliessen sofort — so blockiert das Journal weder das Streamlit-Daemon
noch die naechtlichen Daemons dauerhaft.
"""

from __future__ import annotations

import json
import os
import pathlib
from contextlib import contextmanager
from datetime import date, timedelta

import duckdb
import pandas as pd


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
SQL_DIR = pathlib.Path(__file__).parent / "sql"

VALID_STATUS  = ("pending", "acted_full", "acted_partial", "declined", "expired")
VALID_OUTCOME = ("good", "neutral", "poor")

SUGGEST_WINDOW_DAYS = 30


class JournalError(RuntimeError):
    """Fachlicher Fehler (z.B. unbekannte Recommendation, DB gesperrt)."""


# ---------- Connections / Schema ----------

@contextmanager
def connect(read_only: bool = True):
    """Kurzlebige Connection. read_only=False nur fuer Schreibzugriffe."""
    if not DB_PATH.is_file():
        raise JournalError(f"DB nicht gefunden: {DB_PATH}")
    try:
        con = duckdb.connect(str(DB_PATH), read_only=read_only)
    except (duckdb.IOException, duckdb.Error) as e:
        raise JournalError(f"DB nicht erreichbar (evtl. gerade gesperrt): {e}")
    try:
        yield con
    finally:
        con.close()


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Schema idempotent applyen (CREATE TABLE IF NOT EXISTS)."""
    for f in sorted(SQL_DIR.glob("0*.sql")):
        con.execute(f.read_text())


def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone() is not None


# ---------- linked_trades JSON ----------

def serialize_trades(trades: list[dict] | None) -> str | None:
    """trades: Liste von {ref_instrument_id, broker, trade_lot}."""
    return json.dumps(trades) if trades else None


def parse_linked_trades(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ---------- Lesen (Caller stellt Connection) ----------

def latest_rec_ts(con: duckdb.DuckDBPyConnection) -> str | None:
    if not table_exists(con, "sig_recommendations"):
        return None
    row = con.execute("SELECT max(ts) FROM sig_recommendations").fetchone()
    return str(row[0]) if row and row[0] is not None else None


def list_recommendations(con: duckdb.DuckDBPyConnection,
                         ts: str | None = None) -> pd.DataFrame:
    """Recommendations am ts samt Journal-Status (LEFT JOIN).

    ts=None -> juengster Recommendation-Tag. Toleriert ein noch fehlendes
    sig_decision_journal (Journal-Spalten dann NULL).
    """
    if ts is None:
        ts = latest_rec_ts(con)
    if ts is None:
        return pd.DataFrame()

    if table_exists(con, "sig_decision_journal"):
        sql = """
            SELECT CAST(r.ts AS VARCHAR) AS rec_ts, r.model AS rec_model, r.rec_id,
                   r.category, r.symbol, r.ref_instrument_id,
                   r.action, r.priority, r.title, r.rationale,
                   j.status, j.decided_at, j.rationale AS decision_note,
                   j.linked_trades, j.outcome, j.outcome_pnl_eur,
                   j.outcome_note, j.outcome_assessed_at
            FROM sig_recommendations r
            LEFT JOIN sig_decision_journal j
                   ON j.rec_ts = r.ts AND j.rec_model = r.model
                  AND j.rec_id = r.rec_id
            WHERE r.ts = ?
            ORDER BY CASE r.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2
                                     ELSE 3 END, r.rec_id
        """
    else:
        sql = """
            SELECT CAST(r.ts AS VARCHAR) AS rec_ts, r.model AS rec_model, r.rec_id,
                   r.category, r.symbol, r.ref_instrument_id,
                   r.action, r.priority, r.title, r.rationale,
                   NULL AS status, NULL AS decided_at, NULL AS decision_note,
                   NULL AS linked_trades, NULL AS outcome,
                   NULL AS outcome_pnl_eur, NULL AS outcome_note,
                   NULL AS outcome_assessed_at
            FROM sig_recommendations r
            WHERE r.ts = ?
            ORDER BY CASE r.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2
                                     ELSE 3 END, r.rec_id
        """
    return con.execute(sql, [ts]).df()


def suggest_trades(con: duckdb.DuckDBPyConnection, ref_instrument_id: str | None,
                   rec_ts: str, window_days: int = SUGGEST_WINDOW_DAYS) -> pd.DataFrame:
    """Kandidaten-Trades: gleiches Instrument, ts im Fenster ab rec_ts."""
    if not ref_instrument_id:
        return pd.DataFrame()
    end = (date.fromisoformat(str(rec_ts)) + timedelta(days=window_days)).isoformat()
    return con.execute("""
        SELECT ref_instrument_id, broker, trade_lot,
               CAST(ts AS VARCHAR) AS ts, side,
               quantity, price, currency, realized_pnl
        FROM pos_trades
        WHERE ref_instrument_id = ? AND ts >= ? AND ts <= ?
        ORDER BY ts, broker, trade_lot
    """, [ref_instrument_id, str(rec_ts), end]).df()


def get_journal(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    if not table_exists(con, "sig_decision_journal"):
        return pd.DataFrame()
    return con.execute("""
        SELECT CAST(rec_ts AS VARCHAR) AS rec_ts, rec_model, rec_id,
               rec_action, rec_symbol, rec_title,
               status, CAST(decided_at AS VARCHAR) AS decided_at,
               rationale, linked_trades,
               outcome, outcome_pnl_eur, outcome_note,
               CAST(outcome_assessed_at AS VARCHAR) AS outcome_assessed_at,
               updated_at
        FROM sig_decision_journal
        ORDER BY rec_ts DESC, rec_id
    """).df()


def journal_stats(con: duckdb.DuckDBPyConnection) -> dict:
    """Kennzahlen: Status-/Outcome-Verteilung + Follow-Through-Rate."""
    out: dict = {"n_recs_total": 0, "n_journaled": 0, "by_status": {},
                 "by_outcome": {}, "follow_through_pct": None}
    if table_exists(con, "sig_recommendations"):
        out["n_recs_total"] = int(
            con.execute("SELECT count(*) FROM sig_recommendations").fetchone()[0])
    if not table_exists(con, "sig_decision_journal"):
        return out
    out["n_journaled"] = int(
        con.execute("SELECT count(*) FROM sig_decision_journal").fetchone()[0])
    for status, n in con.execute(
        "SELECT status, count(*) FROM sig_decision_journal GROUP BY status"
    ).fetchall():
        out["by_status"][status] = int(n)
    for outcome, n in con.execute(
        "SELECT outcome, count(*) FROM sig_decision_journal "
        "WHERE outcome IS NOT NULL GROUP BY outcome"
    ).fetchall():
        out["by_outcome"][outcome] = int(n)
    decided = sum(v for k, v in out["by_status"].items() if k != "pending")
    acted = (out["by_status"].get("acted_full", 0)
             + out["by_status"].get("acted_partial", 0))
    if decided:
        out["follow_through_pct"] = round(acted / decided * 100.0, 1)
    return out


# ---------- Schreiben (oeffnet eigene RW-Connection) ----------

def upsert_decision(rec_ts: str, rec_model: str, rec_id: int, *,
                    status: str, rationale: str | None = None,
                    linked_trades: list[dict] | None = None,
                    decided_at: str | None = None) -> None:
    """Entscheidung zu einer Recommendation erfassen/aktualisieren.

    Outcome-Felder bleiben unberuehrt (ON CONFLICT updated nur Decision).
    """
    if status not in VALID_STATUS:
        raise JournalError(
            f"ungueltiger status '{status}' — erlaubt: {', '.join(VALID_STATUS)}")
    if decided_at is None and status != "pending":
        decided_at = date.today().isoformat()

    with connect(read_only=False) as con:
        apply_schema(con)
        rec = con.execute("""
            SELECT action, symbol, title FROM sig_recommendations
            WHERE ts = ? AND model = ? AND rec_id = ?
        """, [rec_ts, rec_model, rec_id]).fetchone()
        if rec is None:
            existing = con.execute("""
                SELECT rec_action, rec_symbol, rec_title
                FROM sig_decision_journal
                WHERE rec_ts = ? AND rec_model = ? AND rec_id = ?
            """, [rec_ts, rec_model, rec_id]).fetchone()
            if existing is None:
                raise JournalError(
                    f"Recommendation ({rec_ts}, {rec_model}, #{rec_id}) "
                    f"nicht gefunden.")
            rec_action, rec_symbol, rec_title = existing
        else:
            rec_action, rec_symbol, rec_title = rec

        con.execute("""
            INSERT INTO sig_decision_journal
                (rec_ts, rec_model, rec_id, rec_action, rec_symbol, rec_title,
                 status, decided_at, rationale, linked_trades, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
            ON CONFLICT (rec_ts, rec_model, rec_id) DO UPDATE SET
                rec_action    = excluded.rec_action,
                rec_symbol    = excluded.rec_symbol,
                rec_title     = excluded.rec_title,
                status        = excluded.status,
                decided_at    = excluded.decided_at,
                rationale     = excluded.rationale,
                linked_trades = excluded.linked_trades,
                updated_at    = now()
        """, [rec_ts, rec_model, rec_id, rec_action, rec_symbol, rec_title,
              status, decided_at, rationale,
              serialize_trades(linked_trades)])


def assess_outcome(rec_ts: str, rec_model: str, rec_id: int, *,
                   outcome: str, pnl_eur: float | None = None,
                   note: str | None = None,
                   assessed_at: str | None = None) -> None:
    """Outcome zu einer bereits erfassten Entscheidung nachtragen."""
    if outcome not in VALID_OUTCOME:
        raise JournalError(
            f"ungueltiges outcome '{outcome}' — erlaubt: {', '.join(VALID_OUTCOME)}")

    with connect(read_only=False) as con:
        apply_schema(con)
        exists = con.execute("""
            SELECT 1 FROM sig_decision_journal
            WHERE rec_ts = ? AND rec_model = ? AND rec_id = ?
        """, [rec_ts, rec_model, rec_id]).fetchone()
        if exists is None:
            raise JournalError(
                "Zu dieser Recommendation gibt es noch keine Entscheidung — "
                "erst erfassen (log), dann bewerten.")
        con.execute("""
            UPDATE sig_decision_journal SET
                outcome             = ?,
                outcome_pnl_eur     = ?,
                outcome_note        = ?,
                outcome_assessed_at = ?,
                updated_at          = now()
            WHERE rec_ts = ? AND rec_model = ? AND rec_id = ?
        """, [outcome, pnl_eur, note, assessed_at or date.today().isoformat(),
              rec_ts, rec_model, rec_id])
