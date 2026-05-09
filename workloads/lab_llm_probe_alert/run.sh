#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.llm.probe_alert.
# Prompt-Engineering-Probe: nimmt EIN sig_alert + yfinance-News, ruft LLM
# zur Erklaerung. KEINE DB-Writes. Reine Iteration fuer Prompt-Tuning.
#
# Aufruf-Beispiele:
#   ~/nova/scripts/nova_run.sh lab_llm_probe_alert nova-hub
#   ~/nova/scripts/nova_run.sh lab_llm_probe_alert nova-hub --symbol AAPL
#   ~/nova/scripts/nova_run.sh lab_llm_probe_alert nova-hub --json --show-prompt
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]      || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.llm.probe_alert "$@"
