-- nova — Decision-Journal Schema.
--
-- sig_decision_journal: schliesst den Feedback-Loop zwischen dem, was der
-- Recommendation-Layer vorgeschlagen hat (sig_recommendations), und dem,
-- was tatsaechlich entschieden + gehandelt wurde (pos_trades).
--
-- Genau 0..1 Journal-Eintrag pro Recommendation. Der Eintrag referenziert
-- die Recommendation ueber deren PK (ts, model, rec_id) und snapshottet
-- zugleich action/symbol/title — so bleibt er lesbar, selbst wenn
-- sig_recommendations am selben Tag neu laeuft (DELETE + re-INSERT).
--
-- Drei Ebenen pro Eintrag:
--   1. Entscheidung  — status + rationale + verknuepfte Trades
--   2. Outcome       — wie ist die Entscheidung ausgegangen (spaeter erfasst)
-- Human-in-loop: das Journal wird kuratiert, kein Daemon schreibt hier.

CREATE TABLE IF NOT EXISTS sig_decision_journal (
    -- FK -> sig_recommendations (ts, model, rec_id)
    rec_ts              DATE NOT NULL,
    rec_model           VARCHAR NOT NULL,
    rec_id              INTEGER NOT NULL,

    -- Snapshot der Recommendation (stabil ggü. Re-Run von sig_recommendations)
    rec_action          VARCHAR,
    rec_symbol          VARCHAR,
    rec_title           VARCHAR,

    -- Entscheidung
    status              VARCHAR NOT NULL DEFAULT 'pending',
                          -- 'pending'|'acted_full'|'acted_partial'|'declined'|'expired'
    decided_at          DATE,
    rationale           VARCHAR,            -- warum so entschieden (Anleger-Notiz)
    linked_trades       VARCHAR,            -- JSON-Array von {ref_instrument_id, broker, trade_lot}

    -- Outcome (spaeter erfasst, NULL = noch nicht bewertet)
    outcome             VARCHAR,            -- 'good'|'neutral'|'poor'
    outcome_pnl_eur     DOUBLE,
    outcome_note        VARCHAR,
    outcome_assessed_at DATE,

    created_at          TIMESTAMP DEFAULT current_timestamp,
    updated_at          TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (rec_ts, rec_model, rec_id)
);

CREATE INDEX IF NOT EXISTS idx_sig_decision_journal_status  ON sig_decision_journal(status);
CREATE INDEX IF NOT EXISTS idx_sig_decision_journal_outcome ON sig_decision_journal(outcome);
