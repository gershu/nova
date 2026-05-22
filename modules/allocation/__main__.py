"""nova — allocation CLI — Portfolio-Allokations-Monitoring.

Stellt die Ist-Allokation (v_mkt_holdings, je Klasse aggregiert) gegen die
Ziel-Baender aus config/allocation.yaml und schreibt Drift + Band-Status
nach sig_allocation. Die Instrument->Klasse-Zuordnung kommt aus
config/instrument_classes.yaml.

Subcommands:
    init    Schema applyen (sig_allocation)
    run     Allokation auswerten + nach sig_allocation schreiben
    eval    Dry-run — Auswertung anzeigen, kein Write
    show    Letzte Allokations-Auswertung (latest ts) — read-only

Beispiele:
    python -m modules.allocation init
    python -m modules.allocation run
    python -m modules.allocation eval     # Diagnose ohne Write
    python -m modules.allocation show
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import uuid
from datetime import date, datetime, timezone

import duckdb
import yaml


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)
REPO_DIR        = pathlib.Path(__file__).parent.parent.parent
SQL_DIR         = pathlib.Path(__file__).parent / "sql"
ALLOCATION_FILE = REPO_DIR / "config" / "allocation.yaml"
CLASSES_FILE    = REPO_DIR / "config" / "instrument_classes.yaml"


class AllocationError(RuntimeError):
    """Fachlicher Fehler (fehlende Config, leeres Portfolio, ...)."""


# ---------- Config ----------

def load_targets() -> dict:
    """target_allocation aus allocation.yaml — {class: {label,target,min,max}}."""
    if not ALLOCATION_FILE.is_file():
        raise AllocationError(f"{ALLOCATION_FILE} nicht gefunden.")
    data = yaml.safe_load(ALLOCATION_FILE.read_text()) or {}
    targets = data.get("target_allocation", {})
    if not targets:
        raise AllocationError("allocation.yaml: target_allocation leer/fehlt.")
    return targets


def load_classification() -> dict[str, str]:
    """instrument_classes.yaml invertieren -> {ref_instrument_id: class}."""
    if not CLASSES_FILE.is_file():
        raise AllocationError(f"{CLASSES_FILE} nicht gefunden.")
    data = yaml.safe_load(CLASSES_FILE.read_text()) or {}
    grouped = data.get("classification", {})
    inv: dict[str, str] = {}
    for cls, ids in grouped.items():
        for rid in (ids or []):
            inv[rid] = cls
    return inv


def load_concentration() -> dict:
    data = yaml.safe_load(ALLOCATION_FILE.read_text()) or {}
    return data.get("concentration", {})


# ---------- Schema ----------

def apply_schema(con: duckdb.DuckDBPyConnection, *, verbose: bool = False) -> None:
    """Schema idempotent applyen — von cmd_init UND cmd_run aufgerufen."""
    for f in sorted(SQL_DIR.glob("0*.sql")):
        con.execute(f.read_text())
        if verbose:
            print(f"    ✓ {f.name}")


# ---------- Auswertung ----------

def compute(con: duckdb.DuckDBPyConnection) -> dict:
    """Ist-Allokation je Klasse + Drift gegen die Ziel-Baender."""
    targets = load_targets()
    cls_map = load_classification()

    # Klassen-Check: jede zugeordnete Klasse muss ein Ziel haben
    unknown = {c for c in cls_map.values()} - set(targets)
    if unknown:
        raise AllocationError(
            f"instrument_classes.yaml nennt Klassen ohne Ziel in "
            f"allocation.yaml: {', '.join(sorted(unknown))}")

    rows = con.execute("""
        SELECT ref_instrument_id, any_value(name) AS name, SUM(mtm_eur) AS eur
        FROM v_mkt_holdings
        WHERE mtm_eur IS NOT NULL
        GROUP BY ref_instrument_id
    """).fetchall()
    if not rows:
        raise AllocationError("Portfolio leer (v_mkt_holdings ohne mtm_eur).")

    total = sum(r[2] for r in rows) or 0.0
    by_class: dict[str, float] = {c: 0.0 for c in targets}
    unclassified: list[tuple] = []
    for rid, name, eur in rows:
        cls = cls_map.get(rid)
        if cls is None:
            unclassified.append((rid, name, eur))
        else:
            by_class[cls] += eur

    classes = []
    for cls, spec in targets.items():
        actual_eur = by_class.get(cls, 0.0)
        actual_pct = (actual_eur / total * 100.0) if total else 0.0
        tgt = spec.get("target_pct")
        lo  = spec.get("min_pct")
        hi  = spec.get("max_pct")
        if lo is not None and actual_pct < lo:
            status = "below"
        elif hi is not None and actual_pct > hi:
            status = "above"
        else:
            status = "within"
        classes.append({
            "asset_class": cls,
            "label":       spec.get("label", cls),
            "target_pct":  tgt,
            "min_pct":     lo,
            "max_pct":     hi,
            "actual_pct":  round(actual_pct, 2),
            "actual_eur":  round(actual_eur, 2),
            "drift_pct":   round(actual_pct - tgt, 2) if tgt is not None else None,
            "band_status": status,
        })

    uncl_eur = sum(e for _, _, e in unclassified)
    return {
        "total_eur":    total,
        "classes":      classes,
        "unclassified": {
            "actual_eur":  round(uncl_eur, 2),
            "actual_pct":  round(uncl_eur / total * 100.0, 2) if total else 0.0,
            "instruments": unclassified,
        },
        "concentration": _concentration(con, total),
    }


def _concentration(con: duckdb.DuckDBPyConnection, total: float) -> dict:
    """Top-Position + Top-5 (je Unternehmen, konsolidiert ueber Boersen)."""
    limits = load_concentration()
    rows = con.execute("""
        SELECT COALESCE(name, ref_instrument_id) AS who, SUM(mtm_eur) AS eur
        FROM v_mkt_holdings
        WHERE mtm_eur IS NOT NULL
        GROUP BY COALESCE(name, ref_instrument_id)
        ORDER BY eur DESC
    """).fetchall()
    top = [(who, eur, eur / total * 100.0 if total else 0.0) for who, eur in rows]
    top1 = top[0] if top else None
    top5_pct = sum(p for _, _, p in top[:5])
    max_single = limits.get("max_single_position_pct")
    max_top5   = limits.get("max_top5_pct")
    return {
        "top1":            top1,
        "top5_pct":        round(top5_pct, 2),
        "max_single_pct":  max_single,
        "max_top5_pct":    max_top5,
        "single_breach":   (top1 is not None and max_single is not None
                            and top1[2] > max_single),
        "top5_breach":     (max_top5 is not None and top5_pct > max_top5),
        "top5":            top[:5],
    }


# ---------- Ausgabe ----------

_STATUS_ICON = {"within": "🟢", "below": "🔴", "above": "🔴", "unclassified": "⚪"}


def _print_report(res: dict) -> None:
    total = res["total_eur"]
    print(f"    Portfolio: {total:,.0f} EUR")
    print()
    print(f"    {'Klasse':<26}{'Ist':>8}{'Ziel':>7}{'Band':>12}{'Drift':>8}  Status")
    print(f"    {'-'*26}{'-'*8}{'-'*7}{'-'*12}{'-'*8}  {'-'*8}")
    for c in res["classes"]:
        band = (f"{c['min_pct']:.0f}-{c['max_pct']:.0f}%"
                if c["min_pct"] is not None else "—")
        drift = f"{c['drift_pct']:+.1f}" if c["drift_pct"] is not None else "—"
        icon = _STATUS_ICON.get(c["band_status"], "·")
        print(f"    {c['label']:<26}{c['actual_pct']:>7.1f}%"
              f"{c['target_pct']:>6.0f}%{band:>12}{drift:>8}  {icon} {c['band_status']}")
    uncl = res["unclassified"]
    if uncl["actual_eur"] > 0:
        print(f"    {'(unclassified)':<26}{uncl['actual_pct']:>7.1f}%"
              f"{'—':>6}{'—':>12}{'—':>8}  ⚪ {len(uncl['instruments'])} Instrument(e)")
        for rid, name, eur in uncl["instruments"]:
            print(f"        ! ohne Klasse: {rid}  {name}  ({eur:,.0f} EUR)")
        print(f"      -> in config/instrument_classes.yaml ergaenzen.")
    con = res["concentration"]
    print()
    print("    Konzentration:")
    if con["top1"]:
        who, eur, pct = con["top1"]
        mark = "🔴 ueber Limit" if con["single_breach"] else "🟢 ok"
        lim = f" (Limit {con['max_single_pct']:.0f}%)" if con["max_single_pct"] else ""
        print(f"      groesste Position : {who}  {pct:.1f}%{lim}  {mark}")
    mark5 = "🔴 ueber Limit" if con["top5_breach"] else "🟢 ok"
    lim5 = f" (Limit {con['max_top5_pct']:.0f}%)" if con["max_top5_pct"] else ""
    print(f"      Top-5 zusammen    : {con['top5_pct']:.1f}%{lim5}  {mark5}")


# ---------- Commands ----------

def cmd_init(args) -> int:
    con = duckdb.connect(str(DB_PATH))
    try:
        apply_schema(con, verbose=True)
        return 0
    finally:
        con.close()


def cmd_eval(args) -> int:
    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        res = compute(con)
    finally:
        con.close()
    print(f"==> allocation eval  ({date.today()})  — Dry-Run, kein Write")
    print()
    _print_report(res)
    return 0


def cmd_run(args) -> int:
    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64
    run_id = f"alloc-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    ts = date.today()
    con = duckdb.connect(str(DB_PATH))
    try:
        apply_schema(con)
        res = compute(con)

        def _ins(asset_class, label, tgt, lo, hi, apct, aeur, drift, status):
            con.execute("""
                INSERT OR REPLACE INTO sig_allocation
                    (ts, asset_class, label, target_pct, min_pct, max_pct,
                     actual_pct, actual_eur, drift_pct, band_status, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [ts, asset_class, label, tgt, lo, hi, apct, aeur, drift,
                  status, run_id])

        for c in res["classes"]:
            _ins(c["asset_class"], c["label"], c["target_pct"], c["min_pct"],
                 c["max_pct"], c["actual_pct"], c["actual_eur"],
                 c["drift_pct"], c["band_status"])
        uncl = res["unclassified"]
        if uncl["actual_eur"] > 0:
            _ins("unclassified", "(unclassified)", None, None, None,
                 uncl["actual_pct"], uncl["actual_eur"], None, "unclassified")

        print(f"==> allocation run  (ts={ts}, run_id={run_id})")
        print()
        _print_report(res)
        n = len(res["classes"]) + (1 if uncl["actual_eur"] > 0 else 0)
        print()
        print(f"==> {n} Zeilen geschrieben: sig_allocation({ts})")
        return 0
    finally:
        con.close()


