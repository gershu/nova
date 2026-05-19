-- nova-lab portfolio_core — Portfolio-View-Tabellen (list_*).
--
-- Tabellen:
--   list_portfolio_views          — benannte Filter-Sichten ('core', 'satellite', ...)
--   list_portfolio_view_members   — m:n auf (ref_instrument_id, broker)
--
-- Konzept: eine View enthaelt "AAPL-bei-IB" als Member, nicht ein abstraktes
-- Lot. Identitaet = (ref_instrument_id, broker), konsistent mit pos_holdings.
--
-- CRUD via modules.db_edit. Idempotent.

CREATE TABLE IF NOT EXISTS list_portfolio_views (
    view_id      VARCHAR PRIMARY KEY,
    name         VARCHAR NOT NULL,
    description  VARCHAR,
    origin       VARCHAR NOT NULL DEFAULT 'user',
    color        VARCHAR,
    active       BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS list_portfolio_view_members (
    view_id           VARCHAR NOT NULL,
    ref_instrument_id VARCHAR NOT NULL,
    broker            VARCHAR NOT NULL,
    added_at          TIMESTAMP DEFAULT current_timestamp,
    added_by          VARCHAR,
    notes             VARCHAR,
    PRIMARY KEY (view_id, ref_instrument_id, broker)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_view_members_view ON list_portfolio_view_members(view_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_view_members_inst ON list_portfolio_view_members(ref_instrument_id, broker);
