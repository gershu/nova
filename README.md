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
├── requirements.txt            # Python-Pakete fuer den Cluster-venv (Bereiche)
├── requirements-lock.txt       # deterministische Pin (== Versionen, transitiv)
├── .python-version             # pyenv-Pin (z.B. 3.11.9)
├── dotfiles/zsh/
│   ├── .zshrc                  # gesymlinkt nach ~/.zshrc
│   └── .p10k.zsh               # Prompt mit NOVA_ROLE-Farben
├── workloads/                  # Job-Wrapper (siehe Konvention unten)
│   ├── hello_world/            # Embedded-Workload (Code im nova-Repo)
│   │   ├── run.sh
│   │   └── hello_world.py
│   └── csp_scanner/            # Sibling-Repo-Workload: Code lebt anderswo
│       └── run.sh              # Wrapper, ruft ~/csp_scanner/src/main.py
├── scripts/
│   ├── provision_node.sh       # auf nova-dev: SSH-Material auf neuen Node kopieren
│   ├── node_set_name.sh        # auf neuem Node: Hostname + NOVA_ROLE setzen
│   ├── node_bootstrap.sh       # auf neuem Node: brew + Repo-Clone + erstes Deploy
│   ├── node_deploy.sh          # auf jedem Node: pull + dotfiles + brew + venv + workload-repos
│   └── cluster_status.sh       # auf nova-dev: SSH-Status-Check ueber UAT/PROD
└── config/
    ├── hosts                   # Hostliste fuer cluster_status.sh
    └── workload_repos.txt      # externe Repos, die node_deploy.sh nach ~/<dir> klont/pullt
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

Jeder Job liegt unter `workloads/<job_name>/` und hat mindestens ein
`run.sh` als Entry-Point. Der eigentliche Code kann auf zwei Arten
verortet sein:

**(a) Embedded** — Code lebt im nova-Repo unter `workloads/<job_name>/`.
Beispiel `hello_world`:

```
workloads/hello_world/
├── run.sh
└── hello_world.py
```

Sinnvoll für kleine, nova-spezifische Skripte mit wenigen Files. Code-Änderungen
werden direkt im nova-Repo committet, sind nach `node_deploy.sh` auf allen Nodes.

**(b) Sibling-Repo** — Code lebt in einem eigenen GitHub-Repo, das auf
jedem Node unter `~/<repo_name>` lokal geklont wird. nova/workloads/<job_name>/
enthält nur den `run.sh`-Wrapper, der ins Sibling-Repo `cd`'t. Beispiel
`csp_scanner`:

```
workloads/csp_scanner/
└── run.sh                  # cd ~/csp_scanner && python -m src.main ...

~/csp_scanner/              # eigenes git-Repo, gepullt durch node_deploy.sh
├── src/
├── config/
└── ...
```

Sinnvoll für Projekte, die eine eigene Identität haben (eigenes README,
eigene Issue-/PR-History, eigenständige Releases). Sibling-Repos werden
in `config/workload_repos.txt` registriert und von `node_deploy.sh`
Schritt 5 mitsynchronisiert.

`run.sh` ist in beiden Varianten der einzige unterstützte Aufruf-Pfad.

### Python-Laufzeitumgebung

- `requirements.txt` definiert die *Intent* — welche Pakete der Cluster-venv
  haben soll, mit Versionsbereichen (z.B. `pandas>=2.2,<3`).
- `requirements-lock.txt` ist das *Ergebnis* — alle Pakete (inkl. transitiver
  Deps) als exakt gepinnte `==`-Versionen, generiert per `pip freeze`.
  Dies ist die Quelle der Wahrheit beim Deploy.
- `.python-version` pinnt die Python-Version für pyenv.
- `~/nova/.venv` wird von `node_deploy.sh` pro Node lokal aufgebaut. Gitignored,
  abgeleiteter Build-Output.

`node_deploy.sh` installiert bevorzugt aus der Lock-Datei → byte-identische
Pakete auf allen Nodes. Fehlt die Lock-Datei, wird auf `requirements.txt`
zurückgefallen (mit WARN, weil dann nicht-deterministisch).

### Python-Pakete updaten / Lock-Datei regenerieren

Updates sind explizit, nicht implizit beim Deploy. Wenn du eine Version
hochziehen oder ein neues Paket aufnehmen willst:

