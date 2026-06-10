"""nova-lab LLM-Job-Queue CLI.

Aktiver Job-Typ: portfolio_digest. (filing_change/filing_8k/quality_narrative
sind im Zuge der GuruFocus-Umstellung stillgelegt — sec-api.)

Subcommands:
    enqueue-digest [--limit N]
        Je offener Portfolio-Position (pos_holdings) einen portfolio_digest-
        Job — kurzer Wochenueberblick. Producer — schnell, keine LLM-Calls.
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
    python -m modules.llm.jobs enqueue-digest
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


def _has(con, name: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
        [name]).fetchone())


def cmd_enqueue_digest(args) -> int:
    """Je offener Portfolio-Position einen portfolio_digest-Job. Producer
    sammelt Q-Score + juengste Filing-Aenderung + Red-Flag in die Payload
    (compute macht nur den LLM-Call). Idempotent ueber input_hash + Dedupe."""
    from datetime import date, timedelta
    _STRONG, _WEAK = 70, 40
    since = (date.today() - timedelta(days=90)).isoformat()
    with dblock.rw_connection() as con:
        q.apply_schema(con)
        if not _has(con, "pos_holdings"):
            print("pos_holdings fehlt — kein Portfolio-Kontext.",
                  file=sys.stderr)
            return 2
        hold = con.execute(
            "SELECT DISTINCT h.ref_instrument_id, i.symbol, i.name "
            "FROM pos_holdings h LEFT JOIN ref_instruments i "
            "ON i.ref_instrument_id = h.ref_instrument_id "
            "WHERE h.valid_to IS NULL ORDER BY i.symbol").fetchall()
        if args.limit:
            hold = hold[:args.limit]

        qmap = {}
        if _has(con, "ref_quality_score"):
            qmap = {r[0]: (r[1], r[2]) for r in con.execute(
                "SELECT ref_instrument_id, score, n_ok "
                "FROM ref_quality_score").fetchall()}
        nmap = {}
        if _has(con, "ref_quality_narrative"):
            nmap = {r[0]: (r[1], r[2]) for r in con.execute(
                "SELECT ref_instrument_id, narrative, red_flag "
                "FROM ref_quality_narrative").fetchall()}
        fmap, negmap = {}, {}
        if _has(con, "ref_filing_change"):
            for r in con.execute(
                    "SELECT ref_instrument_id, form, period, impact, summary, "
                    "COALESCE(event_type, '') FROM ref_filing_change "
                    "QUALIFY row_number() OVER (PARTITION BY ref_instrument_id "
                    "ORDER BY generated_at DESC) = 1").fetchall():
                fmap[r[0]] = r[1:]
            negmap = {r[0]: r[1] for r in con.execute(
                "SELECT ref_instrument_id, COUNT(*) FROM ref_filing_change "
                "WHERE lower(impact) = 'negativ' AND generated_at >= ? "
                "GROUP BY ref_instrument_id", [since]).fetchall()}
        have = {}
        if _has(con, "ref_portfolio_digest"):
            have = dict(con.execute(
                "SELECT ref_instrument_id, input_hash "
                "FROM ref_portfolio_digest").fetchall())

        n_new = n_skip = 0
        for rid, sym, name in hold:
            score, n_ok = qmap.get(rid, (None, None))
            band = ("hohe Qualitaet" if (score is not None and score >= _STRONG)
                    else "gemischt" if (score is not None and score >= _WEAK)
                    else "schwach" if score is not None else "—")
            narrative, red_flag = nmap.get(rid, (None, None))
            f = fmap.get(rid)
            filing = None
            if f:
                form, period, impact, summary, ev = f
                filing = (f"{form} {period or ''} [{ev or impact}]: "
                          f"{(summary or '')[:200]}")
            payload = {"symbol": sym, "name": name, "score": score,
                       "band": band, "n_ok": n_ok, "narrative": narrative,
                       "red_flag": red_flag, "filing": filing,
                       "neg_count": int(negmap.get(rid, 0))}
            h = handlers.digest_input_hash(payload)
            if have.get(rid) == h:
                n_skip += 1
                continue
            jid = q.enqueue(con, "portfolio_digest", ref_instrument_id=rid,
                            payload=payload, priority=120, input_hash=h)
            n_new += 1 if jid else 0
            n_skip += 0 if jid else 1
        print(f"portfolio_digest: {n_new} Jobs angelegt, {n_skip} "
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


def _ago(dt) -> str:
    """Kompakte Relativzeit ggue. jetzt. tz-aware dt -> Vergleich in UTC;
    naive dt -> als LOKALE Wanduhr behandeln (DuckDB konvertiert aware-UTC
    beim TIMESTAMP-Insert in Lokalzeit und liefert sie naiv zurueck)."""
    from datetime import datetime, timezone
    if dt is None:
        return "—"
    if getattr(dt, "tzinfo", None) is not None:
        now = datetime.now(timezone.utc)
    else:
        now = datetime.now()
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

    pd_ = sub.add_parser("enqueue-digest",
                         help="portfolio_digest-Jobs je offener Position")
    pd_.add_argument("--limit", type=int, default=0)
    pd_.set_defaults(func=cmd_enqueue_digest)

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
