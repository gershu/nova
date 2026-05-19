#!/usr/bin/env bash
# lab_ingest_fx_daily.sh — wird taeglich von launchd auf nova-hub aufgerufen.
#
# Submitted einen ingest_fx-Job an die nova-Queue mit Default-Params:
# auto-derive Pairs aus den Currencies in pos_holdings/ref_instruments,
# inkrementell (since:auto, until=heute). Picker holt's beim naechsten Tick.
#
# Default-Params-File wird angelegt falls nicht vorhanden, kann manuell
# angepasst werden (z.B. base-currency anders als EUR, explizite pairs).

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

PARAMS_FILE="${HOME}/jobs/lab_ingest_fx_daily.json"

mkdir -p "$(dirname "${PARAMS_FILE}")"

if [[ ! -f "${PARAMS_FILE}" ]]; then
  cat > "${PARAMS_FILE}" <<'EOF'
{
  "source": "yfinance",
  "base":   "EUR",
  "since":  "auto"
}
EOF
fi

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
export NOVA_PARAMS_FILE="${PARAMS_FILE}"
exec python -m modules.ingest_fx
