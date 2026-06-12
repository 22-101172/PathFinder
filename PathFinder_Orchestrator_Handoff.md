# PathFinder — Orchestrator Design & Implementation Handoff
**Version:** 2.0 (Final Pre-Orchestrator) | **Date:** June 2026 | **Branch:** person-seif
**Codebase:** `O:\Graduation Project\PathFinder_Integration\`

---

## 1. Your Role in This Chat

You are a technical project assistant helping develop PathFinder, an AI-powered academic advising system for the CIS program at EUI (Egyptian University of Informatics).

**Your job in this chat:** Design and implement the **Orchestrator** component.

**How we work:**
- Discuss and lock every design decision before Claude Code touches anything
- One problem at a time — no jumping ahead
- No autonomous decisions from Claude Code
- Cross-validate logic-heavy decisions when needed
- Seif reviews all Claude Code output before proceeding

---

## 2. System Overview

PathFinder answers student queries about curriculum, graduation, career paths, and academic policy. Three engines are coordinated by a gateway layer:

```
Student Query
     ↓
[Query Understanding (QU)] ← already implemented
     ↓ StructuredQuery
[Session Manager] ← load/update session state
     ↓ SessionState + StudentContext
[ORCHESTRATOR] ← THIS CHAT — coordinates everything
     ↓ ResultPackage
[Response Composer] ← already implemented
     ↓
Final Answer
```

---

## 3. Component Status

| Component | Status | Tests | Notes |
|---|---|---|---|
| KG Engine (Neo4j) | ✅ Done | 18/18 live | 18 operations, credit_threshold fixed |
| RAG Engine | ✅ Done | 4/4 pass, 5 skip (rate limit) | Hybrid retrieval + Groq |
| ALE Engine | ✅ Done | 19/19 pass | 6 functions, semester filter fixed |
| KG Adapter | ✅ Done | 18/18 live | Pure pass-through |
| RAG Adapter | ✅ Done | 4/4 pass | _as_dict() fix applied |
| ALE Adapter | ✅ Done | 19/19 pass | grade_points mapping fixed |
| SCP | ✅ Done | 13/13 pass | Excel → StudentContext |
| Session Manager | ✅ Done | 20/20 pass | SQLite, full override logic |
| **Orchestrator** | **🔧 THIS CHAT** | — | Stub exists but broken |
| Query Understanding | ✅ Implemented | — | LLM + regex classification |
| Response Composer | ✅ Implemented | — | LLM + deterministic fallback |
| API (main.py) | ⚠️ Broken imports | — | Needs update after orchestrator |
| UI | ✅ Exists | — | Streamlit, not priority |

**Known broken things to fix after orchestrator:**
- `main.py` imports `create_session, get_session` — both names don't exist. Correct: `get_or_create_session, delete_session`
- Orchestrator stub references `ctx.planned_courses`, `ctx.academic_standing`, `ctx.current_semester` — none of these fields exist on `StudentContext`
- Orchestrator stub uses wrong `ALEAdapter.call()` signature

---

## 4. Project File Structure

```
PathFinder_Integration/
├── adapters/
│   ├── kg_adapter.py
│   ├── rag_adapter.py
│   └── ale_adapter.py
├── engines/
│   ├── kg/
│   │   ├── queries.py          — all 18 Cypher query functions
│   │   ├── neo4j_client.py
│   │   └── data/entity_aliases.json
│   ├── rag/                    — NOTE: lowercase, not RAG
│   │   ├── rag_core.py
│   │   ├── retriever.py
│   │   ├── ingest.py
│   │   ├── CIS_Handbook.md
│   │   ├── chroma_db/          — ChromaDB persistence
│   │   └── chunks.pkl          — BM25 parent chunks
│   └── ale/
│       ├── schemas.py          — ALL ALE + rule bundle Pydantic models
│       └── functions/
│           ├── simulate_gpa_forward.py
│           ├── solve_target_gpa.py
│           ├── check_course_eligibility.py
│           ├── run_graduation_audit.py
│           ├── generate_semester_plan.py
│           └── generate_graduation_roadmap.py
├── gateway/
│   ├── orchestrator.py         — BROKEN STUB — rewrite this
│   ├── query_understanding.py  — implemented
│   ├── response_composer.py    — implemented
│   ├── session_manager.py      — done
│   ├── student_context_provider.py — done
│   ├── utils.py                — get_current_semester() lives here
│   ├── llm_client.py
│   └── models/
│       └── schemas.py          — ALL gateway Pydantic models
├── gateway/session_store/
│   ├── base.py
│   └── sqlite_store.py
├── data/
│   └── students_anonymous.xlsx — student data (2 sheets: data, registrations)
├── tests/
│   ├── smoke_test_ale_adapter.py   — 19/19 pass, pure unit
│   ├── test_session_manager.py     — 20/20 pass, pure unit
│   ├── test_semester_offering_filter.py — 8/8 pass, pure unit
│   ├── test_student_context_provider.py — 13/13 pass
│   ├── test_kg_adapter.py          — 18/18 pass, requires Neo4j
│   └── test_rag_adapter.py         — 4/4 pass, 5 skip (Groq rate limit)
└── main.py                     — FastAPI entry point (broken imports, needs fix)
```

---

## 5. Tech Stack & Configuration

| Component | Value |
|---|---|
| API framework | FastAPI |
| KG database | Neo4j (bolt://localhost:7687) |
| Session storage | SQLite (pathfinder_sessions.db) |
| Student data | Excel (data/students_anonymous.xlsx) |
| Vector store | ChromaDB (engines/rag/chroma_db/) |
| Embedding model | BAAI/bge-small-en-v1.5 (HuggingFace) |
| Reranker | cross-encoder/ms-marco-MiniLM-L-6-v2 |
| LLM (RAG) | Groq llama-3.1-8b-instant (direct requests) |
| LLM (QU/Composer) | Groq via custom LLMClient (httpx) |

**Environment variables:**
```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=institution123
NEO4J_DATABASE=neo4j
GROQ_API_KEY=<required>
LLM_PROVIDER=groq
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=<same as GROQ_API_KEY>
LLM_MODEL=llama-3.1-8b-instant
LLM_TIMEOUT_SECONDS=20
QU_CONTEXT_TURNS=5
SESSION_DB_PATH=pathfinder_sessions.db
```

---

## 6. Adapter Call Signatures (What Orchestrator Calls)

### KGAdapter
```python
kg.call(operation: str, params: dict) -> dict
```
Returns dict with result fields, or `{"error": "error_code", ...}` on failure.
Error codes: `kg_unavailable`, `unknown_operation`, `bad_params`, `kg_error`, `course_not_found`, `role_not_found`, `track_not_found`, `skill_not_found`, `no_courses_provided`

### RAGAdapter
```python
rag.execute(sub_query: str, student_context=None) -> dict
# Returns: {"answer": str, "extracted_facts": list[str], "citations": list[dict]}

