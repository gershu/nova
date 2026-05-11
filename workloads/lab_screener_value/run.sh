#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.screener_value.
# S&P-500 nach Value-Kriterien filtern + on-demand LLM-Strukturierer.
#
# Beispiele:
#   ~/nova/workloads/lab_screener_value/run.sh init
#   ~/nova/workloads/lab_screener_value/run.sh filter
#   ~/nova/workloads/lab_screener_value/run.sh filter --params-file ~/jobs/value_strict.json
#   ~/nova/workloads/lab_screener_value/run.sh llm-deepdive IB:AAPL:USD
#   ~/nova/workloads/lab_screener_value/run.sh show
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.screener_value "$@"
