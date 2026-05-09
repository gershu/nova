#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.llm.alert_explainer.
# Liest sig_alerts vom Tag, holt News, generiert LLM-Erklaerungen ->
# sig_alert_explanations. Idempotent (skip already-done unless --force).
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.llm.alert_explainer "$@"
