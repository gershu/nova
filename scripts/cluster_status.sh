#!/usr/bin/env bash
# cluster_status.sh — auf nova-dev ausführen.
#
# Iteriert über Hosts in config/hosts und liefert je Worker:
#   - Hostname
#   - Uptime
#   - Letzter Commit-SHA + Subject in ~/nova
#   - brew bundle check (in sync? out of sync?)

set -uo pipefail   # -e nicht: ein einzelner Worker-Fehler soll den Rest nicht abbrechen

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOSTS_FILE="${SCRIPT_DIR}/../config/hosts"

if [[ ! -f "${HOSTS_FILE}" ]]; then
  echo "Fehler: Hostliste nicht gefunden: ${HOSTS_FILE}" >&2
  exit 1
fi

# Tabellen-Header
printf '%-12s | %-8s | %-22s | %-12s | %s\n' "HOST" "STATUS" "UPTIME" "COMMIT" "BREW BUNDLE"
printf -- '-------------+----------+------------------------+--------------+-----------\n'

while IFS= read -r host; do
  # Kommentare und Leerzeilen überspringen
  [[ -z "${host}" || "${host}" =~ ^# ]] && continue

  # Quick Reachability Check.
  # ssh -n redirects stdin from /dev/null. Ohne -n erbt ssh stdin der Loop
  # (= ${HOSTS_FILE}) und liest die restlichen Zeilen — die naechsten Hosts
  # gehen verloren, read -r host trifft EOF, Loop endet vorzeitig.
  if ! ssh -n -o ConnectTimeout=5 -o BatchMode=yes "${host}" 'true' >/dev/null 2>&1; then
    printf '%-12s | %-8s | %-22s | %-12s | %s\n' "${host}" "DOWN" "-" "-" "-"
    continue
  fi

  # Daten parallel im selben SSH-Aufruf holen (auch hier -n, gleicher Grund).
  # brew shellenv vor command -v brew, da non-interactive SSH-Sessions
  # weder .zprofile noch .zshrc laden und Homebrew sonst nicht im PATH ist.
  remote_output="$(ssh -n -o ConnectTimeout=5 "${host}" '
    set +e
    [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
    [[ -x /usr/local/bin/brew    ]] && eval "$(/usr/local/bin/brew shellenv)"
    UP=$(uptime | sed -E "s/^.*up *//; s/, *load.*$//; s/, *[0-9]+ users?.*$//")
    if [[ -d "$HOME/nova/.git" ]]; then
      COMMIT=$(git -C "$HOME/nova" log -1 --format="%h %s" 2>/dev/null)
    else
      COMMIT="(no repo)"
    fi
    if command -v brew >/dev/null 2>&1; then
      if brew bundle check --file="$HOME/nova/Brewfile" >/dev/null 2>&1; then
        BREW="ok"
      else
        BREW="drift"
      fi
    else
      BREW="(no brew)"
    fi
    printf "%s|%s|%s\n" "$UP" "$COMMIT" "$BREW"
  ' 2>/dev/null)"

  if [[ -z "${remote_output}" ]]; then
    printf '%-12s | %-8s | %-22s | %-12s | %s\n' "${host}" "ERR" "-" "-" "-"
    continue
  fi

  uptime_field="$(echo "${remote_output}" | cut -d'|' -f1)"
  commit_field="$(echo "${remote_output}" | cut -d'|' -f2)"
  brew_field="$(echo "${remote_output}" | cut -d'|' -f3)"

  # Commit auf 12 Zeichen kürzen für die Tabelle
  short_commit="$(echo "${commit_field}" | awk '{print $1}')"

  printf '%-12s | %-8s | %-22s | %-12s | %s\n' \
    "${host}" "UP" "${uptime_field:0:22}" "${short_commit:0:12}" "${brew_field}"
done < "${HOSTS_FILE}"
