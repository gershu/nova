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
├── dotfiles/zsh/
│   ├── .zshrc                  # gesymlinkt nach ~/.zshrc
│   └── .p10k.zsh               # Prompt mit NOVA_ROLE-Farben
├── scripts/
│   ├── provision_node.sh       # auf nova-dev: SSH-Material auf neuen Node kopieren
│   ├── node_set_name.sh        # auf neuem Node: Hostname + NOVA_ROLE setzen
│   ├── node_bootstrap.sh       # auf neuem Node: brew + Repo-Clone + erstes Deploy
│   ├── node_deploy.sh          # auf jedem Node: git pull + Dotfiles + brew bundle
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

Idempotent: `git pull` → Dotfiles (re-)linken → `brew bundle`.

### Status-Übersicht

Auf nova-dev:

```bash
./scripts/cluster_status.sh
```

Zeigt pro Worker: Reachability, Uptime, letzter Commit-SHA, Brewfile-Drift.

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
