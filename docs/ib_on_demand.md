# IB Gateway — On-Demand-Betrieb

**Ziel:** Der IBKR-Account soll nicht permanent vom nova-Gateway belegt sein,
damit du jederzeit direkt in TWS / IB-Mobile handeln kannst. IBKR erlaubt pro
Username nur **eine** aktive Sitzung — darum laeuft das Gateway jetzt nur noch
**bei Bedarf** statt 24/7.

## Was sich geaendert hat

- **LaunchAgent** (`dotfiles/launchd/de.gershu.nova.lab.ib.gateway.plist`):
  `RunAtLoad=false`, `KeepAlive=false` — startet nicht mehr automatisch und
  wird nach dem Stop nicht neu hochgefahren.
- **IBC** (`config/ibc_config.ini.template`):
  `ExistingSessionDetectedAction=secondary` — falls Gateway und dein manueller
  Login kollidieren, **weicht das Gateway** (human-first).
- **Health** (`config/daemons.yaml`): `lab.ib.gateway` ist `On-Demand`, kein
  `port_check` mehr — „aus" ist kein Fehler mehr im Dashboard.

## Einrichtung (einmalig, als der GUI-User stefan_mac)

```bash
cp ~/nova/dotfiles/launchd/de.gershu.nova.lab.ib.gateway.plist \
   ~/Library/LaunchAgents/
launchctl bootout   gui/$(id -u)/de.gershu.nova.lab.ib.gateway 2>/dev/null
launchctl bootstrap gui/$(id -u) \
   ~/Library/LaunchAgents/de.gershu.nova.lab.ib.gateway.plist
```

## Bedienung

```bash
scripts/ib_gateway_ctl.sh start     # Gateway hochfahren (+ auf Port warten)
scripts/ib_gateway_ctl.sh stop      # Gateway beenden -> Account frei
scripts/ib_gateway_ctl.sh status    # up / down + HOLD-Status
scripts/ib_gateway_ctl.sh pause     # HOLD setzen + stoppen (manuell handeln)
scripts/ib_gateway_ctl.sh resume    # HOLD weg + starten
scripts/ib_gateway_ctl.sh with -- <cmd…>   # hoch, cmd, runter
```

**HOLD:** Solange `~/.nova_ib_hold` existiert, verweigert `start` den Start.
So kann ein geplanter Job das Gateway nicht hochfahren, waehrend du manuell in
IB eingeloggt bist. `pause` setzt den HOLD, `resume` entfernt ihn.

## In Jobs (Python)

```python
from modules.broker.ib_session import ib_gateway_session, IBHeldError

try:
    with ib_gateway_session():
        ...  # IB-Arbeit; Gateway wird danach automatisch gestoppt
except IBHeldError:
    ...  # du handelst gerade manuell -> Job ueberspringen
```

Geplante IB-Jobs immer in `ib_gateway_session()` kapseln — dann ist der Account
nur fuer die Dauer des Jobs belegt und der HOLD wird respektiert.
