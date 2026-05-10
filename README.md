# PathFinder

PathFinder is an academic and career advising backend for EUI/CIS-style student support. The idea is to answer student questions by combining:

- a Knowledge Graph backed by Neo4j for structured academic and career reasoning
- a Retrieval-Augmented Generation pipeline over the CIS handbook for policy questions
- a FastAPI gateway that is meant to unify both behind one `POST /query` API

This README is intentionally written as an AI handoff document. It describes the codebase as it exists now, including what is implemented, what is still scaffolded, and where the current code has drifted from the older architecture notes.

## Current Status

The repository is partially implemented.

What works today:

- `gateway/student_context_provider.py` is implemented
- `gateway/session_manager.py` is implemented
- `gateway/adapters/kg_adapter.py` is implemented
- the Neo4j client and KG query layer are wired for direct in-process use
- `gateway/main.py` and the FastAPI app exist
- `GET /health` works
- acceptance tests for T02/T03 exist in `gateway/tests/test_t02_t03.py`

What is still not implemented:

- `gateway/query_understanding.py`
- `gateway/orchestrator.py`
- `gateway/response_composer.py`

What is important to understand:

- the gateway route `POST /query` exists, but the full end-to-end query pipeline does not currently work because the three components above still raise `NotImplementedError`
- the repository still contains older documentation assumptions about HTTP-based `kg-engine` and `rag-engine`, but the current `gateway` code no longer fully matches those assumptions
- the current `RAGAdapter` imports the local `engines/rag/` code directly instead of calling a separate RAG HTTP service

## Repository Layout

```text
pathfinder/
|- .env.example
|- docker-compose.yml
|- README.md
|- gateway/
|  |- main.py
|  |- query_understanding.py
|  |- orchestrator.py
|  |- response_composer.py
|  |- session_manager.py
|  |- student_context_provider.py
|  |- requirements.txt
|  |- conftest.py
|  |- models/
|  |  |- schemas.py
|  |- tests/
|  |  |- test_t02_t03.py
|  |- adapters/
|     |- kg_adapter.py
|     |- rag_adapter.py
|- engines/
|  |- kg/
|  |  |- kg_engine.py
|  |  |- neo4j_client.py
|  |  |- queries.py
|  |  |- cypher/
|  |  |- data/
|  |- rag/
|     |- ingest.py
|     |- retriever.py
|- data/
|  |- student_profile.json
|  |- handbook/
|- ui/
|  |- Dockerfile
```

## High-Level Architecture

The intended architecture is:

1. UI sends a student query to `POST /query`
2. gateway loads student context and session state
3. query understanding classifies the request
4. orchestrator calls KG, RAG, or both
5. response composer turns structured results into final natural language

The actual code state today is:

1. `gateway/main.py` creates all gateway components at import time
2. `StudentContextProvider` works
3. `SessionManager` works
4. `QueryUnderstandingLayer.classify()` is still unimplemented
5. `Orchestrator.run()` is still unimplemented
6. `ResponseComposer.compose()` is still unimplemented
7. `KGAdapter` is callable
8. `RAGAdapter` exists, but it follows a different design from the old docs

## Gateway API

### Health endpoint

`GET /health`

Response:

```json
{
  "status": "ok",
  "service": "pathfinder-gateway"
}
```

### Main endpoint

`POST /query`

Request schema:

```json
{
  "session_id": "optional existing session id",
  "user_text": "What skills am I missing for Data Scientist?",
  "active_student_id": "S_000123"
}
```

Response schema:

```json
{
  "session_id": "sess_xxxxxxxx",
  "answer_text": "final natural-language answer",
  "citations": [
    {
      "source": "Handbook",
      "page": 12
    }
  ],
  "status": "ok"
}
```

Important current behavior:

- if the student ID is missing, `main.py` returns HTTP 404
- if query understanding is reached, the current implementation returns HTTP 503 because `QueryUnderstandingLayer` is unfinished
- even if query understanding were implemented, orchestration and response composition would still currently return HTTP 503 for the same reason

