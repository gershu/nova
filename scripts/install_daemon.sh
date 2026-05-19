#!/usr/bin/env bash
# install_daemon.sh — generic sudo-Installer fuer nova-LaunchDaemons.
#
# Ersetzt die zwoelf install_lab_*_daemon.sh-Scripts. Boilerplate
# konsolidiert: kopiert die Plist nach /Library/LaunchDaemons/, setzt
# owner/perms, bootstrap'd den Daemon.
#
# Aufruf:
#   sudo ~/nova/scripts/install_daemon.sh <label>
#
# Wobei <label> der Plist-Filename OHNE 'de.gershu.nova.' Prefix und ohne
# '.plist' Suffix ist. Beispiele:
#   sudo ~/nova/scripts/install_daemon.sh lab.dashboard
#   sudo ~/nova/scripts/install_daemon.sh lab.fred_ingest
#   sudo ~/nova/scripts/install_daemon.sh lab.market_monitor
#
# Stop (symmetrisch):
#   sudo launchctl bootout system /Library/LaunchDaemons/de.gershu.nova.<label>.plist
#
# Status:
#   sudo launchctl list | grep de.gershu.nova

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Fehler: muss mit sudo (als root) laufen." >&2
  echo "  sudo $(realpath "$0") $*" >&2
  exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: sudo $(basename "$0") <label>" >&2
  echo "Beispiel: sudo $(basename "$0") lab.fred_ingest" >&2
  exit 64
fi

LABEL="$1"
FQ_LABEL="de.gershu.nova.${LABEL}"
PLIST_FILENAME="${FQ_LABEL}.plist"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

PLIST_SRC="${REPO_DIR}/dotfiles/launchd/${PLIST_FILENAME}"
PLIST_DST="/Library/LaunchDaemons/${PLIST_FILENAME}"
LOGS_DIR="/Users/novaadm/Library/Logs"

[[ -f "${PLIST_SRC}" ]] || { echo "Fehler: ${PLIST_SRC} nicht gefunden." >&2; exit 1; }

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
launchctl bootout "system/${FQ_LABEL}" 2>/dev/null || true

echo "==> Daemon laden"
launchctl bootstrap system "${PLIST_DST}"

sleep 2
echo
echo "==> Status:"
launchctl print "system/${FQ_LABEL}" 2>&1 | head -10 || true
echo
echo "==> Logs:    ${LOGS_DIR}/nova-${LABEL//./.}.log"
echo "==> Stop:    sudo launchctl bootout system ${PLIST_DST}"
