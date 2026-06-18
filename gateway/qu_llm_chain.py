"""
QU LLM fallback chain.

Tries models in order. Moves to the next model on:
  - timeout
  - 429 / rate limit
  - invalid JSON
  - schema-validation failure (unrecognized intent)

Raises AllModelsFailedError if every model fails.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from gateway.llm_client import LLMClient, LLMError, parse_json_object

logger = logging.getLogger(__name__)


class AllModelsFailedError(Exception):
    """Raised when every model in the fallback chain fails."""


class IntentValidationError(Exception):
    """Raised when the LLM output contains an unrecognized intent."""


def load_model_chain() -> list[str]:
    """Load model names from environment. Returns ordered list (primary first)."""
    primary = os.getenv("QU_PRIMARY_MODEL", "llama-3.3-70b-versatile")
    fallback_str = os.getenv(
        "QU_FALLBACK_MODELS",
        "meta-llama/llama-4-scout-17b-16e-instruct,qwen/qwen3-32b,llama-3.1-8b-instant",
    )
    fallbacks = [m.strip() for m in fallback_str.split(",") if m.strip()]
    # Deduplicate while preserving order
    seen: set[str] = set()
    chain: list[str] = []
    for m in [primary] + fallbacks:
        if m not in seen:
            seen.add(m)
            chain.append(m)
    return chain


class QUModelChain:
    """Tries models in order, returns parsed SQ-dict list, or raises AllModelsFailedError."""

    def __init__(
        self,
        llm_client: LLMClient,
        models: list[str] | None = None,
    ) -> None:
        self._client = llm_client
        self._models = models or load_model_chain()

    def call(
        self,
        system: str,
        user_msg: str,
        valid_intents: frozenset[str],
    ) -> list[dict[str, Any]]:
        last_error: Exception | None = None

        for model in self._models:
            try:
                raw = self._client.chat(
                    system=system,
                    user=user_msg,
                    json_mode=True,
                    model=model,
                    temperature=0.0,
                )
                data = parse_json_object(raw)
                sq_list = _extract_sq_list(data)
                _validate_intents(sq_list, valid_intents)
                logger.info("QU: model %s succeeded (%d SQs)", model, len(sq_list))
                return sq_list

            except IntentValidationError as exc:
                logger.warning("QU: model %s returned invalid intent: %s", model, exc)
                last_error = exc

            except LLMError as exc:
                logger.warning("QU: model %s failed: %s", model, _safe_error_msg(exc))
                last_error = exc

        raise AllModelsFailedError(
            f"All QU models exhausted. Last error: {_safe_error_msg(last_error)}"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_sq_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize LLM output to a list of SQ dicts."""
    if "queries" in data and isinstance(data["queries"], list):
        return data["queries"]
    if "intent" in data:
        # Model returned a bare single SQ object
        return [data]
    raise LLMError("LLM output has neither 'queries' list nor 'intent' field")


def _validate_intents(sq_list: list[dict], valid_intents: frozenset[str]) -> None:
    for sq in sq_list:
        intent = sq.get("intent", "")
        if intent not in valid_intents:
            raise IntentValidationError(f"Unrecognized intent: {intent!r}")


def _safe_error_msg(exc: Exception | None) -> str:
    if exc is None:
        return "unknown"
    return str(exc)[:200]
