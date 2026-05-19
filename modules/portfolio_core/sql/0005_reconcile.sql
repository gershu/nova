-- nova-lab portfolio_core — Drift-Check pos_holdings vs pos_trades.
--
-- Da pos_holdings (SCD-2 mutable) und pos_trades (event-log) parallel
-- gepflegt werden, kann Drift entstehen wenn ein Trade nur in einer der
-- beiden Tabellen landet.
--
-- v_pos_reconcile zeigt NUR die Positionen mit drift != 0.
-- Bei sauberer Pflege ist die View leer.


CREATE OR REPLACE VIEW v_pos_reconcile AS
WITH expected AS (
    -- Saldo aus pos_trades: buy - sell pro (ref, broker)
    SELECT ref_instrument_id, broker,
           SUM(CASE WHEN side = 'buy'  THEN  quantity ELSE 0 END) -
           SUM(CASE WHEN side = 'sell' THEN  quantity ELSE 0 END) AS qty_from_trades
    FROM pos_trades
    GROUP BY ref_instrument_id, broker
),
actual AS (
    -- Bestand aus pos_holdings: current rows
    SELECT ref_instrument_id, broker, quantity AS qty_in_holdings
    FROM pos_holdings
    WHERE valid_to IS NULL
)
SELECT
    COALESCE(a.ref_instrument_id, e.ref_instrument_id) AS ref_instrument_id,
    COALESCE(a.broker,            e.broker)            AS broker,
    COALESCE(a.qty_in_holdings,   0)                   AS qty_in_holdings,
    COALESCE(e.qty_from_trades,   0)                   AS qty_from_trades,
    COALESCE(a.qty_in_holdings, 0)
      - COALESCE(e.qty_from_trades, 0)                 AS drift
FROM actual a
FULL OUTER JOIN expected e USING (ref_instrument_id, broker)
WHERE COALESCE(a.qty_in_holdings, 0) <> COALESCE(e.qty_from_trades, 0);
