# PathFinder High-Level Architecture Audit

**Audit Date:** 2026-06-26  
**Branch:** person-seif  
**Scope:** Full system — all implemented components, data sources, API surfaces, and engine boundaries.  
**Note:** This document reports only what is present in the real codebase. Nothing is inferred or invented.

---

## 1. Current System Summary

PathFinder is an AI-powered academic advising chatbot for students at EUI (Egyptian University of Informatics). A student submits a natural-language question; the system identifies the intent, retrieves relevant information from the appropriate engine, and returns a fluent natural-language answer — all within a single HTTP round-trip.

The system follows a **decoupled multi-engine architecture** in which three specialist engines — a Knowledge Graph (KG) for curriculum and career facts, a Retrieval-Augmented Generation (RAG) engine for handbook policy extraction, and a deterministic Academic Logic Engine (ALE) for rule-based academic planning computations — are kept strictly separated. A central Orchestrator routes each request to the correct engine(s). A dedicated Query Understanding (QU) layer parses raw student text into typed, structured queries before the Orchestrator executes them. A Response Composer converts structured engine results into a friendly, citation-aware natural-language reply. Stateful multi-turn conversation is maintained per student via a persistent Session Store, and a Student Context Provider makes each student's academic record available to every component that needs it.

---

## 2. Components

