#!/usr/bin/env bash
# install_lab_ingest_fx_daemon.sh — einmaliger sudo-Setup des
# nova-lab-daily-ingest-FX LaunchDaemon auf nova-hub.
#
# Triggert taeglich um 22:50 Lokalzeit den FX-Ingest in mkt_fx_daily
# (siehe lab_ingest_fx_daily.sh).
#
# Aufruf:
#   sudo ~/nova/scripts/install_lab_ingest_fx_daemon.sh

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Fehler: install_lab_ingest_fx_daemon.sh muss mit sudo (als root) laufen." >&2
  echo "  sudo $(realpath "$0")" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

PLIST_SRC="${REPO_DIR}/dotfiles/launchd/de.gershu.nova.lab.ingest.fx.plist"
PLIST_DST="/Library/LaunchDaemons/de.gershu.nova.lab.ingest.fx.plist"
DRIVER_SH="${REPO_DIR}/scripts/lab_ingest_fx_daily.sh"
LOGS_DIR="/Users/novaadm/Library/Logs"

[[ -f "${PLIST_SRC}" ]]  || { echo "Fehler: ${PLIST_SRC} nicht gefunden." >&2; exit 1; }
[[ -x "${DRIVER_SH}" ]]  || { echo "Fehler: ${DRIVER_SH} nicht executable." >&2; exit 1; }

if [[ "$(hostname -s)" != "nova-hub" ]]; then
  echo "Fehler: Diese Maschine ist nicht nova-hub (Hostname: $(hostname -s))." >&2
  echo "       LaunchDaemon nur auf dem Hub installieren." >&2
  exit 1
fi

echo "==> Logs-Dir sicherstellen: ${LOGS_DIR}"
sudo -u novaadm mkdir -p "${LOGS_DIR}"

echo "==> Kopiere plist nach ${PLIST_DST}"
cp "${PLIST_SRC}" "${PLIST_DST}"
chown root:wheel "${PLIST_DST}"
chmod 644 "${PLIST_DST}"

echo "==> Existierenden Daemon ggf. abladen (idempotent)"
launchctl bootout "system/de.gershu.nova.lab.ingest.fx" 2>/dev/null || true

echo "==> Daemon laden"
launchctl bootstrap system "${PLIST_DST}"

echo "==> Status:"
launchctl print "system/de.gershu.nova.lab.ingest.fx" 2>&1 | head -20 || true

echo
echo "==> Fertig. Daily FX-ingest triggert taeglich 22:50 lokal als novaadm."
echo "    Naechster Lauf: launchctl print system/de.gershu.nova.lab.ingest.fx | grep next"
echo "    Logs: ${LOGS_DIR}/nova-lab-ingest-fx.log"
echo "    Manueller Trigger: sudo launchctl kickstart system/de.gershu.nova.lab.ingest.fx"
echo "    Stop:   sudo launchctl bootout system/de.gershu.nova.lab.ingest.fx"
