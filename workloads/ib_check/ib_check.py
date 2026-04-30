"""IB API Connection Diagnostic.

Layered probe — jede Schicht einzeln, klar sichtbar wo's klemmt:
  1. Hostname-Resolution (DNS/mDNS)
  2. TCP-Connect auf HOST:PORT (raw socket — Firewall/Bind-Check)
  3. IB API-Handshake via ib_async (mit errorEvent-Capture)
  4. Probe-Calls: reqCurrentTime, reqMarketDataType

3-Tier Konfiguration (spaeter ueberschreibt frueher):
  Tier 1 — Defaults im File (siehe Konstanten unten)
  Tier 2 — Env-Vars aus ~/.nova_env:
             IB_GATEWAY_HOST, IB_GATEWAY_PORT, IB_CLIENT_ID,
             IB_MARKET_DATA_TYPE, IB_REQUEST_TIMEOUT
  Tier 3 — JSON via NOVA_PARAMS_FILE (Felder lower-case ohne IB_-Prefix):
             {"host": ..., "port": ..., "client_id": ...,
              "market_data_type": ..., "request_timeout": ...}

Aufruf:
  Lokal:    ~/nova/workloads/ib_check/run.sh
  Remote:   ssh nova-w1 '~/nova/workloads/ib_check/run.sh'
  Sub:      nova_submit.sh ib_check nova-w1 --params-file <override.json>
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import sys
import time
import traceback
from datetime import datetime, timezone

# ============================================================
# Tier 1 — Defaults
# ============================================================
HOST = "nova-hub.local"
PORT = 4001
CLIENT_ID = 7
MARKET_DATA_TYPE = 2
REQUEST_TIMEOUT = 15

# ============================================================
# Tier 2 — Env-Var Overrides (~/.nova_env)
# ============================================================
HOST = os.environ.get("IB_GATEWAY_HOST", HOST)
PORT = int(os.environ.get("IB_GATEWAY_PORT", PORT))
CLIENT_ID = int(os.environ.get("IB_CLIENT_ID", CLIENT_ID))
MARKET_DATA_TYPE = int(os.environ.get("IB_MARKET_DATA_TYPE", MARKET_DATA_TYPE))
REQUEST_TIMEOUT = int(os.environ.get("IB_REQUEST_TIMEOUT", REQUEST_TIMEOUT))

# ============================================================
# Tier 3 — JSON Params via NOVA_PARAMS_FILE
# ============================================================
_pf = os.environ.get("NOVA_PARAMS_FILE")
if _pf:
    _p = pathlib.Path(_pf)
    if _p.is_file():
        try:
            _params = json.loads(_p.read_text())
            HOST = _params.get("host", HOST)
            PORT = int(_params.get("port", PORT))
            CLIENT_ID = int(_params.get("client_id", CLIENT_ID))
            MARKET_DATA_TYPE = int(_params.get("market_data_type", MARKET_DATA_TYPE))
            REQUEST_TIMEOUT = int(_params.get("request_timeout", REQUEST_TIMEOUT))
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[WARN] NOVA_PARAMS_FILE konnte nicht geparst werden: {e}", file=sys.stderr)


# ============================================================
# Helper
# ============================================================

def section(title: str) -> None:
    print()
    print("=" * 64)
    print(title)
    print("=" * 64)


# ============================================================
# Schritt 1 — Hostname-Resolution
# ============================================================

def step1_resolve() -> list[str] | None:
    section("1. Hostname-Resolution")
    print(f"   HOST = {HOST}")
    try:
        infos = socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM)
        addrs = sorted({info[4][0] for info in infos})
        for a in addrs:
            print(f"   resolved to: {a}")
        return addrs
    except socket.gaierror as e:
        print(f"   FEHLER: gaierror {e}")
        print(f"           -> mDNS/DNS-Setup pruefen, Hostname tippfehler?")
        return None


# ============================================================
# Schritt 2 — TCP-Connect (Layer 4, vor IB-Handshake)
# ============================================================

def step2_tcp(addrs: list[str] | None) -> bool:
    section("2. TCP-Connect (raw socket)")
    if not addrs:
        print("   (uebersprungen — keine resolvierte Adresse)")
        return False

    success = False
    for addr in addrs:
        family = socket.AF_INET6 if ":" in addr else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(REQUEST_TIMEOUT)
        print(f"   probiere {addr}:{PORT} (timeout {REQUEST_TIMEOUT}s)...")
        try:
            t0 = time.monotonic()
            sock.connect((addr, PORT))
            elapsed_ms = (time.monotonic() - t0) * 1000
            print(f"   OK: connected in {elapsed_ms:.0f} ms")
            success = True
        except socket.timeout:
            print(f"   FEHLER: timeout nach {REQUEST_TIMEOUT}s")
            print(f"           -> Port {PORT} wahrscheinlich gefiltert (Firewall) oder")
            print(f"              TWS bindet nur auf 127.0.0.1 (nicht auf 0.0.0.0)")
        except ConnectionRefusedError as e:
            print(f"   FEHLER: connection refused ({e})")
            print(f"           -> nichts hoert auf {PORT}, oder bind-only-localhost")
        except OSError as e:
            print(f"   FEHLER: {e.__class__.__name__}: {e}")
        finally:
            try:
                sock.close()
            except OSError:
                pass

    return success


# ============================================================
# Schritt 3 — IB API Handshake
# ============================================================

def step3_ib_connect():
    section("3. IB API Handshake (ib_async)")
    try:
        from ib_async import IB
    except ImportError as e:
        print(f"   FEHLER: ib_async-Import: {e}")
        print(f"           -> requirements-lock.txt sollte ib_async listen,")
        print(f"              node_deploy.sh fehlgeschlagen?")
        return None

    ib = IB()
    captured_errors: list[tuple] = []

    def on_error(reqId, errorCode, errorString, contract):
        captured_errors.append((reqId, errorCode, errorString))
        # IB sendet auch INFO-Messages via errorEvent (Codes 2104, 2106, 2158 = Market data farm OK).
        # Wir markieren die Schwere grob:
        severity = "INFO" if errorCode in (2103, 2104, 2105, 2106, 2107, 2108, 2158, 2168, 2169) else "ERR "
        print(f"   [{severity}] reqId={reqId} code={errorCode} msg={errorString!r}")

    ib.errorEvent += on_error

    print(f"   Verbinde: {HOST}:{PORT} clientId={CLIENT_ID} timeout={REQUEST_TIMEOUT}s")
    try:
        t0 = time.monotonic()
        ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=REQUEST_TIMEOUT)
        elapsed_ms = (time.monotonic() - t0) * 1000
        print(f"   OK: connected in {elapsed_ms:.0f} ms")
        try:
            print(f"      Server-Version  : {ib.client.serverVersion()}")
            print(f"      TWS-Connect-Time: {ib.client.twsConnectionTime()!r}")
        except Exception as e:  # noqa: BLE001
            print(f"      [WARN] Server-Info nicht abrufbar: {e}")
        return ib
    except Exception as e:  # noqa: BLE001
        print(f"   FEHLER: {e.__class__.__name__}: {e}")
        traceback.print_exc()
        if captured_errors:
            print(f"   ({len(captured_errors)} errorEvent-Messages oben)")
        else:
            print(f"   (keine errorEvent-Messages — TWS hat den Handshake nicht geantwortet)")
        return None


# ============================================================
# Schritt 4 — Probe-Calls (nur wenn connected)
# ============================================================

def step4_probe(ib) -> None:
    section("4. Probe-Calls")
    if ib is None or not ib.isConnected():
        print("   (uebersprungen — keine IB-Verbindung)")
        return

    try:
        ct = ib.reqCurrentTime()
        print(f"   reqCurrentTime          : {ct}")
    except Exception as e:  # noqa: BLE001
        print(f"   reqCurrentTime FEHLER   : {e.__class__.__name__}: {e}")

    try:
        ib.reqMarketDataType(MARKET_DATA_TYPE)
        # reqMarketDataType ist fire-and-forget; bei Erfolg keine Exception.
        # Eventuell eingehende errorEvents wurden oben schon geprintet.
        print(f"   reqMarketDataType({MARKET_DATA_TYPE})    : OK (kein direkter Return)")
    except Exception as e:  # noqa: BLE001
        print(f"   reqMarketDataType FEHLER: {e.__class__.__name__}: {e}")


def step5_disconnect(ib) -> None:
    if ib is not None and ib.isConnected():
        ib.disconnect()
        print("   IB.disconnect() OK")


# ============================================================
# Hinweise wenn TCP-Connect failed
# ============================================================

TCP_HINTS = """
Wahrscheinliche Ursachen wenn TCP-Connect fehlschlaegt:

  a) TWS/IB-Gateway bindet nur auf localhost.
     -> Configuration > API > Settings:
        "Allow connections from localhost only" muss DEAKTIVIERT sein.

  b) Trusted IP Addresses fehlen.
     -> Configuration > API > Settings > "Trusted IP Addresses":
        IP des Workers eintragen (z.B. 192.168.2.60 fuer nova-w1).

  c) macOS-Firewall auf nova-hub blockt eingehende Verbindungen.
     -> System Settings > Network > Firewall: deaktivieren oder
        Ausnahme fuer TWS/Gateway.

  d) "Read-Only API" einschraenken zu strikt.
     -> Configuration > API > Settings: ggf. Setting pruefen.
