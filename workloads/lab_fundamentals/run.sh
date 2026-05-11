#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.fundamentals.
# Value-Investor-Lens: Fundamentals-Snapshots fuer Holdings + Watchlists.
#
# Source heute: yfinance (kein IB-Reuters-Sub aktiv).
#
# Beispiele:
#   ~/nova/workloads/lab_fundamentals/run.sh refresh IB:AAPL:USD
#   ~/nova/workloads/lab_fundamentals/run.sh refresh-all --since-days 7
#   ~/nova/workloads/lab_fundamentals/run.sh show IB:AAPL:USD
#   ~/nova/workloads/lab_fundamentals/run.sh coverage
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.fundamentals "$@"
