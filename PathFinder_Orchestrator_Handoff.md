> ⚠️ ARCHIVED / SUPERSEDED DOCUMENT  
> This file is historical reference only.  
> Do not use it as Orchestrator design authority.  
> The authoritative Orchestrator design is:
> `PathFinder_Orchestrator_Phase1_Phase2_Locked_Design.md`
> after replacement with the Phases 1–6 locked version.
>
> Use this handoff only for codebase inventory, historical audit notes, file structure, adapter notes, and old component status.
> If this file conflicts with the locked Phases 1–6 MD, follow the locked MD.
# PathFinder — Orchestrator Design Planning Handoff
**Version:** 2.0 (Final Pre-Orchestrator) | **Date:** June 2026 | **Branch:** person-seif
**Codebase:** `O:\Graduation Project\PathFinder_Integration\`

---

## 1. Your Role in This Chat

You are a technical project assistant helping develop PathFinder, an AI-powered academic advising system for the CIS program at EUI (Egyptian University of Informatics).

**Your job in this chat:** Plan and lock the Orchestrator design. Do not implement the Orchestrator yet. The locked Orchestrator plan will be used to guide Query Understanding (QU) planning and implementation. After QU is implemented, we will return to the Orchestrator plan, revise it if needed, and then implement the Orchestrator.

**How we work:**
- Discuss and lock every design decision before Claude Code touches anything
- One problem at a time — no jumping ahead
- No autonomous decisions from Claude Code
- Cross-validate logic-heavy decisions when needed
- Seif reviews all Claude Code output before proceeding

**Additional working style rules:**
- Distinguish between these four categories before acting:
  1. Document correction — fix inaccurate content in this file
  2. Design decision — discuss and lock before any implementation
  3. Code bug — fix in codebase, not a design decision
  4. Future integration note — note for later, not an immediate change
- Claude Code must not make autonomous architectural decisions
- Seif reviews all Claude Code output before proceeding

---

## 2. System Overview

PathFinder answers student queries about curriculum, graduation, career paths, and academic policy. Three engines are coordinated by a gateway layer:

```text
Student Query
     ↓
[Query Understanding (QU)] ← exists, not final — will be updated after Orchestrator contracts are locked
     ↓ StructuredQuery (with already-resolved entity IDs)
[Session Manager] ← load/update session state
     ↓ SessionState + StudentContext
[ORCHESTRATOR] ← planning target — implementation comes after QU is finalized
     ↓ ResultPackage
[Response Composer] ← exists, not final — will be updated after Orchestrator contracts are locked
     ↓
