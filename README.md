# PathFinder Integration

PathFinder is an academic advising assistant for Egyptian University of Informatics (EUI). It combines structured curriculum and career data, handbook retrieval, student transcript context, and rule-based academic logic behind a single chat interface.

The project is split into a FastAPI backend and a Streamlit frontend. A student logs in with a student ID, asks advising questions in natural language, and the system routes the request to the most suitable engine:

- Knowledge Graph for curriculum, tracks, skills, and career-role relationships
- RAG for handbook and policy questions
- Academic Logic Engine (ALE) for eligibility checking, graduation audit, semester planning, graduation roadmap generation, GPA simulation, and target GPA solving
- LLM-based query understanding and response composition to make the experience conversational

## What The Project Does

PathFinder supports several advising workflows:

- Course exploration: course profile, credits, description, level, prerequisites, and career-focus classification
- Skill exploration: what a course teaches and which courses teach a given skill
- Career guidance: role profiles, role-track fit, best matching roles, skill gaps, and focus-course recommendations for a target role or track
- Track guidance: track overviews, comparisons, and recommendations
- Policy Q&A: handbook-based answers with citations
- Student-aware advising: uses the logged-in student's academic context from the Excel dataset
- Eligibility checking: whether a student can take a specific course under a given attempt type
- Graduation audit: checks all graduation requirements and honors eligibility in one call
- Semester planning: generates multiple plan variants (recommended, lighter load, level-focused) for a target semester
- Graduation roadmap: builds a full semester-by-semester plan from current standing to projected graduation with simulated GPA
- GPA simulation: projects CGPA forward given hypothetical grades, with retake-cap enforcement
- Target GPA solving: determines the grades needed across planned courses to reach a target CGPA, with multi-semester projection and personalized per-course targets
- Session-based chat: keeps conversation history and resolves follow-up references like "that course" or "that track"; supports per-session course and role overrides

## Architecture

### 1. API Layer

`main.py` exposes the backend service using FastAPI:

- `POST /chat` handles user questions
- `GET /sessions/{student_id}` returns previous chat sessions for a student
- `GET /session/{session_id}/history` returns conversation history
- `GET /health` returns a basic health response

### 2. Gateway Layer

The `gateway/` package coordinates the system:

- `query_understanding.py`: classifies the question into an intent and engine pattern
- `orchestrator.py`: routes the request to KG, RAG, ALE, or mixed execution
- `response_composer.py`: turns raw engine output into a user-friendly answer
- `student_context_provider.py`: loads student data from Excel and builds a normalized `StudentContext`; computes per-course retake counts, lifetime improve-retake totals, completed regular semesters (Fall/Spring only, all-withdrawn semesters excluded), and zero-credit P-grade course lists; applies best-outcome resolution when a student has multiple attempts at the same course; handles Con grades (graduation project spanning semesters), I grades (incomplete), and withdrawal exclusion
- `session_manager.py`: manages sessions and conversation history, persisted to SQLite via the `gateway/session_store` package; exposes a context-windowed turn history to query understanding (controlled by `QU_CONTEXT_TURNS`); tracks the last-referenced entity (course, role, track) per session for follow-up resolution; maintains per-session course and role overrides with three merge strategies (`accumulate`, `replace`, `clear`); builds an effective student context by merging `assumed_done` override courses into the student's completed-course list; supports `delete_session`

### 3. Engine Layer

The `engines/` package contains the reasoning backends:

- `engines/kg/`: Neo4j-backed knowledge graph queries for courses, tracks, skills, and roles; includes a multi-step entity resolver that maps natural-language names to graph IDs
- `engines/rag/`: handbook retrieval pipeline using Chroma, BM25, and a cross-encoder reranker
- `engines/ale/`: academic logic modules for eligibility checking, graduation audit, semester planning, graduation roadmap generation, GPA simulation, and target GPA solving

### 4. Adapter Layer

The `adapters/` package gives the orchestrator a clean interface for each engine:

- `KGAdapter`
- `RAGAdapter`
- `ALEAdapter`

