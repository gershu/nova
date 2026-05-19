-- nova-lab Portfolio-Trades-Schema (Event-Log).
--
-- pos_trades = append-only Event-Log fuer Buy/Sell-Trades.
--   PK = (ref_instrument_id, broker, trade_lot)
--   trade_lot ist manuell vergebener Counter pro (ref, broker), startet bei 1.
--   realized_pnl wird manuell beim Sell gepflegt (kein FIFO-Auto).
--
-- Beziehung zu pos_holdings:
--   - Jede pos_trades-Zeile korrespondiert zu einer valid_from-Aenderung
--     in pos_holdings (via ts ↔ valid_from der closing/neuen Row).
--   - Drift-Check via v_pos_reconcile (siehe portfolio_core/sql).

CREATE TABLE IF NOT EXISTS pos_trades (
    ref_instrument_id VARCHAR NOT NULL,
    broker            VARCHAR NOT NULL,
    trade_lot         INTEGER NOT NULL,        -- manueller Counter
    ts                DATE NOT NULL,           -- Trade-Datum
    side              VARCHAR NOT NULL,        -- 'buy' | 'sell'
    quantity          DOUBLE NOT NULL,         -- immer positiv
    price             DOUBLE NOT NULL,         -- pro Aktie, native CCY
    currency          VARCHAR NOT NULL,
    fees              DOUBLE DEFAULT 0,
    realized_pnl      DOUBLE,                  -- NULL bei buy, manuell bei sell (native CCY)
    account           VARCHAR,
    notes             VARCHAR,
    created_at        TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ref_instrument_id, broker, trade_lot)
);

CREATE INDEX IF NOT EXISTS idx_pos_trades_ts   ON pos_trades(ts);
CREATE INDEX IF NOT EXISTS idx_pos_trades_side ON pos_trades(side);
