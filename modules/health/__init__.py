"""nova-lab health — Daemon-Status-Reader + CLI + Daily-Daemon.

Liest config/daemons.yaml als Quelle der Wahrheit, prueft je Daemon den
passenden Status (Audit-Tabelle, primary_table, Prozess/Port, Log-Mtime)
und liefert eine flache Liste DaemonStatus-Records. Davon abgeleitet:

  - python -m modules.health status   -> Tabelle in stdout (CLI/SSH)
  - python -m modules.health run      -> Snapshot in sig_health_snapshots
                                          (Daily-Daemon)
  - Dashboard-Page views/health.py    -> visuelle Ansicht
"""
