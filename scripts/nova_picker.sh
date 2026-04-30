#!/usr/bin/env bash
# nova_picker.sh — wird von launchd auf nova-hub alle ~10 Sekunden gestartet.
#
# Single-shot: claimed alle Jobs aus ~/nova_jobs/queue/ (atomar via mv nach
# running/), dispatched via nova_run.sh, schreibt Ergebnis nach done/.
# Beim nächsten launchd-Tick wird neu geprüft.
#
# Concurrency-Schutz: mkdir-basierter Lock — gleichzeitige Picker-Instanzen
# (z.B. wenn ein Job länger laufen würde als StartInterval) werden gestoppt.

set -uo pipefail   # -e nicht: ein einzelner Job-Fehler darf den Picker-Run nicht abbrechen

# launchd-spawned processes haben minimal env — PATH selbst setzen
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
JOBS_DIR="${HOME}/nova_jobs"
QUEUE_DIR="${JOBS_DIR}/queue"
RUNNING_DIR="${JOBS_DIR}/running"
DONE_DIR="${JOBS_DIR}/done"
LOGS_DIR="${JOBS_DIR}/logs"
LOCK_DIR="${JOBS_DIR}/.picker.lock"

mkdir -p "${QUEUE_DIR}" "${RUNNING_DIR}" "${DONE_DIR}" "${LOGS_DIR}"

# ---------- Lock ----------
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  # Anderer Picker laeuft bereits, oder Lock ist stale
  # Stale-Detection: Lock aelter als 1h -> abraeumen + retry
  if [[ -d "${LOCK_DIR}" ]]; then
    LOCK_AGE_MIN=$(( ($(date +%s) - $(stat -f %m "${LOCK_DIR}" 2>/dev/null || echo 0)) / 60 ))
    if [[ ${LOCK_AGE_MIN} -gt 60 ]]; then
      echo "[$(date -u +%FT%TZ)] Stale lock (${LOCK_AGE_MIN} min) — entferne + retry"
      rmdir "${LOCK_DIR}" 2>/dev/null || true
      mkdir "${LOCK_DIR}" 2>/dev/null || exit 0
    else
      exit 0
    fi
  fi
fi
trap 'rmdir "${LOCK_DIR}" 2>/dev/null || true' EXIT

# ---------- Iteration ueber Jobs ----------
shopt -s nullglob
JOBS=("${QUEUE_DIR}"/*.json)
[[ ${#JOBS[@]} -eq 0 ]] && exit 0

echo "[$(date -u +%FT%TZ)] Picker: ${#JOBS[@]} Job(s) in queue"

for spec_file in "${JOBS[@]}"; do
  job_id="$(basename "${spec_file}" .json)"
  running_file="${RUNNING_DIR}/${job_id}.json"
  done_file="${DONE_DIR}/${job_id}.json"
  log_file="${LOGS_DIR}/${job_id}.log"

  # Atomar claimen via mv (queue -> running)
  if ! mv "${spec_file}" "${running_file}" 2>/dev/null; then
    echo "[$(date -u +%FT%TZ)] Konnte ${job_id} nicht claimen (race?)" >&2
    continue
  fi

  echo "[$(date -u +%FT%TZ)] Dispatching ${job_id}"

  # Spec parsen
  workload="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["workload"])' "${running_file}")"
  worker="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["worker"])' "${running_file}")"

  # Params (optional) als File auf hub-side bereitstellen — nova_run.sh shipt's
  params_file=""
  has_params="$(python3 -c 'import json,sys; print(1 if json.load(open(sys.argv[1])).get("params") else 0)' "${running_file}")"
  if [[ "${has_params}" == "1" ]]; then
    params_file="$(mktemp /tmp/nova_picker_params.XXXXXX.json)"
    python3 -c 'import json,sys; json.dump(json.load(open(sys.argv[1]))["params"], open(sys.argv[2],"w"))' \
      "${running_file}" "${params_file}"
  fi

  # extra_args als Bash-Array (bash-3-kompatibel, kein mapfile)
  extra_args=()
  while IFS= read -r arg; do
    extra_args+=("${arg}")
  done < <(python3 -c 'import json,sys; [print(a) for a in json.load(open(sys.argv[1])).get("extra_args", [])]' "${running_file}")

  # started_at in Spec eintragen
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  python3 - "${running_file}" "${started_at}" <<'PY'
import json, sys
p, ts = sys.argv[1], sys.argv[2]
d = json.load(open(p))
d["status"] = "running"
d["started_at"] = ts
json.dump(d, open(p, "w"), indent=2)
PY

  # nova_run.sh aufrufen, stdout+stderr in log_file
  set +e
  if [[ -n "${params_file}" ]]; then
    if [[ ${#extra_args[@]} -gt 0 ]]; then
      "${REPO_DIR}/scripts/nova_run.sh" "${workload}" "${worker}" --params-file "${params_file}" -- "${extra_args[@]}" >"${log_file}" 2>&1
    else
      "${REPO_DIR}/scripts/nova_run.sh" "${workload}" "${worker}" --params-file "${params_file}" >"${log_file}" 2>&1
    fi
  else
    if [[ ${#extra_args[@]} -gt 0 ]]; then
      "${REPO_DIR}/scripts/nova_run.sh" "${workload}" "${worker}" -- "${extra_args[@]}" >"${log_file}" 2>&1
    else
      "${REPO_DIR}/scripts/nova_run.sh" "${workload}" "${worker}" >"${log_file}" 2>&1
    fi
  fi
  rc=$?

  [[ -n "${params_file}" ]] && rm -f "${params_file}"

  completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  status="success"
  [[ ${rc} -ne 0 ]] && status="failed"

  # Spec anreichern + nach done/ moven
  python3 - "${running_file}" "${status}" "${completed_at}" "${rc}" "${log_file}" <<'PY'
import json, sys
p, status, ts, rc, log = sys.argv[1:6]
d = json.load(open(p))
d["status"] = status
d["completed_at"] = ts
d["exit_code"] = int(rc)
d["log_path"] = log
json.dump(d, open(p, "w"), indent=2)
PY

  mv "${running_file}" "${done_file}"
  echo "[$(date -u +%FT%TZ)] ${job_id} -> ${status} (exit=${rc})"
done
