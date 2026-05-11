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

# Submission via nova_submit -> Picker dispatcht (Hub als Self-Worker).
# Konsistent zum CSP-Daemon-Pattern. Argumente nach 'refresh-all' werden
# vom workload-run.sh als extra_args durchgereicht.
exec "${HOME}/nova/scripts/nova_submit.sh" \
    lab_fundamentals nova-hub \
    refresh-all --since-days 6
