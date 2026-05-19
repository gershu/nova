-- nova-lab monitor schema, version 0002 — LLM-erzeugte Alert-Erklaerungen.
--
-- Logischer FK auf sig_alerts: gleicher PK-Shape (ref_instrument_id, rule_name,
-- direction, ts). Eine Explanation pro Alert; Re-Run mit anderem Modell oder
-- besseren News ueberschreibt via INSERT OR REPLACE.
--
-- model + run_id als Metadata fuer Audit ("welches Modell hat was gesagt
-- wann"), nicht als Teil des PK — sonst wuerden multiple Modelle parallel
-- explanations halten was wir nicht wollen.

CREATE TABLE IF NOT EXISTS sig_alert_explanations (
    ref_instrument_id  VARCHAR NOT NULL,
    rule_name          VARCHAR NOT NULL,
    direction          VARCHAR NOT NULL,    -- '' wenn alert direction NULL hatte (defensive)
    ts                 DATE    NOT NULL,

    -- LLM-Output
    model              VARCHAR NOT NULL,    -- z.B. 'qwen2.5:14b-instruct-q4_K_M'
    explanation        VARCHAR,             -- 2-3 Saetze
    sentiment          VARCHAR,             -- 'negative', 'neutral', 'positive', NULL bei Fail
    confidence         DOUBLE,              -- 0..1

    -- Metadata
    news_count         INTEGER,             -- wieviele News bereitgestellt
    news_used          INTEGER,             -- LLM-claimed wieviele tatsaechlich genutzt
    eval_tokens        INTEGER,
    duration_s         DOUBLE,

    -- Audit
    run_id             VARCHAR NOT NULL,    -- letzter explainer-run der diese erzeugt hat
    generated_at       TIMESTAMP DEFAULT current_timestamp,

    PRIMARY KEY (ref_instrument_id, rule_name, direction, ts)
);

CREATE INDEX IF NOT EXISTS idx_sig_alert_expl_ts        ON sig_alert_explanations(ts);
CREATE INDEX IF NOT EXISTS idx_sig_alert_expl_inst      ON sig_alert_explanations(ref_instrument_id, ts);
CREATE INDEX IF NOT EXISTS idx_sig_alert_expl_sentiment ON sig_alert_explanations(sentiment);
