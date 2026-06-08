"""nova-lab LLM-Job-Queue CLI.

Subcommands:
    enqueue-quality [--limit N]
        Scannt ref_quality_score und legt fuer jeden Wert mit (geaendertem)
        Score einen 'quality_narrative'-Job an (Idempotent ueber input_hash +
        Dedupe offener Jobs). Producer — schnell, keine LLM-Calls.
    worker [--once] [--max N] [--idle-seconds S] [--throttle S]
           [--lock-timeout S] [--model M]
        Konsument: drainiert llm_jobs ueber die lokale LLM. Haelt die
        schreibende DuckDB-Connection NUR kurz (Claim/Persist, unter
        Schreib-Lock); die Inferenz laeuft lock-/connection-frei -> Dashboard
        kann waehrenddessen lesen. --once: bis Queue leer, dann Ende (fuer
        launchd StartInterval). Ohne --once: Dauerlauf mit Idle-Sleep.
    show [--limit N]
        Letzte Jobs + Status-Zaehler (read-only).

Environment:
    LAB_DB_PATH        optional — default ~/nova_data/lab.duckdb
    LLM_OLLAMA_HOST / LLM_DEFAULT_MODEL  (siehe modules.llm.client; LLM laeuft
                       auf nova-w5, wird per HTTP angesprochen)

Beispiele:
    python -m modules.llm.jobs enqueue-quality
    python -m modules.llm.jobs worker --once
    python -m modules.llm.jobs show
"""

from __future__ import annotations

import argparse
import sys
import time

import duckdb

from modules.common import dblock
from modules.llm.client import LLMError, OllamaClient
from . import handlers, queue as q


_SUB_COLS = [
    ("sub_return_on_capital", "return_on_capital"),
    ("sub_balance_sheet",     "balance_sheet"),
    ("sub_stock_based_comp",  "stock_based_comp"),
    ("sub_gaap_vs_non_gaap",  "gaap_vs_non_gaap"),
    ("sub_insider",           "insider"),
]


def cmd_enqueue_quality(args) -> int:
    # Producer ist schnell (keine LLM-Calls) -> kurzer Lock-Block.
    with dblock.rw_connection() as con:
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
        have = dict(con.execute(
            "SELECT ref_instrument_id, input_hash "
            "FROM ref_quality_narrative").fetchall())
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
            n_new += 1 if jid else 0
            n_skip += 0 if jid else 1
        print(f"quality_narrative: {n_new} Jobs angelegt, {n_skip} "
              "uebersprungen (aktuell/offen).")
    return 0


def _fail(job, err: str) -> None:
    try:
        with dblock.rw_connection() as con:
            q.fail(con, job["job_id"], err)
    except TimeoutError:
        pass  # Job bleibt 'running' (wird beim naechsten enqueue nicht doppelt)


def cmd_worker(args) -> int:
    # Preflight: ist die LLM (nova-w5) ueberhaupt erreichbar? Sonst gar nichts
    # claimen — Jobs bleiben pending, naechster Lauf versucht erneut.
    with OllamaClient() as llm:
        ok, msg = llm.health_check()
    if not ok:
        print(f"LLM nicht erreichbar ({msg}) — Lauf uebersprungen, Jobs "
              "bleiben pending.", file=sys.stderr)
        return 0

    try:
        with dblock.rw_connection() as con:
            q.apply_schema(con)
    except TimeoutError as e:
        print(e, file=sys.stderr)
        return 1

    done = err = 0
    while True:
        # 1) Claim — kurz, gelockt.
        try:
            with dblock.rw_connection(timeout=args.lock_timeout) as con:
                job = q.claim(con)
        except TimeoutError:
            if args.once:
                break
            time.sleep(args.idle_seconds)
            continue
        if job is None:
            if args.once:
                break
            time.sleep(args.idle_seconds)
            continue

        kind = job["kind"]
        fn_c = handlers.COMPUTE.get(kind)
        fn_p = handlers.PERSIST.get(kind)
        if not fn_c or not fn_p:
            _fail(job, f"kein Handler fuer '{kind}'")
            err += 1
            continue

        # 2) Compute — langsam (LLM), KEIN Lock/DB.
        try:
            result = fn_c(job, model=args.model)
        except LLMError as e:
            # Transienter Infra-Ausfall (z.B. nova-w5 down): Job NICHT
            # verbrennen, sondern zurueck auf pending und Lauf abbrechen.
            try:
                with dblock.rw_connection(timeout=args.lock_timeout) as con:
                    q.requeue(con, job["job_id"], note=f"LLM: {e}")
            except TimeoutError:
                pass
            print(f"LLM-Problem ({e}) — Job requeued, Lauf abgebrochen.",
                  file=sys.stderr)
            break
        except Exception as e:  # noqa: BLE001
            _fail(job, f"{e.__class__.__name__}: {e}")
            err += 1
            print(f"  ✗ {kind} {job.get('ref_instrument_id')}: "
                  f"{e.__class__.__name__}: {e}", file=sys.stderr)
            continue

        # 3) Persist — kurz, gelockt.
        try:
            with dblock.rw_connection(timeout=args.lock_timeout) as con:
                msg = fn_p(con, job, result)
                q.complete(con, job["job_id"], msg)
            done += 1
            print(f"  ✓ {kind} {msg}")
        except Exception as e:  # noqa: BLE001
            _fail(job, f"persist: {e.__class__.__name__}: {e}")
            err += 1

        if args.max and (done + err) >= args.max:
            break
        if args.throttle:
            time.sleep(args.throttle)

    print(f"Worker: {done} erledigt, {err} Fehler.")
    return 0


def cmd_show(args) -> int:
    con = duckdb.connect(dblock.db_path(), read_only=True)
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


def cmd_reset(args) -> int:
    with dblock.rw_connection() as con:
        q.apply_schema(con)
        n = q.reset(con, errors=True, stuck=args.stuck)
    print(f"{n} Jobs auf 'pending' zurueckgesetzt.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m modules.llm.jobs")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("enqueue-quality",
                        help="Q-Score-Narrativ-Jobs aus ref_quality_score")
    pe.add_argument("--limit", type=int, default=0)
    pe.set_defaults(func=cmd_enqueue_quality)

    pw = sub.add_parser("worker", help="Queue drainieren (LLM)")
    pw.add_argument("--once", action="store_true")
    pw.add_argument("--max", type=int, default=0)
    pw.add_argument("--idle-seconds", dest="idle_seconds", type=float,
                    default=30.0)
    pw.add_argument("--throttle", type=float, default=0.0)
    pw.add_argument("--lock-timeout", dest="lock_timeout", type=float,
                    default=30.0)
    pw.add_argument("--model", type=str, default=None)
    pw.set_defaults(func=cmd_worker)

    ps = sub.add_parser("show", help="Jobs + Status zeigen")
    ps.add_argument("--limit", type=int, default=20)
    ps.set_defaults(func=cmd_show)

    prs = sub.add_parser("reset", help="error/haengende Jobs -> pending")
    prs.add_argument("--stuck", action="store_true",
                     help="auch 'running' (haengende) Jobs zuruecksetzen")
    prs.set_defaults(func=cmd_reset)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
