# nova

Trading-Strategie-Lab + Cluster-Infrastruktur auf macOS-Knoten. Ein Repo,
ein Deploy-Pfad.

## Topologie

```
nova-hub (Control Plane + Worker)
├── nova-w1   (Editor + Worker)
├── nova-w2   (Worker, Clamshell)
└── nova-w5   (LLM-Inference, Ollama)
```

- **Zielsystem:** `nova`
- **Technical User:** `novaadm` (auf jedem Node)
- **NOVA_ROLE:** `HUB` oder `WORKER` — aus Hostname abgeleitet
- **Capability-Metadaten:** `config/nodes.yaml`

## Verzeichnisstruktur

```
nova/
├── Brewfile                    # identisch auf allen Nodes
├── requirements.txt            # Python-Pakete (Bereiche)
├── requirements-lock.txt       # deterministischer Pin
├── .python-version             # pyenv-Pin
│
├── modules/                    # Anwendungs-Code (ingest, monitor, dashboard, ...)
├── notebooks/                  # Jupyter-Analysen
│
├── config/
│   ├── nodes.yaml              # Cluster-Inventar
│   ├── strategies/             # Trading-Strategien
│   ├── watchlists/             # Watchlist-Definitionen
│   └── universe_sp500.yaml     # Symbol-Universum
│
├── scripts/                    # Cluster + Daily-Daemons
│   ├── node_bootstrap.sh       # Initial-Setup neuer Node
│   ├── node_deploy.sh          # git pull + venv + brew + dotfiles
│   ├── install_daemon.sh       # generic LaunchDaemon-Installer
│   ├── check_ib_gateway.sh
│   ├── lab_*_daily.sh          # Schedule-Wrapper (IB-Precheck, params, python -m)
│   └── ...
│
├── dotfiles/
│   ├── zsh/  ssh/  git/  vim/  # symlinked nach ~/...
│   └── launchd/                # LaunchDaemon-Plists
│
└── docs/
    ├── lab.md                  # Datenmodell-Konvention (Prefixes, portfolio_core)
    └── nova_overview.xlsx
```

## Quickstart

### Neuer Node provisionieren

Einmalig auf einem frischen Mac:
```bash
# Auf nova-hub vorbereiten (Deploy-Key, Hostname festlegen):
~/nova/scripts/provision_node.sh nova-w<N>

# Auf dem neuen Mac (als novaadm):
git clone git@github.com:gershu/nova.git ~/nova
~/nova/scripts/node_bootstrap.sh
~/nova/scripts/node_deploy.sh
```

### Routine-Deployment

Bei Code-Updates:
```bash
ssh novaadm@nova-hub '~/nova/scripts/node_deploy.sh'
# bzw. parallel auf alle Worker:
for h in nova-w1 nova-w2 nova-w5; do
  ssh novaadm@$h '~/nova/scripts/node_deploy.sh' &
done; wait
```

### Modul direkt aufrufen

Manuelle Aufrufe:
```bash
cd ~/nova && source .venv/bin/activate
python -m modules.market_monitor show
python -m modules.fred_ingest fetch-all
python -m modules.dashboard         # (lokal — Daemon laeuft via Plist)
```

### Daemon installieren

```bash
sudo ~/nova/scripts/install_daemon.sh lab.dashboard
sudo ~/nova/scripts/install_daemon.sh lab.fred_ingest
sudo ~/nova/scripts/install_daemon.sh lab.market_monitor
# ... (Liste: dotfiles/launchd/de.gershu.nova.lab.*.plist)
```

Stop:
```bash
sudo launchctl bootout system /Library/LaunchDaemons/de.gershu.nova.lab.<label>.plist
```

## Konfigurations-Hierarchie

Drei Tiers, von general nach spezifisch:

1. **Repo** (`config/`, `Brewfile`, `.python-version`, `requirements-lock.txt`) —
   versioniert, gilt fuer alle Nodes
2. **Per-Node** (`~/.nova_env`) — Secrets + Node-spezifische Overrides
   (z.B. `NOVA_FRED_API_KEY`, `LAB_DB_PATH`, IB-Credentials)
3. **CLI/Workload-Params** — JSON-Files in `~/jobs/<workload>_daily.json`,
   gelesen von den `scripts/lab_*_daily.sh` Wrappern, an Modul via
   `NOVA_PARAMS_FILE` env-var weitergereicht

Beispiel `~/.nova_env`:
```bash
export NOVA_FRED_API_KEY="..."
export LAB_DB_PATH="$HOME/nova_data/lab.duckdb"
export OBSIDIAN_VAULT_PATH="$HOME/nova_output/obsidian"
export NOVA_REPO_DIR="$HOME/nova"
```

## Python-Pakete updaten

```bash
cd ~/nova
source .venv/bin/activate
pip install --upgrade <paket>      # ggf. requirements.txt anpassen
pip freeze > requirements-lock.txt # neue Lock-Datei committen
git commit -am "bump <paket>"
git push
# auf allen Nodes:
~/nova/scripts/node_deploy.sh
```

## Workload-Konvention

Jedes Modul unter `modules/` ist self-contained mit:

- `modules/<name>/__main__.py` — CLI-Entry (argparse-Subcommands)
- `modules/<name>/sql/*.sql` — Schema (idempotent, `CREATE OR REPLACE`)
- `modules/<name>/__init__.py`

Aufruf:
```bash
python -m modules.<name> <subcommand> [args]
```

Daily-Daemon-Wrapper in `scripts/lab_<name>_daily.sh` enthalten optional:
- IB-Gateway-Precheck (`scripts/check_ib_gateway.sh`)
- Default-Params-File-Generation
- venv-Aktivierung + `python -m modules.<name>` Aufruf

## Security

- `~/.nova_env` und Secrets ausserhalb des Repos — nie committed
- SSH-Keys via `dotfiles/ssh/config` symlinked, Private Keys aus Repo via
  `.gitignore` ausgeschlossen
- Sudo nur fuer LaunchDaemon-Installer (`install_daemon.sh`,
  `install_caffeinate_agent.sh`, `install_ollama_agent.sh`) — alle anderen
  Scripts laufen als `novaadm` ohne sudo

## Rollen-Farben (powerlevel10k)

`$NOVA_ROLE` wird in `~/.zshrc` aus dem Hostname abgeleitet:
- `HUB` (nova-hub) — Lila
- `WORKER` (nova-w*) — Cyan

Override moeglich via `~/.nova_role`.

## Bewusst nicht im Scope

- Multi-Worker-Job-Dispatch (queue/picker/submit): wurde entfernt — nicht
  produktiv genutzt. Workloads laufen direkt auf nova-hub via LaunchDaemons.
- Auto-Trade: HART human-in-the-loop. Alle Signale werden in `sig_alerts` /
  `sig_*_briefings` geschrieben, nicht automatisch ausgefuehrt.