## Runtime Flow in `gateway/main.py`

`gateway/main.py` is the real entry point for the backend.

It instantiates these singletons at module import time:

- `student_provider = StudentContextProvider()`
- `kg_wrapper = KGAdapter()`
- `rag_wrapper = RAGAdapter()`
- `session_manager = SessionManager(student_provider)`
- `qu_layer = QueryUnderstandingLayer()`
- `orchestrator = Orchestrator(kg_wrapper, rag_wrapper)`
- `composer = ResponseComposer()`

The request pipeline in `handle_query()` is:

1. create or restore a session
2. load the base student context
3. build an effective context using session overrides
4. classify the raw user text
5. apply any overrides detected by the QU layer
6. orchestrate engine calls
7. compose the final user-facing answer
8. persist the last referenced entities and increment turn count

The FastAPI lifespan hook closes the Neo4j client via `kg_wrapper.close()` on shutdown.

## Data Contracts

The canonical Pydantic models live in `gateway/models/schemas.py`.

Key models:

- `QueryRequest`
- `QueryResponse`
- `Citation`
- `EntitySet`
- `SessionOverrides`
- `StructuredQuery`
- `CourseRecord`
- `StudentContext`
- `LastReferenced`
- `SessionState` (external contract model, not the internal dataclass)
- `RAGResult`
- `ResultPackage`

Important design rules already encoded in the schema layer:

- list fields use `Field(default_factory=list)`
- `CourseRecord` is frozen with `ConfigDict(frozen=True)`
- `StudentContext` is the central student object passed through the pipeline
- `planned_courses` is reserved for hypothetical session-time overrides

There is also a global handbook constant:

```python
_GRADUATION_CREDIT_HOURS = 133
```

That constant is used to compute `StudentContext.credit_hours_remaining`.

## Student Context Provider

File: `gateway/student_context_provider.py`

Status: implemented

Responsibilities:

- load the student record from JSON
- validate and normalize it into `StudentContext`
- derive `completed_courses`, `failed_courses`, and `in_progress_courses`
- compute remaining credit hours
- compute max semester credit limit from CGPA
- cache loaded students in memory

Behavior details:

- data source is `data/student_profile.json` by default
- this can be overridden with `STUDENT_DATA_PATH`
- `get_student(student_id)` returns `None` instead of raising on failure
- course history parsing is all-or-nothing: one bad row causes the whole student load to fail
- loaded student contexts are cached by `student_id`

Derived values:

- `credit_hours_remaining = 133 - total_credit_hours_earned`
- max semester hours:
  - `> 3.0` -> `21`
  - `>= 2.0` -> `18`
  - `>= 1.0` -> `15`
  - otherwise `12`

## Session Manager

File: `gateway/session_manager.py`

Status: implemented

Responsibilities:

- create and manage in-memory sessions
- store per-session overrides
- track last referenced entities for future follow-ups
- build `effective_context = base_context + session overrides`

Important behavior:

- session IDs look like `sess_<8 hex chars>`
- if an unknown session ID is passed back after restart, a new session is created
- `added_courses` accumulate across turns and are deduplicated
- `target_role` is replaced when a new one is supplied
- `base_context` is never mutated
- `build_effective_context()` returns a copied `StudentContext` only when overrides exist

Internal state is stored in a dataclass named `SessionState` inside `session_manager.py`. This is different from the Pydantic `SessionState` in `models/schemas.py`.

The file is already written with a future Redis migration in mind. The intended swap points are `_get_session()` and `_set_session()`.

## Query Understanding Layer

File: `gateway/query_understanding.py`

Status: scaffold only, not implemented

Intended job:

- classify user text into a `StructuredQuery`
- detect intent
- decide `engine_pattern` as `kg`, `rag`, or `mixed`
- detect whether the question is student-aware
- extract entities like course codes, role IDs, track IDs, and skill IDs
- detect hypothetical overrides such as added courses or target roles

