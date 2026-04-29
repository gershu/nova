# nova

Job Development & Execution Plattform für einen macOS-Cluster.

## Topologie

```
┌──────────────────┐                  ┌──────────────────┐
│     nova-hub     │  ssh + git push  │    GitHub: nova  │
│  (Control Plane) │ ───────────────▶ │  (Golden Source) │
│  + Entwicklung   │                  └────────┬─────────┘
└────────┬─────────┘                           │ git pull
         │ ssh (status, deploy-trigger)        │ (Deploy Key, read-only)
         │                                     ▼
         ├──────────────▶ nova-w1   ◀──────────┤
         ├──────────────▶ nova-w2   ◀──────────┤
         ├──────────────▶ nova-w3   ◀──────────┤
         └──────────────▶ nova-wN   ◀──────────┘
```

- **Zielsystem:** `nova`
- **Technical User:** `novaadm` (auf jedem Node)
- **Repo:** `nova` (dieses Repo)
- **Naming:** `nova-hub` (Control Plane) + `nova-w<N>` (Worker, ab 1 durchnumeriert)
- **NOVA_ROLE:** `HUB` oder `WORKER` — automatisch aus Hostname abgeleitet,
  Override via `~/.nova_role` möglich
- **Capability-Metadaten:** in `config/nodes.yaml` pro Node (chip, ram_gb,
  os, tags) — Quelle der Wahrheit für Cluster-Übersicht und potenzielle
  Workload-Dispatch-Logik. Hardware ist nicht im Hostnamen kodiert,
  damit der Name stabil bleibt wenn die Maschine umgewidmet/aufgerüstet wird.

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
├── dotfiles/launchd/
│   └── de.gershu.nova.picker.plist  # launchd-Daemon fuer nova_picker (nur hub)
├── workloads/                  # Job-Wrapper (siehe Konvention unten)
│   ├── hello_world/            # Embedded-Workload (Code im nova-Repo)
│   │   ├── run.sh
│   │   └── hello_world.py
│   └── csp_scanner/            # Sibling-Repo-Workload: Code lebt anderswo
│       └── run.sh              # Wrapper, ruft ~/csp_scanner/src/main.py
├── scripts/
│   ├── provision_node.sh       # auf nova-hub: SSH-Material auf neuen Worker kopieren
│   ├── node_set_name.sh        # auf neuem Node: Hostname + NOVA_ROLE setzen
│   ├── node_bootstrap.sh       # auf neuem Node: brew + Repo-Clone + erstes Deploy
│   ├── node_deploy.sh          # auf jedem Node: pull + dotfiles + brew + venv + workload-repos
│   ├── cluster_status.sh       # auf nova-hub: liest nodes.yaml, polled Worker via SSH
│   ├── nova_run.sh             # auf nova-hub: Workload an Worker dispatchen (synchron, Phase 0)
│   ├── nova_submit.sh          # auf nova-hub: Job-Spec ins ~/nova_jobs/queue/ schreiben (Phase 1)
│   ├── nova_picker.sh          # auf nova-hub via launchd: Queue abarbeiten, dispatch via nova_run.sh
│   ├── nova_status.sh          # auf nova-hub: Job-Queue-Übersicht / Detail / Log
│   └── install_picker.sh       # einmaliger sudo-Setup des Picker-LaunchDaemon auf nova-hub
└── config/
    ├── nodes.yaml              # Node-Inventar (Worker + Capability-Metadaten)
    └── workload_repos.txt      # externe Repos, die node_deploy.sh nach ~/<dir> klont/pullt