| Component | Main Files | Responsibility | Inputs | Outputs | Notes / Boundaries |
|---|---|---|---|---|---|
| **UI** | `ui/streamlit_app.py` | Student-facing chat interface | User text, student ID (typed in) | HTTP calls to API; rendered answers | Stateless server-side — client holds session_id in Streamlit session state; uses `requests` library |
| **API Layer** | `main.py` | FastAPI application; entry point for all external calls; startup orchestration | HTTP JSON (QueryRequest: student_id, user_text, session_id?) | HTTP JSON (QueryResponse: answer_text, session_id, citations, status) | Mounts CORS wildcard; owns the `/chat` pipeline; 503 guard if startup failed; student_id masked in logs |
| **Query Understanding (QU)** | `gateway/query_understanding.py`, `gateway/qu_prompt.py`, `gateway/qu_preprocessing.py`, `gateway/qu_llm_chain.py`, `gateway/qu_intents.py` | Parse raw student text into a typed, ordered list of StructuredQuery objects | user_text, LastReferenced (from session), recent turn history, optional KG resolver | `list[StructuredQuery]` (each has: intent, entities, params, session_overrides, student_referential_fallback) | Primary path: LLM chain (model-chain with primary + fallback models). Fallback: deterministic rule-based classifier. Post-LLM normalization layer repairs common LLM shape errors. Privacy hard rule: never sends student PII to LLM |
| **Orchestrator** | `gateway/orchestrator.py` | Intent-based dispatch; builds effective student context; routes each SQ to the right engine; wraps results | `list[StructuredQuery]`, `SessionState`, `rule_bundles` (from RAG) | `TurnWrapper` (ordered `list[PerSQResult]` with turn-level status) | One SQ failure does NOT cascade to others. Never calls QU, never writes to session. Has turn-level caches (course_profile_cache, courses_by_track). Routes to 7 domains |
| **KGAdapter** | `adapters/kg_adapter.py` | Thin adapter: translates Orchestrator calls into Neo4j Cypher queries via KG engine | operation name + params dict | Plain dict result (business data or error dict) | 18 supported operations. Also used by QU for entity resolution (via resolver closure injected at startup). Graceful degradation when Neo4j unavailable |
| **RAGAdapter** | `adapters/rag_adapter.py` | Adapter to RAG engine for (a) free-text policy queries and (b) structured rule-bundle extraction at startup | sub_query text (or expected_schema for structured) | `{found, answer, extracted_facts, citations}` or `{data, citations}` or rule bundle dict | student_context intentionally never forwarded to RAG (hard privacy boundary). get_rule_bundles() called once at startup — results cached in `_rule_bundles` dict in main.py |
| **ALEAdapter** | `adapters/ale_adapter.py` | Adapter to ALE engine: maps StudentContext + rule_bundles + kg_data into ALE Pydantic input schemas; calls ALE functions | operation, StudentContext, rule_bundles, kg_data, params | ALE function result dict | Stateless. All rules provided by caller. 6 operations. Handles Pydantic ValidationError → cannot_compute gracefully |
| **Knowledge Graph Engine** | `engines/kg/queries.py`, `engines/kg/neo4j_client.py` | Cypher queries to the graph database for all curriculum, career, and role facts | Neo4j bolt connection + structured params | Plain dicts (course profiles, prerequisites, skills, roles, tracks, alignment scores, recommendations, entity resolution) | Owns the graph data model (Course, Skill, Role, Track nodes and their relationships). Returns structured error dicts, never raises on business failures |
| **RAG Engine** | `engines/rag/rag_core.py`, `engines/rag/retriever.py`, `engines/rag/ingest.py` | Handbook Q&A via hybrid retrieval + LLM extraction | query text (+ optional expected_schema for structured mode) | extracted_facts list + source_documents with page citations | HybridRetriever: 4-step pipeline (vector search → BM25 → RRF merge → cross-encoder rerank). LLM (Groq API) used for fact extraction from retrieved chunks. Source: CIS Handbook PDF, pre-ingested |
| **Academic Logic Engine (ALE)** | `engines/ale/functions/`, `engines/ale/schemas.py`, `engines/ale/utils/grade_resolver.py` | Deterministic academic logic computations (no LLM, no DB) | Typed Pydantic input objects (StudentContext fields + rule_bundles + kg_data) | Typed Pydantic output objects (serialized as dict by adapter) | 6 pure functions: simulate_gpa_forward, solve_target_gpa, check_course_eligibility, run_graduation_audit, generate_semester_plan, generate_graduation_roadmap. Fully stateless |
| **Student Context Provider (SCP)** | `gateway/student_context_provider.py` | Build a StudentContext for any student from the registrar Excel data | student_id → Excel rows | `StudentContext` Pydantic object (or None if not found) | Loaded once at startup (`load_excel()`). Maps program strings to canonical KG track IDs. Computes derived fields: completed/failed/in_progress courses, retake counts, academic level, current semester (inferred from enrollment patterns). "Computer Science" program is flagged as unsupported (no KG track) |
| **Session Manager** | `gateway/session_manager.py` | Manage multi-turn conversation sessions: create, load, update, delete; enforce student ownership; merge session overrides | session_id, student_id, StudentContext, turn data | `SessionState` objects; persistence via Session Store | Ownership check on every load/delete prevents session hijacking. build_effective_context() applies what-if overrides to base StudentContext for Orchestrator |
| **Session Store** | `gateway/session_store/sqlite_store.py`, `gateway/session_store/base.py` | Persist session state as JSON blobs in a relational database | SessionState objects | Stored/retrieved SessionState; summaries for listing | Table: sessions (session_id, student_id, session_name, last_updated, session_blob). session_blob is JSON. Indexed by student_id |
| **Response Composer** | `gateway/response_composer.py` | Convert Orchestrator TurnWrapper into a student-facing natural-language answer | TurnWrapper, user_text, session_id, session_name | `QueryResponse` (answer_text, citations, status) | Never calls KG/RAG/ALE/QU. Never receives raw StudentContext. Two-stage: deterministic narration packet extraction → LLM NLG (primary + fallbacks). Off-script detection: if LLM asks student for data it already has → deterministic fallback. 25+ intent-specific narration extractors |

---

## 3. Conversational Advising Flow

The following traces a full `/chat` request as implemented in `main.py:chat()`.

### Step 1 — Request enters API (`main.py:chat()`, line 126)

The client (UI or direct API caller) sends a `POST /chat` with:
```json
{ "student_id": "22-101172", "user_text": "Can I take C-CS321?", "session_id": "..." }
```

A 503 guard checks that `_orchestrator` and `_composer` are not `None` (startup must have succeeded). The student_id is immediately masked for all log lines.

### Step 2 — Student context loading (`student_context_provider.get_context()`)

`get_context(request.student_id)` looks up the student in the in-memory Pandas DataFrames (loaded from the Excel file at startup). It computes and returns a typed `StudentContext` object containing CGPA, course history, completed/failed/in_progress course lists, academic level, track ID, retake counts, and current semester. If the student is not found, the API returns HTTP 404.

### Step 3 — Session loading/creation (`session_manager.get_or_create_session()`)

