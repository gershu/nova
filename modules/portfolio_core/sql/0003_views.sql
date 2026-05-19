-- nova-lab portfolio_core — die 4 Core-Views.
--
-- Konzept (SCD-2 + EUR-Aggregat):
--   v_pos_holdings   = pos_holdings (WHERE valid_to IS NULL) + ref_instruments
--   v_mkt_holdings   = v_pos_holdings + v_latest_quote (Marktwert + EUR)
--   v_list_portfolio = list_portfolio_views + members + v_pos_holdings
--   v_mkt_portfolio  = v_list_portfolio + v_latest_quote (+ EUR)
--
-- Identitaet einer Position: (ref_instrument_id, broker).
-- Currency-Conversion auf EUR via v_latest_fx; EUR-Native bleibt durch
-- COALESCE durch.
--
-- Idempotent via CREATE OR REPLACE.


-- =========================================================
-- v_pos_holdings — current state mit Instrument-Stammdaten
-- =========================================================
CREATE OR REPLACE VIEW v_pos_holdings AS
SELECT
    h.ref_instrument_id,
    h.broker,
    i.symbol,
    i.name,
    i.asset_type,
    i.exchange,
    COALESCE(h.currency, i.currency)     AS currency,
    h.quantity,
    h.cost_per_share,
    h.acquired_at,
    h.valid_from,
    h.account,
    h.notes,
    h.change_type,
    h.created_at
FROM pos_holdings h
LEFT JOIN ref_instruments i USING (ref_instrument_id)
WHERE h.valid_to IS NULL;


-- =========================================================
-- v_mkt_holdings — Marktwert je Position, Native + EUR
-- =========================================================
CREATE OR REPLACE VIEW v_mkt_holdings AS
WITH base AS (
    SELECT
        p.ref_instrument_id,
        p.broker,
        p.symbol,
        p.name,
        p.asset_type,
        p.exchange,
        p.currency,
        p.quantity,
        p.cost_per_share,
        p.acquired_at,
        p.valid_from,
        p.account,
        q.quote_ts,
        q.close                                   AS px_close,
        q.quote_source,
        p.quantity * q.close                      AS mtm_native,
        p.cost_per_share * p.quantity             AS cost_total_native,
        (q.close - p.cost_per_share) * p.quantity AS pnl_native
    FROM v_pos_holdings p
    LEFT JOIN v_latest_quote q USING (ref_instrument_id)
)
SELECT
    b.*,
    fx.rate                                                      AS fx_rate_eur,
    COALESCE(fx.rate * b.mtm_native,        b.mtm_native)        AS mtm_eur,
    COALESCE(fx.rate * b.pnl_native,        b.pnl_native)        AS pnl_eur,
    COALESCE(fx.rate * b.cost_total_native, b.cost_total_native) AS cost_total_eur
FROM base b
LEFT JOIN v_latest_fx fx
       ON fx.currency_from = b.currency
      AND fx.currency_to   = 'EUR';


-- =========================================================
-- v_list_portfolio — Portfolio-Views + Members + v_pos_holdings
-- =========================================================
-- Member-JOIN ist auf (ref_instrument_id, broker) — composite Identitaet.
CREATE OR REPLACE VIEW v_list_portfolio AS
SELECT
    v.view_id,
    v.name                               AS view_name,
    v.description                        AS view_description,
    v.color                              AS view_color,
    v.origin                             AS view_origin,
    m.added_at                           AS member_added_at,
    m.added_by                           AS member_added_by,
    m.notes                              AS member_notes,
    p.ref_instrument_id,
    p.broker,
    p.symbol,
    p.name,
    p.asset_type,
    p.exchange,
    p.currency,
    p.quantity,
    p.cost_per_share,
    p.acquired_at,
    p.account
FROM list_portfolio_views v
JOIN list_portfolio_view_members m USING (view_id)
JOIN v_pos_holdings p USING (ref_instrument_id, broker)
WHERE v.active = TRUE;


-- =========================================================
-- v_mkt_portfolio — v_list_portfolio + Marktwert + EUR
-- =========================================================
CREATE OR REPLACE VIEW v_mkt_portfolio AS
WITH base AS (
    SELECT
        l.view_id,
        l.view_name,
        l.view_description,
        l.view_color,
        l.ref_instrument_id,
        l.broker,
        l.symbol,
        l.name,
        l.asset_type,
        l.exchange,
        l.currency,
        l.quantity,
        l.cost_per_share,
        l.acquired_at,
        l.account,
        q.quote_ts,
        q.close                                   AS px_close,
        q.quote_source,
        l.quantity * q.close                      AS mtm_native,
        l.cost_per_share * l.quantity             AS cost_total_native,
        (q.close - l.cost_per_share) * l.quantity AS pnl_native
    FROM v_list_portfolio l
    LEFT JOIN v_latest_quote q USING (ref_instrument_id)
)
SELECT
    b.*,
    fx.rate                                                      AS fx_rate_eur,
    COALESCE(fx.rate * b.mtm_native,        b.mtm_native)        AS mtm_eur,
    COALESCE(fx.rate * b.pnl_native,        b.pnl_native)        AS pnl_eur,
    COALESCE(fx.rate * b.cost_total_native, b.cost_total_native) AS cost_total_eur
FROM base b
LEFT JOIN v_latest_fx fx
       ON fx.currency_from = b.currency
      AND fx.currency_to   = 'EUR';
