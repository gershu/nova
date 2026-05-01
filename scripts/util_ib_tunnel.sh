#!/usr/bin/env bash
# =============================================================================
# tunnel.sh — autossh Tunnel Manager
#
# Usage:
#   tunnel.sh start [--local-port PORT] [--host HOST] [--host-port PORT]
#   tunnel.sh stop  [--local-port PORT] [--host HOST]
#   tunnel.sh status
#
# Defaults:
#   --local-port  4001
#   --host        nova-hub
#   --host-port   4001
#
# Beispiele:
#   tunnel.sh start
#   tunnel.sh start --local-port 4002 --host nova-hub --host-port 4001
#   tunnel.sh stop
#   tunnel.sh stop --local-port 4002 --host nova-hub
# =============================================================================
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_LOCAL_PORT=4001
DEFAULT_HOST="nova-hub"
DEFAULT_HOST_PORT=4001

# ── Helpers ───────────────────────────────────────────────────────────────────
info() { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
ok()   { printf '\033[1;32m[ OK ]\033[0m  %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*"; }
die()  { printf '\033[1;31m[ERR ]\033[0m  %s\n' "$*" >&2; exit 1; }

usage() {
  grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,2\}//'
  exit 0
}

# ── Argument Parsing ──────────────────────────────────────────────────────────
[[ $# -eq 0 ]] && usage

COMMAND="${1}"
shift

LOCAL_PORT="${DEFAULT_LOCAL_PORT}"
HOST="${DEFAULT_HOST}"
HOST_PORT="${DEFAULT_HOST_PORT}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local-port) LOCAL_PORT="$2"; shift 2 ;;
    --host)       HOST="$2";       shift 2 ;;
    --host-port)  HOST_PORT="$2";  shift 2 ;;
    -h|--help)    usage ;;
    *) die "Unbekannter Parameter: $1" ;;
  esac
done

# Match-Pattern für pgrep — eindeutig pro Tunnel-Konfiguration
MATCH="${LOCAL_PORT}:127.0.0.1:${HOST_PORT}.*${HOST}"

# ── Subcommands ───────────────────────────────────────────────────────────────

cmd_start() {
  command -v autossh &>/dev/null || die "autossh nicht gefunden. brew install autossh"

  if pgrep -f "${MATCH}" > /dev/null 2>&1; then
    warn "Tunnel bereits aktiv (local:${LOCAL_PORT} → ${HOST}:${HOST_PORT})"
    cmd_status
    return
  fi

  info "Starte Tunnel: 127.0.0.1:${LOCAL_PORT} → ${HOST}:127.0.0.1:${HOST_PORT}"

  autossh -M 0 -f -N \
    -L "${LOCAL_PORT}:127.0.0.1:${HOST_PORT}" \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    -o StrictHostKeyChecking=accept-new \
    "${HOST}"

  # Kurz warten bis Port offen ist
  local retries=5
  while (( retries-- > 0 )); do
    sleep 1
    if lsof -nP -iTCP:"${LOCAL_PORT}" -sTCP:LISTEN &>/dev/null; then
      ok "Tunnel steht — Port ${LOCAL_PORT} ist offen."
      return
    fi
  done

  die "Tunnel gestartet, aber Port ${LOCAL_PORT} ist nach 5s noch nicht offen."
}

cmd_stop() {
  local pids
  pids=$(pgrep -f "${MATCH}" 2>/dev/null || true)

  if [[ -z "$pids" ]]; then
    warn "Kein aktiver Tunnel gefunden (local:${LOCAL_PORT} → ${HOST}:${HOST_PORT})"
    return
  fi

  info "Stoppe Tunnel PID(s): ${pids}"
  echo "${pids}" | xargs kill
  sleep 1

  if pgrep -f "${MATCH}" > /dev/null 2>&1; then
    die "Prozess läuft noch — versuche: kill -9 ${pids}"
  fi

  ok "Tunnel gestoppt."
}

cmd_status() {
  echo "── Konfiguration ─────────────────────────────────────"
  echo "   local-port : ${LOCAL_PORT}"
  echo "   host       : ${HOST}"
  echo "   host-port  : ${HOST_PORT}"
  echo
  echo "── Prozess ───────────────────────────────────────────"
  if pgrep -af "${MATCH}" 2>/dev/null; then
    ok "autossh läuft."
  else
    warn "Kein autossh-Prozess gefunden."
  fi
  echo
  echo "── TCP-Port ${LOCAL_PORT} ────────────────────────────────────"
  if lsof -nP -iTCP:"${LOCAL_PORT}" -sTCP:LISTEN 2>/dev/null | grep -q .; then
    lsof -nP -iTCP:"${LOCAL_PORT}" -sTCP:LISTEN
    ok "Port ${LOCAL_PORT} ist offen → Tunnel steht."
  else
    warn "Port ${LOCAL_PORT} ist NICHT offen."
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
case "${COMMAND}" in
  start)  cmd_start  ;;
  stop)   cmd_stop   ;;
  status) cmd_status ;;
  -h|--help) usage   ;;
  *) die "Unbekannter Befehl: ${COMMAND}. Nutze: start | stop | status" ;;
esac
