-- nova-lab screener — Stufe 1+2-Persistenz + Stufe-3-Stub.
--
-- Jeder screen-Lauf erzeugt einen run_id und eine Zeile in sig_screen_runs
-- (mit kompletter Parameter-Konfig als JSON), plus pro Kandidat eine Zeile
-- in sig_screen_picks. Iterations-Tauglichkeit: Parameter-Snapshot pro Run
-- erlaubt Vergleich verschiedener Threshold-Konfigurationen ueber die Zeit.

CREATE TABLE IF NOT EXISTS sig_screen_runs (
    run_id           VARCHAR PRIMARY KEY,
    ts               TIMESTAMP NOT NULL,
    universe         VARCHAR,                    -- z.B. 'quality_universe'
    params_json      VARCHAR NOT NULL,           -- vollstaendige FilterConfig
    n_candidates     INTEGER,                    -- Universe-Groesse
    n_passed         INTEGER,                    -- nach Stufe 1
    notes            VARCHAR
);

CREATE TABLE IF NOT EXISTS sig_screen_picks (
    run_id               VARCHAR NOT NULL,
    ref_instrument_id    VARCHAR NOT NULL,
    rank                 INTEGER,
    symbol               VARCHAR,
    name                 VARCHAR,
    sector               VARCHAR,
    market_cap           DOUBLE,

    -- Achsen-Scores (0..1 = Anteil der bestandenen Kriterien je Achse).
    quality_score        DOUBLE,
    growth_score         DOUBLE,
    value_score          DOUBLE,
    composite_score      DOUBLE,
    hard_filter_passes   BOOLEAN,                -- alle Hard-Filter bestanden?

    -- Tuning-Transparenz: pro Kriterium {value, threshold, passed, axis}.
    criteria_detail_json VARCHAR,
    -- Stufe-2-Flags: revenue_accelerating, margin_expanding, etc.
    trend_flags_json     VARCHAR,
    -- Roh-Metriken (nuetzlich fuer Stage-3-Prompt).
    metrics_json         VARCHAR,

    notes                VARCHAR,
    created_at           TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (run_id, ref_instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_sig_screen_picks_rank
    ON sig_screen_picks(run_id, rank);

-- Stufe-3-Stub (Phase C2): LLM-Bewertung on-demand.
CREATE TABLE IF NOT EXISTS sig_screen_thesis (
    run_id              VARCHAR NOT NULL,
    ref_instrument_id   VARCHAR NOT NULL,
    ts                  TIMESTAMP NOT NULL,
    llm_model           VARCHAR,
    verdict             VARCHAR,                 -- 'BUY_CONVICTION'|'WATCH'|'PASS'
    growth_score_llm    DOUBLE,
    value_score_llm     DOUBLE,
    conviction_score    DOUBLE,
    thesis_text         VARCHAR,
    risks_json          VARCHAR,
    citations_json      VARCHAR,                 -- worauf stuetzt sich das?
    PRIMARY KEY (run_id, ref_instrument_id, ts)
);
