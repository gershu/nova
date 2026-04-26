"""Hello World — Smoke-Test fuer das nova-Workload-Setup.

Zeigt:
  - dass Python im erwarteten Cluster-venv laeuft,
  - auf welchem Node der Job ausgefuehrt wird (NOVA_ROLE / hostname),
  - ob der requirements.txt-Stack tatsaechlich importierbar ist.
"""

from __future__ import annotations

import os
import platform
import socket
import sys
from datetime import datetime, timezone


def stack_check() -> str:
    """Versuch, die wichtigsten requirements zu importieren — meldet was geht."""
    results: list[str] = []
    for pkg in ("numpy", "pandas", "duckdb", "yaml"):
        try:
            mod = __import__(pkg)
            version = getattr(mod, "__version__", "?")
            results.append(f"{pkg}={version}")
        except ImportError:
            results.append(f"{pkg}=MISSING")
    return ", ".join(results)


def main() -> int:
    print("==> nova workload: hello_world")
    print(f"    timestamp UTC : {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"    hostname      : {socket.gethostname()}")
    print(f"    NOVA_ROLE     : {os.environ.get('NOVA_ROLE', '(unset)')}")
    print(f"    python        : {platform.python_version()} ({sys.executable})")
    print(f"    stack         : {stack_check()}")
    print("==> done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
