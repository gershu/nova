#!/usr/bin/env bash
# lab_digest_daily.sh — taeglich von launchd auf nova-hub aufgerufen.
# Submitted einen digest-Job. Picker holt's beim naechsten Tick.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

PARAMS_FILE="${HOME}/jobs/lab_digest_daily.json"
mkdir -p "$(dirname "${PARAMS_FILE}")"

if [[ ! -f "${PARAMS_FILE}" ]]; then
  cat > "${PARAMS_FILE}" <<'EOF'
{
  "source": "yfinance",
  "watchlist": "active"
}
EOF
fi

exec "${HOME}/nova/scripts/nova_submit.sh" lab_digest nova-hub --params-file "${PARAMS_FILE}"