### 5. UI Layer

`ui/streamlit_app.py` provides a simple student-facing chat UI with:

- Student ID login
- New chat / session history selection
- Chat-style message flow
- Citation display for handbook answers

## How A Request Flows

1. The student sends a message from the Streamlit UI.
2. FastAPI receives the request on `POST /chat`.
3. The system loads or reuses the student's session and academic context.
4. **Query Understanding** (`understand_query`) classifies the message into one or more structured intents, resolving follow-up references (e.g. "it", "that course") using session context.
5. **Orchestrator** (`execute_turn`) routes each intent to the matching backend and returns a `TurnWrapper` with ordered `PerSQResult` objects:
   - KG for structured curriculum/career questions
   - RAG for handbook/policy questions
   - ALE for academic decision logic
   - Mixed for questions that need both structured data and handbook context
6. **Response Composer** (`compose`) narrates the `TurnWrapper` into a student-facing `QueryResponse`. It tries an LLM model chain (primary then fallbacks) and falls back to deterministic narration if all models fail.
7. The `QueryResponse` (fields: `answer_text`, `citations`, `status`, `session_id`, `session_name`) is returned to the UI and the composed answer is stored in session history.

## Data Sources

This project currently depends on several local and external data sources:

- `data/students_anonymous.xlsx`
  - `data` sheet for student profile fields
  - `registrations` sheet for transcript and registration history
- `engines/rag/CIS_Handbook.md`
  - source document used to build the RAG index
- `engines/rag/chroma_db/` and `engines/rag/chunks.pkl`
  - generated retrieval artifacts
- Neo4j database
  - stores the curriculum / skills / role knowledge graph
- `engines/kg/data/entity_aliases.json`
  - alias and ambiguous-term table used by the KG entity resolver; maintained manually

## RAG Pipeline

The handbook QA engine uses a hybrid retrieval pipeline:

- Parent/child chunking during ingestion
- Dense vector retrieval with `BAAI/bge-small-en-v1.5`
- Sparse retrieval with BM25
- Reciprocal rank fusion
- Cross-encoder reranking with `cross-encoder/ms-marco-MiniLM-L-6-v2`

You only need to rebuild the index when the handbook source changes.

## Knowledge Graph Engine

The KG engine exposes 19 operations across four query groups:

- **Course catalogue (A2)**: course profile, prerequisites (direct or full recursive tree; non-course constraints are stored as `PrerequisiteConstraint` nodes), skills taught by a course, course search by skill name, course focus classification (primary track/skill-category focus of a course), and focus-course recommendations for a target track or role (courses the student has not yet taken that teach the most relevant skills)
- **Career role exploration (B1)**: role profiles with weighted required skills, and roles reachable through a track's courses and skills
- **Skill gap and alignment (B2)**: skill gap analysis, weighted alignment scoring, gap-closing course recommendations, alignment improvement estimation for planned courses, and full role ranking by alignment
- **Track guidance (B3)**: track overview (courses, skills, supported roles), side-by-side track comparison, track recommendations for a given role or skill, and full course list for a track with prerequisites included (used by ALE for semester planning and graduation roadmap generation)

Skills carry a numeric weight that drives all alignment calculations. Weights map to three tiers: `core` (≥ 0.8), `supporting` (≥ 0.6), and `optional` (< 0.6).

The engine also includes a `resolve_entity` operation that maps a natural-language name to a graph ID for any entity type (course, role, track, skill). The resolver runs a six-step pipeline — input validation, exact ID match, exact normalized name match, alias lookup, explicit ambiguous-term lookup, partial name match — and loads its alias table from `engines/kg/data/entity_aliases.json`.

## Academic Logic Engine

The ALE exposes 6 operations, all driven by rule bundles injected at runtime from RAG (no rules are hardcoded in the engine):