Current code state:

- `INTENT_PATTERNS` is defined
- `OVERRIDE_PATTERNS` is defined
- constructor loads `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL`
- every important method still raises `NotImplementedError`

Planned intents currently present in the file:

- `get_prerequisites`
- `get_course_profile`
- `skill_gap_analysis`
- `role_recommendation`
- `track_guidance`
- `handbook_policy_query`
- `ambiguous`

Important doc/code mismatch:

- the docstring examples mention role IDs like `ROLE_DATA_SCIENTIST`
- the KG wrapper comments and earlier notes indicate the actual graph format is `RL_Data_Scientist`
- any future implementation should verify the real IDs expected by the live graph before hardcoding normalization logic

## Orchestrator

File: `gateway/orchestrator.py`

Status: scaffold only, not implemented

Intended job:

- choose the correct workflow
- call KG only, RAG only, mixed, student-aware, or clarification paths
- return a `ResultPackage`

Current code state:

- class and method skeletons exist
- `_error_package()` is implemented
- all workflow methods still raise `NotImplementedError`

Expected workflow families from the file:

- KG-only
- RAG-only
- mixed
- student-aware
- clarification

## Response Composer

File: `gateway/response_composer.py`

Status: scaffold only, not implemented

Intended job:

- take a `ResultPackage`
- produce the final `QueryResponse`
- present data clearly without adding new facts
- merge citations from RAG results

Current code state:

- constructor loads `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL`
- method skeletons exist
- all main methods still raise `NotImplementedError`

## Knowledge Graph Integration

Files:

- `gateway/adapters/kg_adapter.py`
- `engines/kg/neo4j_client.py`
- `engines/kg/queries.py`

Status: implemented at wrapper level

Current design:

- the gateway talks to Neo4j directly through Python
- `KGAdapter` does not call an HTTP `kg-engine` service
- `queries.py` contains the query functions that are dispatched by `KGAdapter`
- `Neo4jClient` wraps the official Neo4j Python driver

This is a major architectural fact: the gateway code currently uses direct in-process KG access, not an HTTP KG service.

### KGAdapter behavior

`KGAdapter.call(operation, params)`:

- looks up the operation in a dispatch map
- calls the mapped wrapper method
- returns structured error dicts instead of raising

Startup behavior:

- `KGAdapter.__init__()` attempts to connect to Neo4j immediately
- a startup connection failure is logged as a warning, not treated as fatal

### Supported KG operations

The wrapper exposes 15 operations:

- `get_course_profile`
- `get_prerequisites`
- `get_skills_taught`
- `search_courses_by_skill`
- `get_role_profile`
- `get_roles_by_track`
- `compute_skill_gap`
- `compute_alignment_score`
- `recommend_courses_to_close_gap`
- `estimate_alignment_improvement`
- `find_best_matching_roles`
- `get_track_overview`
- `compare_tracks`
- `recommend_track_for_role`
- `recommend_track_for_skill`

### Neo4j environment variables

Used by `engines/kg/neo4j_client.py`:

- `NEO4J_URI` default: `bolt://localhost:7687`
- `NEO4J_USER` default: `neo4j`
- `NEO4J_PASSWORD` default: empty string, but effectively required for real use

## RAG Integration

Files:

- `gateway/adapters/rag_adapter.py`
- `engines/rag/ingest.py`
- `engines/rag/retriever.py`

Status: partially implemented, but not aligned with the old architecture docs

### Actual current design

The current `RAGAdapter`:

- imports `get_retriever()` from `engines/rag/retriever.py`
- retrieves relevant handbook chunks locally
- optionally sends the assembled context to an external LLM endpoint via `COLAB_LLM_URL`

This means the current code does not use `RAG_ENGINE_URL`, despite older notes and `docker-compose.yml` suggesting an HTTP `rag-engine` service.

