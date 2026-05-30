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

# X11-Env-Vars hart entfernen: wenn der User XQuartz installiert hat,
# exportiert seine Login-Shell DISPLAY auf den XQuartz-Socket. Java sieht
# das und waehlt den X11-AWT-Toolkit statt des nativen Cocoa-Toolkits;
# dann scheitert AWT-Init und Java exits mit 1 ohne Stderr. Erzwingen,
# dass Java die native macOS-GUI nimmt.
unset DISPLAY XAUTHORITY AWT_TOOLKIT

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

# IBC-Start-Script finden. Wir nutzen ibcstart.sh direkt — der Banner-Wrapper
# displaybannerandlaunch.sh erwartet IBC_PATH/TWS_PATH/etc. als ENV-Vars,
# nicht als CLI-Args; das wuerden wir nur unnoetig spiegeln. ibcstart.sh
# hingegen parst alle Pfade aus den --xxx=...-Argumenten selbst.
IBC_START=""
for cand in \
    "${NOVA_IBC_PATH}/scripts/ibcstart.sh" \
    "${NOVA_IBC_PATH}/libexec/scripts/ibcstart.sh"; do
  if [[ -x "$cand" ]]; then
    IBC_START="$cand"
    break
  fi
done
if [[ -z "${IBC_START}" ]]; then
  echo "[ib-gateway-start] FEHLER: ibcstart.sh nicht ausfuehrbar unter" \
       "${NOVA_IBC_PATH}/scripts/." >&2
  exit 65
fi

if [[ ! -d "${NOVA_IB_GATEWAY_PATH}" ]]; then
  echo "[ib-gateway-start] FEHLER: IB Gateway nicht unter "\
       "${NOVA_IB_GATEWAY_PATH}." >&2
  exit 65
fi

# ---------- Runtime-Config rendern ----------
# Per-User-Pfad, damit User-Wechsel der Plist (z.B. novaadm <-> stefan_mac)
# keine 'Permission denied' beim Ueberschreiben ergibt.
RUNTIME_INI="/tmp/nova-ibc-runtime-$(id -u).ini"
# Variablen-Liste begrenzen, damit envsubst nicht Zufalls-${...} in IBC-Comments
# expandiert.
envsubst '${NOVA_IBKR_USERNAME} ${NOVA_IBKR_PASSWORD} ${NOVA_IBKR_MODE} ${NOVA_IB_API_PORT}' \
  < "${TEMPLATE}" > "${RUNTIME_INI}"
chmod 600 "${RUNTIME_INI}"

echo "[ib-gateway-start] $(date -u +%FT%TZ) rendered ${RUNTIME_INI} (chmod 600)"
echo "[ib-gateway-start] mode=${NOVA_IBKR_MODE} port=${NOVA_IB_API_PORT}"
echo "[ib-gateway-start] gateway=${NOVA_IB_GATEWAY_PATH}"
echo "[ib-gateway-start] ibc=${NOVA_IBC_PATH}"
echo "[ib-gateway-start] start=${IBC_START}"

# ---------- IBC starten ----------
# Argumente fuer ibcstart.sh (IBC 3.x):
#   <version>                   positional: Gateway-Version (z.B. 10.46)
#   --gateway                   nutze Gateway statt TWS
#   --mode=live|paper           Trading-Modus
#   --ibc-ini=FILE              Pfad zur IBC-Runtime-Config
#   --tws-path=DIR              Verzeichnis, das die IB Gateway App enthaelt
#                               (NICHT die .app, sondern der Parent — typisch
#                               /Applications)
#   --tws-settings-path=DIR     Settings-Dir; default waere $HOME/Jts, das
#                               existiert auf nova-hub fuer novaadm nicht.
#                               Wir spiegeln dort stefan_mac's gepflegte
#                               Settings (Layout, akzeptierte Disclaimer).
#   --ibc-path=DIR              Pfad zur IBC-Installation
TWS_SETTINGS="${HOME}/Library/Application Support/IB Gateway ${NOVA_IB_GATEWAY_VER}"
exec "${IBC_START}" "${NOVA_IB_GATEWAY_VER}" \
  --gateway \
  "--mode=${NOVA_IBKR_MODE}" \
  "--ibc-ini=${RUNTIME_INI}" \
  "--tws-path=$(dirname "${NOVA_IB_GATEWAY_PATH}")" \
  "--tws-settings-path=${TWS_SETTINGS}" \
  "--ibc-path=${NOVA_IBC_PATH}"
