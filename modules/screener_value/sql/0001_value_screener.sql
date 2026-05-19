-- nova-lab Value-Screener-Schema, version 0001 (B+-Phase).
-- sig_* (Signals) + audit_* (Process Logs) prefixes.
--
-- Persistiert:
--   sig_value_picks       — gefilterte Top-N Underlyings (weekly auto)
--   sig_value_briefings   — LLM-Strukturierer-Output (on-demand)
--   audit_value_screener_runs — Run-History fuer Audit
--
-- Konvention: Filter-Output landet sowohl als sig_value_picks-Insert (history)
-- als auch als list_watchlist_members in der 'value_picks'-Watchlist
-- (current state) — analog zu screener_csp + system_recommendations.
-- Idempotent (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS sig_value_picks (
    run_id              VARCHAR NOT NULL,         -- nova job_id
    ref_instrument_id   VARCHAR NOT NULL,         -- '{SOURCE}:{SYMBOL}:{CURRENCY}'
    ts                  DATE    NOT NULL,         -- Run-Datum

    -- Score-Breakdown
    composite_score     DOUBLE,                   -- 0..1, final-rank
    conviction_score    DOUBLE,                   -- aus screener_csp/conviction.py
    hard_filter_passes  BOOLEAN,                  -- per-criterion all-pass
    rank                INTEGER,                  -- 1 = best dieses Run

    -- Snapshot der Filter-Kriterien zum Run (Audit)
    criteria_json       VARCHAR,

    -- Notes-String (lesbar fuer digest / CLI)
    notes               VARCHAR,

    created_at          TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (run_id, ref_instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_sig_value_picks_inst ON sig_value_picks(ref_instrument_id, ts);
CREATE INDEX IF NOT EXISTS idx_sig_value_picks_ts   ON sig_value_picks(ts);


CREATE TABLE IF NOT EXISTS sig_value_briefings (
    ref_instrument_id   VARCHAR NOT NULL,
    ts                  DATE    NOT NULL,

    -- LLM-Output
    model               VARCHAR NOT NULL,
    summary             VARCHAR,             -- 2-3 Saetze, Strukturierer-Style
    strengths           VARCHAR,             -- Bullet-Liste als Newline-separierter String
    red_flags           VARCHAR,             -- Bullet-Liste, oder "keine offensichtlichen"

    -- Input-Metadata (Audit: was hat das LLM gesehen)
    fundamentals_used   INTEGER,             -- wieviele Metriken aus fundamentals
    news_count          INTEGER,             -- wieviele News-Headlines im Prompt
    eval_tokens         INTEGER,
    duration_s          DOUBLE,

    run_id              VARCHAR NOT NULL,
    generated_at        TIMESTAMP DEFAULT current_timestamp,

    PRIMARY KEY (ref_instrument_id, ts)
);

CREATE INDEX IF NOT EXISTS idx_sig_value_briefings_inst ON sig_value_briefings(ref_instrument_id, ts);


CREATE TABLE IF NOT EXISTS audit_value_screener_runs (
    run_id              VARCHAR PRIMARY KEY,
    started_at          TIMESTAMP NOT NULL,
    completed_at        TIMESTAMP,
    universe_size       INTEGER,             -- wieviele Symbols im Input-Universe
    fundamentals_hits   INTEGER,             -- wieviele hatten fundamentals
    candidates          INTEGER,             -- nach hard-filter
    picked              INTEGER,             -- top-N als final-output
    status              VARCHAR,             -- 'running', 'success', 'failed'
    error_msg           VARCHAR
);
