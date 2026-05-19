-- nova-lab portfolio_core — Drop-Legacy-Migration.
--
-- Entfernt die alte 3-Layer-View-Schicht (atomic/composed/reports) +
-- Canonical-Layer.
--
-- Idempotent (IF EXISTS). Sicher mehrfach ausfuehrbar.
--
-- WICHTIG: vor diesem Run muessen alle Konsumenten umgestellt sein
-- (Dashboard, Obsidian, Briefings). Siehe modules/portfolio_core/__main__.py
-- drop-legacy fuer Pre-Flight-Check.


-- =========================================================
-- 1) View-Layer 3 (Reports)
-- =========================================================
DROP VIEW IF EXISTS v_report_portfolio_eur;
DROP VIEW IF EXISTS v_report_portfolio_usd;
DROP VIEW IF EXISTS v_report_portfolio_gbp;
DROP VIEW IF EXISTS v_report_portfolio_chf;
DROP VIEW IF EXISTS v_report_by_sector_eur;
DROP VIEW IF EXISTS v_report_by_sector_usd;
DROP VIEW IF EXISTS v_report_holdings_per_view;


-- =========================================================
-- 2) View-Layer 2 (Composed)
-- =========================================================
DROP VIEW IF EXISTS v_holdings_mtm_eur;
DROP VIEW IF EXISTS v_holdings_mtm_usd;
DROP VIEW IF EXISTS v_holdings_mtm_gbp;
DROP VIEW IF EXISTS v_holdings_mtm_chf;
DROP VIEW IF EXISTS v_holdings_mtm;
DROP VIEW IF EXISTS v_holdings_enriched;


-- =========================================================
-- 3) View-Layer 1 (Atomic — die NICHT in portfolio_core uebernommen werden)
-- =========================================================
DROP VIEW IF EXISTS v_fx_all;
DROP VIEW IF EXISTS v_latest_fx_self;


-- =========================================================
-- 4) Portfolio-Views Helper-Views (aus altem portfolio_views-Modul)
-- =========================================================
DROP VIEW IF EXISTS v_portfolio_view_members;
DROP VIEW IF EXISTS v_instrument_view_tags;


-- =========================================================
-- 5) Canonical-Layer komplett (Views + Tabellen)
-- =========================================================
DROP VIEW IF EXISTS v_canonical_map;
DROP VIEW IF EXISTS v_instrument_aggregate_id;
DROP TABLE IF EXISTS ref_instrument_canonical_members;
DROP TABLE IF EXISTS ref_instrument_canonicals;
