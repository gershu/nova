#!/usr/bin/env bash
# provision_node.sh — auf nova-dev ausführen.
#
# Kopiert das SSH-Material (id_ed25519, id_ed25519.pub, authorized_keys, config)
# vom novaadm-Home auf einen neuen Mac. Voraussetzung: novaadm existiert auf
# dem Ziel-Mac, Remote Login ist aktiv, und der Ziel-Hostname ist im LAN
# erreichbar (anfangs noch nicht nova-<env>, sondern z.B. der per macOS-Setup
# vergebene Default-Name).
#
# Usage:
#   ./provision_node.sh <ziel-hostname-oder-ip> <env>
# Example:
#   ./provision_node.sh new-mac.local UAT

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

==> Provisioning erfolgreich.
    Nächste Schritte auf dem neuen Node (per SSH einloggen):

      ssh ${USER_ON_TARGET}@${TARGET}
      git clone git@github.com:<user>/nova.git ~/nova
      ~/nova/scripts/node_set_name.sh ${ENV_NAME}
      ~/nova/scripts/node_bootstrap.sh

    Danach ist der Node als nova-$(echo "${ENV_NAME}" | tr '[:upper:]' '[:lower:]') erreichbar.
EOF
