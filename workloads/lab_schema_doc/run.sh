#!/usr/bin/env bash
# run.sh — Wrapper fuer modules.schema_doc.
# Generiert DBML aus DuckDB-Schema oder SQL-DDL-Files. docs/data_model.dbml
# ist Golden-Source (handgepflegt); dieses Tool generiert
# data_model.generated.dbml (Reference / Diff-Check / Subset-Extraktion).
#
# Beispiele:
#   ~/nova/workloads/lab_schema_doc/run.sh regenerate
#   ~/nova/workloads/lab_schema_doc/run.sh list-tables
#   ~/nova/workloads/lab_schema_doc/run.sh diff
#   ~/nova/workloads/lab_schema_doc/run.sh regenerate \
#       --tables pos_holdings,ref_instruments,v_holdings_mtm \
#       --output docs/data_model.portfolio.dbml
#   ~/nova/workloads/lab_schema_doc/run.sh regenerate \
#       --sql 'modules/portfolio/sql/*.sql' \
#       --output /tmp/portfolio_model.dbml
set -euo pipefail
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
VENV_DIR="${REPO_DIR}/.venv"
LAB_DIR="${HOME}/nova-lab"

[[ -d "${VENV_DIR}" ]]     || { echo "Fehler: venv fehlt." >&2; exit 1; }
[[ -d "${LAB_DIR}/.git" ]] || { echo "Fehler: ${LAB_DIR} kein Git-Repo." >&2; exit 1; }

source "${VENV_DIR}/bin/activate"
cd "${LAB_DIR}"
exec python -m modules.schema_doc "$@"
