#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.scenarios.
# Forward-Shock-Analyse auf Portfolio (read-only).
#
# Beispiele:
#   ~/nova/workloads/lab_scenarios/run.sh shock --symbol AAPL --pct -25
#   ~/nova/workloads/lab_scenarios/run.sh shock --currency USD --pct -10
#   ~/nova/workloads/lab_scenarios/run.sh shock --asset-class stock --pct -15
#   ~/nova/workloads/lab_scenarios/run.sh shock --watchlist buy_candidates --pct -20
#   ~/nova/workloads/lab_scenarios/run.sh run ~/scenarios/tech_crash.json
#   ~/nova/workloads/lab_scenarios/run.sh --base USD shock --currency EUR --pct -10
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.scenarios "$@"
