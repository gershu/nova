#!/usr/bin/env bash
# lab_llm_worker.sh — haeufig von launchd (StartInterval) auf nova-hub.
#
# Enqueued faellige Q-Score-Narrative (aus ref_quality_score) und drainiert
# die LLM-Job-Queue EINMAL (worker --once). RW-DuckDB-Connections sind kurz
# (Schreib-Lock, modules.common.dblock) -> Dashboard-Reads + Batches werden
# nicht blockiert. Die LLM-Inferenz laeuft auf nova-w5 (per HTTP), nicht hier.
#
# Initial-Setup: scripts/install_daemon.sh lab.llm_worker
# Logs:          /Users/novaadm/Library/Logs/nova-lab-llm-worker.log

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"

# Drainiert die Queue (aktiver Job-Typ: portfolio_digest; Producer ist der
# woechentliche lab.portfolio_digest-Daemon). --max begrenzt die Arbeit/Lauf.
exec python -m modules.llm.jobs worker --once --max 50
