#!/usr/bin/env bash
# provision_node.sh — auf nova-hub als novaadm ausführen.
#
# Kopiert SSH-Material (id_ed25519, id_ed25519.pub, authorized_keys, config)
# vom novaadm-Home auf einen neuen Worker-Mac, damit der Node anschließend
# key-basiert erreichbar ist und vom GitHub-Repo per Deploy Key pullen kann.
#
# Voraussetzungen am Ziel-Mac (manuell vorher erledigen):
#   - macOS frisch aufgesetzt
#   - User `novaadm` existiert (Standard-Account)
#   - Remote Login (SSH) aktiv (System Settings → General → Sharing)
#   - Hostname per `sudo scutil --set {Host,LocalHost,Computer}Name nova-w<N>`
#     bereits auf nova-w<N> gesetzt (sonst Workflow umständlich, siehe README)
#   - Ziel-Mac im LAN erreichbar (mDNS oder DNS)
#
# Was dieses Script NICHT macht (kommt im node_bootstrap.sh-Schritt am Node):
#   - Homebrew installieren
#   - Repo nach ~/nova klonen
#   - Dotfiles linken / brew bundle
#
# Usage:
#   ./provision_node.sh <hostname>
# Example:
#   ./provision_node.sh nova-w3

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <hostname>" >&2
  echo "  <hostname> z.B. nova-w3 (muss bereits am Ziel-Mac als HostName gesetzt sein)" >&2
  exit 64
fi

TARGET="$1"

# Naming validieren: nur Worker werden ueber dieses Script provisioniert.
# nova-hub wird manuell aufgesetzt (siehe README — nova-hub ist die einzige
# Maschine die dieses Script ausfuehrt; sich selbst kann sie nicht provisionieren).
if [[ ! "${TARGET}" =~ ^nova-w[0-9]+$ ]]; then
  echo "Ungueltiger Hostname '${TARGET}'. Erwartet: nova-w<N> (z.B. nova-w3)." >&2
  exit 64
fi

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

    Falls der Hostname noch NICHT ${TARGET} ist, vorher zusätzlich:
      ~/nova/scripts/node_set_name.sh ${TARGET}

    Danach: ${TARGET} im config/nodes.yaml mit echten chip/ram_gb/tags Werten
    aktualisieren, committen, pushen — damit cluster_status.sh den Node sieht.
EOF
