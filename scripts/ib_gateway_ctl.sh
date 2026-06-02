#!/usr/bin/env bash
# ib_gateway_ctl.sh — On-Demand-Steuerung des IB-Gateway-LaunchAgents.
#
# Das Gateway laeuft NICHT mehr permanent (Plist ohne KeepAlive/RunAtLoad),
# damit dein IBKR-Account frei bleibt fuer direkte TWS-/Mobile-Nutzung. Es
# wird nur bei Bedarf gestartet und danach wieder gestoppt.
#
# Befehle:
#   start [timeout]  Gateway starten + auf Port warten (default 120s)
#   stop             Gateway beenden (Account wieder frei)
#   status           up/down + HOLD-Status
#   pause            HOLD setzen + stoppen -> Account frei fuer manuelles IB
#   resume           HOLD entfernen + starten
#   with -- <cmd…>   Gateway hochfahren, <cmd> ausfuehren, danach stoppen
#                    (nur stoppen, wenn dieser Aufruf es gestartet hat)
#
# HOLD: solange ~/.nova_ib_hold existiert, verweigert 'start' den Start —
# so kann ein geplanter nova-Job das Gateway nicht hochfahren, waehrend du
# manuell in IB eingeloggt bist.

set -euo pipefail

LABEL="de.gershu.nova.lab.ib.gateway"
PORT="${NOVA_IB_API_PORT:-4001}"
HOLD="${NOVA_IB_HOLD_FILE:-$HOME/.nova_ib_hold}"
DOMAIN="gui/$(id -u)"

[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env" 2>/dev/null || true
PORT="${NOVA_IB_API_PORT:-$PORT}"

_port_open() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; }

_wait_port() {  # $1 = timeout sek
  local t="${1:-120}" i
  for ((i = 0; i < t; i++)); do _port_open && return 0; sleep 1; done
  return 1
}

_start() {
  if [[ -f "$HOLD" ]]; then
    echo "[ib] HOLD aktiv ($HOLD) — Start uebersprungen (manuelle IB-Nutzung)." >&2
    return 3
  fi
  if _port_open; then echo "[ib] laeuft bereits (Port $PORT)."; return 0; fi
  echo "[ib] starte Gateway via launchctl kickstart …"
  launchctl kickstart "$DOMAIN/$LABEL"
  if _wait_port "${1:-120}"; then
    echo "[ib] up (Port $PORT)."
  else
    echo "[ib] Timeout: Port $PORT nicht offen." >&2; return 1
  fi
}

_stop() {
  echo "[ib] stoppe Gateway …"
  launchctl kill TERM "$DOMAIN/$LABEL" 2>/dev/null || true
  pkill -f "ibgateway" 2>/dev/null || true
  pkill -f "IBC" 2>/dev/null || true
  local i
  for ((i = 0; i < 15; i++)); do
    _port_open || { echo "[ib] gestoppt — Account frei."; return 0; }
    sleep 1
  done
  echo "[ib] Warnung: Port $PORT noch offen." >&2; return 1
}

_status() {
  _port_open && echo "up (Port $PORT)" || echo "down"
  [[ -f "$HOLD" ]] && echo "HOLD aktiv (manuelles IB)"
  return 0
}

case "${1:-}" in
  start)  _start "${2:-120}" ;;
  stop)   _stop ;;
  status) _status ;;
  pause)  touch "$HOLD"; _stop
          echo "[ib] pausiert — du kannst jetzt direkt in IB handeln." ;;
  resume) rm -f "$HOLD"; _start "${2:-120}" ;;
  with)
    shift; [[ "${1:-}" == "--" ]] && shift
    [[ $# -ge 1 ]] || { echo "Usage: $0 with -- <cmd…>" >&2; exit 64; }
    started=0
    if ! _port_open; then _start || exit 1; started=1; fi
    set +e; "$@"; rc=$?; set -e
    [[ "$started" == 1 ]] && _stop
    exit "$rc" ;;
  *)
    echo "Usage: $0 {start [timeout]|stop|status|pause|resume|with -- <cmd…>}" >&2
    exit 64 ;;
esac
