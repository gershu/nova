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
    status
        Kompakter Report: Queue-Zaehler (Status x Kind, aeltester pending,
        letzter Fehler) + nova-w5 health + geladene Modelle (/api/ps).

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


def _universe(con, all_instruments: bool):
    if all_instruments:
        rows = con.execute(
            "SELECT ref_instrument_id, symbol FROM ref_instruments "
            "WHERE active AND symbol IS NOT NULL ORDER BY symbol").fetchall()
    else:
        rows = con.execute(
            "SELECT i.ref_instrument_id, i.symbol FROM ref_fundamentals_latest "
            "f JOIN ref_instruments i USING (ref_instrument_id) "
            "WHERE i.symbol IS NOT NULL ORDER BY i.symbol").fetchall()
    # Dedupe nach normalisiertem sec-Ticker: Vendor-Varianten desselben
    # Filers (BRK.B vs. BRK_B) kollabieren -> ein Job statt zwei. Rows sind
    # nach Symbol sortiert ('.' < '_'), daher gewinnt die Punkt-Variante —
    # dieselbe, die das Dashboard via resolve() bevorzugt.
    from modules.sec_filings.client import _sec_ticker
    seen_rid, seen_norm, out = set(), set(), []
    for rid, sym in rows:
        norm = _sec_ticker(sym)
        if rid in seen_rid or norm in seen_norm:
            continue
        seen_rid.add(rid)
        seen_norm.add(norm)
        out.append((rid, sym))
    return out


def cmd_enqueue_filings(args) -> int:
    """Neue Filings je Universums-Wert erkennen -> Jobs nach Form routen:
    10-K/10-Q -> 'filing_change' (GuV-Diff), 8-K -> 'filing_8k' (Text-Summary,
    da 8-K keine GuV hat). --seed: aktuelle Filings nur als 'gesehen' markieren
    (Baseline, keine Jobs) — einmalig vor der ersten echten Ueberwachung."""
    from datetime import datetime, timezone
    from modules.sec_filings import client as sec

    forms = tuple(f.strip() for f in args.forms.split(",") if f.strip())
    # Universum + bisher gesehener Stand: kurzer Lock-Block, dann lockfrei
    # die (langsamen) sec-api-Calls.
    with dblock.rw_connection() as con:
        q.apply_schema(con)
        uni = _universe(con, args.all)
        seen = {(rid, form): acc for rid, form, acc in con.execute(
            "SELECT ref_instrument_id, form, last_accession "
            "FROM ref_filing_seen").fetchall()}
    if args.limit:
        uni = uni[:args.limit]

    n_new = n_seed = n_skip = 0
    for rid, sym in uni:
        for form in forms:
            try:
                # 8-K hat keine GuV -> Text-Summary-Pfad (find_8k_filings
                # liefert items + text_url). 10-K/10-Q -> GuV-Diff.
                if form == "8-K":
                    fil = sec.find_8k_filings(sym, n=2)
                else:
                    fil = sec.find_filings(sym, n=2, forms=(form,))
            except Exception:  # noqa: BLE001
                continue
            if not fil:
                continue
            latest = fil[0]
            acc = latest.get("accession_no")
            if seen.get((rid, form)) == acc:
                n_skip += 1
                continue
            first_time = (rid, form) not in seen
            now = datetime.now(timezone.utc)
            with dblock.rw_connection() as con:
                con.execute(
                    "DELETE FROM ref_filing_seen WHERE ref_instrument_id=? "
                    "AND form=?", [rid, form])
                con.execute(
                    "INSERT INTO ref_filing_seen (ref_instrument_id, form, "
                    "last_accession, last_period, seen_at) VALUES (?,?,?,?,?)",
                    [rid, form, acc, latest.get("period_of_report"), now])
                if first_time and args.seed:
                    n_seed += 1
                elif form == "8-K":
                    q.enqueue(con, "filing_8k", ref_instrument_id=rid,
                              payload={"symbol": sym, "accession": acc,
                                       "period": latest.get(
                                           "period_of_report"),
                                       "filed_at": latest.get("filed_at"),
                                       "items": latest.get("items") or [],
                                       "text_url": latest.get("text_url")},
                              priority=80, dedupe=False)
                    n_new += 1
                else:
                    prior = fil[1] if len(fil) > 1 else None
                    q.enqueue(con, "filing_change", ref_instrument_id=rid,
                              payload={"symbol": sym, "form": form,
                                       "accession": acc,
                                       "period": latest.get(
                                           "period_of_report"),
                                       "new_filing": latest,
                                       "prior_filing": prior,
                                       "prior_period": (prior or {}).get(
                                           "period_of_report")},
                              priority=80, dedupe=False)
                    n_new += 1
        if args.sleep:
            time.sleep(args.sleep)
    print(f"filing watcher: {n_new} Jobs, {n_seed} geseedet, {n_skip} "
          "unveraendert.")
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


