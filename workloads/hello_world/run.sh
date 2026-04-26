#!/usr/bin/env bash
# run.sh — Entry-Point fuer den hello_world-Workload.
#
# Konvention fuer alle nova-Workloads:
#   workloads/<name>/run.sh        Wrapper (aktiviert venv, ruft Logik)
#   workloads/<name>/<name>.py     Python-Logik
#
# Aufruf:
#   ~/nova/workloads/hello_world/run.sh
#   ssh nova-prod '~/nova/workloads/hello_world/run.sh'

set -euo pipefail

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
JOB_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Fehler: venv ${VENV_DIR} nicht gefunden — erst node_deploy.sh ausfuehren." >&2
  exit 1
fi

# venv aktivieren (setzt PATH + VIRTUAL_ENV)
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

exec python "${JOB_DIR}/hello_world.py" "$@"
