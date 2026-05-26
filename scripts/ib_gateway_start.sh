#!/usr/bin/env bash
# ib_gateway_start.sh — IB Gateway via IBC (IBController) starten.
#
# Wird vom LaunchAgent de.gershu.nova.ib.gateway aufgerufen, kann aber auch
# manuell auf der Shell ausgefuehrt werden (nuetzlich fuer Erst-Test).
#
# Schritte:
#   1. ~/.nova_env sourcen (IBKR-Credentials + IBC-/Gateway-Pfade).
#   2. Pflicht-Vars validieren.
#   3. config/ibc_config.ini.template -> /tmp/nova-ibc-runtime.ini rendern
#      (envsubst), chmod 600.
#   4. IBC's gatewaystart.sh aufrufen, blockierend.
#
# Erforderliche Env-Vars (~/.nova_env):
#   NOVA_IBKR_USERNAME    IBKR-Login
#   NOVA_IBKR_PASSWORD    IBKR-Passwort
#   NOVA_IBKR_MODE        'live' oder 'paper'
#   NOVA_IB_API_PORT      typisch 4001 (live) / 4002 (paper)
#   NOVA_IBC_PATH         Pfad zur IBC-Installation (z.B. /opt/homebrew/opt/ibc)
#   NOVA_IB_GATEWAY_PATH  Pfad zur IB-Gateway.app (z.B. /Applications/IB Gateway 10.30)
#   NOVA_IB_GATEWAY_VER   nur die Version, z.B. '10.30' — IBC braucht das separat
#
# Logs: $HOME/Library/Logs/nova-ib-gateway.log (vom LaunchAgent gesetzt).

set -euo pipefail

# ---------- Env laden ----------
[[ -f "$HOME/.nova_env" ]] && source "$HOME/.nova_env"

# PATH defensiv setzen (LaunchAgent-Kontext hat oft kein homebrew).
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

# ---------- Pflicht-Vars pruefen ----------
required=(NOVA_IBKR_USERNAME NOVA_IBKR_PASSWORD NOVA_IBKR_MODE
          NOVA_IB_API_PORT NOVA_IBC_PATH NOVA_IB_GATEWAY_PATH
          NOVA_IB_GATEWAY_VER)
missing=()
for v in "${required[@]}"; do
  [[ -z "${!v:-}" ]] && missing+=("$v")
done
if (( ${#missing[@]} > 0 )); then
  echo "[ib-gateway-start] FEHLER: Env-Vars fehlen: ${missing[*]}" >&2
  echo "[ib-gateway-start] Bitte in ~/.nova_env setzen (siehe docs/ibc_setup.md)." >&2
  exit 64
fi

# ---------- Voraussetzungen pruefen ----------
NOVA_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="${NOVA_ROOT}/config/ibc_config.ini.template"
if [[ ! -f "${TEMPLATE}" ]]; then
  echo "[ib-gateway-start] FEHLER: Template fehlt: ${TEMPLATE}" >&2
  exit 65
fi

GATEWAY_START="${NOVA_IBC_PATH}/scripts/gatewaystart.sh"
if [[ ! -x "${GATEWAY_START}" ]]; then
  # Brew installiert IBC oft direkt nach libexec — Alternativpfad versuchen.
  GATEWAY_START="${NOVA_IBC_PATH}/libexec/scripts/gatewaystart.sh"
fi
if [[ ! -x "${GATEWAY_START}" ]]; then
  echo "[ib-gateway-start] FEHLER: gatewaystart.sh nicht ausfuehrbar unter "\
       "${NOVA_IBC_PATH}." >&2
  exit 65
fi

if [[ ! -d "${NOVA_IB_GATEWAY_PATH}" ]]; then
  echo "[ib-gateway-start] FEHLER: IB Gateway nicht unter "\
       "${NOVA_IB_GATEWAY_PATH}." >&2
  exit 65
fi

# ---------- Runtime-Config rendern ----------
RUNTIME_INI="/tmp/nova-ibc-runtime.ini"
# Variablen-Liste begrenzen, damit envsubst nicht Zufalls-${...} in IBC-Comments
# expandiert.
envsubst '${NOVA_IBKR_USERNAME} ${NOVA_IBKR_PASSWORD} ${NOVA_IBKR_MODE} ${NOVA_IB_API_PORT}' \
  < "${TEMPLATE}" > "${RUNTIME_INI}"
chmod 600 "${RUNTIME_INI}"

echo "[ib-gateway-start] $(date -u +%FT%TZ) "\
     "rendered ${RUNTIME_INI} (chmod 600), starting IBC..."
echo "[ib-gateway-start] mode=${NOVA_IBKR_MODE} port=${NOVA_IB_API_PORT}"
echo "[ib-gateway-start] gateway=${NOVA_IB_GATEWAY_PATH}"
echo "[ib-gateway-start] ibc=${NOVA_IBC_PATH}"

# ---------- IBC starten ----------
# Argumente von gatewaystart.sh (Stand IBC 3.x):
#   -inline           : Output ins gleiche Terminal/Logfile
#   --gateway-vsn=X.Y : Gateway-Version (IBC braucht das, weil es den
#                       Versions-Subpfad in der App selbst kennt)
#   --tws-path=PATH   : Pfad zum Applications-Verzeichnis (NICHT zur .app
#                       selbst, sondern zum Parent — bei uns /Applications)
#   --ibc-ini=FILE    : unsere gerenderte Runtime-Config
#   --mode=live|paper
exec "${GATEWAY_START}" \
  -inline \
  "--gateway-vsn=${NOVA_IB_GATEWAY_VER}" \
  "--tws-path=$(dirname "${NOVA_IB_GATEWAY_PATH}")" \
  "--ibc-ini=${RUNTIME_INI}" \
  "--mode=${NOVA_IBKR_MODE}"
