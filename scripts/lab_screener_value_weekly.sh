#!/usr/bin/env bash
# lab_screener_value_weekly.sh — woechentlich von launchd auf nova-hub.
#
# Schedule: Sonntag 22:30 — nach lab_fundamentals (22:00), vor daily-Sequence.
# Filtert das S&P-500-Universe nach Value-Kriterien.
#
# Default-Filter (im Code): roe>=10%, pe<=35, fcf-yield>=2%, d/e<=2.5,
# composite-score>=0.40. Tuning via Params-JSON unten.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

PARAMS_FILE="${HOME}/jobs/lab_screener_value_weekly.json"
mkdir -p "$(dirname "${PARAMS_FILE}")"

if [[ ! -f "${PARAMS_FILE}" ]]; then
  cat > "${PARAMS_FILE}" <<'EOF'
{
  "min_roe":                0.10,
  "max_pe_ttm":             35.0,
  "min_fcf_yield":          0.02,
  "max_debt_to_equity":     2.5,
  "max_net_debt_to_ebitda": 5.0,
  "min_market_cap":         5000000000,
  "min_composite_score":    0.40,
  "min_revenue_cagr_5y":   -0.02,
  "min_operating_margin":   0.08,
  "sector_blacklist":       [],
  "top_n":                  30,
  "sector_diversification": true,
  "per_sector_cap":         4
}
EOF
fi

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
export NOVA_PARAMS_FILE="${PARAMS_FILE}"
exec python -m modules.screener_value filter
