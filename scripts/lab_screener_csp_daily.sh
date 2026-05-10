#!/usr/bin/env bash
# lab_screener_csp_daily.sh — taeglich von launchd auf nova-hub.
#
# Schedule: 23:35 (nach digest 23:30) — nutzt today's frische quotes
# aus ingest 23:00. system_recommendations-watchlist wird ueberschrieben
# fuer naechsten Tag-Briefing-Zyklus.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

PARAMS_FILE="${HOME}/jobs/lab_screener_csp_daily.json"
mkdir -p "$(dirname "${PARAMS_FILE}")"

if [[ ! -f "${PARAMS_FILE}" ]]; then
  cat > "${PARAMS_FILE}" <<'EOF'
{
  "watchlist_universe":     "csp_universe",
  "min_dte":                25,
  "max_dte":                50,
  "buffer_min_pct":         5,
  "buffer_max_pct":         15,
  "min_annualized_yield":   8.0,
  "max_spread_pct":         25,
  "expirations_per_symbol": 2,
  "top_n_per_symbol":       1,
  "top_n_overall":          20
}
EOF
fi

# Subcommand 'run' fuer den Daily-Job
exec "${HOME}/nova/scripts/nova_submit.sh" lab_screener_csp nova-hub run --params-file "${PARAMS_FILE}"
