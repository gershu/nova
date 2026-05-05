#!/usr/bin/env bash
# run.sh — Wrapper fuer nova-lab/modules/portfolio.import_xlsx.
# Erwartet Excel-Pfad ueber NOVA_PARAMS_FILE oder als CLI-Argument.
#
# Submit-Beispiel (manuell, on-demand):
#   ~/nova/scripts/nova_run.sh lab_portfolio_import nova-hub \
#       --params-file ~/jobs/portfolio_import.json
#
# params-file:
#   {"xlsx_path": "/Users/novaadm/nova_lab_input/portfolio.xlsx"}
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]      || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

# Pfad-Aufloesung: 1) CLI-Arg, 2) NOVA_PARAMS_FILE.xlsx_path
XLSX_PATH=""
if [[ $# -ge 1 ]]; then
    XLSX_PATH="$1"
elif [[ -n "${NOVA_PARAMS_FILE:-}" && -f "${NOVA_PARAMS_FILE}" ]]; then
    XLSX_PATH="$(python3 -c "import json,sys; print(json.load(open('${NOVA_PARAMS_FILE}')).get('xlsx_path',''))")"
fi

if [[ -z "${XLSX_PATH}" ]]; then
    echo "Fehler: kein Excel-Pfad angegeben." >&2
    echo "  Argument oder params-file mit {\"xlsx_path\": \"...\"} setzen." >&2
    exit 64
fi

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.portfolio.import_xlsx "${XLSX_PATH}"
