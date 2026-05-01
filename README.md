# PathFinder — Integration Phase: Implementation Notes

> **Purpose:** This document records every implementation decision made during the Integration Phase. Share it with any AI model that needs to understand what has been built, how it works, and what remains.

---

## 1. System Overview

PathFinder is an AI-powered academic and career advising system for EUI students. The Integration Phase wires two pre-built engines — a Neo4j Knowledge Graph (KG) and a RAG handbook-retrieval pipeline — behind a single FastAPI gateway.

A student sends a plain-English question. The gateway:
1. Loads their academic context
2. Classifies the query
3. Routes it to the right engine(s)
4. Returns a natural-language answer

**One endpoint: `POST /query`** handles all 6 workflow types.

---

## 2. Repository Layout

```
pathfinder/
├── docker-compose.yml           # gateway:8000, kg-engine:8001, rag-engine:8002, neo4j:7687
├── .env.example                 # NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, RAG_ENGINE_URL
├── IMPLEMENTATION_NOTES.md      # ← this file
├── README.md                    # project overview and quick-start
├── gateway/
│   ├── main.py                  # ✅ T04 — FastAPI app, wired 4-phase /query pipeline
│   ├── student_context_provider.py  # ✅ T02 — loads student_profile.json → StudentContext
│   ├── session_manager.py           # ✅ T03 — in-memory sessions, apply overrides, build effective_context
│   ├── query_understanding.py       # 🔧 T07 — scaffolded, rule-based + LLM classifier (pending)
│   ├── orchestrator.py              # 🔧 T08 — scaffolded, W1–W6 workflow selector (pending)
│   ├── response_composer.py         # ❌ T09 — not started, LLM presenter (pending)
│   ├── conftest.py                  # adds gateway/ to sys.path for pytest
│   ├── requirements.txt             # fastapi, pydantic, httpx, neo4j, python-dotenv, uvicorn
│   ├── models/
│   │   └── schemas.py               # ✅ all Pydantic v2 contracts
│   ├── data/
│   │   ├── student_profile.json     # ✅ single student record (Seif Elislam, S_000123)
│   │   ├── kg_engine_reference.py   # API reference for KGEngine (documentation only)
│   │   ├── INSTITUTIONAL_KG_COMPONENT_DOCUMENTATION.md
│   │   └── RAG_DOCUMENTATION.md
│   ├── wrappers/
│   │   ├── __init__.py              # exports KGWrapper, RAGWrapper
│   │   ├── kg_wrapper.py            # ✅ T05 — dispatches all 15 KG operations
│   │   ├── neo4j_client.py          # ✅ T05 — Neo4j driver wrapper (copied from KG-Engine)
│   │   ├── kg_queries.py            # ✅ T05 — all 15 Cypher query functions (copied from KG-Engine)
│   │   └── rag_wrapper.py           # ❌ T06 — not started, HTTP POST to rag-engine:8002
│   └── tests/
│       ├── __init__.py
│       └── test_t02_t03.py          # ✅ 12 acceptance tests (12/12 passing)
├── kg_engine/
│   └── Dockerfile                   # Neo4j-backed KG service (source lives in KG-Engine/)
├── rag_engine/
│   └── Dockerfile                   # RAG pipeline HTTP service
└── ui/
    └── Dockerfile                   # Chat frontend (pending)
```

> **KG-Engine source location:** `MVP Phase/PathFinder KG-Engine/src/` — contains `kg_engine.py`, `neo4j_client.py`, `queries.py`. The gateway copies `neo4j_client.py` and `queries.py` directly into `gateway/wrappers/` so it can call them in-process without HTTP overhead.

---

## 3. Data Contracts (`models/schemas.py`) — Pydantic v2

All components share these schemas. **Do not add business logic here.**

