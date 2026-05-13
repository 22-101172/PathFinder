# `gateway/query_understanding.py`

## 1. Purpose
The single component allowed to interpret raw user text. Converts a free-text
question into a `StructuredQuery` that downstream components (Orchestrator,
SessionManager) can consume deterministically.

Hybrid by design:
- **Layer 1 — rules** for fast, well-known phrasings.
- **Layer 2 — LLM fallback** for anything the rules cannot classify, and only
  if an `LLMClient` is configured.

## 2. What's Inside
- `QueryUnderstandingLayer` — the class wired into `gateway/main.py`.
  - `classify(user_text, student_context=None, session_state=None)` —
    the public entry point.
  - `_rule_layer(...)`, `_detect_intent(...)`,
    `_determine_engine_pattern(...)`, `_extract_entities(...)`,
    `_detect_student_awareness(...)` — rule-layer helpers.
  - `_detect_overrides(...)` — hypothetical-course and target-role detection.
  - `_llm_layer(...)`, `_build_llm_prompt(...)`,
    `_validate_and_build_from_llm(...)` — LLM fallback.
- Module-level constants and tables:
  - `INTENT_RULES` — ordered specific → generic keyword table.
  - `OVERRIDE_ADD_TRIGGERS`, `OVERRIDE_TARGET_ROLE_TRIGGERS`.
  - `STUDENT_AWARE_PRONOUN_RE`, `FOLLOWUP_PRONOUN_RE`, `COURSE_CODE_RE`.
  - `KG_INTENTS`, `RAG_INTENTS`, `MIXED_INTENTS`, `STUDENT_AWARE_INTENTS`.
  - `ALLOWED_INTENTS_FOR_LLM`, `ALLOWED_ENGINE_PATTERNS`,
    `ALLOWED_QUERY_TYPES` — validation sets for LLM output.

## 3. Inputs / Outputs
- Inputs:
  - `user_text: str` — raw user prompt.
  - `student_context: StudentContext | None` — effective context (already
    merged with session overrides by the SessionManager).
  - `session_state: RuntimeSessionState | None` — last_referenced and
    overrides dicts for follow-up / target-role resolution.
- Output: `StructuredQuery` containing `intent`, `engine_pattern`,
  `query_type`, `entities`, `needs_clarification`, `clarification_prompt`,
  `session_overrides`.

## 4. Who Calls It
- `gateway/main.py` — once per request.
- `gateway/tests/test_query_understanding.py` — unit tests.

## 5. What It Calls
- `KGReferenceData` (`gateway/kg_data.py`) for entity resolution.
- `LLMClient` (`gateway/llm_client.py`) for the optional fallback.
- It does NOT call any adapter, KG, RAG, or session-mutation method.

## 6. Debugging / Tracing
- One log line per classification:
  `qu.classified layer=… intent=… engine=… type=… clarification=… has_course=…`
- When the LLM fallback is used or skipped, a `qu.llm_fallback.*` log line
  is emitted at DEBUG/WARNING level.
- Common failure modes and how to spot them:
  - **Wrong entity resolved** — usually the longest-substring matcher in
    `KGReferenceData` picked a shorter alias. Add the longer canonical name
    to the CSV; do not paste an alias in code.
  - **Always returns `ambiguous`** — confirm `KG_DATA_DIR` is set or
    `data/kg/` is populated. Without CSVs the loader runs in starter mode
    and most entity phrases will not resolve.
  - **LLM fallback never runs** — `LLM_API_KEY` is blank, so `is_configured()`
    returns False. Either set the env var or accept the rule-only behaviour.

## 7. What NOT To Put In It
- Engine calls (KG, RAG, Neo4j, HTTP).
- Session mutation — `SessionManager.apply_overrides` owns that.
- Final answer generation — that is `ResponseComposer`.
- Student PII in LLM prompts. The prompt builder enforces this; if a future
  feature needs more context, add it to the allow-list explicitly.
- Domain reasoning (eligibility, graduation, GPA). The composer must also
  never claim these — but QU should not even tempt the composer by adding
  domain hints.
