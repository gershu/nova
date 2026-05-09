#!/usr/bin/env bash
# run.sh — Wrapper fuer nova-lab/modules/llm/probe.py.
# Sanity-Test fuer den Ollama-Endpoint auf nova-w5 — kann von jedem Node
# gerufen werden, default zielt auf http://nova-w5.local:11434.
#
# Aufruf:
#   ~/nova/scripts/nova_run.sh lab_llm_probe nova-hub
#   ~/nova/scripts/nova_run.sh lab_llm_probe nova-w5    # selbst-test direkt auf llm-host
#   ~/nova/workloads/lab_llm_probe/run.sh --list-models
#   ~/nova/workloads/lab_llm_probe/run.sh --json
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]      || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.llm.probe "$@"
