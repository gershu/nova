#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.db_edit.
# Generic DB-Table <-> xlsx-Sync fuer Stammdaten-Pflege.
#
# Beispiele:
#   ~/nova/workloads/lab_db_edit/run.sh list-tables
#   ~/nova/workloads/lab_db_edit/run.sh schema list_portfolio_views
#   ~/nova/workloads/lab_db_edit/run.sh export list_portfolio_views
#   ~/nova/workloads/lab_db_edit/run.sh load ~/nova_output/db_edit/foo.xlsx --mode truncate --dry-run
#   ~/nova/workloads/lab_db_edit/run.sh load ~/nova_output/db_edit/foo.xlsx --mode truncate
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.db_edit "$@"
