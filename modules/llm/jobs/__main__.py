"""nova-lab LLM-Job-Queue CLI.

Subcommands:
    enqueue-quality [--all] [--limit N]
        Scannt ref_quality_score und legt fuer jeden Wert mit (geaendertem)
        Score einen 'quality_narrative'-Job an (Idempotent ueber input_hash +
        Dedupe offener Jobs). Producer — schnell, keine LLM-Calls.
    worker [--once] [--max N] [--idle-seconds S] [--throttle S] [--model M]
        Always-On-Konsument: drainiert llm_jobs seriell ueber die lokale LLM.
        --once: bis Queue leer, dann Ende (fuer Cron/Test). Ohne --once:
        Dauerlauf mit Idle-Sleep (fuer launchd KeepAlive).
    show [--limit N]
        Letzte Jobs + Status-Zaehler.

Environment:
    LAB_DB_PATH        optional — default ~/nova_data/lab.duckdb
    LLM_OLLAMA_HOST / LLM_DEFAULT_MODEL  (siehe modules.llm.client)

Beispiele:
    python -m modules.llm.jobs enqueue-quality
    python -m modules.llm.jobs worker --once
    python -m modules.llm.jobs worker            # Dauerlauf (Daemon)
    python -m modules.llm.jobs show
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

import duckdb

from . import handlers, queue as q


DB_PATH = pathlib.Path(
    os.environ.get(
        "LAB_DB_PATH",
        str(pathlib.Path.home() / "nova_data" / "lab.duckdb"),
    )
)

_SUB_COLS = [
    ("sub_return_on_capital", "return_on_capital"),
    ("sub_balance_sheet",     "balance_sheet"),
    ("sub_stock_based_comp",  "stock_based_comp"),
    ("sub_gaap_vs_non_gaap",  "gaap_vs_non_gaap"),
    ("sub_insider",           "insider"),
]


def cmd_enqueue_quality(args) -> int:
    con = duckdb.connect(str(DB_PATH))
    try:
        q.apply_schema(con)
        if not con.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'ref_quality_score'").fetchone():
            print("ref_quality_score fehlt — erst `python -m "
                  "modules.quality_score run` laufen lassen.", file=sys.stderr)
            return 2
        cols = ", ".join(c for c, _ in _SUB_COLS)
        rows = con.execute(
            f"SELECT ref_instrument_id, symbol, score, {cols} "
            "FROM ref_quality_score WHERE score IS NOT NULL "
            "ORDER BY score DESC").fetchall()
        if args.limit:
            rows = rows[:args.limit]
        # bestehende Hashes (Staleness)
        have = dict(con.execute(
            "SELECT ref_instrument_id, input_hash "
            "FROM ref_quality_narrative").fetchall()) \
            if con.execute("SELECT 1 FROM information_schema.tables WHERE "
                           "table_name='ref_quality_narrative'").fetchone() \
            else {}
        n_new = n_skip = 0
        for r in rows:
            rid, sym, score = r[0], r[1], r[2]
            subs = {key: r[3 + i] for i, (_, key) in enumerate(_SUB_COLS)}
            h = handlers.quality_input_hash(score, subs)
            if have.get(rid) == h:
                n_skip += 1
                continue
            jid = q.enqueue(con, "quality_narrative", ref_instrument_id=rid,
                            payload={"symbol": sym, "score": score,
                                     "subs": subs}, priority=100, input_hash=h)
            if jid:
                n_new += 1
            else:
                n_skip += 1
        print(f"quality_narrative: {n_new} Jobs angelegt, {n_skip} "
              "uebersprungen (aktuell/offen).")
    finally:
        con.close()
    return 0


def cmd_worker(args) -> int:
    con = duckdb.connect(str(DB_PATH))
    try:
        q.apply_schema(con)
        done = err = 0
        while True:
            job = q.claim(con)
            if job is None:
                if args.once:
                    break
                time.sleep(args.idle_seconds)
                continue
            fn = handlers.HANDLERS.get(job["kind"])
            if fn is None:
                q.fail(con, job["job_id"], f"kein Handler fuer '{job['kind']}'")
                err += 1
                continue
            try:
                res = fn(con, job, model=args.model)
                q.complete(con, job["job_id"], res)
                done += 1
                print(f"  ✓ {job['kind']} {res}")
            except Exception as e:  # noqa: BLE001
                q.fail(con, job["job_id"], f"{e.__class__.__name__}: {e}")
                err += 1
                print(f"  ✗ {job['kind']} {job.get('ref_instrument_id')}: "
                      f"{e.__class__.__name__}: {e}", file=sys.stderr)
            if args.max and (done + err) >= args.max:
                break
            if args.throttle:
                time.sleep(args.throttle)
        print(f"Worker: {done} erledigt, {err} Fehler.")
    finally:
        con.close()
    return 0


def cmd_show(args) -> int:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        print("Status:", q.counts(con))
        rows = con.execute(
            "SELECT kind, ref_instrument_id, status, "
            "COALESCE(result, error, ''), updated_at FROM llm_jobs "
            "ORDER BY updated_at DESC LIMIT ?", [args.limit]).fetchall()
    finally:
        con.close()
    for kind, rid, status, msg, ts in rows:
        print(f"  {str(ts)[:19]}  {status:<8}{kind:<20}{rid or '':<14}"
              f"{(msg or '')[:60]}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m modules.llm.jobs")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("enqueue-quality",
                        help="Q-Score-Narrativ-Jobs aus ref_quality_score")
    pe.add_argument("--all", action="store_true")
    pe.add_argument("--limit", type=int, default=0)
    pe.set_defaults(func=cmd_enqueue_quality)

    pw = sub.add_parser("worker", help="Queue drainieren (LLM)")
    pw.add_argument("--once", action="store_true",
                    help="bis Queue leer, dann Ende")
    pw.add_argument("--max", type=int, default=0)
    pw.add_argument("--idle-seconds", dest="idle_seconds", type=float,
                    default=30.0)
    pw.add_argument("--throttle", type=float, default=0.0,
                    help="Pause (s) zwischen Jobs")
    pw.add_argument("--model", type=str, default=None)
    pw.set_defaults(func=cmd_worker)

    ps = sub.add_parser("show", help="Jobs + Status zeigen")
    ps.add_argument("--limit", type=int, default=20)
    ps.set_defaults(func=cmd_show)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
