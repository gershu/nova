-- nova-lab sec_filings — Umsatz-Segmente aus SEC-EDGAR-Filings.
--
-- Eine GuV-Periode enthaelt typischerweise MEHRERE Aufschluesselungen des
-- Umsatzes — je nach Unternehmen z.B. nach Reportable Segments, nach
-- Produkt/Service-Linie, nach Geografie. Wir speichern alle gefundenen
-- Achsen, der Sankey-Tab waehlt visuell aus.
--
-- Schluessel: (Instrument, Periode, Achse, Member) — d.h. eine Achse darf
-- je Periode jeden Member nur einmal liefern; verschiedene Achsen sind
-- nebeneinander erlaubt.
--
-- 'axis' / 'member' sind die rohen XBRL-Bezeichner (z.B.
-- 'us-gaap:StatementBusinessSegmentsAxis' / 'nvda:GraphicsSegmentMember');
-- 'member_label' ist der humanisierte Anzeige-Name (z.B. 'Graphics').

CREATE TABLE IF NOT EXISTS ref_revenue_segments (
    ref_instrument_id  VARCHAR NOT NULL,
    period_end         DATE    NOT NULL,
    axis               VARCHAR NOT NULL,
    member             VARCHAR NOT NULL,
    member_label       VARCHAR,
    value              DOUBLE  NOT NULL,
    currency           VARCHAR DEFAULT 'USD',
    source             VARCHAR DEFAULT 'sec-api.io',
    fetched_at         TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (ref_instrument_id, period_end, axis, member)
);

CREATE INDEX IF NOT EXISTS idx_ref_revenue_segments_period
    ON ref_revenue_segments(period_end);
