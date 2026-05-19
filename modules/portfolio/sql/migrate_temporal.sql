-- ============================================================
-- nova-lab Portfolio — Migration auf SCD-2 + pos_trades
-- ============================================================
-- EINMALIGE MIGRATION. Macht aus dem alten pos_holdings-Snapshot:
--   - pos_holdings mit valid_from / valid_to / change_type (SCD-2)
--   - pos_trades (neue Event-Log-Tabelle)
--   - list_portfolio_view_members mit (ref_instrument_id, broker)
--     statt lot_id
--
-- Vorher: Backup machen!
--   cp ~/nova_data/lab.duckdb ~/nova_data/lab.duckdb.bak
--
-- Ausfuehrung:
--   duckdb ~/nova_data/lab.duckdb < modules/portfolio/sql/migrate_temporal.sql
--
-- Nach der Migration:
--   - pos_holdings.holding_id, lot_id, import_run_id   -> WEG
--   - pos_holdings.valid_from, valid_to, change_type   -> NEU
--   - pos_trades                                       -> NEU + opening-bootstrap
--   - list_portfolio_view_members.lot_id               -> WEG
--   - list_portfolio_view_members.ref_instrument_id +
--     broker                                            -> NEU
--
-- Idempotenz: NICHT idempotent. Zweiter Run wuerde crashen (DROP TABLE
-- auf bereits-droppte Tabelle / INSERT-Duplikate). Genau einmal laufen.
-- ============================================================

BEGIN TRANSACTION;

-- ============================================================
-- Step 1: Backup-Snapshots als TEMP-Tabellen
-- ============================================================
CREATE OR REPLACE TEMPORARY TABLE _bkp_pos_holdings AS
    SELECT * FROM pos_holdings;

CREATE OR REPLACE TEMPORARY TABLE _bkp_list_pvm AS
    SELECT * FROM list_portfolio_view_members;


-- ============================================================
-- Step 2: Alte Tabellen droppen
-- ============================================================
DROP TABLE IF EXISTS pos_holdings;
DROP TABLE IF EXISTS list_portfolio_view_members;


-- ============================================================
-- Step 3: pos_holdings neu — SCD-2
-- ============================================================
CREATE TABLE pos_holdings (
    ref_instrument_id VARCHAR NOT NULL,
    broker            VARCHAR NOT NULL,
    valid_from        DATE NOT NULL,
    valid_to          DATE,                          -- NULL = current
    quantity          DOUBLE NOT NULL,
    cost_per_share    DOUBLE,
    currency          VARCHAR,
    acquired_at       DATE,                          -- echtes Erst-Kaufdatum
    account           VARCHAR,
    notes             VARCHAR,
    change_type       VARCHAR,                       -- 'opening'|'buy'|'sell'|'adjust'
    created_at        TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ref_instrument_id, broker, valid_from)
);

CREATE INDEX idx_pos_holdings_ref     ON pos_holdings(ref_instrument_id);
CREATE INDEX idx_pos_holdings_broker  ON pos_holdings(broker);

INSERT INTO pos_holdings
    (ref_instrument_id, broker, valid_from, valid_to,
     quantity, cost_per_share, currency, acquired_at, account, notes,
     change_type, created_at)
SELECT
    ref_instrument_id,
    broker,
    COALESCE(acquired_at, current_date) AS valid_from,
    NULL                                AS valid_to,
    quantity,
    cost_per_share,
    currency,
    acquired_at,
    account,
    notes,
    'opening'                           AS change_type,
    created_at
FROM _bkp_pos_holdings;


-- ============================================================
-- Step 4: pos_trades — neu, mit Opening-Bootstrap
-- ============================================================
CREATE TABLE IF NOT EXISTS pos_trades (
    ref_instrument_id VARCHAR NOT NULL,
    broker            VARCHAR NOT NULL,
    trade_lot         INTEGER NOT NULL,    -- manuell hochgezaehlt pro (ref, broker)
    ts                DATE NOT NULL,       -- Trade-Datum
    side              VARCHAR NOT NULL,    -- 'buy' | 'sell'
    quantity          DOUBLE NOT NULL,     -- IMMER positiv
    price             DOUBLE NOT NULL,     -- pro Aktie, native CCY
    currency          VARCHAR NOT NULL,
    fees              DOUBLE DEFAULT 0,
    realized_pnl      DOUBLE,              -- NULL bei buy, manuell bei sell (native CCY)
    account           VARCHAR,
    notes             VARCHAR,
    created_at        TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ref_instrument_id, broker, trade_lot)
);

CREATE INDEX idx_pos_trades_ts   ON pos_trades(ts);
CREATE INDEX idx_pos_trades_side ON pos_trades(side);

-- Bootstrap: jede aktuelle Position bekommt einen Opening-Buy mit trade_lot=1
INSERT INTO pos_trades
    (ref_instrument_id, broker, trade_lot, ts, side,
     quantity, price, currency, fees, realized_pnl, account, notes,
     created_at)
SELECT
    ref_instrument_id,
    broker,
    1                                              AS trade_lot,
    valid_from                                     AS ts,
    'buy'                                          AS side,
    quantity,
    COALESCE(cost_per_share, 0)                    AS price,
    COALESCE(currency, 'USD')                      AS currency,
    0                                              AS fees,
    NULL                                           AS realized_pnl,
    account,
    'Opening balance (bootstrap from pos_holdings)' AS notes,
    created_at
FROM pos_holdings
WHERE change_type = 'opening' AND valid_to IS NULL;


-- ============================================================
-- Step 5: list_portfolio_view_members — refactor lot_id -> (ref, broker)
-- ============================================================
CREATE TABLE list_portfolio_view_members (
    view_id           VARCHAR NOT NULL,
    ref_instrument_id VARCHAR NOT NULL,
    broker            VARCHAR NOT NULL,
    added_at          TIMESTAMP DEFAULT current_timestamp,
    added_by          VARCHAR,
    notes             VARCHAR,
    PRIMARY KEY (view_id, ref_instrument_id, broker)
);

CREATE INDEX idx_list_pvm_view ON list_portfolio_view_members(view_id);

-- Migration: pro alter lot_id → (ref_instrument_id, broker) via Backup-Join.
-- DISTINCT weil mehrere alte Lots auf dieselbe (ref, broker) gefallen sein
-- koennen.
INSERT INTO list_portfolio_view_members
    (view_id, ref_instrument_id, broker, added_at, added_by, notes)
SELECT DISTINCT
    m.view_id,
    h.ref_instrument_id,
    h.broker,
    m.added_at,
    m.added_by,
    m.notes
FROM _bkp_list_pvm m
JOIN _bkp_pos_holdings h ON h.lot_id = m.lot_id;


-- ============================================================
-- Step 6: TEMPs aufraeumen
-- ============================================================
DROP TABLE IF EXISTS _bkp_pos_holdings;
DROP TABLE IF EXISTS _bkp_list_pvm;

COMMIT;


-- ============================================================
-- Verify (manuell):
--   .schema pos_holdings
--   .schema pos_trades
--   .schema list_portfolio_view_members
--   SELECT count(*) FROM pos_holdings   WHERE valid_to IS NULL;
--   SELECT count(*) FROM pos_trades     WHERE trade_lot = 1;
--   SELECT count(*) FROM list_portfolio_view_members;
-- ============================================================
