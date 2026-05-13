"""
llm_client.py  [Integration sprint — shared external-LLM client]
────────────────────────────────────────────────────────────────
A tiny, provider-agnostic client for OpenAI-compatible chat completion APIs.

Used by:
  - QueryUnderstandingLayer  → optional LLM fallback when the rule layer cannot
                               classify a query.
  - ResponseComposer         → primary text generation, with deterministic
                               fallback if the call fails.

Provider neutrality:
  The OpenAI Chat Completions shape (`POST {base_url}/chat/completions`) is
  exposed by Groq, OpenRouter, Mistral, Together, local Ollama (with the
  /v1 prefix), and others. Switching providers is therefore a config change,
  never a code change.

Configuration (all via environment variables):
  - LLM_PROVIDER         (label only, e.g. "groq")
  - LLM_BASE_URL         e.g. "https://api.groq.com/openai/v1"
  - LLM_API_KEY          bearer token; if blank, is_configured() returns False
  - LLM_MODEL            e.g. "llama-3.1-8b-instant"
  - LLM_TIMEOUT_SECONDS  request timeout, default 20s

Privacy contract (enforced by *callers*, documented here for clarity):
  Callers MUST NOT send student PII (name, student_id, grades, transcript,
  CGPA, full course history, full session state) through this client.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


# ── Exceptions ───────────────────────────────────────────────────────────────

class LLMError(Exception):
    """Any failure when talking to the LLM provider.

    Wraps lower-level errors (timeouts, HTTP errors, malformed JSON) so the
    call sites only need to catch one exception type."""


class LLMNotConfigured(LLMError):
    """Raised when the client is asked to call the LLM but is not configured.

    Distinct from `LLMError` so callers can decide whether to log this as a
    warning (transient failure) vs INFO (LLM intentionally disabled)."""


# ── Client ───────────────────────────────────────────────────────────────────

class LLMClient:
    """Thin wrapper around an OpenAI-compatible chat completions endpoint.

    The class is intentionally stateless beyond configuration so that a single
    instance can be shared across threads / async handlers. Each `chat()` call
    opens its own short-lived `httpx.Client`."""

    def __init__(
        self,
        provider: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.provider = provider if provider is not None else os.getenv("LLM_PROVIDER", "groq")
        self.base_url = (
            base_url if base_url is not None
            else os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
        ).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("LLM_API_KEY", "")
        self.model = model if model is not None else os.getenv("LLM_MODEL", "llama-3.1-8b-instant")
        try:
            self.timeout_seconds = (
                float(timeout_seconds) if timeout_seconds is not None
                else float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
            )
        except (TypeError, ValueError):
            self.timeout_seconds = 20.0

    # ── Public API ───────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """Return True when both an API key and a base URL are available.

        Callers should use this to decide whether to skip the LLM step
        gracefully instead of catching `LLMNotConfigured`."""
        return bool(self.api_key) and bool(self.base_url)

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        """
        Send one round-trip to the chat completions endpoint and return the
        assistant's text content.

        Parameters:
            system:      system-role message (instructions, schema, persona)
            user:        user-role message (the actual prompt content)
            json_mode:   ask the provider to return a JSON object
                         (set `response_format` accordingly; callers must
                         still defensively `json.loads` the result because
                         not every provider enforces this).
            model:       optional per-call model override
            temperature: low default; we want deterministic-ish answers

        Raises:
            LLMNotConfigured: client has no api_key/base_url
            LLMError:         any network / HTTP / parsing error
        """
        if not self.is_configured():
            raise LLMNotConfigured("LLM client is not configured (missing api_key or base_url)")

        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": model or self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMError(f"LLM request timed out after {self.timeout_seconds}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM transport error: {exc}") from exc

        if response.status_code >= 400:
            # Trim the body so we don't dump full prompts/responses into logs.
            snippet = response.text[:300].replace("\n", " ")
            raise LLMError(
                f"LLM HTTP {response.status_code} from {self.provider}: {snippet}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("LLM response was not valid JSON") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM response missing choices[0].message.content") from exc

        if not isinstance(content, str):
            raise LLMError("LLM response content was not a string")

        return content


# ── Module-level singleton accessor ──────────────────────────────────────────

_default_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Return a lazily-built default `LLMClient` driven by env vars.

    The singleton is rebuilt only if the test suite explicitly clears
    `_default_client`. Production code should never need to do that."""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


def parse_json_object(text: str) -> dict[str, Any]:
    """Tolerant JSON-object extractor for LLM outputs.

    Some providers wrap JSON in markdown fences even when asked not to. We
    try the strict path first and only fall back to substring extraction if
    that fails. Always returns a dict; raises `LLMError` on real garbage."""
    if not isinstance(text, str):
        raise LLMError("parse_json_object: expected str")
    stripped = text.strip()
    # Strip ```json ... ``` fences if present.
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        # remove a leading "json" language tag if present
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].lstrip(":").lstrip()

    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Best-effort: grab the substring between the first '{' and the last '}'.
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = stripped[start : end + 1]
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    raise LLMError("LLM output was not a JSON object")