With a `session_id` provided: the session is loaded from the SQLite store and the student ownership is verified. If the session belongs to a different student, a new session is silently created (no cross-session leakage). If `session_id` is `None`, a new session is created with a name derived from the first user message. The fresh `StudentContext` always replaces the stored one (ensures live data, not stale).

### Step 4 — Query Understanding (`query_understanding.understand_query()`)

Called with:
- `user_text`: the raw student message
- `last_referenced`: from `session.last_referenced` (course/role/track/skill referenced in previous turns)
- `recent_turns`: last N turns from `session.turn_history` (N configured via `QU_CONTEXT_TURNS`)
- `resolver`: a closure around `KGAdapter.call("resolve_entity", ...)` built at startup

**Internal QU flow:**

1. `preprocess(user_text)` → `PreprocessResult`: detects policy signals, out-of-scope signals, student-referential phrases, override/reset commands, semester mentions, target CGPA, explicit course codes, expected grades.
2. Primary path: `QUModelChain.call()` → sends system prompt + user message to LLM (no student PII). LLM returns a JSON list of StructuredQuery dicts.
3. `_parse_raw_sq()` validates and normalizes each raw dict into a `StructuredQuery`.
4. `_normalize_structured_queries_after_llm()`: repairs common LLM shape issues (entity candidate promotion, D6 focus detection, etc.).
5. Fallback: if LLM is unavailable or all models fail, `_deterministic_fallback()` classifies the query from signals detected in Step 1.

**Entity resolution (step 5 in QU):**

`_resolve_all()` / `_resolve_sq()`: each entity in every StructuredQuery is resolved via the KG `resolve_entity` operation. Natural-language names like "Operating Systems" are resolved to canonical IDs like `C-CS204`. Ambiguous matches produce a `clarification_needed` SQ. Course-info intents that fail entity resolution may fall back to a `search_courses_by_skill` reroute (topic fallback). Skill candidates and entity candidates are tried in priority order before giving up.

Returns: `list[StructuredQuery]`, always non-empty (at minimum one `clarification_needed`).

### Step 5 — Orchestrator execution (`Orchestrator.execute_turn()`)

Called with:
- `sqs`: the list of StructuredQuery from QU
- `session`: the SessionState (including base StudentContext and accumulated SessionOverrides)
- `rule_bundles`: the dict of 8 typed Pydantic rule bundles loaded at startup from RAG

**Internal Orchestrator flow:**

1. Accumulates all per-SQ session overrides for this turn via `_collect_turn_overrides()`. If any SQ issued a `clear` override action, the previous session overrides are discarded.
2. Builds `effective_context` via `build_effective_context(base_context, execution_overrides)`: applies what-if assumptions (assumed-passed/failed courses, added courses) to a copy of the base StudentContext — never mutates the original.
3. Iterates over each StructuredQuery sequentially. For each:
   - Checks forbidden/stale intents.
   - Checks student-context requirement (e.g. ALE intents and student-aware career intents require a valid StudentContext).
   - Dispatches to the correct domain handler (`_exec_plan_semester`, `_exec_d2_course`, `_exec_d3_career`, `_exec_d4_track`, `_exec_policy`, `_exec_student_record`, etc.).
   - Wraps result in a `PerSQResult` with intent, status, data, error info, assumptions flags, citations.
   - Exceptions are caught per-SQ and converted to `engine_error` status; failures do not cascade.
4. Assembles all `PerSQResult` objects into a `TurnWrapper` with a turn-level status (`completed`, `partial_success`, `needs_clarification`, `failed`, `out_of_scope`).

**Adapter calls within Orchestrator:**

- **KG intents (D2, D3, D4):** `self._kg.call(operation, params)` → `KGAdapter.call()` → `queries.py` Cypher function → Neo4j
- **Policy intent (D5):** `self._rag.execute(original_text)` → `RAGAdapter.execute()` → `rag_core.extract_facts()`
- **ALE intents (D1):** `self._ale.call(operation, ctx, bundles, kg_data, params)` — Orchestrator first fetches required KG data (prerequisites, course profiles, courses-by-track) and packages it as `kg_data`, then calls `ALEAdapter.call()` → ALE function
- **Student record (D6):** Orchestrator builds a snapshot dict from the `StudentContext` directly. KG may be called to enrich course names/credits for display (via `_enrich_course_details()`)

