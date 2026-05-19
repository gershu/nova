-- Initial-Seed fuer symbols-Tabelle.
-- Idempotent via INSERT OR IGNORE — bei Re-Run werden bestehende
-- Eintraege nicht ueberschrieben.
--
-- Ausfuehren auf hub:
--   duckdb ~/nova_data/lab.duckdb < ~/nova-lab/modules/ingest/sql/seed_symbols.sql

INSERT OR IGNORE INTO symbols (symbol, name, asset_type, currency, exchange, active, notes) VALUES
    -- ETFs
    ('SPY',   'SPDR S&P 500 ETF Trust',           'etf',   'USD', 'NYSE',   true, 'Major US Index ETF'),

    -- Tech (Major)
    ('AAPL',  'Apple Inc.',                       'stock', 'USD', 'NASDAQ', true, 'csp_scanner Watchlist'),
    ('NVDA',  'NVIDIA Corporation',               'stock', 'USD', 'NASDAQ', true, 'csp_scanner Watchlist'),
    ('PLTR',  'Palantir Technologies Inc.',       'stock', 'USD', 'NASDAQ', true, 'csp_scanner Watchlist'),
    ('META',  'Meta Platforms Inc.',              'stock', 'USD', 'NASDAQ', true, ''),
    ('GOOGL', 'Alphabet Inc. (Class A)',          'stock', 'USD', 'NASDAQ', true, ''),
    ('MSFT',  'Microsoft Corporation',            'stock', 'USD', 'NASDAQ', true, ''),
    ('AMZN',  'Amazon.com Inc.',                  'stock', 'USD', 'NASDAQ', true, '');
