# IB Gateway via IBC — Setup auf nova-hub

IBC (IBController) startet das IBKR Gateway automatisch, loggt sich mit
gespeicherten Credentials ein und handhabt den nächtlichen IBKR-Auto-Restart.
Dieses Dokument beschreibt das versionierbare Setup im Repo. Sensible Daten
(Credentials, Pfade) bleiben außerhalb, in `~/.nova_env`.

Ziel-Endpunkt nach erfolgreichem Setup: **IB Gateway auf `127.0.0.1:4001`**
(live) bzw. `:4002` (paper), 24/7 erreichbar von allen nova-Modulen.

## Voraussetzungen

1. **IBKR-Account** mit aktivierter API (TWS/Gateway-API in
   *Account Management → Settings → API → Settings* freischalten).
2. **IB Gateway** installiert. Download via
   [IBKR](https://www.interactivebrokers.com/en/index.php?f=14099#tws-software),
   *Gateway Latest*. Installations­ziel z. B.
   `/Applications/IB Gateway 10.30/`.
3. **IBC** — manuelle Installation von den GitHub-Releases (keinen
   Homebrew-Tap gibt es nicht). Auto-Discovery der aktuellen
   macOS-Release-URL über die GitHub-API, dann download + unzip:
   ```sh
   mkdir -p ~/ibc && cd ~/ibc
   url=$(curl -s https://api.github.com/repos/IbcAlpha/IBC/releases/latest \
         | grep -o 'https://[^"]*IBCMacos[^"]*\.zip' | head -1)
   echo "Lade: $url"
   curl -L -o ibc.zip "$url"
   unzip ibc.zip
   chmod +x scripts/*.sh
   ```
   Fallback bei Problemen: auf
   [github.com/IbcAlpha/IBC/releases](https://github.com/IbcAlpha/IBC/releases)
   den `IBCMacos-X.Y.Z.zip`-Link aus dem obersten Release manuell kopieren.
   Installations­pfad wird in `~/.nova_env` als `NOVA_IBC_PATH` hinterlegt
   (typisch `$HOME/ibc`).
4. **(Optional) Auto-Login für novaadm.** IB Gateway ist eine GUI-App und
   funktioniert *zuverlässiger* mit einer aktiven Aqua-Session. Wir wählen
   trotzdem den system-LaunchDaemon-Pfad (Variante ohne Auto-Login),
   weil nova-hub typisch headless betrieben wird. Falls Gateway in dieser
   Variante nicht stabil startet, ist Auto-Login der Plan B:
   *Systemeinstellungen → Benutzer & Gruppen → Anmelde-Optionen →
   Automatische Anmeldung: novaadm*. Bei aktivem FileVault muss die
   Schlüsselablage zusätzlich konfiguriert werden.

## Env-Vars in `~/.nova_env`

Auf nova-hub als `novaadm` einmalig setzen — `~/.nova_env` ist gitignored
und wird von allen Workload-Wrappern gesourced.

```sh
# IBKR-Credentials und Modus
export NOVA_IBKR_USERNAME="dein_iblogin"
export NOVA_IBKR_PASSWORD="dein_ibpasswort"
export NOVA_IBKR_MODE="live"          # oder 'paper'
export NOVA_IB_API_PORT="4001"        # 4002 für paper

# Pfade zu IBC und IB Gateway
export NOVA_IBC_PATH="$HOME/ibc"
export NOVA_IB_GATEWAY_PATH="/Applications/IB Gateway 10.30"
export NOVA_IB_GATEWAY_VER="10.30"    # nur die Versionsnummer

# IB Gateway Erreichbarkeit (von check_ib_gateway.sh genutzt)
export IB_GATEWAY_HOST="127.0.0.1"
export IB_GATEWAY_PORT="4001"
```

Anschließend: `chmod 600 ~/.nova_env`.

## Installation des system-LaunchDaemons

Die Plist liegt unter `dotfiles/launchd/de.gershu.nova.lab.ib.gateway.plist` und
wird via dem gemeinsamen Repo-Installer eingerichtet — analog zu allen
anderen nova-Daemons.

```sh
cd ~/nova
sudo scripts/install_daemon.sh lab.ib.gateway
```

Das Script kopiert die Plist nach `/Library/LaunchDaemons/`, läd sie ins
`system/`-Domain (`launchctl bootstrap`) und startet sie. Der Daemon
läuft als `UserName=novaadm` (im Plist hart gesetzt), nicht als root —
so findet IBC `~/.nova_env`, IB-Gateway-Settings und User-Cache.

## Verifikation

```sh
# 1. Daemon läuft?
sudo launchctl print system/de.gershu.nova.lab.ib.gateway | head -20

# 2. Gateway-Prozess sichtbar?
pgrep -fl "ibgateway"

# 3. TCP-Port erreichbar?
bash ~/nova/scripts/check_ib_gateway.sh --verbose

# 4. Log lesen
tail -f /Users/novaadm/Library/Logs/nova-ib-gateway.log
```

Bei erfolgreichem Start steht im Log u. a. `Login has completed` und der
Port aus `NOVA_IB_API_PORT` ist offen. Eines der Module schnell testen:

```sh
cd ~/nova && python -m modules.fundamentals probe-ib
```

## Steuerung im Alltag

| Operation         | Befehl                                                                                       |
|-------------------|----------------------------------------------------------------------------------------------|
| Status            | `sudo launchctl print system/de.gershu.nova.lab.ib.gateway`                                       |
| Restart           | `sudo launchctl kickstart -k system/de.gershu.nova.lab.ib.gateway`                                |
| Stop              | `sudo launchctl bootout system /Library/LaunchDaemons/de.gershu.nova.lab.ib.gateway.plist`        |
| Re-install        | `sudo scripts/install_daemon.sh lab.ib.gateway` (idempotent)                                  |
| Manueller Test    | als novaadm: `bash ~/nova/scripts/ib_gateway_start.sh` (Vordergrund, blockiert)               |
| Log live          | `tail -f /Users/novaadm/Library/Logs/nova-ib-gateway.log`                                     |

## Troubleshooting

- **Gateway startet als system-Daemon nicht (kein WindowServer).** Manche
  IBC-/Gateway-Versionen weigern sich ohne eine aktive Aqua-Session
  komplett. Symptom im Log: Gateway-Prozess crasht sofort beim Start,
  ThrottleInterval-Restart-Loop. Fix: Auto-Login für novaadm aktivieren
  (siehe Voraussetzungen oben), Plist als LaunchAgent in
  `~/Library/LaunchAgents/` betreiben statt als system-Daemon.
- **Port 4001 nicht offen, Gateway-Prozess läuft trotzdem.** Meist
  Login-Probleme: falsche Credentials, 2FA aktiviert (IBC kann 2FA nicht
  bedienen — in der IBKR-Account-Verwaltung „Trusted IP" für nova-hub
  hinzufügen und 2FA fürs API-Login deaktivieren), oder Read-Only-Login
  hängt am Dialog (`ReadOnlyLogin=no` im Template prüfen).
- **Tägliche Disconnects gegen Mitternacht ET.** Erwartet — IBKR
  zwingt jeden Tag einen Neulogin. IBC handhabt das via
  `AutoLogoffAction=restart`. Falls Gateway nicht wieder hochkommt,
  prüfe ob `ThrottleInterval` zu lang ist (60 s ok) oder
  ob IBC einen Dialog nicht erkennt — dann IBC updaten.
- **„IB Gateway not found at …"** im Wrapper-Log: `NOVA_IB_GATEWAY_PATH`
  und `NOVA_IB_GATEWAY_VER` in `~/.nova_env` müssen zur tatsächlich
  installierten Version passen. Nach einem IBKR-Update der App den
  Versionsstring anpassen.
- **2FA-Pflicht.** IBC kann keine 2FA-Tokens. Lösung über IBKRs Optionen
  in *Account Management*:
    - „IB Key" Mobile-Auth ausschalten oder
    - „Trusted IPs" für nova-hub eintragen → keine 2FA-Abfrage mehr.

## Sicherheits-Hinweise

- `~/.nova_env` enthält Klartext-Credentials. `chmod 600` ist Pflicht.
- Das Repo selbst hat keine Credentials — nur Platzhalter im
  `config/ibc_config.ini.template`.
- Beim Start rendert der Wrapper eine Runtime-Config in
  `/tmp/nova-ibc-runtime.ini` mit `chmod 600`. Sie bleibt liegen, bis der
  nächste Start sie überschreibt — bei Bedarf manuell löschen.
- Der system-Daemon läuft als `novaadm` (via `UserName`-Key im plist),
  nicht als root — Prozess-Berechtigungen sind so eng wie möglich.

## Bezug zu anderen Modulen

Die folgenden nova-Module setzen voraus, dass Gateway erreichbar ist:

- `modules.fundamentals` (ib_adapter — IB primär, yfinance Fallback)
- `modules.ingest` (Portfolio/Trade-Snapshot via IB-Account)
- `modules.screener_csp` (Options-Daten via IB)
- `modules.portfolio._ib_resolver` (Instrument-Resolver)

Diese Module rufen vorab `scripts/check_ib_gateway.sh` auf; schlägt der
TCP-Precheck fehl, brechen die jeweiligen Daemons mit klarem Log-Eintrag
ab statt eine halbleere IB-Session zu verarbeiten.
