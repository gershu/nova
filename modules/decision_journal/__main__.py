"""nova — Decision-Journal CLI.

Schliesst den Feedback-Loop: was hat der Recommendation-Layer
vorgeschlagen (sig_recommendations) — und was wurde daraus entschieden
(sig_decision_journal). Human-in-loop, kuratiert.

Subcommands:
    init                Schema applyen (sig_decision_journal)
    list                Recommendations am juengsten Tag + Journal-Status
    suggest             Kandidaten-Trades zu einer Recommendation zeigen
    log                 Entscheidung zu einer Recommendation erfassen
    assess              Outcome zu einer erfassten Entscheidung nachtragen
    show                Alle Journal-Eintraege
    stats               Status-/Outcome-Verteilung + Follow-Through-Rate

Recommendation-Adressierung (log/assess/suggest):
    --rec-id ist Pflicht. --ts und --model sind optional und defaulten auf
    den juengsten Tag bzw. das eindeutige Modell.

Trade-Verknuepfung erfolgt in der Dashboard-Page (Suggest + Bestaetigen);
das CLI erfasst Status, Begruendung und Outcome.

Beispiele:
    python -m modules.decision_journal init
    python -m modules.decision_journal list
    python -m modules.decision_journal suggest --rec-id 1
    python -m modules.decision_journal log    --rec-id 1 --status acted_full \\
                                              --rationale "Position getrimmt"
    python -m modules.decision_journal assess --rec-id 1 --outcome good \\
                                              --pnl 1200 --note "richtig gelegen"
    python -m modules.decision_journal stats
"""

from __future__ import annotations

import argparse
import sys

from . import store


_STATUS_ICON = {
    "pending":       "○",
    "acted_full":    "✓",
    "acted_partial": "◐",
    "declined":      "✗",
    "expired":       "⌛",
}
_PRIO_ICON = {"high": "🔴", "medium": "🟠", "low": "🟢"}
_OUTCOME_ICON = {"good": "🟢", "neutral": "⚪", "poor": "🔴"}


# ---------- Helpers ----------

def _resolve_rec(con, ts: str | None, model: str | None) -> tuple[str, str]:
    """ts/model aufloesen — Default: juengster Tag, eindeutiges Modell."""
    if ts is None:
        ts = store.latest_rec_ts(con)
        if ts is None:
            raise store.JournalError("Keine Recommendations vorhanden — "
                                     "erst `modules.llm.recommendations run`.")
    if model is None:
        models = [r[0] for r in con.execute(
            "SELECT DISTINCT model FROM sig_recommendations WHERE ts = ?",
            [ts]).fetchall()]
        if not models:
            raise store.JournalError(f"Keine Recommendations am {ts}.")
        if len(models) > 1:
            raise store.JournalError(
                f"Mehrere Modelle am {ts}: {', '.join(models)} — --model angeben.")
        model = models[0]
    return ts, model


# ---------- init ----------

def cmd_init(args) -> int:
    with store.connect(read_only=False) as con:
        store.apply_schema(con)
    print("    ✓ sig_decision_journal")
    return 0


# ---------- list ----------

def cmd_list(args) -> int:
    with store.connect() as con:
        df = store.list_recommendations(con, ts=args.ts)
        if df.empty:
            print("Keine Recommendations. Erst `modules.llm.recommendations run`.")
            return 0
        ts = str(df.iloc[0]["rec_ts"])
        n_open = int((df["status"].isna()
                      | (df["status"] == "pending")).sum())
        print(f"==> Recommendations vom {ts}  ({len(df)}, davon {n_open} offen)")
        print()
        for _, r in df.iterrows():
            prio = _PRIO_ICON.get(r["priority"], "·")
            status = r["status"] if r["status"] else "pending"
            sicon = _STATUS_ICON.get(status, "○")
            sym = f" [{r['symbol']}]" if r["symbol"] else ""
            print(f"  {sicon} {prio} #{int(r['rec_id'])} "
                  f"[{r['action']}]{sym}  {r['title'] or ''}")
            print(f"       Status: {status}", end="")
            if r["outcome"]:
                print(f"  ·  Outcome: {r['outcome']}", end="")
            print()
        print()
        print("  Legende: ○ offen  ✓ umgesetzt  ◐ teilweise  ✗ verworfen  ⌛ verfallen")
        return 0


# ---------- suggest ----------

def cmd_suggest(args) -> int:
    with store.connect() as con:
        ts, model = _resolve_rec(con, args.ts, args.model)
        rec = con.execute("""
            SELECT ref_instrument_id, symbol, action, title
            FROM sig_recommendations
            WHERE ts = ? AND model = ? AND rec_id = ?
        """, [ts, model, args.rec_id]).fetchone()
        if rec is None:
            print(f"FEHLER: Recommendation #{args.rec_id} am {ts} nicht gefunden.",
                  file=sys.stderr)
            return 64
        ref_id, sym, action, title = rec
        print(f"==> #{args.rec_id} [{action}] {sym or '—'}  {title or ''}")
        if not ref_id:
            print("    (portfolio-/marktweite Recommendation — kein Instrument)")
            return 0
        trades = store.suggest_trades(con, ref_id, ts)
        if trades.empty:
            print(f"    Keine Trades auf {sym or ref_id} im Fenster "
                  f"{store.SUGGEST_WINDOW_DAYS}d ab {ts}.")
            return 0
        print(f"    Kandidaten-Trades ({store.SUGGEST_WINDOW_DAYS}d-Fenster):")
        for _, t in trades.iterrows():
            print(f"      {t['ts']}  {t['side']:<4} {t['quantity']:>10,.2f} "
                  f"@ {t['price']:>10,.2f} {t['currency']}  "
                  f"{t['broker']} lot {int(t['trade_lot'])}")
        return 0


