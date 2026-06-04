# PathFinder — New Chat Handoff Document
## Orchestrator Design & Implementation Phase
**Date:** June 2026 | **Branch:** person-seif | **Codebase:** `O:\Graduation Project\PathFinder_Integration\`

---

## 1. Role of the AI Assistant in This Chat

You are a technical project assistant helping develop PathFinder, an AI-powered academic advising system for the CIS program at EUI (Egyptian University of Informatics).

Your role in this chat: design and plan the **Orchestrator** component. All design decisions must be discussed and locked before any implementation begins. You implement nothing autonomously — Seif approves all logic decisions. Claude Code handles implementation only after the full spec is locked.

---

## 2. System Architecture Overview

Three engines coordinated by an orchestrator, with supporting gateway components:

| Component | Type | Status | Owner |
|---|---|---|---|
| KG Engine (Neo4j) | Knowledge Graph | ✅ Done | Seif |
| RAG Engine | Handbook Retrieval | ✅ Done | Omar (teammate) |
| ALE Engine | Academic Logic | ✅ Done — 108 tests pass | Seif |
| Student Context Provider (SCP) | Data Layer | ✅ Done | Seif |
| Session Manager | State Layer | ✅ Done — SQLite, 18 tests pass | Seif |
| KG Adapter | Integration | ✅ Done | Seif |
| RAG Adapter | Integration | ✅ Done — typed Pydantic, paths fixed | Seif |
| ALE Adapter | Integration | ✅ Done — key names fixed | Seif |
| **Orchestrator** | **Coordinator** | **🔧 NEXT — this chat** | Seif |
| Query Understanding (QU) | NLP Layer | 🔧 After orchestrator | Seif |
| Response Composer | Output Layer | 🔧 After QU | Seif |
| API (main.py) | Gateway | 🔧 Needs update after above | Seif |
| UI (streamlit_app.py) | Frontend | 🔧 Last | Seif |

**Total tests passing: 31 (18 session manager + 13 SCP). All engines operational.**

---

## 3. Project Structure

```
PathFinder_Integration/
├── adapters/
│   ├── kg_adapter.py          — 19 KG operations via call(operation, params)
│   ├── rag_adapter.py         — execute(), execute_structured(), get_rule_bundles()
│   └── ale_adapter.py         — 6 ALE operations via call(operation, student_context, rule_bundles, kg_data, params)
├── engines/
│   ├── ale/
│   │   ├── functions/         — 6 ALE function files
│   │   └── schemas.py         — ALL ALE Pydantic models + 8 rule bundle classes
│   ├── kg/
│   │   ├── neo4j_client.py
│   │   └── queries.py         — 19 KG query functions
│   └── RAG/
│       ├── rag_core.py        — extract_facts(), extract_structured()
│       ├── retriever.py       — HybridRetriever (paths anchored via __file__)
│       ├── ingest.py          — run once to build index
│       ├── CIS_Handbook.md    — source document
│       ├── chroma_db/         — built vector index (already ingested)
│       └── chunks.pkl         — BM25 parent chunks (already ingested)
├── gateway/
│   ├── models/
│   │   └── schemas.py         — shared Pydantic models (gateway layer)
│   ├── session_manager.py     — SQLite-backed session CRUD
│   ├── session_store/
│   │   ├── base.py            — abstract SessionStore interface
│   │   ├── sqlite_store.py    — SQLiteSessionStore implementation
│   │   └── __init__.py
│   ├── student_context_provider.py
│   ├── llm_client.py          — shared Groq LLM client (llama-3.3-70b-versatile)
│   ├── query_understanding.py — TO BE BUILT
│   ├── orchestrator.py        — TO BE BUILT
│   └── response_composer.py   — TO BE BUILT
├── data/
│   └── students_anonymous.xlsx
├── main.py                    — FastAPI entry point (needs update after all components done)
├── .env                       — root-level, single source of truth for all keys
└── pathfinder_sessions.db     — SQLite session database (auto-created)
```

---

## 4. Completed Components — Key Facts

### 4.1 ALE Engine

Six functions fully implemented and tested (108 tests pass):

- `check_course_eligibility` — eligibility based on prerequisites, retake rules, credit thresholds
- `run_graduation_audit` — checks 3 graduation rules (133 credits, CGPA ≥ 2.0, 6 semesters) + honors as sub-result
- `generate_semester_plan` — course recommendations for one semester
- `generate_graduation_roadmap` — multi-semester projection with loop termination safeguards
- `simulate_gpa_forward` — GPA projection with hypothetical courses and retake logic
- `solve_target_gpa` — reverse-solve required grades for a CGPA target

**Key ALE principles:**
- Zero hardcoded rule values — everything injected via rule bundles from RAG
- 8 rule bundle classes defined in `engines/ale/schemas.py` (NOT in gateway schemas)
- ALE adapter (`adapters/ale_adapter.py`) wires ALE to integration codebase — pure mapping, no logic

### 4.2 KG Engine

19 operations available via `KGAdapter.call(operation, params)`:

**Course queries:** `get_course_profile`, `get_prerequisites`, `get_skills_taught`, `search_courses_by_skill`, `get_course_focus`, `get_focus_courses_for_target`, `get_courses_by_track`

**Career/role queries:** `get_role_profile`, `get_roles_by_track`, `compute_skill_gap`, `compute_alignment_score`, `recommend_courses_to_close_gap`, `estimate_alignment_improvement`, `find_best_matching_roles`

**Track queries:** `get_track_overview`, `compare_tracks`, `recommend_track_for_role`, `recommend_track_for_skill`

**Entity resolution:** `resolve_entity(entity_type, entity_text)` — used by QU, not orchestrator

### 4.3 RAG Engine & RAG Adapter

Three public methods on `RAGAdapter`:

- `execute(sub_query)` — free-text handbook query → `{answer, extracted_facts, citations}`
- `execute_structured(sub_query, schema)` — schema-forced extraction → `{data, citations}`
- `get_rule_bundles()` — returns all 8 rule bundles as typed Pydantic models → `dict[str, BaseModel]`

**Important RAG facts:**
- `rag_core.py` loads `.env` from project root (anchored via `__file__`)
- `retriever.py` paths anchored via `__file__` — works from any CWD
- Ingestion already run — `chroma_db/` and `chunks.pkl` exist in `engines/RAG/`
- `GROQ_API_KEY` and `GROQ_MODEL=llama-3.1-8b-instant` in root `.env`
- `get_rule_bundles()` calls `extract_structured()` for each bundle — RAG actually retrieves values from handbook

**Grading scale:** CIS Handbook is the authority. Handbook values in the RAG adapter are correct:

| Grade | Points | Percentage |
|---|---|---|
| A+ | 4.0 | ≥ 96% |
| A | 3.7 | 92–95.9% |
| A- | 3.4 | 88–91.9% |
| B+ | 3.2 | 84–87.9% |
| B | 3.0 | 80–83.9% |
| B- | 2.8 | 76–79.9% |
| C+ | 2.6 | 72–75.9% |
| C | 2.4 | 68–71.9% |
| C- | 2.2 | 64–67.9% |
| D+ | 2.0 | 60–63.9% |
| D | 1.5 | 55–59.9% |
| D- | 1.0 | 50–54.9% |
| F | 0.0 | < 50% |
| Abs | 0.0 | Absent (treated as F) |
| P | None | Pass — not counted in GPA |

### 4.4 Student Context Provider (SCP)

Reads `data/students_anonymous.xlsx`, builds `StudentContext`. Public functions: `load_excel(path)`, `get_context(student_id) → StudentContext | None`.

**What SCP produces:**

Direct from Excel: `student_id`, `name`, `program`, `first_semester`, `study_status`, `military_status`, `cgpa`, `last_semester_gpa`, `cumulative_chs`, `cumulative_cps`, `total_credit_hours_earned`, `last_semester_chs`, `last_semester_cps`, `last_semester_phs`, `current_semester_chs`, `consecutive_warnings`, `total_warnings`, `last_semester_warning`

Derived: `track_id` (normalized from program string), `level` (int 1–4)

From registrations sheet: `course_history`, `completed_courses`, `failed_courses`, `in_progress_courses`, `completed_regular_semesters`, `retake_count`, `total_improve_retakes_used`, `zero_credit_courses_passed: list[str]`

**What SCP does NOT compute (orchestrator's job):**
- `academic_standing` — needs `AcademicWarningRules` from RAG
- `credit_hours` on `CourseRecord` — always 0 from SCP, orchestrator patches via KG `get_course_profile`
- `zero_credit_courses_passed` as bool for ALE — SCP gives `list[str]`, orchestrator cross-references KG
- `current_semester` — call `get_current_semester()` from `gateway/utils.py`

### 4.5 Session Manager

SQLite-backed, persists to `pathfinder_sessions.db` in project root. Storage abstracted behind `SessionStore` interface — swap to Postgres in production via one env var change.

**Public functions:**

```python
get_or_create_session(session_id, student_id, context, first_message) → tuple[SessionState, bool]
# bool = is_new — True if session_id was None or stale