Final Answer
```

**Flow scope note:** This diagram represents the per-query execution flow only. App startup, student Excel loading, adapter initialization, cache initialization, and greeting/welcome generation are outside the Orchestrator path. These belong to the Gateway/UI/Composer layers.

**Startup vs per-query distinction:**
- Startup: Gateway starts app, loads Excel, initializes adapters, may initialize rule bundle cache.
- Per-query: User query → QU → Session Manager → Orchestrator → Composer.

Greeting and GPA-based welcome behavior is NOT an Orchestrator responsibility. It belongs to the UI or Composer layer.

---

## 3. Component Status

| Component | Status | Tests | Notes |
|---|---|---|---|
| KG Engine (Neo4j) | ✅ Done | 18/18 live | 18 operations, A1–A7 grouping, OP numbering locked |
| RAG Engine | ✅ Done | 4/4 pass, 5 skip (rate limit) | Hybrid retrieval + Groq; deep audit completed; integration notes in Section 8A |
| ALE Engine | ✅ Done | 19/19 pass | 6 functions, semester filter fixed; deep audit complete; all rule-driven fixes applied; integration notes in Section 9A |
| KG Adapter | ✅ Done | 18/18 live | Pure pass-through, 18/18 live tests; deep adapter audit completed; integration notes in Section 7A |
| RAG Adapter | ✅ Done | 4/4 pass, 5 skip (rate limit) | _as_dict() fix applied; deep audit completed; execute_structured error propagation fixed; partial rule-bundle loading implemented; integration notes in Section 8A |
| ALE Adapter | ✅ Done | 23/23 pass | grade_points mapping fixed; deep audit complete; ValidationError → cannot_compute fixed; course_credit_lookup support added; assumed_grade resolution added; unused bundle parsing removed; integration notes in Section 9A |
| SCP | ✅ Done / Audited & Locked | 23/23 pass | Excel → StudentContext; validation and regression fixes complete |
| Session Manager | ✅ Audited & Fixed | 29/29 pass | Override cleanup, schema compatibility, typed turn history, and SQLite blob handling fixed |
| Gateway Schemas | ✅ Audited & Fixed | Covered by SCP + Session tests (52/52 combined) | Pydantic contracts cleaned for current scope; status enums and typed turn history added |
| **Orchestrator** | **🔧 Planning now** | — | Stub exists but broken — design/planning now; implementation after QU is finalized |
| Query Understanding | ⚠️ Exists, not final | — | Basic stub exists; does not reflect locked resolve_entity architecture; will be redesigned after Orchestrator contracts are clear |
| Response Composer | ⚠️ Exists, not final | — | Basic stub exists; must align with ResultPackage and ComposerContext after Orchestrator is done |
| API (main.py) | ⚠️ Broken | — | Wrong import names, broken session calls — fix after QU + Orchestrator are locked |
| UI | ⚠️ Exists, not priority | — | Streamlit demo exists; not focus now |

**Do NOT merge test counts into a single total.** Tests are listed per component above.

**Known broken things to fix (in order, after Orchestrator is done):**
1. `main.py` imports `create_session`, `get_session`, `get_student_sessions`, `get_session_history`, `update_session_after_turn` — none of these exist. Correct names: `get_or_create_session`, `get_qu_context`, `apply_query_result`, `append_turn`, `build_effective_context`
2. Orchestrator stub references `ctx.planned_courses`, `ctx.academic_standing`, `ctx.current_semester` — none of these fields exist on `StudentContext`
3. Orchestrator stub uses wrong `ALEAdapter.call()` signature
4. Orchestrator stub calls "check_eligibility" — correct operation name is "check_course_eligibility". Fix during Orchestrator implementation.
5. **Session Manager critical logic bugs fixed (29/29 pass):**
   - `assumed_done` cleans `failed_courses` and `in_progress_courses`
   - `assumed_passed` cleans `failed_courses` and `in_progress_courses`
   - `assumed_failed` cleans `completed_courses`, `in_progress_courses`, and `zero_credit_courses_passed`
   - SQLite corrupted blobs handled gracefully
   - Remaining MVP design note: session state does not recalculate GPA/credits after overrides
   - Remaining design note: `course_override_type` stores latest type only; mixed override semantics must be handled carefully during QU/Orchestrator planning

---

## 4. Project File Structure

```text
PathFinder_Integration/
├── adapters/
│   ├── kg_adapter.py
│   ├── rag_adapter.py
│   └── ale_adapter.py
├── engines/
│   ├── kg/
│   │   ├── queries.py              — all 18 Cypher query functions (A1–A7 groups)
│   │   ├── neo4j_client.py
│   │   └── data/entity_aliases.json — 59/59 courses, 18/18 roles, 29/29 skills, 5/5 tracks; MVP-clean
│   ├── rag/
│   │   ├── rag_core.py
│   │   ├── retriever.py
│   │   ├── ingest.py
│   │   ├── CIS_Handbook.md
│   │   ├── chroma_db/              — ChromaDB persistence
│   │   └── chunks.pkl              — BM25 parent chunks
│   └── ale/
│       ├── schemas.py              — ALL ALE + rule bundle Pydantic models
│       ├── utils/
│       │   └── grade_resolver.py
│       └── functions/
│           ├── simulate_gpa_forward.py
│           ├── solve_target_gpa.py
│           ├── check_course_eligibility.py
│           ├── run_graduation_audit.py
│           ├── generate_semester_plan.py
│           └── generate_graduation_roadmap.py
├── gateway/
│   ├── orchestrator.py             — BROKEN STUB — design/planning now; full rewrite after QU is finalized
│   ├── query_understanding.py      — exists, not final
│   ├── response_composer.py        — exists, not final
│   ├── session_manager.py          — done
│   ├── student_context_provider.py — done
│   ├── utils.py                    — get_current_semester() lives here (system-wide temporal utility, not student-derived)
│   ├── llm_client.py
│   └── models/
│       └── schemas.py              — audited/fixed; status enums and typed turn history added; no dedicated schema test file yet
├── gateway/session_store/
│   ├── base.py
│   └── sqlite_store.py
├── data/
│   └── students_anonymous.xlsx     — student data (2 sheets: data, registrations)
├── tests/
│   ├── smoke_test_ale_adapter.py       — 19/19 pass
│   ├── test_session_manager.py         — 29/29 pass (20 existing + 9 new regression tests)
│   ├── test_semester_offering_filter.py — 8/8 pass
│   ├── test_student_context_provider.py — 23/23 pass (10 regression tests added for SCP edge cases)
│   ├── test_kg_adapter.py              — 18/18 pass, requires Neo4j
│   ├── test_rag_adapter.py             — 4/4 pass, 5 skip (Groq rate limit)
│   ├── rag_manual_test.py              — manual RAG test script
│   └── conftest.py                     — shared pytest fixtures
│   (Schema compatibility is currently covered by test_session_manager.py + test_student_context_provider.py; combined relevant validation: 52/52 pass)
├── ui/
│   └── streamlit_app.py            — Streamlit demo, not final priority
├── main.py                         — FastAPI entry point (broken imports, fix after Orchestrator)
├── requirements.txt
├── README.md
├── pathfinder_sessions.db          — SQLite session store (auto-created)
├── .env                            — real local values, never commit
└── .env.example                    — placeholder values only, safe to commit
```

**Current semester mapping (locked):**
- September–January → Fall
- February–June → Spring
- July–August → Summer

`gateway/utils.get_current_semester()` implements this mapping. This is a system-wide temporal utility, not student-derived.

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

**Important:** `.env` contains real local values and must never be committed. `.env.example` contains placeholder values only and is safe to commit. If `.env_example` (old duplicate) exists, delete it.

**LLM model note:** `LLM_MODEL` is currently shared for QU and Composer. The final model/provider configuration for each component is TBD during QU and Composer design phases.

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
# On engine unavailable: {"answer": "RAG Engine is currently unavailable.", ...}

rag.execute_structured(sub_query: str, expected_schema: dict) -> dict
# Returns on success: {"data": {...}, "citations": list[dict]}
# Returns on failure: {"data": {}, "citations": list[dict], "error": str}
# Important: error key is propagated from rag_core. Callers must check "error" before reading data.

rag.get_rule_bundles() -> dict[str, BaseModel | None]
# Returns on full/partial success: all 8 rule-bundle keys.
# Successful bundles contain Pydantic model instances.
# Failed bundles contain None.
# Returns {} only if all bundle conversions fail.
# CRITICAL: Fires 8 Groq API calls sequentially. Must be loaded once at startup/initialization and cached.
# Never call per-operation or per-query.
# Orchestrator must check that the specific rule bundles required by an ALE operation are not None before calling ALE.
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
Returns model.model_dump() on success, {"status": "cannot_compute", ...} for validation/data-input failures, or {"status": "error", "message": str} for programmer/infrastructure errors.

---

## 7. All 18 KG Operations

> **Contract note:** Function names are the stable runtime contract; operation numbers are documentation/grouping labels only.

| # | Group | Operation | Required Params | Key Return Fields |
|---|---|---|---|---|
| 1 | A1 | `get_course_profile` | `course_code` | `course_code, name, credits, level, semester_offering, tracks, description, credit_threshold` |
| 2 | A1 | `get_prerequisites` | `course_code`, `depth="direct"` | `direct_prerequisites: list[dict]`, `non_course_prerequisites: list[dict]`, `has_prerequisites` |
| 3 | A1 | `get_skills_taught` | `course_code` | `skills_taught: list[dict{skill_id,name,category}]` |
| 4 | A1 | `search_courses_by_skill` | `skill_ids: list[str]` | `queried_skill_ids, unrecognized_skill_ids, results: list[dict], total_results` |
| 5 | A2 | `get_role_profile` | `role_id` | `role_id, role_name, required_skills: list[dict{skill_id,name,tier,weight}]` |
| 6 | A2 | `get_roles_by_track` | `track_id` | `results: list[dict{role_id,role_name}]` |
| 7 | A3 | `compute_skill_gap` | `role_id`, `completed_courses: list[str]` | `missing_skills, covered_skills, total_missing, total_covered` |
| 8 | A3 | `compute_alignment_score` | `role_id`, `completed_courses: list[str]` | `alignment_score: float (0–1)` |
| 9 | A3 | `recommend_courses_to_close_gap` | `role_id`, `completed_courses: list[str]` | `missing_skills: list[dict{taught_by}]` |
| 10 | A3 | `estimate_alignment_improvement` | `role_id`, `completed_courses`, `planned_courses: list[str]` | `current_alignment_score, projected_alignment_score` |
| 11 | A3 | `find_best_matching_roles` | `completed_courses: list[str]` | `ranked_roles: list[dict{role_id,alignment_score,rank}]` |
| 12 | A4 | `get_track_overview` | `track_id` | `track_id, track_name, courses, skills_taught, supported_roles` |
| 13 | A4 | `compare_tracks` | `track_id_1`, `track_id_2` | `track_1, track_2, courses{shared,only}, skills, role_alignment` |
| 14 | A4 | `recommend_track_for_role` | `role_id` | `ranked_tracks: list[dict{track_id,alignment_score,rank}]` |
| 15 | A4 | `recommend_track_for_skill` | `skill_id` | `ranked_tracks: list[dict{track_id,rank}]` |
| 16 | A5 | `get_courses_by_track` | `track_id` | `courses: list[dict{course_code,name,credits,level,prerequisites,semester_offering,credit_threshold,track}]` — orchestrator planning only, not user-facing |
| 17 | A6 | `get_focus_courses_for_target` | `target_id`, `target_type: "track"\|"role"`, `completed_courses` | `focus_courses: list[dict{relevant_skill_count}]` |
| 18 | A7 | `resolve_entity` | `entity_type: "course"\|"role"\|"track"\|"skill"`, `entity_text` | `resolved_id, name, match_type, confidence, status: "ok"\|"ambiguous"\|"not_found"` |

**Credit threshold note:** `get_course_profile` and `get_courses_by_track` both return `credit_threshold: int|None`. `get_prerequisites` returns `non_course_prerequisites` with `{"type": "CREDIT_THRESHOLD", "value": 59}` (int, already parsed).

**OP4 contract note:** `search_courses_by_skill` now uses exact skill_id matching (not name matching). QU must resolve skill names/text to skill_id via resolve_entity("skill", ...) before Orchestrator calls OP4. KG returns all matching courses with no hard cap; Composer decides how many to display.

**OP18 contract note:** `resolve_entity` is primarily a QU tool. QU calls this operation during entity understanding and outputs resolved IDs in StructuredQuery. Orchestrator receives resolved IDs and validates required IDs before calling engines. Orchestrator does NOT call resolve_entity as a primary step.

**entity_aliases.json status (MVP-clean and locked):**
- Courses: 59/59
- Roles: 18/18
- Skills: 29/29
- Tracks: 5/5
- Invalid alias references: none
- Invalid ambiguous_terms references: none

---

## 7A. KG Adapter Contract & Integration Notes

**Status:** Audit completed and locked.

**Architecture:** Thin dispatch layer. `__init__` initializes `Neo4jClient` with 3 retry attempts (2s between each); gracefully degrades to `_client = None` on failure. `call(operation, params)` validates Neo4j availability, looks up operation in dispatch table, unpacks `**params` to method, catches errors uniformly. All 18 methods are pure passthroughs to `engines.kg.queries` functions (zero business logic, zero transformation).

**Error Taxonomy (Adapter Layer):**

| Error Key | Cause | Orchestrator Action |
|---|---|---|
| `kg_unavailable` | Neo4jClient not connected at startup | Skip KG-dependent workflows; provide user message |
| `unknown_operation` | Operation string not in dispatch table | Fix Orchestrator param (bug in operation name) |
| `bad_params` | Wrong keyword argument names/count | Fix Orchestrator param mapping (contract violation) |
| `kg_error` | Unhandled exception inside queries.py | Log and escalate; may be transient or query bug |

Query-level errors (`course_not_found`, `role_not_found`, `no_courses_provided`, etc.) are distinct — they are business logic results, not adapter failures. Orchestrator must check both adapter-level and query-level error shapes.

**Critical Integration Notes:**

1. **`resolve_entity` (OP18) uses `status` field, not `error` key.**
   - Other operations: `{"error": "code"}` on failure
   - `resolve_entity`: Returns one of:
     - `{"status": "ok", "resolved_id": "...", ...}`
     - `{"status": "ambiguous", "matches": [...], ...}`
     - `{"status": "not_found", "matches": [], ...}`
     - `{"status": "error", "error": "...", ...}`
   - QU must branch on `result["status"]` first, not check `"error" in result`. Ambiguous results require user clarification; never silently pick first match.

2. **OP7–OP11 reject empty `completed_courses` at validation layer.**
   - Affected: `compute_skill_gap`, `compute_alignment_score`, `recommend_courses_to_close_gap`, `estimate_alignment_improvement`, `find_best_matching_roles`
   - Return: `{"error": "no_courses_provided"}` before touching Neo4j
   - Exception: `get_focus_courses_for_target` (OP17) explicitly allows empty `completed_courses` for freshman
   - Orchestrator action: Pre-check `completed_courses` before dispatching OP7–OP11

3. **Parameter names must match method signatures exactly.**
   - `call(operation, params)` unpacks as `fn(**params)`
   - Wrong key names → `bad_params` error
   - Use exact names from the 18-operation table in Section 7

4. **No adapter-level caching.**
   - Each call is fresh to the KG engine
   - OP16 (`get_courses_by_track`) and OP12 (`get_track_overview`) are stable per session
   - Cache these at Orchestrator/Session Manager level, not in adapter
   - They will be reused multiple times during planning workflows

5. **Alias file for `resolve_entity` at `engines/kg/data/entity_aliases.json`.**
   - Path is relative to `queries.py`
   - Do not move without updating `_ALIAS_FILE_PATH` constant

**Verification:** Live integration smoke test in `tests/test_kg_adapter.py` covers all 18 operations against real Neo4j. All pass, including credit_threshold parsing.

**Classification:**

| Item | Status | Decision |
|---|---|---|
| KG Adapter thin dispatch pattern | ✅ Locked | Keep as-is; correct architecture |
| Adapter error wrapping | ✅ Locked | 4-tier error model is sufficient |
| `resolve_entity` status-based response | ⚠️ Integration note | QU must branch on `status`, not `error` |
| OP7–OP11 empty completed_courses guard | ⚠️ Integration note | Orchestrator pre-check required |
| OP16 caching | ⚠️ Design note | Cache at Orchestrator/session level |
| OP12 caching | 🕒 Optional future | Only if profiling shows repeated calls |
| Parameter name exactness | ⚠️ Integration note | Orchestrator must match method signatures |
| Alias file location | ⚠️ Structural note | Relative path; do not move without code update |

---

## 8. All 8 RAG Rule Bundles

| Key | Pydantic Class | ALE Operations that consume it |
|---|---|---|
| `grading_scale_rules` | `GradingScaleRules` | simulate_gpa_forward, solve_target_gpa, run_graduation_audit |
| `graduation_requirement_rules` | `GraduationRequirementRules` | solve_target_gpa, run_graduation_audit, generate_semester_plan, generate_graduation_roadmap |
| `academic_warning_rules` | `AcademicWarningRules` | run_graduation_audit |
| `honors_rules` | `HonorsRules` | run_graduation_audit |
| `credit_limit_rules` | `CreditLimitRules` | generate_semester_plan, generate_graduation_roadmap |
| `retake_rules` | `RetakeRules` | simulate_gpa_forward, solve_target_gpa, check_course_eligibility |
| `summer_semester_rules` | `SummerSemesterRules` | generate_semester_plan (Summer only), generate_graduation_roadmap (accelerated mode) |
| `student_level_rules` | `StudentLevelRules` | generate_graduation_roadmap |

**CRITICAL:** `get_rule_bundles()` fires 8 Groq API calls sequentially. Cache the result at startup — never call per-query or per-operation. Returns `dict[str, BaseModel | None]` on partial/full success (all 8 keys present; failed bundles are `None`). Returns `{}` only if all conversions fail. Orchestrator must verify each required bundle is not `None` before calling ALE. See Section 8A for full contract.

---

## 8A. RAG Engine & RAG Adapter Contract — Integration Notes

**Status:** Audit completed and locked. RAG Adapter error propagation and partial rule-bundle loading fixes applied.

### Runtime Architecture

RAG has four layers:

1. `ingest.py` — one-time preprocessing script. Reads `CIS_Handbook.md`, splits it into parent and child chunks, embeds child chunks, stores child vectors in ChromaDB, and stores parent chunks in `chunks.pkl`.
2. `retriever.py` — runtime hybrid retriever. Uses child-vector search, BM25 parent search, RRF merge, and cross-encoder reranking.
3. `rag_core.py` — RAG engine. Retrieves relevant handbook chunks, calls Groq, and returns extracted facts or structured JSON.
4. `rag_adapter.py` — gateway-facing integration boundary used by Orchestrator/ALE workflows.

### Retrieval Design

The RAG pipeline uses parent-child retrieval:

- Child chunks are smaller and embedded for precise semantic search.
- Parent chunks are larger and sent to the LLM for richer context.
- Vector search handles semantic matching.
- BM25 handles exact keyword matching.
- RRF merges both result lists.
- Cross-encoder reranking selects final parent chunks.

This design is appropriate for handbook rules because it balances semantic search with exact policy/rule matching.

### RAGAdapter Public Contract

| Method | Purpose | Main Caller |
|---|---|---|
| `execute(sub_query, student_context=None)` | Free-text handbook Q&A | Orchestrator |
| `execute_structured(sub_query, expected_schema)` | Schema-forced extraction | `get_rule_bundles()` |
| `get_rule_bundles()` | Extracts 8 ALE rule bundles from handbook | Startup / initialization layer |

### `execute_structured()` Error Contract

`execute_structured()` propagates errors from `rag_core`.

Expected shapes:

```python
# Success
{"data": {...}, "citations": [...]}