rag.get_rule_bundles() -> dict[str, BaseModel]
# Returns: 8 keys → Pydantic model instances OR {} on failure
# CRITICAL: 8 Groq API calls fired. Must be cached. Never call per-operation.
```

### ALEAdapter
```python
ale.call(
    operation: str,
    student_context: StudentContext,
    rule_bundles: dict,           # from rag.get_rule_bundles()
    kg_data: dict = None,         # optional KG data
    params: dict = None           # optional extra params
) -> dict
```
Returns `model.model_dump()` on success or `{"status": "error", "message": str}` on failure.

---

## 7. All 18 KG Operations

| # | Operation | Required Params | Key Return Fields |
|---|---|---|---|
| 1 | `get_course_profile` | `course_code` | `course_code, name, credits, level, semester_offering, tracks, description, credit_threshold` |
| 2 | `get_prerequisites` | `course_code`, `depth="direct"` | `direct_prerequisites: list[dict]`, `non_course_prerequisites: list[dict]`, `has_prerequisites` |
| 3 | `get_skills_taught` | `course_code` | `skills_taught: list[dict{skill_id,name,category}]` |
| 4 | `search_courses_by_skill` | `skills: list[str]` | `results: list[dict]`, `unrecognized_skills` |
| 5 | `get_role_profile` | `role_id` | `role_id, role_name, required_skills: list[dict{skill_id,name,tier,weight}]` |
| 6 | `get_roles_by_track` | `track_id` | `results: list[dict{role_id,role_name}]` |
| 7 | `compute_skill_gap` | `role_id`, `completed_courses: list[str]` | `missing_skills, covered_skills, total_missing, total_covered` |
| 8 | `compute_alignment_score` | `role_id`, `completed_courses: list[str]` | `alignment_score: float (0–1)` |
| 9 | `recommend_courses_to_close_gap` | `role_id`, `completed_courses: list[str]` | `missing_skills: list[dict{taught_by}]` |
| 10 | `estimate_alignment_improvement` | `role_id`, `completed_courses`, `planned_courses: list[str]` | `current_alignment_score, projected_alignment_score` |
| 11 | `find_best_matching_roles` | `completed_courses: list[str]` | `ranked_roles: list[dict{role_id,alignment_score,rank}]` |
| 12 | `get_track_overview` | `track_id` | `track_id, track_name, courses, skills_taught, supported_roles` |
| 13 | `compare_tracks` | `track_id_1`, `track_id_2` | `track_1, track_2, courses{shared,only}, skills, role_alignment` |
| 14 | `recommend_track_for_role` | `role_id` | `ranked_tracks: list[dict{track_id,alignment_score,rank}]` |
| 15 | `recommend_track_for_skill` | `skill_id` | `ranked_tracks: list[dict{track_id,rank}]` |
| 16 | `get_courses_by_track` | `track_id` | `courses: list[dict{course_code,name,credits,level,prerequisites,semester_offering,credit_threshold,track}]` |
| 17 | `get_focus_courses_for_target` | `target_id`, `target_type: "track"\|"role"`, `completed_courses` | `focus_courses: list[dict{relevant_skill_count}]` |
| 18 | `resolve_entity` | `entity_type: "course"\|"role"\|"track"\|"skill"`, `entity_text` | `resolved_id, name, match_type, confidence, status: "ok"\|"ambiguous"\|"not_found"` |

**Credit threshold note:** `get_course_profile` and `get_courses_by_track` both return `credit_threshold: int|None`. `get_prerequisites` returns `non_course_prerequisites` with `{"type": "CREDIT_THRESHOLD", "value": 59}` (int, already parsed).

---

## 8. All 8 RAG Rule Bundles

| Key | Pydantic Class | ALE Operations that consume it |
|---|---|---|
| `grading_scale_rules` | `GradingScaleRules` | simulate_gpa_forward, solve_target_gpa, run_graduation_audit |
| `graduation_requirement_rules` | `GraduationRequirementRules` | solve_target_gpa, run_graduation_audit, generate_semester_plan, generate_graduation_roadmap |
| `academic_warning_rules` | `AcademicWarningRules` | run_graduation_audit |
| `honors_rules` | `HonorsRules` | run_graduation_audit |
| `credit_limit_rules` | `CreditLimitRules` | generate_semester_plan, generate_graduation_roadmap |
| `retake_rules` | `RetakeRules` | simulate_gpa_forward, solve_target_gpa, check_course_eligibility, generate_semester_plan, generate_graduation_roadmap |
| `summer_semester_rules` | `SummerSemesterRules` | generate_semester_plan (Summer only), generate_graduation_roadmap (accelerated mode) |
| `student_level_rules` | `StudentLevelRules` | generate_semester_plan, generate_graduation_roadmap |

**CRITICAL:** `get_rule_bundles()` fires 8 Groq API calls. Cache the result. Return `{}` on failure — orchestrator must handle empty bundles gracefully.

---

## 9. All 6 ALE Operations — Full Requirements Table

### simulate_gpa_forward
- **StudentContext fields:** `cgpa`, `cumulative_chs`, `cumulative_cps` (all must be non-None)
- **Rule bundles:** `grading_scale_rules`, `retake_rules`
- **KG data:** none
- **Required params:** `planned_courses: list[dict]` (each: `course_code, course_name, credits, expected_grade, attempt_type, has_cgpa_footprint, old_grade, improve_retake_number`)
- **Optional params:** `excluded_in_progress_courses: list[str] = []`
- **Output statuses:** `projected` | `cannot_compute`
- **Orchestrator prep:** verify `cumulative_cps` non-None; supply planned courses from user input

### solve_target_gpa
- **StudentContext fields:** `cgpa`, `cumulative_chs`, `cumulative_cps`
- **Rule bundles:** `grading_scale_rules`, `retake_rules`, `graduation_requirement_rules`
- **KG data:** none
- **Required params:** `target_cgpa: float`
- **Optional params:** `planned_courses: list = []`, `planned_course_source: str = "orchestrator"`
- **Output statuses:** `solvable` | `impossible` | `already_met` | `cannot_compute`
- **Orchestrator prep:** if `target_cgpa <= current_cgpa` → returns `already_met` without needing planned courses

### check_course_eligibility
- **StudentContext fields:** `completed_courses`, `in_progress_courses`, `retake_count`, `total_improve_retakes_used`, `total_credit_hours_earned`, `cgpa`
- **Rule bundles:** `retake_rules`
- **KG data:** `course_prerequisites: list[str]`, `course_credit_threshold: int|None`
- **Required params:** `target_course_code: str`, `attempt_type: str`
- **Output statuses:** `eligible` | `not_eligible` | `already_completed` | `in_progress` | `retake_cap_exceeded` | `cannot_compute`
- **Orchestrator prep:** call `get_prerequisites(course_code)` → extract `direct_prerequisites` as list of codes + parse `non_course_prerequisites` for `CREDIT_THRESHOLD` value

### run_graduation_audit
- **StudentContext fields:** `study_status`, `completed_courses`, `failed_courses`, `in_progress_courses`, `cgpa`, `total_credit_hours_earned`, `consecutive_warnings`, `total_warnings`, `military_status`, `completed_regular_semesters`, `zero_credit_courses_passed` (converted to bool), `course_history`
- **Rule bundles:** `grading_scale_rules`, `graduation_requirement_rules`, `academic_warning_rules`, `honors_rules`
- **KG data:** none
- **Required params:** none
- **Output statuses:** `eligible` | `not_eligible` | `not_auditable` | `already_graduated` | `dismissed_but_appeal_eligible` | `dismissed_no_appeal`
- **Orchestrator prep:** `bool(effective_context.zero_credit_courses_passed)` for ALE input; course_history `credit_hours` are 0 (sentinel) — honors trajectory will be incomplete until credit patching is implemented

### generate_semester_plan
- **StudentContext fields:** `study_status`, `completed_courses`, `failed_courses`, `in_progress_courses`, `retake_count`, `total_improve_retakes_used`, `cgpa`, `total_credit_hours_earned`, `level`, `track_id`
- **Rule bundles:** `credit_limit_rules`, `graduation_requirement_rules`, `retake_rules`, `student_level_rules` + `summer_semester_rules` if Summer
- **KG data:** `available_courses` from `get_courses_by_track(track_id)["courses"]`
- **Required params:** `target_semester_type: str` ("Fall" | "Spring" | "Summer")
- **Optional params:** `specialization_credit_threshold: int = 60`, `target_track: str|None`, `target_credit_load: int|None`, `max_credits_mode: bool = False`
- **Output statuses:** `plans_generated` | `not_applicable` | `no_eligible_courses` | `cannot_compute`
- **Orchestrator prep:** fetch track courses from KG; ALE now handles semester_offering filtering internally (empty = all semesters)

### generate_graduation_roadmap
- **StudentContext fields:** `study_status`, `completed_courses`, `failed_courses`, `in_progress_courses`, `retake_count`, `total_improve_retakes_used`, `cgpa`, `cumulative_chs`, `cumulative_cps`, `total_credit_hours_earned`, `level`, `track_id`, `zero_credit_courses_passed` (bool), `military_status`, `completed_regular_semesters`
- **Rule bundles:** `grading_scale_rules`, `credit_limit_rules`, `graduation_requirement_rules`, `retake_rules`, `student_level_rules` + `summer_semester_rules` if accelerated
- **KG data:** `available_courses` from `get_courses_by_track(track_id)["courses"]` (no semester pre-filter)
- **Required params:** `target_semester_type: str`, `starting_year: int`
- **Optional params:** `specialization_credit_threshold: int = 60`, `target_track`, `assumed_grade_per_pass`, `accelerated_mode: bool = False`, `max_credits_mode: bool = False`
- **Output statuses:** `complete` | `cannot_complete_projection` | `blocked` | `not_applicable` | `cannot_compute`
- **Orchestrator prep:** derive `starting_year` from `gateway.utils.get_current_semester()`; convert `zero_credit_courses_passed` to bool; verify `cumulative_cps` non-None

---

## 10. StudentContext — Field Truth Table

| Field | Source | Reliable? | Orchestrator action |
|---|---|---|---|
| `student_id` | Excel data["ID"] | ✅ Yes | None |
| `name` | Excel data["Name"] | ✅ Yes | None |
| `program` | Excel data["Program"] | ✅ Yes | None |
| `track_id` | Derived by SCP | ✅ Yes | None |
| `level` | Derived from Excel Level column | ✅ Yes | None |
| `first_semester` | Excel | ✅ Yes | None |
| `study_status` | Excel | ✅ Yes | None |
| `military_status` | Excel | ✅ Yes | None |
| `cgpa` | Excel | ✅ Yes | None |
| `cumulative_chs` | Excel | ✅ Yes | None |
| `cumulative_cps` | Excel | ✅ Yes (may be None for new students) | Verify non-None for GPA ops |
| `total_credit_hours_earned` | Excel | ✅ Yes | None |
| `consecutive_warnings` | Excel | ✅ Yes | None |
| `total_warnings` | Excel | ✅ Yes | None |
| `completed_courses` | Derived | ✅ Yes | None |
| `failed_courses` | Derived | ✅ Yes | None |
| `in_progress_courses` | Derived | ✅ Yes | None |
| `zero_credit_courses_passed` | Derived | ✅ Yes (list[str]) | Convert to bool() for ALE |
| `retake_count` | Derived | ✅ Yes | None |
| `total_improve_retakes_used` | Derived | ✅ Yes | None |
| `completed_regular_semesters` | Derived | ✅ Yes | None |
| `course_history` | Derived | ⚠️ Partial | credit_hours=0 (sentinel); patch from KG for honors |
| `academic_standing` | **DOES NOT EXIST** | — | Orchestrator computes: `cgpa >= 2.0 and consecutive_warnings == 0 → "good", else "warning"` |
| `current_semester` | **DOES NOT EXIST** | — | Orchestrator calls `gateway.utils.get_current_semester()` |
| `planned_courses` | **DOES NOT EXIST** | — | Never existed. Remove from any stubs. |

---

## 11. SessionOverrides & Session Manager

### SessionOverrides fields
```python
added_courses: list[str] = []           # used by "planned" and "assumed_done"
assumed_failed_courses: list[str] = []  # used by "assumed_failed"
assumed_passed_courses: list[str] = []  # used by "assumed_passed"
target_role: Optional[str] = None
course_override_type: Literal["planned","assumed_done","assumed_failed","assumed_passed","gpa_scenario","none"] = "none"
override_action: Literal["accumulate","replace","clear"] = "accumulate"
```

### build_effective_context behavior
```python
build_effective_context(base_context: StudentContext, overrides: SessionOverrides) -> StudentContext
```
- `"assumed_done"` → adds `added_courses` to `completed_courses`
- `"planned"` → adds `added_courses` to `in_progress_courses`
- `"assumed_failed"` → moves `assumed_failed_courses` from `completed_courses` to `failed_courses`, removes from `zero_credit_courses_passed`
- `"assumed_passed"` → moves `assumed_passed_courses` from `failed_courses` to `completed_courses`
- `"gpa_scenario"` → NOT HANDLED → returns base_context unchanged (deferred)
- `"none"` → returns base_context unchanged

**Does NOT recalculate:** `cgpa`, `cumulative_chs`, `cumulative_cps`, `total_credit_hours_earned`. These remain stale after overrides.

### Session Manager public methods
```python
get_or_create_session(session_id, student_id, context, first_message) -> (SessionState, is_new: bool)
get_qu_context(session_id, user_text) -> QUContext | None
apply_query_result(session_id, structured_query) -> None  # updates overrides + last_referenced
build_effective_context(base_context, overrides) -> StudentContext
update_session_after_turn(session_id, user_text, answer_text, ...) -> None
delete_session(session_id) -> bool
```

---

## 12. Gateway Schemas (Key Ones)

### StructuredQuery (from QU to Orchestrator)
```python
intent: str
engine_pattern: str          # "kg" | "rag" | "ale" | "mixed" | "clarification"
query_type: str              # "student_aware" | "general"
original_text: Optional[str]
entities: EntitySet          # course_code, role_id, track_id, skill_id
secondary_entities: Optional[EntitySet]
needs_clarification: bool
clarification_prompt: Optional[str]
session_overrides: SessionOverrides
```

### ResultPackage (Orchestrator to Composer)
```python
original_query: str
intent: str
engine_pattern: str
kg_result: Optional[dict]
rag_result: Optional[RAGResult]
ale_result: Optional[dict]
composer_context: Optional[ComposerContext]
status: str                  # "ok" | "error" | "clarification_needed"
error_detail: Optional[str]
```

### ComposerContext (Orchestrator must populate ALL fields)
```python
track_id: str
level: int
cgpa: Optional[float]
academic_standing: str       # orchestrator computes this
study_status: str
total_credit_hours_earned: int
current_semester: str        # orchestrator gets from get_current_semester()
consecutive_warnings: int
```

---

## 13. Locked Intent Map (8 Domains)

### Domain 1 — Academic Planning (RAG bundles + KG + ALE)
| Intent | ALE Function | KG needed | Rule bundles needed |
|---|---|---|---|
| `plan_next_semester` | `generate_semester_plan` | `get_courses_by_track` | credit_limit, graduation, retake, student_level |
| `generate_graduation_roadmap` | `generate_graduation_roadmap` | `get_courses_by_track` | grading_scale, credit_limit, graduation, retake, student_level |
| `run_graduation_audit` | `run_graduation_audit` | none | grading_scale, graduation, academic_warning, honors |
| `check_course_eligibility` | `check_course_eligibility` | `get_prerequisites` | retake |
| `simulate_gpa_forward` | `simulate_gpa_forward` | none | grading_scale, retake |
| `solve_target_gpa` | `solve_target_gpa` | none | grading_scale, retake, graduation |

### Domain 2 — Course Information (KG only)
`get_course_info`, `get_course_prerequisites`, `get_courses_by_track`, `get_skills_taught`, `search_courses_by_skill`

### Domain 3 — Career & Role Guidance (KG + Context)
`get_role_profile`, `get_roles_by_track`, `compute_skill_gap`, `compute_alignment_score`, `get_focus_courses_for_target`, `recommend_courses_to_close_gap`, `find_best_matching_roles`, `estimate_alignment_improvement`

### Domain 4 — Track Guidance (KG only)
`get_track_overview`, `compare_tracks`, `recommend_track_for_role`, `recommend_track_for_skill`

### Domain 5 — Policy & Handbook (RAG free-text only)
`policy_query` → `rag.execute(original_text)`

### Domain 6 — Student Record (Context + KG name resolution)
`get_record_summary`, `get_academic_standing`

### Domain 7 — Mixed Workflows (Sequential dependent)
- **Type A (parallel):** QU splits into multiple StructuredQuery objects → orchestrator runs each → composer combines
- **Type B (sequential):** output of one engine feeds into next → orchestrator sequences
  - `graduation_audit_with_roadmap`: audit → if not eligible → roadmap (conditional branch)
  - `focus_courses_for_current`: get in_progress_courses from context → `get_focus_courses_for_target`

### Domain 8 — System Responses
`clarification_needed`, `out_of_scope`

---

## 14. What Orchestrator Must Do — Per Turn

```
1. Receive StructuredQuery from QU
2. Load session (already done in main.py before orchestrator)
3. Compute current_semester = get_current_semester()
4. Compute academic_standing from cgpa + consecutive_warnings
5. Build effective_context = build_effective_context(session.student_context, session.overrides)
6. Resolve entities: if entity is natural text → call resolve_entity(entity_type, text)
7. Route by intent/engine_pattern:
   a. KG-only: call kg.call(operation, params) → put in kg_result
   b. RAG-only: call rag.execute(query) → put in rag_result
   c. ALE: fetch rule_bundles (cached), fetch KG data if needed, call ale.call(...) → put in ale_result
   d. Mixed: sequence calls per workflow type
