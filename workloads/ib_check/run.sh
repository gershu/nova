#!/usr/bin/env bash
# run.sh — Entry-Point fuer den ib_check-Workload.
#
# Diagnose-Tool fuer TWS/IB-Gateway-Verbindung. Kein "echter" Workload —
# nur ein Verbindungs-Test mit detailliertem Layered Output.
#
# Aufruf:
#   ~/nova/workloads/ib_check/run.sh
#   ssh nova-w1 '~/nova/workloads/ib_check/run.sh'
#   ~/nova/scripts/nova_run.sh ib_check nova-w1
#   ~/nova/scripts/nova_submit.sh ib_check nova-w1 --params-file ~/jobs/ib_alt.json

set -euo pipefail

# Per-Node Tier-2-Overrides laden (~/.nova_env, gitignored).
# shellcheck disable=SC1091
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
JOB_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Fehler: venv ${VENV_DIR} nicht gefunden — erst node_deploy.sh ausfuehren." >&2
  exit 1
fi

# venv aktivieren (Cluster-venv mit ib_async aus requirements-lock.txt)
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

exec python "${JOB_DIR}/ib_check.py" "$@"
