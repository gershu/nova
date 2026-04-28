#!/usr/bin/env bash
# run.sh — Entry-Point fuer den csp_scanner-Workload (Sibling-Repo-Modell).
#
# csp_scanner lebt auf jedem Node als eigenes git-Repo unter ~/csp_scanner.
# Wird durch node_deploy.sh Schritt 5 (Workload-Repos) mitgepullt — nicht
# dieser Workload-Ordner.
#
# Aufruf:
#   ~/nova/workloads/csp_scanner/run.sh
#   ssh nova-w2 '~/nova/workloads/csp_scanner/run.sh'

set -euo pipefail

# Per-Node Overrides laden (Tier 2: ~/.nova_env, gitignored, nicht auto-deployed).
# Idempotent: macht nichts, falls die Datei fehlt.
# shellcheck disable=SC1091
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
JOB_SRC_DIR="${NOVA_CSP_SCANNER_DIR:-$HOME/csp_scanner}"
OUTPUT_DIR="${HOME}/nova_output/csp_scanner"

# Tier 2 Overrides fuer die Configs (defaults bleiben repo-tracked):
WATCHLIST_PATH="${CSP_SCANNER_WATCHLIST:-config/watchlist.yaml}"
SETTINGS_PATH="${CSP_SCANNER_SETTINGS:-config/settings.yaml}"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Fehler: venv ${VENV_DIR} nicht gefunden — erst node_deploy.sh ausfuehren." >&2
  exit 1
fi
if [[ ! -d "${JOB_SRC_DIR}/.git" ]]; then
  echo "Fehler: ${JOB_SRC_DIR} ist kein Git-Repo." >&2
  echo "       node_deploy.sh klont es laut config/workload_repos.txt." >&2
  exit 1
fi

# Per-Node Output-Dir + Symlink ${JOB_SRC_DIR}/output -> ~/nova_output/csp_scanner/
mkdir -p "${OUTPUT_DIR}"
LINK="${JOB_SRC_DIR}/output"
if [[ -L "${LINK}" ]]; then
  if [[ "$(readlink "${LINK}")" != "${OUTPUT_DIR}" ]]; then
    rm -f "${LINK}"
    ln -s "${OUTPUT_DIR}" "${LINK}"
  fi
elif [[ -e "${LINK}" ]]; then
  mv "${LINK}" "${LINK}.bak.$(date +%Y%m%d%H%M%S)"
  ln -s "${OUTPUT_DIR}" "${LINK}"
else
  ln -s "${OUTPUT_DIR}" "${LINK}"
fi

# venv aktivieren (gemeinsamer nova-Cluster-venv, deps aus nova requirements-lock.txt)
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# CWD = csp_scanner-Repo, damit `python -m src.main` das src/-Package findet
# und die relativen --watchlist / --settings Pfade aufloesen.
cd "${JOB_SRC_DIR}"

exec python -m src.main \
    --watchlist "${WATCHLIST_PATH}" \
    --settings "${SETTINGS_PATH}" \
    "$@"
