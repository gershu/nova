-- nova-lab sec_filings — Income-Statement-Kerndaten aus SEC-EDGAR.
--
-- ref_income_statement haelt pro (Instrument, Berichtsperiode) die
-- GuV-Kernzeilen in Berichtswaehrung. Eine Zeile = ein 10-Q/10-K-Filing.
--
-- Bewusst getrennt von ref_fundamentals_latest: dort stehen abgeleitete
-- Kennzahlen + Margen als Momentaufnahme; hier die absoluten GuV-Betraege
-- je Periode — die Rohbasis fuer das GuV-Sankey.
--
-- Quelle: sec-api.io XBRL-to-JSON. Betraege wie im Filing berichtet
-- (i.d.R. USD). 'other_income' = pretax_income - operating_income, also
-- der Saldo aus Finanz-/Beteiligungsergebnis und Sonstigem.

CREATE TABLE IF NOT EXISTS ref_income_statement (
    ref_instrument_id  VARCHAR NOT NULL,
    period_end         DATE    NOT NULL,      -- periodOfReport des Filings
    form_type          VARCHAR,               -- '10-Q' | '10-K'
    fiscal_period      VARCHAR,               -- frei: 'Q1 2026' o.ae.
    accession_no       VARCHAR,               -- SEC-Accession des Filings
    filed_at           TIMESTAMP,             -- Einreichungs-Zeitpunkt
    period_months      INTEGER,               -- Dauer der Berichtsperiode (3|12)
    currency           VARCHAR DEFAULT 'USD',

    revenue            DOUBLE,
    cost_of_revenue    DOUBLE,
    gross_profit       DOUBLE,
    rd_expense         DOUBLE,                -- Forschung & Entwicklung
    sga_expense        DOUBLE,                -- Vertrieb, Verwaltung, Allg.
    operating_expense  DOUBLE,                -- Summe Betriebsaufwand
    operating_income   DOUBLE,
    other_income       DOUBLE,                -- pretax - operating
    pretax_income      DOUBLE,
    tax_expense        DOUBLE,
    net_income         DOUBLE,

    source             VARCHAR DEFAULT 'sec-api.io',
    fetched_at         TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ref_instrument_id, period_end)
);

CREATE INDEX IF NOT EXISTS idx_ref_income_statement_period
    ON ref_income_statement(period_end);

CREATE TABLE IF NOT EXISTS audit_sec_filings_runs (
    run_id          VARCHAR PRIMARY KEY,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    instrument_count INTEGER,                 -- wieviele Namen versucht
    rows_upserted   INTEGER,
    rows_skipped    INTEGER,                  -- kein Filing / nicht US-gelistet
    status          VARCHAR,                  -- 'ok'|'partial'|'fail'
    error_msg       VARCHAR
);
