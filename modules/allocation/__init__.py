"""nova — Allokations-Monitoring.

Stellt die Ist-Allokation (aus v_mkt_holdings, je Klasse aggregiert) gegen
die Ziel-Baender aus config/allocation.yaml und schreibt die Drift nach
sig_allocation. Policy-Anker ist das Investment Policy Statement
(docs/investment_policy_statement.md).
"""
