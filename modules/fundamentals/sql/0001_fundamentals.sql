-- nova-lab Fundamentals-Schema, version 0001 (B+-Phase).
-- ref_* prefix (Reference / Stammdaten, slow-changing).
--
-- Persistiert Fundamentals-Snapshots pro Underlying — Value-Investor-Lens.
-- Source-agnostisch: yfinance ist heute primary, IB kann spaeter dazukommen
-- (Reuters Worldwide Fundamentals Subscription noetig — siehe ib_adapter.py).
--
-- Schreibt: modules.fundamentals (CLI + Wochen-Daemon).
-- Liest:    Notebooks 04+05, modules.screener_csp (Value-Filter).
--
-- Idempotent (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS ref_fundamentals_snapshot (
    ref_instrument_id   VARCHAR NOT NULL,
    ts                  DATE    NOT NULL,       -- Snapshot-Tag (= wann gefetcht)
    source              VARCHAR NOT NULL,       -- 'yfinance', 'ib', ...

    -- Identity / Classification
    sector              VARCHAR,
    industry            VARCHAR,
    country             VARCHAR,
    employees           INTEGER,
    market_cap          DOUBLE,                 -- in instrument-currency
    enterprise_value    DOUBLE,
    shares_outstanding  DOUBLE,

    -- Valuation
    pe_ttm              DOUBLE,                 -- trailing P/E
    pe_forward          DOUBLE,                 -- forward P/E (analyst-cons)
    pb                  DOUBLE,                 -- price/book
    ps_ttm              DOUBLE,                 -- price/sales TTM
    p_fcf               DOUBLE,                 -- price/free cashflow
    ev_ebitda           DOUBLE,
    ev_sales            DOUBLE,
    peg_ratio           DOUBLE,                 -- forward P/E / expected growth

    -- Quality (Profitability)
    roe                 DOUBLE,                 -- return on equity
    roa                 DOUBLE,                 -- return on assets
    roic                DOUBLE,                 -- return on invested capital
    gross_margin        DOUBLE,
    operating_margin    DOUBLE,
    net_margin          DOUBLE,
    fcf_margin          DOUBLE,                 -- FCF / revenue

    -- Solidity (Balance Sheet)
    debt_to_equity      DOUBLE,
    net_debt_to_ebitda  DOUBLE,
    current_ratio       DOUBLE,
    quick_ratio         DOUBLE,
    interest_coverage   DOUBLE,                 -- EBIT / interest expense

    -- Cashflow + Dividends
    fcf_yield           DOUBLE,                 -- FCF / market_cap
    dividend_yield      DOUBLE,
    payout_ratio        DOUBLE,                 -- dividends / net income
    dividend_per_share  DOUBLE,

    -- Growth (5-Jahres-CAGRs, aus Historic Financials)
    revenue_cagr_5y     DOUBLE,
    eps_cagr_5y         DOUBLE,
    fcf_cagr_5y         DOUBLE,
    dividend_cagr_5y    DOUBLE,

    -- Raw / Audit
    payload_json        VARCHAR,                -- adapter-spezifischer Rohdaten-Dump
    fetched_at          TIMESTAMP DEFAULT current_timestamp,
    run_id              VARCHAR,

    PRIMARY KEY (ref_instrument_id, ts, source)
);

CREATE INDEX IF NOT EXISTS idx_ref_fund_inst   ON ref_fundamentals_snapshot(ref_instrument_id);
CREATE INDEX IF NOT EXISTS idx_ref_fund_ts     ON ref_fundamentals_snapshot(ts);
CREATE INDEX IF NOT EXISTS idx_ref_fund_sector ON ref_fundamentals_snapshot(sector);

-- View: latest Snapshot pro instrument (alle sources gemerged, neueste ts gewinnt).
-- Source-Prio bei tie: 'ib' > 'yfinance' > rest — manuell setzbar via ORDER BY.
CREATE OR REPLACE VIEW ref_fundamentals_latest AS
WITH ranked AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY ref_instrument_id
               ORDER BY ts DESC,
                        CASE source WHEN 'ib' THEN 1
                                    WHEN 'yfinance' THEN 2
                                    ELSE 9 END
           ) AS rk
    FROM ref_fundamentals_snapshot
)
SELECT * EXCLUDE (rk) FROM ranked WHERE rk = 1;
