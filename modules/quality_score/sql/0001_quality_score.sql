-- nova-lab: vorberechneter Gesamt-Qualitaets-Score (Shearn-5-Themen) je
-- Universums-Wert. Befuellt vom Batch `python -m modules.quality_score run`
-- (nutzt modules.dashboard.quality.overall_score). Eine Zeile je
-- ref_instrument_id (jeweils der zuletzt berechnete Stand).
--
-- score 0..100 (gewichteter Anteil erfuellter Kriterien, fehlende Themen
-- renormiert) oder NULL, wenn keine Themen auswertbar waren. sub_* sind die
-- Teil-Scores 0..1 je Thema (NULL = kein Datum). Konsumiert vom Screener-
-- Dashboard als „Q-Score"-Spalte/Filter (ganzes Universum, sofort).

CREATE TABLE IF NOT EXISTS ref_quality_score (
    ref_instrument_id      VARCHAR PRIMARY KEY,
    symbol                 VARCHAR,
    score                  INTEGER,        -- 0..100 oder NULL
    n_ok                   INTEGER,        -- auswertbare Themen (0..5)
    sub_return_on_capital  DOUBLE,
    sub_balance_sheet      DOUBLE,
    sub_stock_based_comp   DOUBLE,
    sub_gaap_vs_non_gaap   DOUBLE,
    sub_insider            DOUBLE,
    n_years                INTEGER,        -- Lookback der Berechnung
    period                 VARCHAR,        -- 'annual' | 'quarterly'
    error                  VARCHAR,        -- gesetzt, wenn Berechnung scheiterte
    computed_at            TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_quality_score
    ON ref_quality_score(score);
