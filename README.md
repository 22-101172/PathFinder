# PathFinder

PathFinder is an AI-powered academic advising system for Egyptian University of Informatics (EUI). It combines a curriculum knowledge graph, handbook retrieval, academic logic, student context, and a student analytics engine behind a single conversational interface, with a separate analytics dashboard for students and advisors.

The system is split into a FastAPI backend, a React browser UI (primary), and a Streamlit chat UI (alternative). A user logs in with a student ID or advisor ID. Students get a chat advisor and a personal analytics dashboard. Advisors get a cohort-level console with per-student drill-down. All academic advising flows through the chat pipeline; the analytics dashboard is served by a separate Student Analysis Engine (SAE) service.

## What The System Does

### Chat Advisor (students)

The conversational advisor handles 26 locked intents across six domains:

- **Course exploration** — course profile, credits, description, level, prerequisites (direct and full recursive tree), skills taught, and courses that teach a given skill
- **Career guidance** — role profiles, role-track fit, skill gap analysis, alignment scoring, course recommendations to close a skill gap, best-matching role ranking, alignment improvement estimation for planned courses, and focus-course recommendations for a role or track
- **Track guidance** — track overviews, side-by-side track comparisons, and track recommendations by role or skill
- **Academic planning** — semester plan generation with multiple variants (recommended, lighter load, level-focused, max-credits, requested-courses fill); graduation roadmap with per-semester CGPA simulation; eligibility checking per course and attempt type; graduation audit including honors eligibility
- **GPA tools** — forward CGPA projection given hypothetical grades with retake-cap enforcement; target-GPA solving with per-course grade distribution and multi-semester fallback
- **Policy Q&A** — handbook-based answers with page citations
- **Student record** — snapshot of academic standing, credits, CGPA, course history, and warnings

Session management supports follow-up references ("that course", "the second one"), per-session what-if overrides (assume passed / assume failed / planned courses / clear assumptions), and persistent conversation history in SQLite.

### SAE Analytics Dashboard (students and advisors)

The Student Analysis Engine runs as a separate service and exposes analytics built on the full cohort dataset:

- **Student dashboard** — GPA trend with trajectory projection, credit pace vs. expected pace, risk level classification (low / moderate / high), anomaly detection (grade spikes, sudden drops, credit stalls), cohort percentile, semester difficulty timeline, prerequisite bottleneck map, and course category performance
- **Student analysis** — structured advisor-facing key points, LLM-generated talking points for advising sessions, track performance, CGPA trend history, and risk flags
- **Advisor console** — all active students sorted by risk level with immediate-action flags and cohort-wide statistics
- **Course risk analytics** — course pass-rate analytics across all historical first attempts, filterable by academic level
- **GPA simulation** — project CGPA impact of hypothetical grades on currently enrolled courses

### React UI Features

The primary browser UI (`ui_react/`) supports:

- English and Arabic interface (RTL-aware layout)
- Dual role: student view (chat + analytics dashboard) and advisor view (cohort console + per-student drill-down)
- Student login with live backend validation; advisor login by ID prefix (`ADV-…`)
- Full chat interface with session history sidebar, citation toggle, and new-chat button
- SAE analytics pages rendered as interactive dashboards alongside the chat

---

## Architecture

### 1. API Layer — `main.py`

FastAPI entrypoint. All routes are exposed from a single process.

**Chat and session endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Submit a message; returns `session_id`, `session_name`, `answer_text`, `citations`, `status` |
| GET | `/sessions/{student_id}` | List all sessions for a student |
| GET | `/students/{student_id}/sessions/{session_id}/history` | Session turn history (ownership-verified) |
| DELETE | `/students/{student_id}/sessions/{session_id}` | Delete one session owned by the student |
| DELETE | `/dev/students/{student_id}/sessions` | Delete all sessions for one student (dev mode only) |
| DELETE | `/dev/sessions` | Delete all sessions globally (dev mode only) |
| GET | `/health` | Basic health check |
| GET | `/session/{session_id}/history` | **Deprecated — returns 410 Gone**; use the ownership-safe path above |

