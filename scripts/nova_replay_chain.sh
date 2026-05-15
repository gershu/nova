#!/usr/bin/env bash
# nova_replay_chain.sh — synchroner Re-Run der Daily-Daemon-Chain.
#
# Use-Case: gestern hat IB Gateway gestreikt / ein Daemon gefailt → kein
# Digest. Heute morgen will Stefan die ganze Chain nochmal nachholen,
# ohne 10 launchctl-kickstarts und ohne auf den Picker zu warten.
#
# Vorgehen: ruft die workloads/<n>/run.sh Scripts DIREKT auf (synchron),
# bypassed nova_submit/Picker. Liest die gleichen Params-Files wie die
# Production-Drivers, sodass alle Defaults greifen.
#
# Pre-Checks: IB-Gateway-Check wird vor IB-abhaengige Steps automatisch
# ausgefuehrt (gleiche Logik wie in lab_*_daily.sh).
#
# Default-Chain (in Reihenfolge):
#   ingest_fx → news_sa → ingest → screener_csp → monitor → digest →
#   alert_explainer → portfolio_briefing → obsidian
#
# Optional: --include-weekly fuegt fundamentals + screener_value vorn an.
#
# Usage:
#   nova_replay_chain.sh                      # full daily chain
#   nova_replay_chain.sh --from monitor       # ab monitor (skipped earlier)
#   nova_replay_chain.sh --skip news_sa       # einzelne Steps auslassen
#   nova_replay_chain.sh --dry-run            # nur ausgeben was wuerde
#   nova_replay_chain.sh --no-stop-on-fail    # weiter bei Fehler
#   nova_replay_chain.sh --include-weekly     # mit fundamentals + value-screener
#
# Logs: ~/nova_replay_logs/replay_<ts>/<step>.log

set -euo pipefail

REPO_DIR="${NOVA_REPO_DIR:-$HOME/nova}"
JOBS_DIR="${HOME}/jobs"
LOG_DIR_ROOT="${HOME}/nova_replay_logs"
WORKLOADS_DIR="${REPO_DIR}/workloads"

# Chain-Definition: "label|workload|extra_args"
# extra_args wird in eval'd damit ${JOBS_DIR} drin resolved.
DAILY_CHAIN=(
    "ingest_fx|lab_ingest_fx|--params-file ${JOBS_DIR}/lab_ingest_fx_daily.json"
    "news_sa|lab_news_sa|fetch"
    "ingest|lab_ingest|--params-file ${JOBS_DIR}/lab_ingest_daily.json"
    "screener_csp|lab_screener_csp|run --params-file ${JOBS_DIR}/lab_screener_csp_daily.json"
    "monitor|lab_monitor|--params-file ${JOBS_DIR}/lab_monitor_daily.json"
    "digest|lab_digest|--params-file ${JOBS_DIR}/lab_digest_daily.json"
    "alert_explainer|lab_alert_explainer|--params-file ${JOBS_DIR}/lab_alert_explainer_daily.json"
    "portfolio_briefing|lab_portfolio_briefing|--params-file ${JOBS_DIR}/lab_portfolio_briefing_daily.json"
    "obsidian|lab_obsidian|publish"
)
WEEKLY_CHAIN=(
    "fundamentals|lab_fundamentals|refresh-all --since-days 6"
    "screener_value|lab_screener_value|filter --params-file ${JOBS_DIR}/lab_screener_value_weekly.json"
)

# Steps die einen IB-Precheck brauchen
IB_DEPENDENT=("screener_csp")
# Hinweis: lab_ingest haengt vom params.source ab — wenn 'ib' brauch Gateway.
# Der Daily-Driver checkt das schon; wir delegieren denselben Check.

# ---------- Args parsen ----------

FROM_STEP=""
SKIP_STEPS=()
DRY_RUN=0
STOP_ON_FAIL=1
INCLUDE_WEEKLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)              FROM_STEP="$2"; shift 2 ;;
        --skip)              SKIP_STEPS+=("$2"); shift 2 ;;
        --dry-run)           DRY_RUN=1; shift ;;
        --no-stop-on-fail)   STOP_ON_FAIL=0; shift ;;
        --include-weekly)    INCLUDE_WEEKLY=1; shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | grep '^# ' | sed 's/^# *//' | head -n -1
            exit 0 ;;
        *) echo "Fehler: unbekannte Option '$1'" >&2; exit 64 ;;
    esac
done

# Setup
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="${LOG_DIR_ROOT}/replay_${TS}"
mkdir -p "${LOG_DIR}"

# Chain zusammenbauen
CHAIN=()
if [[ ${INCLUDE_WEEKLY} -eq 1 ]]; then
    CHAIN+=("${WEEKLY_CHAIN[@]}")
