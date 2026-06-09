-- nova-lab: getrackte 13F-Filer ("Superinvestoren").
--
-- ref_superinvestor_holdings: das gemeldete Portfolio je (Manager, Periode).
-- ref_superinvestor_changes : Quartalsveraenderung ggue. Vorperiode
--   (NEW/ADD/TRIM/EXIT) — der eigentliche Ideen-Mehrwert.
-- Befuellt vom Ingest (python -m modules.superinvestors ingest).
--
-- HINWEIS: 13F = nur US-Long-Aktien + gelistete Optionen (put_call), keine
-- Shorts/Cash/Non-US; 45-Tage-Lag. Ideen-Quelle, kein Timing-Signal.

CREATE TABLE IF NOT EXISTS ref_superinvestor_holdings (
    manager_cik   VARCHAR,
    manager_name  VARCHAR,
    period        VARCHAR,          -- periodOfReport (Quartalsende)
    filed_at      TIMESTAMP,
    ticker        VARCHAR,
    cusip         VARCHAR,
    name          VARCHAR,
    value         DOUBLE,           -- gemeldeter Marktwert (USD)
    shares        DOUBLE,
    put_call      VARCHAR,          -- ''=Aktie | 'Put' | 'Call'
    ingested_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (manager_cik, period, cusip, put_call)
);

CREATE INDEX IF NOT EXISTS idx_superinv_hold_mgr
    ON ref_superinvestor_holdings(manager_cik, period);

CREATE TABLE IF NOT EXISTS ref_superinvestor_changes (
    manager_cik   VARCHAR,
    manager_name  VARCHAR,
    period        VARCHAR,
    prior_period  VARCHAR,
    ticker        VARCHAR,
    cusip         VARCHAR,
    name          VARCHAR,
    put_call      VARCHAR,
    change_type   VARCHAR,          -- NEW | ADD | TRIM | EXIT
    value_new     DOUBLE,
    value_old     DOUBLE,
    shares_new    DOUBLE,
    shares_old    DOUBLE,
    computed_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (manager_cik, period, cusip, put_call)
);

CREATE INDEX IF NOT EXISTS idx_superinv_chg_period
    ON ref_superinvestor_changes(period, change_type);
