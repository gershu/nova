#!/usr/bin/env bash
# node_deploy.sh — auf jedem Node lokal oder remote ausführbar.
#
# Idempotent:
#   1. git pull (Repo aktualisieren)
#   2. Dotfiles symlinken (zsh/.zshrc, zsh/.p10k.zsh)
#   3. brew bundle (Software installieren/aktualisieren)

set -euo pipefail

REPO_DIR="$HOME/nova"

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  echo "Fehler: ${REPO_DIR} ist kein Git-Repo. Erst node_bootstrap.sh ausführen." >&2
  exit 1
fi

# --- 1) Sourcen aktualisieren -----------------------------------------------
echo "==> [1/3] git pull in ${REPO_DIR}..."
git -C "${REPO_DIR}" pull --ff-only

# --- 2) Dotfiles symlinken --------------------------------------------------
# link_file <quelle-im-repo> <ziel-im-home>
link_file() {
  local src="$1"
  local dst="$2"

  if [[ ! -e "${src}" ]]; then
    echo "    WARN: Quelle ${src} existiert nicht — überspringe."
    return
  fi

  # Wenn das Ziel bereits ein Symlink auf die richtige Quelle ist, fertig.
  if [[ -L "${dst}" ]] && [[ "$(readlink "${dst}")" == "${src}" ]]; then
    echo "    OK:  ${dst} -> ${src} (bereits korrekt)"
    return
  fi

  # Existierende Datei/Symlink mit Backup-Suffix sichern.
  if [[ -e "${dst}" || -L "${dst}" ]]; then
    local backup
    backup="${dst}.bak.$(date +%Y%m%d%H%M%S)"
    mv "${dst}" "${backup}"
    echo "    BACKUP: ${dst} -> ${backup}"
  fi

  ln -s "${src}" "${dst}"
  echo "    LINK:   ${dst} -> ${src}"
}

echo "==> [2/3] Dotfiles linken..."
link_file "${REPO_DIR}/dotfiles/zsh/.zshrc"   "$HOME/.zshrc"
link_file "${REPO_DIR}/dotfiles/zsh/.p10k.zsh" "$HOME/.p10k.zsh"

# --- 3) brew bundle ---------------------------------------------------------
echo "==> [3/3] brew bundle ..."

# Falls brew nicht im PATH ist (z.B. erste Shell nach Installation), shellenv laden.
if ! command -v brew >/dev/null 2>&1; then
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Fehler: 'brew' ist nicht im PATH. node_bootstrap.sh installiert es." >&2
  exit 1
fi

brew bundle --file="${REPO_DIR}/Brewfile"

echo
echo "==> Deploy fertig auf $(hostname) (NOVA_ROLE=${NOVA_ROLE:-unset})."
