#!/usr/bin/env bash
# node_deploy.sh — auf jedem Node lokal oder remote ausführbar.
#
# Idempotent:
#   1. git pull (nova-Repo aktualisieren)
#   2. Dotfiles symlinken (zsh/.zshrc, zsh/.p10k.zsh)
#   3. brew bundle (Software installieren/aktualisieren)
#   4. Python-Umgebung: pyenv install (gemaess .python-version) + venv +
#      pip install -r requirements-lock.txt
#   5. Workload-Repos (config/workload_repos.txt): Sibling-Repos klonen/pullen,
#      damit Workload-Code unabhaengig von nova versioniert bleibt aber auf
#      jedem Node konsistent ist.

set -euo pipefail

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  echo "Fehler: ${REPO_DIR} ist kein Git-Repo. Erst node_bootstrap.sh ausführen." >&2
  exit 1
fi

# --- 1) Sourcen aktualisieren -----------------------------------------------
echo "==> [1/5] git pull in ${REPO_DIR}..."
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

echo "==> [2/5] Dotfiles linken..."
link_file "${REPO_DIR}/dotfiles/zsh/.zshrc"   "$HOME/.zshrc"
link_file "${REPO_DIR}/dotfiles/zsh/.p10k.zsh" "$HOME/.p10k.zsh"

# --- 3) brew bundle ---------------------------------------------------------
echo "==> [3/5] brew bundle ..."

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

# --- 4) Python venv + requirements ------------------------------------------
echo "==> [4/5] Python-Umgebung..."

VENV_DIR="${REPO_DIR}/.venv"
REQ_FILE="${REPO_DIR}/requirements.txt"
LOCK_FILE="${REPO_DIR}/requirements-lock.txt"
PY_VERSION_FILE="${REPO_DIR}/.python-version"

# pyenv-Setup (init laden, falls vorhanden — node_bootstrap installiert pyenv via brew).
if command -v pyenv >/dev/null 2>&1; then
  eval "$(pyenv init -)"

  # Pinned Python-Version installieren, falls .python-version vorhanden und
  # die Version noch nicht installiert ist. Erster Lauf kann mehrere Minuten
  # dauern (Source-Build).
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

# venv anlegen, falls nicht vorhanden. cd ins Repo, damit pyenv die
# .python-version aus dem CWD aufgreift und python3 die richtige Version ist.
if [[ ! -d "${VENV_DIR}" ]]; then
  echo "    Lege venv unter ${VENV_DIR} an..."
  ( cd "${REPO_DIR}" && python3 -m venv "${VENV_DIR}" )
fi

# pip + requirements (idempotent — bereits installierte Pakete in passender
# Version werden uebersprungen).
#
# Bevorzuge requirements-lock.txt (deterministisch, == gepinnte Versionen
# inklusive transitiver Deps). Fallback auf requirements.txt (Versions-Bereiche),
# falls Lock-File fehlt — z.B. waehrend Initial-Setup oder bewusster Regen.
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip setuptools wheel

if [[ -f "${LOCK_FILE}" ]]; then
  echo "    pip install -r requirements-lock.txt (deterministic)..."
  "${VENV_DIR}/bin/pip" install --quiet -r "${LOCK_FILE}"
elif [[ -f "${REQ_FILE}" ]]; then
  echo "    WARN: requirements-lock.txt fehlt — Fallback auf requirements.txt."
  echo "    pip install -r requirements.txt (Bereiche, NICHT byte-identisch)..."
  "${VENV_DIR}/bin/pip" install --quiet -r "${REQ_FILE}"
else
  echo "    WARN: weder requirements-lock.txt noch requirements.txt gefunden."
fi

# --- 5) Workload-Repos (Sibling-Repos) --------------------------------------
echo "==> [5/5] Workload-Repos synchronisieren..."

WORKLOAD_REPOS_FILE="${REPO_DIR}/config/workload_repos.txt"
if [[ -f "${WORKLOAD_REPOS_FILE}" ]]; then
  while IFS= read -r line; do
    # Kommentare + Leerzeilen ueberspringen
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue

    # Format: <lokales-verzeichnis> <git-url>
    local_dir="$(echo "${line}" | awk '{print $1}')"
    git_url="$(echo "${line}" | awk '{print $2}')"

    [[ -z "${local_dir}" || -z "${git_url}" ]] && continue

    target="${HOME}/${local_dir}"

    if [[ -d "${target}/.git" ]]; then
      echo "    git pull in ${target}..."
      git -C "${target}" pull --ff-only
    else
      echo "    git clone ${git_url} -> ${target}..."
      git clone "${git_url}" "${target}"
    fi
  done < "${WORKLOAD_REPOS_FILE}"
else
  echo "    (keine config/workload_repos.txt — uebersprungen)"
fi

echo
echo "==> Deploy fertig auf $(hostname) (NOVA_ROLE=${NOVA_ROLE:-unset})."
