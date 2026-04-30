#!/usr/bin/env bash
# install_picker.sh — einmaliger sudo-Setup des nova-Picker als LaunchDaemon.
#
# Nur auf nova-hub auszufuehren. Kopiert die plist nach /Library/LaunchDaemons,
# setzt root:wheel-Owner + 644 Mode, und bootstrap't den Daemon.
#
# Idempotent: falls der Daemon bereits laeuft, wird er erst bootout't, dann
# neu bootstrap't (Reload).
#
# Aufruf:
#   sudo ~/nova/scripts/install_picker.sh
#
# Daemon laeuft danach automatisch beim Boot, unabhaengig von Login-Sessions.
# Logs: /Users/novaadm/Library/Logs/nova-picker.log.

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Fehler: install_picker.sh muss mit sudo (als root) laufen." >&2
  echo "  sudo $(realpath "$0")" >&2
  exit 1
fi

# Erwartet: aufgerufen aus /Users/novaadm/nova oder vom stefan_pro-Editor-Repo.
# REPO_DIR aus Pfad ableiten (parent of scripts/).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

PLIST_SRC="${REPO_DIR}/dotfiles/launchd/de.gershu.nova.picker.plist"
PLIST_DST="/Library/LaunchDaemons/de.gershu.nova.picker.plist"
PICKER_SH="${REPO_DIR}/scripts/nova_picker.sh"
LOGS_DIR="/Users/novaadm/Library/Logs"

# Sanity-Checks
[[ -f "${PLIST_SRC}" ]] || { echo "Fehler: ${PLIST_SRC} nicht gefunden." >&2; exit 1; }
[[ -x "${PICKER_SH}" ]] || { echo "Fehler: ${PICKER_SH} nicht executable." >&2; exit 1; }

# Hub-Hostname-Check (Daemon nur auf nova-hub installieren)
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
launchctl bootout "system/de.gershu.nova.picker" 2>/dev/null || true

echo "==> Daemon laden"
launchctl bootstrap system "${PLIST_DST}"

echo "==> Status:"
launchctl print "system/de.gershu.nova.picker" 2>&1 | head -20 || true

echo
echo "==> Fertig. Picker laeuft als novaadm im system-Kontext."
echo "    Logs: ${LOGS_DIR}/nova-picker.log"
echo "    Status: launchctl print system/de.gershu.nova.picker"
echo "    Stop:   sudo launchctl bootout system/de.gershu.nova.picker"
