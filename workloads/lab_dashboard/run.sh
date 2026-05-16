#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.dashboard (Streamlit-Frontend).
#
# Wird vom LaunchDaemon long-running gestartet (KeepAlive=true).
# Binded an 0.0.0.0:8501 fuer Tailscale-Zugriff von stefan_mac.
#
# Beispiele:
#   ~/nova/workloads/lab_dashboard/run.sh
#   ~/nova/workloads/lab_dashboard/run.sh --port 8502 --bind 127.0.0.1
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.dashboard "$@"
