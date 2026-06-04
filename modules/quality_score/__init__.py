"""nova-lab: Batch-Vorberechnung des Gesamt-Qualitaets-Scores (Shearn-5-
Themen) je Universums-Wert -> Tabelle ref_quality_score.

Single Source der Score-Logik bleibt modules.dashboard.quality; dieses Paket
ist nur Orchestrierung (Universum laden, je Wert rechnen, persistieren) als
nachtfaehiger Batch. CLI: `python -m modules.quality_score run`.
"""
