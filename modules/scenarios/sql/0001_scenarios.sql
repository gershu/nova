-- nova-lab Scenarios-Schema, version 0001.
-- ref_*: definition (slow-changing). sig_*: run-history (events).
-- Idempotent.

-- Scenario-Definitionen (Stammdaten)
CREATE TABLE IF NOT EXISTS ref_scenarios (
    scenario_id     VARCHAR PRIMARY KEY,            -- slug: 'tech_crash_2026'
    name            VARCHAR NOT NULL,                -- display name
    description     VARCHAR,
    base_currency   VARCHAR DEFAULT 'EUR',
    tags            VARCHAR,                         -- comma-separated frei: 'stress,tech,2026'
    active          BOOLEAN DEFAULT true,
    created_at      TIMESTAMP DEFAULT current_timestamp,
    updated_at      TIMESTAMP DEFAULT current_timestamp
);

-- Shocks innerhalb eines Scenarios (ein Scenario = N Shocks)
CREATE TABLE IF NOT EXISTS ref_scenario_shocks (
    scenario_id     VARCHAR NOT NULL,                -- FK -> ref_scenarios
    shock_idx       INTEGER NOT NULL,                -- order within scenario
    target          VARCHAR NOT NULL,                -- 'symbol' | 'currency' | 'asset_class' | 'watchlist'
    target_value    VARCHAR NOT NULL,                -- 'AAPL' | 'USD' | 'stock' | 'observation'
    pct_change      DOUBLE NOT NULL,                 -- decimal: -0.25 fuer -25%
    PRIMARY KEY (scenario_id, shock_idx)
);

-- Run-Historie fuer historische Vergleiche / trend-analysis
CREATE TABLE IF NOT EXISTS sig_scenario_runs (
    run_id                  VARCHAR PRIMARY KEY,    -- generated UUID
    scenario_id             VARCHAR NOT NULL,
    ts                      DATE NOT NULL,           -- quote-cutoff
    base_currency           VARCHAR NOT NULL,
    portfolio_total_before  DOUBLE,
    portfolio_total_after   DOUBLE,
    delta_abs               DOUBLE,
    delta_pct               DOUBLE,
    holdings_count          INTEGER,
    affected_count          INTEGER,
    nova_run_id             VARCHAR,                 -- NOVA_JOB_ID falls via daemon/picker
    created_at              TIMESTAMP DEFAULT current_timestamp
);

CREATE INDEX IF NOT EXISTS idx_sig_scenario_runs_scn ON sig_scenario_runs(scenario_id, ts);
CREATE INDEX IF NOT EXISTS idx_sig_scenario_runs_ts  ON sig_scenario_runs(ts);
