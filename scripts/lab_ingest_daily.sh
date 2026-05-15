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

[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

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

# IB-Precheck conditional: nur wenn source='ib' im params-file, brauchen wir
# Gateway. yfinance-Source braucht kein IB.
SOURCE="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('source','yfinance'))" "${PARAMS_FILE}" 2>/dev/null || echo "yfinance")"
if [[ "${SOURCE}" == "ib" ]]; then
  if ! "${HOME}/nova/scripts/check_ib_gateway.sh"; then
    echo "[lab_ingest_daily] IB Gateway down + source='ib' — Job uebersprungen." >&2
    exit 0
  fi
fi

exec "${HOME}/nova/scripts/nova_submit.sh" lab_ingest nova-hub --params-file "${PARAMS_FILE}"