# Failure
{"data": {}, "citations": [...], "error": "..."}
```

Any caller of `execute_structured()` must check `"error"` before trusting `data`.

### Rule Bundle Loading and Caching

`get_rule_bundles()` fires 8 sequential Groq calls. It must not be called per query or per ALE operation.

Required integration behavior:

- Load rule bundles once at startup or adapter/service initialization.
- Cache and reuse them for all subsequent ALE calls.
- If loading returns `{}`, treat this as total RAG rule-bundle failure.
- If loading returns a dict, check each required bundle value before calling ALE.
- Never pass a required `None` bundle into an ALE operation.

### Partial Rule-Bundle Contract

`get_rule_bundles()` now supports partial success.

Return shapes:

```python
# Full success
{
    "retake_rules": RetakeRules(...),
    "credit_limit_rules": CreditLimitRules(...),
    ...
}

# Partial success
{
    "retake_rules": RetakeRules(...),
    "credit_limit_rules": None,
    "summer_semester_rules": SummerSemesterRules(...),
    ...
}

# Total failure
{}
```

Rules:

- If at least one bundle succeeds, all 8 keys are returned.
- Successful bundles contain Pydantic model instances.
- Failed bundles contain `None`.
- If all bundle conversions fail, `{}` is returned.
- Per-bundle extraction errors and Pydantic conversion errors are logged with the specific bundle name.

### Orchestrator Responsibility

The Orchestrator must not assume that a non-empty rule bundle dict means all rule bundles are usable.

Before calling an ALE operation, it must check that every rule bundle required by that operation is present and not `None`.

Example:

```python
required = ["grading_scale_rules", "retake_rules"]

if not rule_bundles:
    # total failure
    return structured_error("academic_rules_unavailable")

missing = [k for k in required if rule_bundles.get(k) is None]
if missing:
    return structured_error(
        "required_rule_bundles_unavailable",
        missing_rule_bundles=missing,
    )

