"""nova-lab obsidian CLI — exportiert DB-Inhalte als Markdown-Vault.

Subcommands:
    publish              Full Vault Export (default-CLI use)
    clean                Loescht alte Daily-/Pick-Files (Default: >90 Tage)
    show-vault-path      Zeigt Output-Pfad (Default oder via OBSIDIAN_VAULT_PATH)

ENV:
    OBSIDIAN_VAULT_PATH  Override fuer Output-Pfad
                         Default: ~/nova_output/obsidian/

Auf stefan_mac wird per rsync von dort in den lokalen Obsidian-Vault gezogen.

Beispiele:
    python -m modules.obsidian publish
    python -m modules.obsidian show-vault-path
    python -m modules.obsidian clean --keep-days 60
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from datetime import date, timedelta

import duckdb

from .publisher import _vault_path, publish_all


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)


# ---------- publish ----------

def cmd_publish(args) -> int:
    vault = pathlib.Path(args.vault_path) if args.vault_path else _vault_path()
    vault.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        print(f"==> Obsidian publish")
        print(f"    DB:    {DB_PATH}")
        print(f"    Vault: {vault}")
        print()
        stats = publish_all(con, vault)
        print(f"==> {stats.files_written} files written:")
        for s in stats.sections:
            print(f"    - {s}")
        return 0
    finally:
        con.close()


# ---------- clean ----------

def cmd_clean(args) -> int:
    vault = pathlib.Path(args.vault_path) if args.vault_path else _vault_path()
    if not vault.is_dir():
        print(f"Vault {vault} existiert nicht — nichts zu loeschen.")
        return 0

    cutoff = date.today() - timedelta(days=args.keep_days)
    n_deleted = 0
    # Daily-Files: Filename ist YYYY-MM-DD.md
    for sub in ("daily", "csp", "value"):
        d = vault / sub
        if not d.is_dir():
            continue
        for p in d.glob("*.md"):
            # Datum aus Filename ableiten (faengt mit YYYY-MM-DD an)
            stem = p.stem
            iso = stem[:10]
            try:
                file_date = date.fromisoformat(iso)
            except ValueError:
                continue   # kein ISO-prefix -> behalten
            if file_date < cutoff:
                p.unlink()
                n_deleted += 1
                print(f"    deleted: {p.relative_to(vault)}")

    print(f"==> {n_deleted} files older than {cutoff} entfernt.")
    return 0


# ---------- show-vault-path ----------

def cmd_show_vault_path(args) -> int:
    print(_vault_path())
    return 0


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("publish", help="Full Vault Export")
    pp.add_argument("--vault-path", default=None,
                     help="Override Output-Pfad (sonst OBSIDIAN_VAULT_PATH oder ~/nova_output/obsidian/)")

    pc = sub.add_parser("clean", help="Loescht alte Daily-/Pick-Files")
    pc.add_argument("--keep-days", type=int, default=90)
    pc.add_argument("--vault-path", default=None)

    sub.add_parser("show-vault-path", help="Aktueller Output-Pfad")

    args = p.parse_args()

    if args.cmd != "show-vault-path" and not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64

    dispatch = {
        "publish":         cmd_publish,
        "clean":           cmd_clean,
        "show-vault-path": cmd_show_vault_path,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
