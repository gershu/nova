#!/usr/bin/env bash
# check_ib_gateway.sh — TCP-Precheck ob IB Gateway erreichbar ist.
#
# Exit-Codes:
#   0  Gateway erreichbar
#   1  Nicht erreichbar (Daemon-Driver soll abbrechen)
#
# Konfig via ENV:
#   IB_GATEWAY_HOST     default 127.0.0.1
#   IB_GATEWAY_PORT     default 4001
#   IB_PRECHECK_TIMEOUT default 2 (sec)
#   NOVA_SKIP_IF_NO_IB  default 1 (= echte Pre-Check). Wenn 0: gibt nur Warn
#                       aus, exit 0 — fuer Debugging
#
# Aufruf:
#   bash check_ib_gateway.sh           # silent ok, log on fail
#   bash check_ib_gateway.sh --verbose # logget always

set -euo pipefail

HOST="${IB_GATEWAY_HOST:-127.0.0.1}"
PORT="${IB_GATEWAY_PORT:-4001}"
TIMEOUT="${IB_PRECHECK_TIMEOUT:-2}"
SKIP_IF_NO="${NOVA_SKIP_IF_NO_IB:-1}"

verbose=0
for arg in "$@"; do
  [[ "$arg" == "--verbose" || "$arg" == "-v" ]] && verbose=1
done

# nc -z probiert TCP-connect, kein Daten-Transfer. -G ist macOS BSD-nc Timeout,
# -w ist Linux GNU-nc Timeout. Wir versuchen beide; einer wird gehen.
if nc -z -G "${TIMEOUT}" "${HOST}" "${PORT}" 2>/dev/null \
   || nc -z -w "${TIMEOUT}" "${HOST}" "${PORT}" 2>/dev/null; then
  [[ "${verbose}" -eq 1 ]] && echo "[ib-precheck] IB Gateway ${HOST}:${PORT} ok"
  exit 0
fi

# Nicht erreichbar
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[ib-precheck ${ts}] FEHLER: IB Gateway ${HOST}:${PORT} nicht erreichbar." >&2
echo "[ib-precheck ${ts}] Pruefe: sudo launchctl print system/de.gershu.nova.lab.ib.gateway" >&2

if [[ "${SKIP_IF_NO}" == "0" ]]; then
  echo "[ib-precheck ${ts}] NOVA_SKIP_IF_NO_IB=0 -> ignoriere Failure, weiter." >&2
  exit 0
fi
exit 1
