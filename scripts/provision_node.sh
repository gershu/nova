#!/usr/bin/env bash
# provision_node.sh — auf nova-dev als novaadm ausführen.
#
# Kopiert SSH-Material (id_ed25519, id_ed25519.pub, authorized_keys, config)
# vom novaadm-Home auf einen neuen Mac, damit der Node anschließend
# key-basiert erreichbar ist und vom GitHub-Repo per Deploy Key pullen kann.
#
# Voraussetzungen am Ziel-Mac (manuell vorher erledigen):
#   - macOS frisch aufgesetzt
#   - User `novaadm` existiert (Standard-Account)
#   - Remote Login (SSH) aktiv (System Settings → General → Sharing)
#   - Hostname per `sudo scutil --set {Host,LocalHost,Computer}Name nova-<env>`
#     bereits auf nova-<env> gesetzt (sonst Workflow umständlich, siehe README)
#   - Ziel-Mac im LAN erreichbar (mDNS oder DNS)
#
# Was dieses Script NICHT macht (kommt im node_bootstrap.sh-Schritt am Node):
#   - Homebrew installieren
#   - Repo nach ~/nova klonen
#   - Dotfiles linken / brew bundle
#
# Usage:
#   ./provision_node.sh <ziel-hostname-oder-ip> <env>
# Example:
#   ./provision_node.sh nova-uat UAT

set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <ziel-hostname-oder-ip> <env>" >&2
  echo "  <env> ist eines von: DEV UAT PROD" >&2
  exit 64
fi

TARGET="$1"
ENV_NAME="$2"

case "$ENV_NAME" in
  DEV|UAT|PROD) ;;
  *)
    echo "Ungültiges Environment '$ENV_NAME'. Erlaubt: DEV UAT PROD" >&2
    exit 64
    ;;
esac

# Annahme: novaadm ist der lokale User.
USER_ON_TARGET="novaadm"

echo "==> Verbinde initial zu ${USER_ON_TARGET}@${TARGET} (Passwort wird einmalig benötigt, falls Key noch nicht akzeptiert)..."
ssh -o StrictHostKeyChecking=accept-new "${USER_ON_TARGET}@${TARGET}" \
  'mkdir -p ~/.ssh && chmod 700 ~/.ssh'

echo "==> Kopiere SSH-Material via rsync..."
# '|| true', damit fehlende Dateien (z.B. config) das Provisioning nicht abbrechen.
rsync -av "$HOME/.ssh/id_ed25519"      "${USER_ON_TARGET}@${TARGET}:~/.ssh/" 2>/dev/null || true
rsync -av "$HOME/.ssh/id_ed25519.pub"  "${USER_ON_TARGET}@${TARGET}:~/.ssh/" 2>/dev/null || true
rsync -av "$HOME/.ssh/authorized_keys" "${USER_ON_TARGET}@${TARGET}:~/.ssh/" 2>/dev/null || true
rsync -av "$HOME/.ssh/config"          "${USER_ON_TARGET}@${TARGET}:~/.ssh/" 2>/dev/null || true

echo "==> Korrigiere Permissions auf dem Ziel und zeige Status..."
ssh "${USER_ON_TARGET}@${TARGET}" '
  chmod 600 ~/.ssh/id_ed25519     2>/dev/null || true
  chmod 644 ~/.ssh/id_ed25519.pub 2>/dev/null || true
  chmod 600 ~/.ssh/authorized_keys 2>/dev/null || true
  chmod 600 ~/.ssh/config         2>/dev/null || true
  echo "Host:    $(hostname)"
  echo "User:    $(whoami)"
  echo "OS:      $(sw_vers -productVersion 2>/dev/null || uname -sr)"
'

cat <<EOF

==> Provisioning erfolgreich. SSH-Keys liegen jetzt auf ${TARGET}.

    Nächste Schritte am neuen Node (per SSH einloggen):

      ssh ${USER_ON_TARGET}@${TARGET}
      git clone git@github.com:gershu/nova.git ~/nova
      ~/nova/scripts/node_bootstrap.sh

    node_bootstrap.sh installiert brew, überspringt den Clone (existiert nun)
    und ruft node_deploy.sh auf — am Ende ist der Node deploy-fertig.

    Falls der Hostname noch NICHT nova-$(echo "${ENV_NAME}" | tr '[:upper:]' '[:lower:]') ist, vorher zusätzlich:
      ~/nova/scripts/node_set_name.sh ${ENV_NAME}
EOF
