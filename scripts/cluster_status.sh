#!/usr/bin/env bash
# cluster_status.sh — auf nova-hub ausführen.
#
# Liest config/nodes.yaml und liefert je Worker (role=worker):
#   - Hostname
#   - Tags aus nodes.yaml (z.B. compute-heavy, ib-capable)
#   - Reachability
#   - Uptime
#   - Letzter Commit-SHA in ~/nova
#   - brew bundle check (in sync / drift)
#
# Der Hub selbst wird nicht gepollt (er ist ja der Pollende).


# -u (unbound variable) kann Fehler verursachen, wenn Arrays leer bleiben. Erst initialisieren, dann -u setzen.
WORKERS=()
set -uo pipefail   # -e nicht: ein einzelner Worker-Fehler soll den Rest nicht abbrechen

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NODES_FILE="${SCRIPT_DIR}/../config/nodes.yaml"

if [[ ! -f "${NODES_FILE}" ]]; then
  echo "Fehler: ${NODES_FILE} nicht gefunden." >&2
  exit 1
fi

if ! command -v yq >/dev/null 2>&1; then
  echo "Fehler: yq nicht im PATH. brew bundle (Brewfile listet yq) ausfuehren." >&2
  exit 1
fi

# Liste aller Worker-Namen (roles enthaelt "worker") aus nodes.yaml extrahieren.
# nova-hub kann sowohl hub als auch worker sein (Mehrfach-Rolle) — wir pollen
# allerdings den Hub nicht via SSH (er ist ja der Pollende). Skip wenn Hostname
# == eigener Hostname.
LOCAL_HOST="$(hostname -s 2>/dev/null || hostname)"

WORKERS=()
while IFS= read -r line; do
  [[ "${line}" == "${LOCAL_HOST}" ]] && continue   # self-skip
  WORKERS+=("$line")
done < <(yq -r '.nodes | to_entries | .[] | select(.value.roles | contains(["worker"])) | .key' "${NODES_FILE}")

if [[ ${#WORKERS[@]} -eq 0 ]]; then
  echo "Hinweis: keine Worker in ${NODES_FILE} (role=worker) gefunden." >&2
  exit 0
fi

# Tabellen-Header
printf '%-12s | %-8s | %-22s | %-12s | %-12s | %s\n' "HOST" "STATUS" "UPTIME" "COMMIT" "BREW" "TAGS"
printf -- '-------------+----------+------------------------+--------------+--------------+----------------\n'

for host in "${WORKERS[@]}"; do
  # Tags aus nodes.yaml als kommagetrennte Liste (oder "-" wenn leer)
  tags="$(yq -r ".nodes.\"${host}\".tags // [] | join(\",\")" "${NODES_FILE}")"
  [[ -z "${tags}" ]] && tags="-"

  # Quick Reachability Check.
  # ssh -n redirects stdin from /dev/null (sonst klauten ssh-Aufrufe stdin
  # einer fruehen while-read-Schleife — ehemals Bug, jetzt for-Loop).
  if ! ssh -n -o ConnectTimeout=5 -o BatchMode=yes "${host}" 'true' >/dev/null 2>&1; then
    printf '%-12s | %-8s | %-22s | %-12s | %-12s | %s\n' "${host}" "DOWN" "-" "-" "-" "${tags}"
    continue
  fi

  # Daten im selben SSH-Aufruf holen.
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
    printf '%-12s | %-8s | %-22s | %-12s | %-12s | %s\n' "${host}" "ERR" "-" "-" "-" "${tags}"
    continue
  fi

  uptime_field="$(echo "${remote_output}" | cut -d'|' -f1)"
  commit_field="$(echo "${remote_output}" | cut -d'|' -f2)"
  brew_field="$(echo "${remote_output}" | cut -d'|' -f3)"

  short_commit="$(echo "${commit_field}" | awk '{print $1}')"

  printf '%-12s | %-8s | %-22s | %-12s | %-12s | %s\n' \
    "${host}" "UP" "${uptime_field:0:22}" "${short_commit:0:12}" "${brew_field}" "${tags}"
done