### Step 6 — Response Composer (`ResponseComposer.compose()`)

Called with: `user_text`, the `TurnWrapper`, `session_id`, `session_name`.

**Internal Composer flow:**

1. Sorts results by `sq_index` (preserves original query order).
2. For each `PerSQResult`, calls `_extract_packet()`: builds a compact, intent-specific narration dict ("narration packet") containing only the fields the LLM needs to phrase the answer. Raw StudentContext is never in the packet.
3. Attempts LLM NLG (primary → fallback chain): sends `_SYSTEM_PROMPT` + narration packet JSON to the LLM. Strips `<think>` blocks (Qwen3 style). Detects off-script responses (LLM asking student for info it already has) → falls back to deterministic.
4. If LLM unavailable or all models fail: `_deterministic_answer(packets)` generates a plain-text answer using 25+ intent-specific narration branches in `_narrate_intent()`.
5. Citations are merged and deduplicated from all PerSQResult objects.
6. Returns `QueryResponse(session_id, session_name, answer_text, citations, status)`.

### Step 7 — Session update (`session_manager.update_session_after_turn()`)

After the pipeline completes:
- `merge_turn_overrides(sqs)` extracts the accumulated per-SQ overrides from this turn.
- `Orchestrator.extract_last_referenced(sqs)` extracts the first resolved entity (course/role/track/skill) for future anaphora resolution.
- `update_session_after_turn()` appends the turn to `session.turn_history` (user text + composed answer), merges overrides, and merges last_referenced into session state. Persists to SQLite.

### Step 8 — API response

The `QueryResponse` is serialized as JSON and returned to the client:
```json
{
  "session_id": "...",
  "session_name": "Can I take C-CS321?",
  "answer_text": "Yes, you are eligible to take ...",
  "citations": [],
  "status": "ok"
}
```

---

## 4. Engine Responsibilities and Boundaries

### Knowledge Graph Engine (KG)

**Owns:**
- All curriculum facts: course catalogue, credit hours, levels, semester offerings, prerequisites (direct and transitive), tracks each course belongs to.
- All career/role facts: role profiles, skills required per role, roles per track.
- All skill facts: skills taught per course, courses covering a skill.
- Track comparison and recommendation facts.
- Entity resolution: mapping natural-language names/aliases to canonical IDs (course codes, role IDs, track IDs, skill IDs).
- `compute_skill_gap`, `compute_alignment_score`, `estimate_alignment_improvement`, `find_best_matching_roles`: pure graph computations based on curriculum-skill edges and student's completed courses.

**Must NOT own:**
- Any academic policy rules (credit limits, retake caps, GPA thresholds) — those belong to RAG/ALE.
- Any academic decisions (eligible/not eligible, can graduate) — those belong to ALE.
- Student personal data (grades, CGPA, warnings) — those belong to SCP/StudentContext.

### RAG Engine

**Owns:**
- All handbook policy text extraction: graduation requirements, warning thresholds, credit limits, retake caps, honors criteria, summer semester rules, grading scale, student level thresholds.
- Two modes: free-text Q&A (policy_query intent) and structured extraction (rule bundle loading at startup).
- Source citations (handbook page numbers).

**Must NOT own:**
- Student-personal data: RAGAdapter explicitly refuses to forward StudentContext to the RAG engine.
- Academic decisions or course catalogue facts — only extracts what the handbook says.
- Runtime per-query rule reloading: rule bundles are loaded once at startup and cached.

### Academic Logic Engine (ALE)

**Owns:**
- All deterministic academic computations: GPA simulation, target-GPA solving, course eligibility checking (prerequisites + retake rules), graduation audit (credits + CGPA + zero-credit courses + military + semesters), semester planning (eligible courses selection respecting credit limits), graduation roadmap (multi-semester projection).
- ALE receives all inputs externally (StudentContext from SCP, rules from RAG, course data from KG). It contains no database access and no LLM calls.

**Must NOT own:**
- Rule retrieval: rules are injected by the Orchestrator via rule_bundles.
- Course catalogue retrieval: available courses are injected by the Orchestrator via kg_data.
- Natural-language output: ALE returns structured dicts that the Composer phrases.

### Response Composer

