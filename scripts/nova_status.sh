#!/usr/bin/env bash
# nova_status.sh — auf nova-hub. Übersicht des Job-Queue-Zustands.
#
# Usage:
#   nova_status.sh                 # Counts + letzte 10 Done
#   nova_status.sh --recent <N>    # letzte N Done
#   nova_status.sh --job <id>      # voller Spec eines Jobs
#   nova_status.sh --log <id>      # Log eines Jobs (cat)

set -euo pipefail

JOBS_DIR="${HOME}/nova_jobs"
QUEUE_DIR="${JOBS_DIR}/queue"
RUNNING_DIR="${JOBS_DIR}/running"
DONE_DIR="${JOBS_DIR}/done"
LOGS_DIR="${JOBS_DIR}/logs"

count_files() { ls -1 "$1"/*.json 2>/dev/null | wc -l | tr -d ' '; }

if [[ $# -eq 0 ]]; then
  q="$(count_files "${QUEUE_DIR}")"
  r="$(count_files "${RUNNING_DIR}")"
  d="$(count_files "${DONE_DIR}")"
  echo "queue:   ${q}"
  echo "running: ${r}"
  echo "done:    ${d}"
  echo
  echo "letzte 10 Jobs (done):"
  printf '%-50s | %-8s | %-9s | %s\n' "JOB_ID" "STATUS" "EXIT" "WORKER"
  printf -- '---------------------------------------------------+----------+-----------+----------\n'
  ls -1t "${DONE_DIR}"/*.json 2>/dev/null | head -10 | while read -r f; do
    python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
print(f"{d[\"job_id\"][:50]:<50} | {d.get(\"status\",\"?\"):<8} | {str(d.get(\"exit_code\",\"-\")):<9} | {d.get(\"worker\",\"-\")}")
' "$f"
  done
  exit 0
fi

case "$1" in
  --recent)
    [[ $# -lt 2 ]] && { echo "--recent <N>" >&2; exit 64; }
    N="$2"
    ls -1t "${DONE_DIR}"/*.json 2>/dev/null | head -"${N}" | while read -r f; do
      python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
print(f"{d[\"job_id\"]} | {d.get(\"status\",\"?\"):<8} | exit={d.get(\"exit_code\",\"-\")} | {d.get(\"worker\",\"-\")}")
' "$f"
    done
    ;;
  --job)
    [[ $# -lt 2 ]] && { echo "--job <id>" >&2; exit 64; }
    ID="$2"
    for d in "${QUEUE_DIR}" "${RUNNING_DIR}" "${DONE_DIR}"; do
      f="${d}/${ID}.json"
      if [[ -f "${f}" ]]; then
        echo "Found in: $(basename "${d}")"
        cat "${f}"
        exit 0
      fi
    done
    echo "Job ${ID} nicht gefunden in queue/running/done." >&2
    exit 1
    ;;
  --log)
    [[ $# -lt 2 ]] && { echo "--log <id>" >&2; exit 64; }
    ID="$2"
    LOG="${LOGS_DIR}/${ID}.log"
    if [[ -f "${LOG}" ]]; then
      cat "${LOG}"
    else
      echo "Log ${LOG} nicht gefunden." >&2
      exit 1
    fi
    ;;
  *)
    echo "Unbekannte Option: $1" >&2
    exit 64
    ;;
esac