```

## Workflows

### Neuen Worker-Node hinzufügen

**Voraussetzungen am neuen Mac (manuell vorab):**

- macOS installiert, User `novaadm` angelegt
- Remote Login (SSH) aktiv: System Settings → General → Sharing
- Hostname auf `nova-w<N>` gesetzt (nächste freie Nummer aus `config/nodes.yaml`):
  ```bash
  sudo scutil --set HostName      nova-w3
  sudo scutil --set LocalHostName nova-w3
  sudo scutil --set ComputerName  nova-w3
  sudo killall -HUP mDNSResponder
  ```
  (Alternativ über System Settings UI. Wer das überspringt, muss
  `node_set_name.sh` später manuell auf dem Node nachholen.)
- Mac im LAN erreichbar (mDNS via `<host>.local`)

**Auf nova-hub als `novaadm`:**

```bash
~/nova/scripts/provision_node.sh nova-w3
```

Kopiert das SSH-Material (id_ed25519, authorized_keys, ssh/config) auf
den neuen Node. Beim ersten Connect einmalig das `novaadm`-Passwort
auf dem Ziel-Mac eingeben.

**Auf dem neuen Node als `novaadm`:**

```bash
ssh novaadm@nova-w3
git clone git@github.com:gershu/nova.git ~/nova
~/nova/scripts/node_bootstrap.sh
```

`node_bootstrap.sh` installiert Homebrew, überspringt den bereits
durchgeführten Clone und ruft `node_deploy.sh` auf — am Ende ist der
Node deploy-fertig.

**Letzter Schritt — Node ins Inventar aufnehmen** (als `stefan_pro`):

```bash
cd ~/Documents/Claude/Projects/nova
# config/nodes.yaml: Eintrag fuer nova-w3 mit echten Werten ausfuellen
#   chip: arm64 oder x86_64
#   ram_gb: tatsaechlicher Wert
#   os: macos-15 / macos-14 / macos-10.15 etc.
#   tags: [...] passende Capability-Tags
git add config/nodes.yaml
git commit -m "Add nova-w3 to inventory"
git push origin main
```

### Routine-Deployment

Auf einem Node lokal oder remote von nova-hub:

```bash
~/nova/scripts/node_deploy.sh
# oder remote:
ssh nova-w1 '~/nova/scripts/node_deploy.sh'
```

Idempotent: `git pull` → Dotfiles (re-)linken → `brew bundle` → Python-venv
synchronisieren (`pyenv install` falls nötig, `~/nova/.venv` anlegen,
`pip install -r requirements.txt`).

### Status-Übersicht

Auf nova-hub:

```bash
~/nova/scripts/cluster_status.sh
```

Liest `config/nodes.yaml` und pollt alle Worker (`role: worker`) per SSH.
Zeigt pro Worker: Reachability, Uptime, letzter Commit-SHA, Brewfile-Drift,
Capability-Tags aus dem Inventar.

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
# 1) Auf nova-hub als novaadm: Bereich in requirements.txt anpassen
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

# 3) Auf jedem Worker (als novaadm): rüberziehen
ssh nova-w1 '~/nova/scripts/node_deploy.sh'
ssh nova-w2 '~/nova/scripts/node_deploy.sh'
# (entsprechend fuer w3, w4, ...)
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
auf nova-w2, und der Default ist überschrieben — ohne dass du die
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

**Lokal auf einem Node** (Smoke-Test, manuelle Runs):

```bash
~/nova/workloads/hello_world/run.sh
~/nova/workloads/csp_scanner/run.sh
```

**Remote vom Hub via Dispatcher** (Standard-Pfad, synchron):

```bash
~/nova/scripts/nova_run.sh hello_world nova-w1
~/nova/scripts/nova_run.sh csp_scanner nova-w2 --params-file ~/jobs/aapl.json
~/nova/scripts/nova_run.sh csp_scanner nova-w2 -- --extra-flag wert
```

`nova_run.sh`:
- validiert dass der Workload existiert und der Worker in `nodes.yaml` (role=worker) steht
- shipt optional eine Params-Datei via scp auf den Worker und exportiert
  `NOVA_PARAMS_FILE` mit dem Remote-Pfad (Workload kann's per
  `os.environ.get('NOVA_PARAMS_FILE')` lesen + JSON parsen)
- ssh'd, streamt stdout/stderr live, gibt den Remote-Exit-Code zurück
- räumt die Params-Datei nach dem Run auf

**Asynchron via Job-Queue + Picker** (Phase-1-Variante):

```bash
# Job einreichen (return't sofort mit Job-ID)
JOB_ID=$(~/nova/scripts/nova_submit.sh csp_scanner nova-w2 --params-file ~/jobs/aapl.json)

