#!/usr/bin/env bash
# node_set_name.sh — auf dem Ziel-Node ausführen.
#
# Setzt Hostname/Alias auf den uebergebenen nova-Namen und persistiert NOVA_ROLE
# in ~/.nova_role, damit zsh + p10k die korrekte Rollenfarbe waehlen.
#
# Naming-Konvention:
#   nova-hub        Control Plane (Hub)
#   nova-w<N>       Worker, durchnumeriert ab 1
#
# Rolle wird automatisch aus dem Namen abgeleitet:
#   nova-hub        -> NOVA_ROLE=HUB
#   nova-w<N>       -> NOVA_ROLE=WORKER
#
# Usage:
#   ./node_set_name.sh <hostname>
# Examples:
#   ./node_set_name.sh nova-hub
#   ./node_set_name.sh nova-w3

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <hostname>" >&2
  echo "  Erlaubt: nova-hub | nova-w<N>" >&2
  exit 64
fi

NEW_HOSTNAME="$1"

# Rolle ableiten + Namensformat validieren
if [[ "${NEW_HOSTNAME}" == "nova-hub" ]]; then
  ROLE="HUB"
elif [[ "${NEW_HOSTNAME}" =~ ^nova-w[0-9]+$ ]]; then
  ROLE="WORKER"
else
  echo "Ungueltiger Hostname '${NEW_HOSTNAME}'. Erlaubt: nova-hub | nova-w<N>" >&2
  exit 64
fi

echo "==> Setze HostName / LocalHostName / ComputerName auf '${NEW_HOSTNAME}'..."
sudo scutil --set HostName      "${NEW_HOSTNAME}"
sudo scutil --set LocalHostName "${NEW_HOSTNAME}"
sudo scutil --set ComputerName  "${NEW_HOSTNAME}"

# DNS-Cache leeren, damit Aenderungen sofort greifen.
sudo dscacheutil -flushcache 2>/dev/null || true
sudo killall -HUP mDNSResponder 2>/dev/null || true

echo "==> Schreibe NOVA_ROLE=${ROLE} nach ~/.nova_role..."
cat > "$HOME/.nova_role" <<EOF
# Wird von ~/.zshrc geladen — definiert die Rolle dieses Nodes.
# Erzeugt durch node_set_name.sh am $(date -u +"%Y-%m-%dT%H:%M:%SZ").
export NOVA_ROLE=${ROLE}
EOF
chmod 644 "$HOME/.nova_role"

echo "==> Fertig. Aktuelle Werte:"
echo "    HostName:      $(scutil --get HostName 2>/dev/null || echo '(nicht gesetzt)')"
echo "    LocalHostName: $(scutil --get LocalHostName 2>/dev/null || echo '(nicht gesetzt)')"
echo "    ComputerName:  $(scutil --get ComputerName 2>/dev/null || echo '(nicht gesetzt)')"
echo "    NOVA_ROLE:     ${ROLE}"

cat <<EOF

Hinweis: Eine neue zsh-Session oeffnen, damit NOVA_ROLE geladen ist und der
Prompt die richtige Farbe zeigt.
EOF
