-- nova-lab monitor schema, version 0003 — LLM-erzeugte Portfolio-Tagesbriefings.
--
-- Eine Briefing pro (Datum, Modell). PK enthaelt model damit verschiedene
-- Modelle parallel briefings produzieren koennen (Audit / A/B-Vergleich).
-- Digest nimmt das jeweils neueste pro Datum.

CREATE TABLE IF NOT EXISTS sig_portfolio_briefings (
    ts                 DATE NOT NULL,
    model              VARCHAR NOT NULL,        -- z.B. 'qwen2.5:14b-instruct-q4_K_M'

    -- Snapshot-Kontext (zu welchem Stand wurde Briefing erzeugt)
    base_currency      VARCHAR NOT NULL,
    portfolio_total    DOUBLE,                  -- in base_currency
    delta_abs_day      DOUBLE,                  -- vs vortag
    delta_pct_day      DOUBLE,
    holdings_count     INTEGER,                 -- wieviele lots
    alerts_count       INTEGER,                 -- wieviele heute auf gehaltenen Werten

    -- LLM-Output
    headline           VARCHAR,                 -- 1-Zeiler
    body               VARCHAR,                 -- 2-3 Absaetze Markdown
    sentiment          VARCHAR,                 -- 'positive' / 'neutral' / 'negative'
    confidence         DOUBLE,                  -- 0..1

    -- Metadata
    eval_tokens        INTEGER,
    duration_s         DOUBLE,
    run_id             VARCHAR NOT NULL,
    generated_at       TIMESTAMP DEFAULT current_timestamp,

    PRIMARY KEY (ts, model)
);

CREATE INDEX IF NOT EXISTS idx_sig_portfolio_briefings_ts ON sig_portfolio_briefings(ts);
