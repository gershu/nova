#!/usr/bin/env bash
# run.sh — Wrapper fuer nova-lab/modules/portfolio.resolve_conids.
# Migration-Helper: altes symbol-basiertes Excel -> ConID-basiert.
#
# Aufruf:
#   ~/nova/workloads/lab_portfolio_resolve_conids/run.sh \
#       ~/nova_lab_input/portfolio.xlsx \
#       ~/nova_lab_input/portfolio_v2.xlsx
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]      || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]]  || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }
[[ $# -eq 2 ]]              || { echo "Usage: $0 <old.xlsx> <new.xlsx>" >&2; exit 64; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.portfolio.resolve_conids "$@"
