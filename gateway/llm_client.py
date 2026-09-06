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

Configuration — shared provider credentials (LLM_*):
  - LLM_PROVIDER         label only, e.g. "groq"
  - LLM_BASE_URL         e.g. "https://api.groq.com/openai/v1"
  - LLM_API_KEY          bearer token; if blank, is_configured() returns False
  - LLM_MODEL            internal fallback default; callers pass their own
                         model via chat(model=...)
  - LLM_TIMEOUT_SECONDS  safe fallback request timeout (default 20s); callers
                         should pass per-call timeout_seconds instead

Component-specific behavior lives in the caller's env vars:
  - QU uses QU_PRIMARY_MODEL, QU_FALLBACK_MODELS, QU_TIMEOUT_SECONDS
  - Composer uses COMPOSER_PRIMARY_MODEL, COMPOSER_FALLBACK_MODELS,
    COMPOSER_TIMEOUT_SECONDS, COMPOSER_USE_LLM

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
from dotenv import load_dotenv

# Ensure .env is loaded before grabbing vars
load_dotenv(override=True)

logger = logging.getLogger(__name__)


# ── Exceptions ───────────────────────────────────────────────────────────────

class LLMError(Exception):
    """Any failure when talking to the LLM provider."""


class LLMNotConfigured(LLMError):
    """Raised when the client is asked to call the LLM but is not configured."""


# ── Client ───────────────────────────────────────────────────────────────────

class LLMClient:
    """Thin wrapper around an OpenAI-compatible chat completions endpoint."""

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

    def is_configured(self) -> bool:
        return bool(self.api_key) and bool(self.base_url)

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        model: Optional[str] = None,
        temperature: float = 0.2,
        timeout_seconds: Optional[float] = None,
    ) -> str:
        """Send a chat completion request.

        timeout_seconds: per-call override; falls back to client default when None.
        """
        if not self.is_configured():
            raise LLMNotConfigured("LLM client is not configured (missing api_key or base_url)")

        effective_timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds

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
            with httpx.Client(timeout=effective_timeout) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMError(f"LLM request timed out after {effective_timeout}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM transport error: {exc}") from exc

        if response.status_code >= 400:
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
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


def parse_json_object(text: str) -> dict[str, Any]:
    """Tolerant JSON-object extractor for LLM outputs."""
    if not isinstance(text, str):
        raise LLMError("parse_json_object: expected str")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].lstrip(":").lstrip()

    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

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
