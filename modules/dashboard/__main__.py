"""nova-lab dashboard entrypoint — startet Streamlit-App.

Aufruf-Convention:
    python -m modules.dashboard [--port 8501] [--bind 0.0.0.0]

Default: bind auf 0.0.0.0 fuer Tailscale-Erreichbarkeit. Bei lokal-only:
--bind 127.0.0.1.

Eigentlicher App-Code in app.py + views/*; Navigation via st.navigation.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys


APP_PATH = pathlib.Path(__file__).parent / "app.py"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8501)
    p.add_argument("--bind", default="0.0.0.0",
                     help="Default 0.0.0.0 fuer Tailscale; '127.0.0.1' fuer lokal-only.")
    args = p.parse_args()

    if not APP_PATH.is_file():
        print(f"FEHLER: {APP_PATH} fehlt.", file=sys.stderr)
        return 64

    # Streamlit via 'python -m streamlit' starten — vermeidet PATH-Lookup
    # nach der 'streamlit'-Binary (die je nach Setup im venv/bin liegt aber
    # nicht im laufenden PATH ist).
    try:
        import streamlit                          # noqa: F401  Sanity-check
    except ImportError:
        print("FEHLER: streamlit nicht installiert in dieser Python-Umgebung.",
              file=sys.stderr)
        print(f"        Python: {sys.executable}", file=sys.stderr)
        print(f"        Fix:    pip install streamlit plotly  oder", file=sys.stderr)
        print(f"                ~/nova/scripts/node_deploy.sh  (installiert aus requirements.txt)", file=sys.stderr)
        return 64

    os.execv(sys.executable, [
        sys.executable, "-m", "streamlit", "run", str(APP_PATH),
        "--server.port", str(args.port),
        "--server.address", args.bind,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