```python
_GRADUATION_CREDIT_HOURS = 133  # CIS Handbook constant

class QueryRequest:       # UI → Gateway POST body
    session_id: Optional[str]    # None = new session
    user_text: str
    active_student_id: str       # e.g. "S_000123"

class QueryResponse:      # Gateway → UI
    session_id: str
    answer_text: str
    citations: list[Citation]
    status: str            # "ok" | "error" | "clarification_needed"

class CourseRecord:        # ConfigDict(frozen=True) — transcript records are immutable
    course_code, course_name, credit_hours, grade, grade_points, semester_taken
    status: Literal["passed", "failed", "in_progress"]

class StudentContext:      # Full student object flowing through the system
    # Stored: student_id, name, track_id, level, current_semester, cgpa,
    #         academic_standing, total_credit_hours_earned
    # Computed: credit_hours_remaining, max_credit_hours_allowed, course_history,
    #           completed_courses, failed_courses, in_progress_courses
    # Override: planned_courses  ← always [] from provider; Session Manager fills per turn

class EntitySet:           # Entities extracted by QU Layer
    course_code, role_id, track_id, skill_id: Optional[str]

class SessionOverrides:    # Detected by QU Layer — applied by Session Manager only
    added_courses: list[str]
    target_role: Optional[str]

class StructuredQuery:     # Output of QU Layer → consumed by Orchestrator
    intent: str            # e.g. "get_prerequisites", "skill_gap_analysis"
    engine_pattern: str    # "kg" | "rag" | "mixed"
    query_type: str        # "student_aware" | "non_student_aware"
    entities: EntitySet
    needs_clarification: bool
    clarification_prompt: Optional[str]
    session_overrides: SessionOverrides

class ResultPackage:       # Orchestrator → ResponseComposer
    original_query: str
    engine_pattern: str
    kg_result: Optional[dict]
    rag_result: Optional[RAGResult]
    student_context: Optional[StudentContext]
    status: str            # "ok" | "error" | "clarification_needed"
    error_detail: Optional[str]

class RAGResult:
    answer: Optional[str]
    citations: list[Citation]
```

**Critical Pydantic v2 rules applied throughout:**
- All list fields use `Field(default_factory=list)` — never `= []` (shared mutable default bug)
- Frozen models use `ConfigDict(frozen=True)`
- Copies use `model_copy(update={...})` — never in-place mutation

---

## 4. `StudentContextProvider` (T02) — ✅ Complete

**File:** `gateway/student_context_provider.py`

Loads `student_profile.json` and returns a `StudentContext` object with derived fields computed.

### Key design decisions

| Decision | Implementation |
|---|---|
| All file I/O in one method | `_load_record()` — single swap point for database migration |
| Keyword-only args | `_build_context(*, raw, ...)` — 8 params, prevents silent positional bug |
| Partial history = None | `_parse_course_history()` returns `None` if any entry fails — partial data corrupts KG gap calculations |
| Caching | `self._cache: dict[str, StudentContext]` — second call returns same Python object (`is`) |
| Data path | `STUDENT_DATA_PATH` env var or `<script_dir>/data/student_profile.json` |

### Derived fields computed at load time

```python
# Course buckets (from course_history[].status)
completed_courses  = [c.course_code for c in history if c.status == "passed"]
failed_courses     = [c.course_code for c in history if c.status == "failed"]
in_progress_courses = [c.course_code for c in history if c.status == "in_progress"]

# Credit hours
credit_hours_remaining = 133 - total_credit_hours_earned

# Max hours per semester (CIS Handbook §5)
if cgpa > 3.0:  return 21
if cgpa >= 2.0: return 18
if cgpa >= 1.0: return 15
return 12
```

### Student record (S_000123 — Seif Elislam)
- Track: AI | CGPA: 2.85 | Earned credits: 76 | Remaining: 57
- Max credits/semester: 18
- Failed: C-CS218
- In-progress (Spring 2025): C-AI321, C-CS316, HUM228

---

## 5. `SessionManager` (T03) — ✅ Complete

**File:** `gateway/session_manager.py`

Maintains runtime conversational state across turns. Builds `effective_context = base_context + session_overrides`.

### Internal `SessionState` dataclass (NOT the Pydantic one from schemas.py)

```python
@dataclass
class SessionState:
    session_id: str
    active_student_id: str
    created_at: datetime
    last_updated: datetime
    turn_count: int = 0
    last_referenced: dict  # {"course_code": None, "role_id": None, "workflow": None}
    overrides: dict        # {"added_courses": [], "target_role": None}
```

It's a `dataclass`, not a Pydantic model — it's internal runtime state, not a serialized contract.

### Public API

```python
get_or_create_session(student_id, session_id=None) -> str
    # New ID format: "sess_" + uuid.uuid4().hex[:8]
    # Given but not found → warn + create new (handles stale IDs after restart)

apply_overrides(session_id, overrides_dict) -> None
    # added_courses → EXTEND and deduplicate (accumulate across turns)
    # target_role   → REPLACE (new target obsoletes previous)

build_effective_context(base_context, session_id) -> StudentContext
    # No overrides → return base_context unchanged
    # Has overrides → base_context.model_copy(update={"planned_courses": list(added_courses)})
    # list() creates a copy — prevents mutation bleed across turns
    # base_context is NEVER mutated

update_last_referenced(session_id, course_code=None, role_id=None, workflow=None) -> None
    # Only overwrites keys that are explicitly non-None

record_turn(session_id) -> None
    # Increments turn_count, updates last_updated
```

### Migration notes (documented in module docstring)
- **Redis:** Replace `_get_session()` and `_set_session()` only — all public API stays identical
- Trigger: set `SESSION_STORE=redis` in environment

