#!/usr/bin/env bash
# obsidian_pull_from_hub.sh — laeuft auf stefan_mac.
#
# Pullt den nova-Obsidian-Export von nova-hub via rsync in den lokalen
# Obsidian-Vault. Idempotent, safe, --delete fuer alte System-Files
# (instruments/ + daily/ etc.). Stefans eigene Notes im Vault bleiben
# ungeruehrt — wir synchronisieren nur den nova-lab/ Sub-Folder.
#
# Config via ENV (in ~/.zshrc oder shell-init):
#   NOVA_HUB_SSH_HOST   default 'nova-hub'  (host alias in ~/.ssh/config)
#   NOVA_HUB_USER       default 'novaadm'
#   OBSIDIAN_VAULT_ROOT default '/Users/stefan_mac/Documents/Obsidian Vault'
#
# Manual:  bash ~/nova/scripts/obsidian_pull_from_hub.sh
# Cron:    via LaunchAgent (dotfiles/launchd/de.gershu.nova.obsidian.pull.plist)

set -euo pipefail

NOVA_HUB_SSH_HOST="${NOVA_HUB_SSH_HOST:-nova-hub}"
NOVA_HUB_USER="${NOVA_HUB_USER:-novaadm}"
VAULT_ROOT="${OBSIDIAN_VAULT_ROOT:-/Users/stefan_mac/Documents/Obsidian Vault}"
VAULT_TARGET="${VAULT_ROOT}/nova-lab"

REMOTE_SRC="${NOVA_HUB_USER}@${NOVA_HUB_SSH_HOST}:nova_output/obsidian/"

# Vault muss existieren
if [[ ! -d "${VAULT_ROOT}" ]]; then
  echo "Fehler: Obsidian-Vault nicht gefunden: ${VAULT_ROOT}" >&2
  echo "  Setze OBSIDIAN_VAULT_ROOT-ENV oder erstelle das Verzeichnis." >&2
  exit 1
fi

mkdir -p "${VAULT_TARGET}"

# rsync mit:
#   -a     archive (preserve perms, timestamps)
#   -v     verbose
#   -z     compress
#   --delete  alte system-files entfernen (instruments/, daily/, etc.)
#   --exclude=.git*  keine Git-Artifacts kopieren (sind eh nicht im Source)
#
# WICHTIG: --delete entfernt NUR Files innerhalb des nova-lab/-Targets, NICHT
# andere Folder im Vault. Stefans Notes ausserhalb von nova-lab/ sind safe.
echo "==> Pulling nova-lab Obsidian-Export von ${NOVA_HUB_SSH_HOST}..."
if rsync -avz --delete \
      --exclude='.git*' --exclude='.DS_Store' \
      "${REMOTE_SRC}" "${VAULT_TARGET}/"; then
  echo "==> Pull abgeschlossen. Vault-Target: ${VAULT_TARGET}"
  exit 0
fi

RC=$?
# rsync exit 23 oder Operation-Not-Permitted: oft TCC-Permission-Issue
if [[ ${RC} -eq 23 || ${RC} -eq 11 ]]; then
  echo
  echo "==> rsync FEHLER (rc=${RC}). Wahrscheinliche Ursache:"
  echo "    macOS TCC-Protection auf ${VAULT_ROOT} (Documents/Desktop/Downloads"
  echo "    sind seit Mojave geschuetzt fuer LaunchAgents ohne UI-Kontext)."
  echo
  echo "    Fix einmalig:"
  echo "      System Settings -> Privacy & Security -> Full Disk Access"
  echo "      + /usr/bin/rsync"
  echo "      + /bin/bash"
  echo "      + $(realpath "$0")"
  echo
  echo "    Danach LaunchAgent neustarten:"
  echo "      launchctl bootout 'gui/\$(id -u)' ~/Library/LaunchAgents/de.gershu.nova.obsidian.pull.plist"
  echo "      launchctl bootstrap 'gui/\$(id -u)' ~/Library/LaunchAgents/de.gershu.nova.obsidian.pull.plist"
  echo
  echo "    Alternative: VAULT_ROOT auf TCC-freien Pfad setzen (ENV"
  echo "    OBSIDIAN_VAULT_ROOT=~/Obsidian_Sync ; Obsidian Vault dorthin verschieben)."
fi
exit ${RC}