# safe to call ALE
```

This preserves successful bundles while preventing ALE from running with incomplete rule data.

### Remaining RAG Engine Notes

| Finding | Classification | Decision |
|---|---|---|
| `rag_core.py` defaults to `llama-3.1-8b-instant` | Design/config issue | Keep for now; defer model-config cleanup |
| RAGAdapter does not pass `groq_model` override | Integration/config note | Keep for now; decide later whether rule bundles need stronger model |
| Top 6 source documents are cited, but only top 3 chunks are sent to LLM | Minor citation-integrity note | Document only; no immediate change |
| Context truncation to 1500 chars per chunk | Legacy/design note | Keep for now |
| Retriever startup failure sets retriever to `None` and returns empty results | Startup health note | Startup/integration should detect RAG readiness |
| No retry/backoff for Groq calls | Future reliability improvement | Not required for MVP |

### Testing Note

Existing RAG Adapter tests remain `4/4 pass, 5 skipped`. The new error-propagation and partial-bundle failure paths were manually reviewed but are not currently covered by automated tests.

---

## 9. All 6 ALE Operations — Full Requirements Table

### simulate_gpa_forward
- **StudentContext fields:** `cgpa`, `cumulative_chs`, `cumulative_cps` (all must be non-None — pre-check before calling)
- **Rule bundles:** `grading_scale_rules`, `retake_rules`
- **KG data:** none directly — but Orchestrator must fetch credits
- **Required params:** `planned_courses: list[dict]` (each entry requires: `course_code, course_name, credits, expected_grade, attempt_type, has_cgpa_footprint, old_grade, improve_retake_number, is_currently_in_progress`)
- **Optional params:** `excluded_in_progress_courses: list[str] = []`
- **Output statuses:** `projected` | `cannot_compute`
- **Orchestrator prep:**
  1. Pre-check `cgpa`, `cumulative_chs`, `cumulative_cps` all non-None; return user message if missing
  2. Build `planned_courses` from QU entities + SCP `in_progress_courses`
  3. For each course, fetch real credits via `KG.get_course_profile(course_code)` — do NOT use `credit_hours=0` sentinel from SCP
  4. Derive per-course fields from StudentContext:
     - `attempt_type`: first_attempt / failed_retake / improve_retake
     - `has_cgpa_footprint`: course in history?
     - `old_grade`: from course_history
     - `improve_retake_number`: from `retake_count` dict
     - `is_currently_in_progress`: from `in_progress_courses`
  5. Handle natural language patterns: "all B in current courses" → expand `in_progress_courses` into `planned_courses` with `expected_grade="B"`
  6. Merge session overrides into `planned_courses` without mutating base StudentContext
  7. Cache course credit lookups at session level

### solve_target_gpa
- **StudentContext fields:** `cgpa`, `cumulative_chs`, `cumulative_cps` (all must be non-None — pre-check before calling)
- **Rule bundles:** `grading_scale_rules`, `retake_rules`, `graduation_requirement_rules`
- **KG data:** none directly — but Orchestrator must fetch credits for courses
- **Required params:** `target_cgpa: float`
- **Optional params:** `planned_courses: list = []`, `planned_course_source: str = "orchestrator"`
- **Output statuses:** `solvable` | `impossible` | `already_met` | `cannot_compute`
- **Orchestrator prep:**
  1. Extract `target_cgpa` from QU entity extraction
  2. If `target_cgpa <= current_cgpa` → function returns `already_met` before requiring planned courses (early check already implemented)
  3. Build `planned_courses` same as simulate_gpa_forward — real credits from KG, per-course fields from StudentContext
  4. Supply `historical_grade` per course by looking up prerequisite course grade from `course_history` (enables personalized distribution; silently skipped if not supplied)
  5. Set `planned_course_source` to indicate origin
  6. Handle query variations: "what do I need in my current courses to get 3.0?" → expand `in_progress_courses` into `planned_courses`
  7. Verify `graduation_requirement_rules` non-None (needed for multi-semester projection in impossible case)

### check_course_eligibility
- **StudentContext fields:** `completed_courses`, `in_progress_courses`, `retake_count`, `total_improve_retakes_used`, `total_credit_hours_earned`, `cgpa`
- **Rule bundles:** `retake_rules`
- **KG data:** `course_prerequisites: list[str]`, `course_credit_threshold: int|None`
- **Required params:** `target_course_code: str`, `attempt_type: str`
- **Output statuses:** `eligible` | `not_eligible` | `already_completed` | `in_progress` | `retake_cap_exceeded` | `cannot_compute`
- **Orchestrator prep:**
  1. Call `get_prerequisites(course_code)` → extract `direct_prerequisites` as list of codes + parse `non_course_prerequisites` for `CREDIT_THRESHOLD`
  2. Derive `attempt_type` from student records:
     - Course never in history → `"first_attempt"`
     - Course in `failed_courses` → `"failed_retake"`
     - Course in `completed_courses` (passed) → `"improve_retake"`
  3. If `attempt_type == "improve_retake"` and `cgpa is None` → do NOT call ALE. Return user message: "Cannot check improve retake eligibility — CGPA is unavailable."
  4. Use exact operation string `"check_course_eligibility"` — NOT `"check_eligibility"` (mismatch will return status=error)

### run_graduation_audit
- **StudentContext fields:** `study_status`, `completed_courses`, `failed_courses`, `in_progress_courses`, `cgpa`, `total_credit_hours_earned`, `consecutive_warnings`, `total_warnings`, `military_status`, `completed_regular_semesters`, `zero_credit_courses_passed` (converted to bool), `course_history`
- **Rule bundles (function-level):** `graduation_requirement_rules`, `academic_warning_rules`, `honors_rules`
- **Rule bundles (adapter-level only):** `grading_scale_rules` — needed by `_map_course_history()` to resolve letter grades to grade_points; not used by function itself
- **KG data:** `course_credit_lookup: dict[str, int]` — required for accurate honors CGPA trajectory. Pass as `kg_data={"course_credit_lookup": {code: credits, ...}}`
- **Required params:** none
- **Output statuses:** `eligible` | `not_eligible` | `not_auditable` | `already_graduated` | `dismissed_but_appeal_eligible` | `dismissed_no_appeal` | `cannot_compute`
- **Orchestrator prep:**
  1. Convert `zero_credit_courses_passed`: `bool(effective_context.zero_credit_courses_passed)`
  2. Build `course_credit_lookup`: call `KG.get_course_profile(code)` for every unique `course_code` in `sc.course_history`; build `{code: credits}` dict; pass in `kg_data`; cache at session level
  3. Without step 2, honors CGPA trajectory silently returns `passed=True` vacuously (false positive for honors eligibility)
  4. `military_status=None` → female student → military check skipped automatically; no conversion needed
  5. `completed_courses` and `in_progress_courses` are required by schema but NOT used in graduation check logic — pass as-is from StudentContext
  6. Military, zero-credit, and low-CGPA checks do not stop audit computation; they are returned as failed checks in the output and may make final_status="not_eligible" — Composer must surface them clearly
  7. `assumed_done` session overrides add to `completed_courses` but have NO effect on audit results — Composer should note this if override is active
  8. Verify `dismissal_extension_credits_percentage` in `academic_warning_rules` is decimal (0.0–1.0) not integer during bundle validation

### generate_semester_plan
- **StudentContext fields:** `study_status`, `completed_courses`, `failed_courses`, `in_progress_courses`, `retake_count`, `total_improve_retakes_used`, `cgpa`, `total_credit_hours_earned`, `level`, `track_id`
- **Rule bundles:** `credit_limit_rules`, `graduation_requirement_rules` + `summer_semester_rules` if Summer
- **Note:** `retake_rules` and `student_level_rules` were previously listed here but are NOT used by the function — removed from adapter and schema (Patch B)
- **KG data:** `available_courses` from `get_courses_by_track(track_id)["courses"]`
- **Required params:** `target_semester_type: str` ("Fall"|"Spring"|"Summer")
- **Optional params:** `target_track: str|None`, `target_credit_load: int|None`, `max_credits_mode: bool = False`
- **Output statuses:** `plans_generated` | `not_applicable` | `no_eligible_courses` | `cannot_compute`
- **Orchestrator prep:**
  1. Fetch `available_courses` via `KG.get_courses_by_track(track_id)` — do NOT semester-filter before passing; function handles this
  2. Cache `available_courses` per `track_id` per session (same cache reused by generate_graduation_roadmap)
  3. Determine `target_semester_type` from QU entity extraction
  4. Pass `summer_semester_rules` only for Summer queries
  5. Apply `build_effective_context()` overrides before calling ALE — override courses must be in correct lists
  6. `official_track` is mapped from `sc.track_id` by adapter; None = no official track assigned
  7. `incomplete_grade_flag` derived by adapter from `sc.course_history` (any grade=="I" → True)
  8. CGPA bracket boundaries (3.0, 2.0, 1.0) and plan constants are system-design values, not handbook rules — do not try to extract from RAG
  9. Handle query variations: "what can I take next semester?", "give me a light load for Summer" → map to `target_semester_type`, `max_credits_mode`, `target_credit_load`
  10. `is_retake` priority flag is derived from `failed_courses` internally — no retake_rules bundle needed for course selection

### generate_graduation_roadmap
- **StudentContext fields:** `study_status`, `completed_courses`, `failed_courses`, `in_progress_courses`, `retake_count`, `total_improve_retakes_used`, `cgpa`, `cumulative_chs`, `cumulative_cps`, `total_credit_hours_earned`, `level`, `track_id`, `zero_credit_courses_passed` (bool), `military_status`, `completed_regular_semesters`
- **Rule bundles:** `grading_scale_rules`, `credit_limit_rules`, `graduation_requirement_rules`, `student_level_rules` + `summer_semester_rules` if accelerated mode or Summer start
- **Note:** `retake_rules` was previously listed here but is NOT used by the function — removed from adapter and schema (Patch B). `grading_scale_rules` is kept because adapter now uses it to resolve `assumed_grade_per_pass` letter → float.
- **KG data:** `available_courses` from `get_courses_by_track(track_id)["courses"]` — must be complete catalogue, NO semester pre-filter
- **Required params:** `target_semester_type: str`, `starting_year: int`
- **Optional params:** `target_track`, `assumed_grade_per_pass` (letter or float — adapter resolves), `accelerated_mode: bool = False`, `max_credits_mode: bool = False`, `target_credit_load: int|None`
- **Output statuses:** `complete` | `cannot_complete_projection` | `blocked` | `not_applicable` | `cannot_compute`
- **Orchestrator prep:**
  1. Derive `starting_year` from `gateway.utils.get_current_semester()`
  2. Convert `zero_credit_courses_passed` to bool
  3. Pre-check `cgpa`, `cumulative_chs`, `cumulative_cps` non-None
  4. Pass `available_courses` as complete catalogue — do NOT pre-filter by semester (function simulates multiple future semesters)
  5. Cache `available_courses` per `track_id` (same cache as generate_semester_plan)
  6. `assumed_grade_per_pass` can be letter ("B") or float (3.0) — adapter resolves using grading_scale_rules; "P" grade returns cannot_compute["invalid_assumed_grade"]; if None, function uses internal default (C+ / 2.6)
  7. Military, zero-credit, and low-CGPA are collected as non_course_blockers in output but do NOT stop roadmap course simulation — roadmap continues regardless; Composer surfaces blockers separately
  8. `military_status=None` → female → no military blocker; no conversion
  9. Pass `summer_semester_rules` only for accelerated_mode=True or Summer starting semester
  10. Apply `build_effective_context()` overrides before calling ALE

---

## 9A. ALE Engine & ALE Adapter — Deep Audit Notes

**Status:** Full audit completed. All 6 functions, schemas.py, grade_resolver.py, and ale_adapter.py audited. All immediate fixes applied.

---

### ALE Adapter Error Contract (Post-Audit)

```python
# Pydantic ValidationError (bad/missing input field)
{"status": "cannot_compute", "reason_codes": ["invalid_input"],
 "required_data_missing": [], "message": str(exc), "operation": str}

