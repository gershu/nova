-- nova-lab DuckDB schema, version 0001 (B-Phase consolidated).
--
-- Tabellen-Kategorien via Prefix:
--   ref_*    Reference / Stammdaten     (slow-changing, instrument master)
--   mkt_*    Market Data / Time-Series  (Kursdaten, FX-Rates spaeter)
--   pos_*    Positions / Portfolio      (in modules/portfolio/sql/)
--   sig_*    Signals / Events           (in modules/monitor/sql/, kommt spaeter)
--   audit_*  Technical / Process Logs
--
-- ref_instrument_id Format:  '{SOURCE}:{SYMBOL}:{CURRENCY}'  (uppercase)
--   Beispiele:  'IB:AAPL:USD', 'IB:SAP:EUR', 'IB:IBCID654613619:NOK'
--   Deterministisch ueber Re-Imports — siehe import_xlsx.make_ref_instrument_id.
--
-- Naming-Konvention: Englische Standard-Terminologie an IB API + Bloomberg.
-- Idempotent: alles mit IF NOT EXISTS.
-- Keine FK-Constraints in dieser Phase — kommt spaeter.

-- ============================================================
-- Reference Layer
-- ============================================================

CREATE TABLE IF NOT EXISTS ref_instruments (
    ref_instrument_id  VARCHAR PRIMARY KEY,    -- '{SOURCE}:{SYMBOL}:{CURRENCY}'
    con_id             INTEGER UNIQUE,         -- IB Contract ID, optional fuer non-IB
    isin               VARCHAR,
    symbol             VARCHAR NOT NULL,       -- IB localSymbol (oder yfinance ticker je nach preferred_source)
    currency           VARCHAR NOT NULL,
    preferred_source   VARCHAR NOT NULL DEFAULT 'IB',
    name               VARCHAR,
    asset_type         VARCHAR,                -- 'stock', 'etf', 'bond', 'option', 'fund', 'fx', 'crypto', ...
    exchange           VARCHAR,                -- IB primaryExchange (XETRA, NASDAQ, ARCA, ...)
    active             BOOLEAN DEFAULT true,
    notes              VARCHAR,
    created_at         TIMESTAMP DEFAULT current_timestamp,
    updated_at         TIMESTAMP DEFAULT current_timestamp
);

-- ============================================================
-- Market Data Layer (Time-Series)
-- ============================================================

-- mkt_quotes_daily: EOD pro instrument + source. Multi-Source-Coexistenz via PK.
-- 'source' (data provider) ist NICHT zwingend = ref_instruments.preferred_source —
-- das ist absichtlich: yfinance kann als Fallback fuer ein IB-Instrument quotes liefern.
CREATE TABLE IF NOT EXISTS mkt_quotes_daily (
    ref_instrument_id  VARCHAR NOT NULL,
    ts                 DATE NOT NULL,
    open               DOUBLE,
    high               DOUBLE,
    low                DOUBLE,
    close              DOUBLE,
    adj_close          DOUBLE,                 -- yfinance liefert das, IB nicht (= close)
    volume             BIGINT,
    source             VARCHAR NOT NULL,       -- 'ib', 'yfinance', 'alpha_vantage', ...
    fetched_at         TIMESTAMP DEFAULT current_timestamp,
    run_id             VARCHAR,                -- audit_ingest_runs.run_id
    PRIMARY KEY (ref_instrument_id, ts, source)
);

CREATE TABLE IF NOT EXISTS mkt_quotes_intraday (
    ref_instrument_id  VARCHAR NOT NULL,
    ts                 TIMESTAMP NOT NULL,     -- bevorzugt UTC
    open               DOUBLE,
    high               DOUBLE,
    low                DOUBLE,
    close              DOUBLE,
    volume             BIGINT,
    interval_s         INTEGER NOT NULL,       -- 60, 300, 900, ...
    source             VARCHAR NOT NULL,
    fetched_at         TIMESTAMP DEFAULT current_timestamp,
    run_id             VARCHAR,
    PRIMARY KEY (ref_instrument_id, ts, interval_s, source)
);

-- mkt_fx_daily: FX-Rates pro Currency-Pair + Tag + Source.
-- Konvention: rate = wieviel currency_to fuer 1 currency_from.
-- z.B. (currency_from='EUR', currency_to='USD', rate=1.08) = "1 EUR = 1.08 USD"
-- Symmetrische Speicherung: jeder Fetch schreibt auch die invertierte Richtung
-- — vereinfacht downstream-Joins, kein 1/rate noetig.
CREATE TABLE IF NOT EXISTS mkt_fx_daily (
    currency_from  VARCHAR NOT NULL,           -- ISO-4217: 'EUR', 'USD', 'NOK', ...
    currency_to    VARCHAR NOT NULL,           -- ISO-4217
    ts             DATE NOT NULL,
    rate           DOUBLE NOT NULL,            -- 1 currency_from = rate currency_to
    source         VARCHAR NOT NULL,           -- 'yfinance', 'ib', 'ecb'
    fetched_at     TIMESTAMP DEFAULT current_timestamp,
    run_id         VARCHAR,
    PRIMARY KEY (currency_from, currency_to, ts, source)
);

CREATE INDEX IF NOT EXISTS idx_mkt_fx_daily_pair ON mkt_fx_daily(currency_from, currency_to);

-- ============================================================
-- Audit Layer
-- ============================================================

CREATE TABLE IF NOT EXISTS audit_ingest_runs (
    run_id           VARCHAR PRIMARY KEY,      -- nova job_id (NOVA_JOB_ID env)
    source           VARCHAR NOT NULL,
    started_at       TIMESTAMP NOT NULL,
    completed_at     TIMESTAMP,
    instruments_req  INTEGER,                  -- wieviele Instrumente angefordert
    rows_added       INTEGER DEFAULT 0,
    status           VARCHAR,                  -- 'running', 'success', 'failed', 'partial'
    error_msg        VARCHAR
);

-- ============================================================
-- Indizes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_ref_inst_con_id   ON ref_instruments(con_id);
CREATE INDEX IF NOT EXISTS idx_ref_inst_isin     ON ref_instruments(isin);
CREATE INDEX IF NOT EXISTS idx_ref_inst_symbol   ON ref_instruments(symbol);
CREATE INDEX IF NOT EXISTS idx_ref_inst_active   ON ref_instruments(active);
