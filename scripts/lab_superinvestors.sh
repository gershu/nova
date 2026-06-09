#!/usr/bin/env bash
# lab_superinvestors.sh — 13F-Superinvestoren-Ingest, woechentlich per launchd.
#
# Holt je konfiguriertem Filer (config/superinvestors.yaml) das neueste
# 13F-Portfolio + Quartalsveraenderungen. 13F erscheinen ~45 Tage nach
# Quartalsende; ein woechentlicher Lauf faengt neue Filings ein, sobald sie
# da sind (Upsert ist idempotent). sec-api-lastig, daher selten + separat.
#
# Initial-Setup: scripts/install_daemon.sh lab.superinvestors
# Logs:          /Users/novaadm/Library/Logs/nova-lab-superinvestors.log

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"

exec python -m modules.superinvestors ingest