### `RAGAdapter.execute()`

Inputs:

- `sub_query: str`
- optional `student_context`

Behavior:

- returns a fallback answer if the query is empty
- returns a fallback answer if the retriever failed to initialize
- calls `retriever.retrieve(sub_query, k_vec=20, k_bm25=15, k_final=6)`
- builds citations from retrieved documents
- if `COLAB_LLM_URL` is missing, returns citations with `"LLM endpoint not set."`
- otherwise posts to the external endpoint and returns the generated answer

Return shape today is a plain dict like:

```json
{
  "answer": "text answer",
  "citations": [
    {
      "source": "Handbook",
      "page": 12,
      "text": "retrieved excerpt"
    }
  ]
}
```

Important note:

- `RAGAdapter` exposes `execute()`
- the planned orchestrator docstrings talk about wrapper calls in more abstract terms
- any orchestrator implementation should follow the actual method name and return shape in the current code unless the wrapper is refactored first

### `engines/rag/ingest.py`

Purpose:

- ingest the handbook markdown file into a Chroma vector store
- create parent and child chunks
- persist the vector DB and a pickled parent chunk map

Current assumptions:

- source markdown file is `CIS_Handbook.md`
- vector DB is stored under `engines/rag/chroma_db`
- parent chunk map is stored in `engines/rag/chunks.pkl`

Chunking constants:

- parent chunk size: `800`
- parent overlap: `250`
- child chunk size: `200`
- child overlap: `40`

Embedding model:

- `BAAI/bge-small-en-v1.5`

### `engines/rag/retriever.py`

Intended retrieval pipeline:

- Chroma dense retrieval
- BM25 lexical retrieval
- reciprocal rank fusion
- cross-encoder reranking

Configured models:

- embeddings: `BAAI/bge-small-en-v1.5`
- reranker: `cross-encoder/ms-marco-MiniLM-L-6-v2`

Important caution:

- the file currently appears to have indentation and formatting issues
- it may need cleanup before the local RAG path can run reliably
- because of that, treat the RAG code as present but not yet production-stable

## Current Demo Student Data

File: `data/student_profile.json`

There is currently one hardcoded student profile:

- `student_id`: `S_000123`
- `name`: `Seif Elislam`
- `track_id`: `AI`
- `level`: `3`
- `current_semester`: `Spring`
- `cgpa`: `2.85`
- `academic_standing`: `good`
- `total_credit_hours_earned`: `76`

Derived from the current provider logic:

- `credit_hours_remaining`: `57`
- `max_credit_hours_allowed`: `18`

Transcript highlights:

- one failed course: `C-CS218`
- in progress: `C-AI321`, `C-CS316`, `HUM228`

This student record is the current foundation for all tests and all gateway demos.

## Tests

File: `gateway/tests/test_t02_t03.py`

Current automated coverage is limited to:

- T02: `StudentContextProvider`
- T03: `SessionManager`

The test module verifies:

- student loading
- derived course buckets
- credit-hours-remaining formula
- max-credit-hours rule for CGPA 2.85
- invalid ID handling
- provider caching
- session ID generation
- session reuse
- override application
- base context immutability
- override accumulation across turns
- selective update of `last_referenced`

Run:

```bash
cd gateway
python -m pytest tests/test_t02_t03.py -v
```

There are no tests yet for:

- `main.py`
- query understanding
- orchestration
- response composition
- KG wrapper behavior against a live database
- RAG ingestion or retrieval

## Environment Variables

Values currently documented in `.env.example`:

- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `KG_ENGINE_URL`
- `RAG_ENGINE_URL`
- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`
- `SESSION_STORE`

Additional variable used by the current codebase:

- `STUDENT_DATA_PATH`
- `COLAB_LLM_URL`

Important mismatch:

- `KG_ENGINE_URL` and `RAG_ENGINE_URL` are part of the old service-oriented plan
- current gateway code does not use `KG_ENGINE_URL`
- current `RAGAdapter` does not use `RAG_ENGINE_URL`
- current RAG path uses `COLAB_LLM_URL` instead for answer generation

## Docker and Deployment Reality

### `gateway/Dockerfile`

This is functional for the gateway itself:

- uses `python:3.11-slim`
- installs `gateway/requirements.txt`
- exposes port `8000`
- launches `uvicorn main:app --host 0.0.0.0 --port 8000 --reload`

### `ui/Dockerfile`

This is a placeholder too. There is no actual UI source in the repo yet.

### `docker-compose.yml`

The compose file describes services for:

- `neo4j`
- `kg-engine`
- `rag-engine`
- `gateway`
- `ui`

However, in the current repository:

- `neo4j` is real
- `gateway` is real
- `kg-engine` is not implemented as a real containerized service
- `rag-engine` is not implemented as a real containerized service
- `ui` is not implemented as a real app

So `docker-compose.yml` should be treated as aspirational or partially stale, not as a guaranteed working deployment definition.

## Key Design Invariants

These are the important rules already encoded by the current code:

- `StudentContextProvider` loads durable student truth
- `SessionManager` owns temporary conversational overrides
- `SessionManager` must not interpret raw user text
- hypothetical courses belong in `planned_courses`, not in `completed_courses`
- `base_context` must never be mutated
- `KGAdapter` is a thin adapter and should return error dicts rather than throw
- the QU layer is the only place that should interpret user text for routing and override detection
- the response composer is intended to present results, not invent them

## Architectural Drift and Important Mismatches

Another model working on this repo should know these mismatches before editing anything:

1. The README and comments historically describe an HTTP-based split between gateway, KG engine, and RAG engine.
2. The actual gateway code currently imports KG and RAG pieces more directly.
3. `KGAdapter` already talks to Neo4j locally and does not use `KG_ENGINE_URL`.
4. `RAGAdapter` imports local retrieval code and uses `COLAB_LLM_URL`; it does not call `RAG_ENGINE_URL`.
5. `docker-compose.yml` still reflects the older service split.
6. `engines/rag/retriever.py` now lives under the shared `engines/` tree and should stay aligned with the gateway adapter contract.
7. The main `/query` route is structurally present but not functionally complete because T07, T08, and T09 are unfinished.

If you extend the codebase, decide first whether the project should move toward:

- direct in-process integration, or
- separate HTTP microservices

Right now the codebase mixes both ideas.

## Practical Run Notes

### Gateway only

If you only want to inspect the gateway API shape:

```bash
cd gateway
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Notes:

- `/health` should work
- `/query` will not complete successfully end-to-end until T07-T09 are implemented
- Neo4j connectivity depends on valid Neo4j credentials
- local RAG availability depends on the retriever artifacts and Python dependencies not listed in `gateway/requirements.txt`

### Tests

```bash
cd gateway
python -m pytest tests/test_t02_t03.py -v
```

## Suggested Next Steps

If the next AI model is expected to continue implementation, the most sensible order is:

1. decide the final architecture for KG and RAG integration
2. stabilize `engines/rag/retriever.py` and confirm the intended RAG runtime path
3. implement `QueryUnderstandingLayer`
4. implement `Orchestrator`
5. implement `ResponseComposer`
6. add integration tests for `POST /query`
7. either fix or simplify `docker-compose.yml` so it matches reality

## Short Summary for Future AI Models

This repo is a partially implemented academic advisor backend. The solid pieces today are student context loading, session handling, and KG wrapper plumbing. The user-facing `/query` pipeline is scaffolded but blocked by unimplemented query understanding, orchestration, and response composition. The biggest conceptual issue is architectural drift: the documentation and compose file still assume HTTP microservices, while the current Python code directly imports KG and RAG logic in-process. Any future work should first resolve that mismatch, then build the unfinished gateway layers on top of a single consistent integration strategy.
