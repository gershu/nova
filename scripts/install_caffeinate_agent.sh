#!/usr/bin/env bash
# install_caffeinate_agent.sh — User-level LaunchAgent fuer caffeinate.
#
# Verhindert dass headless-Macs (insbesondere nova-w5 als LLM-Host) in
# Sleep gehen, was mDNS/Bonjour breakt + Ollama-Endpoint unerreichbar macht.
#
# Nicht ausschliesslich fuer nova-w5 — kann auf jedem nicht-Hub-Worker
# installiert werden, der dauerhaft erreichbar sein soll. Hostname-Check
# ist deshalb absichtlich locker (nur Warnung wenn nova-hub).
#
# Aufruf:
#   ~/nova/scripts/install_caffeinate_agent.sh
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Fehler: install_caffeinate_agent.sh darf NICHT als root laufen." >&2
  echo "       LaunchAgent ist user-level. Aufruf als novaadm." >&2
  exit 1
fi

if [[ "$(hostname -s)" == "nova-hub" ]]; then
  echo "Hinweis: hub hat schon einen Picker-Daemon der ihn wach haelt." >&2
  echo "         caffeinate ist hier optional. Trotzdem installieren? (y/N)" >&2
  read -r ans
  [[ "${ans:-N}" =~ ^[yY]$ ]] || exit 0
fi

UID_NUM="$(id -u)"
if ! launchctl print "gui/${UID_NUM}" >/dev/null 2>&1; then
  echo "Fehler: keine aktive GUI-Session fuer UID ${UID_NUM}." >&2
  echo "       LaunchAgent braucht Aqua-Session. Auto-Login + Reboot oder lokal in Terminal.app ausfuehren." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

PLIST_SRC="${REPO_DIR}/dotfiles/launchd/de.gershu.nova.caffeinate.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/de.gershu.nova.caffeinate.plist"
LOGS_DIR="${HOME}/Library/Logs"

[[ -f "${PLIST_SRC}" ]] || { echo "Fehler: ${PLIST_SRC} nicht gefunden." >&2; exit 1; }

echo "==> Logs-Dir sicherstellen"
mkdir -p "${LOGS_DIR}" "$(dirname "${PLIST_DST}")"

echo "==> Kopiere plist nach ${PLIST_DST}"
cp "${PLIST_SRC}" "${PLIST_DST}"
chmod 644 "${PLIST_DST}"

echo "==> Existierenden Agent ggf. abladen (idempotent)"
launchctl bootout "gui/${UID_NUM}/de.gershu.nova.caffeinate" 2>/dev/null || true

echo "==> Agent laden"
launchctl bootstrap "gui/${UID_NUM}" "${PLIST_DST}"

echo
echo "==> Fertig. caffeinate -dims laeuft permanent als $(whoami) auf $(hostname -s)."
echo "    Verifizieren: pgrep -fl caffeinate"
echo "    Stop:         launchctl bootout gui/${UID_NUM}/de.gershu.nova.caffeinate"
echo
echo "==> Parallel manuell pruefen / setzen:"
echo "    System Settings -> Energy"
echo "      - Prevent automatic sleeping when display is off: an"
echo "      - Wake for network access:                         an"
echo "      - Start up automatically after power failure:      an"