# ---------- log ----------

def cmd_log(args) -> int:
    with store.connect() as con:
        ts, model = _resolve_rec(con, args.ts, args.model)
    try:
        store.upsert_decision(ts, model, args.rec_id,
                              status=args.status,
                              rationale=args.rationale,
                              decided_at=args.decided_at)
    except store.JournalError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 64
    print(f"==> Entscheidung erfasst: #{args.rec_id} ({ts}) -> {args.status}")
    return 0


# ---------- assess ----------

def cmd_assess(args) -> int:
    with store.connect() as con:
        ts, model = _resolve_rec(con, args.ts, args.model)
    try:
        store.assess_outcome(ts, model, args.rec_id,
                             outcome=args.outcome,
                             pnl_eur=args.pnl,
                             note=args.note)
    except store.JournalError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 64
    print(f"==> Outcome erfasst: #{args.rec_id} ({ts}) -> {args.outcome}")
    return 0


# ---------- show ----------

def cmd_show(args) -> int:
    with store.connect() as con:
        df = store.get_journal(con)
    if df.empty:
        print("Journal leer. Erst `log` ausfuehren (oder `init`).")
        return 0
    print(f"==> Decision-Journal  ({len(df)} Eintraege)")
    print()
    for _, r in df.iterrows():
        sicon = _STATUS_ICON.get(r["status"], "·")
        sym = f" [{r['rec_symbol']}]" if r["rec_symbol"] else ""
        print(f"  {sicon} {r['rec_ts']} #{int(r['rec_id'])} "
              f"[{r['rec_action'] or '—'}]{sym}  {r['rec_title'] or ''}")
        print(f"       Status: {r['status']}"
              f"{'  ·  entschieden ' + str(r['decided_at']) if r['decided_at'] is not None and str(r['decided_at']) != 'NaT' else ''}")
        if r["rationale"]:
            print(f"       Begruendung: {r['rationale']}")
        trades = store.parse_linked_trades(r["linked_trades"])
        if trades:
            print(f"       Verknuepfte Trades: {len(trades)}")
        if r["outcome"]:
            oc = _OUTCOME_ICON.get(r["outcome"], "·")
            pnl = (f"  ·  {r['outcome_pnl_eur']:+,.0f} EUR"
                   if r["outcome_pnl_eur"] is not None
                   and str(r["outcome_pnl_eur"]) != "nan" else "")
            print(f"       Outcome: {oc} {r['outcome']}{pnl}")
            if r["outcome_note"]:
                print(f"                {r['outcome_note']}")
        print()
    return 0


# ---------- stats ----------

def cmd_stats(args) -> int:
    with store.connect() as con:
        s = store.journal_stats(con)
    print("==> Decision-Journal — Statistik")
    print()
    print(f"  Recommendations gesamt : {s['n_recs_total']}")
    print(f"  davon journalisiert    : {s['n_journaled']}")
    print()
    print("  Status:")
    for st in store.VALID_STATUS:
        if st in s["by_status"]:
            print(f"    {_STATUS_ICON.get(st, '·')} {st:<14} {s['by_status'][st]}")
    if s["by_outcome"]:
        print()
        print("  Outcome:")
        for oc in store.VALID_OUTCOME:
            if oc in s["by_outcome"]:
                print(f"    {_OUTCOME_ICON.get(oc, '·')} {oc:<14} {s['by_outcome'][oc]}")
    if s["follow_through_pct"] is not None:
        print()
        print(f"  Follow-Through-Rate    : {s['follow_through_pct']} %  "
              f"(umgesetzt / entschieden)")
    return 0


# ---------- main ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Schema applyen")

    pl = sub.add_parser("list", help="Recommendations + Journal-Status")
    pl.add_argument("--ts", default=None, help="Recommendation-Tag (Default: juengster)")

    def _rec_args(sp):
        sp.add_argument("--rec-id", type=int, required=True, dest="rec_id")
        sp.add_argument("--ts", default=None, help="Default: juengster Tag")
        sp.add_argument("--model", default=None, help="Default: eindeutiges Modell")

    psug = sub.add_parser("suggest", help="Kandidaten-Trades zu einer Recommendation")
    _rec_args(psug)

    plog = sub.add_parser("log", help="Entscheidung erfassen")
    _rec_args(plog)
    plog.add_argument("--status", required=True, choices=store.VALID_STATUS)
    plog.add_argument("--rationale", default=None, help="Begruendung")
    plog.add_argument("--decided-at", default=None, dest="decided_at",
                       help="Entscheidungs-Datum (Default: heute)")

    pa = sub.add_parser("assess", help="Outcome nachtragen")
    _rec_args(pa)
    pa.add_argument("--outcome", required=True, choices=store.VALID_OUTCOME)
    pa.add_argument("--pnl", type=float, default=None, help="Outcome-PnL EUR")
    pa.add_argument("--note", default=None, help="Outcome-Notiz")

    sub.add_parser("show",  help="Alle Journal-Eintraege")
    sub.add_parser("stats", help="Status-/Outcome-Verteilung")

    args = p.parse_args()
    dispatch = {
        "init": cmd_init, "list": cmd_list, "suggest": cmd_suggest,
        "log": cmd_log, "assess": cmd_assess, "show": cmd_show,
        "stats": cmd_stats,
    }
    try:
        return dispatch[args.cmd](args)
    except store.JournalError as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())