# Status abfragen
~/nova/scripts/nova_status.sh                    # Counts + letzte 10 Done
~/nova/scripts/nova_status.sh --job "${JOB_ID}"  # voller Spec inkl. exit_code
~/nova/scripts/nova_status.sh --log "${JOB_ID}"  # stdout/stderr des Runs
```

Mechanik:
- `nova_submit.sh` schreibt Job-Spec (JSON, mit inlined Params) atomar in
  `~/nova_jobs/queue/<id>.json`.
- `nova_picker.sh` läuft per launchd-**Daemon** alle 10s auf nova-hub. Per
  Iteration: claimed via mv nach `running/`, dispatcht via `nova_run.sh`,
  schreibt Result + log_path nach `done/<id>.json`.
- Concurrency-Schutz: mkdir-basierter Lock — kein Doppel-Pickup wenn ein
  Job länger läuft als das Polling-Intervall.
- Fehler-Workflow: bei exit ≠ 0 wird `status: failed` ins JSON geschrieben,
  Job landet trotzdem in done/. Re-Submit ist manuell.

**LaunchDaemon-Setup auf nova-hub** (einmalig, mit sudo):

```bash
# auf nova-hub als novaadm:
sudo ~/nova/scripts/install_picker.sh
```

Das kopiert die plist nach `/Library/LaunchDaemons/`, setzt root:wheel +
mode 644, und bootstrap't den Daemon im system-Kontext. Er läuft als
`novaadm` (per `UserName`-Key), startet automatisch beim Boot, **braucht
keine Login-Session** (LaunchAgent würde das brauchen — funktioniert nicht
auf headless Hubs, die nur via SSH bedient werden).

`node_deploy.sh` installiert den Daemon nicht selbst (kann nicht sudo'n);
es checkt nur ob er aktiv ist und gibt einen Hinweis aus, falls nicht.

Welcher Node welchen Job ausführt entscheidet der Aufrufer — kein
Capability-basierter Auto-Dispatch (steht für eine spätere Phase, sobald
Tags aus `nodes.yaml` mit Workload-Anforderungen gematcht werden sollen).

### Parameter-Konvention für Workloads

Drei Wege wie Werte zur Workload kommen, in dieser Präzedenz (jeweils
Spezialfall vor Generischem):

1. **Per-Invocation Parameter-Datei** (für nova_run.sh dispatched jobs).
   `--params-file <pfad>` shipped die Datei nach `/tmp/...` auf dem Worker
   und setzt `NOVA_PARAMS_FILE`. Workload-Code:
   ```python
   import os, json, pathlib
   pf = os.environ.get("NOVA_PARAMS_FILE")
   params = json.loads(pathlib.Path(pf).read_text()) if pf else {}
   ```
2. **Per-Node `~/.nova_env`** (Tier 2, siehe oben). Für Werte, die pro Node
   stabil sind: Hosts, Ports, Account-IDs.
3. **Repo-Defaults** (Tier 1, in git). Für Settings, die für alle Nodes
   gleich sind: workloads/csp_scanner/run.sh' hardcoded `--watchlist`,
   yaml-files im Sibling-Repo, etc.

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
  liegen ausschließlich im `novaadm`-Home auf nova-hub und werden manuell per
  `provision_node.sh` (rsync) auf neue Nodes kopiert.
- **Keine** Secrets im Repo.
- GitHub-Auth: derselbe Key ist als **Deploy Key (read-only)** im nova-Repo
  bei GitHub eingetragen — Worker pullen via `git@github.com:...`.

## Rollen-Farben (powerlevel10k via `$NOVA_ROLE`)

| Rolle  | Farbcode | Effekt | Bedeutung                           |
|--------|----------|--------|--------------------------------------|
| HUB    | 124      | rot    | Hub — git push, dispatch (sensibel) |
| WORKER | 70       | grün   | Worker — sichere Ausführung         |
| *      | 39       | blau   | Fallback (Hostname matcht kein Schema) |

`NOVA_ROLE` wird automatisch aus dem Hostname abgeleitet (`nova-hub` → HUB,
`nova-w<N>` → WORKER, durch die case-Logik in `dotfiles/zsh/.zshrc`).
Override-File `~/.nova_role` (nicht versioniert) kann die automatische
Ableitung überschreiben — z.B. wenn ein Node temporär eine andere Rolle hat
oder der Hostname dem Schema nicht folgt.

## Bewusst nicht im Scope

- Rollback-Mechanik (Fix per `git revert` + redeploy)
- zentrales Logging
- HW/OS-Vorab-Check (außer dem manuellen Inventar in `config/nodes.yaml`)
- automatischer Workload-Dispatch (Capability-Matching aus `nodes.yaml` →
  Auswahl des Nodes, der die Tags erfüllt) — Mechanik ist ungenutzt da, kann
  später ergänzt werden
