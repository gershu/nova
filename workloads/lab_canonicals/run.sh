#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.canonicals.
# Pure CLI (kein Daemon) — Pflege der Identitaets-Aggregation
# fuer Multi-Class-Listings (GOOGL+GOOG, BRK.A+BRK.B etc.).
#
# Beispiele:
#   ~/nova/workloads/lab_canonicals/run.sh init
#   ~/nova/workloads/lab_canonicals/run.sh list
#   ~/nova/workloads/lab_canonicals/run.sh show ALPHABET
#   ~/nova/workloads/lab_canonicals/run.sh add-canonical FOX --name "Fox Corp."
#   ~/nova/workloads/lab_canonicals/run.sh add-member FOX IB:FOXA:USD
#   ~/nova/workloads/lab_canonicals/run.sh which IB:GOOG:USD
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.canonicals "$@"
