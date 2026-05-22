#!/usr/bin/env bash
# lab_setup_daily.sh — taeglich von launchd auf nova-hub.
#
# Schedule: 23:00 UTC — nach market_monitor (22:45) + den Equity-Ingest-Steps,
# damit die Setup-Detection auf frischen Markt- + Portfolio-Daten arbeitet.
#
# Wertet config/setups.yaml gegen den DB-Stand aus, schreibt aktive Setups
# nach sig_market_setups.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
exec python -m modules.setup run
