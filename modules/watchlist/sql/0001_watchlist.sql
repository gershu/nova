-- nova-lab Watchlist-Schema, version 0001 (B-Phase).
-- list_* prefix (Listen / Groupings).
--
-- Many-to-many Membership: jedes Instrument kann auf 0..N Listen sein.
-- Echte Overlaps werden abgebildet (z.B. "buy_candidate" + "system_recommendation").
--
-- WICHTIG: pos_holdings ist Single-Source-of-Truth fuer "im Portfolio".
-- KEINE 'in_portfolio'-Watchlist — stattdessen v_relevant_instruments-View
-- vereint Portfolio-Holdings + Watchlist-Members in einem SELECT.

CREATE TABLE IF NOT EXISTS list_watchlists (
    watchlist_id   VARCHAR PRIMARY KEY,        -- slug (lowercase): 'buy_candidates', 'observation', ...
    name           VARCHAR NOT NULL,           -- display-name fuer UI/Reports
    description    VARCHAR,
    origin         VARCHAR NOT NULL DEFAULT 'user',   -- 'user' oder 'system' (auto-managed)
    active         BOOLEAN DEFAULT true,
    created_at     TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS list_watchlist_members (
    watchlist_id       VARCHAR NOT NULL,
    ref_instrument_id  VARCHAR NOT NULL,           -- logischer FK -> ref_instruments
    added_at           TIMESTAMP DEFAULT current_timestamp,
    added_by           VARCHAR,                    -- 'user', 'system', 'cli', 'screener', ...
    notes              VARCHAR,
    PRIMARY KEY (watchlist_id, ref_instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_list_members_inst ON list_watchlist_members(ref_instrument_id);
CREATE INDEX IF NOT EXISTS idx_list_members_list ON list_watchlist_members(watchlist_id);

-- v_relevant_instruments: alles was wir analyse-relevant tracken — Portfolio + Watchlists.
-- Source-Spalte zeigt WO ein Instrument auftaucht (kann mehrfach erscheinen wenn auf
-- mehreren Listen plus im Portfolio).
--
-- DROP IF EXISTS damit Schema-Aenderungen am View greifen (CREATE VIEW IF NOT EXISTS
-- gibt's in DuckDB, aber CREATE OR REPLACE ist robuster fuer Iteration).
CREATE OR REPLACE VIEW v_relevant_instruments AS
SELECT DISTINCT ref_instrument_id, 'portfolio' AS source
  FROM pos_holdings
UNION
SELECT ref_instrument_id, watchlist_id AS source
  FROM list_watchlist_members;
