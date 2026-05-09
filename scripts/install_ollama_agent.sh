#!/usr/bin/env bash
# install_ollama_agent.sh — einmaliger User-level Setup des Ollama-LaunchAgent
# auf nova-w5.
#
# WICHTIG: KEIN sudo, NICHT als root. LaunchAgent laeuft in der User-Session
# von novaadm — Metal-GPU-Access setzt aktive User-Session voraus, deshalb
# auch Auto-Login fuer novaadm erforderlich (System Settings -> Users & Groups).
#
# Aufruf:
#   ~/nova/scripts/install_ollama_agent.sh

set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Fehler: install_ollama_agent.sh darf NICHT als root laufen." >&2
  echo "       LaunchAgent ist user-level. Aufruf als novaadm:" >&2
  echo "  ~/nova/scripts/install_ollama_agent.sh" >&2
  exit 1
fi

if [[ "$(hostname -s)" != "nova-w5" ]]; then
  echo "Fehler: Diese Maschine ist nicht nova-w5 (Hostname: $(hostname -s))." >&2
  echo "       Ollama-LaunchAgent ist nur fuer nova-w5 vorgesehen." >&2
  exit 1
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "Fehler: ollama nicht installiert. node_deploy.sh erst laufen lassen" >&2
  echo "       (installiert via Brewfile)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

PLIST_SRC="${REPO_DIR}/dotfiles/launchd/de.gershu.nova.ollama.plist"
PLIST_DST="${HOME}/Library/LaunchAgents/de.gershu.nova.ollama.plist"
LOGS_DIR="${HOME}/Library/Logs"
MODELS_DIR="${HOME}/.ollama/models"

[[ -f "${PLIST_SRC}" ]] || { echo "Fehler: ${PLIST_SRC} nicht gefunden." >&2; exit 1; }

echo "==> Logs-Dir + Models-Dir sicherstellen"
mkdir -p "${LOGS_DIR}" "${MODELS_DIR}" "$(dirname "${PLIST_DST}")"

echo "==> Kopiere plist nach ${PLIST_DST}"
cp "${PLIST_SRC}" "${PLIST_DST}"
chmod 644 "${PLIST_DST}"

echo "==> Existierenden Agent ggf. abladen (idempotent)"
launchctl bootout "gui/$(id -u)/de.gershu.nova.ollama" 2>/dev/null || true

echo "==> Agent laden"
launchctl bootstrap "gui/$(id -u)" "${PLIST_DST}"

echo "==> Status:"
launchctl print "gui/$(id -u)/de.gershu.nova.ollama" 2>&1 | head -20 || true

echo
echo "==> Fertig. Ollama serve laeuft auf http://0.0.0.0:11434 (LAN-erreichbar als nova-w5.local:11434)."
echo "    Logs:   ${LOGS_DIR}/ollama.log"
echo "    Stop:   launchctl bootout gui/\$(id -u)/de.gershu.nova.ollama"
echo
echo "==> Naechste Schritte:"
echo "    1. Modell pullen (z.B. Qwen 2.5 14B Instruct, ~9 GB):"
echo "         ollama pull qwen2.5:14b-instruct-q4_K_M"
echo "    2. Smoke-Test:"
echo "         curl http://localhost:11434/api/generate -d '{\"model\":\"qwen2.5:14b-instruct-q4_K_M\",\"prompt\":\"hello\",\"stream\":false}'"
echo "    3. Vom Hub aus:"
echo "         curl http://nova-w5.local:11434/api/generate -d '{\"model\":\"qwen2.5:14b-instruct-q4_K_M\",\"prompt\":\"hello\",\"stream\":false}'"
echo
echo "==> Sicherheit (manuell pruefen):"
echo "    - System Settings -> Network -> Firewall: macOS-Firewall an,"
echo "      eingehend nur aus LAN (192.168.0.0/16)."
echo "    - Ollama hat KEINE Auth — niemals nach aussen exposen."
