#!/usr/bin/env bash
# lab_health_daily.sh — taeglich von launchd auf nova-hub.
#
# Schedule: 06:00 UTC — nach Ende der naechtlichen Sequence (23:30 UTC),
# sodass alle abendlichen Daemons bereits gelaufen sein sollten und wir
# am Morgen einen sauberen Snapshot haben.
#
# Persistiert in sig_health_snapshots. Exit-Code des CLI ist 0 = alles
# frisch, 2 = einer oder mehrere stale/failed/down — Daemon-Log behaelt
# diese Information.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
exec python -m modules.health run
