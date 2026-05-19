-- nova-lab fred_ingest — Initialer Default-Series-Seed.
-- Idempotent (INSERT OR IGNORE).
-- Stefan kann via `python -m modules.fred_ingest add-series ...` weitere
-- adden.

INSERT OR IGNORE INTO ref_economic_series
    (series_id, name, description, category, units, frequency, source, notes)
VALUES
    ('VIXCLS',
     'CBOE Volatility Index',
     'Implizite 30d-Vola des S&P 500 — "Fear Index"',
     'volatility', 'index', 'daily', 'fred',
     'Stress > 25, Panic > 40'),

    ('T10Y2Y',
     '10Y-2Y Treasury Spread',
     'Differenz 10Y minus 2Y US-Treasury — Yield-Curve',
     'rates', 'percent', 'daily', 'fred',
     'Invertiert (< 0) ist klassischer Recession-Leading-Indikator'),

    ('DGS10',
     '10Y Treasury Constant Maturity',
     'Rendite 10-jaehrige US-Staatsanleihe — Risk-Free-Rate-Proxy',
     'rates', 'percent', 'daily', 'fred', NULL),

    ('BAMLH0A0HYM2',
     'ICE BofA US HY Index Option-Adj Spread',
     'Credit-Spread US-High-Yield vs Treasury',
     'credit', 'percent', 'daily', 'fred',
     'Stress > 5%, Panic > 8%'),

    ('DCOILWTICO',
     'WTI Crude Oil Spot Price',
     'Inflation- + Wachstums-Proxy',
     'commodity', 'usd', 'daily', 'fred', NULL),

    ('DTWEXBGS',
     'USD Broad Index (Nominal)',
     'Trade-weighted USD-Index gegen breiten Korb',
     'fx', 'index', 'daily', 'fred',
     'Hoher Wert = starker USD = FX-Stress fuer EM');
