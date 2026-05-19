#!/usr/bin/env bash
# lab_alert_explainer_daily.sh — taeglich von launchd auf nova-hub.
#
# Submitted alert_explainer-Job. Picker holt's beim naechsten Tick.
# Sequenz: monitor (23:15) -> THIS (23:20) -> digest (23:30).

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

PARAMS_FILE="${HOME}/jobs/lab_alert_explainer_daily.json"
mkdir -p "$(dirname "${PARAMS_FILE}")"

if [[ ! -f "${PARAMS_FILE}" ]]; then
  cat > "${PARAMS_FILE}" <<'EOF'
{
  "max_news":   3,
  "max_alerts": 50,
  "force":      false
}
EOF
fi

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
export NOVA_PARAMS_FILE="${PARAMS_FILE}"
exec python -m modules.llm.alert_explainer
