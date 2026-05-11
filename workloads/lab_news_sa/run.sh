#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.news_sa.
# Pullt Seeking-Alpha-Mails via Gmail-IMAP + persistiert in ref_sa_articles.
#
# Beispiele:
#   ~/nova/workloads/lab_news_sa/run.sh init
#   ~/nova/workloads/lab_news_sa/run.sh fetch
#   ~/nova/workloads/lab_news_sa/run.sh show AAPL
#   ~/nova/workloads/lab_news_sa/run.sh list-recent --limit 20
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.news_sa "$@"
