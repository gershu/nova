#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.options_history.
# Historische Trend-Queries auf mkt_options_snapshot (read-only).
# Persistierung passiert als Side-Effect von lab_screener_csp.
#
# Beispiele:
#   ~/nova/workloads/lab_options_history/run.sh show IB:AAPL:USD
#   ~/nova/workloads/lab_options_history/run.sh strike IB:AAPL:USD --strike 275 --exp 2026-06-12
#   ~/nova/workloads/lab_options_history/run.sh iv-trend IB:NVDA:USD --days 60
#   ~/nova/workloads/lab_options_history/run.sh premium-trend IB:MSFT:USD --days 30
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.options_history "$@"