get_qu_context(session_id, user_text) → QUContext
# Packages user_text + last N turns + last_referenced + current_overrides for QU

apply_query_result(session_id, structured_query) → StudentContext
# Applies QU overrides, updates last_referenced, returns effective_context

update_session_after_turn(session_id, user_text, answer_text, new_overrides, new_last_referenced) → None
# Appends turn, saves to SQLite

get_student_sessions(student_id) → StudentSessionsResponse
get_session_history(session_id) → SessionHistoryResponse | None
delete_session(session_id) → bool
clear_all_sessions() → int  # DEV ONLY — never expose via API
```

**N turns for QU:** controlled by `QU_CONTEXT_TURNS` env var, default 5.

---

## 5. Key Gateway Schemas (`gateway/models/schemas.py`)

### SessionOverrides
```python
class SessionOverrides(BaseModel):
    added_courses: list[str] = []
    target_role: Optional[str] = None
    course_override_type: Literal["planned", "assumed_done", "gpa_scenario", "none"] = "none"
    override_action: Literal["accumulate", "replace", "clear"] = "accumulate"
```

**Override semantics for orchestrator:**
- `"planned"` → `added_courses` feeds into KG `planned_courses` for alignment estimation
- `"assumed_done"` → `added_courses` merged into `completed_courses` for eligibility checks
- `"gpa_scenario"` → `added_courses` fed to ALE GPA functions as hypothetical planned courses
- `"none"` → no course overrides active

### QUContext (session manager → QU)
```python
class QUContext(BaseModel):
    user_text: str
    recent_turns: list[dict]       # last N turns {"user": ..., "answer": ...}
    last_referenced: LastReferenced # {course_code, role_id, track_id}
    current_overrides: SessionOverrides
