#!/usr/bin/env bash
# lab_fundamentals_weekly.sh — woechentlich von launchd auf nova-hub.
#
# Schedule: Sonntag 22:00 — vor der taeglichen Sequence ab 22:50.
# Refresht Fundamentals fuer Holdings + Watchlist-Members.
#
# --since-days 6: ueberspringt Symbole deren letzter Snapshot juenger als
# 6 Tage ist. Verhindert Doppel-Refresh wenn der Daemon nach einem
# Manual-Run nochmal triggert; gibt aber nach genau 7 Tagen einen frischen
# Snapshot fuer alle.
#
# Kein --params-file noetig — die zwei Parameter (source/since-days) sind
# stabil genug fuer den Code-Pfad. Aenderungen ueber Edit dieses Drivers.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

source "${HOME}/nova/.venv/bin/activate"
cd "${HOME}/nova"
exec python -m modules.fundamentals refresh-all --since-days 6
