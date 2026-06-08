-- nova-lab: Portfolio-Digest (dritter Ergebnis-Typ, Job-Kind 'portfolio_digest').
--
-- Je offener Portfolio-Position ein kurzer, vorberechneter Wochenueberblick:
-- der Producer (enqueue-digest) sammelt die Inputs (Q-Score, juengste
-- Filing-Aenderung, Red-Flag) und legt sie in die Job-Payload; der Worker
-- erzeugt den Text per LLM (lock-frei) und schreibt ihn hier. Eine Zeile je
-- Wert (zuletzt erzeugter Stand). Nutzt die sonst idle LLM kontinuierlich.

CREATE TABLE IF NOT EXISTS ref_portfolio_digest (
    ref_instrument_id  VARCHAR PRIMARY KEY,
    symbol             VARCHAR,
    digest             VARCHAR,        -- 3-4 Saetze Wochenueberblick
    score              INTEGER,        -- Q-Score-Stand zum Digest (oder NULL)
    input_hash         VARCHAR,        -- Skip, wenn unveraendert
    model              VARCHAR,
    generated_at       TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_portfolio_digest_ts
    ON ref_portfolio_digest(generated_at);
