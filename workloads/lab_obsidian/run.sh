#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.obsidian.
# Exportiert DB-Inhalte als Markdown-Vault nach ~/nova_output/obsidian/
# (oder OBSIDIAN_VAULT_PATH override).
#
# Beispiele:
#   ~/nova/workloads/lab_obsidian/run.sh publish
#   ~/nova/workloads/lab_obsidian/run.sh show-vault-path
#   ~/nova/workloads/lab_obsidian/run.sh clean --keep-days 60
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.obsidian "$@"