**SAE proxy endpoints** (forwarded to the SAE service):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/sae/health` | SAE service liveness |
| GET | `/sae/student/{student_id}` | Full analytics profile for a student |
| GET | `/sae/student/{student_id}/analysis` | Advisor-focused analysis with talking points |
| POST | `/sae/student/{student_id}/simulate` | GPA simulation for a student |
| GET | `/sae/advisor/overview` | All active students sorted by risk level |
| GET | `/sae/courses/risk` | Course pass-rate analytics, optionally filtered by level |

### 2. Gateway Layer — `gateway/`

Coordinates the chat pipeline and shared infrastructure:

- `query_understanding.py` — classifies the user message into one or more of the 26 locked intents using an LLM model chain with keyword fallback; resolves follow-up references using session context and structured turn memory
- `qu_intents.py` — single source of truth for all locked intents, forbidden intents, and intent descriptions
- `qu_llm_chain.py` — LLM fallback chain; tries models in order on timeout, 429, bad JSON, or intent validation failure
- `qu_preprocessing.py` — input normalization and follow-up reference resolution before the LLM prompt
- `qu_prompt.py` — QU system and user prompt templates
- `orchestrator.py` — routes each intent to KG, RAG, ALE, or mixed execution; assembles a `TurnWrapper` with one `PerSQResult` per intent; wraps every result individually so failures do not cascade
- `response_composer.py` — narrates the `TurnWrapper` into a student-facing `QueryResponse` using an LLM model chain with deterministic fallback; never calls engines
- `student_context_provider.py` — loads student data from the Excel dataset; builds a normalized `StudentContext` with per-course retake counts, lifetime improve-retake totals, active-course inference, completed regular semester count (Fall/Spring only, all-withdrawn semesters excluded), and zero-credit P-grade lists; applies best-outcome grade resolution across multiple attempts; handles Con, I, and withdrawal grades
- `session_manager.py` — manages session lifecycle; persists to SQLite via `gateway/session_store`; exposes a windowed turn history to QU (controlled by `QU_CONTEXT_TURNS`); tracks the last-referenced entity (course, role, track, skill) per session; maintains per-session course and role overrides with three merge strategies (`accumulate`, `replace`, `clear`); builds effective student context by merging what-if assumptions at query time
- `turn_memory_builder.py` — builds one compact `TurnMemory` per completed turn; extracts safe-to-inject context for follow-up resolution (courses, skills, roles, tracks, policy text, ordered display items) without storing raw student PII
- `sae_rules_bridge.py` — converts PathFinder's RAG rule bundles into the flat dict format the SAE rule engine expects; enables the SAE to use live policy-sourced thresholds instead of hardcoded defaults
- `llm_client.py` — shared OpenAI-compatible LLM client used by QU and Composer
- `utils.py` — shared utilities (current semester label derived from system date)
- `models/schemas.py` — all shared Pydantic models: `QueryRequest`, `QueryResponse`, `StudentContext`, `SessionState`, `TurnWrapper`, `PerSQResult`, `TurnMemory`, `StructuredQuery`, and more

### 3. Engine Layer — `engines/`

#### Academic Logic Engine — `engines/ale/`

Six operations driven entirely by rule bundles injected at runtime from RAG. No academic thresholds are hardcoded:

- `check_course_eligibility` — validates prerequisites, credit thresholds, and retake caps; returns `eligible`, `not_eligible`, `already_completed`, `in_progress`, or `retake_cap_exceeded`
- `run_graduation_audit` — evaluates all graduation requirements (credits, CGPA, semester count, military training, zero-credit courses) and honors eligibility; returns per-check breakdowns and next-step guidance; uses the official student record and excludes what-if session assumptions
- `generate_semester_plan` — generates multiple plan variants for a target semester; respects CGPA-bracket credit caps, retake priority, student level, and requested-courses fill; supports `max_credits_mode`, `lighter_load_mode`, `target_credit_load`, `requested_plan_count`, and `requested_courses`; resolves relative semester references ("next semester"); excludes non-universal zero-credit courses (HUM110, C-MA110) from normal planning
- `generate_graduation_roadmap` — builds a full semester-by-semester plan from current standing to projected graduation; simulates CGPA after each semester; detects non-course blockers; supports accelerated (summer) and max-credits modes
- `simulate_gpa_forward` — projects CGPA forward given hypothetical grades; enforces retake caps; handles grade-point replacement vs. addition; returns per-course breakdowns and applied overrides
- `solve_target_gpa` — determines the required grade average to reach a target CGPA; generates a multi-semester projection when impossible in one semester; produces personalized per-course grade targets based on prerequisite history

Rule bundles consumed: `grading_scale_rules`, `retake_rules`, `credit_limit_rules`, `graduation_requirement_rules`, `academic_warning_rules`, `honors_rules`, `summer_semester_rules`, `student_level_rules`.

#### Knowledge Graph Engine — `engines/kg/`

Neo4j-backed curriculum and career graph. Exposes 19 operations across four groups:

- **Course catalogue** — course profile, direct and recursive prerequisites, prerequisite constraints (non-course requirements stored as `PrerequisiteConstraint` nodes), skills taught by a course, course search by skill name, course focus classification, and focus-course recommendations for a target role or track
- **Career role exploration** — role profiles with weighted required skills, and roles reachable through a track's courses and skills
- **Skill gap and alignment** — skill gap analysis, weighted alignment scoring, gap-closing course recommendations, alignment improvement estimation for planned courses, and full role ranking by alignment
- **Track guidance** — track overview (courses, skills, supported roles), side-by-side track comparison, track recommendations by role or skill, and full course list for a track with prerequisites (used by ALE for planning and roadmap generation)

Skills carry a numeric weight driving all alignment calculations: `core` (≥ 0.8), `supporting` (≥ 0.6), `optional` (< 0.6).

The `resolve_entity` operation maps natural-language names to graph IDs via a six-step pipeline: input validation → exact ID match → exact normalized name match → alias lookup → ambiguous-term lookup → partial name match. Alias table: `engines/kg/data/entity_aliases.json`.

#### RAG Handbook Engine — `engines/rag/`

Hybrid retrieval pipeline for university policy Q&A:

- Parent/child chunking during ingestion
- Dense vector retrieval with `BAAI/bge-small-en-v1.5` (Chroma)
- Sparse retrieval with BM25
- Reciprocal rank fusion
- Cross-encoder reranking with `cross-encoder/ms-marco-MiniLM-L-6-v2`

Also extracts rule bundles at startup (11 structured policy extractions via Groq) that feed ALE and the SAE rules bridge.

#### Student Analysis Engine — `engines/sae/`

A self-contained analytics service. Runs as a separate FastAPI process on port 8502. PathFinder's main API proxies all `/sae/*` requests to it. The SAE never shares a process or database with the chat pipeline.

Key modules:

- `api.py` — FastAPI router; all SAE endpoints live here
- `engine.py` — single entry point for all analytical functions; delegates all data I/O to the adapter layer
- `rule_engine.py` — all analysis rules parameterized by a `rules` dict; thresholds can be overridden at runtime by values from the RAG policy bridge
- `cgpa_calculator.py` — CGPA recalculation from raw transcript using best-grade-per-course logic; credit-hour sources: course catalogue Excel → curriculum JSON fallback
- `feature_engineering.py` — computes engineered features per student (GPA slope, credit pace ratio, failed-core rate, warning risk, pass rate)
- `data_loader.py` — loads and validates the student Excel dataset (two sheets: `data` and `registrations`)
- `scheduler.py` — periodic background cache refresh for the advisor overview
- `sae_adapter.py` — internal data adapter; lazy-loads DataFrames, exposes student context, cohort stats, and credit-hour map
- `providers/` — injectable data providers for testing (`FakeStudentContextProvider`, `FakeRulesProvider`)
- `ml_research/` — ML model training, augmentation, and saved model artifacts (used during model development)
- `data/` — SAE-local data files (student Excel dataset, course catalogue)
- `rules/curriculum_2026.json` — curriculum course list and credit-hour map used as JSON fallback by the CGPA calculator

SAE analytics output includes: GPA trend (slope + classification), credit pace vs. expected pace, risk flags and risk level, anomaly detection (grade spikes, sudden drops, credit stalls, chronic course failures), trajectory projection (semester-by-semester CGPA forecast), semester difficulty timeline, prerequisite bottleneck map, cohort percentile, course category performance, mitigation suggestions, advisor key points, and LLM-generated talking points.

Rules are injected at request time via `gateway/sae_rules_bridge.py`, which converts PathFinder's loaded RAG rule bundles into the flat threshold dict the SAE expects. If no rules are supplied, the SAE falls back to its own `DEFAULT_RULES`.

### 4. Adapter Layer — `adapters/`

Thin wrappers giving the orchestrator and main API a clean interface for each engine:

- `kg_adapter.py` — `KGAdapter`: wraps all 19 KG operations
- `rag_adapter.py` — `RAGAdapter`: wraps handbook retrieval and rule bundle extraction
- `ale_adapter.py` — `ALEAdapter`: wraps all 6 ALE operations
- `sae_adapter.py` — `SAEAdapter`: HTTP client from PathFinder's main API to the SAE service; handles connection errors, timeouts, and HTTP errors gracefully — SAE unavailability never crashes the chat pipeline

### 5. UI Layer

#### React UI — `ui_react/` (primary)

Browser-based frontend. No build step required — Babel transforms JSX in-browser.

- `index.html` — entry point; configure the backend URL via `window.__PF_API_BASE__` at the top of the file
- `js/api.js` — all backend calls (chat, sessions, SAE endpoints)
- `js/components.js` — shared UI components (icons, layout primitives, charts)
- `js/data_flows.js` — data fetching and state management hooks
- `js/sae_pages.js` — `StudentDashboard`, `StudentAnalysisPage`, `AdvisorConsole` components
- `js/main_app.js` — top-level app, routing, login screen, chat view
- `app.css` — full stylesheet
- `vendor/` — bundled React, ReactDOM, and Babel (no CDN dependency)

#### Streamlit UI — `ui/` (alternative)

Python-based chat-only frontend. Supports student ID login, session history sidebar, chat message flow, and citation display.

---

## How A Chat Request Flows

1. The student types a message in the React UI (or Streamlit).
2. FastAPI receives `POST /chat` with `{ user_text, student_id, session_id? }`.
3. The system loads or creates the student's session and fetches the `StudentContext` from the Excel dataset.
4. **Query Understanding** (`understand_query`) classifies the message into one or more `StructuredQuery` objects with locked intents. It uses session history, last-referenced entities, and the previous turn's `TurnMemory` to resolve follow-up references.
5. **Orchestrator** (`execute_turn`) routes each structured query to the right backend (KG / RAG / ALE / mixed) and assembles a `TurnWrapper` with one `PerSQResult` per intent. Every result is wrapped individually; a failure in one intent does not cascade to others.
6. **TurnMemory** (`build_turn_memory`) extracts a compact, PII-safe memory object from the completed turn for injection into the next QU call.
7. **Response Composer** (`compose`) narrates the `TurnWrapper` into a student-facing `QueryResponse`. It tries an LLM model chain (primary then fallbacks) and falls back to deterministic narration if all models fail. It never calls engines.
8. The session is updated with the new turn and the built `TurnMemory`.
9. The `QueryResponse` (`answer_text`, `citations`, `status`, `session_id`, `session_name`) is returned to the UI.

---

## Data Sources

| Source | Location | Contents |
|--------|----------|----------|
| Student dataset | `data/students_anonymous.xlsx` | `data` sheet (profiles) + `registrations` sheet (transcript history) |
| SAE student dataset | `engines/sae/data/students_anonymous.xlsx` | Same file mirrored for SAE-local use |
| Course catalogue | `engines/sae/data/Course_Catalogue_Correct_Version.xlsx` | Credit-hour map for CGPA recalculation |
| Curriculum rules | `engines/sae/rules/curriculum_2026.json` | Course list, prerequisites, credits (CGPA calculator fallback) |
| KG entity aliases | `engines/kg/data/entity_aliases.json` | Alias and ambiguous-term table for the entity resolver |
| Handbook | `engines/rag/CIS_Handbook.md` | Source document for handbook ingestion |
| RAG artifacts | `engines/rag/chroma_db/`, `engines/rag/chunks.pkl` | Generated vector index and BM25 artifacts |
| Neo4j database | external | Curriculum / skills / roles knowledge graph |

---

## Project Structure

```text
PathFinder_Integration/
├── main.py                           # FastAPI entrypoint; chat pipeline + SAE proxy routes
├── requirements.txt                  # Python dependencies for the backend
├── .env.example                      # Environment variable template
├── pytest.ini                        # Pytest configuration
│
├── adapters/
│   ├── ale_adapter.py                # ALEAdapter
│   ├── kg_adapter.py                 # KGAdapter
│   ├── rag_adapter.py                # RAGAdapter
│   └── sae_adapter.py                # SAEAdapter (HTTP client to SAE service)
│
├── data/
│   └── students_anonymous.xlsx       # Student dataset (profiles + transcript history)
│
├── engines/
│   ├── ale/                          # Academic Logic Engine
│   │   ├── ale_schemas.py            # ALE Pydantic input/output schemas
│   │   ├── functions/
│   │   │   ├── check_course_eligibility.py
│   │   │   ├── run_graduation_audit.py
│   │   │   ├── generate_semester_plan.py
│   │   │   ├── generate_graduation_roadmap.py
│   │   │   ├── simulate_gpa_forward.py
│   │   │   └── solve_target_gpa.py
│   │   ├── utils/
│   │   │   └── grade_resolver.py     # Shared grade-point and retake resolution
│   │   └── tests/                    # ALE unit + real-record tests (14 files)
│   │
│   ├── kg/                           # Knowledge Graph Engine (Neo4j)
│   │   ├── neo4j_client.py           # Neo4j driver and connection management
│   │   ├── queries.py                # All 19 KG operations
│   │   ├── cypher/
│   │   │   ├── load.cypher           # Load graph from CSVs
│   │   │   ├── reset.cypher          # Reset the graph
│   │   │   └── verify.cypher         # Verify graph integrity
│   │   ├── data/
│   │   │   ├── courses.csv
│   │   │   ├── prerequisites.csv
│   │   │   ├── course_track.csv
│   │   │   ├── tracks.csv
│   │   │   └── entity_aliases.json   # Alias + ambiguous-term table
│   │   └── tests/                    # KG adapter + query tests
│   │
│   ├── rag/                          # RAG Handbook Engine
│   │   ├── ingest.py                 # Index builder (run once or when handbook changes)
│   │   ├── rag_core.py               # Hybrid retrieval pipeline
│   │   ├── retriever.py              # Retriever interface
│   │   ├── CIS_Handbook.md           # Source handbook document
│   │   ├── chroma_db/                # Generated vector index (not in git)
│   │   ├── chunks.pkl                # Generated BM25 artifact (not in git)
│   │   └── manual_eval/              # Manual RAG evaluation logs
│   │
│   └── sae/                          # Student Analysis Engine
│       ├── api.py                    # FastAPI router for all SAE endpoints
│       ├── engine.py                 # Main analytics entry point
│       ├── rule_engine.py            # All analysis rules (GPA trend, risk, anomaly, etc.)
│       ├── cgpa_calculator.py        # CGPA recalculation from raw transcript
│       ├── feature_engineering.py    # ML feature computation
│       ├── data_loader.py            # Student Excel dataset loader
│       ├── sae_adapter.py            # Internal data adapter (lazy DataFrames)
│       ├── scheduler.py              # Background cache refresh
│       ├── llm_client.py             # SAE-local LLM client (talking points)
│       ├── llm_advisor.py            # LLM-driven advisor text generation
│       ├── providers/
│       │   ├── rules_provider.py     # Injectable rules provider
│       │   └── student_context_provider.py  # Injectable student context provider
│       ├── ml_research/              # ML training and model artifacts
│       │   ├── augmentation.py
│       │   ├── trainer.py
│       │   └── saved_models/graduation_model.pkl
│       ├── data/
│       │   ├── students_anonymous.xlsx
│       │   └── Course_Catalogue_Correct_Version.xlsx
│       └── rules/
│           └── curriculum_2026.json
│
├── gateway/
│   ├── llm_client.py                 # Shared OpenAI-compatible LLM client
│   ├── query_understanding.py        # QU entrypoint
│   ├── qu_intents.py                 # Locked intent taxonomy (26 intents)
│   ├── qu_llm_chain.py               # LLM model chain with fallback logic
│   ├── qu_preprocessing.py           # Input normalization and follow-up resolution
│   ├── qu_prompt.py                  # QU prompt templates
│   ├── orchestrator.py               # Intent routing and TurnWrapper assembly
│   ├── response_composer.py          # LLM-based answer narration
│   ├── session_manager.py            # Session lifecycle and override management
│   ├── student_context_provider.py   # Loads and normalizes StudentContext from Excel
│   ├── turn_memory_builder.py        # Builds compact TurnMemory per completed turn
│   ├── sae_rules_bridge.py           # Converts RAG rule bundles to SAE threshold dict
│   ├── utils.py                      # Shared utilities
│   ├── models/
│   │   └── schemas.py                # All shared Pydantic models
│   ├── session_store/
│   │   ├── __init__.py
│   │   ├── base.py                   # SessionStore ABC
│   │   └── sqlite_store.py           # SQLiteSessionStore
│   └── Documentation/                # Technical documentation per component
│
├── ui/
│   ├── streamlit_app.py              # Streamlit chat frontend (alternative)
│   └── requirements.txt              # Streamlit-specific dependencies
│
├── ui_react/                         # React browser UI (primary)
│   ├── index.html                    # Entry point — configure backend URL here
│   ├── app.css                       # Full stylesheet
│   ├── js/
│   │   ├── api.js                    # All backend API calls
│   │   ├── components.js             # Shared UI components
│   │   ├── data_flows.js             # Data fetching and state management
│   │   ├── sae_pages.js              # Student dashboard + advisor console components
│   │   └── main_app.js               # Top-level app, routing, login, chat view
│   └── vendor/                       # Bundled React, ReactDOM, Babel (no CDN)
│
├── tests/                            # Main test suite
│   ├── conftest.py                   # Shared fixtures
│   ├── test_main.py                  # API endpoint tests
│   ├── test_orchestrator.py          # Orchestrator routing tests
│   ├── test_query_understanding.py   # QU classification tests
│   ├── test_response_composer.py     # Composer narration tests
│   ├── test_session_manager.py       # Session lifecycle and override tests
│   ├── test_student_context_provider.py
│   ├── test_turn_memory.py           # TurnMemory builder tests
│   ├── test_ale_adapter.py           # ALE adapter tests
│   ├── test_kg_adapter.py            # KG adapter tests
│   ├── test_kg_adapter_logging.py
│   ├── test_rag_adapter.py
│   ├── test_rag_adapter_execute.py
│   ├── test_rag_adapter_structured.py
│   ├── test_rag_core_structured.py
│   ├── test_rag_rule_bundles.py
│   ├── test_semester_plan_redesign.py
│   ├── test_semester_offering_filter.py
│   ├── test_integration_contracts.py # Cross-component contract tests
│   ├── test_integration_domain1.py   # Domain 1 integration tests
│   ├── test_phase2_d2_course_info.py
│   ├── test_phase2_d3_career.py
│   ├── test_phase2_d4_track_guidance.py
│   ├── test_phase2_d6_student_record.py
│   ├── test_op4_matched_skills.py
│   ├── test_utils.py
│   ├── test_streamlit_app.py
│   ├── acceptance_orchestrator.py    # End-to-end orchestrator acceptance tests
│   ├── acceptance_qu.py              # End-to-end QU acceptance tests
│   ├── smoke_test_qu.py              # QU smoke tests (live LLM)
│   ├── smoke_test_ale_adapter.py     # ALE smoke tests (live data)
│   └── rag_manual_test.py            # Manual RAG retrieval test
│
└── scripts/
    ├── live_qu_behavior_check.py     # Multi-query QU live behavior check
    └── one_query_qu_trial.py         # Single-query QU trial runner
```

---

## Requirements

- Python 3.10+
- Neo4j instance populated with the knowledge graph
- An OpenAI-compatible LLM endpoint (e.g. Groq) for QU and Composer
- Groq API key for RAG rule bundle extraction at startup
- Student dataset at `data/students_anonymous.xlsx`
- RAG index built via `python engines/rag/ingest.py` (first time or on handbook change)

---

## Configuration

Copy `.env.example` to `.env` and fill in the values.

**Shared LLM client** (QU and Composer):

```env
LLM_PROVIDER=groq
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=your_key_here
LLM_MODEL=llama-3.1-8b-instant
LLM_TIMEOUT_SECONDS=20
```

**Query Understanding model chain:**

```env
QU_PRIMARY_MODEL=llama-3.3-70b-versatile
QU_FALLBACK_MODELS=openai/gpt-oss-120b,openai/gpt-oss-20b
QU_TIMEOUT_SECONDS=30
```

**Response Composer model chain:**

```env
COMPOSER_USE_LLM=true
COMPOSER_PRIMARY_MODEL=qwen/qwen3-32b
COMPOSER_FALLBACK_MODELS=llama-3.1-8b-instant,openai/gpt-oss-20b
COMPOSER_TIMEOUT_SECONDS=30
```

**Knowledge Graph:**

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j
```

**RAG** (Groq used for rule bundle extraction at startup):

```env
GROQ_API_KEY=your_groq_key
RAG_GROQ_MODEL=llama-3.1-8b-instant
RAG_FALLBACK_MODELS=llama-3.1-8b-instant
RAG_TIMEOUT_SECONDS=60
RAG_RULE_BUNDLE_DELAY_SECONDS=2.0
```

**Session:**

```env
SESSION_DB_PATH=./pathfinder_sessions.db
QU_CONTEXT_TURNS=5
```

**React UI:**

```env
PATHFINDER_API_URL=http://localhost:8000
```

**Student Analysis Engine:**

```env
SAE_BASE_URL=http://localhost:8502
SAE_TIMEOUT_SECONDS=30
```

**Dev-only flags** (do not set in production):

```env
APP_ENV=dev
DEV_MODE=true
PATHFINDER_TRACE=false
```

---

## Setup

**1. Create a virtual environment (Python 3.10+):**

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
```

**2. Install dependencies:**

```bash
pip install -r requirements.txt
```

**3. Configure the environment:**

```bash
cp .env.example .env
```

Fill in at minimum: `LLM_API_KEY`, `LLM_BASE_URL`, `GROQ_API_KEY`, and Neo4j credentials.

**4. Start Neo4j** and verify the knowledge graph is loaded. To load from scratch:

```bash
# Run load.cypher in the Neo4j browser or via cypher-shell
engines/kg/cypher/load.cypher
```

**5. Place the student dataset:**

Ensure `data/students_anonymous.xlsx` is present with a `data` sheet and a `registrations` sheet.

**6. Build the RAG index** (first time, or when the handbook changes):

```bash
python engines/rag/ingest.py
```

This creates `engines/rag/chroma_db/` and `engines/rag/chunks.pkl`. The backend will not start without them.

**7. Start the main backend:**

```bash
python -m uvicorn main:app --reload
```

Startup takes 25–110 seconds (RAG model loading + 11 rule bundle extractions). Watch for `PathFinder: ready.` in the log.

API docs: `http://localhost:8000/docs`

**8. Start the SAE service** (separate terminal, same virtual environment):

```bash
uvicorn engines.sae.api:app --port 8502
```

The SAE loads the student dataset and course catalogue on first request. The main backend will log `SAE not reachable` at startup if the SAE is not running yet — the chat pipeline continues to work without it; only the analytics dashboard will be unavailable.

**9. Open the React UI:**

Open `ui_react/index.html` directly in a browser, or serve it with any static file server:

```bash
python -m http.server 5500 --directory ui_react
```

Then open `http://localhost:5500`. The backend URL is configured via `window.__PF_API_BASE__` at the top of `index.html` — change it if your backend is not on port 8000.

**10. (Alternative) Start the Streamlit UI:**

```bash
python -m streamlit run ui/streamlit_app.py
```

Streamlit UI: `http://localhost:8501`. Configure `PATHFINDER_API_URL` in `.env` to match the backend address.

---

## Session Deletion and Developer Cleanup

Sessions are persisted in SQLite at `SESSION_DB_PATH`. Three deletion flows are available:

### Per-student session delete (production-safe)

```bash
# Via React UI — trash button in the session sidebar
# Via API:
curl -X DELETE http://localhost:8000/students/STU000001/sessions/<session_id>
```

Ownership is verified: the session must belong to the given student ID.

### Bulk delete for one student (dev mode)

Enable dev mode in `.env` (`APP_ENV=dev`), then:

```powershell
# Delete all sessions for one student
Invoke-RestMethod -Method Delete http://localhost:8000/dev/students/STU000001/sessions

# Expected response:
# { "deleted": true, "student_id": "STU000001", "count": 12 }
```

### Global session reset (dev mode)

```powershell
Invoke-RestMethod -Method Delete http://localhost:8000/dev/sessions
# { "deleted": true, "count": 57 }
```

### Direct SQLite cleanup (when backend is offline)

```sql
-- Inspect sessions for one student
SELECT session_id, session_name, last_updated
FROM sessions
WHERE student_id = 'STU000001'
ORDER BY last_updated DESC;

-- Delete
DELETE FROM sessions WHERE student_id = 'STU000001';
```

---

## Test Commands

Run targeted component tests (no live LLM or Neo4j required for most):

```bash
# Core pipeline
python -m pytest tests/test_main.py -v --tb=short
python -m pytest tests/test_orchestrator.py -v --tb=short
python -m pytest tests/test_query_understanding.py -v --tb=short
python -m pytest tests/test_response_composer.py -v --tb=short
python -m pytest tests/test_session_manager.py -v --tb=short
python -m pytest tests/test_turn_memory.py -v --tb=short
python -m pytest tests/test_student_context_provider.py -v --tb=short

# ALE engine (offline)
python -m pytest tests/test_ale_adapter.py tests/test_semester_plan_redesign.py -v --tb=short

# Integration contracts
python -m pytest tests/test_integration_contracts.py -v --tb=short

# Full offline suite
python -m pytest tests/ -v --tb=short -k "not smoke"
```

Do not run `smoke_test_*.py` or `acceptance_*.py` without live LLM and Neo4j configured.

---

## Example Queries

**Policy and handbook:**
- "What is the grading scale at EUI?"
- "What is the retake policy for failed courses?"
- "How many credits can I register with my current GPA?"

**Course information:**
- "Tell me about C-CS301"
- "What are the prerequisites for C-AI421?"
- "What skills does Deep Learning teach?"
- "Which courses teach machine learning?"

**Academic decisions:**
- "Can I take C-CS401 now?"
- "Can I graduate this semester?"
- "What courses do I still need to graduate?"
- "Give me my graduation roadmap"
- "What courses can I take next semester?"
- "Give me the maximum load I can take next semester"
- "Plan next semester with Introduction to Database Systems and fill the rest"

**GPA tools:**
- "If I get A in Introduction to Database Systems, what will my CGPA be?"
- "What grades do I need to reach a 3.0 CGPA?"

**Career and track guidance:**
- "I want to become a data scientist — what skills am I missing?"
- "What roles can I get with the AI track?"
- "Compare AI and Data Science tracks."

**Session chaining:**
- "Tell me about C-CS301" → "What are its prerequisites?" → "Can I take it?"
- "Assume I passed Operating Systems. Now plan my semester." → "Reset assumptions."

---

## Current Limitations

- Sessions are persisted in a local SQLite file. Horizontal scaling or a shared remote store is not yet supported.
- The current semester label is derived from the system date in `gateway/utils.py`. There is no administrative calendar override.
- Student login is based only on IDs found in the Excel sheet; the SAE validates student IDs against its own loaded dataset.
- Rule bundle loading at startup calls the Groq API 11 times with a configurable inter-call delay (`RAG_RULE_BUNDLE_DELAY_SECONDS`). Cold start takes roughly 25–110 seconds depending on rate limits.
- `qwen/qwen3-32b` (Composer primary) is a preview model; production model selection is deferred.
- The `/health` endpoint returns a basic `{"status": "ok"}` response only. Per-component health checks are not yet implemented.
- The SAE advisor overview is cached to disk for 24 hours by default; forced refresh via `force_refresh=true` query parameter.
- The React UI uses in-browser Babel transformation which is suitable for development and demos but not for a production deployment — a proper build step (Vite, Create React App) should replace it before production use.

---

## Troubleshooting

**Backend fails to start / rule bundles not loaded:**
Check the startup log. The RAG retriever must initialise before rule bundles are extracted. If you see `retriever not ready at import time`, the `engines/rag/chroma_db/` or `engines/rag/chunks.pkl` artifacts are missing — run `python engines/rag/ingest.py` first.

**Rule bundle partial load (e.g. `student_level_rules` fails with 429):**
Groq rate-limit transient error at startup. The affected bundle loads as `None`; ALE uses a safe default for that bundle. Restart the backend after a few seconds.

**SAE not reachable at startup:**
The chat pipeline continues to work. Start `uvicorn engines.sae.api:app --port 8502` in a separate terminal. The main backend will pick it up on the next `/sae/*` request without restart.

**Answer text starts with `<think>` tag:**
Reasoning models emit chain-of-thought in `<think>` blocks. The Composer strips these automatically. If they appear in responses, ensure `gateway/response_composer.py` is up to date.

**Neo4j unavailable:**
The KG adapter degrades gracefully — KG-dependent intents return `kg_unavailable` and the Composer narrates the error. Policy and record queries continue to work.

**React UI cannot reach backend:**
Edit `window.__PF_API_BASE__` at the top of `ui_react/index.html` to match your backend address and port.

**Streamlit UI cannot reach backend:**
Set `PATHFINDER_API_URL=http://127.0.0.1:8000` in `.env`.

---

## Recommended Model Configuration

```env
# Query Understanding
QU_PRIMARY_MODEL=llama-3.3-70b-versatile
QU_FALLBACK_MODELS=openai/gpt-oss-120b,openai/gpt-oss-20b
QU_TIMEOUT_SECONDS=30

# Response Composer
COMPOSER_USE_LLM=true
COMPOSER_PRIMARY_MODEL=qwen/qwen3-32b
COMPOSER_FALLBACK_MODELS=llama-3.1-8b-instant,openai/gpt-oss-20b
COMPOSER_TIMEOUT_SECONDS=30
```

Composer intentionally avoids `llama-3.3-70b-versatile` to prevent provider rate conflicts with QU. `qwen/qwen3-32b` produces high-quality narration but is a preview model; `openai/gpt-oss-20b` is the recommended production fallback.

---

## Development Notes

- The main backend (`main.py`) and the SAE service (`engines/sae/api.py`) are separate processes. They share no database, no in-process state, and no imports at runtime — communication is HTTP only.
- The `/chat` endpoint returns `QueryResponse` (`session_id`, `session_name`, `answer_text`, `citations`, `status`). The intermediate `TurnWrapper` is internal.
- QU and Composer use the shared `gateway/llm_client.py` with independent model chains controlled by `QU_*` and `COMPOSER_*` env vars.
- FastAPI and Streamlit (if used) are run as separate processes.
- The React UI can be opened as a local file (`file://`) or served by any static server — no Node.js or npm required.
