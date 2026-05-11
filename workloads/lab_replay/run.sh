#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.replay.
# Historical-Replay-Analyse: nimmt heutiges Portfolio + historische Quotes
# -> worst-day, worst-week, drawdown, replay.
#
# Beispiele:
#   ~/nova/workloads/lab_replay/run.sh worst-day --lookback-days 730
#   ~/nova/workloads/lab_replay/run.sh worst-week --top 5
#   ~/nova/workloads/lab_replay/run.sh drawdown
#   ~/nova/workloads/lab_replay/run.sh replay --from 2024-01-01 --to 2024-12-31 --csv
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.replay "$@"