- **check_course_eligibility**: checks whether a student can register for a course under a given attempt type (`first_attempt`, `failed_retake`, `improve_retake`); validates prerequisites, credit thresholds, and retake caps; returns `eligible`, `not_eligible`, `already_completed`, `in_progress`, or `retake_cap_exceeded`
- **run_graduation_audit**: evaluates all graduation requirements (credits, CGPA, semester count, military training, zero-credit courses) and computes honors eligibility based on full transcript history; returns per-check breakdowns and next-step guidance
- **generate_semester_plan**: generates two or three plan variants (e.g. Recommended, Lighter Load, Level Focused) for a single target semester (Fall / Spring / Summer); respects CGPA-bracket credit caps, retake priority, and student level
- **generate_graduation_roadmap**: builds a full semester-by-semester projection from current standing to projected graduation; simulates CGPA after each semester; detects non-course blockers (CGPA, military, zero-credit); supports accelerated (summer) and max-credits modes
- **simulate_gpa_forward**: projects CGPA forward given hypothetical grades for planned courses; enforces retake caps and handles grade-point replacement vs addition; returns per-course breakdowns and applied grade overrides
- **solve_target_gpa**: determines the required grade average across planned courses to reach a target CGPA; when impossible in a single semester, generates a multi-semester projection; produces a personalized per-course grade distribution based on prerequisite history

Rule bundles consumed by ALE operations:

- `grading_scale` — letter-to-grade-points mapping and percentage ranges
- `retake_rules` — failed retake caps, improve-retake caps and limits
- `credit_limit_rules` — CGPA-bracket credit maxima and minimums per semester
- `graduation_rules` — total credits, minimum CGPA, semester count bounds, and auxiliary requirements
- `warning_rules` — warning thresholds and dismissal conditions
- `honors_rules` — honors eligibility criteria
- `summer_rules` — summer course count limits
- `student_level_rules` — credit-hour thresholds for Freshman / Sophomore / Junior / Senior classification

## Project Structure

```text
PathFinder_Integration/
|- adapters/              # Thin wrappers around KG, RAG, and ALE
|- data/                  # Student dataset
|- engines/
|  |- ale/                # Academic logic engine
|  |- kg/                 # Neo4j queries and client
|  |- rag/                # Handbook ingestion and retrieval
|- gateway/               # Routing, context, session, response composition
|  |- session_store/      # SessionStore ABC and SQLiteSessionStore implementation
|- ui/                    # Streamlit frontend
|- main.py                # FastAPI entrypoint
|- requirements.txt       # Backend dependencies
|- README.md
```

## Detailed Project Structure

