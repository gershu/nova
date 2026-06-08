"""nova-lab LLM-Job-Queue (minimal).

Verwandelt die stossweise LLM-Nutzung in einen kontinuierlichen
Hintergrund-Worker: Producer enqueuen Jobs in llm_jobs, ein Always-On-Worker
(`python -m modules.llm.jobs worker`) drainiert sie seriell ueber die lokale
LLM (modules.llm.client.OllamaClient).

Erster Job-Typ: 'quality_narrative' — LLM-Synthese zum vorberechneten
Gesamt-Qualitaets-Score (ref_quality_score -> ref_quality_narrative).
"""