```bash
# 1) Auf nova-dev als novaadm: Bereich in requirements.txt anpassen
#    (oder neues Paket dazu), dann frisch auflösen + freezen:
~/nova/.venv/bin/pip install -U -r ~/nova/requirements.txt
~/nova/.venv/bin/pip freeze --exclude-editable > /tmp/requirements-lock.txt

# 2) Als stefan_pro: Lock-Datei ins Editor-Repo übernehmen + committen
cp /tmp/requirements-lock.txt ~/Documents/Claude/Projects/nova/requirements-lock.txt
cd ~/Documents/Claude/Projects/nova
git diff requirements-lock.txt    # zur Kontrolle
git add requirements.txt requirements-lock.txt
git commit -m "Update Python deps lock"
git push origin main

# 3) Auf nova-uat / nova-prod (als novaadm): rüberziehen
ssh nova-uat  '~/nova/scripts/node_deploy.sh'
ssh nova-prod '~/nova/scripts/node_deploy.sh'
```

Bis dieser Drei-Schritt-Flow durchlaufen ist, behalten alle Nodes die
bisherigen gepinnten Versionen.

### Output-Konvention

Jobs schreiben Output in `~/nova_output/<job_name>/` (per Node lokal,
nicht versioniert). Innerhalb des Workload-Ordners legt `run.sh` einen
Symlink `output → ~/nova_output/<job_name>/` an, damit Code, der
relativ nach `output/` schreibt, automatisch im richtigen Ziel landet
ohne Code-Änderung. Der Symlink ist via `.gitignore` (workloads/*/output)
ausgeschlossen.

### Konfigurations-Hierarchie (3 Tiers)

Werte für Workloads kommen aus drei Schichten, in dieser Präzedenz
(später überschreibt früher):

1. **Tier 1 — Repo-Defaults** (in git, identisch über alle Nodes).
   Z.B. `workloads/csp_scanner/config/settings.yaml` (embedded) oder
   `~/csp_scanner/config/*.yaml` (sibling-repo). Änderung: git commit
   + push + `node_deploy.sh`.
2. **Tier 2 — Per-Node `~/.nova_env`** (gitignored, manuell pro Node).
   Shell-File mit `export VAR=value`-Zeilen. Wird von jedem `run.sh`
   automatisch sourced. Die Variablen sind danach als `os.environ.*`
   in Python sichtbar. Änderung: Datei direkt editieren, sofort
   wirksam. Kein Deploy nötig.
3. **Tier 3 — Per-Invocation Args** (Command-Line). `run.sh "$@"`
   reicht alles weitere an Python durch. Für one-off Overrides.

`run.sh`-Files exposen Tier-2-Hooks für die wichtigsten Werte. Beispiel
csp_scanner:

```bash
WATCHLIST_PATH="${CSP_SCANNER_WATCHLIST:-config/watchlist.yaml}"
SETTINGS_PATH="${CSP_SCANNER_SETTINGS:-config/settings.yaml}"
```

Setze `CSP_SCANNER_WATCHLIST=config/watchlist_prod.yaml` in `~/.nova_env`
auf nova-prod, und der Default ist überschrieben — ohne dass du die
`run.sh` oder die Repo-yaml-Files anrührst.

**Setup pro Node (einmalig, als novaadm):**

```bash
cp ~/nova/nova_env.example ~/.nova_env
chmod 600 ~/.nova_env
vi ~/.nova_env     # Werte fuer DIESEN Node ausfuellen
```

`nova_env.example` im Repo dokumentiert die verfügbaren Variablen.
Die echte `~/.nova_env` ist niemals versioniert — sie kann pro Node
divergieren (das ist der Sinn).

### Job ausführen

Manuell auf einem Node lokal:

```bash
~/nova/workloads/hello_world/run.sh
~/nova/workloads/csp_scanner/run.sh
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

Falls der Job neue Python-Pakete braucht: erst `requirements.txt` ergänzen,
dann `requirements-lock.txt` regenerieren (siehe oben). `node_deploy.sh`
zieht die neue Version beim nächsten Lauf.

### Sibling-Repo-Workload anlegen

Wenn der Workload ein eigenes Repo bekommen soll (z.B. weil er groß genug
ist um eigene History zu rechtfertigen):

1. Repo bei GitHub anlegen, z.B. `gershu/<workload_name>`.
2. Den nova-Deploy-Key (`~/.ssh/id_ed25519.pub` von novaadm) zusätzlich als
   Deploy Key (read-only) auf diesem Repo registrieren — selber Key wie bei
   `gershu/nova`. GitHub warnt „This key is already in use" beim Hinzufügen,
   das ist erwartet und zu bestätigen.
3. In `config/workload_repos.txt` eine Zeile ergänzen:
   ```
   <lokales-verzeichnis> git@github.com:gershu/<workload_name>.git
   ```
4. `workloads/<workload_name>/run.sh` anlegen (als Kopie + Anpassung von
   `csp_scanner/run.sh`), das `~/<lokales-verzeichnis>` als CWD nutzt.
5. nova committen + pushen, dann `node_deploy.sh` auf jedem Node — Schritt 5
   klont das neue Sibling-Repo.

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
