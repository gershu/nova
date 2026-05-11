#!/usr/bin/env bash
# install_lab_screener_csp_daemon.sh — sudo-Setup des
# nova-lab CSP-Screener LaunchDaemon auf nova-hub.
#
# Triggert taeglich 23:05 — nach ingest 23:00, vor monitor 23:15. Mit
# frischen quotes vom 23:00 ingest. Ueberschreibt system_recommendations-
# watchlist mit Top-N CSP-Kandidaten — damit digest 23:30 die Picks
# anzeigen kann (siehe digest/sections/csp_picks.py).
#
# Aufruf:
#   sudo ~/nova/scripts/install_lab_screener_csp_daemon.sh

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Fehler: muss mit sudo (als root) laufen." >&2
  echo "  sudo $(realpath "$0")" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

PLIST_SRC="${REPO_DIR}/dotfiles/launchd/de.gershu.nova.lab.screener_csp.plist"
PLIST_DST="/Library/LaunchDaemons/de.gershu.nova.lab.screener_csp.plist"
DRIVER_SH="${REPO_DIR}/scripts/lab_screener_csp_daily.sh"
LOGS_DIR="/Users/novaadm/Library/Logs"

[[ -f "${PLIST_SRC}" ]] || { echo "Fehler: ${PLIST_SRC} nicht gefunden." >&2; exit 1; }
[[ -x "${DRIVER_SH}" ]] || { echo "Fehler: ${DRIVER_SH} nicht executable." >&2; exit 1; }

if [[ "$(hostname -s)" != "nova-hub" ]]; then
  echo "Fehler: nicht nova-hub (Hostname: $(hostname -s))." >&2
  exit 1
fi

echo "==> Logs-Dir sicherstellen: ${LOGS_DIR}"
sudo -u novaadm mkdir -p "${LOGS_DIR}"

echo "==> Kopiere plist nach ${PLIST_DST}"
cp "${PLIST_SRC}" "${PLIST_DST}"
chown root:wheel "${PLIST_DST}"
chmod 644 "${PLIST_DST}"

echo "==> Existierenden Daemon ggf. abladen (idempotent)"
launchctl bootout "system/de.gershu.nova.lab.screener_csp" 2>/dev/null || true

echo "==> Daemon laden"
launchctl bootstrap system "${PLIST_DST}"

echo "==> Status:"
launchctl print "system/de.gershu.nova.lab.screener_csp" 2>&1 | head -20 || true

echo
echo "==> Fertig. Daily CSP-Screener triggert taeglich 23:35 lokal als novaadm."
echo "    Voraussetzung: Watchlist 'csp_universe' mit Underlyings vorhanden:"
echo "      python -m modules.screener_csp init"
echo "      python -m modules.watchlist add IB:AAPL:USD --to csp_universe"
echo "    Logs: ${LOGS_DIR}/nova-lab-screener-csp.log"
echo "    Manueller Trigger: sudo launchctl kickstart system/de.gershu.nova.lab.screener_csp"
echo "    Stop:   sudo launchctl bootout system/de.gershu.nova.lab.screener_csp"
