# `gateway/response_composer.py`

## 1. Purpose
The presentation layer. Converts a `ResultPackage` into a final
`QueryResponse` for the UI. It is intentionally LLM-based for the `ok` path
(so the answer reads naturally) but is wrapped in a deterministic fallback so
the gateway always returns useful text even when the LLM is down or not
configured.

## 2. What's Inside
- `ResponseComposer` class.
  - `compose(result)` — dispatch on `result.status`.
  - `_compose_clarification(result)`, `_compose_error(result)` —
    deterministic, never call the LLM.
  - `_compose_answer(result)` — calls the LLM with a sanitized prompt and
    falls back on any failure.
  - `_build_prompt(result)` — privacy-safe (system, user) prompt builder.
  - `_fallback_response(result, citations)` — deterministic renderer.
- Module-level helpers:
  - `_FRIENDLY_ERRORS` — maps internal error codes (e.g. `course_not_found`)
    to user-facing sentences.
  - `_sanitize_student_summary(context)` — non-personal allow-list
    (`track_id`, `level`, `current_semester`).
  - `_format_*` helpers — one deterministic renderer per KG result shape
    used by the fallback path.

## 3. Inputs / Outputs
- Input: `ResultPackage` with `original_query`, `kg_result`, `rag_result`,
  `student_context`, `status`, `error_detail`.
- Output: `QueryResponse` (the gateway fills in `session_id` afterwards).
- Status semantics:
  - `clarification_needed` → the answer text IS the clarification prompt.
  - `error` → friendly mapped message.
  - `ok` → LLM-generated text (or deterministic fallback).

## 4. Who Calls It
- `gateway/main.py` once per request.
- `gateway/tests/test_response_composer.py`.

## 5. What It Calls
- `LLMClient.chat(...)` — only on the `ok` path.
- Nothing else. No adapters, no session mutation, no `engines.*`.

## 6. Debugging / Tracing
- DEBUG: `composer.mode mode=<ok|clarification|error>`.
- DEBUG: `composer.llm status=ok chars=<n>` on successful LLM calls.
- WARNING: `composer.llm status=fallback reason=<…>` when the LLM fails or
  is not configured.
- Failure modes:
  - **LLM never called** — `LLM_API_KEY` is blank, so the composer goes
    straight to the deterministic fallback. Expected behaviour for local
    development.
  - **Generic-sounding answer** — the LLM returned text but the request
    actually fell back. Check WARNING logs for `status=fallback`.
  - **Citations missing** — the rag adapter returned without citations or
    the composer received a non-`rag` engine pattern with no RAG block.

## 7. What NOT To Put In It
- Adapter calls (KG/RAG).
- Routing or workflow logic.
- New facts that are not present in the structured input. The system prompt
  enforces this; do not weaken it.
- Eligibility, graduation, GPA, credit-limit, or registration claims. The
  composer must phrase outputs as "based on the curriculum…", never as
  decisions.
- Student PII inside the LLM prompt. The allow-list in
  `_sanitize_student_summary` is the only contract — extend it only after a
  privacy review.