**Owns:**
- Natural-language generation only: takes structured narration packets and produces student-friendly text.
- Intent-specific narration extractors: 25+ domain-specific field extraction and formatting functions.
- Off-script detection: ensures the LLM does not ask the student for information the system already has.

**Must NOT own:**
- Any fact derivation, academic rule application, or new data retrieval.
- Direct access to StudentContext, KG, RAG, ALE, or QU.
- Any session state mutation.

### Query Understanding (QU)

**Owns:**
- Intent classification and entity extraction only.
- Session override parsing (what-if assumptions embedded in student messages).
- Entity resolution via KG (the only KG call QU is permitted to make).
- Deterministic fallback classification when LLM is unavailable.

**Must NOT own:**
- Any advising logic or academic computations.
- Any KG business operations (only `resolve_entity` is permitted).
- Student personal data (never sent to any LLM).

### Orchestrator

**Owns:**
- Intent routing: deciding which engine(s) to call for each StructuredQuery.
- Effective context construction: applying session overrides to the base StudentContext.
- Cross-engine data orchestration: fetching KG data required by ALE, packaging it as `kg_data`.
- Per-SQ error isolation: one failure does not cascade.
- Turn-level result assembly and status computation.

**Must NOT own:**
- QU (never calls `understand_query()`).
- Session persistence (never calls `update_session_after_turn()`).
- Natural-language generation (never calls Composer).
- Business logic that belongs to an engine (no academic rule duplication).

---

## 5. Data Sources

| Data Source | Description | Read by |
|---|---|---|
| **Student Excel File** (`data/students_anonymous.xlsx`) | Two sheets: `data` (one row per student: ID, Name, Program, Level, CGPA, warnings, etc.) and `registrations` (one row per course registration attempt: course code, semester, status, grade). Source of all student academic records. | Student Context Provider (SCP) at startup via `load_excel()` |
| **Graph Database (Neo4j)** | Stores the full CIS curriculum knowledge graph: Course nodes, Skill nodes, Role nodes, Track nodes, and edges (prerequisites, teaches, required_by, belongs_to, aligned_with, etc.). Queried per request via Cypher. | KGAdapter → KG Engine (queries.py) |
| **CIS Handbook (PDF → Vector Store + BM25 index)** | The CIS Student Handbook PDF, pre-ingested into a Chroma vector database (child chunks, ~200 tokens) and a BM25 index (parent chunks, ~800 tokens) stored on disk. Used for all policy Q&A and rule bundle extraction. | RAG Engine (retriever.py + rag_core.py) |
| **Entity Aliases JSON** (`engines/kg/data/entity_aliases.json`) | Alias mappings used by the KG entity resolver to match natural-language course/role/skill names to canonical graph IDs. | KG Engine (queries.py — `q_resolve_entity`) |
| **Session Database (SQLite)** | A SQLite file (`pathfinder_sessions.db` by default, path configurable). Table `sessions` stores one row per session with a JSON blob of the full `SessionState` (turn history, overrides, last_referenced, student_context snapshot). | Session Manager → SQLiteSessionStore |
| **Environment Variables / `.env`** | API keys, model names, database URIs, timeouts, feature flags. See `.env.example` for full list. Key vars: `NEO4J_*`, `GROQ_API_KEY`, `LLM_*`, `QU_*`, `COMPOSER_*`, `SESSION_DB_PATH`, `PATHFINDER_TRACE`, `APP_ENV`. | All components read relevant vars at startup/init time |
| **Rule Bundles (in-memory, sourced from RAG at startup)** | 8 typed Pydantic objects extracted from the handbook at startup and held in `_rule_bundles` dict in `main.py`. Bundles: `grading_scale_rules`, `graduation_requirement_rules`, `academic_warning_rules`, `honors_rules`, `credit_limit_rules`, `retake_rules`, `summer_semester_rules`, `student_level_rules`. | Orchestrator passes them to ALEAdapter on every ALE call; also used by Orchestrator directly for academic_warning_rules |
| **Advisor-Student Mapping** | No advisor-student mapping data source exists in the current codebase. This feature is not implemented. | N/A |

---

## 6. API and UI Boundaries

### Implemented API Endpoints

