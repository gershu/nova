-- nova-lab fred_ingest — Economic Series Schema.
--
-- Trennung zu equity-Daten in ref_instruments/mkt_quotes_daily, weil
-- Economic Indicators kategorial anders sind:
--   - Keine OHLCV-Struktur (1 Wert pro Datum reicht)
--   - Nicht tradeable
--   - Verschiedene Frequenzen (daily/weekly/monthly)
--
-- Tabellen:
--   ref_economic_series   — Metadaten je Series
--   mkt_economic_series   — Time-Series-Werte
--
-- Source: aktuell nur 'fred' (St. Louis Fed). Spalte vorgesehen damit
-- spaeter ECB-SDW, Quandl, BEA etc. dazu koennen.

CREATE TABLE IF NOT EXISTS ref_economic_series (
    series_id    VARCHAR PRIMARY KEY,        -- z.B. 'VIXCLS', 'DGS10'
    name         VARCHAR NOT NULL,            -- Long-name fuer UI
    description  VARCHAR,
    category     VARCHAR,                     -- 'volatility'|'rates'|'fx'|'credit'|'commodity'|'macro'
    units        VARCHAR,                     -- 'percent'|'index'|'usd'|...
    frequency    VARCHAR,                     -- 'daily'|'weekly'|'monthly'
    source       VARCHAR NOT NULL DEFAULT 'fred',
    active       BOOLEAN DEFAULT TRUE,        -- inactive = nicht im fetch-all
    notes        VARCHAR,
    created_at   TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS mkt_economic_series (
    series_id    VARCHAR NOT NULL,
    ts           DATE NOT NULL,
    value        DOUBLE NOT NULL,
    source       VARCHAR NOT NULL DEFAULT 'fred',
    fetched_at   TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (series_id, ts, source)
);

CREATE INDEX IF NOT EXISTS idx_mkt_economic_series_ts ON mkt_economic_series(ts);

CREATE TABLE IF NOT EXISTS audit_fred_ingest_runs (
    run_id          VARCHAR PRIMARY KEY,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    series_count    INTEGER,                  -- wieviele series ingested
    rows_inserted   INTEGER,
    rows_skipped    INTEGER,
    status          VARCHAR,                  -- 'ok'|'partial'|'fail'
    error_msg       VARCHAR
);
