"""nova-lab screener CLI — Quality-GARP-Screening.

Aktueller Stand (Phase B0): nur 'init'. Stufe 1/2/3 folgen in Phase C.

Subcommands:
    init [--universe-yaml PATH]
        Lese config/screener_quality_universe.yaml, lege die Namen in
        ref_instruments an und in die Watchlist 'quality_universe'.
        Idempotent — existierende Eintraege bleiben unangetastet.

Environment:
    LAB_DB_PATH      optional — default ~/nova_data/lab.duckdb

Beispiele:
    python -m modules.screener init
    python -m modules.screener init --universe-yaml /pfad/zu/eigener.yaml
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

import duckdb

from modules.screener_value.universe import (
    UniverseMember, load_universe, ref_instrument_id_for,
)
from . import UNIVERSE_WATCHLIST


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
DEFAULT_YAML = (pathlib.Path(__file__).parent.parent.parent
                / "config" / "screener_quality_universe.yaml")
ADDED_BY_TAG = "screener.init"


# ---------- init ----------

def cmd_init(args) -> int:
    """Quality-Universe-YAML -> ref_instruments + Watchlist."""
    yaml_path = pathlib.Path(args.universe_yaml) if args.universe_yaml \
        else DEFAULT_YAML
    members = load_universe(yaml_path)
    print(f"==> Universe geladen: {len(members)} Symbole aus {yaml_path.name}")

    con = duckdb.connect(str(DB_PATH))
    try:
        # 1. Watchlist anlegen (idempotent)
        con.execute("""
            INSERT INTO list_watchlists (watchlist_id, name, description, origin)
            VALUES (?, ?, ?, 'system')
            ON CONFLICT (watchlist_id) DO NOTHING
        """, [UNIVERSE_WATCHLIST,
              "Quality-Universe (Screener)",
              "Kuratierte ~100 Qualitaets-Compounder aus "
              "config/screener_quality_universe.yaml."])

        # 2. ref_instruments + watchlist_members je Member
        n_new_inst, n_new_member, n_skipped = 0, 0, 0
        for m in members:
            rid = ref_instrument_id_for(m)
            existed = con.execute(
                "SELECT 1 FROM ref_instruments WHERE ref_instrument_id = ?",
                [rid],
            ).fetchone()
            if not existed:
                con.execute("""
                    INSERT INTO ref_instruments
                        (ref_instrument_id, symbol, currency, name, asset_type,
                         preferred_source, exchange, active, notes)
                    VALUES (?, ?, ?, ?, 'stock', 'yfinance',
                            'NASDAQ/NYSE', TRUE, ?)
                """, [rid, m.symbol, m.currency, m.name,
                      f"auto-added from {UNIVERSE_WATCHLIST}; "
                      f"sector={m.sector or '-'}"])
                n_new_inst += 1
            else:
                n_skipped += 1

            existed_m = con.execute("""
                SELECT 1 FROM list_watchlist_members
                WHERE watchlist_id = ? AND ref_instrument_id = ?
            """, [UNIVERSE_WATCHLIST, rid]).fetchone()
            if not existed_m:
                con.execute("""
                    INSERT INTO list_watchlist_members
                        (watchlist_id, ref_instrument_id, added_by, notes)
                    VALUES (?, ?, ?, ?)
                """, [UNIVERSE_WATCHLIST, rid, ADDED_BY_TAG,
                      f"sector={m.sector or '-'}; name={m.name}"])
                n_new_member += 1

        print(f"    ref_instruments     : +{n_new_inst} neu, "
              f"{n_skipped} schon vorhanden")
        print(f"    watchlist members   : +{n_new_member} neu "
              f"(in {UNIVERSE_WATCHLIST})")
        print()
        print("==> Next steps:")
        print("    1. python -m modules.fundamentals refresh-all "
              "(Fundamentals-Snapshot fuer neue Namen)")
        print(f"    2. python -m modules.sec_filings backfill-all "
              f"--watchlist {UNIVERSE_WATCHLIST} --quarters 20")
        print("       (GuV-Historie fuer das Universum)")
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init",
        help="Quality-Universum-YAML -> ref_instruments + Watchlist")
    p_init.add_argument("--universe-yaml", default=None,
        help=f"YAML-Pfad (default: {DEFAULT_YAML.name})")

    args = p.parse_args()
    if args.cmd != "init" and not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}. 'init' zuerst.",
              file=sys.stderr)
        return 64

    dispatch = {"init": cmd_init}
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
