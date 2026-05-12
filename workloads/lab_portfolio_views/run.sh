#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.portfolio_views.
# Pure CLI — Pflege der Portfolio-Sichten. Kein Daemon noetig.
#
# Beispiele:
#   ~/nova/workloads/lab_portfolio_views/run.sh init
#   ~/nova/workloads/lab_portfolio_views/run.sh add core IB:AAPL:USD IB:MSFT:USD
#   ~/nova/workloads/lab_portfolio_views/run.sh add sell_candidates IB:INTC:USD --notes "earnings miss"
#   ~/nova/workloads/lab_portfolio_views/run.sh list
#   ~/nova/workloads/lab_portfolio_views/run.sh show core
#   ~/nova/workloads/lab_portfolio_views/run.sh which IB:AAPL:USD
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.portfolio_views "$@"