```text
PathFinder_Integration/
|- main.py                                          # FastAPI entrypoint; exposes /chat, /sessions, /session, /health
|- requirements.txt                                 # Backend Python dependencies
|- .env.example                                     # Environment variable template
|- .env
|- pytest.ini                                       # Pytest configuration
|- .gitignore
|- PathFinder_Orchestrator_Handoff.md               # Orchestrator design handoff doc
|- PathFinder_Orchestrator_Phases_1_6_Locked_Design.md  # Locked orchestrator design (phases 1–6)
|- PathFinder_Query_Understanding_Locked_Design.md  # Locked QU design doc
|
|- adapters/                          # Thin wrappers giving the orchestrator a clean engine interface
|  |- kg_adapter.py                   # KGAdapter: wraps all KG query operations
|  |- rag_adapter.py                  # RAGAdapter: wraps handbook retrieval
|  |- ale_adapter.py                  # ALEAdapter: wraps all ALE academic logic operations
|
|- data/
|  |- students_anonymous.xlsx         # Student dataset (profile + transcript/registration history)
|
|- engines/
|  |- ale/                            # Academic Logic Engine
|  |  |- schemas.py                   # ALE input/output Pydantic schemas
|  |  |- functions/
|  |  |  |- check_course_eligibility.py     # Eligibility check for a course + attempt type
|  |  |  |- run_graduation_audit.py         # Full graduation requirements + honors check
|  |  |  |- generate_semester_plan.py       # Multi-variant semester plan generator
|  |  |  |- generate_graduation_roadmap.py  # Full semester-by-semester graduation roadmap
|  |  |  |- simulate_gpa_forward.py         # CGPA projection given hypothetical grades
|  |  |  |- solve_target_gpa.py             # Grade targets needed to reach a CGPA goal
|  |  |- utils/
|  |     |- grade_resolver.py               # Shared grade-point and retake resolution utilities
|  |
|  |- kg/                             # Knowledge Graph engine (Neo4j)
|  |  |- neo4j_client.py              # Neo4j connection and driver management
|  |  |- queries.py                   # All 19 KG operations (courses, skills, roles, tracks, resolver)
|  |  |- cypher/
|  |  |  |- load.cypher               # Cypher script to load graph data from CSVs into Neo4j
|  |  |  |- verify.cypher             # Cypher script to verify graph integrity after loading
|  |  |- data/
|  |  |  |- courses.csv               # Course nodes
|  |  |  |- course_skill.csv          # Course→Skill edges with weights
|  |  |  |- course_track.csv          # Course→Track membership edges
|  |  |  |- prerequisites.csv         # Course→Course prerequisite edges
|  |  |  |- roles.csv                 # Career role nodes
|  |  |  |- role_skill.csv            # Role→Skill edges with weights
|  |  |  |- skills.csv                # Skill nodes
|  |  |  |- tracks.csv                # Track nodes
|  |  |  |- entity_aliases.json       # Alias + ambiguous-term table for the KG entity resolver
|  |  |- Original Data source/
|  |     |- Course Catalogue_Correct Version.xlsx   # Raw source used to build the KG CSVs
|  |
|  |- rag/                            # RAG handbook engine
|     |- ingest.py                    # Index builder: chunks handbook, builds Chroma + BM25 index
|     |- rag_core.py                  # Hybrid retrieval pipeline (dense + BM25 + cross-encoder reranker)
|     |- retriever.py                 # Retriever interface used by the RAG adapter
|     |- CIS_Handbook.md              # Source handbook document
|     |- chunks.pkl                   # Generated BM25/parent-chunk artifact (rebuilt by ingest.py)
|     |- chroma_db/                   # The vector embeddings DB for the handbook
|        |- chroma.sqlite3
|
|- gateway/                           # Routing, query understanding, session, and response composition
|  |- llm_client.py                   # Shared OpenAI-compatible LLM client with model-chain fallback
|  |- query_understanding.py          # QU entrypoint: orchestrates preprocessing, LLM chain, and keyword fallback
|  |- qu_intents.py                   # Intent and engine-pattern definitions (all recognized intents)
|  |- qu_llm_chain.py                 # LLM-based intent classification chain with retry/fallback logic
|  |- qu_preprocessing.py             # Input normalization and follow-up reference resolution
|  |- qu_prompt.py                    # QU system and user prompt templates
|  |- orchestrator.py                 # Routes intents to KG / RAG / ALE; assembles TurnWrapper
|  |- response_composer.py            # Narrates TurnWrapper into a student-facing QueryResponse
|  |- session_manager.py              # Session lifecycle, context window, entity tracking, course/role overrides
|  |- student_context_provider.py     # Loads student data from Excel; builds normalized StudentContext
|  |- utils.py                        # Shared utilities (current semester label, etc.)
|  |- models/
|  |  |- schemas.py                   # Shared Pydantic models (QueryRequest, QueryResponse, TurnWrapper, etc.)
|  |- session_store/
|     |- __init__.py                  # Package init; exports SessionStore and SQLiteSessionStore
|     |- base.py                      # SessionStore abstract base class (ABC)
|     |- sqlite_store.py              # SQLiteSessionStore: persists sessions and history to SQLite
|     |- pathfinder_sessions.db       # The SQLite DB that holds the sessions
|
|- ui/
|  |- streamlit_app.py                # Streamlit student-facing chat frontend
|  |- requirements.txt                # UI-specific Python dependencies (Streamlit, requests)
|
|- tests/
   |- conftest.py                     # Shared pytest fixtures
   |- test_main.py                    # API endpoint tests (/chat, /sessions, /health)
   |- test_orchestrator.py            # Orchestrator routing and intent-dispatch tests
   |- test_query_understanding.py     # QU classification and fallback tests
   |- test_rag_adapter.py             # RAG adapter tests
   |- test_kg_adapter.py              # KG adapter tests
   |- test_response_composer.py       # Response composer narration tests
   |- test_session_manager.py         # Session manager lifecycle and override tests
   |- test_student_context_provider.py  # Student context loading and normalization tests
   |- test_semester_offering_filter.py  # Semester offering filter logic tests
   |- acceptance_orchestrator.py      # End-to-end orchestrator acceptance tests
   |- acceptance_qu.py                # End-to-end QU acceptance tests
   |- smoke_test_qu.py                # QU smoke tests (live LLM call)
   |- smoke_test_ale_adapter.py       # ALE adapter smoke tests
   |- rag_manual_test.py              # Manual RAG retrieval test script
```

