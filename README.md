# PathFinder

PathFinder is an academic and career advising backend for EUI/CIS-style student support. The current MVP is a FastAPI-based modular monolith that combines:

- a local Knowledge Graph integration through Neo4j
- a local RAG retrieval path over the handbook
- a gateway API that unifies both behind `POST /query`

This README is the main handoff file for humans and AI models. Start here before opening any other project document.

## How To Use This File

Use this file when you need:

- the current project status in one place
- the real runtime architecture, not older blueprint assumptions
- the safest next implementation order
- a guide to the other Markdown handoff files

## Current Status

The integration-phase pipeline is now end-to-end. `POST /query` produces a valid `QueryResponse` for every supported workflow, with safe fallbacks when external dependencies are not available.

Implemented and usable today:

- `gateway/student_context_provider.py`
- `gateway/session_manager.py`
- `gateway/adapters/kg_adapter.py`
- `gateway/adapters/rag_adapter.py`
- `gateway/main.py`
- `gateway/kg_data.py` — KG reference-data alias loader (CSV-backed)
- `gateway/llm_client.py` — shared OpenAI-compatible LLM client
- `gateway/query_understanding.py` — rule layer + LLM fallback + override detection
- `gateway/orchestrator.py` — deterministic workflow dispatcher (KG-only, RAG-only, mixed, student-aware, clarification)
- `gateway/response_composer.py` — LLM-based presentation with deterministic fallback
- `GET /health`, `POST /query`
- Unit + endpoint tests in `gateway/tests/`

Intentionally deferred (do not implement without re-reading the plan):

- The full Academic Logic Engine (eligibility, graduation audit, GPA simulation, credit-limit decisions).
- The `compare_tracks` KG operation. `EntitySet` carries only one `track_id`; the QU layer routes such queries to a polite clarification.

## Current Architecture

```text
UI
  -> FastAPI Gateway (gateway/main.py)
      -> StudentContextProvider
      -> SessionManager
      -> QueryUnderstandingLayer
           - rule layer
           - optional LLM fallback (llm_client.py)
      -> Orchestrator
           - KGAdapter -> engines/kg -> Neo4j
           - RAGAdapter -> engines/rag -> external answer-generation endpoint
      -> ResponseComposer
           - optional LLM call (llm_client.py)
           - deterministic fallback
```

This is a modular monolith for now, not a KG/RAG microservice split.

## Current Request Flow

`gateway/main.py` does this per request:

1. `SessionManager.get_or_create_session(student_id, session_id?)`.
2. `StudentContextProvider.get_student(student_id)` — 404 if not found.
3. `SessionManager.build_effective_context(base_context, session_id)`.
4. `QueryUnderstandingLayer.classify(user_text, effective_context, session_state)`.
5. Apply any returned `session_overrides`; rebuild effective context if needed.
6. `Orchestrator.run(structured_query, effective_context, original_query)`.
7. `ResponseComposer.compose(result_package)`.
8. Update `last_referenced`, increment `turn_count`, attach `session_id`.

For a step-by-step walkthrough including log lines, see `docs/current/runtime_flow.md`.

## Student Context And Session Rules

Invariants to preserve:

- `StudentContextProvider` owns durable student truth.
- `SessionManager` owns temporary conversational state and overrides.
- `SessionManager` must not interpret raw user text — that is the QU layer's job.
- Hypothetical courses belong in `planned_courses`, not in `completed_courses`.
- `base_context` is never mutated.
- QU is the only layer that interprets user text for routing and override detection.
- The ResponseComposer presents facts, not new conclusions.

## Academic Logic Scope

Academic calculations are intentionally outside the current gateway integration. That means:

- the gateway does not own credit-limit or graduation calculations
- `StudentContextProvider` loads raw student record data and derived course buckets only
- any future academic calculations belong to the Academic module, not this layer
- the ResponseComposer is explicitly instructed not to claim eligibility, GPA impact, or graduation status

## Configuration

Required for production behaviour, optional for local development (the system falls back safely when these are blank):

