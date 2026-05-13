# `gateway/main.py`

## 1. Purpose
FastAPI HTTP entry point. Owns wiring of all pipeline components into singletons,
the `/health` and `/query` endpoints, and the structured log lines that make a
request traceable. Intentionally thin — no business logic lives here.

## 2. What's Inside
- Module-level singletons: `student_provider`, `kg_adapter`, `rag_adapter`,
  `session_manager`, `_kg_reference`, `_llm_client`, `qu_layer`, `orchestrator`,
  `composer`.
- `lifespan` async-context manager that calls `kg_adapter.close()` on
  shutdown.
- `_hash_student_id(student_id)` — short SHA-1 hash used for trace logs so we
  do not write raw IDs.
- Two endpoints:
  - `GET /health` — static OK probe.
  - `POST /query` — full pipeline.

## 3. Inputs / Outputs
- Input: `QueryRequest` JSON body
  (`session_id?`, `user_text`, `active_student_id`).
- Output: `QueryResponse`
  (`session_id`, `answer_text`, `citations`, `status`).
- Errors:
  - HTTP 404 if the student does not exist.
  - HTTP 500 only on truly unexpected exceptions (the pipeline normally
    handles failures and returns a valid `QueryResponse` with status `error`).

## 4. Who Calls It
- The UI (or any HTTP client) hits this module's endpoints.
- pytest's `TestClient` in `gateway/tests/test_query_endpoint.py` exercises
  the same endpoints with the pipeline stubbed out.

## 5. What It Calls
- `StudentContextProvider`, `SessionManager`,
  `QueryUnderstandingLayer.classify`,
  `Orchestrator.run`, `ResponseComposer.compose`.
- The adapters (`KGAdapter`, `RAGAdapter`) indirectly via the orchestrator.
- `load_kg_reference_data()` and `get_llm_client()` at import time.

## 6. Debugging / Tracing
- Each request emits this sequence at INFO level:
  `gateway.request.received` → `qu.classified` → `orchestrator.workflow` →
  `gateway.response.sent`. Run with `LOG_LEVEL=DEBUG` for adapter and
  composer details.
- The student id in log lines is a short SHA-1 prefix
  (`_hash_student_id`) — never the raw id.
- Common failure modes:
  - HTTP 404 → `StudentContextProvider` returned `None` (wrong id or
    `STUDENT_DATA_PATH` misconfigured).
  - HTTP 200 with `status: "clarification_needed"` → QU set a clarification
    prompt OR the Orchestrator could not build params.
  - HTTP 200 with `status: "error"` → KG or RAG returned an error shape;
    composer surfaces a friendly mapping.

## 7. What NOT To Put In It
- Routing decisions, keyword interpretation, override detection — these belong
  to QueryUnderstandingLayer.
- Engine calls beyond the adapters — these belong to the Orchestrator.
- Answer formatting, prompt building — these belong to the ResponseComposer.
- Domain logic (eligibility, graduation, GPA, credit limits) — those are
  Academic Logic Engine concerns and are intentionally out of scope for this
  integration phase.