## Requirements

- Python environment with the packages in `requirements.txt`
- Neo4j instance populated with the knowledge graph
- An OpenAI-compatible LLM endpoint for query understanding / response composition
- Student dataset Excel file present at `data/students_anonymous.xlsx`

## Configuration

The code reads configuration from `.env`. Copy `.env.example` to `.env` and fill in the values.

**Shared LLM client** (used by Query Understanding and Response Composer):

- `LLM_PROVIDER` — LLM provider label (e.g. `groq`)
- `LLM_BASE_URL` — OpenAI-compatible endpoint base URL
- `LLM_API_KEY` — API key for the LLM provider
- `LLM_MODEL` — default model name (overridden per component below)
- `LLM_TIMEOUT_SECONDS` — request timeout in seconds (default: `30`)

**Query Understanding model chain** (QU tries models in order; falls back to keyword matching):

- `QU_PRIMARY_MODEL` — primary model for intent classification (default: `llama-3.3-70b-versatile`)
- `QU_FALLBACK_MODELS` — comma-separated fallback models tried on timeout, 429, or bad JSON

**Response Composer model chain** (Composer tries models in order; falls back to deterministic narration):

- `COMPOSER_USE_LLM` — set to `false` to skip LLM and always use deterministic fallback (useful in CI)
- `COMPOSER_PRIMARY_MODEL` — primary model for answer narration (default: `qwen/qwen3-32b`)
- `COMPOSER_FALLBACK_MODELS` — comma-separated fallback models tried on failure

**Knowledge Graph**:

- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`

**RAG** (Groq-based generator used for rule bundle extraction at startup):

- `GROQ_API_KEY`
- `GROQ_MODEL`

**Session and UI**:

- `PATHFINDER_API_URL` — backend URL used by the Streamlit UI (default: `http://localhost:8000`)
- `SESSION_DB_PATH` — path to the SQLite session file (default: `pathfinder_sessions.db`)
- `QU_CONTEXT_TURNS` — number of recent turns passed to QU for context (default: `5`)

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure the environment:

- Edit `.env`
- Add your `LLM_API_KEY`
- Set the rest of the LLM / Neo4j variables as needed for your environment

Place the student dataset:

- Ensure `data/students_anonymous.xlsx` is present

Build the RAG index the first time, or whenever the handbook changes:

```bash
python engines/rag/ingest.py
```

Run the backend: (It also shows the loggings)

```bash
python -m uvicorn main:app --reload
```

Run the UI from a separate terminal:

```bash
python -m streamlit run ui/streamlit_app.py
```

## Example Questions

**Policy and handbook:**

- "What is the grading scale at EUI?"
- "What is the minimum GPA to stay enrolled?"
- "What is the retake policy for failed courses?"
- "What is the withdrawal deadline?"

**Course information:**

- "Tell me about C-CS301"
- "What are the prerequisites for C-AI421?"
- "What skills does Deep Learning teach?"
- "Which courses teach machine learning?"

**Academic decisions (student-aware):**

