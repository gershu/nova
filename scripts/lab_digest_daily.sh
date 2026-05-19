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

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
export NOVA_PARAMS_FILE="${PARAMS_FILE}"
exec python -m modules.digest
