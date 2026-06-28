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
import time
from typing import Any

from gateway.llm_client import LLMClient, LLMError, parse_json_object

logger = logging.getLogger(__name__)


class AllModelsFailedError(Exception):
    """Raised when every model in the fallback chain fails."""


class IntentValidationError(Exception):
    """Raised when the LLM output contains an unrecognized intent."""


def _classify_failure(exc: Exception) -> str:
    """Classify a QU chain failure into a diagnostic category."""
    if isinstance(exc, IntentValidationError):
        return "intent_validation"
    if isinstance(exc, LLMError):
        msg = str(exc).lower()
        if "timed out" in msg or "timeout" in msg:
            return "timeout"
        if "http 429" in msg:
            return "rate_limit"
        if "http 413" in msg:
            return "payload_too_large"
        if "not a json object" in msg or "not valid json" in msg or "expected str" in msg:
            return "invalid_json"
        return "llm_error"
    return "unknown"


def _load_qu_timeout() -> float:
    """Read QU_TIMEOUT_SECONDS from env; return 30.0 if missing or invalid."""
    try:
        return float(os.getenv("QU_TIMEOUT_SECONDS", "30"))
    except (TypeError, ValueError):
        return 30.0


def load_model_chain() -> list[str]:
    """Load model names from environment. Returns ordered list (primary first)."""
    primary = os.getenv("QU_PRIMARY_MODEL", "llama-3.3-70b-versatile")
    fallback_str = os.getenv(
        "QU_FALLBACK_MODELS",
        "openai/gpt-oss-120b,openai/gpt-oss-20b",
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
        self._timeout: float = _load_qu_timeout()
        logger.info("QU.model_chain models=%d timeout=%.1f", len(self._models), self._timeout)

    def call(
        self,
        system: str,
        user_msg: str,
        valid_intents: frozenset[str],
        trace_id: str = "",
    ) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        n_models = len(self._models)
        est_tokens_per_call = (len(system) + len(user_msg) + 3) // 4
        total_tokens_sent = 0
        tokens_per_attempt: list[int] = []
        _chain_t0 = time.perf_counter()

        for attempt_idx, model in enumerate(self._models):
            _attempt_t0 = time.perf_counter()
            logger.info(
                "QU.model_attempt trace_id=%s attempt=%d/%d model=%s "
                "timeout=%.1fs estimated_tokens=%d",
                trace_id, attempt_idx + 1, n_models, model,
                self._timeout, est_tokens_per_call,
            )
            total_tokens_sent += est_tokens_per_call
            tokens_per_attempt.append(est_tokens_per_call)

            try:
                raw = self._client.chat(
                    system=system,
                    user=user_msg,
                    json_mode=True,
                    model=model,
                    temperature=0.0,
                    timeout_seconds=self._timeout,
                )
                data = parse_json_object(raw)
                sq_list = _extract_sq_list(data)
                _validate_intents(sq_list, valid_intents)
                _attempt_ms = int((time.perf_counter() - _attempt_t0) * 1000)
                logger.info(
                    "QU.model_attempt_result trace_id=%s attempt=%d/%d model=%s "
                    "result=success raw_len=%d sq_count=%d duration_ms=%d",
                    trace_id, attempt_idx + 1, n_models, model,
                    len(raw), len(sq_list), _attempt_ms,
                )
                _chain_ms = int((time.perf_counter() - _chain_t0) * 1000)
                logger.info(
                    "QU.model_chain_summary trace_id=%s final_status=success "
                    "models_attempted=%d winning_model=%s "
                    "tokens_per_attempt=%s total_estimated_tokens=%d "
                    "chain_duration_ms=%d exact_token_usage=not_available",
                    trace_id, attempt_idx + 1, model,
                    tokens_per_attempt, total_tokens_sent, _chain_ms,
                )
                return sq_list

            except (IntentValidationError, LLMError) as exc:
                _attempt_ms = int((time.perf_counter() - _attempt_t0) * 1000)
                fail_cat = _classify_failure(exc)
                logger.warning(
                    "QU.model_attempt_result trace_id=%s attempt=%d/%d model=%s "
                    "result=failed error_type=%s error_category=%s "
                    "error=%s duration_ms=%d",
                    trace_id, attempt_idx + 1, n_models, model,
                    type(exc).__name__, fail_cat,
                    _safe_error_msg(exc)[:120], _attempt_ms,
                )
                last_error = exc

        _chain_ms = int((time.perf_counter() - _chain_t0) * 1000)
        logger.warning(
            "QU.model_chain_summary trace_id=%s final_status=all_failed "
            "models_attempted=%d tokens_per_attempt=%s total_estimated_tokens=%d "
            "chain_duration_ms=%d exact_token_usage=not_available",
            trace_id, n_models, tokens_per_attempt, total_tokens_sent, _chain_ms,
        )
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