def cmd_show(args) -> int:
    if not DB_PATH.is_file():
        print(f"FEHLER: DB nicht gefunden: {DB_PATH}", file=sys.stderr)
        return 64
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        latest = con.execute("SELECT max(ts) FROM sig_allocation").fetchone()
        if not latest or not latest[0]:
            print("Keine Allokations-Auswertung. Erst 'run' ausfuehren.")
            return 0
        rows = con.execute("""
            SELECT label, target_pct, min_pct, max_pct, actual_pct,
                   actual_eur, drift_pct, band_status
            FROM sig_allocation WHERE ts = ?
            ORDER BY target_pct DESC NULLS LAST, label
        """, [latest[0]]).fetchall()
        print(f"==> Allokation am {latest[0]}  ({len(rows)} Klassen)")
        print()
        print(f"  {'Klasse':<26}{'Ist':>8}{'Ziel':>7}{'Drift':>8}  Status")
        print(f"  {'-'*26}{'-'*8}{'-'*7}{'-'*8}  {'-'*8}")
        for label, tgt, lo, hi, apct, aeur, drift, status in rows:
            icon = _STATUS_ICON.get(status, "·")
            tgt_s   = f"{tgt:.0f}%"   if tgt   is not None else "—"
            drift_s = f"{drift:+.1f}" if drift is not None else "—"
            print(f"  {label:<26}{apct:>7.1f}%{tgt_s:>7}{drift_s:>8}  {icon} {status}")
        return 0
    finally:
        con.close()


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="Schema applyen")
    sub.add_parser("run",  help="Allokation auswerten + schreiben")
    sub.add_parser("eval", help="Dry-run — Auswertung anzeigen, kein Write")
    sub.add_parser("show", help="Letzte Allokations-Auswertung")

    args = p.parse_args()
    dispatch = {"init": cmd_init, "run": cmd_run,
                "eval": cmd_eval, "show": cmd_show}
    try:
        return dispatch[args.cmd](args)
    except AllocationError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
