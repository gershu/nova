"""Probe-Workload fuer den Ollama-Endpoint.

Aufruf:
    python -m modules.llm.probe                              # Default: health + minimal generate
    python -m modules.llm.probe --prompt "Was ist ETF?"      # custom prompt
    python -m modules.llm.probe --model llama3.1:8b          # anderes Modell
    python -m modules.llm.probe --json                       # testet JSON-mode

Via nova:
    ~/nova/scripts/nova_run.sh lab_llm_probe nova-hub
    (Workload laeuft auf hub, ruft via HTTP nach nova-w5)
"""

from __future__ import annotations

import argparse
import json
import sys

from .client import LLMError, OllamaClient


DEFAULT_PROMPT = "Antworte mit genau einem Wort: OK"
DEFAULT_JSON_PROMPT = (
    'Beantworte als JSON-Objekt mit Schluesseln "answer" und "confidence" (0..1). '
    'Frage: Was ist die Hauptstadt von Frankreich?'
)


def main() -> int:
    p = argparse.ArgumentParser(description="Ollama-Endpoint Probe")
    p.add_argument("--host", help="LLM_OLLAMA_HOST override")
    p.add_argument("--model", help="LLM_DEFAULT_MODEL override")
    p.add_argument("--prompt", default=None, help="Custom prompt (sonst Default)")
    p.add_argument("--system", default=None, help="System-Prompt (optional)")
    p.add_argument("--json", action="store_true", help="JSON-Mode + JSON-Default-Prompt")
    p.add_argument("--list-models", action="store_true", help="Zeige verfuegbare Modelle und exit")
    args = p.parse_args()

    with OllamaClient(host=args.host, model=args.model) as llm:
        print(f"==> nova-lab llm probe")
        print(f"    host          : {llm.host}")
        print(f"    default model : {llm.default_model}")
        print(f"    timeout       : {llm.timeout_s}s")
        print(f"    retries       : {llm.retries}")
        print()

        # Health
        print("==> health check")
        ok, msg = llm.health_check()
        print(f"    {'OK' if ok else 'FAIL'}: {msg}")
        if not ok:
            print()
            print("    Pruefen:", file=sys.stderr)
            print("      - laeuft Ollama auf nova-w5? curl http://nova-w5.local:11434/api/tags", file=sys.stderr)
            print("      - Firewall? launchctl print gui/$(id -u)/de.gershu.nova.ollama auf nova-w5", file=sys.stderr)
            return 1

        if args.list_models:
            print()
            print("==> models")
            for m in llm.list_models():
                size_gb = m.get("size", 0) / 1e9
                print(f"    {m.get('name'):40s}  {size_gb:.2f} GB")
            return 0

        # Generate
        prompt = args.prompt or (DEFAULT_JSON_PROMPT if args.json else DEFAULT_PROMPT)
        print()
        print(f"==> generate (json_mode={args.json})")
        print(f"    prompt: {prompt!r}")
        if args.system:
            print(f"    system: {args.system!r}")

        try:
            r = llm.generate(prompt, system=args.system, json_mode=args.json)
        except LLMError as e:
            print(f"FEHLER: {e}", file=sys.stderr)
            return 1

        print()
        print(f"    model      : {r.model}")
        print(f"    duration   : {r.duration_s:.2f}s")
        print(f"    eval_count : {r.eval_count} tokens")
        print(f"    speed      : {r.tps:.1f} tokens/s")
        print()
        print(f"    response:")
        for line in r.text.splitlines() or [""]:
            print(f"      {line}")

        # JSON-Mode-Sanity: parse + pretty-print
        if args.json:
            print()
            try:
                parsed = json.loads(r.text)
                print(f"    parsed JSON: {json.dumps(parsed, indent=2, ensure_ascii=False)}")
            except json.JSONDecodeError as e:
                print(f"    [WARN] JSON-Mode aktiv aber Output ist kein valid JSON: {e}",
                      file=sys.stderr)
                return 1

        return 0


if __name__ == "__main__":
    raise SystemExit(main())
