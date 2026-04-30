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


print_args() {
  echo "Anzahl Parameter: $#"

  local i=1
  for arg in "$@"; do
    echo "[$i] $arg"
    i=$((i + 1))
  done
}

print_args "$@"

set -euo pipefail

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
JOB_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Fehler: venv ${VENV_DIR} nicht gefunden — erst node_deploy.sh ausfuehren." >&2
  exit 1
fi


wait_with_progress() {
  local seconds="${1:-20}"
  local label="${2:-wait}"

  echo "$(date '+%Y-%m-%d %H:%M:%S') START ${label}: ${seconds}s"

  for ((i=1; i<=seconds; i++)); do
    printf "\r[%3d/%3d] %s" "$i" "$seconds" "$label"
    sleep 1
  done

  printf "\n"
  echo "$(date '+%Y-%m-%d %H:%M:%S') END   ${label}: ${seconds}s"
}

wait_with_progress 20 "nova pause"






# venv aktivieren (setzt PATH + VIRTUAL_ENV)
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

exec python "${JOB_DIR}/hello_world.py" "$@"
