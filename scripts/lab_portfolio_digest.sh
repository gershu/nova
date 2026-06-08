#!/usr/bin/env bash
# lab_portfolio_digest.sh — Portfolio-Digest-Producer, woechentlich per launchd.
#
# Legt je offener Portfolio-Position einen portfolio_digest-Job an (Q-Score +
# juengste Filing-Aenderung + Red-Flag). Die LLM-Synthese erledigt danach der
# llm_worker (alle 5 Minuten) auf nova-w5. Producer macht keine LLM-Calls und
# nur kurze DuckDB-Schreibzugriffe (Schreib-Lock).
#
# Eigenstaendig vom bestehenden lab.digest (taeglicher Markdown-Digest 23:30).
#
# Initial-Setup: scripts/install_daemon.sh lab.portfolio_digest
# Logs:          /Users/novaadm/Library/Logs/nova-lab-portfolio-digest.log

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"

exec python -m modules.llm.jobs enqueue-digest
