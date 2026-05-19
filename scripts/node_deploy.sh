#!/usr/bin/env bash
# node_deploy.sh — auf jedem Node lokal oder remote ausführbar.
#
# Idempotent:
#   1. git pull (nova-Repo aktualisieren — beinhaltet Code + Workloads + Configs)
#   2. Dotfiles symlinken (zsh, ssh/config, git, vim aus dotfiles/)
#   3. brew bundle (Software installieren/aktualisieren)
#   4. Python-Umgebung: pyenv install (gemaess .python-version) + venv +
#      pip install -r requirements-lock.txt
#   5. nbstripout-Filter aktivieren (Jupyter-Auto-Saves clean halten)

set -euo pipefail

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  echo "Fehler: ${REPO_DIR} ist kein Git-Repo. Erst node_bootstrap.sh ausführen." >&2
  exit 1
fi

# --- 1) Sourcen aktualisieren -----------------------------------------------

# Bereinige Jupyter-Auto-Saves BEVOR git pull laeuft — sonst blockiert
# dirty working tree den fast-forward.
NBSTRIPOUT_BIN="${REPO_DIR}/.venv/bin/nbstripout"
if [[ -x "${NBSTRIPOUT_BIN}" ]]; then
  dirty="$(cd "${REPO_DIR}" && git diff --name-only -- '*.ipynb' 2>/dev/null || true)"
  if [[ -n "${dirty}" ]]; then
    echo "==> [1/5] Strippe dirty .ipynb via nbstripout..."
    while IFS= read -r nb; do
      [[ -z "${nb}" ]] && continue
      "${NBSTRIPOUT_BIN}" "${REPO_DIR}/${nb}" 2>/dev/null || true
    done <<< "${dirty}"
  fi
fi

echo "==> [1/5] git pull in ${REPO_DIR}..."
git -C "${REPO_DIR}" pull --ff-only

# --- 2) Dotfiles symlinken --------------------------------------------------

link_file() {
  local src="$1"
  local dst="$2"

  if [[ ! -e "${src}" ]]; then
    echo "    WARN: Quelle ${src} existiert nicht — überspringe."
    return
  fi

  if [[ -L "${dst}" ]] && [[ "$(readlink "${dst}")" == "${src}" ]]; then
    echo "    OK:  ${dst} -> ${src} (bereits korrekt)"
    return
  fi

  if [[ -e "${dst}" || -L "${dst}" ]]; then
    local backup
    backup="${dst}.bak.$(date +%Y%m%d%H%M%S)"
    mv "${dst}" "${backup}"
    echo "    BACKUP: ${dst} -> ${backup}"
  fi

  ln -s "${src}" "${dst}"
  echo "    LINK:   ${dst} -> ${src}"
}

echo "==> [2/5] Dotfiles linken..."
[[ -d "$HOME/.ssh" ]] || { mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"; }

link_file "${REPO_DIR}/dotfiles/zsh/.zshrc"     "$HOME/.zshrc"
link_file "${REPO_DIR}/dotfiles/zsh/.p10k.zsh"  "$HOME/.p10k.zsh"
link_file "${REPO_DIR}/dotfiles/ssh/config"     "$HOME/.ssh/config"
link_file "${REPO_DIR}/dotfiles/git/.gitconfig" "$HOME/.gitconfig"
link_file "${REPO_DIR}/dotfiles/vim/.vimrc"     "$HOME/.vimrc"

# --- 3) brew bundle ---------------------------------------------------------

echo "==> [3/5] brew bundle ..."

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

# --- 4) Python venv + requirements ------------------------------------------

echo "==> [4/5] Python-Umgebung..."

VENV_DIR="${REPO_DIR}/.venv"
REQ_FILE="${REPO_DIR}/requirements.txt"
LOCK_FILE="${REPO_DIR}/requirements-lock.txt"
PY_VERSION_FILE="${REPO_DIR}/.python-version"

if command -v pyenv >/dev/null 2>&1; then
  eval "$(pyenv init -)"

  if [[ -f "${PY_VERSION_FILE}" ]]; then
    PY_VERSION="$(tr -d '[:space:]' < "${PY_VERSION_FILE}")"
    if [[ -n "${PY_VERSION}" ]] && ! pyenv versions --bare | grep -qx "${PY_VERSION}"; then
      echo "    pyenv install ${PY_VERSION} (kann mehrere Minuten dauern)..."
      pyenv install "${PY_VERSION}"
    fi
  fi
else
  echo "    WARN: pyenv nicht im PATH — verwende System-python3."
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "    Lege venv unter ${VENV_DIR} an..."
  ( cd "${REPO_DIR}" && python3 -m venv "${VENV_DIR}" )
fi

"${VENV_DIR}/bin/pip" install --quiet --upgrade pip setuptools wheel

if [[ -f "${LOCK_FILE}" ]]; then
  echo "    pip install -r requirements-lock.txt (deterministic)..."
  "${VENV_DIR}/bin/pip" install --quiet -r "${LOCK_FILE}"
elif [[ -f "${REQ_FILE}" ]]; then
  echo "    WARN: requirements-lock.txt fehlt — Fallback auf requirements.txt."
  "${VENV_DIR}/bin/pip" install --quiet -r "${REQ_FILE}"
else
  echo "    WARN: weder requirements-lock.txt noch requirements.txt gefunden."
fi

# --- 5) nbstripout-Filter aktivieren ----------------------------------------

echo "==> [5/5] nbstripout-Filter..."
if [[ -x "${NBSTRIPOUT_BIN}" ]] && [[ -f "${REPO_DIR}/.gitattributes" ]]; then
  if grep -q 'nbstripout' "${REPO_DIR}/.gitattributes" 2>/dev/null; then
    ( cd "${REPO_DIR}" && "${NBSTRIPOUT_BIN}" --install --attributes .gitattributes ) 2>/dev/null || true
    echo "    nbstripout-Filter aktiv."
  fi
fi

echo
echo "==> Deploy fertig auf $(hostname) (NOVA_ROLE=${NOVA_ROLE:-unset})."