---

## 6. `main.py` — Gateway Endpoint (T04) — ✅ Complete

**File:** `gateway/main.py`

### 4-phase pipeline

```python
@app.post("/query", response_model=QueryResponse)
def handle_query(request: QueryRequest) -> QueryResponse:

    # Phase 1 — Preparation
    session_id     = session_manager.get_or_create_session(request.active_student_id, request.session_id)
    base_context   = student_provider.get_student(request.active_student_id)  # 404 if None
    effective_context = session_manager.build_effective_context(base_context, session_id)

    # Phase 2 — Query Understanding
    structured_query = qu_layer.classify(request.user_text, effective_context)
    # Apply any overrides detected by QU layer
    if overrides.added_courses or overrides.target_role:
        session_manager.apply_overrides(session_id, {...})
        effective_context = session_manager.build_effective_context(base_context, session_id)  # rebuild

    # Phase 3 — Orchestration
    result_package = orchestrator.run(structured_query, effective_context, request.user_text)

    # Phase 4 — Response
    response = composer.compose(result_package)
    session_manager.update_last_referenced(session_id, course_code=..., role_id=..., workflow=...)
    session_manager.record_turn(session_id)
    response.session_id = session_id
    return response
```

### Graceful degradation while components are being built

Each phase wraps its call in `except NotImplementedError → HTTP 503` with a message identifying which task is pending (T07/T08/T09). The server stays alive and returns structured errors rather than crashing.

### Lifespan handler

```python
@asynccontextmanager
async def lifespan(app):
    yield
    kg_wrapper.close()   # releases Neo4j driver on shutdown
```

---

## 7. `KGWrapper` (T05) — ✅ Complete

**Files:** `gateway/wrappers/kg_wrapper.py`, `gateway/wrappers/neo4j_client.py`, `gateway/wrappers/kg_queries.py`

### How KGEngine code is integrated

The KGEngine source lives at `MVP Phase/PathFinder KG-Engine/src/`. Rather than running it as a separate HTTP service, the gateway imports it directly in-process:

- `neo4j_client.py` — copied from `KG-Engine/src/neo4j_client.py`, dotenv load removed (Docker/shell sets env vars)
- `kg_queries.py` — copied verbatim from `KG-Engine/src/queries.py` (all 15 Cypher query functions)

This avoids HTTP serialization overhead for what are pure Python→Neo4j calls.

### Connection lifecycle

```python
class KGWrapper:
    def __init__(self):
        self._client = Neo4jClient()
        self._client.connect()    # held open for process lifetime

    def close(self):
        self._client.close()      # called by FastAPI lifespan on shutdown
```

Connection failure at startup is logged as a warning (non-fatal) — the first real query that fails will return an error dict.

### Dispatch

```python
def call(self, operation: str, params: dict) -> dict:
    fn = _dispatch_map.get(operation)
    if fn is None:
        return {"status": "error", "message": f"Unknown KG operation: {operation!r}"}
    try:
        return fn(**params)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
```

### All 15 operations

| Group | Operation | Key params |
|---|---|---|
| A2 | `get_course_profile` | `course_code` |
| A2 | `get_prerequisites` | `course_code`, `depth` ("direct"\|"full") |
| A2 | `get_skills_taught` | `course_code` |
| A2 | `search_courses_by_skill` | `skills: list[str]` |
| B1 | `get_role_profile` | `role_id` (e.g. `"RL_Data_Scientist"`) |
| B1 | `get_roles_by_track` | `track_id` (e.g. `"AI"`) |
| B2 | `compute_skill_gap` | `role_id`, `completed_courses` |
| B2 | `compute_alignment_score` | `role_id`, `completed_courses` |
| B2 | `recommend_courses_to_close_gap` | `role_id`, `completed_courses` |
| B2 | `estimate_alignment_improvement` | `role_id`, `completed_courses`, `planned_courses` |
| B2 | `find_best_matching_roles` | `completed_courses` |
| B3 | `get_track_overview` | `track_id` |
| B3 | `compare_tracks` | `track_id_1`, `track_id_2` |
| B3 | `recommend_track_for_role` | `role_id` |
| B3 | `recommend_track_for_skill` | `skill_id` |

**Role ID format:** `RL_Data_Scientist` (not `ROLE_DATA_SCIENTIST`). This is the actual format in the Neo4j graph. The blueprint prose examples use the wrong format.

---

## 8. Pending Components

### T06 — `wrappers/rag_wrapper.py` ❌

HTTP POST to `RAG_ENGINE_URL/query` (default `http://rag-engine:8002`). Must:
- Accept a sub-query string + optional student context
- POST `{"query": sub_query, ...}` to the RAG service
- Parse response into `RAGResult { answer, citations }`
- Never raise — return `RAGResult(answer=None, citations=[])` on failure

