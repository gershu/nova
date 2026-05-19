#!/usr/bin/env bash
# lab_portfolio_briefing_daily.sh — taeglich von launchd auf nova-hub.
#
# Sequenz: monitor 23:15 -> alert_explainer 23:20 -> THIS 23:25 -> digest 23:30.
# Submitted Briefing-Job. Picker holt's beim naechsten Tick.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

PARAMS_FILE="${HOME}/jobs/lab_portfolio_briefing_daily.json"
mkdir -p "$(dirname "${PARAMS_FILE}")"

if [[ ! -f "${PARAMS_FILE}" ]]; then
  cat > "${PARAMS_FILE}" <<'EOF'
{
  "base_currency": "EUR",
  "max_movers":    5,
  "max_alerts":    20,
  "force":         false
}
EOF
fi

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
export NOVA_PARAMS_FILE="${PARAMS_FILE}"
exec python -m modules.llm.portfolio_briefing