| Method | Path | Description | Auth / Guard |
|---|---|---|---|
| `POST` | `/chat` | Main chat endpoint. Accepts `QueryRequest` (student_id, user_text, session_id?). Runs the full QU → Orchestrator → Composer pipeline. Returns `QueryResponse`. | 503 if startup failed; 404 if student not found |
| `GET` | `/sessions/{student_id}` | List all sessions for a student (session_id, session_name, last_updated). Returns `StudentSessionsResponse`. | None (caller must supply correct student_id) |
| `GET` | `/students/{student_id}/sessions/{session_id}/history` | Return turn history for a session. Ownership check: returns 404 if session belongs to a different student. Returns `SessionHistoryResponse`. | Student ownership verified |
| `DELETE` | `/students/{student_id}/sessions/{session_id}` | Delete a specific session. Ownership verified before deletion. Returns 404 if not found or wrong student. | Student ownership verified |
| `DELETE` | `/dev/students/{student_id}/sessions` | **DEV ONLY.** Delete all sessions for a student. Requires `APP_ENV=dev` or `DEV_MODE=true`; returns 403 otherwise. | Dev mode gate |
| `DELETE` | `/dev/sessions` | **DEV ONLY.** Delete ALL sessions globally. Same dev gate. Use with extreme caution. | Dev mode gate |
| `GET` | `/health` | Health check. Returns `{"status": "ok", "service": "PathFinder"}`. | None |
| `GET` | `/session/{session_id}/history` | **DEPRECATED.** Returns HTTP 410 Gone. Clients must use the student-scoped endpoint above. | N/A |

CORS is configured with `allow_origins=["*"]` (all origins allowed). This is intentional for development; production deployment should restrict this.

### UI Integration (Streamlit)

The UI (`ui/streamlit_app.py`) is a single-page Streamlit app. It operates as a pure HTTP client to the FastAPI backend:

- **Login**: Student types their student ID into a sidebar text input. No password or authentication.
- **Session list**: On login, fetches `GET /sessions/{student_id}` to show past sessions in the sidebar. User can click to load any session.
- **Load history**: When a session is selected, fetches `GET /students/{student_id}/sessions/{session_id}/history` to populate the message display.
- **Chat**: User types a message and submits; the app calls `POST /chat` with the current `student_id` and `session_id`. The returned `session_id` is stored in client-side session state for subsequent turns.
- **Delete session**: A button calls `DELETE /students/{student_id}/sessions/{session_id}`.

---

## 7. Architecture Diagram Recommendation

Three diagrams are recommended. Below each, the nodes and directed arrows are specified.

### Diagram 1 — High-Level System Architecture

This diagram shows the major deployment components and how data flows between them at the system level.

**Nodes:**
- Student Browser / UI (Streamlit)
- API Server (FastAPI — main.py)
- Query Understanding Layer
- Orchestrator
- Response Composer
- KG Adapter → Knowledge Graph (Graph DB)
- RAG Adapter → RAG Engine → Handbook Vector Store
- ALE Adapter → Academic Logic Engine (in-process, no DB)
- Student Context Provider → Registrar Data (Excel / DB)
- Session Manager → Session Database
- Shared LLM Provider (external, OpenAI-compatible API)
- Groq LLM API (external, for RAG engine)

**Arrows:**
- Student Browser → API: `POST /chat`
- Student Browser ← API: `QueryResponse`
- API → Student Context Provider: `get_context(student_id)`
- API → Session Manager: `get_or_create_session()`
- API → Query Understanding: `understand_query(user_text, last_referenced, recent_turns, resolver)`
- Query Understanding → Shared LLM Provider: LLM call (intent + entity extraction)
- Query Understanding → KG Adapter: `resolve_entity()` (entity resolution only)
- API → Orchestrator: `execute_turn(sqs, session, rule_bundles)`
- Orchestrator → KG Adapter: domain 2/3/4 intent calls
- Orchestrator → RAG Adapter: `execute(policy text)` for domain 5
- Orchestrator → ALE Adapter: domain 1 ALE function calls
- KG Adapter → Graph DB: Cypher queries
- RAG Adapter → RAG Engine → Handbook Vector Store: retrieval + extraction
- RAG Engine → Groq LLM API: fact extraction LLM call
- ALE Adapter → ALE Engine: pure function call (in-process)
- API → Response Composer: `compose(user_text, turn, session_id)`
- Response Composer → Shared LLM Provider: NLG LLM call
- API → Session Manager: `update_session_after_turn()`
- Session Manager → Session Database: read/write