### T07 — `query_understanding.py` 🔧

Two-layer classifier. Skeleton has `INTENT_PATTERNS` and `OVERRIDE_PATTERNS` already defined.

**Layer 1 (rule-based):** keyword scan → extract entities → detect overrides → return `StructuredQuery`

**Layer 2 (LLM fallback):** HTTP call to `LLM_BASE_URL` with `LLM_API_KEY` and `LLM_MODEL`. Prompt asks LLM to return structured JSON matching `StructuredQuery` schema.

**Intent → engine_pattern mapping:**
```
get_prerequisites, get_course_profile, skill_gap_analysis,
role_recommendation, track_guidance                          → "kg"
handbook_policy_query                                        → "rag"
course_and_policy_query                                      → "mixed"
ambiguous                                                    → needs_clarification=True
```

**Entity normalization:** text like `"data scientist"` must resolve to `"RL_Data_Scientist"`, `"AI track"` → `"AI"`.

### T08 — `orchestrator.py` 🔧

Routes `StructuredQuery` to the right workflow. Skeleton has `_error_package()` already implemented.

**Workflow dispatch:**
```
needs_clarification=True     → _clarification_workflow()      (W6)
query_type="student_aware"   → _student_aware_workflow()      (W4)
engine_pattern="kg"          → _kg_only_workflow()             (W1)
engine_pattern="rag"         → _rag_only_workflow()            (W2)
engine_pattern="mixed"       → _mixed_workflow()               (W3)
```

**Intent → KG operation mapping** (key mappings):
```
"get_prerequisites"   → "get_prerequisites"
"get_course_profile"  → "get_course_profile"
"skill_gap_analysis"  → "compute_skill_gap"   (student-aware)
"role_recommendation" → "find_best_matching_roles"
"track_guidance"      → "get_track_overview" or "compare_tracks"
```

### T09 — `response_composer.py` ❌

Presentation layer only. Converts `ResultPackage` → `QueryResponse` via LLM.

**LLM instruction:** "Present the data below in clear, friendly language. Do NOT add information not in the data."

```python
compose(result) -> QueryResponse
    # "ok"                   → _compose_answer()      → LLM call
    # "clarification_needed" → _compose_clarification() → direct question to user
    # "error"                → _compose_error()       → friendly error message
```

`session_id` is NOT set here — `main.py` sets it after `compose()` returns.

### T10 — UI ⏳

React chat interface. `POST /query` → display `answer_text` + `citations`.

---

## 9. Critical Architecture Rules

1. **Override detection belongs to QU Layer only.** Session Manager only *applies* overrides. Never detect overrides in `session_manager.py`.

2. **One endpoint.** `POST /query` handles all 6 workflow types. Do not add new routes.

3. **Wrappers never raise.** Both `KGWrapper.call()` and `RAGWrapper.query()` must catch all exceptions and return structured error dicts. Orchestrator always expects a return value.

4. **base_context is read-only.** Session overrides are runtime-only. The cached `StudentContext` from the provider is never mutated. `build_effective_context()` always uses `model_copy()`.

5. **LLM stays external.** QU Layer (fallback) and ResponseComposer call a hosted LLM API. No GPU-heavy inference runs in the gateway process.

6. **Schemas are frozen.** Do not change `models/schemas.py` without team agreement. All components depend on the same contracts.

---

## 10. Environment Variables

| Variable | Used By | Default |
|---|---|---|
| `NEO4J_URI` | Neo4jClient | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4jClient | `neo4j` |
| `NEO4J_PASSWORD` | Neo4jClient | *(required — set in `.env`, no default)* |
| `LLM_API_KEY` | QU Layer, ResponseComposer | — |
| `LLM_BASE_URL` | QU Layer, ResponseComposer | — |
| `LLM_MODEL` | QU Layer, ResponseComposer | — |
| `RAG_ENGINE_URL` | RAGWrapper | `http://rag-engine:8002` |
| `STUDENT_DATA_PATH` | StudentContextProvider | `<gateway_dir>/data/student_profile.json` |

---

## 11. How to Run

```bash
# Local dev (requires running Neo4j on localhost:7687)
cd pathfinder/gateway
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Docker (full stack)
cd pathfinder
cp .env.example .env    # fill in LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
docker compose up --build
```

**Tests:**
```bash
cd pathfinder/gateway
python -m pytest tests/test_t02_t03.py -v
# Expected: 12 passed
```

**Health check:**
```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "pathfinder-gateway"}
```

**Sample query (once T07–T09 are done):**
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"user_text": "What skills am I missing for Data Scientist?", "active_student_id": "S_000123"}'
```
