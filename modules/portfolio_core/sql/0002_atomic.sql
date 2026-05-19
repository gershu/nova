-- nova-lab portfolio_core — Atomic-Helper-Views.
--
-- Konventionen:
--   - Source-Priority: ib > yfinance > ecb > rest
--   - Latest pro instrument / pair (nicht globaler MAX(ts))
--   - Numerics roh (kein round); Formatierung im Frontend
--   - Idempotent via CREATE OR REPLACE


-- v_latest_quote — pro ref_instrument_id der juengste close.
-- Tied-ts werden via source-priority entschieden.
CREATE OR REPLACE VIEW v_latest_quote AS
WITH ranked AS (
    SELECT
        ref_instrument_id,
        ts,
        open, high, low, close, adj_close, volume,
        source,
        ROW_NUMBER() OVER (
            PARTITION BY ref_instrument_id
            ORDER BY ts DESC,
                     CASE source
                         WHEN 'ib'       THEN 1
                         WHEN 'yfinance' THEN 2
                         ELSE 9
                     END
        ) AS rk
    FROM mkt_quotes_daily
)
SELECT
    ref_instrument_id,
    ts            AS quote_ts,
    open, high, low, close, adj_close, volume,
    source        AS quote_source
FROM ranked
WHERE rk = 1;


-- v_latest_fx — pro currency_pair der juengste rate.
-- Tied-ts: 'ib' > 'yfinance' > 'ecb'.
CREATE OR REPLACE VIEW v_latest_fx AS
WITH ranked AS (
    SELECT
        currency_from, currency_to, ts, rate, source,
        ROW_NUMBER() OVER (
            PARTITION BY currency_from, currency_to
            ORDER BY ts DESC,
                     CASE source
                         WHEN 'ib'       THEN 1
                         WHEN 'yfinance' THEN 2
                         WHEN 'ecb'      THEN 3
                         ELSE 9
                     END
        ) AS rk
    FROM mkt_fx_daily
)
SELECT
    currency_from,
    currency_to,
    ts     AS fx_ts,
    rate,
    source AS fx_source
FROM ranked
WHERE rk = 1;
