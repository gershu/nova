#!/usr/bin/env bash
# setup_obsidian_pull_stefan_mac.sh — einmalige Konfiguration auf stefan_mac.
#
# Installiert den Obsidian-Pull-LaunchAgent. Keine sudo-Rechte noetig.
#
# Voraussetzungen vor Aufruf:
#   1. ~/.ssh/config hat 'nova-hub' Host-Alias mit funktionierendem Key
#   2. ssh nova-hub 'ls nova_output/obsidian/' liefert ein Verzeichnis
#   3. Obsidian-Vault existiert (Default: ~/Documents/Obsidian Vault/)
#
# Aufruf:
#   bash ~/nova/scripts/setup_obsidian_pull_stefan_mac.sh

set -euo pipefail

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
PLIST_SRC="${REPO_DIR}/dotfiles/launchd/de.gershu.nova.obsidian.pull.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/de.gershu.nova.obsidian.pull.plist"
LOG_FILE="${HOME}/Library/Logs/nova-obsidian-pull.log"
LABEL="de.gershu.nova.obsidian.pull"

VAULT_ROOT="${OBSIDIAN_VAULT_ROOT:-${HOME}/Documents/Obsidian Vault}"

echo "==> Pre-checks"
[[ -f "${PLIST_SRC}" ]] || { echo "Fehler: ${PLIST_SRC} nicht gefunden — Repo gepullt?" >&2; exit 1; }
[[ -d "${VAULT_ROOT}" ]] || { echo "Fehler: Obsidian-Vault ${VAULT_ROOT} existiert nicht." >&2; exit 1; }

echo "    Pruefe SSH-Verbindung zu nova-hub..."
if ! ssh -o ConnectTimeout=5 nova-hub 'true' 2>/dev/null; then
  echo "Fehler: ssh nova-hub schlaegt fehl." >&2
  echo "  Pruefe ~/.ssh/config 'Host nova-hub' + Key." >&2
  exit 1
fi
echo "    OK."

echo "==> LaunchAgents-Folder sicherstellen"
mkdir -p "${HOME}/Library/LaunchAgents"
mkdir -p "${HOME}/Library/Logs"

echo "==> Kopiere plist nach ${PLIST_DST}"
cp "${PLIST_SRC}" "${PLIST_DST}"

echo "==> Existierenden Agent ggf. abladen (idempotent)"
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true

echo "==> Agent laden"
launchctl bootstrap "gui/$(id -u)" "${PLIST_DST}"

echo "==> Status:"
launchctl print "gui/$(id -u)/${LABEL}" 2>&1 | head -15 || true

echo
echo "==> Setup fertig."
echo
echo "    Vault-Target:  ${VAULT_ROOT}/nova-lab/"
echo "    Schedule:      taeglich 00:10 (Pull von nova-hub via rsync)"
echo "    Logs:          ${LOG_FILE}"
echo
echo "    Erster Manual-Test:"
echo "      bash ${REPO_DIR}/scripts/obsidian_pull_from_hub.sh"
echo
echo "    Trigger via launchctl:"
echo "      launchctl kickstart gui/\$(id -u)/${LABEL}"
echo
echo "    Stop:"
echo "      launchctl bootout gui/\$(id -u) ${PLIST_DST}"
echo
echo "==> WICHTIG bei macOS Mojave+ und Vault unter ~/Documents/:"
echo
echo "    Falls 'Operation not permitted' im Log auftaucht, ist es TCC"
echo "    (Documents/Desktop/Downloads sind geschuetzte Folder). LaunchAgents"
echo "    haben keinen UI-Kontext und triggern keinen Permission-Prompt."
echo
echo "    Fix einmalig:"
echo "      System Settings -> Privacy & Security -> Full Disk Access"
echo "      + /usr/bin/rsync"
echo "      + /bin/bash"
echo "      + ${REPO_DIR}/scripts/obsidian_pull_from_hub.sh"
echo
echo "    Danach LaunchAgent neustarten:"
echo "      launchctl bootout gui/\$(id -u) ${PLIST_DST}"
echo "      launchctl bootstrap gui/\$(id -u) ${PLIST_DST}"
