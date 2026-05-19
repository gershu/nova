-- nova-lab Portfolio-Schema, version 0002 (SCD-2-Phase).
--
-- pos_holdings ist bitemporal (Type-2):
--   - PK = (ref_instrument_id, broker, valid_from)
--   - valid_to = NULL bedeutet "current state"
--   - bei Buy/Sell: alte Row schliessen + neue Row appenden
--   - cost_per_share manuell pflegen (kein FIFO-Auto)
--
-- Konsumenten lesen via v_pos_holdings (filtert valid_to IS NULL).
-- Direkt-Zugriff auf pos_holdings nur fuer Time-Travel-Queries.

CREATE TABLE IF NOT EXISTS pos_holdings (
    ref_instrument_id VARCHAR NOT NULL,
    broker            VARCHAR NOT NULL,
    valid_from        DATE NOT NULL,
    valid_to          DATE,                      -- NULL = current
    quantity          DOUBLE NOT NULL,
    cost_per_share    DOUBLE,
    currency          VARCHAR,
    acquired_at       DATE,                      -- echtes Erst-Kaufdatum
    account           VARCHAR,
    notes             VARCHAR,
    change_type       VARCHAR,                   -- 'opening'|'buy'|'sell'|'adjust'
    created_at        TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ref_instrument_id, broker, valid_from)
);

CREATE INDEX IF NOT EXISTS idx_pos_holdings_ref     ON pos_holdings(ref_instrument_id);
CREATE INDEX IF NOT EXISTS idx_pos_holdings_broker  ON pos_holdings(broker);


CREATE TABLE IF NOT EXISTS audit_portfolio_imports (
    run_id            VARCHAR PRIMARY KEY,
    imported_at       TIMESTAMP NOT NULL,
    file_path         VARCHAR NOT NULL,
    file_hash         VARCHAR,
    rows_read         INTEGER,
    rows_imported     INTEGER,
    rows_skipped      INTEGER DEFAULT 0,
    new_instruments   INTEGER DEFAULT 0,
    isin_mismatches   INTEGER DEFAULT 0,
    status            VARCHAR,
    error_msg         VARCHAR
);