- "Can I take C-CS401 now?"
- "Am I eligible to register for next semester?"
- "What is my current GPA?"
- "Can I graduate this semester?"
- "What courses do I still need to graduate?"
- "Give me my graduation roadmap"
- "What courses can I take next semester?"

**Career and track guidance:**

- "What roles can I get with the AI track?"
- "What skills does a Machine Learning Engineer need?"
- "Tell me about the Data Science track"

**Student record:**

- "What is my study status?"
- "What program am I enrolled in?"
- "What level am I in?"

**Session chaining (follow-ups):**

- "Tell me about C-CS301" → "What are its prerequisites?" → "Can I take it?"

## Current Limitations

These are worth knowing if you continue developing the project:

- Sessions are persisted in a local SQLite file (`SESSION_DB_PATH`). Horizontal scaling or a shared remote store is not yet supported.
- The current semester label is derived dynamically from the system date in `gateway/utils.py` (`get_current_semester`). There is no administrative override if the academic calendar differs from the calendar mapping (Sep–Jan → Fall, Feb–Jun → Spring, Jul–Aug → Summer).
- Student login is based only on IDs found in the Excel sheet.
- The backend assumes the student Excel file has the expected sheet names and columns.
- Semester planning and graduation roadmap both receive the available course list from the KG (`get_courses_by_track`). The orchestrator must populate `kg_data["available_courses"]` correctly; if it passes an empty list, ALE will return `no_eligible_courses`.
- GPA simulation and target GPA solving are implemented in ALE, but the chat flow does not yet gather a rich simulation scenario (e.g. planned courses with attempt types and old grades) automatically from the conversation.
- The frontend is a lightweight internal UI and does not include authentication beyond student ID entry.
- The `delete_session` operation exists in the session manager but is not exposed via any API endpoint.
- Rule bundle loading at startup calls the Groq API 11 times with a 2-second inter-call delay. On a cold start this takes roughly 25–35 seconds. `student_level_rules` is the most rate-limit-sensitive bundle and may fail on the first attempt if Groq returns a 429; a backend restart resolves it.

## Troubleshooting

**Backend fails to start / rule bundles not loaded:**
Run `uvicorn main:app` and check the startup log. The RAG retriever must initialise before rule bundles are loaded. If you see `retriever not ready at import time`, the `engines/rag/chroma_db/` or `engines/rag/chunks.pkl` artifacts are missing — run `python engines/rag/ingest.py` first.

**Rule bundle partial load (e.g. `student_level_rules` fails with 429):**
This is a Groq rate-limit transient error at startup. The bundle loads as `None`; the ALE will use a safe default for that bundle. Restart the backend after a few seconds to retry.

**Answer text starts with `<think>` tag:**
Reasoning models (Qwen3 and similar) emit chain-of-thought in `<think>` blocks. The Composer strips these automatically. If you see them in responses, ensure `gateway/response_composer.py` is up to date.

**Neo4j unavailable:**
The KG adapter degrades gracefully: KG-dependent intents return `kg_unavailable` and the Composer narrates the error. The system still serves policy and record queries.

**Streamlit cannot reach backend:**
Set `PATHFINDER_API_URL=http://127.0.0.1:8000` in `.env` (or the host:port where the backend is running).

## Development Notes

- FastAPI and Streamlit are run as separate processes.
- Query understanding and response composition both use the shared `gateway/llm_client.py` with separate model chains controlled by `QU_*` and `COMPOSER_*` env vars.
- The `/chat` endpoint returns `QueryResponse` (fields: `session_id`, `session_name`, `answer_text`, `citations`, `status`). The intermediate `TurnWrapper` is internal and not exposed.
- Neo4j connectivity is verified when the KG adapter starts up.

## Suggested Next Improvements

- Wire the orchestrator to pass `kg_data["available_courses"]` from `get_courses_by_track` for semester planning and roadmap requests
- Expose `DELETE /session/{session_id}` in the API layer
- Add tests for routing, adapters, and ALE modules
- Add deployment instructions for backend, frontend, Neo4j, and vector index artifacts
