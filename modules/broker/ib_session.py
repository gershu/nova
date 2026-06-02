"""On-Demand-IB-Gateway-Session fuer Jobs, die kurzzeitig IB brauchen.

Das Gateway laeuft nicht mehr permanent (Account bleibt frei fuer direkte
TWS-Nutzung). Jobs fahren es nur fuer ihre Dauer hoch:

    from modules.broker.ib_session import ib_gateway_session, IBHeldError

    try:
        with ib_gateway_session():
            ib = IB()
            ib.connect("127.0.0.1", 4001, clientId=7)
            ...   # IB-Arbeit
        # danach wird das Gateway automatisch gestoppt (Account frei),
        # sofern DIESER Block es gestartet hat.
    except IBHeldError:
        ...   # du bist gerade manuell in IB -> Job ueberspringen

Steuerung dahinter: scripts/ib_gateway_ctl.sh.
"""

from __future__ import annotations

import contextlib
import pathlib
import subprocess

_CTL = (pathlib.Path(__file__).resolve().parents[2]
        / "scripts" / "ib_gateway_ctl.sh")


class IBHeldError(RuntimeError):
    """IB-HOLD aktiv — manuelle IB-Nutzung; Gateway-Start unterbleibt."""


def _ctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run([str(_CTL), *args], capture_output=True, text=True)


@contextlib.contextmanager
def ib_gateway_session(timeout: int = 120):
    """Stellt sicher, dass das Gateway laeuft; stoppt es danach wieder,
    wenn dieser Block es gestartet hat. Raises IBHeldError bei HOLD."""
    started = False
    st = _ctl("status")
    if "up" not in (st.stdout or ""):
        r = _ctl("start", str(int(timeout)))
        if r.returncode == 3:
            raise IBHeldError(
                "IB-HOLD aktiv (manuelle IB-Nutzung) — Job uebersprungen.")
        if r.returncode != 0:
            raise RuntimeError(
                f"IB-Gateway-Start fehlgeschlagen: "
                f"{(r.stderr or r.stdout or '').strip()}")
        started = True
    try:
        yield
    finally:
        if started:
            _ctl("stop")
