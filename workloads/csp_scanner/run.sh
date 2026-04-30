#!/usr/bin/env bash
# run.sh — Entry-Point fuer den csp_scanner-Workload (Sibling-Repo-Modell).
#
# csp_scanner lebt auf jedem Node als eigenes git-Repo unter ~/csp_scanner.
# Wird durch node_deploy.sh Schritt 5 (Workload-Repos) mitgepullt — nicht
# dieser Workload-Ordner.
#
# Aufruf:
#   ~/nova/workloads/csp_scanner/run.sh
#   ssh nova-w2 '~/nova/workloads/csp_scanner/run.sh'

set -euo pipefail

# Per-Node Overrides laden (Tier 2: ~/.nova_env, gitignored, nicht auto-deployed).
# Idempotent: macht nichts, falls die Datei fehlt.
# shellcheck disable=SC1091
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
JOB_SRC_DIR="${NOVA_CSP_SCANNER_DIR:-$HOME/csp_scanner}"
OUTPUT_DIR="${HOME}/nova_output/csp_scanner"

# 3-Tier Konfig-Hierarchie fuer Watchlist + Settings:
#   Tier 1 — Defaults (csp_scanner-Repo Files)
WATCHLIST_PATH="config/watchlist.yaml"
SETTINGS_PATH="config/settings.yaml"

#   Tier 2 — Per-Node-Override aus ~/.nova_env (CSP_SCANNER_WATCHLIST/CSP_SCANNER_SETTINGS)
[[ -n "${CSP_SCANNER_WATCHLIST:-}" ]] && WATCHLIST_PATH="${CSP_SCANNER_WATCHLIST}"
[[ -n "${CSP_SCANNER_SETTINGS:-}"  ]] && SETTINGS_PATH="${CSP_SCANNER_SETTINGS}"

#   Tier 3 — Per-Job-Override aus NOVA_PARAMS_FILE (nova_submit JSON)
#   JSON-Format: {"watchlist": "config/foo.yaml", "settings": "config/bar.yaml"}
#   Beide Felder optional; was nicht gesetzt ist behaelt den Tier-2/Tier-1-Wert.
if [[ -n "${NOVA_PARAMS_FILE:-}" && -f "${NOVA_PARAMS_FILE}" ]]; then
  override_wl="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("watchlist",""))' "${NOVA_PARAMS_FILE}" 2>/dev/null || true)"
  override_st="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("settings",""))'  "${NOVA_PARAMS_FILE}" 2>/dev/null || true)"
  [[ -n "${override_wl}" ]] && WATCHLIST_PATH="${override_wl}"
  [[ -n "${override_st}" ]] && SETTINGS_PATH="${override_st}"
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Fehler: venv ${VENV_DIR} nicht gefunden — erst node_deploy.sh ausfuehren." >&2
  exit 1
fi
if [[ ! -d "${JOB_SRC_DIR}/.git" ]]; then
  echo "Fehler: ${JOB_SRC_DIR} ist kein Git-Repo." >&2
  echo "       node_deploy.sh klont es laut config/workload_repos.txt." >&2
  exit 1
fi

# Per-Node Output-Dir + Symlink ${JOB_SRC_DIR}/output -> ~/nova_output/csp_scanner/
mkdir -p "${OUTPUT_DIR}"
LINK="${JOB_SRC_DIR}/output"
if [[ -L "${LINK}" ]]; then
  if [[ "$(readlink "${LINK}")" != "${OUTPUT_DIR}" ]]; then
    rm -f "${LINK}"
    ln -s "${OUTPUT_DIR}" "${LINK}"
  fi
elif [[ -e "${LINK}" ]]; then
  mv "${LINK}" "${LINK}.bak.$(date +%Y%m%d%H%M%S)"
  ln -s "${OUTPUT_DIR}" "${LINK}"
else
  ln -s "${OUTPUT_DIR}" "${LINK}"
fi

# venv aktivieren (gemeinsamer nova-Cluster-venv, deps aus nova requirements-lock.txt)
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# CWD = csp_scanner-Repo, damit `python -m src.main` das src/-Package findet
# und die relativen --watchlist / --settings Pfade aufloesen.
cd "${JOB_SRC_DIR}"

<<<<<<< Updated upstream
echo "==> csp_scanner config:"
echo "    watchlist : ${WATCHLIST_PATH}"
echo "    settings  : ${SETTINGS_PATH}"

exec python -m src.main \
=======
exec "${VENV_DIR}/bin/python" -m src.main \
>>>>>>> Stashed changes
    --watchlist "${WATCHLIST_PATH}" \
    --settings "${SETTINGS_PATH}" \
    "$@"