fi
CHAIN+=("${DAILY_CHAIN[@]}")

# Filter: --from
if [[ -n "${FROM_STEP}" ]]; then
    found=0
    NEW_CHAIN=()
    for entry in "${CHAIN[@]}"; do
        label="${entry%%|*}"
        if [[ "${label}" == "${FROM_STEP}" ]]; then found=1; fi
        if [[ ${found} -eq 1 ]]; then NEW_CHAIN+=("${entry}"); fi
    done
    if [[ ${found} -eq 0 ]]; then
        echo "FEHLER: --from '${FROM_STEP}' nicht in Chain. Verfuegbare Labels:" >&2
        for entry in "${CHAIN[@]}"; do echo "  ${entry%%|*}" >&2; done
        exit 64
    fi
    CHAIN=("${NEW_CHAIN[@]}")
fi

# Filter: --skip
NEW_CHAIN=()
for entry in "${CHAIN[@]}"; do
    label="${entry%%|*}"
    skip_this=0
    for skip in "${SKIP_STEPS[@]:-}"; do
        [[ "${skip}" == "${label}" ]] && skip_this=1 && break
    done
    [[ ${skip_this} -eq 0 ]] && NEW_CHAIN+=("${entry}")
done
CHAIN=("${NEW_CHAIN[@]}")

# Header
echo "==> nova-replay-chain  (ts=${TS})"
echo "    Logs:        ${LOG_DIR}"
echo "    Dry-Run:     ${DRY_RUN}"
echo "    Stop-on-fail: ${STOP_ON_FAIL}"
echo "    Steps        (${#CHAIN[@]}):"
for entry in "${CHAIN[@]}"; do
    IFS='|' read -r label workload args <<< "${entry}"
    echo "      ${label}  ->  ${workload}  ${args}"
done
echo

if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "[DRY-RUN] keine Steps ausgefuehrt."
    exit 0
fi

# ---------- Step-Execution ----------

run_step() {
    local label="$1" workload="$2" args="$3"
    local logfile="${LOG_DIR}/${label}.log"
    local run_sh="${WORKLOADS_DIR}/${workload}/run.sh"

    # IB-Precheck vor IB-Steps
    for ib_dep in "${IB_DEPENDENT[@]}"; do
        if [[ "${ib_dep}" == "${label}" ]]; then
            if ! "${REPO_DIR}/scripts/check_ib_gateway.sh" >/dev/null 2>&1; then
                echo "  [SKIP]  ${label} — IB Gateway down"
                echo "(IB-Precheck failed)" > "${logfile}"
                return 0    # skip, kein fail
            fi
        fi
    done

    if [[ ! -x "${run_sh}" ]]; then
        echo "  [FAIL]  ${label} — ${run_sh} fehlt/nicht executable"
        return 1
    fi

    local start_ts=$(date -u +%s)
    echo "  [RUN ]  ${label}  ($(date -u +%H:%M:%S))"
    # eval um ${VARS} in args zu expandieren
    local cmd
    cmd="\"${run_sh}\" $(eval echo "${args}")"
    if eval "${cmd}" > "${logfile}" 2>&1; then
        local elapsed=$(( $(date -u +%s) - start_ts ))
        echo "  [OK  ]  ${label}  (${elapsed}s)"
        return 0
    else
        local rc=$?
        local elapsed=$(( $(date -u +%s) - start_ts ))
        echo "  [FAIL]  ${label}  (rc=${rc}, ${elapsed}s)  — siehe ${logfile}"
        return ${rc}
    fi
}

# Main loop
n_ok=0
n_fail=0
n_skip=0
for entry in "${CHAIN[@]}"; do
    IFS='|' read -r label workload args <<< "${entry}"
    if run_step "${label}" "${workload}" "${args}"; then
        # Check ob skipped (logfile-content):
        if [[ -f "${LOG_DIR}/${label}.log" ]] && \
           grep -q '^(IB-Precheck failed)$' "${LOG_DIR}/${label}.log" 2>/dev/null; then
            ((n_skip++))
        else
            ((n_ok++))
        fi
    else
        ((n_fail++))
        if [[ ${STOP_ON_FAIL} -eq 1 ]]; then
            echo
            echo "==> Chain abgebrochen bei '${label}'. Logs in ${LOG_DIR}."
            echo "    Resume mit:  $(basename "$0") --from ${label}"
            exit 65
        fi
    fi
done

echo
echo "==> Chain fertig.  ok=${n_ok}  fail=${n_fail}  skip=${n_skip}"
echo "    Logs: ${LOG_DIR}"
exit 0
