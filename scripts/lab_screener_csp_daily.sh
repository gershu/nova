#!/usr/bin/env bash
# lab_screener_csp_daily.sh — taeglich von launchd auf nova-hub.
#
# Schedule: 23:05 (nach ingest 23:00, vor monitor 23:15) — nutzt today's
# frisch-ingested quotes. system_recommendations-watchlist wird ueberschrieben
# damit digest 23:30 die frischen CSP-Picks anzeigen kann.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

# ENV laden fuer IB_GATEWAY_HOST/PORT etc.
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

# IB-Precheck — screener_csp braucht zwingend Option-Chains via IB.
# Bei Failure clean abort; downstream-Daemons (monitor, digest) laufen
# weiter mit dem letzten Stand. NOVA_SKIP_IF_NO_IB=0 deaktiviert das Gate.
if ! "${HOME}/nova/scripts/check_ib_gateway.sh"; then
  echo "[lab_screener_csp_daily] IB Gateway down — Job uebersprungen." >&2
  exit 0    # exit 0 damit launchd den daemon nicht als 'failed' markiert
fi

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
