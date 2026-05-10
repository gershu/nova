#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.watchlist CLI.
#
# Usage:
#   ~/nova/scripts/nova_run.sh lab_watchlist nova-hub init
#   ~/nova/workloads/lab_watchlist/run.sh lists
#   ~/nova/workloads/lab_watchlist/run.sh add IB:AAPL:USD --to buy_candidates
#   ~/nova/workloads/lab_watchlist/run.sh find AAPL
#   ~/nova/workloads/lab_watchlist/run.sh where IB:AAPL:USD
#   ~/nova/workloads/lab_watchlist/run.sh show buy_candidates
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.watchlist "$@"
