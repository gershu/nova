-- nova-lab: Filing-Change-Watcher (zweiter LLM-Job-Typ 'filing_change').
--
-- ref_filing_seen: je (Wert, Form) das zuletzt gesehene Filing — verhindert
-- Doppel-Jobs und ermoeglicht den Baseline-Seed (enqueue-filings --seed).
-- ref_filing_change: die LLM-Zusammenfassung der Veraenderung des neuesten
-- Filings ggue. der Vorperiode (Umsatz/Margen-Deltas + Einordnung).

CREATE TABLE IF NOT EXISTS ref_filing_seen (
    ref_instrument_id  VARCHAR,
    form               VARCHAR,            -- '10-K' | '10-Q' | '8-K'
    last_accession     VARCHAR,
    last_period        VARCHAR,
    seen_at            TIMESTAMP NOT NULL,
    PRIMARY KEY (ref_instrument_id, form)
);

CREATE TABLE IF NOT EXISTS ref_filing_change (
    ref_instrument_id  VARCHAR,
    symbol             VARCHAR,
    form               VARCHAR,
    accession          VARCHAR,
    period             VARCHAR,
    prior_period       VARCHAR,
    summary            VARCHAR,            -- 2-3 Saetze, was sich geaendert hat
    impact             VARCHAR,            -- positiv | neutral | negativ | n/a
    deltas_json        VARCHAR,            -- Roh-Deltas (Umsatz/Margen)
    model              VARCHAR,
    generated_at       TIMESTAMP NOT NULL,
    PRIMARY KEY (ref_instrument_id, form, accession)
);

CREATE INDEX IF NOT EXISTS idx_ref_filing_change_ts
    ON ref_filing_change(generated_at);
