-- nova-lab: 8-K-Klassifikation. Strukturierter Ereignistyp je Filing-Change.
--
-- Fuer 8-K leitet der filing_8k-Handler eine Kategorie ab (primaer
-- deterministisch aus den 8-K-Item-Codes, LLM als Fallback) und schreibt sie
-- hier. 10-K/10-Q (GuV-Diff) lassen das Feld NULL. Idempotent — laeuft bei
-- jedem apply_schema mit.

ALTER TABLE ref_filing_change ADD COLUMN IF NOT EXISTS event_type VARCHAR;
