#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.portfolio_core.
# Pflegt die Portfolio-View-Schicht (4 Core-Views + 2 Atomic-Helpers).
#
# Beispiele:
#   ~/nova/workloads/lab_portfolio_core/run.sh init
#   ~/nova/workloads/lab_portfolio_core/run.sh list
#   ~/nova/workloads/lab_portfolio_core/run.sh show v_mkt_holdings --limit 20
#   ~/nova/workloads/lab_portfolio_core/run.sh drop-legacy           # dry-run
#   ~/nova/workloads/lab_portfolio_core/run.sh drop-legacy --yes     # apply
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.portfolio_core "$@"