8. Build ComposerContext with computed academic_standing + current_semester
9. Return ResultPackage(kg_result, rag_result, ale_result, composer_context, status)
```

**What orchestrator must NOT do:**
- Read from Excel (SCP handles that in main.py)
- Write to session store (main.py handles after orchestrator returns)
- Format the final answer (ResponseComposer handles that)
- Hardcode any academic rule values
- Raise exceptions to callers (catch all → return error ResultPackage)

---

## 15. What Orchestrator Must Patch/Enrich Before ALE

| What | How | When |
|---|---|---|
| `academic_standing` for ComposerContext | `"good" if cgpa >= 2.0 and consecutive_warnings == 0 else "warning"` | Every turn |
| `current_semester` for ComposerContext | `gateway.utils.get_current_semester()` | Every turn |
| Effective course lists | `build_effective_context(session.student_context, session.overrides)` | Before any ALE/student-aware KG call |
| `zero_credit_courses_passed` → bool | `bool(effective_context.zero_credit_courses_passed)` | For run_graduation_audit, generate_graduation_roadmap |
| `kg_data["course_prerequisites"]` | `kg.call("get_prerequisites", {"course_code": code, "depth": "direct"})["direct_prerequisites"]` → list of code strings | For check_course_eligibility |
| `kg_data["course_credit_threshold"]` | From same get_prerequisites call: find CREDIT_THRESHOLD in `non_course_prerequisites["value"]` | For check_course_eligibility |
| `kg_data["available_courses"]` | `kg.call("get_courses_by_track", {"track_id": track_id})["courses"]` | For generate_semester_plan, generate_graduation_roadmap |
| `starting_year` | Parse from `get_current_semester()` | For generate_graduation_roadmap |
| `course_history[*].credit_hours` | `kg.call("get_course_profile", {"course_code": code})["credits"]` per course → rebuild frozen CourseRecord objects | For accurate honors audit (high-value deferred gap) |

---

## 16. What Orchestrator Should Cache

| Data | Why | Strategy |
|---|---|---|
| Rule bundles | 8 Groq API calls — rate limit risk | Cache at startup or once per session; invalidate on ingest |
| Track courses | `get_courses_by_track` results | Cache per track_id per session |

---

## 17. Known Deferred Issues (Orchestrator Must Handle)

| Issue | Current behavior | Orchestrator responsibility |
|---|---|---|
| `course_history[*].credit_hours = 0` | SCP sentinel | Fetch from KG, rebuild frozen CourseRecord objects. Deferred to orchestrator design. |
| `academic_standing` not in StudentContext | Field absent | Compute inline: cgpa >= 2.0 and warnings == 0 |
| `current_semester` not in StudentContext | Field absent | Call `gateway.utils.get_current_semester()` |
| Numeric aggregates stale after overrides | Not recalculated | Warn user results are hypothetical; note in ResultPackage |
| `gpa_scenario` override type unhandled | Returns base_context unchanged | Design in this chat |
| Override field routing | Not documented | QU must populate correct field per type; validate in orchestrator |
| `zero_credit_courses_passed` list vs bool | ALE expects bool | `bool(effective_context.zero_credit_courses_passed)` |

---

## 18. Key Architecture Constraints (Non-Negotiable)

1. **No rule values hardcoded** — all rules come from RAG bundles
2. **ALE receives all data as structured input** — never fetches externally
3. **KG, RAG, ALE are pure engines** — orchestrator handles all coordination
4. **QU calls `resolve_entity()`** for entity normalization (not orchestrator)
5. **Orchestrator follows pre-mapped intent workflows** — not dynamic discovery
6. **Response Composer handles all NLG** — orchestrator never writes prose
7. **No PII ever logged** — QU and Composer never receive raw student identity

---

## 19. Where to Start in This Chat

**Step 1 (first thing):** Review this document and confirm you understand everything. Give a one-paragraph summary.

**Step 2:** Design the `StructuredQuery` → Orchestrator interface contract (what exact object the orchestrator receives from QU).

**Step 3:** Design the orchestrator class structure — constructor, instance variables, main entry point.

**Step 4:** Work through each intent level from easy to hard:
- Level 1: Single engine, no dependencies (pure KG, pure RAG, pure context)
- Level 2: Context + single engine (KG with student data)
- Level 3: RAG bundles + single ALE (no KG data needed)
- Level 4: RAG bundles + KG + ALE (full planning)
- Level 5: Sequential dependent multi-step (audit → roadmap)
- Level 6: Type A parallel (QU splits)

**Step 5:** Design rule bundle caching strategy.

**Step 6:** Design error handling contract.

**Step 7:** Lock everything, then send to Claude Code for implementation.

---

*End of handoff document. All components above are implemented, tested, and verified. The orchestrator is the missing connector.*