# Unknown operation name (programmer error)
{"status": "error", "message": str(exc), "operation": str}

# Unexpected runtime exception
{"status": "error", "message": str(exc), "operation": str}
```

Orchestrator must check `result.get("status")` before reading ALE output fields. `"cannot_compute"` is a structured ALE-layer failure; `"error"` is an infrastructure or programmer error.

---

### Rule Bundle Requirements Per Operation (Corrected After Audit)

| Operation | Function-level bundles | Adapter-level bundles | Optional |
|---|---|---|---|
| `check_course_eligibility` | `retake_rules` | — | — |
| `simulate_gpa_forward` | `grading_scale_rules`, `retake_rules` | — | — |
| `solve_target_gpa` | `grading_scale_rules`, `retake_rules`, `graduation_requirement_rules` | — | — |
| `run_graduation_audit` | `graduation_requirement_rules`, `academic_warning_rules`, `honors_rules` | `grading_scale_rules` (for _map_course_history) | — |
| `generate_semester_plan` | `credit_limit_rules`, `graduation_requirement_rules` | — | `summer_semester_rules` (Summer only) |
| `generate_graduation_roadmap` | `credit_limit_rules`, `graduation_requirement_rules`, `student_level_rules` | `grading_scale_rules` (for assumed_grade resolution) | `summer_semester_rules` (accelerated/Summer) |

**Note:** `retake_rules` and `student_level_rules` were previously parsed by adapter for generate_semester_plan but confirmed unused — removed (Patch B). `retake_rules` was previously parsed for generate_graduation_roadmap but confirmed unused — removed (Patch B).

---

### Code Fixes Applied During Audit

| File | Fix | Why |
|---|---|---|
| `check_course_eligibility.py` | Failed-retake grade cap warning now uses `retake_rules.failed_first_retake_grade_cap` | Was hardcoded "B" — violated rule-driven principle |
| `simulate_gpa_forward.py` | `_apply_retake_cap()` cap_reason strings now rule-driven | Was hardcoded "B" in prose |
| `solve_target_gpa.py` | `_cap_grade_points_for_course()` cap_reason strings now rule-driven | Was hardcoded "B" in prose |
| `ale_adapter.py` | `ValidationError` now returns `cannot_compute["invalid_input"]` shape | Was returning raw `{"status":"error"}` |
| `ale_adapter.py` | `_map_course_history()` accepts optional `course_credit_lookup` | Enables honors CGPA trajectory accuracy |
| `ale_adapter.py` | `assumed_grade_per_pass` resolved via `resolve_grade()` before constructing input | "B" string caused Pydantic ValidationError; now correctly resolves |
| `ale_adapter.py` + `schemas.py` | Removed `retake_rules` from generate_semester_plan | Confirmed unused by function; caused false failures |
| `ale_adapter.py` + `schemas.py` | Removed `student_level_rules` from generate_semester_plan | Confirmed unused by function |
| `ale_adapter.py` + `schemas.py` | Removed `retake_rules` from generate_graduation_roadmap | Confirmed unused by function |
| `generate_semester_plan.py` | Removed stale `RetakeRules` import | Import existed but was never used in function body |

---

### Orchestrator Requirements — Complete Ledger

**For check_course_eligibility:**

| # | Requirement | Source | Failure if missing |
|---|---|---|---|
| OR-CE1 | Derive `attempt_type` from student records before calling ALE | SCP StudentContext | Wrong eligibility decision |
| OR-CE2 | If `attempt_type=="improve_retake"` and `cgpa is None` → skip ALE, return user message | SCP StudentContext | Pydantic ValidationError → cannot_compute["invalid_input"] |
| OR-CE3 | Use exact operation string `"check_course_eligibility"` | Orchestrator | Operation string mismatch → {"status":"error"} |
| OR-CE4 | Pre-check `attempt_type` before calling — ALE trusts it blindly | Orchestrator | Wrong status returned |

**For simulate_gpa_forward:**

| # | Requirement | Source | Failure if missing |
|---|---|---|---|
| OR-GF1 | Build `planned_courses` list before calling ALE | QU + SCP + KG | cannot_compute["empty_planned_courses"] |
| OR-GF2 | Fetch real credits via `KG.get_course_profile(course_code)` per course | KG | GPA math wrong if credit_hours=0 from SCP |
| OR-GF3 | Expand natural language grade patterns into per-course expected_grade | QU + SCP | Query variation unsupported |
| OR-GF4 | Derive per-course: attempt_type, has_cgpa_footprint, old_grade, improve_retake_number, is_currently_in_progress | SCP StudentContext | Wrong GPA math |
| OR-GF5 | Pre-check cgpa, cumulative_chs, cumulative_cps non-None | SCP StudentContext | Pydantic rejects → cannot_compute["invalid_input"] |
| OR-GF6 | Verify grading_scale_rules and retake_rules non-None | RAG rule bundles | _parse_rules → {"status":"error"} |
| OR-GF7 | Merge session overrides without mutating base StudentContext | Session/Overrides | Override state bleeds into base record |

**For solve_target_gpa:**

| # | Requirement | Source | Failure if missing |
|---|---|---|---|
| OR-TG1 | Extract `target_cgpa` from QU entity extraction | QU | cannot_compute["invalid_target_cgpa"] |
| OR-TG2 | Build `planned_courses` with real credits from KG per course | KG | GPA math wrong |
| OR-TG3 | Derive per-course: attempt_type, has_cgpa_footprint, old_grade, improve_retake_number | SCP StudentContext | Wrong impossibility detection |
| OR-TG4 | Supply `historical_grade` per course from course_history for personalized distribution | SCP StudentContext | Personalized distribution skipped (degraded, not broken) |
| OR-TG5 | Pre-check cgpa, cumulative_chs, cumulative_cps non-None | SCP StudentContext | Pydantic rejects → cannot_compute["invalid_input"] |
| OR-TG6 | Verify grading_scale_rules, retake_rules, graduation_requirement_rules non-None | RAG rule bundles | _parse_rules → {"status":"error"} |
| OR-TG7 | Handle query: "what do I need in current courses to get 3.0?" | QU + SCP + KG | Query variation unsupported |

**For run_graduation_audit:**

| # | Requirement | Source | Failure if missing |
|---|---|---|---|
| OR-GA1 | Convert zero_credit_courses_passed to bool | SCP StudentContext | Semantically wrong check result |
| OR-GA2 | Build kg_data["course_credit_lookup"] from KG per course in course_history; cache at session level | KG | Honors trajectory silently returns vacuous passed=True |
| OR-GA3 | Verify graduation_requirement_rules, academic_warning_rules, honors_rules non-None | RAG rule bundles | _parse_rules → {"status":"error"} |
| OR-GA4 | Verify grading_scale_rules non-None (adapter-level for _map_course_history) | RAG rule bundles | _parse_rules → {"status":"error"} |
| OR-GA5 | Verify dismissal_extension_credits_percentage is decimal (0.0–1.0) during bundle loading | RAG rule bundles | Appeal threshold wildly miscalculated if percentage integer returned |
| OR-GA6 | If assumed_done override active, inform Composer that audit reflects actual record only | Session/Overrides | User confused why audit unchanged after assuming course done |
| OR-GA7 | military_status passes as-is; None=female=no military blocker | SCP | No action needed |

**For generate_semester_plan:**

| # | Requirement | Source | Failure if missing |
|---|---|---|---|
| OR-SP1 | Fetch available_courses via KG.get_courses_by_track(track_id) — complete catalogue, no semester pre-filter | KG | cannot_compute["required_data_missing"] |
| OR-SP2 | Determine target_semester_type from QU | QU | Function cannot run |
| OR-SP3 | Pass summer_semester_rules only for Summer queries | RAG rule bundles | cannot_compute["missing_summer_rules"] for Summer |
| OR-SP4 | Apply build_effective_context() overrides before calling ALE | Session/Overrides | Plan ignores student assumptions |
| OR-SP5 | Cache available_courses per track_id per session | KG | Repeated expensive KG calls |
| OR-SP6 | Handle query variations: "what can I take?", "light load for Summer" → map to params | QU + Orchestrator | Query variations unsupported |
| OR-SP7 | Do NOT pass retake_rules or student_level_rules — removed from schema | ALE Adapter | N/A — removed; no action needed |

**For generate_graduation_roadmap:**

| # | Requirement | Source | Failure if missing |
|---|---|---|---|
| OR-GR1 | Fetch available_courses — complete catalogue, no semester pre-filter | KG | Simulation blocked; future courses invisible |
| OR-GR2 | Pre-resolve assumed_grade_per_pass handled by adapter — pass raw letter or float | ALE Adapter | Already handled; no Orchestrator action needed |
| OR-GR3 | Convert zero_credit_courses_passed to bool | SCP StudentContext | Non-course blocker may be incorrectly flagged |
| OR-GR4 | Derive starting_year from get_current_semester() | gateway/utils | Roadmap calendar starts in wrong year |
| OR-GR5 | Verify graduation_requirement_rules, credit_limit_rules, student_level_rules non-None | RAG rule bundles | _parse_rules → {"status":"error"} |
| OR-GR6 | Pass summer_semester_rules only for accelerated_mode=True or Summer start | RAG rule bundles | cannot_compute["missing_summer_rules"] |
| OR-GR7 | Apply build_effective_context() before calling ALE | Session/Overrides | Simulation ignores student assumptions |
| OR-GR8 | Cache available_courses per track_id (same cache as generate_semester_plan) | KG | Repeated expensive KG calls |
| OR-GR9 | Do NOT pre-filter available_courses by semester | KG | Simulation cannot plan beyond first semester |
| OR-GR10 | Do NOT pass retake_rules — removed from schema | ALE Adapter | N/A — removed; no action needed |

---

### Career Recommendation Workflow — Locked Decision

**`recommend_courses_to_close_gap` (KG OP9) integration:**

| User Query | Correct Workflow |
|---|---|
| "What courses close my gap for Data Scientist?" | KG OP9 only. Composer adds eligibility disclaimer. |
| "Can I take these courses?" | Orchestrator routes to ALE check_course_eligibility per course |
| "What should I take next semester toward Data Scientist?" | Combined: KG OP9 + ALE generate_semester_plan |
| "Plan my next semester" | ALE generate_semester_plan only |

**Locked rules:**
- KG OP9 stays as-is — it answers the career/skill question correctly
- Orchestrator does NOT apply inline eligibility filtering
- Composer adds disclaimer for KG-only recommendations: "These courses are skill-relevant for your target role. Eligibility depends on prerequisites, credit thresholds, and semester availability."
- If eligibility is needed, route to ALE check_course_eligibility

---

### grade_resolver.py Notes

`resolve_grade(grade_input, grading_scale, course_code) → float | None`

- Accepts: letter string ("B"), grade points float (0.0–4.0), percentage (4.0–100.0)
- Returns: float grade points, or None for P-grade
- Raises: GradeResolutionError on unrecognized input
- Used by: simulate_gpa_forward, solve_target_gpa, and ale_adapter (for roadmap assumed_grade_per_pass resolution)

`derive_level(passed_hours, rules) → str`

- Fully rule-driven via StudentLevelRules fields
- Called per simulation pass in generate_graduation_roadmap — level evolves dynamically

**Import required in ale_adapter.py (now present):**
```python
from engines.ale.utils.grade_resolver import GradeResolutionError, resolve_grade
```

---

### ALE schemas.py — Post-Audit State

**Fields removed (Patch B):**
- `GenerateSemesterPlanInput.retake_rules` — confirmed unused
- `GenerateSemesterPlanInput.student_level_rules` — confirmed unused
- `GenerateGraduationRoadmapInput.retake_rules` — confirmed unused

**Fields kept:**
- `GenerateGraduationRoadmapInput.student_level_rules` — used by `derive_level()` at line 277
- `RetakeRules` and `StudentLevelRules` model classes — still used by other operations

**Documentation inconsistency (low priority, no fix needed now):**
- ALE_Integration_Contract.md lists `semester_offering` as collapsed string ("Both"/"Fall") but adapter correctly uses `list[str]` — code is authoritative
- ALE_Integration_Contract.md says `credit_hours=3` but SCP sets `credit_hours=0` sentinel — code is authoritative
- `specialization_credit_threshold` appears in contract params but absent from all code — stale doc; rule removed per Section 18

---

## 9B. Student Context Provider — Final Contract

**Status:** Audit completed and locked. All data-correctness and robustness fixes applied.

### Public API (Unchanged)

```python
load_excel(path: str) -> None
get_context(student_id: str) -> Optional[StudentContext]
```

### Architecture

- SCP loads the Excel student source at Gateway startup.
- SCP validates file existence, required sheets, and required columns at load time. Raises `FileNotFoundError` or `ValueError` with clear messages if validation fails.
- SCP builds `StudentContext` only; it does not call KG, RAG, ALE, Session Manager, Orchestrator, or Composer.
- SCP does not apply academic rules beyond transcript normalization.
- SCP does not patch course credits. `CourseRecord.credit_hours` remains `0` as a sentinel.
- Orchestrator remains responsible for KG-based `course_credit_lookup` before ALE graduation audit/honors logic.
- `course_history` remains the authoritative attempt-level transcript history.

### Fixed Bugs (SCP Audit Pass)

| Bug | Fix Applied |
|---|---|
| `completed_regular_semesters` included current semester | Now excludes current semester using `get_current_semester()` |
| `_map_status()` checked blank grade before failed tag | Explicit failed outcomes now checked before blank-grade `in_progress` fallback |
| Current retakes hidden by best-outcome | `in_progress_courses` now includes active current retakes even when course was previously completed/failed |
| Resolved incomplete lingered in `in_progress_courses` | Old incomplete attempts no longer remain `in_progress` if later resolved by a passed/repeated/failed row |
| `study_status` blank/NaN became string `"nan"` | Blank/NaN cells now default to `"Studying"` via `_get_study_status()` helper |
| `zero_credit_courses_passed` had duplicates | De-duplicated; returns `sorted(list(set(...)))` |
| `load_excel()` had no validation | Now validates file existence, required sheets, and required columns before loading |

### Locked Assumptions

- Excel registration rows are assumed chronological; the latest row per course is treated as the most recent attempt. If rows are out of order, `in_progress_courses` logic may produce unexpected results.
- A withdrawn improve-retake attempt counts as a used improve-retake slot. This is documented in `_compute_improve_retakes()`. Withdrawal does not "refund" the slot.
- A withdrawn regular attempt does not count in `retake_count`.

### Orchestrator Integration Notes

- Orchestrator can rely on `completed_courses`, `failed_courses`, and `in_progress_courses` as normalized derived lists.
- Orchestrator must still compute `current_semester` separately for `ComposerContext` (via `gateway.utils.get_current_semester()`).
- Orchestrator must still compute `academic_standing` separately (`cgpa >= 2.0 and consecutive_warnings == 0`).
- Orchestrator must still convert `zero_credit_courses_passed` list to the ALE-required boolean: `bool(effective_context.zero_credit_courses_passed)`.
- Orchestrator must still patch `course_history` credits from KG before calling `run_graduation_audit`.
- Orchestrator should preserve SCP's `course_history` when building ALE inputs.

### Future Improvement Note

SCP still uses module-level loaded DataFrames for MVP simplicity. This is acceptable for the current single-process demo. Future production upgrade: replace Excel/module-level state with a database-backed or dependency-injected provider without changing the `StudentContext` contract.

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
| `cgpa` | Excel | ✅ Yes | Pre-check non-None before improve_retake eligibility check and GPA ops |
| `cumulative_chs` | Excel | ✅ Yes | Pre-check non-None before GPA simulation ops |
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
| `course_history` | Derived | ⚠️ Partial | Build course_credit_lookup={code:credits} from KG.get_course_profile() per unique code; pass in kg_data for run_graduation_audit; cache at session level. Without this, honors CGPA trajectory is silently wrong. |
| `academic_standing` | **DOES NOT EXIST** | — | Orchestrator computes: `cgpa >= 2.0 and consecutive_warnings == 0 → "good", else "warning"` |
| `current_semester` | **DOES NOT EXIST** | — | Orchestrator calls `gateway.utils.get_current_semester()` |
| `planned_courses` | **DOES NOT EXIST** | — | Never existed. Remove from any stubs. |

---

## 11. SessionOverrides & Session Manager — Logic Audit Contract

**Status:** Session Manager audit completed and schema compatibility resolved for current scope.

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

Applies overrides to produce hypothetical StudentContext. Never mutates base_context.

- `"planned"` → adds `added_courses` to `in_progress_courses`
- `"assumed_done"` → adds `added_courses` to `completed_courses`; removes from `failed_courses` and `in_progress_courses`
- `"assumed_failed"` → adds `assumed_failed_courses` to `failed_courses`; removes from `completed_courses`, `in_progress_courses`, and `zero_credit_courses_passed`
- `"assumed_passed"` → adds `assumed_passed_courses` to `completed_courses`; removes from `failed_courses` and `in_progress_courses`
- `"gpa_scenario"` → does not mutate `StudentContext`; Orchestrator/ALE params handle GPA scenarios
- `"none"` → no mutation

**Does NOT recalculate:** `cgpa`, `cumulative_chs`, `cumulative_cps`, `total_credit_hours_earned`. These remain stale after overrides.

**Design limitation:** `course_override_type` tracks only the latest non-`"none"` type, so mixed override sessions require careful QU/Orchestrator handling.

### Fixed Bugs (Session Manager Audit Pass)

| Bug | Fix Applied |
|---|---|
| `"assumed_done"` didn't clean `failed_courses` | Now removes from `failed_courses` when course is assumed done |
| `"assumed_done"` didn't clean `in_progress_courses` | Now removes from `in_progress_courses` when course is assumed done |
| `"assumed_passed"` didn't clean `in_progress_courses` | Now removes from `in_progress_courses` when course is assumed passed |
| `"assumed_failed"` didn't clean `in_progress_courses` | Now removes from `in_progress_courses` when course is assumed failed |
| `SQLiteSessionStore.load()` crashed on corrupted blob | Now handles `ValidationError` gracefully; returns `None` with warning log |
| `SQLiteSessionStore.get_all_for_student()` crashed on corrupted blob | Now skips and logs corrupted blobs instead of crashing |

### Session Manager public methods

```python
get_or_create_session(session_id, student_id, context, first_message) -> (SessionState, is_new: bool)
get_qu_context(session_id, user_text) -> QUContext | None
apply_query_result(session_id, structured_query) -> None  # updates overrides + last_referenced
build_effective_context(base_context, overrides) -> StudentContext
update_session_after_turn(session_id, user_text, answer_text, ...) -> None
delete_session(session_id) -> bool
```

### Design Notes & Deferred Issues

**course_override_type history loss (Design Decision Needed):**
`course_override_type` tracks only the most recent non-`"none"` type. If a student "assumes passed C-AI321"
then "plans C-SW222", the session's `course_override_type` becomes `"planned"` but both `assumed_passed_courses`
and `added_courses` are accumulated. This mismatch means Orchestrator must carefully apply
`build_effective_context()` — it cannot blindly follow `course_override_type` to know which list to process.

**Workaround (MVP-acceptable):** Orchestrator calls `build_effective_context()` once per operation, using the
correct override type. Or: QU ensures only one override type is active at a time (sets `override_action="replace"`
to prevent accumulation).

**Flagged for Orchestrator Planning:** Lock a rule about which override types can coexist and when Orchestrator
should apply them.

**Double override application risk:**
If `main.py` calls both `apply_query_result()` AND `update_session_after_turn()` with the same overrides
in the same turn, they accumulate twice (set-union saves from duplication in lists, but `course_override_type`
could flip unexpectedly).

**Workaround:** `main.py` chooses one path: either `apply_query_result()` OR `update_session_after_turn()`
with `new_overrides`, not both for the same turn's QU output.

**PII in session_blob (acceptable for MVP):**
`update_session_after_turn()` stores raw user text and answer text (with student data) in SQLite without
sanitization. This is acceptable for the current single-user evaluation. Production upgrade should
encrypt or sanitize session_blob before storage.

### Orchestrator Integration Notes

- Orchestrator can rely on `build_effective_context()` to produce clean, consistent StudentContext for ALE
- Orchestrator must be aware that `course_override_type` may not match the full accumulated state
- Orchestrator should call `build_effective_context()` once per ALE operation using correct override type
- Session Store gracefully handles corrupted or stale sessions (no crashes)

---

## 11A. Session Manager & SQLiteSessionStore — Integration Notes

**Status:** Logic audited and fixed. Schema compatibility resolved for current scope.

### SQLiteSessionStore Architecture

Four-layer persistence:
1. `SessionState` (Pydantic model) — in-memory representation
2. `session.model_dump_json()` — serialization to JSON string
3. SQLite `TEXT` column `session_blob` — storage
4. `SessionState.model_validate_json()` — deserialization with error handling

**Important:** All `SessionState` fields must have Pydantic defaults. If a new field is added without a default,
old session blobs in the database will fail to deserialize and return `None` (graceful degradation).

### Session Lifecycle

1. `get_or_create_session(session_id, student_id, context, first_message)` → creates new session or loads existing
   - Stale `session_id` (not found) → creates new session with `is_new=True`
   - Orchestrator receives fresh context from SCP per turn, but stored session preserves continuity

2. `get_qu_context(session_id, user_text)` → builds QU context from turn history
   - Returns last `QU_CONTEXT_TURNS` turns (env configurable, default 5)
   - No PII exposure; `QUContext` never includes `StudentContext`

3. `apply_query_result(session_id, structured_query)` → applies QU's overrides and entities BEFORE Orchestrator
   - Merges overrides via `_apply_overrides()` (accumulate by default)
   - Updates `last_referenced` entity tracking for pronoun resolution

4. Orchestrator runs; returns results

5. `update_session_after_turn(session_id, user_text, answer_text, ...)` → persists turn to history
   - Appends `{user, answer}` to `turn_history`
   - Optionally applies late override updates

6. Next turn: cycle repeats

### Error Handling

- Missing session → `get_or_create_session()` creates new (graceful)
- Stale `session_id` → `get_or_create_session()` creates new with `is_new=True` (Composer can inform user)
- Corrupted `session_blob` → `load()` and `get_all_for_student()` return `None`/skip (graceful, logged)
- Database errors → unhandled (would crash; acceptable for MVP; production should add retry/fallback)

### Performance Notes

- `get_summaries_for_student()` fast: reads `(session_id, session_name, last_updated)` only (no blob deserialization)
- `get_all_for_student()` slower: deserializes every blob; use only for bulk operations
- Session Manager holds no caches; every load/save hits SQLite
- `session_blob` grows with `turn_history` length; old sessions may become large but SQLite `TEXT` supports arbitrary size

### Future Improvements

1. **Schema migration:** SQLite `_init_db()` cannot handle breaking schema changes. Future: add version column and migration logic if `SessionState` schema changes
2. **Encryption:** `session_blob` contains user queries and answers; production should encrypt at rest
3. **Database choice:** SQLite acceptable for MVP; production: PostgreSQL for multi-instance deployment
4. **skill_id tracking:** `LastReferenced` doesn't track `skill_id`; if skill-pronoun resolution is needed, add it

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
status: Literal["ok", "error", "clarification_needed"] = "ok"
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

## 12A. Gateway Schemas Contract — Audited & Fixed

### Status

- Gateway schemas audited and fixed for current pre-Orchestrator scope.
- Compatible with SCP, Session Manager, SQLite session persistence, and ALE Adapter expectations.
- QU, Orchestrator, Composer, API, and UI-specific contracts are still allowed to evolve during their own design phases.

### Locked Models for Current Scope

**StudentContext**

- Canonical student state produced by SCP and consumed by Session Manager/ALE Adapter.
- Includes course history, GPA fields, warning fields, completed/failed/in-progress course lists, retake counts, zero-credit passed list.
- `course_history`, `completed_courses`, `failed_courses`, and `in_progress_courses` now use safe `Field(default_factory=list)` defaults.
- `credit_hours=0` sentinel in `CourseRecord` remains intentional; Orchestrator must patch real course credits from KG before ALE graduation audit/honors workflows.

**CourseRecord**

- Frozen Pydantic model.
- `status` Literal values: `"passed"`, `"repeated"`, `"failed"`, `"in_progress"`, `"withdrawn"`, `"incomplete"`

**SessionOverrides**

- Fields: `added_courses`, `assumed_failed_courses`, `assumed_passed_courses`, `target_role`, `course_override_type`, `override_action`
- `course_override_type` Literal values: `"planned"`, `"assumed_done"`, `"assumed_failed"`, `"assumed_passed"`, `"gpa_scenario"`, `"none"`
- `override_action` Literal values: `"accumulate"`, `"replace"`, `"clear"`
- Compatible with current Session Manager.

**Turn**

- Typed conversation turn: `user: str`, `answer: str`
- Used by `QUContext.recent_turns` and `SessionState.turn_history`.

**QueryResponse**

- `status` Literal values: `"ok"`, `"error"`, `"clarification_needed"` (default `"ok"`)

**ResultPackage**

- `status` Literal values: `"ok"`, `"error"`, `"clarification_needed"` (default `"ok"`)

### Deferred Until QU / Orchestrator / Composer Design

Do not over-lock these yet:

- `StructuredQuery.intent` remains `str`
- `StructuredQuery.engine_pattern` remains `str`
- `StructuredQuery.query_type` remains `str`
- `ComposerContext.academic_standing` remains `str`
- `LastReferenced` does not include `skill_id` yet

These fields depend on the final QU intent taxonomy, Orchestrator routing map, and Composer presentation contract. They should be finalized during those component design phases.

### Important Accuracy Note

Not all list/dict fields in `schemas.py` use `Field(default_factory=...)`. Some mutable defaults may still exist in non-critical/simple response models (e.g., `SessionOverrides`, `RAGResult`). Tests currently pass and remaining cleanup can be done opportunistically if needed. Only the `StudentContext` list fields and `SessionState.turn_history` were corrected in this audit pass.

---

## Planning-Phase Sections (13–19)

These sections represent planning guidance derived from engine/adapter audit work. 
They are **subject to revision and adaptation** during Orchestrator/QU/Composer planning 
if design work reveals different requirements.

**Sections 13–17** are guidance (intent maps, patching strategy, caching, deferred issues). 
They should inform planning but not constrain it if a better approach emerges.

**Section 18 (Architecture Constraints)** is **non-negotiable** and must guide all planning decisions.

**Section 19 (Where to Start)** is the planning process itself.

---

## 13. Locked Intent Map (8 Domains)

### Domain 1 — Academic Planning (RAG bundles + KG + ALE)
| Intent | ALE Function | KG needed | Rule bundles needed |
|---|---|---|---|
| `plan_next_semester` | `generate_semester_plan` | `get_courses_by_track` | credit_limit, graduation (retake and student_level removed — unused by function) |
| `generate_graduation_roadmap` | `generate_graduation_roadmap` | `get_courses_by_track` | grading_scale, credit_limit, graduation, student_level (retake removed — unused by function; grading_scale kept for assumed_grade resolution) |
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
6. Validate that required entity IDs are present in StructuredQuery — QU has already resolved all entities; Orchestrator does NOT interpret raw text or call resolve_entity as a primary step
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
| `kg_data["course_credit_lookup"]` | Build `{course_code: credits}` dict by calling `kg.call("get_course_profile", {"course_code": code})["credits"]` for every unique course_code in `sc.course_history`. Pass as `kg_data={"course_credit_lookup": lookup}` when calling ALE `run_graduation_audit`. Cache at session level — do not re-fetch per query. ALE Adapter's `_map_course_history()` now accepts and applies this lookup automatically. Without it, honors CGPA trajectory returns vacuously passed=True (false positive). | For run_graduation_audit only |
| `planned_courses[*].credits` | Call `kg.call("get_course_profile", {"course_code": code})["credits"]` per course. Do NOT use `credit_hours=0` sentinel from SCP course_history. Build per-course credits before constructing planned_courses list. Cache lookups at session level. | For simulate_gpa_forward, solve_target_gpa |

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
| `course_credit_lookup` for honors trajectory | ALE Adapter `_map_course_history()` now accepts optional `course_credit_lookup` dict. Orchestrator must build and pass it for `run_graduation_audit`. Without it, `_honors_cgpa_trajectory()` ignores all courses (credits<=0 filter) and returns vacuous passed=True. Implementation locked: Option B (Orchestrator builds lookup, adapter applies it). Cache at session level. | Build lookup before calling run_graduation_audit |
| `cgpa=None` + `improve_retake` path | Pydantic ValidationError at adapter construction → adapter returns `cannot_compute["invalid_input"]` instead of user-friendly message | Orchestrator pre-check: if `cgpa is None` and `attempt_type == "improve_retake"` → skip ALE, return user message |
| `assumed_grade_per_pass` letter resolution | Fixed in adapter: `resolve_grade()` now called before constructing GenerateGraduationRoadmapInput. "B"→3.0, "P"→cannot_compute, invalid→cannot_compute["invalid_assumed_grade"] | No Orchestrator action needed — handled at adapter layer |
| `planned_courses[*].credits` from SCP | SCP `credit_hours=0` sentinel cannot be used for GPA math | Orchestrator must fetch real credits from KG per course before building planned_courses for simulate_gpa_forward and solve_target_gpa |
| Operation name mismatch in Orchestrator stub | Stub calls `"check_eligibility"` — adapter dispatcher expects `"check_course_eligibility"` | Fix during Orchestrator implementation: use exact string `"check_course_eligibility"` |
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
8. **No specialization_credit_threshold or fabricated track-assignment rules** — this rule was removed because it was not handbook-based. Do not reintroduce any hardcoded track-assignment or year-based credit threshold. If official_track is None and a target_track is provided in a session override, Orchestrator should pass a flag to Composer that results are advisory only and must be confirmed with an academic advisor.

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

**Step 7:** Lock everything. Implementation is deferred — QU planning and implementation come next. After QU is finalized, return to this plan, revise if needed, then implement the Orchestrator.

---

*End of handoff document. Engines and adapters are implemented and tested. QU and Response Composer exist but are not final. The Orchestrator is the planning target for the next design chat.*