def _ago(dt) -> str:
    """Kompakte Relativzeit ggue. jetzt (UTC). dt naiv=UTC oder tz-aware."""
    from datetime import datetime, timezone
    if dt is None:
        return "—"
    if getattr(dt, "tzinfo", None) is not None:
        now = datetime.now(timezone.utc)
    else:
        now = datetime.utcnow()
    sec = (now - dt).total_seconds()
    past = sec >= 0
    sec = abs(int(sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = [f"{d}d" if d else "", f"{h}h" if h else "",
             f"{m}m" if (m and not d) else ""]
    s = " ".join(p for p in parts if p) or "0m"
    return f"vor {s}" if past else f"in {s}"


def _gb(n) -> str:
    try:
        return f"{float(n) / 1e9:.1f} GB"
    except (TypeError, ValueError):
        return "?"


def cmd_status(args) -> int:
    """Kompakter Health-Report: Queue-Zaehler + nova-w5 /api/ps + health."""
    from datetime import datetime, timezone

    con = duckdb.connect(dblock.db_path(), read_only=True)
    try:
        counts = q.counts(con)
        oldest = con.execute("SELECT MIN(created_at) FROM llm_jobs "
                             "WHERE status='pending'").fetchone()[0]
        last_done = con.execute("SELECT MAX(updated_at) FROM llm_jobs "
                                "WHERE status='done'").fetchone()[0]
        err = con.execute(
            "SELECT updated_at, kind, ref_instrument_id, error FROM llm_jobs "
            "WHERE status='error' ORDER BY updated_at DESC LIMIT 1").fetchone()
        matrix = con.execute(
            "SELECT kind, status, COUNT(*) FROM llm_jobs "
            "GROUP BY kind, status").fetchall()
    finally:
        con.close()

    print(f"nova-lab LLM-Job-Status   (DB: {dblock.db_path()})")
    print("=" * 60)
    order = ["pending", "running", "done", "error"]
    line = " · ".join(f"{s} {counts.get(s, 0)}" for s in order
                      if s in counts or s in order)
    print(f"Queue:  {line}")
    print(f"        aeltester pending: {str(oldest)[:16]}  ({_ago(oldest)})")
    print(f"        letzter done:      {str(last_done)[:16]}  "
          f"({_ago(last_done)})")
    if err:
        ts, kind, rid, emsg = err
        print(f"        letzter Fehler:    {str(ts)[:16]}  {kind} "
              f"{rid or ''}")
        print(f"          {(emsg or '')[:80]}")

    if matrix:
        kinds = sorted({k for k, _, _ in matrix})
        by = {(k, s): n for k, s, n in matrix}
        print()
        print(f"  {'kind':<20}" + "".join(f"{s:>9}" for s in order))
        for k in kinds:
            print(f"  {k:<20}"
                  + "".join(f"{by.get((k, s), 0):>9}" for s in order))

    host = OllamaClient().host
    print()
    print(f"nova-w5 ({host}):")
    try:
        with OllamaClient() as llm:
            ok, msg = llm.health_check()
            print(f"        health: {'OK' if ok else 'DOWN'} — {msg}")
            if ok:
                loaded = llm.ps()
                if not loaded:
                    print("        /api/ps: kein Modell resident (idle)")
                for m in loaded:
                    name = m.get("name") or m.get("model") or "?"
                    exp = m.get("expires_at")
                    when = ""
                    if exp:
                        try:
                            dt = datetime.fromisoformat(
                                str(exp).replace("Z", "+00:00"))
                            when = f"  entladen {_ago(dt)}"
                        except ValueError:
                            pass
                    print(f"        {name:<32} {_gb(m.get('size'))} "
                          f"(VRAM {_gb(m.get('size_vram'))}){when}")
    except LLMError as e:
        print(f"        health: DOWN — {e}")
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

    pf = sub.add_parser("enqueue-filings",
                        help="neue Filings -> filing_change (K/Q) / "
                             "filing_8k (8-K)")
    pf.add_argument("--all", action="store_true")
    pf.add_argument("--limit", type=int, default=0)
    pf.add_argument("--forms", type=str, default="10-K,10-Q,8-K")
    pf.add_argument("--seed", action="store_true",
                    help="aktuelle Filings nur als gesehen markieren "
                         "(Baseline, keine Jobs)")
    pf.add_argument("--sleep", type=float, default=0.0)
    pf.set_defaults(func=cmd_enqueue_filings)

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

    pst = sub.add_parser("status",
                         help="Queue-Zaehler + nova-w5 /api/ps + health")
    pst.set_defaults(func=cmd_status)

    prs = sub.add_parser("reset", help="error/haengende Jobs -> pending")
    prs.add_argument("--stuck", action="store_true",
                     help="auch 'running' (haengende) Jobs zuruecksetzen")
    prs.set_defaults(func=cmd_reset)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
