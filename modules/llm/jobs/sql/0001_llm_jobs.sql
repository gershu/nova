-- nova-lab: minimale LLM-Arbeitsschlange + erstes Ergebnis-Ziel.
--
-- llm_jobs ist eine persistente Queue: Producer (Batches, Events) legen Jobs
-- ab, ein Always-On-Worker (python -m modules.llm.jobs worker) drainiert sie
-- seriell ueber die lokale LLM. So wird die ansonsten stossweise LLM-Nutzung
-- zu einer kontinuierlichen, vorberechnenden Hintergrundlast (Idle-faehig).
--
-- ref_quality_narrative ist das erste Ergebnis: je Universums-Wert eine
-- LLM-Synthese zum vorberechneten Gesamt-Qualitaets-Score (Tabelle
-- ref_quality_score) — „warum dieser Score" + groesstes Red Flag.

CREATE TABLE IF NOT EXISTS llm_jobs (
    job_id             VARCHAR PRIMARY KEY,
    kind               VARCHAR NOT NULL,        -- z.B. 'quality_narrative'
    ref_instrument_id  VARCHAR,
    payload_json       VARCHAR,
    priority           INTEGER DEFAULT 100,     -- kleiner = wichtiger
    input_hash         VARCHAR,                 -- fuer Staleness/Dedupe
    status             VARCHAR DEFAULT 'pending', -- pending|running|done|error
    result             VARCHAR,
    error              VARCHAR,
    attempts           INTEGER DEFAULT 0,
    created_at         TIMESTAMP NOT NULL,
    updated_at         TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_jobs_queue
    ON llm_jobs(status, priority, created_at);


CREATE TABLE IF NOT EXISTS ref_quality_narrative (
    ref_instrument_id  VARCHAR PRIMARY KEY,
    symbol             VARCHAR,
    score              INTEGER,        -- Score, fuer den die Synthese gilt
    narrative          VARCHAR,        -- 2-3 Saetze „warum dieser Score"
    red_flag           VARCHAR,        -- groesstes Risiko (1 Satz) oder leer
    model              VARCHAR,
    input_hash         VARCHAR,        -- = Hash aus score + Teil-Scores
    generated_at       TIMESTAMP NOT NULL
);
