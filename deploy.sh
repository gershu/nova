#!/usr/bin/env bash

set -euo pipefail

REMOTE_HOST="novaadm@nova-hub"
SERVICE="system/de.gershu.nova.lab.dashboard"

echo "🚀 Push"
rm -f .git/HEAD.lock
git push origin main

echo "📦 Deploy"

ssh "$REMOTE_HOST" "
set -e
~/nova/scripts/node_deploy.sh
sudo launchctl kickstart -k $SERVICE
"

echo "✅ Fertig"
