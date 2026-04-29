#!/usr/bin/env bash
# nova_submit.sh — auf nova-hub als novaadm.
#
# Schreibt einen Job-Spec ins ~/nova_jobs/queue/, der vom nova_picker
# (launchd-Agent, alle ~10s) abgeholt und an den Worker dispatched wird.
#
# Im Gegensatz zu nova_run.sh (synchron, blockiert bis Job fertig) ist
# nova_submit asynchron: Aufruf return't sofort mit der Job-ID.
#
# Usage:
#   nova_submit.sh <workload> <worker> [--params-file <pfad>] [-- <args...>]
#
# Examples:
#   nova_submit.sh hello_world nova-w1
#   nova_submit.sh csp_scanner nova-w2 --params-file ~/jobs/aapl.json
#   nova_submit.sh csp_scanner nova-w2 --params-file p.json -- --debug

set -euo pipefail

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
NODES_FILE="${REPO_DIR}/config/nodes.yaml"
JOBS_DIR="${HOME}/nova_jobs"
QUEUE_DIR="${JOBS_DIR}/queue"

# ---------- Args parsen (selbe Logik wie nova_run.sh) ----------
if [[ $# -lt 2 ]]; then
  cat >&2 <<EOF
Usage: $(basename "$0") <workload> <worker> [--params-file <pfad>] [-- <args...>]

Schreibt einen Job-Spec in ${QUEUE_DIR}/. Picker holt ihn ab.
EOF
  exit 64
fi

WORKLOAD="$1"; shift
WORKER="$1"; shift
PARAMS_FILE=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --params-file)
      [[ $# -lt 2 ]] && { echo "--params-file braucht einen Wert" >&2; exit 64; }
      PARAMS_FILE="$2"; shift 2 ;;
    --)
      shift; EXTRA_ARGS=("$@"); break ;;
    *)
      EXTRA_ARGS+=("$1"); shift ;;
  esac
done

# ---------- Validierung Workload + Worker ----------
WORKLOAD_RUN="${REPO_DIR}/workloads/${WORKLOAD}/run.sh"
if [[ ! -f "${WORKLOAD_RUN}" ]]; then
  echo "Fehler: Workload '${WORKLOAD}' nicht gefunden (kein ${WORKLOAD_RUN})." >&2
  exit 1
fi

if ! command -v yq >/dev/null 2>&1; then
  echo "Fehler: yq nicht im PATH." >&2
  exit 1
fi

if ! yq -r '.nodes | to_entries | .[] | select(.value.role == "worker") | .key' \
       "${NODES_FILE}" | grep -qx "${WORKER}"; then
  echo "Fehler: '${WORKER}' ist kein bekannter Worker in ${NODES_FILE}." >&2
  exit 1
fi

# ---------- Verzeichnisse anlegen ----------
mkdir -p "${QUEUE_DIR}" "${JOBS_DIR}/running" "${JOBS_DIR}/done" "${JOBS_DIR}/logs"

# ---------- Job-ID + Job-Spec bauen ----------
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
JOB_ID="${TIMESTAMP}-${WORKLOAD}-pid$$"
SUBMITTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SUBMITTED_BY="${USER:-$(whoami)}"

# Params optional als Inline-Objekt (kein File-Pfad — Queue muss self-contained sein)
PARAMS_JSON="null"
if [[ -n "${PARAMS_FILE}" ]]; then
  if [[ ! -f "${PARAMS_FILE}" ]]; then
    echo "Fehler: --params-file '${PARAMS_FILE}' existiert nicht." >&2
    exit 1
  fi
  # Validieren dass es JSON ist + minimieren
  if ! PARAMS_JSON="$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))' "${PARAMS_FILE}" 2>/dev/null)"; then
    echo "Fehler: '${PARAMS_FILE}' ist kein gueltiges JSON." >&2
    exit 1
  fi
fi

# extra_args als JSON-Array
EXTRA_JSON="[]"
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  EXTRA_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${EXTRA_ARGS[@]}")"
fi

# Spec zusammenbauen via python (sauberes JSON)
SPEC_JSON="$(python3 - <<EOF
import json
spec = {
  "job_id":        "${JOB_ID}",
  "workload":      "${WORKLOAD}",
  "worker":        "${WORKER}",
  "params":        ${PARAMS_JSON},
  "extra_args":    ${EXTRA_JSON},
  "submitted_at":  "${SUBMITTED_AT}",
  "submitted_by":  "${SUBMITTED_BY}",
  "status":        "queued"
}
print(json.dumps(spec, indent=2))
EOF
)"

# ---------- Atomar in queue/ schreiben (tmp + mv) ----------
TMP="$(mktemp /tmp/nova_submit.XXXXXX.json)"
echo "${SPEC_JSON}" > "${TMP}"
mv "${TMP}" "${QUEUE_DIR}/${JOB_ID}.json"

echo "${JOB_ID}"
