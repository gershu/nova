#!/usr/bin/env bash
# lab_allocation_daily.sh — taeglich von launchd auf nova-hub.
#
# Schedule: 23:10 UTC — nach setup-detection (23:00), vor dem
# Recommendation-Layer (23:15), damit Recommendations die frische
# Allokations-Drift nutzen koennen.
#
# Wertet config/allocation.yaml gegen die Ist-Allokation aus und
# schreibt Drift + Band-Status nach sig_allocation.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
exec python -m modules.allocation run
