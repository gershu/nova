#!/usr/bin/env bash
# node_set_name.sh — auf dem Ziel-Node ausführen.
#
# Setzt Hostname/Alias auf nova-<env> und persistiert NOVA_ROLE in ~/.nova_role,
# damit die zsh-/p10k-Konfiguration die korrekte Rollenfarbe wählt.
#
# Usage:
#   ./node_set_name.sh <env>
# Example:
#   ./node_set_name.sh UAT

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <env>" >&2
  echo "  <env> ist eines von: DEV UAT PROD" >&2
  exit 64
fi

ENV_NAME="$1"

case "$ENV_NAME" in
  DEV|UAT|PROD) ;;
  *)
    echo "Ungültiges Environment '$ENV_NAME'. Erlaubt: DEV UAT PROD" >&2
    exit 64
    ;;
esac

ENV_LOWER="$(echo "$ENV_NAME" | tr '[:upper:]' '[:lower:]')"
NEW_HOSTNAME="nova-${ENV_LOWER}"

echo "==> Setze HostName / LocalHostName / ComputerName auf '${NEW_HOSTNAME}'..."
sudo scutil --set HostName      "${NEW_HOSTNAME}"
sudo scutil --set LocalHostName "${NEW_HOSTNAME}"
sudo scutil --set ComputerName  "${NEW_HOSTNAME}"

# DNS-Cache leeren, damit Änderungen sofort greifen.
sudo dscacheutil -flushcache 2>/dev/null || true
sudo killall -HUP mDNSResponder 2>/dev/null || true

echo "==> Schreibe NOVA_ROLE=${ENV_NAME} nach ~/.nova_role..."
cat > "$HOME/.nova_role" <<EOF
# Wird von ~/.zshrc geladen — definiert die Rolle dieses Nodes.
# Erzeugt durch node_set_name.sh am $(date -u +"%Y-%m-%dT%H:%M:%SZ").
export NOVA_ROLE=${ENV_NAME}
EOF
chmod 644 "$HOME/.nova_role"

echo "==> Fertig. Aktuelle Werte:"
echo "    HostName:      $(scutil --get HostName 2>/dev/null || echo '(nicht gesetzt)')"
echo "    LocalHostName: $(scutil --get LocalHostName 2>/dev/null || echo '(nicht gesetzt)')"
echo "    ComputerName:  $(scutil --get ComputerName 2>/dev/null || echo '(nicht gesetzt)')"
echo "    NOVA_ROLE:     ${ENV_NAME}"

cat <<EOF

Hinweis: Eine neue zsh-Session öffnen, damit NOVA_ROLE geladen ist und der
Prompt die richtige Farbe zeigt.
EOF
