-- nova-lab DuckDB schema, version 0003 — Earnings-Calendar.
-- ref_* prefix (Stammdaten / Reference).
-- Idempotent.
--
-- Mehrere Eintraege pro Instrument (nahe + zukuenftige Quartale).
-- Quelle: yfinance.calendar (MVP), spaeter ggf. IB Fundamentals.

CREATE TABLE IF NOT EXISTS ref_earnings_calendar (
    ref_instrument_id  VARCHAR NOT NULL,
    earnings_date      DATE NOT NULL,
    source             VARCHAR NOT NULL DEFAULT 'yfinance',
    fetched_at         TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ref_instrument_id, earnings_date)
);

CREATE INDEX IF NOT EXISTS idx_earnings_inst    ON ref_earnings_calendar(ref_instrument_id);
CREATE INDEX IF NOT EXISTS idx_earnings_date    ON ref_earnings_calendar(earnings_date);