---

### Diagram 2 — Request Flow and Pipeline (Sequence / Data Flow)

This diagram shows the ordered pipeline within a single `/chat` request.

**Nodes (in execution order):**
1. Client
2. FastAPI `/chat`
3. Student Context Provider
4. Session Manager (load)
5. Query Understanding
6. KG Entity Resolver (via KGAdapter)
7. Orchestrator
8. [KG / RAG / ALE / StudentContext snapshot] per intent
9. Response Composer
10. LLM NLG (or deterministic fallback)
11. Session Manager (save)
12. FastAPI response

**Arrows (ordered):**
- Client → FastAPI: POST /chat (student_id, user_text, session_id?)
- FastAPI → SCP: get_context(student_id) → StudentContext
- FastAPI → Session Manager: get_or_create_session() → SessionState
- FastAPI → QU: understand_query(user_text, last_referenced, recent_turns, resolver)
- QU → LLM: classify intent + extract entities
- QU → KG resolver: resolve entities (course names → codes, roles, tracks, skills)
- QU → FastAPI: list[StructuredQuery]
- FastAPI → Orchestrator: execute_turn(sqs, session, rule_bundles)
- Orchestrator → [KGAdapter | RAGAdapter | ALEAdapter]: per SQ dispatch
- Orchestrator → FastAPI: TurnWrapper
- FastAPI → Composer: compose(user_text, TurnWrapper, session_id)
- Composer → LLM: NLG call (narration packet — no student PII)
- Composer → FastAPI: QueryResponse
- FastAPI → Session Manager: update_session_after_turn()
- FastAPI → Client: QueryResponse (answer_text, citations, status)

---

### Diagram 3 — Data / Source Architecture

This diagram shows where data lives and which components read/write it.

**Nodes (data stores):**
- Registrar Excel (students_anonymous.xlsx)
- Graph Database (Neo4j — courses, skills, roles, tracks)
- Handbook Vector Store (Chroma DB + BM25 index — CIS handbook PDF)
- Session Database (SQLite)
- Environment Config (.env)
- In-memory rule_bundles dict (loaded at startup from RAG)

**Nodes (components that access data):**
- Student Context Provider
- KG Engine / KGAdapter
- RAG Engine / RAGAdapter
- ALE Engine / ALEAdapter
- Session Manager / SQLiteSessionStore
- All components (config via .env)

**Arrows:**
- Student Context Provider ← Registrar Excel: read at startup, holds in Pandas DataFrames
- KGAdapter → Graph DB: Cypher read per request
- RAGAdapter → Handbook Vector Store: retrieval read per policy query
- RAGAdapter → Handbook Vector Store: structured extraction at startup (rule bundles)
- RAGAdapter → in-memory rule_bundles: writes at startup; Orchestrator reads per ALE call
- Session Manager → Session Database: read/write per request
- ALE Engine ← Orchestrator: receives rule_bundles + kg_data as function arguments (no DB access)
- All components ← .env: read at init time

---

## 8. Why Each Component Exists (Architectural Rationale)

### Why an Adapter Layer (KGAdapter, RAGAdapter, ALEAdapter)?

The adapters decouple the Orchestrator from the engines' internal APIs. The Orchestrator calls `kg.call("get_course_profile", {...})` — it does not know about Cypher, Neo4j drivers, or graph data structures. Similarly, `ale.call("run_graduation_audit", ctx, bundles, kg_data, params)` hides all Pydantic schema mapping, rule parsing, and fallback logic from the Orchestrator. This means:
- Any engine can be replaced (e.g., Neo4j → a different graph DB) without touching the Orchestrator.
- Logging, error normalization, and timeout handling live in one place per engine.
- The adapter boundary enforces the contract: the Orchestrator can never accidentally call an engine operation that doesn't exist.

### Why the Orchestrator is structured the way it is (intent-based dispatch, not engine-based)?

The Orchestrator dispatches by **intent** (e.g., `plan_semester`, `policy_query`), not by which engine is involved. This is deliberate:
- An intent always maps to exactly one engine (or student context snapshot), even if that mapping changes in the future.
- The student's query may produce multiple StructuredQuery objects (multi-intent turn); the Orchestrator executes them as independent units and isolates failures.
- Effective context construction (applying what-if overrides) happens once per turn, not per engine call.
- The Orchestrator is the only place where KG data is fetched and packaged as `kg_data` for ALE, so ALE stays engine-agnostic.

