#!/usr/bin/env bash
# nova_run.sh — auf nova-hub als novaadm ausführen.
#
# Dispatched einen Workload an einen Worker via SSH und reicht optional
# eine Parameter-Datei (JSON o.ae.) durch. Phase-0-Variante eines Job-
# Dispatchers — kein DB-Backend, keine UI, kein State, kein Retry.
# Single shot: dispatcht, wartet auf Exit, gibt Exit-Code zurueck.
#
# Architektur:
#   1. Validiert Workload (existiert workloads/<name>/run.sh) und Worker
#      (steht als role=worker in config/nodes.yaml).
#   2. Falls --params-file gesetzt: scp die Datei nach /tmp/ auf dem Worker
#      und setzt NOVA_PARAMS_FILE auf den Remote-Pfad. Workload kann's
#      via os.environ.get('NOVA_PARAMS_FILE') lesen.
#   3. SSH zum Worker, ruft run.sh mit allen verbleibenden Args auf.
#   4. stdout/stderr streamen live zurueck, Exit-Code wird durchgereicht.
#   5. Aufraeumen: /tmp/-Datei auf Worker entfernen.
#
# Usage:
#   nova_run.sh <workload> <worker> [--params-file <pfad>] [-- <args...>]
#
# Examples:
#   nova_run.sh hello_world nova-w1
#   nova_run.sh csp_scanner nova-w2 --params-file ~/jobs/csp_aapl.json
#   nova_run.sh csp_scanner nova-w2 -- --extra-flag wert
#   nova_run.sh csp_scanner nova-w2 --params-file p.json -- --override-x

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
NODES_FILE="${REPO_DIR}/config/nodes.yaml"

# ---------- Args parsen ----------
if [[ $# -lt 2 ]]; then
  cat >&2 <<EOF
Usage: $(basename "$0") <workload> <worker> [--params-file <pfad>] [-- <args...>]

  <workload>      Name unter workloads/<name>/ (mit existierendem run.sh)
  <worker>        Hostname aus config/nodes.yaml (role=worker)
  --params-file   Optional, JSON o.ae. — wird auf den Worker kopiert,
                  Pfad als NOVA_PARAMS_FILE env-var verfuegbar.
  -- <args...>    Alles nach -- wird unveraendert an run.sh durchgereicht.

Examples:
  $(basename "$0") hello_world nova-w1
  $(basename "$0") csp_scanner nova-w2 --params-file params.json
  $(basename "$0") csp_scanner nova-w2 -- --extra-flag wert
EOF
  exit 64
fi

WORKLOAD="$1"; shift
WORKER="$1"; shift
PARAMS_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --params-file)
      [[ $# -lt 2 ]] && { echo "--params-file braucht einen Wert" >&2; exit 64; }
      PARAMS_FILE="$2"; shift 2 ;;
    --)
      shift; break ;;
    *)
      break ;;  # restliche Args gehen an run.sh
  esac
done

# ---------- Validierung Workload ----------
WORKLOAD_RUN="${REPO_DIR}/workloads/${WORKLOAD}/run.sh"
if [[ ! -f "${WORKLOAD_RUN}" ]]; then
  echo "Fehler: Workload '${WORKLOAD}' nicht gefunden (kein ${WORKLOAD_RUN})." >&2
  echo "Verfuegbare Workloads:" >&2
  ls -1 "${REPO_DIR}/workloads/" 2>/dev/null | sed 's/^/  - /' >&2
  exit 1
fi

# ---------- Validierung Worker gegen nodes.yaml ----------
if ! command -v yq >/dev/null 2>&1; then
  echo "Fehler: yq nicht im PATH (Brewfile listet's, sonst brew bundle)." >&2
  exit 1
fi

if ! yq -r '.nodes | to_entries | .[] | select(.value.role == "worker") | .key' \
       "${NODES_FILE}" | grep -qx "${WORKER}"; then
  echo "Fehler: '${WORKER}' ist kein bekannter Worker in ${NODES_FILE}." >&2
  echo "Bekannte Worker:" >&2
  yq -r '.nodes | to_entries | .[] | select(.value.role == "worker") | .key' \
       "${NODES_FILE}" | sed 's/^/  - /' >&2
  exit 1
fi

# ---------- Params-File optional auf Worker shippen ----------
REMOTE_PARAMS=""
if [[ -n "${PARAMS_FILE}" ]]; then
  if [[ ! -f "${PARAMS_FILE}" ]]; then
    echo "Fehler: --params-file '${PARAMS_FILE}' existiert nicht." >&2
    exit 1
  fi

  # Eindeutiger Pfad auf dem Worker (vermeidet Race bei concurrent Aufrufen)
  REMOTE_PARAMS="/tmp/nova_params_$(date +%s)_$$_$(basename "${PARAMS_FILE}")"
  echo "==> Shippe ${PARAMS_FILE} -> ${WORKER}:${REMOTE_PARAMS}"
  scp -q "${PARAMS_FILE}" "${WORKER}:${REMOTE_PARAMS}"
fi

# ---------- Remote-Aufruf bauen + ausfuehren ----------
REMOTE_CMD="~/nova/workloads/${WORKLOAD}/run.sh"
if [[ -n "${REMOTE_PARAMS}" ]]; then
  REMOTE_CMD="NOVA_PARAMS_FILE=${REMOTE_PARAMS} ${REMOTE_CMD}"
fi

echo "==> Dispatch: ${WORKLOAD} -> ${WORKER}"
echo "    ${REMOTE_CMD} $*"
echo

# stdin geschlossen (sonst klauten ssh-Calls evtl. lokales stdin),
# stdout/stderr stream live.
set +e
ssh -n "${WORKER}" "${REMOTE_CMD} $*"
RC=$?
set -e

# ---------- Cleanup ----------
if [[ -n "${REMOTE_PARAMS}" ]]; then
  ssh -n "${WORKER}" "rm -f ${REMOTE_PARAMS}" 2>/dev/null || true
fi

echo
if [[ ${RC} -eq 0 ]]; then
  echo "==> Workload erfolgreich (exit 0)."
else
  echo "==> Workload exit ${RC}." >&2
fi
exit ${RC}
