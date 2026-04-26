# nova

Job Development & Execution Plattform für einen macOS-Cluster.

## Topologie

```
┌──────────────────┐                  ┌──────────────────┐
│     nova-dev     │  ssh + git push  │    GitHub: nova  │
│  (Control Plane) │ ───────────────▶ │  (Golden Source) │
│  + Entwicklung   │                  └────────┬─────────┘
└────────┬─────────┘                           │ git pull
         │ ssh (status, deploy-trigger)        │ (Deploy Key, read-only)
         │                                     ▼
         ├──────────────▶ nova-uat   ◀──────────┤
         │                                     │
         └──────────────▶ nova-prod  ◀──────────┘
```

- **Zielsystem:** `nova`
- **Technical User:** `novaadm` (auf jedem Node)
- **Repo:** `nova` (dieses Repo)
- **Environments (initial):** DEV, UAT, PROD — strikt 1:1 zu Worker-Nodes
- **Hostnamen:** `nova-dev`, `nova-uat`, `nova-prod`

## Verzeichnisstruktur

```
nova/
├── README.md
├── Brewfile                    # identisch fuer alle Environments
├── requirements.txt            # Python-Pakete fuer den Cluster-venv
├── .python-version             # pyenv-Pin (z.B. 3.11.9)
├── dotfiles/zsh/
│   ├── .zshrc                  # gesymlinkt nach ~/.zshrc
│   └── .p10k.zsh               # Prompt mit NOVA_ROLE-Farben
├── workloads/                  # Job-Code (siehe Konvention unten)
│   └── hello_world/
│       ├── run.sh              # Entry-Point Wrapper (aktiviert venv)
│       └── hello_world.py      # Python-Logik
├── scripts/
│   ├── provision_node.sh       # auf nova-dev: SSH-Material auf neuen Node kopieren
│   ├── node_set_name.sh        # auf neuem Node: Hostname + NOVA_ROLE setzen
│   ├── node_bootstrap.sh       # auf neuem Node: brew + Repo-Clone + erstes Deploy
│   ├── node_deploy.sh          # auf jedem Node: git pull + Dotfiles + brew bundle + Python venv
│   └── cluster_status.sh       # auf nova-dev: SSH-Status-Check ueber UAT/PROD
└── config/
    └── hosts                   # Hostliste fuer cluster_status.sh
```

## Workflows

### Neuen Node hinzufügen

**Voraussetzungen am neuen Mac (manuell vorab):**

- macOS installiert, User `novaadm` angelegt
- Remote Login (SSH) aktiv: System Settings → General → Sharing
- Hostname auf `nova-<env>` gesetzt:
  ```bash
  sudo scutil --set HostName      nova-uat
  sudo scutil --set LocalHostName nova-uat
  sudo scutil --set ComputerName  nova-uat
  sudo killall -HUP mDNSResponder
  ```
  (Alternativ über System Settings UI. Wer das überspringt, muss
  `node_set_name.sh` später manuell auf dem Node nachholen.)
- Mac im LAN erreichbar (mDNS via `<host>.local`)

**Auf nova-dev als `novaadm`:**

```bash
~/nova/scripts/provision_node.sh nova-uat UAT
```

Kopiert das SSH-Material (id_ed25519, authorized_keys, ssh/config) auf
den neuen Node. Beim ersten Connect einmalig das `novaadm`-Passwort
auf dem Ziel-Mac eingeben.

**Auf dem neuen Node als `novaadm`:**

```bash
ssh novaadm@nova-uat
git clone git@github.com:gershu/nova.git ~/nova
~/nova/scripts/node_bootstrap.sh
```

`node_bootstrap.sh` installiert Homebrew, überspringt den bereits
durchgeführten Clone und ruft `node_deploy.sh` auf — am Ende ist der
Node deploy-fertig.

### Routine-Deployment

Auf einem Node lokal oder remote von nova-dev:

```bash
~/nova/scripts/node_deploy.sh
# oder remote:
ssh nova-uat '~/nova/scripts/node_deploy.sh'
```

