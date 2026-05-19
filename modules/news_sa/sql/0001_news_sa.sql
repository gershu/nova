-- nova-lab Seeking-Alpha-News-Schema, version 0001.
-- ref_* prefix (Reference / Stammdaten — slow-changing).
--
-- Persistiert die Headlines+Summaries von Seeking-Alpha-Email-Alerts.
-- Volltext NICHT gespeichert (Stefans Wahl: 'Summary + URL reicht').
--
-- Quelle: Gmail-Label 'nova-sa' -> IMAP-Pull (modules/news_sa).
-- Konsument: modules.screener_value.llm_brief + modules.llm.alert_explainer.
--
-- WICHTIG fuer LLM-Konsum: SA-Inhalte sind EDITORIAL OPINION (thesis-driven),
-- nicht neutrale News. Konsumenten muessen das im Prompt explizit ausweisen
-- damit das LLM die Argumente faktisch wiedergibt statt zu uebernehmen.
--
-- Idempotent (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS ref_sa_articles (
    article_id     VARCHAR PRIMARY KEY,         -- hash(message_id) — stable across re-runs
    source         VARCHAR NOT NULL DEFAULT 'seekingalpha',
    ts             TIMESTAMP NOT NULL,          -- email-Date (UTC)
    title          VARCHAR NOT NULL,
    summary        VARCHAR,                     -- erste Absaetze, gestrippter HTML
    url            VARCHAR,                     -- canonical link auf seekingalpha.com

    -- IMAP-Audit
    imap_uid       VARCHAR,                     -- Gmail-UID, fuer Debugging
    raw_subject    VARCHAR,
    raw_from       VARCHAR,

    fetched_at     TIMESTAMP DEFAULT current_timestamp,
    run_id         VARCHAR
);

-- M:N — ein Artikel kann mehrere Symbole adressieren ("Top 5 Banks to watch").
CREATE TABLE IF NOT EXISTS ref_sa_article_symbols (
    article_id         VARCHAR NOT NULL,        -- FK -> ref_sa_articles
    ref_instrument_id  VARCHAR NOT NULL,        -- FK -> ref_instruments (LOGISCH, kein constraint)
    extracted_from     VARCHAR,                 -- 'subject' | 'body' | 'manual'
    confidence         DOUBLE DEFAULT 1.0,      -- 1.0 = sicher aus Pattern, < bei Heuristik
    PRIMARY KEY (article_id, ref_instrument_id)
);

CREATE INDEX IF NOT EXISTS idx_ref_sa_articles_ts        ON ref_sa_articles(ts);
CREATE INDEX IF NOT EXISTS idx_ref_sa_article_syms_inst  ON ref_sa_article_symbols(ref_instrument_id);

-- Audit-Trail: jeder IMAP-Pull-Run.
CREATE TABLE IF NOT EXISTS audit_news_sa_runs (
    run_id            VARCHAR PRIMARY KEY,
    started_at        TIMESTAMP NOT NULL,
    completed_at      TIMESTAMP,
    mails_seen        INTEGER,                  -- in nova-sa label gefunden
    mails_parsed      INTEGER,                  -- erfolgreich -> ref_sa_articles
    mails_moved       INTEGER,                  -- in nova-sa/processed verschoben
    mails_failed      INTEGER,
    status            VARCHAR,                  -- 'running', 'success', 'failed', 'partial'
    error_msg         VARCHAR
);
