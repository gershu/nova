-- nova-lab: vorberechnete GuruFocus-Qualitaets-Kennzahlen je Universums-Wert.
-- Ersetzt den hauseigenen Shearn-Score (ref_quality_score, sec-api-basiert).
-- Befuellt vom Batch `python -m modules.gurufocus ingest-scores`.

CREATE TABLE IF NOT EXISTS ref_gf_score (
    ref_instrument_id        VARCHAR PRIMARY KEY,
    symbol                   VARCHAR,
    name                     VARCHAR,
    sector                   VARCHAR,
    gf_score                 DOUBLE,     -- 0..100 (Gesamt)
    gf_value                 DOUBLE,     -- intrinsischer Wert (GF)
    price_to_gf_value        DOUBLE,     -- Kurs / GF-Value (>1 = ueberbewertet)
    gf_valuation             VARCHAR,    -- Text-Einordnung
    rank_financial_strength  DOUBLE,
    rank_profitability       DOUBLE,
    rank_growth              DOUBLE,
    rank_balancesheet        DOUBLE,
    predictability           DOUBLE,     -- 0..5
    fscore                   DOUBLE,     -- Piotroski 0..9
    zscore                   DOUBLE,     -- Altman
    mscore                   DOUBLE,     -- Beneish
    moat_score               DOUBLE,
    roic                     DOUBLE,     -- %
    wacc                     DOUBLE,     -- %
    error                    VARCHAR,
    computed_at              TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_gf_score ON ref_gf_score(gf_score);