```

### LastReferenced
```python
class LastReferenced(BaseModel):
    course_code: Optional[str] = None
    role_id: Optional[str] = None
    track_id: Optional[str] = None
```

### StudentContext (important notes)
- NO `planned_courses` field — overrides live in `SessionOverrides` only
- NO `academic_standing` field — orchestrator computes from RAG rules
- NO `current_semester` field — from `get_current_semester()` in `utils.py`
- `zero_credit_courses_passed` is `list[str]` — orchestrator converts to bool for ALE

### Rule Bundle Key Names (RAG adapter output → ALE adapter input)
These keys are now aligned. Any future code must use exactly these:

| Key | Pydantic Model |
|---|---|
| `"retake_rules"` | `RetakeRules` |
| `"credit_limit_rules"` | `CreditLimitRules` |
| `"summer_semester_rules"` | `SummerSemesterRules` |
| `"graduation_requirement_rules"` | `GraduationRequirementRules` |
| `"academic_warning_rules"` | `AcademicWarningRules` |
| `"honors_rules"` | `HonorsRules` |
| `"grading_scale_rules"` | `GradingScaleRules` |
| `"student_level_rules"` | `StudentLevelRules` |

All 8 classes are in `engines/ale/schemas.py`.

---

## 6. main.py Current State — Do Not Fix Yet

`main.py` exists but is broken — it references old session manager functions and components that don't exist yet:

- Imports `create_session`, `get_session` — these no longer exist, replaced by `get_or_create_session`
- Calls `understand_query()` — QU not built yet
- Calls `_orchestrator.extract_last_referenced(sq)` — orchestrator not built yet
- Session handling does not use `apply_query_result` or `get_qu_context`

**Fix `main.py` last — after Orchestrator, QU, and Composer are all built.**

---

## 7. Complete System Flow — Every Turn

Understanding this is critical before designing the orchestrator.

```
Student sends: {session_id, user_text, student_id}
        ↓
Phase 1 — Gateway (main.py) receives request
        ↓
Phase 2 — Session Manager: get_or_create_session()
          → If new: calls SCP for fresh StudentContext snapshot
          → Returns (SessionState, is_new)
        ↓
Phase 3 — Session Manager: get_qu_context()
          → Packages user_text + last N turns + last_referenced + current_overrides
          → Returns QUContext
        ↓
