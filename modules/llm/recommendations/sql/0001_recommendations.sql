-- nova-lab recommendations — LLM-erzeugte Handlungs-Vorschlaege.
--
-- sig_recommendations: 0..N Vorschlaege pro (ts, model). Der Layer hebt die
-- verstreuten Signale (Setups, Alerts, Portfolio-Zustand) zu konkreten,
-- begruendeten Handlungs-Punkten — bleibt aber human-in-loop: Vorschlaege,
-- keine Order.
--
-- rec_id ist ein laufender Counter pro (ts, model), vom Modul vergeben.
-- Re-Run am selben Tag ersetzt die Vorschlaege (DELETE + re-INSERT).

CREATE TABLE IF NOT EXISTS sig_recommendations (
    ts                DATE NOT NULL,
    model             VARCHAR NOT NULL,
    rec_id            INTEGER NOT NULL,        -- laufend pro (ts, model)
    category          VARCHAR,                  -- 'position'|'risk'|'market'|'opportunity'
    ref_instrument_id VARCHAR,                  -- betroffene Position; NULL = portfolio-weit
    symbol            VARCHAR,                  -- vom LLM genanntes Symbol (Anzeige)
    action            VARCHAR NOT NULL,         -- 'review'|'trim'|'add'|'hedge'|'watch'|'rebalance'|'no_action'
    priority          VARCHAR NOT NULL,         -- 'high'|'medium'|'low'
    title             VARCHAR,                  -- 1-Zeiler
    rationale         VARCHAR,                  -- Begruendung (LLM)
    based_on          VARCHAR,                  -- JSON: welche Setups/Alerts die Basis waren
    run_id            VARCHAR NOT NULL,
    created_at        TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ts, model, rec_id)
);

CREATE INDEX IF NOT EXISTS idx_sig_recommendations_ts       ON sig_recommendations(ts);
CREATE INDEX IF NOT EXISTS idx_sig_recommendations_priority ON sig_recommendations(priority);
