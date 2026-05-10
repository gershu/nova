#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.screener_csp.
#
# Beispiele:
#   ~/nova/workloads/lab_screener_csp/run.sh init
#   ~/nova/workloads/lab_screener_csp/run.sh run
#   ~/nova/scripts/nova_run.sh lab_screener_csp nova-hub run --params-file ~/jobs/lab_screener_csp_daily.json
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.screener_csp "$@"
