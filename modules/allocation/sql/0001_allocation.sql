-- nova — Allokations-Monitoring Schema.
--
-- sig_allocation: eine Row pro (ts, asset_class). Der Allokations-Lauf
-- aggregiert v_mkt_holdings je Klasse (Zuordnung aus
-- config/instrument_classes.yaml), stellt das Ist gegen die Ziel-Baender
-- aus config/allocation.yaml und schreibt Drift + Band-Status.
--
-- Die Pseudo-Klasse 'unclassified' fasst Holdings ohne Klassen-Eintrag —
-- target/min/max sind dann NULL, band_status = 'unclassified'.
--
-- run_id ist Audit-Metadata (nicht im PK) — Re-Run am selben Tag
-- ueberschreibt via INSERT OR REPLACE.

CREATE TABLE IF NOT EXISTS sig_allocation (
    ts           DATE NOT NULL,            -- Auswertungs-Datum
    asset_class  VARCHAR NOT NULL,         -- Klasse aus allocation.yaml | 'unclassified'
    label        VARCHAR,                  -- Anzeige-Label
    target_pct   DOUBLE,                   -- Zielwert (NULL fuer unclassified)
    min_pct      DOUBLE,                   -- unteres Toleranzband
    max_pct      DOUBLE,                   -- oberes Toleranzband
    actual_pct   DOUBLE,                   -- Ist-Anteil am Portfolio
    actual_eur   DOUBLE,                   -- Ist-Wert EUR
    drift_pct    DOUBLE,                   -- actual_pct - target_pct
    band_status  VARCHAR NOT NULL,         -- 'within' | 'below' | 'above' | 'unclassified'
    run_id       VARCHAR NOT NULL,
    created_at   TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ts, asset_class)
);

CREATE INDEX IF NOT EXISTS idx_sig_allocation_ts     ON sig_allocation(ts);
CREATE INDEX IF NOT EXISTS idx_sig_allocation_status ON sig_allocation(band_status);