"""

IB_HINTS = """
Wahrscheinliche Ursachen wenn TCP ok aber IB-Handshake fehlschlaegt:

  a) Client-ID-Konflikt (selber clientId schon mit anderer Session offen).
     -> Anderen CLIENT_ID setzen (Tier 2 oder 3), z.B. IB_CLIENT_ID=8.

  b) Master Client ID vom TWS gesperrt.
     -> Configuration > API > Settings > "Master API client ID" pruefen.

  c) errorEvent code 502 = "Couldn't connect to TWS".
  d) errorEvent code 504 = "Not connected".
  e) errorEvent code 326 = "Unable to connect as client id is already in use".
"""


# ============================================================
# Main
# ============================================================

def main() -> int:
    print("==> IB Connection Check")
    print(f"    timestamp UTC : {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"    hostname      : {socket.gethostname()}")
    print(f"    NOVA_ROLE     : {os.environ.get('NOVA_ROLE', '(unset)')}")
    print()
    print("    Konfiguration (effektiv nach Tier-1/2/3-Aufloesung):")
    print(f"      HOST              = {HOST}")
    print(f"      PORT              = {PORT}")
    print(f"      CLIENT_ID         = {CLIENT_ID}")
    print(f"      MARKET_DATA_TYPE  = {MARKET_DATA_TYPE}")
    print(f"      REQUEST_TIMEOUT   = {REQUEST_TIMEOUT}s")

    addrs = step1_resolve()
    tcp_ok = step2_tcp(addrs)

    if not tcp_ok:
        print(TCP_HINTS)
        print("==> ABORT: TCP-Connect failed, kein Sinn weiter zu pruefen.")
        return 1

    ib = step3_ib_connect()
    step4_probe(ib)
    step5_disconnect(ib)

    print()
    if ib is not None:
        print("==> Verbindung erfolgreich.")
        return 0

    print(IB_HINTS)
    print("==> IB-Handshake fehlgeschlagen — Hinweise oben.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
