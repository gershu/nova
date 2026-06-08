#!/usr/bin/env bash
# lab_filings.sh — Filing-Watcher-Producer, taeglich per launchd auf nova-hub.
#
# Erkennt neue SEC-Filings je Universums-Wert und legt LLM-Jobs an:
#   10-K/10-Q -> filing_change (GuV-Diff),  8-K -> filing_8k (Text-Summary).
# Bewusst SEPARAT vom 5-Minuten-Worker, weil sec-api-lastig (1 Lauf/Tag genug).
# Die eigentliche LLM-Inferenz erledigt danach der llm_worker (nova-w5).
# RW-DuckDB-Connections sind kurz (Schreib-Lock, modules.common.dblock).
#
# Initial-Setup: scripts/install_daemon.sh lab.filings
# Logs:          /Users/novaadm/Library/Logs/nova-lab-filings.log
#
# Erststart (Baseline, KEINE Jobs) einmalig manuell:
#   python -m modules.llm.jobs enqueue-filings --seed

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"

# --sleep entlastet die sec-api; ohne --all nur das analysierte Universum.
exec python -m modules.llm.jobs enqueue-filings --sleep 0.2
