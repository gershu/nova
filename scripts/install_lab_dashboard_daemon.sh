#!/usr/bin/env bash
# install_lab_dashboard_daemon.sh — sudo-Setup des nova-lab
# Dashboard LaunchDaemon (Streamlit long-running).
#
# Unterschied zu den daily-Daemons: KeepAlive=true, RunAtLoad=true —
# laeuft permanent. Bei crash automatischer Restart (mit 10s throttle).
#
# Aufruf:
#   sudo ~/nova/scripts/install_lab_dashboard_daemon.sh

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Fehler: muss mit sudo (als root) laufen." >&2
  echo "  sudo $(realpath "$0")" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

PLIST_SRC="${REPO_DIR}/dotfiles/launchd/de.gershu.nova.lab.dashboard.plist"
PLIST_DST="/Library/LaunchDaemons/de.gershu.nova.lab.dashboard.plist"
RUN_SH="${REPO_DIR}/workloads/lab_dashboard/run.sh"
LOGS_DIR="/Users/novaadm/Library/Logs"

[[ -f "${PLIST_SRC}" ]] || { echo "Fehler: ${PLIST_SRC} nicht gefunden." >&2; exit 1; }
[[ -x "${RUN_SH}" ]]    || { echo "Fehler: ${RUN_SH} nicht executable." >&2; exit 1; }

if [[ "$(hostname -s)" != "nova-hub" ]]; then
  echo "Fehler: nicht nova-hub (Hostname: $(hostname -s))." >&2
  exit 1
fi

echo "==> Logs-Dir sicherstellen: ${LOGS_DIR}"
sudo -u novaadm mkdir -p "${LOGS_DIR}"

echo "==> Kopiere plist nach ${PLIST_DST}"
cp "${PLIST_SRC}" "${PLIST_DST}"
chown root:wheel "${PLIST_DST}"
chmod 644 "${PLIST_DST}"

echo "==> Existierenden Daemon ggf. abladen (idempotent)"
launchctl bootout "system/de.gershu.nova.lab.dashboard" 2>/dev/null || true

echo "==> Daemon laden (startet sofort durch RunAtLoad=true)"
launchctl bootstrap system "${PLIST_DST}"

sleep 2
echo "==> Status:"
launchctl print "system/de.gershu.nova.lab.dashboard" 2>&1 | head -15 || true

echo
echo "==> Fertig. Dashboard laeuft permanent auf 0.0.0.0:8501."
echo
echo "    Zugriff:"
echo "      via Tailscale:   http://nova-hub:8501  (oder http://<tailscale-ip>:8501)"
echo "      via SSH-Tunnel:  ssh -L 8501:localhost:8501 nova-hub  ->  http://localhost:8501"
echo
echo "    Logs:              ${LOGS_DIR}/nova-lab-dashboard.log"
echo "    Restart:           sudo launchctl kickstart -k system/de.gershu.nova.lab.dashboard"
echo "    Stop:              sudo launchctl bootout system/de.gershu.nova.lab.dashboard"