### Why Query Understanding is a separate layer?

QU separates **understanding** from **execution**. It:
- Isolates LLM calls for classification from engine calls for advising (privacy boundary: student PII never leaves QU).
- Provides a deterministic fallback so the system can still classify simple queries if the LLM is unavailable.
- Handles session context (anaphora: "what about that course?" resolved via `last_referenced`) without exposing that logic to the Orchestrator.
- Normalizes and resolves entities before the Orchestrator ever sees them, so the Orchestrator only receives canonical IDs.

### Why the Response Composer is a separate layer?

The Composer separates **data retrieval** from **narration**. It:
- Receives only a safe narration packet (no raw student data) so the LLM never has access to sensitive records.
- Provides a full deterministic fallback (25+ intent-specific narration branches) so the system can answer even when the LLM is down.
- Detects "off-script" LLM responses (asking the student for information the system already has) and falls back to the deterministic path.
- Centralizes all display formatting rules (course label format, track display names, skill ID to readable name, etc.).

### Why the Session Manager / Session Store?

Session state enables multi-turn conversation:
- `last_referenced` tracks the most recently mentioned course/role/track/skill so pronouns and follow-up questions can be resolved correctly by QU.
- `overrides` (accumulated what-if assumptions) persist across turns so a student can say "assume I passed X" and subsequent questions respect that assumption until explicitly cleared.
- `turn_history` is fed back to QU as recent context so the LLM can understand follow-up queries.
- The ownership check (student A cannot access student B's session) is a security boundary enforced at every session load and delete operation.

### Why the Student Context Provider is a separate module?

The SCP is the single authoritative source of student academic data. It:
- Centralizes the complex logic of computing derived fields (completed/failed/in_progress courses, current semester inference from enrollment patterns, retake counts, program-to-track mapping).
- Is called once per request at the API layer and produces a clean `StudentContext` Pydantic object that all downstream components can trust.
- Ensures no component directly parses Excel rows — they all receive the structured `StudentContext`.

### Why ALE uses stateless pure functions?

ALE contains computations that must be deterministic and auditable: a student's graduation eligibility check must not depend on any internal state or stale cache. By:
- Accepting all inputs as explicit typed arguments (no globals, no DB calls)
- Returning typed Pydantic output objects
- Using `rule_bundles` injected by the caller

ALE functions are straightforwardly testable, reproducible, and replaceable. All 6 ALE functions have dedicated test suites with both synthetic and real-record tests.

### Why does RAG load rule bundles at startup rather than per request?

The 8 rule bundles require approximately 8–12 LLM calls (some bundles use two-query approaches for better coverage). This would add ~20–30 seconds to every request if done live. Loading once at startup amortizes this cost. The tradeoff is that rule bundle values are fixed until the server restarts. Since the CIS handbook changes infrequently (at most once per academic year), this is an acceptable tradeoff.

---

## 9. Final Architecture Statement Draft

*For use in thesis, presentation, and supervisor documentation:*

PathFinder follows a decoupled multi-engine architecture designed to provide accurate, context-aware academic advising while maintaining strict separation between knowledge retrieval, rule-based computation, and natural-language generation. A student's natural-language query passes through a Query Understanding layer that classifies intent and resolves named entities against a curriculum knowledge graph, producing a structured internal representation without exposing any student personal information to an external language model. A central Orchestrator then dispatches each structured query to the appropriate specialist engine: a Knowledge Graph engine for curriculum, career, and role facts; a Retrieval-Augmented Generation engine for university handbook policy extraction with source citations; or a deterministic Academic Logic Engine for computations such as GPA simulation, course eligibility checking, semester planning, and graduation auditing. The Orchestrator assembles results from multiple engines within a single turn and enforces strict isolation — one engine's failure never cascades to another. A Response Composer converts the structured engine results into fluent, student-facing natural language via a language model, with a full deterministic fallback for system resilience. Multi-turn conversational continuity is maintained through a persistent session store that tracks referenced entities, accumulated what-if assumptions, and turn history across multiple interactions per student. The system's architecture ensures that each component owns exactly one well-defined responsibility, making individual components independently testable, replaceable, and auditable.
