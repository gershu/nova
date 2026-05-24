#!/usr/bin/env bash
# lab_sec_filings_weekly.sh — woechentlich von launchd auf nova-hub.
#
# Schedule: Sonntag 21:30 — vor dem Fundamentals-Refresh (22:00).
# Zieht die GuV-Kernzeilen (Income Statement) aus dem juengsten 10-Q/10-K
# je Holding + Watchlist-Member nach ref_income_statement.
#
# Woechentlich, weil 10-Q/10-K nur quartalsweise erscheinen — --since-days 6
# ueberspringt Symbole mit Snapshot juenger als 6 Tage (Doppel-Run-Schutz).
#
# ENV: NOVA_SEC_API_KEY via ~/.nova_env.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
exec python -m modules.sec_filings fetch-all --since-days 6
