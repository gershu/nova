-- nova-lab health — Snapshot pro Run.
--
-- Eine Zeile pro Health-Check-Run. details_json speichert pro Daemon den
-- Status zum Zeitpunkt des Snapshots (label, overall, last_run_ts, metric).
-- So koennen wir spaeter Drift-Trends nachzeichnen ("seit wann hakt monitor?")
-- ohne pro Daemon eigene Tabellen zu pflegen.

CREATE TABLE IF NOT EXISTS sig_health_snapshots (
    run_id            VARCHAR PRIMARY KEY,
    ts                TIMESTAMP NOT NULL,
    total_daemons     INTEGER,
    fresh_count       INTEGER,
    stale_count       INTEGER,
    failed_count      INTEGER,
    down_count        INTEGER,    -- long-running, aber Prozess/Port weg
    unknown_count     INTEGER,    -- DB-Query gescheitert o.ae.
    details_json      VARCHAR     -- liste von dicts pro Daemon
);

CREATE INDEX IF NOT EXISTS idx_sig_health_snapshots_ts
    ON sig_health_snapshots(ts);
