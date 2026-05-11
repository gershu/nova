#!/usr/bin/env bash
# lab_news_sa_daily.sh — taeglich von launchd auf nova-hub.
#
# Schedule: 22:55 — zwischen ingest_fx (22:50) und ingest (23:00).
# Pullt Seeking-Alpha-Mails aus Gmail-Label 'nova-sa' und verschiebt
# verarbeitete in 'nova-sa/processed'.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

exec "${HOME}/nova/scripts/nova_submit.sh" \
    lab_news_sa nova-hub \
    fetch
