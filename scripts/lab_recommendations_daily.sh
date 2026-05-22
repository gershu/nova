#!/usr/bin/env bash
# lab_recommendations_daily.sh — taeglich von launchd auf nova-hub.
#
# Schedule: 23:15 UTC — nach setup-detection (23:00), damit der
# Recommendation-Layer die frisch erkannten Setups + Alerts nutzt.
#
# Erzeugt LLM-basierte Handlungs-Vorschlaege in sig_recommendations.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

PARAMS_FILE="${HOME}/jobs/lab_recommendations_daily.json"
mkdir -p "$(dirname "${PARAMS_FILE}")"
if [[ ! -f "${PARAMS_FILE}" ]]; then
  cat > "${PARAMS_FILE}" <<'EOF'
{
  "model": "qwen2.5:14b-instruct-q4_K_M"
}
EOF
fi

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
export NOVA_PARAMS_FILE="${PARAMS_FILE}"
exec python -m modules.llm.recommendations run