Idempotent: `git pull` → Dotfiles (re-)linken → `brew bundle` → Python-venv
synchronisieren (`pyenv install` falls nötig, `~/nova/.venv` anlegen,
`pip install -r requirements.txt`).

### Status-Übersicht

Auf nova-dev:

```bash
./scripts/cluster_status.sh
```

Zeigt pro Worker: Reachability, Uptime, letzter Commit-SHA, Brewfile-Drift.

## Workloads

### Konvention

Jeder Job liegt unter `workloads/<job_name>/` und besteht aus mindestens:

```
workloads/<job_name>/
├── run.sh           # Entry-Point: aktiviert ${REPO_DIR}/.venv, ruft Logik
└── <job_name>.py    # Python-Logik
```

`run.sh` ist der einzige unterstützte Aufruf-Pfad. Direkt `python <job>.py`
zu rufen funktioniert auch, lädt aber den Cluster-venv nicht — Pakete aus
`requirements.txt` sind dann nur verfügbar, wenn der venv schon im Shell-PATH
aktiviert ist.

### Python-Laufzeitumgebung

- `requirements.txt` am Repo-Root listet alle Pakete (mit Versionsbereichen).
- `.python-version` am Repo-Root pinnt die Python-Version für pyenv.
- `~/nova/.venv` wird von `node_deploy.sh` pro Node lokal aufgebaut/aktualisiert.
  Der Ordner ist gitignored — er ist kein Repo-Inhalt, sondern abgeleiteter
  Build-Output.
- Garantie: nach erfolgreichem `node_deploy.sh` haben alle Nodes byte-identische
  Python-Version + identische Pakete in identischen Versionen.

### Job ausführen

Manuell auf einem Node lokal:

```bash
~/nova/workloads/hello_world/run.sh
```

Oder remote von einem beliebigen Node aus (typischerweise nova-dev):

```bash
ssh nova-uat  '~/nova/workloads/hello_world/run.sh'
ssh nova-prod '~/nova/workloads/hello_world/run.sh'
```

Welcher Node welchen Job ausführt entscheidet der Aufrufer — kein Scheduler,
keine Lastverteilung, keine Job-Queue. Output landet auf stdout/stderr des
Aufrufers (kein zentrales Logging im Scope).

### Neuen Workload anlegen

```bash
mkdir -p ~/Documents/Claude/Projects/nova/workloads/<name>
# <name>.py + run.sh erstellen (run.sh als Kopie von hello_world/run.sh)
git add workloads/<name>
git commit && git push
# auf den Worker-Nodes (als novaadm): node_deploy.sh fuer git pull
```

Falls der Job neue Python-Pakete braucht: vorher `requirements.txt` ergänzen.
`node_deploy.sh` installiert sie beim nächsten Lauf in den Cluster-venv.

## Security

- SSH-Keys (`id_ed25519`, `id_ed25519.pub`, `authorized_keys`, `config`)
  liegen ausschließlich im `novaadm`-Home auf nova-dev und werden manuell per
  `provision_node.sh` (rsync) auf neue Nodes kopiert.
- **Keine** Secrets im Repo.
- GitHub-Auth: derselbe Key ist als **Deploy Key (read-only)** im nova-Repo
  bei GitHub eingetragen — Worker pullen via `git@github.com:...`.

## Environment-Farben (powerlevel10k via `$NOVA_ROLE`)

| Env  | Farbcode | Effekt          |
|------|----------|------------------|
| DEV  | 70       | grün             |
| UAT  | 220      | klares gelb      |
| PROD | 124      | weiches PROD-rot |

`NOVA_ROLE` wird in `~/.nova_role` (nicht versioniert) abgelegt, von `~/.zshrc`
geladen, und steuert den Prompt in `dotfiles/zsh/.p10k.zsh`.

## Bewusst nicht im Scope

- Rollback-Mechanik (Fix per `git revert` + redeploy)
- zentrales Logging
- HW/OS-Vorab-Check
- mehrere Worker pro Environment
