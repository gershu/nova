"""Job-Handler je `kind`, zweiphasig getrennt:

  compute(job, model) -> dict   : LANGSAM (LLM-Inferenz), KEIN DB-Zugriff.
  persist(con, job, result) -> str : SCHNELL, schreibt das Ergebnis.

Diese Trennung ist bewusst: der Worker haelt die schreibende DuckDB-Connection
nur fuer das kurze persist() (unter dem Schreib-Lock); die langsame Inferenz
laeuft connection-/lock-frei -> das Dashboard kann waehrenddessen lesen.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from modules.llm.client import OllamaClient

_THEME_LABELS = {
    "return_on_capital": "Return on Capital",
    "balance_sheet":     "Balance Sheet",
    "stock_based_comp":  "Stock-based Compensation",
    "gaap_vs_non_gaap":  "GAAP vs non-GAAP",
    "insider":           "Insider",
}

_SYSTEM = (
    "Du bist ein nuechterner Buy-and-Hold-Investmentanalyst. Du fasst "
    "ausschliesslich die vorgegebenen Qualitaets-Scores zusammen — keine "
    "Kauf-/Verkaufsempfehlung, keine Kursprognose, keine erfundenen Zahlen.")

_PROMPT = """Wert: {symbol}
Gesamt-Qualitaets-Score (0-100): {score}
Teil-Scores je Thema (0-100, hoeher = besser):
{lines}

Aufgabe:
1. "narrative": 2-3 nuechterne deutsche Saetze, warum der Score so ausfaellt —
   nenne die staerksten und schwaechsten Themen konkret.
2. "red_flag": das EINE groesste Risiko in einem Satz (aus dem schwaechsten
   Thema abgeleitet) — oder "" wenn alle Themen stark sind.

Antworte ausschliesslich als JSON: {{"narrative": "...", "red_flag": "..."}}"""


def quality_input_hash(score, subs: dict) -> str:
    key = f"{score}|" + "|".join(
        f"{k}:{round(v, 3) if isinstance(v, (int, float)) else 'NA'}"
        for k, v in sorted(subs.items()))
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _parse(text: str):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            return (str(d.get("narrative", "")).strip(),
                    str(d.get("red_flag", "")).strip())
        except Exception:  # noqa: BLE001
            pass
    return text.strip(), ""


# ---- quality_narrative ----

def quality_compute(job, *, model=None) -> dict:
    """LLM-Call (langsam, kein DB)."""
    p = job["payload"]
    symbol = p.get("symbol") or job.get("ref_instrument_id")
    score = p.get("score")
    subs = p.get("subs") or {}
    lines = "\n".join(
        f"- {_THEME_LABELS.get(k, k)}: "
        f"{round(v * 100) if isinstance(v, (int, float)) else 'n/a'}"
        for k, v in subs.items())
    prompt = _PROMPT.format(symbol=symbol, score=score, lines=lines)
    with OllamaClient() as llm:
        r = llm.generate(prompt, system=_SYSTEM, json_mode=True, model=model)
    narrative, red_flag = _parse(r.text)
    return {"symbol": symbol, "score": score, "narrative": narrative,
            "red_flag": red_flag, "model": getattr(r, "model", model or "?")}


def quality_persist(con, job, result: dict) -> str:
    """Ergebnis schreiben (schnell, unter Schreib-Lock)."""
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM ref_quality_narrative WHERE ref_instrument_id=?",
                [job["ref_instrument_id"]])
    con.execute(
        "INSERT INTO ref_quality_narrative (ref_instrument_id, symbol, score, "
        "narrative, red_flag, model, input_hash, generated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [job["ref_instrument_id"], result["symbol"], result["score"],
         result["narrative"], result["red_flag"], result["model"],
         job.get("input_hash"), now])
    return f"{result['symbol']}: {result['narrative'][:70]}"


COMPUTE = {"quality_narrative": quality_compute}
PERSIST = {"quality_narrative": quality_persist}
