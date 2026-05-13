# `gateway/orchestrator.py`

## 1. Purpose
Deterministic workflow dispatcher between the gateway and the KG/RAG adapters.
Given a `StructuredQuery` and the effective student context, it chooses one of
six workflows, invokes the appropriate adapter(s), and aggregates the results
into a `ResultPackage` for the ResponseComposer.

There is no LLM here, and no direct Neo4j access. Every decision the
Orchestrator makes is reproducible from its inputs.

## 2. What's Inside
- `Orchestrator` class wired into `gateway/main.py`.
  - `run(structured_query, effective_context, original_query)` — top-level
    dispatch.
  - Six workflow methods:
    `_kg_only_workflow`, `_rag_only_workflow`, `_mixed_workflow`,
    `_student_aware_workflow`, `_clarification_workflow`, plus the
    error-package builders.
  - `_map_intent_to_kg_operation`, `_build_kg_params`,
    `_pick_mixed_kg_op` — pure helpers.
  - `_safe_kg_call`, `_safe_rag_call` — defensive wrappers that normalize
    unexpected exceptions into structured error dicts.
- Module-level constants:
  - `INTENT_TO_KG_OP` — the single canonical intent → KG-operation table.
  - `_kg_result_is_error`, `_kg_error_detail`, `_rag_dict_is_error`,
    `_to_rag_result` — helpers that paper over the dual KG error shapes and
    convert raw RAG dicts into the schema `RAGResult`.

## 3. Inputs / Outputs
- Inputs:
  - `structured_query: StructuredQuery` — from QueryUnderstandingLayer.
  - `effective_context: StudentContext | None` — base context plus session
    overrides (the SessionManager has already merged overrides into
    `planned_courses` at this point).
  - `original_query: str` — the raw user text; passed through to RAG and the
    composer prompt, never re-interpreted here.
- Output: `ResultPackage` with `status ∈ {"ok", "error", "clarification_needed"}`.

## 4. Who Calls It
- `gateway/main.py` once per request.
- `gateway/tests/test_orchestrator.py` against the fake adapters.

## 5. What It Calls
- `KGAdapter.call(operation, params)`.
- `RAGAdapter.execute(sub_query)` (with `student_context` deliberately omitted
  — the handbook RAG should not be conditioned on transcript data).
- Nothing else. Notably no imports of `engines.kg.queries`, no LLM client,
  no session mutation.

## 6. Debugging / Tracing
- INFO log line per dispatch:
  `orchestrator.workflow workflow=<…> intent=<…> status=<…>`.
- DEBUG log lines per adapter call:
  `kg.call op=<…> status=ok keys=[…]` and `rag.call status=ok citations=<n>`.
- Failure modes:
  - **Returns `error` instead of `ok`** — inspect the KG result for both
    `{"error": …}` and `{"status": "error", "message": …}` shapes.
  - **Returns `clarification_needed`** outside of an ambiguous query — the
    `_build_kg_params` helper detected a required field was missing.
    Check that QU populated the relevant entity slot.
  - **Mixed workflow only calls RAG** — the query lacked a `course_code`,
    so the KG side was skipped intentionally.

## 7. What NOT To Put In It
- Anything that interprets raw user text. The orchestrator may inspect
  `original_query` only with simple keyword checks (e.g. picking
  `get_prerequisites` vs `get_course_profile` in the mixed workflow). Heavier
  interpretation belongs to QU.
- LLM calls.
- Direct Neo4j or `engines.kg.queries` imports — always go through `KGAdapter`.
- Final user-facing text — the composer owns presentation.
- Eligibility, graduation, or GPA decisions. The KG returns raw facts; the
  ALE (future module) is what would interpret them.
