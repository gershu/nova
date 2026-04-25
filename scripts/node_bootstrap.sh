#!/usr/bin/env bash
# node_bootstrap.sh — auf dem Ziel-Node nach node_set_name.sh ausführen.
#
# Idempotenter Initial-Setup-Schritt:
#   1. Homebrew installieren (falls nicht vorhanden)
#   2. nova-Repo nach ~/nova klonen (falls nicht vorhanden)
#   3. node_deploy.sh aufrufen (linkt Dotfiles, brew bundle)
#
# Voraussetzung: ~/.ssh/id_ed25519 ist auf dem Node und in GitHub als
# Deploy Key (read-only) für das nova-Repo eingetragen.

set -euo pipefail

REPO_URL="${NOVA_REPO_URL:-git@github.com:CHANGEME/nova.git}"
REPO_DIR="$HOME/nova"

echo "==> Schritt 1/3: Homebrew prüfen..."
if ! command -v brew >/dev/null 2>&1; then
  echo "    Homebrew nicht gefunden — installiere..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  # Homebrew-Pfad in aktuelle Shell laden (Apple Silicon vs. Intel)
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
else
  echo "    Homebrew ist bereits installiert ($(brew --version | head -n1))."
fi

echo "==> Schritt 2/3: nova-Repo unter ${REPO_DIR}..."
if [[ -d "${REPO_DIR}/.git" ]]; then
  echo "    Repo existiert bereits — überspringe clone."
else
  echo "    Klone ${REPO_URL} nach ${REPO_DIR}..."
  git clone "${REPO_URL}" "${REPO_DIR}"
fi

echo "==> Schritt 3/3: node_deploy.sh ausführen..."
"${REPO_DIR}/scripts/node_deploy.sh"

cat <<EOF

==> Bootstrap fertig.
    Eine neue zsh-Session öffnen, damit Dotfiles und NOVA_ROLE aktiv sind.
EOF
