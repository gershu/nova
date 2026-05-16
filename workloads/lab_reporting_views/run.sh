#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.reporting_views.
# Pflegt 3-Layer-View-Schema (atomic / composed / reports) als Reporting-
# Schicht fuer Dashboard, Obsidian, Notebooks, db_edit-Export.
#
# Beispiele:
#   ~/nova/workloads/lab_reporting_views/run.sh init
#   ~/nova/workloads/lab_reporting_views/run.sh list
#   ~/nova/workloads/lab_reporting_views/run.sh show v_report_portfolio_eur
#   ~/nova/workloads/lab_reporting_views/run.sh drop-all
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.reporting_views "$@"
