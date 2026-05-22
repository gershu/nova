-- nova-lab setup — Trading-Setup-Detection Schema.
--
-- sig_market_setups: ein erkanntes Setup pro (setup_name, ts). Der Detector
-- laeuft taeglich, schreibt fuer jedes aktuell-aktive Setup eine Row.
-- Eine "Setup-Episode" (Setup aktiv ueber mehrere Tage) leitet man per Query
-- aus aufeinanderfolgenden ts ab — konsistent zum sig_alerts-Pattern.
--
-- run_id ist Audit-Metadata (nicht im PK) — Re-Run am selben Tag
-- ueberschreibt via INSERT OR REPLACE.

CREATE TABLE IF NOT EXISTS sig_market_setups (
    setup_name    VARCHAR NOT NULL,        -- 'risk_off_regime', 'position_concentration', ...
    ts            DATE NOT NULL,            -- Detektions-Datum
    severity      VARCHAR NOT NULL,         -- 'info' | 'warning' | 'critical'
    category      VARCHAR,                  -- 'market' | 'portfolio' | 'risk'
    summary       VARCHAR,                  -- 1-Zeiler — was wurde erkannt
    details       VARCHAR,                  -- JSON: ausgewertete Bedingungen + Werte
    run_id        VARCHAR NOT NULL,         -- Audit: welcher Detector-Run
    created_at    TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (setup_name, ts)
);

CREATE INDEX IF NOT EXISTS idx_sig_market_setups_ts       ON sig_market_setups(ts);
CREATE INDEX IF NOT EXISTS idx_sig_market_setups_severity ON sig_market_setups(severity);
