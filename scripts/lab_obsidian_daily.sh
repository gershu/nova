#!/usr/bin/env bash
# lab_obsidian_daily.sh — taeglich von launchd auf nova-hub, 23:55.
# Letzter Step der Daily-Sequence — exportiert DB-Inhalte als MD-Vault
# nach ~/nova_output/obsidian/ (oder OBSIDIAN_VAULT_PATH override).

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
exec python -m modules.obsidian publish
