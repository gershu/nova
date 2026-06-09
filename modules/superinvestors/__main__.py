"""nova-lab Superinvestoren-Ingest.

Holt je konfiguriertem Filer (config/superinvestors.yaml) das neueste
13F-Portfolio + die Quartalsveraenderung ggue. Vorperiode und schreibt sie
nach ref_superinvestor_holdings / ref_superinvestor_changes.

Die (langsamen) sec-api-Calls laufen lock-frei; nur die Upserts laufen kurz
unter dem DuckDB-Schreib-Lock (modules.common.dblock).

Subcommands:
    ingest [--limit N] [--filings K]   Filer einlesen (K Filings je Manager,
                                       Default 2 = neueste + Vorperiode).
    show   [--limit N]                 letzte Changes anzeigen (read-only).

Beispiel:
    python -m modules.superinvestors ingest
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime, timezone

import duckdb
import yaml

from modules.common import dblock
from modules.sec_filings import client as sec

SQL_DIR = pathlib.Path(__file__).parent / "sql"
CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" \
    / "superinvestors.yaml"

_ADD_TRIM_EPS = 0.02   # +-2% Aktienzahl -> ADD/TRIM, sonst HOLD


def _apply_schema(con) -> None:
    for f in sorted(SQL_DIR.glob("0*.sql")):
        con.execute(f.read_text())


def _load_managers() -> list[dict]:
    if not CONFIG.is_file():
        return []
    data = yaml.safe_load(CONFIG.read_text()) or {}
    out = []
    for m in data.get("managers", []) or []:
        if m.get("cik") or m.get("name"):
            out.append({"cik": m.get("cik"), "name": m.get("name")})
    return out


def _key(h: dict):
    """Positions-Schluessel: CUSIP (sonst Ticker) + put_call."""
    return (h.get("cusip") or h.get("ticker") or "", h.get("put_call") or "")


def compute_changes(new_holdings: list[dict],
                    old_holdings: list[dict]) -> list[dict]:
    """QoQ-Deltas: NEW/ADD/TRIM/EXIT je Position. HOLD wird verworfen."""
    new_map = {_key(h): h for h in new_holdings}
    old_map = {_key(h): h for h in old_holdings}
    out = []
    for k, h in new_map.items():
        o = old_map.get(k)
        sh_n, v_n = h.get("shares"), h.get("value")
        if o is None:
            ct = "NEW"
        else:
            sh_o = o.get("shares")
            if sh_n is not None and sh_o not in (None, 0):
                d = sh_n / sh_o - 1.0
                ct = "ADD" if d > _ADD_TRIM_EPS else \
                     "TRIM" if d < -_ADD_TRIM_EPS else "HOLD"
            else:
                ct = "HOLD"
        if ct == "HOLD":
            continue
        out.append({**h, "change_type": ct, "value_new": v_n,
                    "value_old": (o or {}).get("value"),
                    "shares_new": sh_n, "shares_old": (o or {}).get("shares")})
    for k, o in old_map.items():
        if k not in new_map:
            out.append({**o, "change_type": "EXIT",
                        "value_new": None, "value_old": o.get("value"),
                        "shares_new": None, "shares_old": o.get("shares")})
    return out


def _persist(con, mid: str, mname: str, newest: dict, changes: list[dict],
             prior_period) -> None:
    now = datetime.now(timezone.utc)
    period = newest.get("period")
    filed = newest.get("filed_at")
    con.execute("DELETE FROM ref_superinvestor_holdings "
                "WHERE manager_cik=? AND period=?", [mid, period])
    for h in newest.get("holdings", []):
        con.execute(
            "INSERT INTO ref_superinvestor_holdings (manager_cik, manager_name,"
            " period, filed_at, ticker, cusip, name, value, shares, put_call, "
            "ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [mid, mname, period, filed, h.get("ticker"), h.get("cusip"),
             h.get("name"), h.get("value"), h.get("shares"),
             h.get("put_call") or "", now])
    con.execute("DELETE FROM ref_superinvestor_changes "
                "WHERE manager_cik=? AND period=?", [mid, period])
    for c in changes:
        con.execute(
            "INSERT INTO ref_superinvestor_changes (manager_cik, manager_name,"
            " period, prior_period, ticker, cusip, name, put_call, "
            "change_type, value_new, value_old, shares_new, shares_old, "
            "computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [mid, mname, period, prior_period, c.get("ticker"), c.get("cusip"),
             c.get("name"), c.get("put_call") or "", c["change_type"],
             c.get("value_new"), c.get("value_old"), c.get("shares_new"),
             c.get("shares_old"), now])


def cmd_ingest(args) -> int:
    managers = _load_managers()
    if not managers:
        print(f"Keine Manager in {CONFIG}.", file=sys.stderr)
        return 2
    if args.limit:
        managers = managers[:args.limit]
    with dblock.rw_connection() as con:
        _apply_schema(con)

    n_ok = n_err = 0
    for m in managers:
        label = m.get("name") or m.get("cik")
        try:  # sec-api: lock-frei
            res = sec.fetch_manager_13f(cik=m.get("cik"), name=m.get("name"),
                                        n_filings=max(2, args.filings))
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {label}: {e.__class__.__name__}: {e}", file=sys.stderr)
            n_err += 1
            continue
        filings = res.get("filings") or []
        if not filings:
            print(f"  ✗ {label}: {res.get('error')}", file=sys.stderr)
            n_err += 1
            continue
        newest = filings[0]
        older = filings[1] if len(filings) > 1 else None
        changes = compute_changes(newest.get("holdings", []),
                                  (older or {}).get("holdings", []))
        mid = str(m.get("cik") or newest.get("cik") or label)
        with dblock.rw_connection() as con:
            _persist(con, mid, m.get("name") or newest.get("manager"),
                     newest, changes, (older or {}).get("period"))
        n_new = sum(1 for c in changes if c["change_type"] == "NEW")
        n_exit = sum(1 for c in changes if c["change_type"] == "EXIT")
        print(f"  ✓ {label}: {newest.get('period')} — "
              f"{len(newest.get('holdings', []))} Positionen, "
              f"{n_new} neu / {n_exit} raus")
        n_ok += 1
    print(f"Superinvestoren: {n_ok} ok, {n_err} Fehler.")
    return 0


def cmd_show(args) -> int:
    con = duckdb.connect(dblock.db_path(), read_only=True)
    try:
        if not con.execute("SELECT 1 FROM information_schema.tables WHERE "
                           "table_name='ref_superinvestor_changes'").fetchone():
            print("Noch kein Ingest gelaufen.")
            return 0
        rows = con.execute(
            "SELECT manager_name, period, change_type, ticker, name, value_new "
            "FROM ref_superinvestor_changes ORDER BY period DESC, "
            "change_type, value_new DESC NULLS LAST LIMIT ?",
            [args.limit]).fetchall()
    finally:
        con.close()
    for mn, per, ct, tk, nm, v in rows:
        print(f"  {per}  {ct:<5}{(tk or '')[:6]:<7}{(mn or '')[:22]:<24}"
              f"{(nm or '')[:24]}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m modules.superinvestors")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("ingest", help="13F-Filer einlesen")
    pi.add_argument("--limit", type=int, default=0)
    pi.add_argument("--filings", type=int, default=2)
    pi.set_defaults(func=cmd_ingest)
    ps = sub.add_parser("show", help="letzte Changes")
    ps.add_argument("--limit", type=int, default=30)
    ps.set_defaults(func=cmd_show)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
