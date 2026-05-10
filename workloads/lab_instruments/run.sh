#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.instruments CLI.
#
# Stammdaten-Pflege fuer ref_instruments (analog zu lab_watchlist).
#
# Beispiele:
#   ~/nova/workloads/lab_instruments/run.sh add --conid 4815747
#   ~/nova/workloads/lab_instruments/run.sh add --symbol VIX --exchange CBOE --currency USD
#   ~/nova/workloads/lab_instruments/run.sh find AAPL
#   ~/nova/workloads/lab_instruments/run.sh show IB:AAPL:USD
#   ~/nova/workloads/lab_instruments/run.sh list --asset-class etf
#   ~/nova/workloads/lab_instruments/run.sh update IB:QQQ:USD --asset-class etf
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.instruments "$@"
