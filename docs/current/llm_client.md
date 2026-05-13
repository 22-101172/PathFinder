# `gateway/llm_client.py`

## 1. Purpose
Provider-agnostic OpenAI-compatible chat-completions client. One small class
that both `QueryUnderstandingLayer` (Layer 2 fallback) and
`ResponseComposer` (primary text generation) use.

The shape is OpenAI-compatible because every realistic provider we may switch
to — Groq, OpenRouter, Mistral, Together, local Ollama — exposes the same
`POST {base_url}/chat/completions` endpoint. Provider switching is therefore
a config change, never a code change.

## 2. What's Inside
- `LLMError` and `LLMNotConfigured` (a `LLMError` subclass).
- `LLMClient` class:
  - Constructor reads `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`,
    `LLM_MODEL`, `LLM_TIMEOUT_SECONDS` from the environment by default; each
    can be overridden for tests.
  - `is_configured()` — both api_key and base_url present.
  - `chat(system, user, *, json_mode=False, model=None, temperature=0.2)`
    sends a single chat completion and returns the assistant's text.
- `get_llm_client()` — lazy module-level singleton accessor.
- `parse_json_object(text)` — tolerant JSON-object extractor used by the
  QU LLM fallback (handles markdown fences and trailing prose).

## 3. Inputs / Outputs
- Input: a `(system, user)` pair of strings plus optional flags.
- Output: the LLM's textual response (string). Errors raise `LLMError` or
  `LLMNotConfigured`.
- Privacy contract is enforced by callers, not by this client.

## 4. Who Calls It
- `gateway.query_understanding.QueryUnderstandingLayer._llm_layer`.
- `gateway.response_composer.ResponseComposer._compose_answer`.

## 5. What It Calls
- `httpx.Client.post` against the configured base URL.

## 6. Debugging / Tracing
- The client itself does not log on success — the call sites do.
- Common failure modes:
  - `LLMNotConfigured` — `LLM_API_KEY` blank. Callers fall back gracefully.
  - `LLMError("LLM request timed out …")` — provider too slow; raise the
    timeout or pick a faster model.
  - `LLMError("LLM HTTP 401 …")` — bad API key.
  - `LLMError("LLM HTTP 429 …")` — provider rate limit; either back off or
    switch model.

## 7. What NOT To Put In It
- Provider-specific request shapes — the client is intentionally generic.
- Prompt-building logic — that lives in the caller.
- Privacy filters — callers are responsible for prompt content.
- Streaming or function-calling features — out of scope for this MVP; add
  behind a feature flag if needed.