| Variable | Purpose |
|---|---|
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | KG connection (used by `KGAdapter`) |
| `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS` | Shared LLM client used by QU (fallback) and ResponseComposer |
| `LLM_MODEL_COMPOSER` | Optional stronger model for the composer; defaults to `LLM_MODEL` |
| `KG_DATA_DIR` | Folder containing the KG team's CSVs (`courses.csv`, `roles.csv`, `tracks.csv`, `skills.csv`) used for entity-name resolution |
| `COLAB_LLM_URL` | Endpoint used by `RAGAdapter` for handbook answer generation |
| `SESSION_STORE` | `memory` is the only mode today; `redis` is a future migration |
| `STUDENT_DATA_PATH` | Override for `data/student_profile.json` |

See `.env.example` for the recommended Groq-based default that you can switch to OpenRouter, Mistral, or local Ollama by changing the URL/model alone.

## Run Locally

```bash
# install dependencies
pip install -r gateway/requirements.txt

# start the gateway
uvicorn gateway.main:app --reload --port 8000

# smoke test
curl http://localhost:8000/health
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"active_student_id":"S_000123","user_text":"What are the prerequisites for C-AI311?"}'
```

If `LLM_API_KEY` is blank, the gateway still works — the composer falls back to deterministic text. If `KG_DATA_DIR` is unset and the workspace's `PathFinder KG-Engine/data/` folder is missing, the QU layer falls back to a tiny starter alias set and logs a warning at startup.

## Tests

```bash
python -m pytest gateway/tests -v
```

Coverage:

- `test_t02_t03.py` — student provider and session manager regression suite.
- `test_query_understanding.py` — rule layer, LLM fallback, follow-up resolution, override detection, privacy guard.
- `test_orchestrator.py` — every workflow path with fake KG/RAG adapters.
- `test_response_composer.py` — LLM path, deterministic fallback, citation handling, privacy guard.
- `test_query_endpoint.py` — `/health` and `/query` with stubbed pipeline.

All tests are offline — no live Neo4j, RAG, or LLM is required.

## Trace A Request

Every `/query` request emits these log lines in order at INFO level:

```
gateway.request.received  student=<hash> session=<present|none> text_len=…
qu.classified             layer=<rule|llm|fallback> intent=… engine=… type=…
orchestrator.workflow     workflow=<…> intent=… status=…
gateway.response.sent     student=<hash> session=<id> status=…
```

Set `LOG_LEVEL=DEBUG` (or pass `--log-cli-level=DEBUG` to pytest) to see KG/RAG adapter outcomes, composer mode, and override application.

The student id is always logged as a short SHA-1 prefix — never the raw id.

## Docker

`docker-compose.yml` defines: `neo4j`, `gateway`, `ui`.

Caveats:

- `ui` is still a placeholder service — no UI app source yet.
- There is no separate `kg-engine` or `rag-engine` service. KG and RAG remain local modules behind gateway adapters.

## Safe Next Implementation Order

If another AI model continues the project, the safest order is:

1. Hook up the real LLM provider key (Groq default) and observe production behaviour.
2. Expand the QU intent table with phrasings discovered in real traffic.
3. Add `compare_tracks` after agreeing on a schema extension for two track ids.
4. Build the Academic Logic Engine **as its own module** — do not bolt it onto the orchestrator.

Do not do these without revisiting the plan:

- Do not split KG/RAG into HTTP microservices yet.
- Do not let the orchestrator interpret user text.
- Do not send student PII to the shared LLM client.

## Document Guide

- `README.md` — start here.
- `PathFinder_Blueprint_Drift_Handoff.md` — compare current reality vs older blueprint.
- `docs/CODEBASE_UNDERSTANDING_REPORT.md` — repo map and per-file responsibilities (high level).
- `docs/REFINEMENT_LOG.md` — small cleanup decisions made before the integration sprint.
- `docs/current/runtime_flow.md` — request walkthrough.
- `docs/current/*.md` — per-file deep dives:
  - `gateway_main.md`
  - `query_understanding.md`
  - `orchestrator.md`
  - `response_composer.md`
  - `session_manager.md`
  - `student_context_provider.md`
  - `kg_adapter.md`
  - `rag_adapter.md`
  - `llm_client.md`
  - `kg_data.md`

## Short Summary For The Next Model

PathFinder is now a working KG + RAG advising gateway: `POST /query` returns useful answers for prerequisites, course profiles, skill-gap analyses, alignment scores, role recommendations, and handbook policy questions, with safe fallbacks when external dependencies (LLM, KG CSVs) are missing. Hard-line boundaries — no academic decisions in the gateway, no LLM-driven routing, no student PII in external prompts — are enforced both in code and in the per-file docs under `docs/current/`.
