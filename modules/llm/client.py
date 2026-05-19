"""OllamaClient — Wrapper fuer das Ollama HTTP-API auf nova-w5.

Default-Endpoint: http://nova-w5.local:11434 (LAN-only, ohne Auth).
Override via ENV-Vars (siehe ~/.nova_env Tier 2):
  LLM_OLLAMA_HOST     (default: 'http://nova-w5.local:11434')
  LLM_DEFAULT_MODEL   (default: 'qwen2.5:14b-instruct-q4_K_M')
  LLM_TIMEOUT_S       (default: 120)
  LLM_RETRIES         (default: 3)

Usage:
    with OllamaClient() as llm:
        r = llm.generate("Was ist eine Cash-Secured-Put?")
        print(r.text, r.tps)

Adapter-Pattern: spaeter koennen OpenAIClient / AnthropicClient hinzukommen
(als Fallback wenn lokal ueberlastet oder Modell zu schwach). Fuer MVP:
nur Ollama.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import requests


log = logging.getLogger("nova.llm.ollama")


# ---------- Datentypen ----------

@dataclass
class LLMResponse:
    """Antwort eines LLM-Calls — alles was wir downstream brauchen."""
    text:        str
    model:       str
    duration_s:  float                       # End-to-End vom Request bis Response
    eval_count:  int                         # Tokens generated
    tps:         float                       # Tokens per second
    raw:         dict[str, Any]              # vollstaendige Ollama-Response

    @property
    def is_empty(self) -> bool:
        return not self.text or not self.text.strip()


class LLMError(RuntimeError):
    """Basis fuer alle LLM-bezogenen Fehler."""
    pass


# ---------- Client ----------

class OllamaClient:
    """Sync HTTP-Client fuer Ollama. Nutzt requests.Session() im
    Context-Manager-Mode fuer Connection-Pooling bei Batch-Use-Cases."""

    DEFAULT_HOST     = "http://nova-w5.local:11434"
    DEFAULT_MODEL    = "qwen2.5:14b-instruct-q4_K_M"
    DEFAULT_TIMEOUT  = 120
    DEFAULT_RETRIES  = 3
    BACKOFF_BASE_S   = 0.5

    def __init__(
        self,
        host:       str | None = None,
        model:      str | None = None,
        timeout_s:  int | None = None,
        retries:    int | None = None,
    ) -> None:
        self.host          = host      or os.environ.get("LLM_OLLAMA_HOST",    self.DEFAULT_HOST)
        self.default_model = model     or os.environ.get("LLM_DEFAULT_MODEL",  self.DEFAULT_MODEL)
        self.timeout_s     = timeout_s or int(os.environ.get("LLM_TIMEOUT_S",  self.DEFAULT_TIMEOUT))
        self.retries       = retries   or int(os.environ.get("LLM_RETRIES",    self.DEFAULT_RETRIES))
        self._session: requests.Session | None = None

    # ---------- Lifecycle ----------

    def __enter__(self) -> "OllamaClient":
        self._session = requests.Session()
        return self

    def __exit__(self, *exc) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.host.rstrip('/')}{path}"
        kwargs.setdefault("timeout", self.timeout_s)

        attempt = 0
        last_err: Exception | None = None
        while attempt < self.retries:
            try:
                if self._session is not None:
                    return self._session.request(method, url, **kwargs)
                return requests.request(method, url, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_err = e
                attempt += 1
                if attempt >= self.retries:
                    break
                backoff = self.BACKOFF_BASE_S * (2 ** (attempt - 1))
                log.warning("LLM request failed (attempt %d/%d): %s — retry in %.1fs",
                            attempt, self.retries, e, backoff)
                time.sleep(backoff)
        raise LLMError(
            f"LLM request to {url} failed after {self.retries} attempts: "
            f"{last_err.__class__.__name__}: {last_err}"
        )

    # ---------- Ops ----------

    def health_check(self) -> tuple[bool, str]:
        """Pingt /api/tags. Returnt (ok, message)."""
        try:
            r = self._request("GET", "/api/tags", timeout=10)
            r.raise_for_status()
            data = r.json()
            models = [m["name"] for m in data.get("models", [])]
            return True, f"ok, {len(models)} models: {', '.join(models[:5])}"
        except Exception as e:  # noqa: BLE001
            return False, f"{e.__class__.__name__}: {e}"

    def list_models(self) -> list[dict[str, Any]]:
        r = self._request("GET", "/api/tags", timeout=10)
        r.raise_for_status()
        return r.json().get("models", [])

    def generate(
        self,
        prompt:    str,
        *,
        model:     str | None = None,
        system:    str | None = None,
        json_mode: bool       = False,
        options:   dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Single-shot prompt -> response. Default-Modell aus self.default_model.

        json_mode=True erzwingt valid JSON output (Ollama feature). Stelle sicher
        dass dein Prompt das Modell zu JSON-Format anleitet, sonst halluciniert
        es Garbage in JSON-Wrapper.
        """
        payload: dict[str, Any] = {
            "model":  model or self.default_model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"
        if options:
            payload["options"] = options

        return self._call_and_parse("/api/generate", payload)

    def chat(
        self,
        messages:  list[dict[str, str]],   # [{"role":"system|user|assistant", "content":"..."}, ...]
        *,
        model:     str | None = None,
        json_mode: bool       = False,
        options:   dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Multi-turn chat via /api/chat. Nutzbar wenn echte Conversation noetig
        ist; fuer Single-Shot ist generate() einfacher."""
        payload: dict[str, Any] = {
            "model":    model or self.default_model,
            "messages": messages,
            "stream":   False,
        }
        if json_mode:
            payload["format"] = "json"
        if options:
            payload["options"] = options

        return self._call_and_parse("/api/chat", payload, response_key="message")

    def _call_and_parse(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        response_key: str = "response",
    ) -> LLMResponse:
        t0 = time.monotonic()
        r = self._request("POST", path, json=payload)
        elapsed_s = time.monotonic() - t0

        if r.status_code == 404:
            raise LLMError(
                f"Model not found: {payload.get('model')!r}. "
                f"Pull it via: ollama pull {payload.get('model')}"
            )
        if not r.ok:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise LLMError(f"Invalid JSON from Ollama: {e}; body={r.text[:300]}") from e

        # /api/generate -> data["response"]; /api/chat -> data["message"]["content"]
        if response_key == "message":
            text = (data.get("message") or {}).get("content", "")
        else:
            text = data.get("response", "")

        # Token counts and durations from Ollama-Metadata
        eval_count    = int(data.get("eval_count", 0) or 0)
        eval_dur_ns   = float(data.get("eval_duration", 0) or 0)
        tps = (eval_count / (eval_dur_ns / 1e9)) if eval_dur_ns > 0 else 0.0

        return LLMResponse(
            text=text,
            model=data.get("model", payload.get("model", "?")),
            duration_s=elapsed_s,
            eval_count=eval_count,
            tps=tps,
            raw=data,
        )
