#!/usr/bin/env bash
# lab_monitor_daily.sh — wird taeglich von launchd auf nova-hub aufgerufen.
#
# Submitted einen monitor-Job an die nova-Queue mit Default-Params:
# alle aktiven Symbole, alle vier Regeln mit Standard-Schwellwerten.
# Triggert sich nach dem ingest-Daemon (23:00 ingest -> 23:15 monitor).

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

PARAMS_FILE="${HOME}/jobs/lab_monitor_daily.json"

mkdir -p "$(dirname "${PARAMS_FILE}")"

if [[ ! -f "${PARAMS_FILE}" ]]; then
  cat > "${PARAMS_FILE}" <<'EOF'
{
  "source": "yfinance",
  "watchlist": "active"
}
EOF
fi

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
export NOVA_PARAMS_FILE="${PARAMS_FILE}"
exec python -m modules.monitor
