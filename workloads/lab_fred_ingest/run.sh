#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.fred_ingest.
# Holt Economic-Indikatoren via FRED-API.
#
# Beispiele:
#   ~/nova/workloads/lab_fred_ingest/run.sh init
#   ~/nova/workloads/lab_fred_ingest/run.sh list
#   ~/nova/workloads/lab_fred_ingest/run.sh fetch VIXCLS
#   ~/nova/workloads/lab_fred_ingest/run.sh fetch-all              # Daemon-Modus
#   ~/nova/workloads/lab_fred_ingest/run.sh show VIXCLS --limit 30
#
# ENV:
#   NOVA_FRED_API_KEY  Pflicht — via ~/.nova_env
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.fred_ingest "$@"
