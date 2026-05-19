-- nova-lab monitor schema, version 0001 (B-Phase).
-- sig_* prefix (Signals/Events).
-- ref_instrument_id ist der Identifier (joint sich mit ref_instruments).
-- Idempotent — alle Tabellen mit IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS sig_alerts (
    run_id             VARCHAR NOT NULL,    -- nova job_id des monitor-Laufs
    ref_instrument_id  VARCHAR NOT NULL,    -- '{SOURCE}:{SYMBOL}:{CURRENCY}' — joint mit ref_instruments
    rule_name          VARCHAR NOT NULL,    -- 'daily_change_pct', 'volume_spike', '52w_high', '52w_low', 'sma_cross'
    direction          VARCHAR,             -- 'up', 'down', 'golden', 'death', NULL
    trigger_value      DOUBLE,              -- Mess-Wert der getriggert hat
    threshold          DOUBLE,              -- Schwellwert der Regel
    ts                 DATE NOT NULL,       -- Datum des Quotes der getriggert hat
    details            VARCHAR,             -- JSON-blob fuer regel-spezifische Felder
    created_at         TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (run_id, ref_instrument_id, rule_name, direction, ts)
);

CREATE INDEX IF NOT EXISTS idx_sig_alerts_inst ON sig_alerts(ref_instrument_id, ts);
CREATE INDEX IF NOT EXISTS idx_sig_alerts_rule ON sig_alerts(rule_name, ts);
