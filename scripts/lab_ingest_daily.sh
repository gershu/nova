#!/usr/bin/env bash
# lab_ingest_daily.sh — wird taeglich von launchd auf nova-hub aufgerufen.
#
# Submitted einen ingest-Job an die nova-Queue mit Default-Params:
# alle aktiven Symbole aus der DB, inkrementell (since:auto, until=heute).
# Picker holt's beim naechsten Tick.
#
# Default-Params-File wird angelegt falls nicht vorhanden, kann manuell
# angepasst werden (z.B. fuer andere Source).

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

PARAMS_FILE="${HOME}/jobs/lab_ingest_daily.json"

mkdir -p "$(dirname "${PARAMS_FILE}")"

if [[ ! -f "${PARAMS_FILE}" ]]; then
  cat > "${PARAMS_FILE}" <<'EOF'
{
  "source": "yfinance",
  "watchlist": "active",
  "since": "auto"
}
EOF
fi

exec "${HOME}/nova/scripts/nova_submit.sh" lab_ingest nova-hub --params-file "${PARAMS_FILE}"
