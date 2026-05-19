-- nova-lab DuckDB schema, version 0004 — Option-Chain Snapshots.
-- mkt_* prefix (Market Data / Time-Series).
-- Idempotent.
--
-- Persistiert taegliche Snapshots der vom screener_csp evaluierten Optionen.
-- Erlaubt historische Trend-Queries:
--   "Wie hat sich AAPL-275-Strike-Premium ueber die letzten 30 Tage entwickelt?"
--   "IV-Trend fuer NVDA letzten 60 Tage?"
--
-- Schreibt: modules.screener_csp am Ende eines runs (Side-Effect).
-- Liest:    modules.options_history (CLI fuer Trend-Queries).

CREATE TABLE IF NOT EXISTS mkt_options_snapshot (
    ref_instrument_id   VARCHAR NOT NULL,        -- Underlying
    expiration          DATE NOT NULL,
    strike              DOUBLE NOT NULL,
    "right"             VARCHAR NOT NULL,         -- 'P' (put) oder 'C' (call)
    ts                  DATE NOT NULL,            -- Snapshot-Tag
    source              VARCHAR NOT NULL DEFAULT 'ib',

    -- Quote
    bid                 DOUBLE,
    ask                 DOUBLE,
    last                DOUBLE,
    volume              BIGINT,
    open_int            BIGINT,
    iv                  DOUBLE,

    -- Context fuer Trend-Analyse
    underlying_spot     DOUBLE,                   -- spot-Preis zum Snapshot-Zeitpunkt
    dte                 INTEGER,                  -- days-to-expiration zum ts

    -- Audit
    fetched_at          TIMESTAMP DEFAULT current_timestamp,
    run_id              VARCHAR,

    PRIMARY KEY (ref_instrument_id, expiration, strike, "right", ts, source)
);

CREATE INDEX IF NOT EXISTS idx_mkt_options_inst    ON mkt_options_snapshot(ref_instrument_id);
CREATE INDEX IF NOT EXISTS idx_mkt_options_inst_exp ON mkt_options_snapshot(ref_instrument_id, expiration);
CREATE INDEX IF NOT EXISTS idx_mkt_options_ts      ON mkt_options_snapshot(ts);