Phase 4 — Query Understanding (QU):
          → Receives QUContext
          → Classifies intent
          → Extracts raw entities
          → Calls KG resolve_entity() to normalize
          → Detects override intent, sets SessionOverrides fields
          → Returns StructuredQuery
        ↓
Phase 5 — Session Manager: apply_query_result(structured_query)
          → Applies overrides (accumulate/replace/clear)
          → Updates last_referenced
          → Builds effective StudentContext
          → Returns effective_context
        ↓
Phase 6 — Orchestrator:
          → Receives StructuredQuery + effective_context
          → Fetches rule bundles from RAG (cached per session)
          → Patches credit_hours via KG get_course_profile
          → Computes academic_standing from rules
          → Dispatches to correct engine(s)
          → Returns ResultPackage
        ↓
Phase 7 — Response Composer:
          → Receives ResultPackage
          → Calls Groq (llama-3.3-70b-versatile)
          → Returns natural language answer
        ↓
Phase 8 — Session Manager: update_session_after_turn()
          → Appends turn to history
          → Saves to SQLite
        ↓
Phase 9 — Gateway returns {session_id, session_name, answer_text, status} to UI
```

---

## 8. Orchestrator — Scope and Responsibilities

### What It Does
- Receives `StructuredQuery` from QU and `effective_context: StudentContext` from session manager
- Fetches and caches rule bundles from RAG (cache TTL: per session or 30 days)
- Patches `credit_hours` on `CourseRecord` via KG `get_course_profile` (SCP sets them to 0)
- Computes `academic_standing` from `AcademicWarningRules` (not SCP's job)
- Converts `zero_credit_courses_passed: list[str]` to bool via KG cross-reference
- Dispatches to correct engine(s) based on `engine_pattern` from `StructuredQuery`
- Builds `ResultPackage` for the composer

### Engine Routing Patterns
- `"kg"` — KG only
- `"rag"` — RAG free-text only
- `"ale"` — ALE only (needs rule bundles from RAG + course data from KG)
- `"mixed"` — multiple engines (e.g. KG + ALE)
- `"clarification"` — no engine, return clarification request to composer

### What It Must NOT Do
- No academic rule application
- No LLM calls (composer's job)
- No session state management (session manager's job)
- No entity resolution (QU's job)
- No direct access to Excel data (SCP's job)

---

## 9. Core Architectural Principles — Never Violate

1. **No hardcoded rule values anywhere** — all rules come from RAG rule bundles
2. **Orchestrator coordinates only** — never computes academic logic
3. **All academic rules come from RAG rule bundles**
4. **All course credit hours come from KG**
5. **SCP never applies rules** — reads and derives from raw data only
6. **Privacy:** `student_id` and `name` never reach QU or Response Composer (stripped to aggregate fields)
7. **ALE never calls RAG or KG directly** — orchestrator mediates everything
8. **Session manager owns `planned_courses`** via `SessionOverrides.added_courses` — not in `StudentContext`

---

## 10. Environment & Configuration

Root `.env` keys:
- `GROQ_API_KEY` — Groq API key (used by both RAG engine and QU/Composer)
- `GROQ_MODEL=llama-3.1-8b-instant` — model for RAG engine specifically
- `QU_CONTEXT_TURNS=5` — how many recent turns passed to QU (optional)
- `SESSION_DB_PATH=pathfinder_sessions.db` — SQLite path (optional)

Neo4j: running locally, password `institution123`

LLM for QU/Composer: `llama-3.3-70b-versatile` via `gateway/llm_client.py`

LLM for RAG engine: `llama-3.1-8b-instant` configured in `rag_core.py`

---

## 11. Development Rules

- Design before implementation — lock all logic decisions before Claude Code touches anything
- Assistant handles architecture and system design — Seif has authority over all business logic decisions
- Claude Code implements only what is fully specified
- Cross-validate math/logic-heavy decisions before locking
- No Docker/deployment concerns until the very end
- Do not touch teammate files (RAG engine files inside `engines/RAG/`) without flagging it
- One component at a time — do not mix component fixes

---

## 12. What To Do In This Chat

**Start here:** Design the Orchestrator. Map every possible workflow, every engine routing pattern, every input/output contract. Lock all decisions before implementation.

**Remaining order after orchestrator:**
1. Query Understanding (QU)
2. Response Composer
3. API (main.py update)
4. UI

---

*End of Handoff Document*
