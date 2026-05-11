#!/usr/bin/env bash
# install_lab_news_sa_daemon.sh — sudo-Setup des nova-lab
# Seeking-Alpha-IMAP-Pull LaunchDaemon auf nova-hub.
#
# Triggert taeglich 22:55 — zwischen ingest_fx (22:50) und ingest (23:00).
# Pullt Mails aus Gmail-Label nova-sa, persistiert summaries, verschiebt
# nach nova-sa/processed.
#
# Aufruf:
#   sudo ~/nova/scripts/install_lab_news_sa_daemon.sh

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Fehler: muss mit sudo (als root) laufen." >&2
  echo "  sudo $(realpath "$0")" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "${SCRIPT_DIR}")"

PLIST_SRC="${REPO_DIR}/dotfiles/launchd/de.gershu.nova.lab.news_sa.plist"
PLIST_DST="/Library/LaunchDaemons/de.gershu.nova.lab.news_sa.plist"
DRIVER_SH="${REPO_DIR}/scripts/lab_news_sa_daily.sh"
LOGS_DIR="/Users/novaadm/Library/Logs"

[[ -f "${PLIST_SRC}" ]] || { echo "Fehler: ${PLIST_SRC} nicht gefunden." >&2; exit 1; }
[[ -x "${DRIVER_SH}" ]] || { echo "Fehler: ${DRIVER_SH} nicht executable." >&2; exit 1; }

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
launchctl bootout "system/de.gershu.nova.lab.news_sa" 2>/dev/null || true

echo "==> Daemon laden"
launchctl bootstrap system "${PLIST_DST}"

echo "==> Status:"
launchctl print "system/de.gershu.nova.lab.news_sa" 2>&1 | head -20 || true

echo
echo "==> Fertig. Daemon triggert taeglich 22:55 lokal als novaadm."
echo
echo "    Voraussetzungen:"
echo "      1. ~/.nova_env enthaelt GMAIL_IMAP_USER + GMAIL_IMAP_PASSWORD"
echo "      2. Gmail-Filter: From: noreply@seekingalpha.com -> Label 'nova-sa'"
echo "      3. SA-Email-Alerts in seekingalpha.com aktiviert"
echo "      4. Schema migriert:    lab_news_sa run.sh init"
echo
echo "    Logs:              ${LOGS_DIR}/nova-lab-news-sa.log"
echo "    Manueller Trigger: sudo launchctl kickstart system/de.gershu.nova.lab.news_sa"
echo "    Stop:              sudo launchctl bootout system/de.gershu.nova.lab.news_sa"
