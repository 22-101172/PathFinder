# PathFinder — Final Execution Plan

> **Purpose:** This is the single authoritative execution plan for PathFinder from current state to a fully correct, demo-ready, and production-roadmap-complete academic advising system.
>
> All phases must be executed in order. Documentation produced in each phase is open to modification as integration refinements surface later.
>
> **The goal is not a system that passes tests. The goal is a system that genuinely acts as a real academic advisor — one that a student trusts, a supervisor respects, and a university can adopt.**

---

## Phase 0 — Baseline Reality Check

### Status: COMPLETE ✓

### Goal
ad
Confirm the current system starts and works end-to-end at smoke-test level before any deep audit begins.

---

### Infrastructure Results

```
Backend startup:      PASS (~108 seconds — P3 optimization, deferred to Phase 5)
Student Excel load:   PASS — 816 students, 14,966 registration rows
KG / Neo4j:           PASS — connected
RAG:                  PASS — embedding model + cross-encoder loaded, 8 rule bundles
Orchestrator:         PASS
Composer:             PASS — qwen/qwen3-32b primary, use_llm=True
Health endpoint:      PASS — GET /health → 200 OK
Streamlit UI:         PASS — sends queries, receives responses, preserves session
```

### Startup Warnings (Non-Blocking)

```
HuggingFace unauthenticated requests     → set HF_TOKEN later (P3)
LangChain Chroma deprecation             → migrate to langchain_chroma (P3 / Phase 5)
```

### Behavioral Smoke Test Results

| # | Query | Intent | Result | Priority |
|---|-------|--------|--------|----------|
| 1 | What courses did I complete? | get_student_record | PASS | — |
| 2 | What are the prerequisites of Advanced Physics? | get_course_prerequisites | PASS | — |
| 3 | Can I take it? (Advanced Physics) | check_course_eligibility | STRUCTURAL PASS, SEMANTIC FAIL | P1/P2 |
| 4 | What happens if my CGPA drops below 2? | policy_query | PASS | — |
| 5 | What courses should I take next semester? | plan_semester | FAIL | P1 |
| 6 | I want to become a data scientist, what am I missing? | compute_skill_gap | PASS | — |
| 7 | Compare AI and Data Science | compare_tracks | FAIL | P1 |
| 8 | If I pass Programming Fundamentals, can I take Advanced Programming? | check_course_eligibility | PASS | — |
| 9 | Reset Assumptions | get_student_record | STRUCTURAL PASS, WORDING FAIL | P2 |

### Phase 0 Issue Map — Carried Forward into Phase 1

```
[P1] plan_semester
     Query: "What courses should I take next semester?"
     Issue: Fake missing-data answer even though SCP loaded student context.
     Expected: Real plan OR correct reason (no track catalogue, no eligible courses, etc.)

[P1] compare_tracks
     Query: "Compare AI and Data Science."
     Issue: QU produced two compare_tracks SQs instead of one SQ with primary + secondary track.
     Expected: One SQ, intent=compare_tracks, entity=AI, secondary_entity=Data Science.

[P1/P2] check_course_eligibility — in_progress narration
     Query: "Can I take it?" after Advanced Physics.
     Issue: status=in_progress was narrated as "not eligible, missing prerequisites."
     Expected: "You are already enrolled in Advanced Physics."

[P2] Composer entity display
     Issue: Responses expose internal codes only (C-CS219, RL_Data_Scientist, DSE).
     Expected: Names first, codes in brackets → "Advanced Programming (C-CS219)", "Data Scientist"

[P2] Reset assumptions wording
     Issue: "Your academic record has been updated" implies official registrar data changed.
     Expected: "I cleared your what-if assumptions. You are back to your official record."

[P2] Policy factuality
     Issue: Verify CGPA below 2 / probation / retake / credit-limit claims match handbook exactly.

[P3] Startup performance
     Issue: ~108 seconds due to RAG model loading, KG init, rule bundle extraction.
     Deferred to Phase 5.

[P3] Logging depth
     Issue: Current logs show routing/status but not enough to diagnose component failures.
     Addressed continuously throughout Phase 1.
```

---

## Phase 1 — Component & Engine Audit

### Status: COMPLETE ✅ — PASS / PHASE 1 COMPONENT AUDIT LOCKED

Next: Phase 1.5 — Integration Readiness Check (per execution plan order).

### Goal

Audit, test, clean, and stabilize every component, engine, adapter, and interface in its own scope before integration testing begins.

---

### What Phase 1 IS

```
Component scope audit
Engine scope audit
Adapter scope audit
Boundary and responsibility definition
Input/output contract verification
Internal logic correctness (syntactic, runtime, semantic)
Academic/career logic correctness where relevant
Component-level tests using realistic student records where applicable
Error handling verification
Safe component-level logging
SWE and design quality
P1 fixes inside component scope
```

### What Phase 1 Is NOT

```
Intent-by-intent testing
Domain behavioral testing
Full chatbot evaluation
End-to-end testing
UI regression testing
Intent Behavior Matrix building
Integration testing
```

---

### Components Covered

```
1.  KG Engine                    neo4j_client.py, queries.py
2.  KGAdapter                    adapters/kg_adapter.py
3.  RAG Engine                   rag_core.py, retriever.py, ingest.py
4.  RAGAdapter                   adapters/rag_adapter.py
5.  ALE Engine                   engines/ale/functions/ (all functions)
6.  ALEAdapter                   adapters/ale_adapter.py
7.  Student Context Provider     gateway/student_context_provider.py
8.  Session Manager              gateway/session_manager.py, sqlite_store.py
9.  Query Understanding          gateway/query_understanding.py, qu_intents.py,
                                  qu_llm_chain.py, qu_preprocessing.py, qu_prompt.py
10. Orchestrator                 gateway/orchestrator.py
11. Response Composer            gateway/response_composer.py
12. API Gateway                  main.py
13. Streamlit UI                 ui/streamlit_app.py
14. Shared schemas/contracts     schemas.py, base.py, utils.py, llm_client.py,
                                  entity_aliases.json, _env, _env.example, README.md
```

---

### What Each Component Audit Must Cover

For every component, the audit must answer:

**Scope and Responsibility**
```
What is this component responsible for?
What is it explicitly NOT responsible for?
Which component owns each kind of logic that currently touches this one?
Are there boundary violations — is this component doing another component's job?
```

**Inputs and Outputs**
```
What does it receive as input?
What does it produce as output?
What is the error/failure output shape?
What validation happens on inputs and outputs?
Are input shapes validated before use (especially LLM outputs)?
```

**Internal Logic Correctness**
```
Syntactic and runtime correctness
Semantic correctness — does it do what it claims?
Academic/career logic correctness where relevant
Edge cases from real student records
No hardcoded academic thresholds (all thresholds must come from rule bundles or KG)
```

**SWE Quality**
```
Maintainable, readable structure
No god functions where avoidable
No spaghetti branching
Consistent naming conventions
Clear model serialization (Pydantic model_dump handled correctly)
Independently testable units where possible
```

**Error Handling**
```
All failure paths produce structured, descriptive output
No silent failures or swallowed exceptions
Engine unavailability handled gracefully (not crash)
Business-level not-found is distinguished from infrastructure failure
```

**Logging**
```
Component-level logs sufficient for diagnosing failures in isolation
Safe logs only — no PII, no full transcript dumps, no raw grade lists
Enough detail to answer: "which component caused this failure?"
```

**Component-Level Tests**
```
At least smoke-level tests per major operation
Realistic cases using real student records from students_anonymous.xlsx where relevant
Edge/failure cases tested
Regression check for any Phase 0 deficiency that touches this component
```

**Fixes**
```
P1 issues fixed immediately during this step
P2/P3 issues recorded in the Deficiency Register with justification for deferral
No P1 issues deferred without documented strong justification
```

---

### Audit Order and Steps

Components are audited in this order. Each later component depends on earlier ones being stable.

---

#### Step 0 — Shared Contracts / Schemas Quick Scan

### Status: COMPLETE ✓

**Files audited:** `schemas.py`, `base.py`, `utils.py`, `llm_client.py`, `entity_aliases.json`, `.env.example`

**Goal:** Identify major schema/model shapes and obvious contract risks only. No deep schema refactor. Deep schema changes wait until engine/adapter contracts are fully understood.

---

**Files Changed in Step 0:**
```
gateway/llm_client.py       — per-call timeout_seconds added to chat(); docstring updated
gateway/qu_llm_chain.py     — reads QU_TIMEOUT_SECONDS; passes it into every client.chat() call
gateway/response_composer.py — reads COMPOSER_TIMEOUT_SECONDS; passes it into every LLM call
engines/rag/rag_core.py     — hardcoded timeout=60 replaced with env-driven _RAG_TIMEOUT
.env.example                — updated to match new config contract (see below)
```

---

**Audit Results by File:**

`schemas.py`
```
Decision: acceptable for now, not locked yet.
Remains the live shared contract while each component audit clarifies real I/O shapes.
No deep schema refactor now.

Carry-forward risks:
- Some mutable defaults may need Field(default_factory=...) cleanup
- Legacy fields (engine_pattern, query_type, ResultPackage, TurnResponse) kept
  until confirmed unused during component audits
- PerSQResult.data remains flexible for MVP; Composer must know actual result shapes
- Citation shape may need alignment across RAGAdapter, Orchestrator, Composer, and API
- LastReferenced tracks course/role/track only — decide during QU/Session audit
  whether skill_id is needed
- SessionOverrides.target_role naming should be reviewed if it actually stores role_id
```

`base.py`
```
Decision: leave unchanged. Abstract session-store contract is acceptable.
Not locked until Session Manager audit.

Carry-forward to Session Manager audit:
- Verify concrete SQLiteSessionStore implements all methods
- Decide whether delete/delete_all are internal/dev-only or API/UI exposed
- Check whether get_summaries_for_student() return shape aligns with SessionSummary
```

`utils.py`
```
Decision: keep as-is. Do NOT empty it.

get_next_semester()   — valid pure semester math; used by Orchestrator from
                        StudentContext.current_semester (not machine date)
get_current_semester() — downgraded to date-based fallback only

Architectural decision locked:
- SCP owns authoritative current_semester from registrar/Excel registration data
- utils.py owns only pure semester calculations and date-based fallback
- Orchestrator consumes ctx.current_semester; uses utils only for normalization

Carry-forward to SCP audit:
- Derive current_semester from Excel registration rows:
  find rows with empty grade, take the most frequent semester value
  (guard against stale rows with empty grades from past semesters)
- Keep utils.get_current_semester() only as fallback if registrar data is absent
- Future roadmap support may use utils.py for pure calculations
  (e.g. "third Fall from now" -> "Fall 2028"), but this belongs to the
  roadmap/generate_graduation_roadmap path, NOT plan_semester
```

`llm_client.py` + QU chain + Composer config — P1 Contract Fix Applied
```
Problem: QU_TIMEOUT_SECONDS and COMPOSER_TIMEOUT_SECONDS were defined in .env
but never read; both components shared one client-level timeout.

Fix applied:
- LLMClient.chat() now accepts timeout_seconds: Optional[float] = None
- Uses per-call timeout when provided; falls back to client default otherwise
- QUModelChain reads QU_TIMEOUT_SECONDS and passes it on every client.chat() call
- ResponseComposer reads COMPOSER_TIMEOUT_SECONDS and passes it on every LLM call

Config contract after fix:
  Shared provider credentials (required):
    LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY
  Optional internal fallbacks (no component depends on these directly):
    LLM_MODEL, LLM_TIMEOUT_SECONDS
  QU-owned:
    QU_PRIMARY_MODEL, QU_FALLBACK_MODELS, QU_TIMEOUT_SECONDS
  Composer-owned:
    COMPOSER_USE_LLM, COMPOSER_PRIMARY_MODEL, COMPOSER_FALLBACK_MODELS,
    COMPOSER_TIMEOUT_SECONDS

No live LLM/API calls were run (API keys intentionally absent during fix).
```

`rag_core.py` — RAG Timeout Config Fix Applied
```
Problem: direct requests.post(...) in rag_core.py had hardcoded timeout=60.

Fix applied:
- Added RAG_TIMEOUT_SECONDS=60 to .env.example
- Added _load_rag_timeout() helper and _RAG_TIMEOUT module constant
- Replaced hardcoded timeout=60 with timeout=_RAG_TIMEOUT

RAG was NOT migrated to shared LLMClient — intentionally deferred to RAG audit.

Carry-forward to RAG audit:
- GROQ_MODEL env loading should be reviewed at call time
- GROQ_API_KEY loading should be reviewed
- Decide whether RAG should remain separate or eventually share LLMClient
```

`entity_aliases.json`
```
Decision: not deeply audited in Step 0.
Alias coverage and quality will be audited alongside KG entity resolution and QU.

Carry-forward to KG/QU audit:
- Verify all alias targets point to existing course/role/track/skill IDs in Neo4j
- Verify all four entity types are covered: course, role, track, skill
- Check ambiguous/overlapping aliases during KG/QU audit
- Do not spend time on broad alias tuning until entity resolution behavior is audited
```

---

**Step 0 Carry-Forward Register:**

| # | Risk / Item | Carry to |
|---|-------------|----------|
| S0-1 | schemas.py mutable defaults, legacy fields, citation shape, LastReferenced.skill_id | Per component audit |
| S0-2 | base.py: verify SessionStore implementation; delete visibility; summary shape | Session Manager audit |
| S0-3 | utils.py: make SCP authoritative for current_semester; keep utils as fallback only | SCP audit |
| S0-4 | RAG LLM config: GROQ_MODEL/KEY loading, LLMClient migration decision | RAG audit |
| S0-5 | entity_aliases.json: verify coverage, check ambiguous terms | KG + QU audit |
| S0-6 | Composer entity display: names first, codes in brackets (Phase 0 P2 carry-forward) | Composer audit |

---

**Note:** Step 0 intentionally did not lock schemas, base.py, or entity aliases deeply.
Those will be finalized during their relevant component audits (Steps 1–8).

---

#### Step 1 — KG Engine + KGAdapter Audit

**Files:** `neo4j_client.py`, `queries.py`, `adapters/kg_adapter.py`

**Goal:** Ensure KG provides correct, complete, raw curriculum/career/skill/track facts. KG should return data only — no academic advising decisions.

---

##### Step 1A — KG Data Layer Audit and Upgrade

**Status: COMPLETE ✅**

**Completed work:**

- Audited `courses.csv` against the authoritative course catalogue.
  - Confirmed course row count, column names, course codes, credit values, levels, semester normalization, zero-credit courses, and Cypher compatibility.
- Audited `prerequisites.csv`.
  - Confirmed course prerequisite edges, credit-threshold prerequisite constraints, no dangling prerequisite references, and compatibility with `load.cypher`.
- Audited `tracks.csv` and `course_track.csv`.
  - Confirmed all track IDs, track-course mappings, shared-course mappings, and no dangling references.
  - Identified cross-layer track naming mismatch as a later QU/SCP responsibility:
    - KG canonical track IDs are `AI`, `CYS`, `DSE`, `SWE`, `GEN`.
    - QU/SCP must not use old informal values like `Cyber`, `Data Science`, `SW`, `CS`, or `General` internally.
- Audited original `skills.csv`, `roles.csv`, `course_skill.csv`, and `role_skill.csv`.
- Improved the skill taxonomy using course catalogue descriptions and curriculum/market relevance.
- Improved course-to-skill mappings across all 59 courses in 6 batches.
- Improved role taxonomy:
  - Removed stale/weak role `RL_IT_Manager`.
  - Added market-validated roles: `RL_MLOps_Engineer`, `RL_Cloud_Engineer`, `RL_QA_Engineer`.
- Improved role-to-skill mappings aligned with updated skill taxonomy and real job-market requirements.
- Removed stale skill `SK_Reinforcement_Learning`.
- Validated all canonical IDs and relationship references across all KG CSVs.
- Updated and validated `entity_aliases.json`:
  - All 59 courses, 20 roles, 5 tracks, 52 skills covered with student-style aliases.
  - Zero cross-entity alias conflicts.
  - Removed entities (`SK_Reinforcement_Learning`, `RL_IT_Manager`) confirmed absent.
- Reloaded Neo4j: ran `reset.cypher`, `load.cypher`, and `verify.cypher`.
- Confirmed final Neo4j node and relationship counts match expected values.

**Final KG data after reload:**
```
Nodes:
  Course:                  59
  Track:                    5
  Skill:                   52
  Role:                    20
  PrerequisiteConstraint:   2

Relationships:
  PREREQ:                  47
  HAS_PREREQ_CONSTRAINT:    6
  BELONGS_TO:              61
  TEACHES:                101
  REQUIRES:               180
```

**Step 1A result: PASS / COMPLETE ✅**

---

##### Step 1B — KG Engine Operation Audit

**Status: COMPLETE ✅**

**Completed work:**

1. `neo4j_client.py` audit — **PASS**
   - Connection setup, retry behavior, environment variable loading, query execution, and session handling reviewed.
   - No code changes required.

2. `queries.py` operation inventory — **PASS WITH MINOR NOTES**
   - Confirmed all 18 expected KG operations exist.
   - No missing public operations.
   - No stale/extra public operations.

3. OP17 placement cleanup — **DONE**
   - `q_get_focus_courses_for_target` was incorrectly placed after OP4.
   - Moved to correct logical section under A6 Target-Based Focus.
   - No logic, signature, Cypher, or return fields changed.
   - Full 18-operation import/smoke check passed after the move.

4. `queries.py` operation-by-operation responsibility audit — **DONE (OP1–OP18)**
   - Each operation verified for: responsibility, input contract, output contract, KG/ALE/QU/Composer boundary, student query patterns, and integration risks.

**Operation audit results:**
```
OP1  get_course_profile                 PASS WITH MINOR NOTES
OP2  get_prerequisites                  PASS WITH MINOR NOTES
OP3  get_skills_taught                  PASS WITH MINOR NOTES
OP4  search_courses_by_skill            PASS WITH SMALL FIX
OP5  get_role_profile                   PASS
OP6  get_roles_by_track                 PASS WITH MINOR NOTES
OP7  compute_skill_gap                  PASS WITH MINOR NOTES
OP8  compute_alignment_score            PASS WITH MINOR NOTES
OP9  recommend_courses_to_close_gap     PASS WITH MINOR NOTES
OP10 estimate_alignment_improvement     PASS WITH MINOR NOTES
OP11 find_best_matching_roles           PASS WITH CONFIRMED MINOR FILTER NOTE
OP12 get_track_overview                 PASS WITH MINOR NOTES
OP13 compare_tracks                     PASS WITH MINOR NOTES
OP14 recommend_track_for_role           PASS WITH MINOR NOTES
OP15 recommend_track_for_skill          PASS WITH MINOR NOTES
OP16 get_courses_by_track               PASS WITH IMPORTANT BOUNDARY NOTES
OP17 get_focus_courses_for_target       PASS WITH UPDATED UNDERSTANDING
OP18 resolve_entity                     PASS WITH CARRY-FORWARD RETEST NOTE
```

**Code changes made during Step 1B:**

- OP4 `q_search_courses_by_skill`:
  - Added `matched_skills` beside `matched_skill_ids`.
  - `matched_skills` includes skill ID, skill name, and category so Composer can narrate readable skill names instead of raw `SK_*` IDs.
  - Focused OP4 tests passed. Full 18-operation smoke/import test passed.

- OP17 `q_get_focus_courses_for_target`:
  - Moved to correct section only. No logic change.

5. Direct KG operation-level tests completed.

   - Test file: `engines/kg/tests/test_queries_operations.py`
   - Scope: direct live tests for `engines/kg/queries.py` through `Neo4jClient`.
   - Excludes KGAdapter, QU, Orchestrator, Composer, API, Streamlit, and full chatbot flow.
   - Total: 163 tests.
   - Result: 163 passed, 0 failed.
   - No production bugs found.

**Operation test coverage:**
```
OP1  q_get_course_profile                  8 tests   PASS
OP2  q_get_prerequisites                   9 tests   PASS
OP3  q_get_skills_taught                   8 tests   PASS
OP4  q_search_courses_by_skill             6 tests   PASS
OP5  q_get_role_profile                    7 tests   PASS
OP6  q_get_roles_by_track                  7 tests   PASS
OP7  q_compute_skill_gap                   7 tests   PASS
OP8  q_compute_alignment_score             7 tests   PASS
OP9  q_recommend_courses_to_close_gap      8 tests   PASS
OP10 q_estimate_alignment_improvement      7 tests   PASS
OP11 q_find_best_matching_roles            7 tests   PASS
OP12 q_get_track_overview                  7 tests   PASS
OP13 q_compare_tracks                      5 tests   PASS
OP14 q_recommend_track_for_role            8 tests   PASS
OP15 q_recommend_track_for_skill           9 tests   PASS
OP16 q_get_courses_by_track                8 tests   PASS
OP17 q_get_focus_courses_for_target       10 tests   PASS
OP18 q_resolve_entity                     30 tests   PASS
Total: 163 passed, 0 failed — Runtime: 5.56s
```

**Step 1B testing result:**

- Direct operation-level tests were created under `engines/kg/tests/`.
- `test_queries_operations.py` covers OP1–OP18.
- Total tests: 163.
- Result: 163 passed, 0 failed.
- All 18 KG operations were validated against the currently loaded KG data.
- No production bugs were found.
- Step 1B is now complete.

**Step 1B result: PASS / COMPLETE ✅**

---

##### Step 1B — Carry-Forward Findings for Later Component Audits

1. **KG boundary (enforced throughout)**
   - KG returns curriculum/career/skill/track facts only.
   - KG must not decide eligibility, semester plans, graduation status, credit limits, or academic advice.
   - ALE owns academic decisions. QU owns intent/entity-type interpretation. Orchestrator owns routing and enrichment. Composer owns final student-facing wording.

2. **Names vs IDs**
   - KG operations generally return IDs plus readable names.
   - Composer must prefer readable names first, with IDs in parentheses only when needed.
   - Student-facing answers must never expose raw `RL_*` or `SK_*` IDs if names are available.

3. **Track ID normalization**
   - Canonical KG track IDs: `AI`, `CYS`, `DSE`, `SWE`, `GEN`.
   - QU, SCP, Session Manager, and Orchestrator must use these canonical IDs.
   - Old informal values (`Cyber`, `Data Science`, `SW`, `CS`, `General`) must be normalized before any KG call.

4. **Composer wording rules derived from KG operation review**
   - `get_roles_by_track`, `get_track_overview`, `compare_tracks` → phrase roles as "connected roles" or "related roles," not guaranteed careers.
   - `recommend_track_for_role`, `recommend_track_for_skill` → frame as curriculum-skill fit, not personal academic advice.
   - `recommend_courses_to_close_gap`, `get_focus_courses_for_target` → must not be narrated as registration plans.
   - `estimate_alignment_improvement`, `compute_alignment_score` → measure curriculum-skill alignment only, not employability readiness.
   - Display requests like "top 3" or "brief" are Composer concerns — KG should always return complete structured results.

5. **QU disambiguation findings**
   - QU must decide entity type before calling `resolve_entity`.
   - Ambiguous phrases like "Software Engineering" may refer to a track, role, or course domain — QU should use session context or ask clarification.
   - `compare_tracks` supports exactly two tracks for MVP. If the user asks for 3+ tracks, QU must return `clarification_needed`.
   - "What should I study for X?" → maps to skill-gap/learning needs, not automatic course recommendation.
   - "What courses should I take for X?" → may imply registration/eligibility, which routes to ALE planning, not KG course search alone.

6. **OP10 planned-course source**
   - OP10 must remain pure KG calculation. Orchestrator must resolve `planned_courses` before calling OP10.
   - Sources: explicit query courses → session plan/roadmap reference → session planned-course assumptions → in-progress courses fallback.
   - If in-progress fallback is used, Composer must state that assumption clearly.

7. **OP17 completed/in-progress course handling**
   - OP17 accepts `completed_courses` and excludes them from focus-course results. Empty list is valid.
   - Orchestrator/QU audit should later decide whether to also exclude in-progress courses based on query wording:
     - "future/remaining/not yet taken" → exclude completed + in-progress
     - "current/this semester" → include or prioritize in-progress
     - general focus request → use intent/context
   - OP17 is not semester planning and must not be narrated as eligibility advice.

8. **Empty `completed_courses` behavior**
   - OP7, OP8, OP9, OP11 may treat empty `completed_courses` as `no_courses_provided` for MVP.
   - Future improvement: freshmen/new students should see OP7 → all skills missing, OP8 → 0% alignment, OP11 → "not enough completed-course evidence."

9. **Required/elective split**
   - Current KG data does not support required/elective labeling.
   - Do not invent required/elective/common-course labels without explicit data support.
   - Recorded as a data limitation, not a KG bug.

10. **OP18 alias maintenance**
    - `entity_aliases.json` must be revalidated whenever courses, roles, tracks, or skills change.
    - QU preprocessing should normalize loose course-code forms (`cs219`, `c cs 219`, `c-cs219`) before resolver usage.

---

##### Step 1C — KGAdapter Audit

**Status: COMPLETE ✅**

**Completed work:**

- Audited `adapters/kg_adapter.py`.
- Confirmed the adapter is thin and does not add academic advising logic.
- Confirmed all 18 locked KG operations are exposed through `call(operation, params)`.
- Confirmed operation names match the KG contract.
- Confirmed adapter methods forward params directly to `queries.py`.
- Confirmed query-level business results are preserved and not incorrectly wrapped as infrastructure errors.
- Confirmed adapter-level errors are separated from query-level business errors:
  - `kg_unavailable`
  - `unknown_operation`
  - `bad_params`
  - `kg_error`
- Confirmed Orchestrator must use exact operation names and exact parameter names expected by adapter methods.

**Adapter tests:**

- Test file: `engines/kg/tests/test_kg_adapter.py`
- Total adapter tests: 83.
- Result: 83 passed, 0 failed.
- Combined KG test suite: 246 passed, 0 failed.
- Production code changes required: none.

**Important confirmed case:**

- `get_prerequisites` with `depth="full"` works correctly through KGAdapter.
- The adapter passes `depth` through unchanged.
- Full prerequisite trees are returned when prerequisite chains exist.
- This addresses the earlier trial E2E concern where full prerequisites looked wrong.

**Step 1C result: PASS / COMPLETE ✅**

---

##### Step 1D — KG Component Tests

**Status: COMPLETE ✅ / Satisfied by Step 1B + Step 1C**

**Reason:**

The originally planned KG component tests were already completed during Step 1B and Step 1C.

Covered by Step 1B direct KG operation tests:
- Ran each KG operation directly.
- Tested realistic course, role, track, and skill examples.
- Tested resolver aliases.
- Tested missing/not-found cases.
- Tested removed entity safety.
- Tested realistic student-like completed-course sets.
- Result: 163 passed, 0 failed.

Covered by Step 1C adapter tests:
- Tested all 18 operations through KGAdapter.
- Tested adapter dispatch contract.
- Tested adapter error handling.
- Tested query-level business error passthrough.
- Tested full-prerequisite `depth="full"` behavior through the adapter.
- Result: 83 passed, 0 failed.

**No additional Step 1D test file is needed.**

**Step 1D result: PASS / COMPLETE ✅**

---

##### Step 1 Overall Result

**Status: COMPLETE ✅**

KG Engine + KGAdapter audit is complete.

Final step results:
- Step 1A KG Data Layer Audit and Upgrade: COMPLETE ✅
- Step 1B KG Engine Operation Audit: COMPLETE ✅
- Step 1C KGAdapter Audit: COMPLETE ✅
- Step 1D KG Component Tests: COMPLETE ✅ / satisfied by Step 1B + Step 1C

Final KG test status:
- Direct KG operation tests: 163 passed, 0 failed.
- KGAdapter tests: 83 passed, 0 failed.
- Combined KG test suite: 246 passed, 0 failed.

No production bugs remain in the KG Engine or KGAdapter from this audit.

Carry-forward findings remain documented for later QU, SCP, Orchestrator, Composer, and ALE audits.

---

##### Next Immediate Task

Proceed to the next Phase 1 component audit after KG.

Do not run more KG operation or adapter tests unless KG data/code changes again.

The KG component is now validated at:
- data layer level
- query operation level
- direct live operation test level
- adapter contract test level

---

#### Step 2 — RAG Engine + RAGAdapter Audit

**Files:** `rag_core.py`, `retriever.py`, `ingest.py`, `adapters/rag_adapter.py`

**Goal:** Ensure RAG serves two distinct roles correctly and safely.

**Role 1: Policy Q&A**
```
- Policy query is rewritten to be self-contained before retrieval
- Retrieved chunks are relevant to the query
- Citations reference real handbook pages/sections
- No student data of any kind enters the RAG call
- Missing evidence returns soft_no_evidence — never hallucination
- Citations are included in the response when available
```

**Role 2: Rule Bundle Extraction**
```
- All 8 rule bundles extracted correctly at startup
- Failed bundles return None safely (not crash, not wrong values)
- Bundle values are validated (not accepted as raw LLM strings)
- Rule bundle cache is populated at startup and used per-session
- Bundle extraction failure for one bundle does not crash others
```

**RAGAdapter:**
```
- rag.execute(sub_query, student_context=None) contract stable
- student_context is never forwarded to RAG core (hard rule)
- rag.get_rule_bundles() returns all 8 bundle keys with None for failed bundles
- Adapter output shapes are consistent and validated
- No business logic inside adapter
```

**Startup / performance check:**
```
- Embedding model loaded once (not per-request)
- Cross-encoder loaded once
- ChromaDB loaded once
- Chroma deprecation warning: confirm migration plan to langchain_chroma
- HuggingFace check: confirm offline/cached mode is possible
```

---

**Step 2 Status Summary:**
```
Step 2A — RAG Engine Normal Policy Path:        COMPLETE — PASS WITH NOTES ✅
Step 2B — Normal RAG Question Testing:          COMPLETE — PASS WITH NOTES ✅
Step 2C — RAGAdapter Normal Policy Path:        COMPLETE — PASS WITH FIXES ✅
Step 2D — RAG Rule-Bundle Extraction:           COMPLETE — PASS WITH FIXES ✅
```

Normal RAG path audited and tested; rule-bundle extraction intentionally deferred.

---

##### Step 2A — RAG Engine Normal Policy Path Audit

**Status: COMPLETE ✅**

**Scope:**
- Audited normal handbook policy Q&A / fact extraction only.
- Files covered:
  - `engines/rag/ingest.py`
  - `engines/rag/retriever.py`
  - `engines/rag/rag_core.py`
  - `engines/rag/manual_eval/rag_query_runner.py`
  - `engines/rag/manual_eval/sample_queries.txt`
  - `engines/rag/manual_eval/README.md`
- NOT audited in this step:
  - `RAGAdapter.execute()`
  - `RAGAdapter.execute_structured()`
  - `RAGAdapter.get_rule_bundles()`
  - ALE rule-bundle startup safety
  - Full chatbot / UI / E2E
  - Composer behavior
  - Intent-level behavior

**Architectural clarification — evidence-first RAG:**
```
PathFinder's normal RAG path is evidence-first.

The RAG LLM is not used as a free final-answer generator. The flow is:

  student policy question
  → retrieval over handbook chunks
  → strict LLM fact extraction from retrieved excerpts
  → structured output: found, extracted_facts, source_documents, query
  → RAGAdapter / Orchestrator pass evidence forward
  → Response Composer creates final student-facing wording

This design reduces hallucination risk because the RAG model is constrained
to extract facts from retrieved handbook text rather than invent final
advising answers.
```

Additional boundaries:
- Normal RAG answers policy/handbook questions only.
- ALE owns deterministic academic calculations and planning.
- Composer owns final narration.
- RAG must not receive student PII or transcript context for normal policy queries.

---

###### Step 2A.1 — `engines/rag/ingest.py`

**Status: PASS ✅**

**Findings:**
- Handbook source is markdown: `CIS_Handbook.md`.
- Pages parsed using `--- PAGE N ---` delimiter.
- Pre-page synthetic structured summary treated as page 0.
- Parent/child chunking implemented:
  - parent chunk size = 800, overlap = 250
  - child chunk size = 200, overlap = 40
- Parent chunks stored in `chunks.pkl`.
- Child chunks stored in Chroma.
- Parent/child relation uses `parent_id`.
- Metadata preserved: `doc_id`, `version_date`, `page`, `major`, `handbook_type`, `parent_id`, `chunk_type`.
- Env-driven paths supported: `RAG_HANDBOOK_PATH`, `RAG_CHROMA_DIR`, `RAG_CHUNKS_FILE`.
- Missing handbook fails clearly with `FileNotFoundError`.
- Chroma import deprecation is non-blocking carry-forward.

**Changes made:**
- Replaced fragile relative paths with env-configurable paths and safe defaults.
- `.env` loaded from project root.
- Defaults anchored to `engines/rag/` regardless of current working directory.
- Missing handbook now raises `FileNotFoundError` instead of silently returning.
- Removed stale final message mentioning `streamlit run app.py`.
- Chroma import intentionally left as `langchain_community.vectorstores.Chroma` with a carry-forward note.

`.env.example` additions:
```
RAG_HANDBOOK_PATH=
RAG_CHROMA_DIR=
RAG_CHUNKS_FILE=
```

**Carry-forward:**
- Synthetic page 0 is useful for explicit limitation statements, but may produce less official-looking citations than real handbook pages.
- Later decide whether page 0 should be shown to students as a source, hidden, or labeled as "structured handbook summary."

---

###### Step 2A.2 — `engines/rag/retriever.py`

**Status: PASS ✅**

**Findings:**
- Hybrid retrieval confirmed:
  1. Dense vector search over child chunks.
  2. BM25 over parent chunks.
  3. Reciprocal Rank Fusion merge.
  4. Cross-encoder reranking.
- Default final context passed into RAG LLM: up to 6 parent chunks.
- Empty query returns empty list safely.
- Missing Chroma artifacts or `chunks.pkl` fail clearly with message to run `ingest.py` first.
- Env path contract aligned with `ingest.py`.
- `HF_TOKEN` is optional; picked up by Hugging Face tooling if present.
- First startup may be slow because embeddings/reranker models load locally.
- LangChain Chroma deprecation warning is non-blocking and deferred.

**Changes made:**
- Added `.env` loading from project root.
- `RAG_CHROMA_DIR` and `RAG_CHUNKS_FILE` now used consistently with `ingest.py`.
- Added explicit artifact checks with clear message to run `ingest.py` first.
- Replaced startup `print()` calls with `logger.info()`.
- Added empty-query guard in `retrieve()`.
- Chroma import intentionally left unchanged as compatibility carry-forward.

`.env.example` additions:
```
HF_TOKEN=    # optional — only in private .env, never committed
```

**Carry-forward:**
- Migrate `langchain_community.vectorstores.Chroma` to `langchain_chroma.Chroma` later, after dependency confirmation.
- RAG startup performance deferred — retriever/model loading happens at import/startup.

---

###### Step 2A.3 — `engines/rag/rag_core.py`

**Status: PASS ✅ (normal `extract_facts()` only)**

**Findings:**
- `extract_facts()` is the normal policy Q&A function.
- `extract_structured()` exists but was not audited in this step.
- `extract_facts()` output schema:
  - `found`
  - `extracted_facts`
  - `source_documents`
  - `query`
  - `error` (safe code when relevant)
- Empty query guard exists.
- Retriever-unavailable case handled safely.
- LLM errors return safe error code (e.g. `rag_llm_error`), not raw exception text.
- `source_documents` returned only when facts are found.
- `allow_fallback=False` supports fair single-model manual evaluation.
- RAG timeout from `RAG_TIMEOUT_SECONDS`.

**Changes made:**
- Added package-safe retriever import (relative first, fallback local).
- `extract_facts()` now accepts `groq_model: str | None = None`.
- Model resolution order:
  1. explicit `groq_model` argument
  2. `RAG_GROQ_MODEL` env var
  3. `GROQ_MODEL` env var
  4. `DEFAULT_RAG_MODEL`
  5. `RAG_FALLBACK_MODELS`
- Added empty-query guard.
- Wrapped retrieval and LLM call in safe try/except.
- Raw exception strings logged internally, not returned to callers.

**Current model configuration:**
```python
DEFAULT_RAG_MODEL = "llama-3.1-8b-instant"
```
```env
RAG_GROQ_MODEL=llama-3.1-8b-instant
RAG_FALLBACK_MODELS=openai/gpt-oss-20b
RAG_REASONING_EFFORT=low
```
`RAG_REASONING_EFFORT` is injected only for `openai/gpt-oss*` models.

`.env.example` additions:
```
RAG_GROQ_MODEL=llama-3.1-8b-instant    # optional — overrides GROQ_MODEL for RAG only
```

**Carry-forward:**
- Top `rag_core.py` docstring says `source_pages`; actual output uses `source_documents`. Fix during Composer audit.
- `time` import may be unused — check during cleanup pass.
- `extract_structured()` / rule-bundle extraction intentionally not audited or modified.
- Chroma import migration remains a future/compatibility note only.
- RAG still uses direct `requests.post` to Groq rather than shared `LLMClient`; acceptable for now.

---

###### Step 2A.4 — Manual RAG Evaluator

**Files:**
- `engines/rag/manual_eval/rag_query_runner.py`
- `engines/rag/manual_eval/sample_queries.txt`
- `engines/rag/manual_eval/README.md`

**Purpose:** Standalone manual evaluator for inspecting normal RAG extraction behavior. Not CI. Not production code.

**Supported flags:**
```
--query
--queries-file
--model
--delay-seconds
--max-retries
--retry-delay-seconds
```

**Important fixes made:**
- Windows Unicode output crash fixed.
- stdout/stderr reconfigured to UTF-8 where possible.
- Source snippet print line uses ASCII-safe separator.
- JSON output uses `ensure_ascii=True`.
- Delay/retry support added due to Groq HTTP 429 rate-limit errors during Llama full-file testing.

**Commands used during Step 2A evaluation:**
```powershell
python -m engines.rag.manual_eval.rag_query_runner --model openai/gpt-oss-20b --queries-file engines/rag/manual_eval/sample_queries.txt > eval_gpt_oss.txt

python -m engines.rag.manual_eval.rag_query_runner --model llama-3.1-8b-instant --queries-file engines/rag/manual_eval/sample_queries.txt --delay-seconds 6 --max-retries 2 --retry-delay-seconds 15 > eval_llama.txt
```

**Syntax checks (both returned no errors):**
```powershell
python -m py_compile engines/rag/rag_core.py
python -m py_compile engines/rag/manual_eval/rag_query_runner.py
```

---

###### Step 2A.5 — RAG Model/Provider Research and Decision

**Models evaluated:**

`llama-3.1-8b-instant` (Groq):
- Fast and cheaper relative to GPT-OSS 20B.
- Supports JSON-style extraction workflow used by RAG.
- More conservative and safer on no-evidence questions:
  - Returned `found=false` on: future GPA prediction, AI minor requirements, cross-faculty registration due to schedule conflict, course-drop-for-GPA advice.
- Extracted facts sometimes terse (e.g. "133 credit hours") — acceptable because Composer can reframe facts into complete student-facing sentences.
- Hit Groq 429 rate limits during full-file manual testing without delay; manual evaluator needed throttling.
- Risk: provider/model availability and possible deprecation must be monitored (carry-forward, not a blocker).

`openai/gpt-oss-20b` (Groq):
- Reasoning-capable model.
- More expensive output side than Llama in Groq pricing at evaluation time.
- `RAG_REASONING_EFFORT=low` used only for GPT-OSS models.
- Completed all 12 manual queries without API failure.
- Produced more complete wording than Llama on several direct factual queries.
- Issues observed:
  1. `What is an incomplete grade?` returned a truncated extracted fact.
  2. Some no-evidence/limitation questions returned `found=true` with a limitation answer, but cited chunks did not visibly support the extracted limitation.
  3. Unsupported limitation claims are risky for an academic/policy-facing system even when semantically useful.

**Manual comparison:**

| Criterion | llama-3.1-8b-instant | openai/gpt-oss-20b |
|---|---|---|
| Full test completion | PASS after delay/retry | PASS |
| JSON validity | PASS | PASS |
| Direct factual extraction | PASS | PASS |
| No-evidence behavior | Safer / conservative | Weaker / sometimes found=true |
| Citation support on limitation questions | Safer | Concern |
| Fact wording | Terse but acceptable | More complete |
| Incomplete-grade query | Better | Truncated |
| Rate-limit behavior | Needed throttled manual run | Completed initial full run |
| Current PathFinder role | **Primary** | **Fallback** |

**Final model decision:**
```
Primary model:  llama-3.1-8b-instant
Fallback model: openai/gpt-oss-20b
```

Reason: Handbook policy correctness and no-evidence safety are more important than fluent extraction wording. Llama was safer on no-evidence / limitation queries. GPT-OSS remains a useful fallback due to strong performance on direct factual questions, but is not primary due to observed citation-support and truncation issues.

> This decision is empirical for the current PathFinder handbook extraction task, not a general claim that Llama is always better than GPT-OSS.

> Provider/model choice is not permanently locked. It should be revisited if Groq deprecates a model, changes limits/pricing, or if later RAGAdapter/Composer tests show different behavior.

---

###### Step 2A.6 — Manual Normal RAG Test Set

12 queries tested manually (not final chatbot tests — test only `extract_facts()` directly):

1. How many credit hours do I need to graduate?
2. What happens if my CGPA drops below 2.0?
3. What is the attendance requirement for a course?
4. What happens if I miss the final exam without an approved excuse?
5. How many credit hours can a student register if their CGPA is between 2.0 and 3.0?
6. When can a student appeal a grade?
7. What is the grading scale for B+?
8. What is an incomplete grade?
9. Does the handbook explain how to predict my future GPA?
10. Does the handbook describe AI minor requirements?
11. Does the handbook say I can register for courses in another faculty if there is a schedule conflict?
12. Which course should I drop to improve my GPA?

These tests do not cover QU, Orchestrator, RAGAdapter, Composer, API, or UI. They are sufficient to justify the current normal RAG core/model decision before adapter audit.

---

##### Step 2B — Normal RAG Question Testing

**Status: COMPLETE — PASS WITH NOTES ✅**

**What was done:**
- Ran multiple rounds of manual normal RAG policy testing using `rag_query_runner.py`.
- Round 3 was the final regression sweep after prompt hardening.
- All manual evaluation files and outputs live under `engines/rag/manual_eval/`.

**Round 3 files:**
- Query file: `engines/rag/manual_eval/rag_policy_deep_tests.txt`
- Output file: `engines/rag/manual_eval/eval_policy_deep_llama_round3.txt`

**Round 3 results:**
```
Total queries: 64
PASS:          53
WEAK:          11
FAIL:           0
```

**Runtime stability:**
- No full-run blocker.
- Retries handled transient `rag_llm_error`.
- JSON output remained usable.
- No regressions on previously passing queries.

**Improvements made to `engines/rag/rag_core.py`:**
- Strengthened `SYSTEM_INSTRUCTION` for normal `extract_facts()`.
- Added/kept generic `_deduplicate_facts()` helper.
- No hardcoded academic rules added.
- Retriever not changed.
- Rule-bundle extraction not changed.
- RAGAdapter not changed.

**Prompt hardening covered:**
- Exact numeric bracket selection.
- Broad numeric ranges.
- Preserving comparison operators (below / at least / greater than or equal).
- Multi-part questions.
- Yes/no and negative policy questions.
- Topic coverage questions.
- Complete standalone facts.
- Deduplication.

**Critical cases fixed:**
- `If I got 87%, what letter grade and GPA points does that match?` → now returns B+ / 3.2 / 84–87.9%.
- Academic warning compound query → preserves "CGPA below 2.0", includes first-semester exemption and 4 consecutive / 6 separate warnings dismissal rule.
- Honors negative questions → "failed a course before" returns `found=true` with the "never failed" requirement; "disciplinary penalties" returns `found=true` with the "never had disciplinary penalties" requirement.
- Summer semester compound question → returns default 2 courses and high-CGPA 3 courses with CGPA ≥ 3.
- No-evidence questions remained safe: future GPA formula, course-drop advice, cross-faculty registration conflict, job choice, handbook deciding a course choice.

**Round 3 WEAK items — accepted as carry-forward, not blockers:**
1. Minor duplicate or near-duplicate facts (GPA vs CGPA, compound retake, grade appeal).
2. Some facts correct but poorly worded (minimum pass percentage lacks framing; graduation requirement compound has dangling "in at least 6 semesters").
3. CGPA 1.5 bracket: returns both 15-credit and 12-credit bracket; correct bracket is first — Composer/full-system logic can narrate the specific applicable value later.
4. Some excerpt-boundary issues: incomplete grade definition can be truncated.
5. A few low-stakes synthesis/no-evidence imperfections:
   - AI minor sometimes returns synthesized negative statement.
   - Advanced Programming course-choice question can return a prereq-checking fact.
   - Final graduating semester 21-credit query included "even if on academic probation" from synthetic summary / nearby source rather than visible raw page.
6. Accepted because: no FAIL cases remained; core policy facts mostly correct; final personalized decisions should rely on ALE/rule bundles; Composer can clean wording and select the relevant fact; RAGAdapter and full policy composition will be audited later.

**Architecture clarification:**
- Normal RAG `extract_facts()` does not generate the final student-facing answer.
- It retrieves handbook chunks and uses the RAG LLM as a strict fact-extraction layer.
- Final wording is the responsibility of `ResponseComposer`.
- `RAGAdapter.execute()` currently packages/joins facts into an `answer` compatibility field, but this is not the final human answer.

---

##### Step 2C — RAGAdapter Normal Policy Path Audit

**Status: COMPLETE — PASS WITH FIXES ✅**

**Scope:** `adapters/rag_adapter.py` — `RAGAdapter.execute()` and `__init__` import path only.
`execute_structured()` and `get_rule_bundles()` were not audited or modified (Step 2D scope).

**Issues found and fixed:**

| # | Issue | Fix |
|---|---|---|
| 1 | `found` dropped from `execute()` return dict | Added `found` to all return shapes |
| 2 | Safe `error` codes from rag_core dropped | Preserved as-is in return dict |
| 3 | No-evidence and RAG failure collapsed to same answer with no `error` key | Distinguished with `error` key per case |
| 4 | Raw exception text in user-facing `answer` (`f"An error occurred: {exc}"`) | Replaced with generic `_ANSWER_FAILURE`; raw text logged internally only |
| 5 | Empty query returned no `error` key | Returns `error="empty_query"` |
| 6 | RAG unavailable returned no `error` key, wrong answer text | Returns `error="rag_unavailable"`, distinct answer |
| 7 | Citation builder assumed every source_document is a dict (crash on malformed) | `_build_citations()` static method: safe `.get()` for dicts, handles LangChain Document-like objects, skips unknown types |
| 8 | `__init__` used fragile `sys.path` hacking with uppercase `engines/RAG` path | Package import first (`from engines.rag.rag_core import ...`); minimal sys.path fallback second |

**`found` normalization in `execute()`:**
- Facts present → `found=True` (regardless of raw rag_core `found` field)
- Facts absent → `found=False` (conservative)
- Error present → `found=False` (regardless of facts)

**Final `execute()` output contract:**
```python
# Evidence found
{"found": True,  "answer": "fact 1 fact 2", "extracted_facts": [...], "citations": [...]}

# No evidence
{"found": False, "answer": "Not found in handbook.", "extracted_facts": [], "citations": []}

# Empty query
{"found": False, "answer": "Not found in handbook.", "extracted_facts": [], "citations": [], "error": "empty_query"}

# RAG unavailable
{"found": False, "answer": "The handbook search service is currently unavailable.", "extracted_facts": [], "citations": [], "error": "rag_unavailable"}

# rag_core safe error (rag_llm_error, rag_retrieval_error, …)
{"found": False, "answer": "I could not search the handbook safely right now.", "extracted_facts": [], "citations": [], "error": "<error_code>"}

# Unexpected adapter exception
{"found": False, "answer": "I could not search the handbook safely right now.", "extracted_facts": [], "citations": [], "error": "rag_adapter_error"}
```

**Files changed:**
- `adapters/rag_adapter.py` — `__init__`, `execute()`, added `_build_citations()` static method

**Files NOT changed:**
- `engines/rag/rag_core.py`, `retriever.py`, `ingest.py` — not touched
- `execute_structured()`, `get_rule_bundles()` — not touched
- ALE, KG, QU, Orchestrator, Composer, API, UI, `.env` — not touched

**Tests added:**
- `tests/test_rag_adapter_execute.py` — 10 unit tests, stubbed `extract_facts`, no live Groq calls
- Result: **10 passed, 0 failed**

**Confirmed:**
- No hardcoded academic rules added to adapter
- No live Groq calls in new tests
- `execute_structured()` and `get_rule_bundles()` unmodified

---

##### Step 2D — RAG Rule-Bundle Extraction Audit

**Status: COMPLETE — PASS WITH FIXES ✅**

**Scope:**
- `RAGAdapter.execute_structured()` — Part A
- `engines/rag/rag_core.py` — `extract_structured()` — Part B
- `RAGAdapter.get_rule_bundles()` — summer fallback documentation — Part C
- Three new test files — Part D

**Issues found and fixed:**

| # | Location | Issue | Fix |
|---|----------|-------|-----|
| SD-1 | `execute_structured()` | No empty-query guard | Added `empty_query` guard before calling fn |
| SD-2 | `execute_structured()` | No invalid-schema guard | Added `invalid_schema` guard before calling fn |
| SD-3 | `execute_structured()` | Raw exception returned as `str(exc)` | Replaced with `rag_adapter_error` + `logger.exception()` |
| SD-4 | `execute_structured()` | Duplicated citation-build logic | Refactored to reuse `_build_citations()` static method |
| SD-5 | `extract_structured()` in rag_core | No `empty_query` guard | Added |
| SD-6 | `extract_structured()` in rag_core | No `invalid_schema` guard | Added |
| SD-7 | `extract_structured()` in rag_core | Silent return on `retriever=None` | Added `rag_unavailable` error code |
| SD-8 | `extract_structured()` in rag_core | Silent return on missing API key | Added `rag_not_configured` error code |
| SD-9 | `extract_structured()` in rag_core | Single model call, no chain/fallback | Replaced with `_load_rag_model_chain()` loop |
| SD-10 | `extract_structured()` in rag_core | Raw exception string as error code | Replaced with `rag_llm_error` |
| SD-11 | `get_rule_bundles()` | Summer fallback undocumented | Added explicit "HANDBOOK-BACKED STARTUP FALLBACK (CIS Handbook p.7)" comment + `logger.info()` |

**`execute_structured()` output contract:**

| Condition | `data` | `error` | `citations` |
|-----------|--------|---------|-------------|
| Empty/whitespace query | `{}` | `"empty_query"` | `[]` |
| None / non-dict / empty-dict schema | `{}` | `"invalid_schema"` | `[]` |
| `extract_structured_fn` is None | `{}` | `"rag_unavailable"` | `[]` |
| rag_core returns safe error | `{}` | preserved code | built from any available source_documents |
| Unexpected exception in fn | `{}` | `"rag_adapter_error"` | `[]` |
| Success | extracted dict | *(absent)* | built from source_documents |

**`extract_structured()` output contract (rag_core):**

| Condition | `data` | `error` |
|-----------|--------|---------|
| Empty/whitespace query | `{}` | `"empty_query"` |
| None / non-dict / empty-dict schema | `{}` | `"invalid_schema"` |
| `retriever` module-level var is None | `{}` | `"rag_unavailable"` |
| Retriever `.retrieve()` raises | `{}` | `"rag_retrieval_error"` |
| Missing GROQ_API_KEY | `{}` | `"rag_not_configured"` |
| All models in chain fail | `{}` | `"rag_llm_error"` |
| Success | extracted dict | *(absent)* |

**Test results:**

```
tests/test_rag_adapter_structured.py    9 passed, 0 failed  ✅
tests/test_rag_core_structured.py       8 passed, 0 failed  ✅
tests/test_rag_rule_bundles.py         11 passed, 0 failed  ✅
```

**Confirmed constraints:**
- ALE files not modified.
- No live Groq calls in any of the three test files.
- `execute()` normal path regression test passes (test_normal_execute_still_works_after_structured_changes).
- Raw exception strings cannot escape structured path — verified by test SD-9 (adapter) and test 6 (rag_core).
- Summer fallback fills only None fields, never overwrites RAG-extracted values — verified by test_get_rule_bundles_summer_fallback_fills_none_fields.

**Important note:** Rule-bundle extraction is higher-risk than normal policy Q&A because wrong extracted values directly affect ALE decisions. The structured path now has identical error-handling discipline to the normal `extract_facts()` path.

---

**Step 2D live verification follow-up: COMPLETE — PASS WITH SMALL PATCH ✅**

Manual live run `RAGAdapter().get_rule_bundles(inter_call_delay=8.0)` was executed after Step 2D tests passed.

**Result:** All 8 bundles loaded. No API crash. No missing bundle.

**Issue found:** Two boolean fields in `graduation_requirement_rules` extracted as `False`:
- `must_pass_zero_credit_courses`: False → should be True
- `military_training_required_for_males`: False → should be True

**Root cause:** LLM boolean defaults emit `False` when those specific lines are missed in retrieval.

**Fix applied — `adapters/rag_adapter.py`:**
- Added `_load_rule_bundle_delay() -> float` module-level helper (reads `RAG_RULE_BUNDLE_DELAY_SECONDS` env var, defaults to 2.0).
- Changed `get_rule_bundles(inter_call_delay: float = 2.0)` → `get_rule_bundles(inter_call_delay: float | None = None)`.
- If `inter_call_delay` is `None`, reads env; explicit arg takes precedence. Fully backward-compatible (`main.py` now auto-uses env value).
- Added deterministic handbook-backed normalization after graduation `_merge()`:
  - `gr_data["must_pass_zero_credit_courses"] = True`
  - `gr_data["military_training_required_for_males"] = True`

**`.env.example` updated:** `RAG_RULE_BUNDLE_DELAY_SECONDS=2.0` added under RAG section.

**5 new tests added to `tests/test_rag_rule_bundles.py`:**

```
test_get_rule_bundles_explicit_delay_is_used           PASS ✅
test_get_rule_bundles_reads_env_delay                  PASS ✅
test_load_rule_bundle_delay_invalid_env_falls_back     PASS ✅
test_graduation_normalization_overrides_false           PASS ✅
test_graduation_normalization_preserves_other_values   PASS ✅
```

**Full test suite after patch:**

```
tests/test_rag_rule_bundles.py          16 passed, 0 failed  ✅
tests/test_rag_adapter_execute.py       10 passed, 0 failed  ✅  (regression)
tests/test_rag_adapter_structured.py     9 passed, 0 failed  ✅  (regression)
tests/test_rag_core_structured.py        8 passed, 0 failed  ✅  (regression)
Total: 43 passed, 0 failed
```

**ALE files not modified. No live Groq calls in any test. Step 2 remains locked.**

---

##### Step 2 Carry-Forward Register

| # | Item | Carry to |
|---|---|---|
| RAG-1 | Chroma import deprecation | Phase 5 / dependency cleanup |
| RAG-2 | Synthetic page 0 citation policy | RAGAdapter / Composer audit |
| RAG-3 | GPT-OSS limitation-query citation concerns | RAGAdapter / Composer audit |
| RAG-4 | Rule-bundle extraction not audited | ~~Step 2D~~ **CLOSED — Step 2D complete** |
| RAG-5 | Groq 429 rate limits during manual tests | Testing strategy / startup / caching |
| RAG-6 | Provider/model deprecation/availability risk | Deployment notes / final README |
| RAG-7 | Retriever/model load at import time affects startup | Phase 5 performance |
| RAG-8 | RAG still uses direct Groq requests, not shared LLMClient | Future cleanup |
| RAG-CF-1 | Llama primary model accepted for demo but has deprecation/migration risk | Deployment notes / final README |
| RAG-CF-2 | Some exact numeric bracket cases can include adjacent bracket; Composer/ALE should apply student-specific values | RAGAdapter / Composer / ALE audit |
| RAG-CF-3 | Some extracted facts awkward/truncated due to chunk boundaries | Composer audit |
| RAG-CF-4 | Some no-evidence/limitation answers can be synthesized rather than exactly quoted; monitor in Adapter/Composer | RAGAdapter / Composer audit |
| RAG-CF-5 | Normal RAG is not the authority for deterministic academic calculations; ALE/rule bundles remain the source for graduation audit, eligibility, GPA simulation, credit limits, honors | ALE audit / Orchestrator audit |
| RAG-CF-6 | Rule-bundle structured extraction still not audited | ~~Step 2D~~ **CLOSED — Step 2D complete** |
| RAG-CF-7 | RAGAdapter normal path still not audited | ~~Step 2C~~ **CLOSED — Step 2C complete** |
| RAG-CF-8 | All manual eval artifacts should live under `engines/rag/manual_eval/` | Confirmed organization |

---

#### KG/RAG Observability + Documentation Follow-up: COMPLETE ✅

- RAGAdapter now logs safe operation-level summaries:
  - execute()
  - execute_structured()
  - get_rule_bundles()
- KGAdapter now logs safe operation-level summaries:
  - operation start
  - summarized params
  - success/business_error/adapter_error
  - result keys/counts
  - duration_ms
- RAG technical documentation created:
  - engines/rag/RAG_TECHNICAL_DOCUMENTATION.md
- KG technical documentation created:
  - engines/kg/KG_TECHNICAL_DOCUMENTATION.md
- No engine logic changed during documentation.
- KG/RAG are now functionally locked and observable enough for Phase 2 integration.

---

#### Step 3 — ALE Engine + ALEAdapter Audit

**Status: COMPLETE ✅**

**Files audited:** All files under `engines/ale/functions/`, `adapters/ale_adapter.py`

**Goal:** Ensure every ALE function produces academically correct results, uses rule bundles (never hardcoded thresholds), and returns structured, diagnosable output.

---

##### Step 3A — `check_course_eligibility`

**Status: COMPLETE — PASS ✅**

**Findings:**
- Correctly returns: `eligible`, `not_eligible`, `in_progress`, `already_completed`.
- Prerequisite checking covers direct prerequisites via `completed_courses`.
- Credit-threshold prerequisites checked correctly.
- What-if assumptions applied from `effective_context` when provided.
- `reason_codes` returned consistently across all output branches.
- `missing_prerequisite_names` listed when not eligible.

**Changes made:** None — no production bugs found.

**Tests:** No standalone test file created in this pass. Eligibility logic is exercised transitively by `generate_semester_plan` and `generate_graduation_roadmap` test suites.

**Step 3A result: PASS ✅**

---

##### Step 3B — `run_graduation_audit`

**Status: COMPLETE — PASS ✅**

**Findings:**
- Always operates on `base_context` — never `effective_context` or session assumptions. Correct.
- Checks all graduation requirements from `graduation_requirement_rules` bundle:
  - Total credit hours earned vs. required.
  - CGPA vs. minimum.
  - Regular semesters completed vs. minimum required.
  - Zero-credit courses passed (when `must_pass_zero_credit_courses=True`).
  - Military training (when `military_training_required_for_males=True`).
- Returns clear `passed/failed` per requirement category.
- Returns explicit `cannot_compute` with `reason_codes` and `required_data_missing` when bundles are missing.
- Non-male students: military requirement correctly marked not-applicable.

**Changes made:** None — no production bugs found.

**Tests created:** `engines/ale/tests/test_run_graduation_audit.py`
- 23 synthetic tests (Groups A–D: structural validation, requirement checking, edge cases, `cannot_compute` guards)
- 3 real-record tests (STU000004 studying gate, STU000026 status check, graduated-student gate)
- Result: **26 passed, 0 failed**

**Step 3B result: PASS ✅**

---

##### Step 3C — `simulate_gpa_forward`

**Status: COMPLETE — PASS WITH FIXES ✅**

**Findings:**
- Core GPA simulation correct: new quality points added to existing; denominator increases by non-zero-credit courses only.
- Pass/fail and zero-credit courses correctly excluded from GPA calculation.
- Edge case: empty planned-courses list → returns current CGPA unchanged. Correct.
- Missing null guards: `gpa_counted_credits=None` and `current_quality_points=None` would cause silent `TypeError` in division.

**Issues found and fixed:**

| # | Issue | Fix |
|---|-------|-----|
| 1 | `gpa_counted_credits=None` caused silent `TypeError` in division | Added explicit `None` guard → `cannot_compute ["missing_gpa_counted_credits"]` |
| 2 | `current_quality_points=None` caused silent `TypeError` | Added explicit `None` guard → `cannot_compute ["missing_current_quality_points"]` |

**Changes made:**
- `engines/ale/functions/simulate_gpa_forward.py`: two null-input guards added at Phase 1 validation.

**Tests created:** `engines/ale/tests/test_simulate_gpa_forward.py`
- 21 synthetic tests (Groups A–D: structural validation, GPA arithmetic, edge cases, cannot_compute guards)
- 3 real-record tests (STU000004 CGPA projection, zero-PHs edge case, non-studying gate)
- Result: **24 passed, 0 failed**

**Step 3C result: PASS WITH FIXES ✅**

---

##### Step 3D — `solve_target_gpa`

**Status: COMPLETE — PASS WITH FIXES ✅**

**Findings:**
- Core target-GPA math is correct: footprint courses use quality-point replacement (subtract old QP, add new QP); denominator unchanged.
- Multi-semester projection existed but ignored `completed_regular_semesters` — wrong for mid-enrollment students.
- Grade-target distribution was not cap-aware: excess target points assigned to capped courses, violating `max_grade_points`.
- `attempt_type` was `str` — no Literal validation; invalid values silently ignored.
- Missing guards for `credits_per_semester <= 0`, `gpa_counted_credits=None`, and invalid `attempt_type` at runtime.

**Issues found and fixed:**

| # | Issue | Fix |
|---|-------|-----|
| 1 | `PlannedCourseTarget.attempt_type: str` — no Literal validation | Changed to `Literal["first_attempt", "failed_retake", "improve_retake"]` in `schemas.py` |
| 2 | Missing `completed_regular_semesters` in `SolveTargetGPAInput` | Added `completed_regular_semesters: int \| None = None` |
| 3 | `gpa_counted_credits=None` guard missing | Added → `cannot_compute ["missing_gpa_counted_credits"]` |
| 4 | Invalid `attempt_type` not caught at runtime (model_construct bypass) | Added belt-and-suspenders guard → `cannot_compute [f"invalid_attempt_type_{code}"]` |
| 5 | Cap-aware distribution missing: excess QP assigned to capped courses | Implemented iterative redistribution: excess from capped courses redistributed to uncapped; no target ever exceeds `max_grade_points` |
| 6 | `credits_per_semester <= 0` guard missing | Added → `cannot_compute ["invalid_credits_per_semester"]` |
| 7 | Multi-semester projection ignored `completed_regular_semesters` | `remaining_sems = max(0, max_regular_sems - completed_regular_semesters)` when provided; warning emitted when not provided |
| 8 | Personalization thresholds hardcoded inline | Promoted to named constants: `_LOW_HISTORY_THRESHOLD=2.4`, `_HIGH_HISTORY_THRESHOLD=3.4`, `_PERSONALIZATION_DELTA=0.4` |

**`ale_adapter.py` change:**
- `_solve_target_gpa()` now passes `completed_regular_semesters=sc.completed_regular_semesters`.

**STU000009 anchor — replacement policy confirmed:**
- `cumulative_chs=43` (additive would give ≥47) proves footprint courses use replacement, not addition.
- Phase 3 math verified: subtract old QP, add new QP; denominator unchanged for footprint courses.

**Tests created:** `engines/ale/tests/test_solve_target_gpa.py`
- 32 synthetic tests (Groups A–F: structural validation, GPA math, footprint vs. first-attempt, cap-aware distribution, multi-semester projection, cannot_compute guards)
- 4 real-record tests (STU000004 projection, STU000009 replacement-policy anchor, STU000026 status check, null-field cannot_compute)
- Result: **36 passed, 0 failed**

**Step 3D result: PASS WITH FIXES ✅**

---

##### Step 3E — `generate_semester_plan`

**Status: COMPLETE — PASS WITH FIXES ✅**

**Findings:**
- Normal-semester planning (Fall/Spring) was correct.
- `target_semester_type` and `student_level` were `str` — no Literal validation.
- Silent Freshman default: `_LEVEL_MAP.get(input.student_level, 1)` silently defaulted invalid levels.
- Minimum-credit warning not emitted when `target_credit_load < minimum_per_semester` for non-Summer semesters.
- Eligible-credits warning incorrectly firing for Summer (credit minimum is not applicable; Summer uses course-count cap).

**Issues found and fixed:**

| # | Issue | Fix |
|---|-------|-----|
| 1 | `target_semester_type: str` — no Literal validation | Changed to `Literal["Fall", "Spring", "Summer"]` in `schemas.py` |
| 2 | `student_level: str` — no Literal validation | Changed to `Literal["Freshman", "Sophomore", "Junior", "Senior"]` in `schemas.py` |
| 3 | Belt-and-suspenders guard missing for invalid `target_semester_type` | Added → `cannot_compute ["invalid_target_semester_type"]` |
| 4 | Belt-and-suspenders guard missing for invalid `student_level` | Added → `cannot_compute ["invalid_student_level"]` |
| 5 | Silent Freshman default: `_LEVEL_MAP.get(input.student_level, 1)` | Changed to `_LEVEL_MAP[input.student_level]` (safe after guard) |
| 6 | Minimum-credit warning not raised when load below minimum | Added warning for non-Summer only: `target_credit_load < minimum_per_semester` |
| 7 | Eligible-credits warning firing on Summer passes | Wrapped in `if not is_summer:` guard |

**Tests created:** `engines/ale/tests/test_generate_semester_plan.py`
- 57 synthetic tests (Groups A–H: structural validation, invalid-value guards, Summer vs. normal semester, credit-cap enforcement, course eligibility filtering, retake prioritization, minimum-credit warning, edge cases)
- 9 real-record tests (STU000004, STU000009, STU000026 — multiple scenarios including Summer, max_credits_mode, target-semester type selection)
- Result: **66 passed, 0 failed**

**Step 3E result: PASS WITH FIXES ✅**

---

##### Step 3F — `generate_graduation_roadmap`

**Status: COMPLETE — PASS WITH FIXES ✅**

**Scope:** Multi-semester graduation projection engine. Two operating modes:
- **Graduation mode:** simulate forward until graduation or a terminal stop condition.
- **Target-semester mode:** simulate through a caller-resolved target end semester and stop whether or not graduation is reached.

**Issues found and fixed:**

| # | Issue | Fix |
|---|-------|-----|
| 1 | `target_semester_type: str` — no Literal validation | Changed to `Literal["Fall", "Spring", "Summer"]` in `schemas.py` |
| 2 | `student_level: str` — no Literal validation | Changed to `Literal["Freshman", "Sophomore", "Junior", "Senior"]` in `schemas.py` |
| 3 | Belt-and-suspenders guards missing for invalid semester type / student level | Added: `invalid_target_semester_type`, `invalid_student_level` |
| 4 | No `starting_year` sanity range check | Added `_YEAR_MIN=2000`, `_YEAR_MAX=2100` guard → `invalid_starting_year` |
| 5 | No target-end-semester mode | Added `target_end_semester_type / target_end_year` fields; validation: both fields or neither, type check, year range, chronological order → `target_end_before_start` |
| 6 | CGPA graduation gate used initial CGPA — wrong for multi-semester projection | Moved gate to Phase 5; uses final `sim_cgpa` after loop exits |
| 7 | `already_done` check did not verify projected CGPA | Added `sim_cgpa >= graduation_rules.minimum_cgpa` to `already_done` condition |
| 8 | Military and zero-credit blockers not gated by rule toggles | Military gated by `military_training_required_for_males`; zero-credit by `must_pass_zero_credit_courses` |
| 9 | Failed-retake grade cap not applied during GPA simulation | Adapter pre-resolves `retake_rules.failed_first_retake_grade_cap` letter to float; ALE applies `min(assumed_grade, cap_pts)` per retake course |
| 10 | No warning progression simulation | Added `sim_consecutive_warnings`, `sim_total_warnings`; updated after each regular pass (Summer excluded); early stop when limit reached → `projected_warning_limit_reached` |
| 11 | Max-semester enforcement did not use `completed_regular_semesters` | Fixed: `completed_regular_semesters + simulated_regular >= maximum_regular_semesters` |
| 12 | Eligible-credits warning firing on Summer passes | Wrapped in `if not is_summer_pass:` guard |
| 13 | No semester chronological ordering helper | Added `_semester_key(season, year) -> tuple[int, int]` with academic-year encoding (Fall YYYY → (YYYY, 0); Spring YYYY → (YYYY-1, 1); Summer YYYY → (YYYY-1, 2)) |

**New schema fields — `GenerateGraduationRoadmapInput`:**
```python
target_end_semester_type: Literal["Fall", "Spring", "Summer"] | None = None
target_end_year: int | None = None
consecutive_warnings: int = 0
total_warnings: int = 0
warning_rules: AcademicWarningRules | None = None
failed_retake_grade_cap_points: float | None = None
```

**New schema fields — `GenerateGraduationRoadmapOutput`:**
```python
target_reached_without_graduation: bool = False
projected_consecutive_warnings: int | None = None
projected_total_warnings: int | None = None
warning_limit_reached_in_semester: str | None = None
```

**`ale_adapter.py` changes (`_generate_graduation_roadmap`):**
- Parses `AcademicWarningRules` from `rule_bundles["academic_warning_rules"]`.
- Pre-resolves `failed_retake_grade_cap_points` from `retake_rules.failed_first_retake_grade_cap` letter via grading scale.
- Passes `consecutive_warnings`, `total_warnings`, `warning_rules`, `failed_retake_grade_cap_points`, `target_end_semester_type`, `target_end_year` to `GenerateGraduationRoadmapInput`.

**Tests created:**

`engines/ale/tests/test_generate_graduation_roadmap.py`:
- 66 synthetic tests across Groups A–J:
  - Group A (15): structural validation, required-data-missing guards, cannot_compute paths
  - Group B (8): invalid-value rejection via belt-and-suspenders guards
  - Group C (2): study status gate (non-studying returns `not_applicable`)
  - Group D (5): graduation mode behavior (multi-semester, terminal stops)
  - Group E (5): target-semester mode stop and gap reporting
  - Group F (6): GPA behavior including failed-retake cap
  - Group G (6): warning progression simulation
  - Group H (6): non-course requirements (military, zero-credit, rule-toggle gating)
  - Group I (8): eligibility filtering and course selection
  - Group J (5): semester sequence and credit-load behavior

`engines/ale/tests/test_generate_graduation_roadmap_real_records.py`:
- 9 tests (8 pass, 1 skipped — no graduated student in current dataset)
  - STU000004 (7 tests): study-status gate, PHs=16 verified, multi-semester roadmap produced, default vs. `max_credits_mode` cap, completed-courses not reselected, target-semester mode stops early
  - STU000026 (1 test): study-status gate stable
  - Non-studying (1 test): graduated-student gate — skipped (no graduated record found)
- Result: **74 passed, 1 skipped**

**Step 3F result: PASS WITH FIXES ✅**

---

##### ALE Core Function Audit Summary

| Function | Status | Synthetic Tests | Real-Record Tests | Changes |
|---|---|---|---|---|
| `check_course_eligibility` | PASS ✅ | — (covered by plan/roadmap suites) | — | None |
| `run_graduation_audit` | PASS ✅ | 23 | 3 | None |
| `simulate_gpa_forward` | PASS WITH FIXES ✅ | 21 | 3 | 2 null guards added |
| `solve_target_gpa` | PASS WITH FIXES ✅ | 32 | 4 | 8 fixes; `schemas.py`; adapter updated |
| `generate_semester_plan` | PASS WITH FIXES ✅ | 57 | 9 | 7 fixes; `schemas.py` |
| `generate_graduation_roadmap` | PASS WITH FIXES ✅ | 66 | 8 (+1 skip) | 13 fixes; `schemas.py`; adapter updated |
| **Total** | | **199** | **27 (+1 skip)** | |

**Combined ALE test suite: 241 passed, 5 skipped, 0 failed — Runtime: 7.70s**

---

##### Step 3 Closure Audit Summary

| Closure Item | Status | Details |
|---|---|---|
| `grade_resolver.py` audit | PASS ✅ | Abs normalization, numeric-string parsing, `math.isfinite()` guard, `derive_level()` |
| `schemas.py` consistency audit | PASS ✅ | `str` → `Literal` for attempt_type, semester_type, student_level; docstrings updated |
| `adapters/ale_adapter.py` full audit | PASS ✅ | ADAPTER-1 (level fallback removed), ADAPTER-2 (zero-credit boolean fixed), ADAPTER-3–8 confirmed |
| ALEAdapter logging pass | COMPLETE ✅ | Safe summarizers, timing, INFO/WARNING/ERROR logs, no PII logged |
| ALE technical documentation | COMPLETE ✅ | `engines/ale/ALE_TECHNICAL_DOCUMENTATION.md` — 10 sections |

**`grade_resolver.py` key fixes (2026-06-22):**
- Added `_resolve_string()` dispatcher; numeric strings (`"3.7"`, `"90"`) parsed via `float()` and routed to `_resolve_numeric()`
- Added `_normalize_letter()`: `abs/ABS/Abs` → `"Abs"`, others → `.upper()` (preserves `+`/`-`)
- Added `math.isfinite()` guard — `float("nan")` / `float("inf")` now explicitly rejected
- Whitespace stripped before any resolution; empty/whitespace-only strings raise `GradeResolutionError`

**`adapters/ale_adapter.py` key fixes (2026-06-22):**
- **ADAPTER-1**: `_map_student_level(level) -> str | None` — invalid levels return `None` → `cannot_compute / invalid_student_level`; silent Freshman default removed from both `_generate_semester_plan` and `_generate_graduation_roadmap`
- **ADAPTER-2**: `_compute_zero_credit_requirement_passed(sc, kg_data, params) -> bool | None` — `bool(sc.zero_credit_courses_passed)` replaced; returns `set(required).issubset(set(passed))`; returns `None` when required list absent → `cannot_compute / missing_required_zero_credit_course_list`
- **ADAPTER-3**: `_parse_rules()` logs warning + raises `ValueError` on missing/invalid bundle (correct `error` path — infrastructure issue)
- **ADAPTER-4–8**: Confirmed correct — no additional fixes needed

**ALEAdapter logging additions:**
- Module-level constants: `_SAFE_SCALAR_PARAM_KEYS`, `_RESULT_COUNT_FIELDS`
- Helpers: `_duration_ms(start) -> int`, `_summarize_params(params)`, `_summarize_kg_data(kg_data)`, `_summarize_result(result)`
- `call()`: INFO at start, INFO/WARNING at result (WARNING when `cannot_compute`/`error`), ERROR + WARNING on exceptions; `duration_ms` in every result log
- All summarizers: counts + 3-item code previews only; no PII, no full course lists, no grades

---

##### Step 3 Final Test Summary

| Test file | Passed | Skipped | Failed |
|---|---|---|---|
| `engines/ale/tests/test_check_course_eligibility.py` | (covered by plan/roadmap suites) | — | — |
| `engines/ale/tests/test_run_graduation_audit.py` | 26 | 0 | 0 |
| `engines/ale/tests/test_simulate_gpa_forward.py` | 24 | 0 | 0 |
| `engines/ale/tests/test_solve_target_gpa.py` | 36 | 0 | 0 |
| `engines/ale/tests/test_generate_semester_plan.py` | 66 | 0 | 0 |
| `engines/ale/tests/test_generate_graduation_roadmap.py` | 74 | 1 | 0 |
| `engines/ale/tests/test_generate_graduation_roadmap_real_records.py` | (included above) | — | — |
| `engines/ale/tests/test_grade_resolver.py` | 43 | 0 | 0 |
| `engines/ale/tests/test_grade_resolver_real_records.py` | 15 | 4 | 0 |
| `tests/test_ale_adapter.py` | 59 | 0 | 0 |
| `tests/smoke_test_ale_adapter.py` | 15 | 0 | 0 |
| **Total** | **358** | **5** | **0** |

5 skips = "no graduated student in current dataset" (pre-existing; valid data gap, not a bug).

---

##### Step 3 Carry-Forward Register

| # | Item | Carry to |
|---|---|---|
| ALE-A | `credits = profile.get("credits") or 3` in Orchestrator silently defaults 0-credit courses to 3 — wrong for GPA math | Step 7 — Orchestrator audit |
| ALE-B | `old_grade` not set for target-GPA footprint courses in Orchestrator → `solve_target_gpa` returns `cannot_compute` for retake cases | Step 7 — Orchestrator audit |
| ALE-C | `improve_retake_number` not computed for `PlannedCourseTarget` in Orchestrator | Step 7 — Orchestrator audit |
| ALE-D | Orchestrator must pass `required_zero_credit_courses` in `kg_data` for graduation audit and roadmap calls | Step 7 — Orchestrator audit |
| ALE-E | Composer must phrase multi-semester GPA projection as a simplified mathematical projection, not an actual registration roadmap | Step 8 — Composer audit |

---

**Step 3 result: COMPLETE ✅**

---

#### Step 4 — Student Context Provider Audit

**Files:** `gateway/student_context_provider.py`, `gateway/models/schemas.py`,
`adapters/ale_adapter.py` (compatibility guard only), `tests/test_student_context_provider.py`

**Goal:** Ensure the official student record is extracted correctly from the registrar Excel data and that all academic status semantics are correct.

---

### Status: COMPLETE ✅ — PASS / LOCKED WITH INTEGRATION CARRY-FORWARD NOTES

---

#### SCP Scope and Responsibility

SCP owns:
- Student scalar fields from the registrar (CGPA, credit totals, level, study status, first semester, military status, warnings)
- Course attempt history (`course_history: list[CourseRecord]`)
- Derived course lists: `completed_courses`, `failed_courses`, `in_progress_courses`, `zero_credit_courses_passed`
- `retake_count` and `total_improve_retakes_used`
- `completed_regular_semesters`
- `current_semester` inference from registrar data
- Track support status (`track_id`, `track_status`, `track_error_code`)

SCP does NOT own:
- Course names (codes only; KG enriches names)
- Per-course credit hours (all `CourseRecord.credit_hours = None`; KG/Orchestrator patches credits)
- Prerequisites
- Eligibility decisions
- Semester planning
- Graduation audit
- Career recommendations
- Final response wording
- Session assumptions

---

#### Files Changed

**`gateway/models/schemas.py`**

| Field | Before | After |
|---|---|---|
| `CourseRecord.credit_hours` | `int` | `Optional[int] = None` |
| `StudentContext.track_id` | `str` | `Optional[str] = None` |
| `StudentContext.track_status` | *(absent)* | `Literal["supported","unsupported"] = "supported"` |
| `StudentContext.track_error_code` | *(absent)* | `Optional[str] = None` |
| `StudentContext.level` | `int` | `Optional[int] = None` |

**`gateway/student_context_provider.py`** — rewritten with 9 logic fixes + global inference:

| Fix | Description |
|---|---|
| Track normalization | `PROGRAM_TO_TRACK` maps to KG canonical IDs only: AI, CYS, DSE, SWE, GEN. Old informal values removed. |
| CS → unsupported | Computer Science returns `track_id=None, track_status="unsupported"` — no KG node exists |
| Unknown programs | Unknown program strings → unsupported |
| Level=None | `LEVEL_MAP.get(level_raw)` with None default; no silent Freshman fallback |
| `credit_hours=None` | `CourseRecord.credit_hours=None` everywhere; sentinel `0` removed |
| Improve retakes | `total_improve_retakes_used` counts distinct course codes, not raw attempt rows |
| Failed course detection | `latest_meaningful_status` (latest non-withdrawn row) replaces priority map — fixes "incomplete hides later failed" |
| Global current semester | `_compute_global_current_semester(df_reg)`: counts active blank-registered rows across entire sheet; threshold ≥ 100 rows; Spring 2026 = 3170 rows — winner by large margin |
| `in_progress_courses` | Active blank-registered rows in global current semester only; I-grade rows (incomplete) are NOT in `in_progress_courses` |
| `completed_regular_semesters` | Always excludes `_global_current_semester`; if None (synthetic tests), excludes nothing |

**Active blank-registered row definition:**
- `Letter Grade` is blank (None)
- `Registration Status` contains `Registered`
- `Registration Status` does NOT contain `Succeeded`, `Failed`, `Withdrawn`, or `Forced Withdraw`

Grade normalization added to `_clean_grade()`: P, Con, I, F, Abs, W normalized to canonical casing regardless of input case.

**`adapters/ale_adapter.py`** — compatibility guard only:
- `credits = real_credits if real_credits is not None else 0` in `_map_course_history()`
- Preserves `CourseHistoryEntry.credits: int` contract while `CourseRecord.credit_hours` is now `Optional[int]`
- No other changes to ALE logic

**`tests/test_student_context_provider.py`** — expanded from 23 to 90 tests:
- `_inject()` helper updated: accepts `global_current_sem=None` parameter to set `scp._global_current_semester` directly for synthetic tests
- 31 Phase 1 tests added (credit_hours, track normalization, level, semester inference, course outcomes, improve-retakes)
- 36 Phase 2 tests added (global inference, active-blank in_progress, FW grade variants, grade normalization, `_is_active_blank` parametrized)
- 6 tests updated (Tests 4, 13, 15, 17, 35–38) for new global-inference semantics

---

#### SCP Design Decisions (Locked)

1. **SCP owns registrar/student-record facts only.** No academic rules, no course catalogue, no eligibility, no planning.

2. **`CourseRecord.credit_hours = Optional[int] = None` everywhere.** SCP has no authoritative course-credit data. Student-level totals (`cumulative_chs`, `total_credit_hours_earned`) are kept because they come directly from the registrar sheet — a different source from per-course catalogue credits.

3. **Global current semester inferred once at `load_excel()` time.** Not per-student. Method: count active blank-registered rows per semester across the full registrations sheet; the semester with the highest count wins if count ≥ 100. Real-data result: Spring 2026 = 3170 active rows.

4. **`in_progress_courses` = active blank-registered rows in the global current semester only.** I-grade (incomplete) rows from past semesters are not in `in_progress_courses` — they appear in `course_history` with `status="incomplete"`.

5. **`completed_regular_semesters` always excludes the global current semester.** Fall and Spring only. Summer excluded. When `_global_current_semester` is None (synthetic tests without `load_excel()`), no semester is excluded.

6. **Supported tracks map to KG canonical IDs only: AI, CYS, DSE, SWE, GEN.** Old informal values (Cyber, Data Science, SW, CS, General) must never be used.

7. **Computer Science and unknown programs return `track_status="unsupported"`, `track_id=None`, `track_error_code="unsupported_track"`.** Student record still loads; only track-dependent flows (planning, roadmap, recommendations) must be blocked by the Orchestrator.

8. **Forced Withdraw is currently treated as `withdrawn`.** Real data shows 31 FW rows in Fall 2025 with three grade patterns: blank (14), Abs (14), W (3). All are mapped to `withdrawn`. FW+Abs policy needs supervisor/registrar confirmation before changing.

---

#### Test Results

```
SCP tests:             90 passed, 0 failed ✅
ALE adapter + smoke:   74 passed, 0 failed ✅ (unchanged by SCP changes)
Full tests/ suite:    444 passed, 2 pre-existing failures

Pre-existing failures (out of SCP scope):
  test_response_composer.py::TestCompose::test_compose_llm_model_chain_tries_primary_then_fallback
    — Composer mock side_effect() doesn't accept timeout_seconds (added in Step 0); fix in Composer audit
  tests/test_rag_adapter.py::test_rule_bundle_academic_warning_extra_summer_semesters
    — RAG rule bundle test from untracked test files; not SCP-related
```

---

#### Real-Record Validation

Students sampled: STU000001, STU000005, STU000017, STU000026, STU000041, STU000100

| Student | Program | Status | Validation |
|---|---|---|---|
| STU000001 | AI | Studying, Level 4 | current_semester="Spring 2026" via active_blank_threshold ✅; in_progress_courses populated ✅ |
| STU000005 | GEN | Studying | Failed-then-passed courses correctly separated ✅ |
| STU000017 | AI | Graduated | No in_progress_courses; improve retakes counted as distinct courses ✅ |
| STU000026 | CS | Graduated | track_id=None, track_status="unsupported"; context still loads ✅ |
| STU000041 | GEN | Suspended, Freshman | All FW rows → withdrawn; failed_courses=[], retake_count={} ✅ |
| STU000100 | CYS | Graduated | track_id="CYS" ✅ |

Track distribution in real data:
```
AI  (53 students)  → supported
CYS (33 students)  → supported
DSE (15 students)  → supported
SWE (11 students)  → supported
GEN (693 students) → supported
CS  (11 students)  → unsupported
```

---

#### Step 4 Carry-Forward Notes (Orchestrator Integration Responsibility)

| # | Note |
|---|---|
| SCP-CF-1 | Orchestrator must block track-dependent flows (planning, roadmap, recommendations) when `track_status == "unsupported"`. KG has no curriculum data for unsupported programs. |
| SCP-CF-2 | Orchestrator/KG must enrich course names for student-facing answers. `StudentContext.completed_courses` contains only codes. |
| SCP-CF-3 | Orchestrator must always provide `kg_data["course_credit_lookup"]` for ALE calls that need credits. `CourseRecord.credit_hours = None` everywhere; ALE adapter falls back to 0 when lookup absent. |
| SCP-CF-4 | Orchestrator must not use `retake_count` as `improve_retake_number`. `retake_count` is the non-withdrawn attempt count per course; `total_improve_retakes_used` is the slot count (distinct courses improved). |
| SCP-CF-5 | Forced Withdraw policy (FW+Abs grade variant) must be confirmed with supervisor/registrar. Current behavior: FW → withdrawn regardless of grade. |
| SCP-CF-6 | Composer mock `timeout_seconds` failure belongs to the Composer audit (Step 8). |
| SCP-CF-7 | RAG rule bundle test failure belongs to the RAG/RAGAdapter audit or integration environment. Not SCP-related. |

---

#### Step 5 — Session Manager Audit

**Status: COMPLETE ✅**

**Files audited:**
```
gateway/session_manager.py
gateway/session_store/base.py
gateway/session_store/sqlite_store.py
gateway/models/schemas.py                          (session-related models only)
main.py                                            (session endpoints and logging)
gateway/orchestrator.py                            (last_referenced extraction compatibility only)
tests/test_session_manager.py
tests/test_main.py
gateway/Documentation/Session_Manager_Technical_Documentation.md
```

**Goal:** Ensure multi-turn session state is correct, assumption overrides are isolated from the official record, and reset behavior is clean.

---

##### Verified and Fixed Behavior

**Session ownership safety**
- Provided `session_id` is checked against the stored `student_id` before reuse.
- Mismatch (different student) → new session created; safe warning logged with `owner=<redacted>` (no PII).
- Stale/unknown `session_id` → new session created.
- Same-student reuse → `student_context` refreshed from the fresh SCP context while preserving overrides, `last_referenced`, and `turn_history`.

**Override merging (deterministic)**
- `_apply_overrides()` uses `_dedupe_ordered()` for all list merges: first-occurrence order preserved, no set-order instability.
- `override_action="clear"` → returns empty `SessionOverrides()`.
- `override_action="replace"` → replaces with incoming only; resets to `accumulate` for the next turn.
- `override_action="accumulate"` (default) → unions all three course lists independently.

**`build_effective_context` — independent list handling**
```
Step 1 — assumed_failed_courses:
  Add to failed_courses.
  Remove from completed_courses, in_progress_courses, zero_credit_courses_passed.

Step 2 — assumed_passed_courses:
  Add to completed_courses.
  Remove from failed_courses, in_progress_courses.

Step 3 — added_courses (planned / assumed_done):
  course_override_type == "assumed_done"  → add to completed, remove from failed/in_progress
  anything else                           → add to in_progress (planned), regardless of override_type

If same course appears in both assumed_failed and assumed_passed:
  failed applied first (Step 1), passed applied second (Step 2) → course ends in completed.
  Deterministic and documented by test.
```
- `base_context` is never mutated: all lists copied at the top of the function.
- No-op case: empty overrides → returns `base_context` unchanged (same object, no copy).

**`LastReferenced` — full four-field support**
- `LastReferenced` now carries: `course_code`, `role_id`, `track_id`, `skill_id`.
- `_apply_last_referenced()` merges field by field: only updates a field when the new value is non-None/non-empty; prior values for unmentioned fields are preserved.
- `Orchestrator.extract_last_referenced()` includes `skill_id` from `EntitySet`.
- `update_session_after_turn()` merges via `_apply_last_referenced()`, never replaces.

**Schema mutable defaults**
- `SessionOverrides.added_courses`, `assumed_failed_courses`, `assumed_passed_courses` → `Field(default_factory=list)` ✅
- `SessionState.last_referenced` → `Field(default_factory=LastReferenced)` ✅
- `SessionState.overrides` → `Field(default_factory=SessionOverrides)` ✅
- No instance shares a mutable default with another instance.

**History endpoint safety**
- `GET /students/{student_id}/sessions/{session_id}/history` → ownership checked before returning history; wrong student → 404.
- Old `GET /session/{session_id}/history` → returns **410 Gone** with migration note.

**Delete / session API layering**

| Function | Who it serves | Ownership check |
|---|---|---|
| `SQLiteSessionStore.delete(session_id)` | Internal primitive only | None |
| `delete_session_for_student(student_id, session_id)` | Student-facing delete | Yes — ownership verified |
| `delete_all_sessions_for_student(student_id)` | Developer/admin cleanup | n/a (student-scoped) |
| `delete_all_sessions_global()` | Developer/admin cleanup | n/a (global) |
| `clear_all_sessions()` | Backward-compat alias | → delegates to `delete_all_sessions_global()` |

Student-facing endpoints:
- `DELETE /students/{student_id}/sessions/{session_id}` → calls `delete_session_for_student`; returns 404 on mismatch or not-found.

Developer-only cleanup endpoints (guarded by `_is_dev_mode()` — `APP_ENV=dev` or `DEV_MODE=true`):
- `DELETE /dev/students/{student_id}/sessions` → deletes all sessions for one student; returns 403 outside dev mode.
- `DELETE /dev/sessions` → deletes all sessions globally; logged at WARNING; returns 403 outside dev mode.

**Deletion mechanism clarification:**
The multiple delete functions represent intentional backend layering, not multiple student-facing features.
- The student UI should expose only one delete action: "Delete session" from the chat history menu, which calls `DELETE /students/{student_id}/sessions/{session_id}`.
- The bulk and global deletes are developer/admin cleanup utilities only and must never appear in the student UI.

**Logging**
- `/chat` log line changed from `request.user_text[:60]` → `query_len=len(request.user_text or "")`.
- Session lifecycle events logged at `INFO`: new/reused/refreshed/mismatch.
- Override counts logged, not full override lists.
- Mismatch warnings use `owner=<redacted>`.
- Dev global delete logged at `WARNING`.
- No full `StudentContext` dumps, no transcript dumps, no raw grade lists.

**Legacy `apply_query_result`**
- Kept at `session_manager.py` with a clear LEGACY docstring.
- Does not conflict with `update_session_after_turn`.
- Tests in G10 cover it for backward compatibility.

---

##### Files Changed

| File | Change |
|---|---|
| `main.py` | Replaced `request.user_text[:60]` with `query_len=len(request.user_text or "")` in `/chat` log |

All other verified behavior was already correct after the Antigravity patch.

---

##### Test Results

```
python -m pytest tests/test_session_manager.py tests/test_main.py -v --tb=short
100 passed in 1.69s ✅
```

Test groups covered:
```
G1  — SQLiteSessionStore isolation (save/load/delete/corrupted blobs)    11 tests
G2  — Override accumulation (_apply_overrides / merge_turn_overrides)    11 tests
G3  — build_effective_context multi-list independence                    13 tests
G4  — last_referenced merging                                             5 tests
G5  — get_or_create_session ownership / context refresh                   7 tests
G6  — update_session_after_turn (merge, replace, last_referenced merge)   4 tests
G7  — Delete operations (single, per-student, global, history safety)     8 tests
G8  — Session lifecycle integration                                        3 tests
G9  — Schema mutable-default regression                                    6 tests
G10 — Legacy apply_query_result (backward compatibility)                   4 tests
test_main.py — FastAPI endpoint wiring and safety                         30 tests
Total: 100 passed, 0 failed
```

---

##### Documentation

`gateway/Documentation/Session_Manager_Technical_Documentation.md` created.

Documents: purpose, architecture, `SessionState` model, `LastReferenced`, `SessionOverrides`, `build_effective_context` behavior, persistence layer, API endpoints, deletion layering, logging/privacy policy, error handling, test coverage, and carry-forward notes.

---

##### Step 5 Carry-Forward Notes

| # | Note | Carry to |
|---|---|---|
| SM-CF-1 | UI must wire the three-dot "Delete session" action to `DELETE /students/{student_id}/sessions/{session_id}` | UI audit (Step 13) |
| SM-CF-2 | Developer cleanup endpoints (`/dev/...`) must stay disabled in production (`APP_ENV != dev`) | Deployment / Phase 5 |
| SM-CF-3 | Future authentication should bind session ownership to the authenticated user identity, not only the path `student_id` parameter | Auth / Phase 5 |
| SM-CF-4 | If storage migrates from SQLite to a production DB, the `SessionStore` abstract contract must be preserved; only `SQLiteSessionStore` is replaced | Phase 5 infrastructure |

---

**Step 5 result: PASS / LOCKED ✅**

---

#### Step 6 — Query Understanding Audit

Status:
COMPLETE ✅ — PASS / LOCKED WITH CARRY-FORWARD NOTES

Files audited:

* gateway/query_understanding.py
* gateway/qu_intents.py
* gateway/qu_preprocessing.py
* gateway/qu_prompt.py
* gateway/qu_llm_chain.py
* gateway/models/schemas.py only for StructuredQuery / EntitySet / SessionOverrides / LastReferenced
* gateway/session_manager.py and gateway/main.py only for QU context wiring
* tests/test_query_understanding.py
* scripts/one_query_qu_trial.py
* Query_Understanding_Technical_Documentation.md

1. QU Scope and Responsibility
* QU converts user_text to list[StructuredQuery].
* QU does not answer users.
* QU does not call ALE, RAG, Composer, Orchestrator, or KG business operations.
* QU may use KG only through injected resolve_entity resolver.
* QU never sends StudentContext, student_id, name, CGPA, grades, transcript, or course history to the LLM.

2. Locked Intent Taxonomy
* 26 locked intents validated.
* Forbidden stale intents rejected.
* plan_semester remains the active intent.
* plan_next_semester is forbidden/stale and must NOT be introduced.

3. Major Fixes / Audit Outcomes
* locked intent taxonomy and forbidden intent enforcement
* prompt boundary hardening
* clarification guard for role/track queries
* career gap vs focus courses vs gap-closing boundary
* direct vs full prerequisite depth extraction
* policy query rewriting
* compound query decomposition
* session override extraction and clear/reset behavior
* relative and explicit semester extraction
* expected grade and target GPA extraction
* entity resolution safety
* resolver absent / unresolved mention filtering
* LLM output validation and normalization
* deterministic fallback safety

4. Model Chain and Provider Findings
* Current demo chain:
  QU_PRIMARY_MODEL=llama-3.3-70b-versatile
  QU_FALLBACK_MODELS=openai/gpt-oss-120b,openai/gpt-oss-20b
  QU_TIMEOUT_SECONDS=30
* Removed old fallback defaults.
* GPT-OSS 120B and 20B are current fallbacks / production candidates.
* Llama was kept as demo primary.
* Manual trials hit Groq TPD on Llama.
* QU prompt is large, around 5.6K tokens per call observed from Groq 429 messages.
* Approx cost estimates and pricing/limits are documented in Query_Understanding_Technical_Documentation.md, not repeated fully here.
* Production improvement: deterministic-first router + prompt compression + paid/enterprise limits.
* Composer model-chain audit remains separate carry-forward.

5. Manual One-Query Trial Findings
* “i wanna be data scientist what am i missing” → compute_skill_gap.
* “what are the important courses for data scientist” → get_focus_courses_for_target.
* typo/personal focus query worked on GPT-OSS fallbacks.
* “compare ai and swe” → compare_tracks on GPT-OSS fallbacks.
* “what should i register next semester” → plan_semester with relative semester params.
* GPA simulation query worked and resolved Operating Systems to C-CS316.
* CGPA below 2 produced policy_query with self-contained rewrite.
* full prerequisite query worked on GPT-OSS 120B; GPT-OSS 20B had one JSON validation error.
* tuition query → out_of_scope.
* “what should I study to become a data scientist” returning recommend_courses_to_close_gap is accepted as better/more personal than get_focus_courses_for_target.

6. QU Logging
* QU.start
* QU.preprocess
* QU.model_chain
* QU model success/failure
* QU.resolve
* QU.resolve_failed
* QU.result
Logs are privacy-safe:
* no raw user_text
* no raw prompt
* no raw LLM output
* no recent_turn text
* no student PII

7. Tests
Command:
`python -m pytest tests/test_query_understanding.py -v --tb=short`

Result:
`119 passed in 0.28s`

* tests/test_query_understanding_behavior.py was intentionally deleted
* broad behavior testing deferred to Phase 2
* scripts/one_query_qu_trial.py kept as controlled manual diagnostic tool

8. Documentation
* Query_Understanding_Technical_Documentation.md created/updated
* includes model chain, pricing/limits, token budget, manual trials, logging, privacy, carry-forward notes

9. Step 6 Carry-Forward Register

| ID | Carry-Forward Note |
|---|---|
| QU-CF-1 | Orchestrator must consume depth for prerequisite queries. |
| QU-CF-2 | Orchestrator must consume target_semester_text and semester_resolution_source for relative semester planning. |
| QU-CF-3 | Orchestrator must consume explicit target_semester / target_semester_type. |
| QU-CF-4 | Orchestrator must use secondary_entities.track_id for compare_tracks. |
| QU-CF-5 | Orchestrator must respect student_referential_fallback when selecting context. |
| QU-CF-6 | Composer must distinguish get_focus_courses_for_target vs recommend_courses_to_close_gap in narration. |
| QU-CF-7 | Composer must narrate policy_query outputs concisely and avoid irrelevant fact dumps. |
| QU-CF-8 | Composer model-chain audit is still required; current Composer defaults may contain stale/deprecated/preview models. |
| QU-CF-9 | QU prompt is large; production needs prompt compression / deterministic-first routing. |
| QU-CF-10 | Provider pricing/limits/model availability must be rechecked before production. |
| QU-CF-11 | GPT-OSS 20B had one JSON validation issue; monitor during Phase 2. |
| QU-CF-12 | Broad live QU testing should not be repeated under current free/on-demand limits. |

10. Step 6 Final Result

Step 6 result: COMPLETE ✅ — PASS / QU LOCKED WITH CARRY-FORWARD NOTES

---

#### Step 7 — Orchestrator Audit

**Status:** COMPLETE ✅ — PASS / ORCHESTRATOR LOCKED

**Files:** `gateway/orchestrator.py`, `gateway/utils.py` (semester utilities)

**Scope and Responsibility:**
The Orchestrator is PathFinder's controlled execution layer between QU and Composer. It accepts `list[StructuredQuery]` from QU, dispatches each SQ to the correct engine (KG/RAG/ALE), and returns a `TurnWrapper` with one `PerSQResult` per SQ. It does not parse user text, generate responses, or persist session state.

**Major Fixes and Audit Outcomes:**
```
1.  All 26 locked intents route correctly — no unhandled or silently dropped intent.
2.  Forbidden/stale intents (14) explicitly rejected before routing.
3.  Context selection enforced per intent:
      run_graduation_audit        → base_context only (never effective_context)
      D1 academic planning        → effective_context
      D3 student-aware career     → effective_context
      D4 track (conditional)      → effective_context only when student_referential_fallback=True
      D2 course info, D5 policy   → no context (stateless)
4.  Rule bundle validation: required bundles checked before every ALE call;
    missing required bundle → structured engine_error, no silent default.
5.  KG enrichment fixed:
      required_zero_credit_courses supplied to ALE graduation audit and roadmap.
      course_credit_lookup built from KG for transcript codes — no fake default credits.
      Missing credits in transcript not silently defaulted (removed `or 3` sentinel).
6.  old_grade and improve_retake_number correctly populated for retake courses
    in simulate_gpa_forward and solve_target_gpa (ALE carry-forwards ALE-B/C closed).
7.  KG adapter errors distinguished from KG business not-found results.
8.  Unsupported-track planning and recommendation flows blocked safely.
9.  Graduated student early return for plan_semester and generate_graduation_roadmap.
10. Relative semester carry-forward closed:
      resolve_relative_semester_text() handles next/N/ordinal/word forms for 1–6 counts.
      Resolution uses ctx.current_semester, not machine date.
      ALE receives only resolved (season, year) fields — never raw natural-language phrases.
      Explicit target semesters (e.g., "Fall 2028") also handled.
11. Final logging pass: structured key=value format, privacy-safe (no PII, no grades,
    session_id truncated to 8 chars, raw semester text capped at 80 chars).
```

**Test Results:**
```
tests/test_orchestrator.py    103 passed, 0 failed
tests/test_utils.py            18 passed, 0 failed
Total                         121 passed, 0 failed
```

**Remaining Carry-Forwards (Deferred — Non-Blocking):**
```
ORC-CF-1  Full end-to-end chatbot behavior and intent-behavior matrix → Phase 2 integration
ORC-CF-2  Composer narration correctness for Orchestrator result shapes → Step 8 Composer audit
ORC-CF-3  API/UI integration testing → later phases
ORC-CF-4  improve_retake_number MVP approximation (first improve-retake treated as #1);
          precise count requires a future SCP per-course improve-retake field → SCP enhancement
ORC-CF-5  Turn-level cache is per-call, not per-session → Phase 5 optimization
```

**Documentation:** `gateway/Documentation/Orchestrator_Technical_Documentation.md`

Step 7 result: COMPLETE ✅ — PASS / ORCHESTRATOR LOCKED

---

#### Step 8 — Response Composer Audit

**Files:** `gateway/response_composer.py`

**Status: COMPLETE ✅ — PASS / RESPONSE COMPOSER LOCKED**

**Scope and responsibility:**

The Response Composer is PathFinder's student-facing narration layer. It receives a `TurnWrapper` (containing one `PerSQResult` per sub-query) and the original `user_text`, then returns a `QueryResponse` with a natural-language answer, citations, status, and LLM metadata. It does not call KG, RAG, ALE, QU, SCP, or Session Manager. It does not receive raw `StudentContext`. It does not mutate session state. It does not invent facts, course names, credits, eligibility decisions, or citations.

**Audit completed:**

```
Packet building:
- Every intent has a dedicated _extract_* extractor
- Packet contains only safe fields needed for narration; no raw StudentContext
- Missing fields handled gracefully (not crash, not hallucination)
- No raw PerSQResult dump passed to LLM

Controlled LLM narration:
- System prompt explicitly constrains LLM to packet facts only
- System prompt states: names first, codes in brackets
- System prompt states: no internal engine names
- Qwen <think> tag stripping applied
- Off-script detection applied (LLM asking student for info system already has)
- Fabricated-source stripping applied

Deterministic fallback:
- Triggered when LLM disabled, not configured, returns empty, goes off-script, or all models fail
- Fallback is intent-aware for all 26 locked intents
```

**Major audit fixes applied during Step 8:**

```
1. Display formatting: _fmt_course_label, _fmt_role_label, _fmt_skill_label,
   _fmt_track_label helpers added; _TRACK_DISPLAY_MAP for all 5 canonical track IDs;
   _safe_code_name changed to name-first "Name (CODE)" format

2. LLM model-chain cleanup: llama-3.3-70b-versatile removed from Composer fallbacks
   (QU uses it as primary; sharing on a single /chat request causes rate-limit contention);
   final chain: qwen/qwen3-32b → llama-3.1-8b-instant → openai/gpt-oss-20b

3. System prompt rules 26–28 added: name-first display, no raw RL_*/SK_* IDs,
   eligibility_status semantics, reset-assumptions wording

4. Eligibility wording fix: _extract_eligibility always maps ALE status → eligibility_status;
   _narrate_intent checks eligibility_status before eligible bool;
   in_progress → "already enrolled", already_completed → "already passed",
   retake_cap_exceeded → "retake cap reached"

5. Role/skill/track ID cleanup: all narration branches use _fmt_* helpers;
   no raw RL_* or SK_* IDs reach student-facing output

6. Plan/roadmap formatting: semester plan course_labels use "Name (CODE) — N credits" format

7. Credit-limit personalization: _personalize_credit_limit helper added;
   applied when get_student_record + policy_query are both in the same turn with
   CGPA evidence + credit-limit policy evidence;
   mapping: CGPA > 3.0 → 21h, 2.0–3.0 → 18h, 1.0–2.0 → 15h, < 1.0 → 12h

8. search_courses_by_skill: _extract_courses_by_skill now extracts skill_name (not raw IDs)

9. get_roles_by_track: _extract_roles_by_track now extracts track_name

10. LLM safety: <think> stripping, off-script fallback, fabricated-source stripping,
    empty-response fallback, all-models-failed fallback; all preserved and tested

11. Privacy-safe diagnostic logging: _duration_ms, _safe_session_id, _summarize_packets
    helpers; compose() start log (session 8-char, turn_status, result count);
    compose() result log (qr_status, llm_used, model, fallback_reason, answer_len,
    citations, duration_ms, packet_summary); LLM attempt logs per model tried;
    raw user text, full prompt, answer text, student ID, grades never logged

12. Citation handling: deduplication by (source, page) key; no invented citations;
    no Sources section if no upstream citations exist
```

**Test results:**

```
tests/test_response_composer.py    125 passed, 0 failed
```

No live LLM calls in tests. LLM fully mocked. Test categories: LLM success/disabled/failure paths, deterministic fallback, every PerSQResult status family, multi-SQ ordering, citation merging and deduplication, assumption/override notices, display formatting helpers, eligibility status semantics, role/skill/track ID cleanup, plan/roadmap name-first formatting, credit-limit personalization, logging privacy and diagnostic fields, no engine calls, no raw StudentContext leakage.

**Technical documentation:**

`gateway/Documentation/Response_Composer_Technical_Documentation.md`

**Remaining carry-forwards (non-blocking):**

```
COMP-CF-1  Deterministic reset-assumptions wording blocked until Orchestrator
           propagates assumptions_cleared=True into PerSQResult.data
           (Orchestrator intentionally not modified in Step 8)

COMP-CF-2  Full chatbot quality and intent-behavior correctness deferred to
           Phase 2 integration, E2E, and manual behavior testing

COMP-CF-3  Production model-chain hardening: replace qwen/qwen3-32b preview
           primary with openai/gpt-oss-20b before long-term deployment

COMP-CF-4  Rich citation excerpts not preserved in current Citation schema;
           source/page only (schema evolution if needed)
```

**Verdict:** Composer is component-locked. No P1 issues remain open. Ready for Step 9 API Gateway audit and later integration testing.

Step 8 result: COMPLETE ✅ — PASS / RESPONSE COMPOSER LOCKED

---

#### Step 9 — API Gateway Audit

**Files:** `main.py`

**Goal:** Ensure the API is a thin, clean interface with no business logic, and that all endpoints behave correctly.

**Issues found and fixed:**
```
1. No 503 readiness guard — if _orchestrator/_composer is None at request time,
   /chat crashed with AttributeError. Fixed: early guard returns 503 before any pipeline call.

2. No pipeline exception handling — unexpected errors exposed stack traces via FastAPI
   default 500. Fixed: try/except around QU→Orchestrator→Composer; returns safe HTTP 500
   with type(exc).__name__ logged only, no stack trace in response.

3. Student ID logged in clear — logged full student_id on both log lines.
   Fixed: _mask_student_id() truncates to first 3 chars + "***".
```

**Confirmed correct (no changes needed):**
```
- /chat → QU → Orchestrator → Composer order is correct
- answer_text from Composer stored in history (not stale placeholder)
- replace_overrides/had_clear wired correctly
- recent_turns passed to understand_query
- resolver injected from KG (disabled safely when KG unavailable)
- rule_bundles passed to execute_turn
- session endpoints: list, ownership-safe history, ownership-safe delete
- dev endpoints gated behind _is_dev_mode() correctly
- deprecated /session/{id}/history returns 410
- /health returns {"status": "ok", "service": "PathFinder"}
- No academic logic in main.py
```

**Tests:** 34 tests pass (30 existing + 4 new: 503 × 2, 500 on pipeline error, recent_turns)

**Documentation:** `gateway/Documentation/API_Gateway_Technical_Documentation.md`

Step 9 result: COMPLETE ✅ — PASS / API GATEWAY LOCKED

---

#### Step 10 — Streamlit UI Audit

**Files:** `ui/streamlit_app.py`

**Goal:** Ensure the UI is a minimal, correctly wired interface with no business logic.

**Issues found and fixed:**
```
1. CRITICAL: api_load_history called deprecated GET /session/{session_id}/history
   which returns 410 Gone — session history was completely broken.
   Fixed: now calls GET /students/{student_id}/sessions/{session_id}/history.

2. api_load_history missing student_id parameter — the correct endpoint requires
   both student_id and session_id for ownership verification.
   Fixed: function signature updated to api_load_history(student_id, session_id);
   call site updated to pass st.session_state.student_id.
```

**Confirmed correct (no changes needed):**
```
- PATHFINDER_API_URL env var used for API base
- POST /chat sends student_id, user_text, optional session_id
- Reads answer_text (not "answer") from QueryResponse
- session_id updated in state after first successful response
- session_name updated and session list refreshed
- No academic logic in UI
- Logout clears all state keys
- New Chat clears session_id/messages but keeps student_id
- Citations rendered correctly with page number when available
- Network errors shown as friendly messages in assistant bubble
- No raw JSON or traceback shown to user
```

**Demo usability added:**
```
Per-session delete button (🗑) added to sidebar session list.
- Layout: st.columns([0.85, 0.15]) — load button left, delete button right
- api_delete_session(student_id, session_id) calls DELETE /students/{sid}/sessions/{ses}
- _apply_delete_to_state pure helper updates all_sessions and clears active session fields
  if the deleted session was active
- On failure: st.warning shown, no crash
- Cross-student deletion prevented at API layer (ownership verified before delete)
```

**Tests:** 21 tests in tests/test_streamlit_app.py — all pass (13 original + 8 new for delete)

**Documentation:** `gateway/Documentation/Streamlit_UI_Technical_Documentation.md`

Step 10 result: COMPLETE ✅ — PASS / STREAMLIT UI LOCKED

---

#### Step 11 — Config / Startup / README Audit

**Files:** `.env.example`, `README.md`, `main.py`, `ui/streamlit_app.py`, `requirements.txt`, `pytest.ini`

**Goal:** Ensure the system can be set up and run reliably by someone following the documentation.

**Step 11 result: COMPLETE ✅ — PASS / CONFIG + STARTUP + README LOCKED**

**Issues found and fixed:**
```
1. README Architecture — deprecated GET /session/{session_id}/history listed as active → corrected
2. README Architecture — missing GET /students/.../history and DELETE /students/.../sessions endpoints → added
3. README KG section — "19 operations" → "18 operations" (matches KGAdapter dispatch table)
4. README Configuration — missing LLM_MODEL, LLM_TIMEOUT_SECONDS, QU_TIMEOUT_SECONDS,
   COMPOSER_TIMEOUT_SECONDS, APP_ENV, DEV_MODE → added
5. README Current Limitations — stale "delete_session not exposed" line → removed
6. README Setup — missing venv creation, .env copy step, browser URLs → added full sequence
7. .env.example — missing LLM_MODEL, LLM_TIMEOUT_SECONDS, APP_ENV, DEV_MODE → added
8. README missing sections — Recommended Model Configuration, Test Commands, Supervisor Demo → added
9. README Suggested Next Improvements — listed completed items → removed; updated with real carry-forwards
10. QU intents count — added "26 locked intents" to Architecture section
```

**Documentation created:**
```
gateway/Documentation/Config_Startup_README_Technical_Documentation.md
```

**Carry-forwards:**
```
CF-CONFIG-1  Composer reset-assumptions wording (needs assumptions_cleared signal)  Phase 1.5
CF-CONFIG-2  LangChain Chroma deprecation warning                                  Phase 5
CF-CONFIG-3  Per-component health checks                                            Post-Phase 2
CF-CONFIG-4  qwen/qwen3-32b preview model — production Composer model decision     Phase 2
CF-CONFIG-5  Startup cold time ~110 s (RAG model loading)                          Phase 5
```

---

#### Step 11 original audit scope

**Audit:**
```
Environment variables:
- All required variables documented in _env.example
- No undocumented required variables exist
- Default values are safe where applicable
- API keys and secrets are not hardcoded anywhere

Startup commands:
- Backend command documented and correct
- UI command documented and correct
- Neo4j startup documented

RAG setup:
- Ingest steps documented
- How to rebuild the index from scratch is clear
- Which handbook file is ingested is documented

Known limitations:
- Known issues documented honestly
- Recovery steps if LLM provider fails documented

README accuracy:
- README reflects current state (not outdated architecture)
- Dependencies are listed correctly
- Python version requirements stated
```

---

#### Step 12 — Final Phase 1 Review and Closure

**Status: COMPLETE ✅ — PASS / PHASE 1 COMPONENT AUDIT LOCKED**

**Documentation:** `gateway/Documentation/Phase_1_Final_Review.md`

---

##### Phase 1 Final Summary

**Overall result: COMPLETE ✅ — PASS / COMPONENTS LOCKED**

**What Phase 1 achieved:**
- All 14 components audited in isolation (engines, adapters, gateway, UI, config)
- Engine/adapter boundaries clarified and enforced (KG, RAG, ALE each responsible for data/decisions only)
- Intent taxonomy locked: 26 intents, `plan_semester` active, `plan_next_semester` forbidden
- Orchestrator routing locked
- Composer narration locked (name-first display, no raw IDs, in_progress wording, fallback chain)
- API and UI confirmed as thin surfaces with no business logic
- Config/startup/README locked and accurate
- Safe, structured logging added across all components (no PII, no raw transcripts)
- Component-level tests added and passed across all steps

**What Phase 1 did NOT validate:**
- Full E2E chatbot behavior (deferred to Phase 2)
- Intent-by-intent behavioral matrix (deferred to Phase 2)
- UI regression testing in a browser (not run)
- Live supervisor demo run (deferred to Phase 6)
- Production deployment readiness (deferred to Phase 5/6)

---

##### Phase 1 Component Status Table

| Step | Component | Status | Tests |
|------|-----------|--------|-------|
| 0 | Shared Contracts / Schemas | COMPLETE ✅ | — (covered per step) |
| 1 | KG Engine + KGAdapter | COMPLETE ✅ LOCKED | 246 passed |
| 2 | RAG Engine + RAGAdapter | COMPLETE ✅ LOCKED | 43 passed |
| 3 | ALE Engine + ALEAdapter | COMPLETE ✅ LOCKED | 358 passed, 5 skipped |
| 4 | Student Context Provider | COMPLETE ✅ LOCKED | 90 passed |
| 5 | Session Manager | COMPLETE ✅ LOCKED | 100 passed (incl. test_main.py 30) |
| 6 | Query Understanding | COMPLETE ✅ LOCKED | 119 passed |
| 7 | Orchestrator | COMPLETE ✅ LOCKED | 121 passed |
| 8 | Response Composer | COMPLETE ✅ LOCKED | 125 passed |
| 9 | API Gateway | COMPLETE ✅ LOCKED | 34 passed (test_main.py) |
| 10 | Streamlit UI | COMPLETE ✅ LOCKED | 21 passed |
| 11 | Config / Startup / README | COMPLETE ✅ LOCKED | 34 passed (test_main.py) |

---

##### Phase 1 Documentation Produced

| Document | Location |
|----------|----------|
| KG Technical Documentation | `engines/kg/KG_TECHNICAL_DOCUMENTATION.md` |
| RAG Technical Documentation | `engines/rag/RAG_TECHNICAL_DOCUMENTATION.md` |
| ALE Technical Documentation | `engines/ale/ALE_TECHNICAL_DOCUMENTATION.md` |
| Student Context Provider Technical Documentation | `gateway/Documentation/Student_Context_Provider_Technical_Documentation.md` |
| Session Manager Technical Documentation | `gateway/Documentation/Session_Manager_Technical_Documentation.md` |
| Query Understanding Technical Documentation | `gateway/Documentation/Query_Understanding_Technical_Documentation.md` |
| Orchestrator Technical Documentation | `gateway/Documentation/Orchestrator_Technical_Documentation.md` |
| Response Composer Technical Documentation | `gateway/Documentation/Response_Composer_Technical_Documentation.md` |
| API Gateway Technical Documentation | `gateway/Documentation/API_Gateway_Technical_Documentation.md` |
| Streamlit UI Technical Documentation | `gateway/Documentation/Streamlit_UI_Technical_Documentation.md` |
| Config / Startup / README Technical Documentation | `gateway/Documentation/Config_Startup_README_Technical_Documentation.md` |
| Phase 1 Final Review | `gateway/Documentation/Phase_1_Final_Review.md` |

---

##### Phase 1 Consolidated Carry-Forward Register

Items are grouped by the phase where they will be addressed.

**Phase 1.5 — Integration Readiness Check**
- COMP-CF-1: Composer deterministic reset-assumptions wording blocked until Orchestrator propagates `assumptions_cleared=True` signal into `PerSQResult.data`.

**Phase 2 — Integration & Behavioral Testing**
- Full E2E chatbot behavior validation deferred to Phase 2.
- Intent-by-intent behavioral matrix (`PathFinder_Phase1_Intent_Behavior_Matrix.md`) available as reference artifact; not an active Phase 1 work item.
- Phase 0 P1/P2 issues (plan_semester real output, compare_tracks routing, eligibility in_progress narration, reset-assumptions wording) must be verified closed in Phase 2 against live behavior.

**Phase 3 — Chatbot Experience**
- UI status display can visually distinguish `ok`, `clarification_needed`, `error` response states (currently text-only).

**Phase 5 — Performance and Production Readiness**
- Startup cold time ~108–110 seconds due to RAG/KG/rule-bundle loading; optimize in Phase 5.
- LangChain Chroma deprecation warning; migrate to `langchain_chroma` in Phase 5.
- Production authentication/security is out of scope for current demo UI; address in Phase 5.
- RAG still uses direct Groq requests, not shared LLMClient; acceptable for now.

**Post-Phase 2 / Post-Demo**
- `/health` can expose per-component readiness status later.
- `qwen/qwen3-32b` is a preview model; production Composer primary model selection to be finalized before long-term deployment.
- Richer citation schema/excerpts (currently source/page only) optional for later.
- KG data does not support required/elective course labeling; recorded as a data limitation, not a blocker.
- OP10 planned-course source resolution (Orchestrator must resolve before calling KG).
- OP17 in-progress course handling decision (exclude vs. include based on query wording).

---

##### P1 Blockers: None

No P1 blockers were found at Phase 1 closure. All P1 issues discovered during component audits were fixed within their respective steps. All deferred items are P2/P3 with documented justification.

---

**Step 12 result: COMPLETE ✅ — PASS / PHASE 1 COMPONENT AUDIT LOCKED**

---

### Phase 1 Outputs

```
✅ Component Audit Report per component (Step 12 + per-step sections)
✅ Deficiency Register (P1 fixed, P2/P3 deferred with justification — Step 12 carry-forward register)
✅ Component-level test results (documented per step; Step 12 summary table)
✅ Foundational P1 fixes applied (all P1 issues resolved within step scope)
✅ Component-level safe logging added to all components
✅ Updated contracts/schemas where needed
✅ Updated README/config notes where needed
✅ Decision record: plan_semester behavior restriction (plan_next_semester forbidden; locked in Step 6)
✅ Technical documentation produced for all 11 components (gateway/Documentation/ + engine folders)
✅ Phase 1 Final Review document created (gateway/Documentation/Phase_1_Final_Review.md)
```

### Phase 1 Done-Enough Bar

```
Every component/engine/adapter/interface has been audited.
Component responsibilities and boundaries are clear and documented.
P1 component-scope issues are fixed or explicitly deferred with strong justification.
Component-level tests are recorded.
Component-level logs are sufficient for debugging individual component failures.
No obvious contract mismatch remains within component scope.
System is ready for Phase 1.5 Integration Readiness Check.
```

---

## Phase 1.5 — Integration Readiness Check

**Status: COMPLETE ✅ — PASS / INTEGRATION CONTRACTS LOCKED**

### Goal

Verify that audited components can connect correctly at their contract boundaries before end-to-end behavioral testing begins.

This phase is **contract-scope only**.

### Contracts Verified

```
Contract 1: QU StructuredQuery              → Orchestrator input shape         PASS ✅
Contract 2: Orchestrator PerSQResult        → Composer input shape              PASS ✅
Contract 3: Orchestrator → KGAdapter        → 18 operations, param/result shapes PASS ✅
Contract 4: Orchestrator → RAGAdapter       → execute() / get_rule_bundles()    PASS ✅
Contract 5: Orchestrator → ALEAdapter       → 6 operations, input shapes        PASS ✅
Contract 6: Session state                   → QU context / Orch effective_ctx   PASS ✅
Contract 7: API /chat endpoint              → full pipeline request/response     PASS ✅
Contract 8: Composer final response         → QueryResponse API shape / UI       PASS ✅
Contract 9: Reset-assumptions signal        → assumptions_cleared structured flag FIXED ✅
```

### Mismatches Found and Fixed

**Contract 9 — Reset-assumptions structured signal (COMP-CF-1)**

- **Root cause:** Orchestrator `execute_turn` computed `had_clear` but did not pass it into `_execute_sq` or `_exec_student_record`. No structured flag reached the Composer.
- **Fix:** Added `had_clear` parameter through `_execute_sq` → `_exec_student_record`. When `had_clear=True`, Orchestrator now sets `data["assumptions_cleared"]=True` and the standard message in the `get_student_record` result. Composer `_extract_student_record` and `_narrate_intent` updated to detect and render the standard wording.
- **Files changed:** `gateway/orchestrator.py`, `gateway/response_composer.py`

### Tests

```
tests/test_integration_contracts.py   50 tests — 50 passed ✅
tests/test_orchestrator.py           103 tests — 103 passed ✅
tests/test_response_composer.py      118 tests — 118 passed ✅
tests/test_main.py                    34 tests  —  34 passed ✅

Total: 305 tests — 305 passed, 0 failed
```

### Phase 1.5 Outputs

```
✅ Integration Contract Checklist (gateway/Documentation/Phase_1_5_Integration_Readiness_Check.md)
✅ Contract 9 mismatch fixed (orchestrator.py + response_composer.py)
✅ New test file: tests/test_integration_contracts.py (50 tests)
✅ Execution plan updated
✅ All carry-forwards from Phase 1 resolved
```

### Done-Enough Bar

> No known P1 contract mismatch remains before Phase 2 begins. ✅

---

## Phase 2 — Integration & Behavioral Testing

### Status: PARTIAL COMPLETE ✅ — Behavioral Stabilization Done (2026-06-25)

D6 student-record enrichment fixed and verified. Behavioral stabilization completed 2026-06-25; session-memory patch applied and rolled back as unstable; all behavioral fixes preserved. 1037 tests pass (1 pre-existing RAG failure). Full intent-by-intent integration testing and chatbot intelligence upgrade continue in the Last Battle Plan.

### Goal

Test PathFinder as a complete academic advising chatbot after all components have been individually audited and stabilized.

Phase 2 is **integration-scope and end-to-end-scope**. This is where intent-by-intent and domain-by-domain testing belongs.

---

### What Phase 2 Tests

```
Intent-by-intent behavior across all 26 locked intents
Domain-by-domain behavior (D1 Academic Planning, D2 Course Info,
  D3 Career/Role, D4 Track Guidance, D5 Policy, D6 Student Record)
Multi-turn session behavior
Compound queries (multiple SQs from one message)
Session continuity and assumption accumulation/reset
Policy and RAG factuality verification
Career and track guidance correctness
API endpoint behavior
Streamlit UI behavior
Regression of all Phase 0 P1/P2 issues
```

### Intent Behavior Matrix

The Intent Behavior Matrix (`PathFinder_Phase1_Intent_Behavior_Matrix.md`) is a parked reference artifact. It is available for use as a reference during Phase 2 behavioral testing to define expected student-facing behavior per intent.

It is **not an active Phase 1 work item**. Do not continue or expand it during Phase 1.

### Phase 2 Testing Order

```
D6 — Student Record (foundation)
D2 — Course Info (simple KG, entity resolution baseline)
D1 — Academic Planning (highest risk, Phase 0 P1 issues)
D3 — Career / Role Guidance
D4 — Track Guidance (Phase 0 P1 issue: compare_tracks)
D5 — Policy / RAG
Control — out_of_scope, clarification_needed
Multi-SQ compound queries
Full regression sweep through Streamlit
```

### Phase 0 Issues That Must Be Verified Closed in Phase 2

```
[P1] plan_semester → must produce a real plan or a correct, specific reason
[P1] compare_tracks → one SQ with two tracks, no clarification asked
[P1/P2] check_course_eligibility in_progress → "already enrolled", not "missing prerequisites"
[P2] Composer entity display → names first, codes in brackets, no raw IDs
[P2] Reset assumptions wording → "cleared assumptions", not "updated your record"
[P2] Policy factuality → handbook claims verified against actual handbook content
```

### Phase 2 Outputs

```
☐ Intent/domain test reports
☐ End-to-end deficiency list
☐ Integration fixes applied
☐ Regression test set (saved list of queries that must pass going forward)
☐ Supervisor demo query candidates
☐ Confirmed closure or explicit documentation of all Phase 0 P1/P2 issues
```

### Phase 2 Done-Enough Bar

```
All 26 supported intents pass at least one realistic end-to-end test.
Core student-aware intents pass multiple realistic test scenarios.
All Phase 0 P1 issues are fixed and verified through Streamlit.
All Phase 0 P2 issues are fixed or explicitly documented with justification.
Regression test set passes end-to-end.
```

---

### Phase 2 D6 Defect Log

| ID | Description | Status | Tests |
|----|-------------|--------|-------|
| D6-BUG-1 | Student-record course lists returned raw codes only (`C-CS112`, `HUM111` etc.) — no course names. Observed in manual test with STU000031. | **FIXED 2026-06-24** | 27 tests in `test_phase2_d6_student_record.py` + 10 in `test_orchestrator.py` (D6 enrichment) + 12 in `test_response_composer.py` (D6 rendering) |

**Fix summary (D6-BUG-1):**

- `gateway/orchestrator.py`: Added `_enrich_course_details()` method that calls `get_course_profile` per course code using `caches.course_profile_cache`. Updated `_exec_student_record` to populate `completed_course_details`, `in_progress_course_details`, `failed_course_details` — each item `{course_code, course_name, credits}`. Raw code lists kept for backward compat. KG failure → fallback with `course_name=None, credits=None`; never fails the student record result.
- `gateway/response_composer.py`: Added `_render_course_detail()` helper. Updated `_extract_student_record` to forward the three detail fields. Updated `_narrate_intent` for `get_student_record` to prefer `*_course_details` over raw codes, rendering each as `"Course Name (COURSE_CODE)"` (name-first, per rule 26). Fallback to raw codes if details absent.

**Post-fix behavior (STU000031):**

```
Completed courses (5):
  • Humanities I (HUM111)
  • Physics I (C-PH111)
  • Introduction to Computer Science (C-CS111)
  • Calculus I (C-MA111)
  • Technical Writing (HUM126)

In-progress courses (5):
  • Programming Fundamentals (C-CS112)
  • Calculus II (C-MA112)
  • Advanced Physics (C-PH112)
  • Digital Logic Design (C-PH113)
  • Introduction to Management (HUM224)
```

D6 manual testing may resume.

---

## PathFinder — The Last Battle Plan

### 26 June → 30 June 2026

#### Mission

Finish PathFinder as a complete, demo-ready, deployable academic and career advising product by the end of 30 June.

The goal is a system that is not only technically working, but feels like a real academic and career advisor: useful, intelligent, stable, explainable, and ready to be shown in discussion and at the CIS GP Fair.

---

### Day 1 — Friday, 26 June

#### LLM Provider Fix + Full Intent-by-Intent Integration Testing

**Due:** End of 26 June

**Main goal:** Fix the LLM provider issue, then complete full end-to-end testing and fixing for every locked chatbot intent.

**A. LLM provider issue**

- Fix the current QU/Composer provider problem.
- Decide whether to continue with Groq or switch to Gemini/OpenAI-compatible provider.
- Ensure QU can run without repeated 429 rate-limit failures, 413 payload/TPM failures, or invalid model failures.
- Preserve existing Groq compatibility even if Gemini is added.
- Restart backend and confirm normal chatbot queries work again.

**B. Full intent-by-intent integration testing**

Test and fix all chatbot domains end-to-end:

1. **Student Record** — current level, CGPA, academic standing, completed courses, current courses, failed courses, course status checks, assumptions/reset assumptions.
2. **Course Info** — course profile, prerequisites, full prerequisite chain, skills taught, courses by skill.
3. **Policy / RAG** — attendance, grading scale, honors, graduation requirements, retake policy, warning/CGPA below 2, credit limits.
4. **Career / Role** — role profile, roles by track, skill gap, alignment score, best matching roles, gap-closing courses, alignment improvement, focus courses.
5. **Track Guidance** — track overview, compare tracks, recommend track for role, recommend track for skill.
6. **Academic Planning** — course eligibility, semester plan, graduation audit, graduation roadmap, GPA simulation, target GPA solving.
7. **Control** — clarification_needed, out_of_scope.

**Fixing rule:** Every failed query must be classified by component (QU / KG / Resolver / Orchestrator / ALE / RAG / Composer / Session / Data limitation / UI-API), then fixed only where needed for correct end-to-end behavior.

**Exit condition:** All chatbot intents tested end-to-end at least once; no major logical errors remain in the core academic advising behavior.

---

### Day 2 — Saturday, 27 June

#### Retesting + Real Chatbot Intelligence Upgrade

**Due:** End of 27 June

**Main goal:** Confirm the full intent system is still correct after Day 1 fixes. Then upgrade the chatbot experience so PathFinder feels intelligent, flexible, and conversational — not like a rigid intent demo.

**A. Integration retesting**

- Retest all fixes from Day 1. Confirm every domain still works: Academic Planning, Course Info, Career/Role, Track Guidance, Policy, Student Record, Control intents.
- Confirm the most important demo paths are stable:
  - "What is my CGPA?"
  - "Can I take X?"
  - "Plan next semester for me."
  - "Can I graduate?"
  - "I want to become a Data Scientist. What am I missing?"
  - "Compare AI and Data Science."
  - "What is the attendance policy?"

**Exit condition for Part A:** System is not only patched, but retested and confirmed stable after fixes.

**B. Real chatbot intelligence upgrade**

**Objective:** Make PathFinder behave like a real chatbot. Students should not feel that the system only works when they use exact intent phrasing. If the student asks naturally, vaguely, or slightly outside the expected wording, PathFinder should still respond usefully when the request is related to academic advising, curriculum, career guidance, or student progress.

What must be improved:

1. **QU flexibility** — Upgrade Query Understanding to map more natural student phrasing into the correct intents. Focus on: alternative wording, casual phrasing, incomplete-but-understandable questions, multi-intent questions, follow-up style questions, vague but in-domain questions, role/course/track ambiguity, policy + student-context questions, course + eligibility combined, career + course recommendation combined.

   Examples:
   - "Am I cooked academically?" → academic standing / warning, not out_of_scope.
   - "What should I do if I want data science?" → career/role guidance, skill gap, or track/role recommendation depending on context.
   - "Is this course useful for AI?" → use last course if available; answer through skills/role/track relevance.
   - "I'm lost, what should I take?" → planning/advising, not generic clarification.
   - "How do I improve my situation?" → academic standing + planning/target GPA if student context supports it.
   - "What does this course open for me?" → course skills and related career/role usefulness.

2. **Multi-intent handling** — Improve cases where one user question should produce more than one StructuredQuery.
   - "Can I take Computer Vision and what are its prerequisites?" → get_course_prerequisites + check_course_eligibility.
   - "Can I graduate and what is left?" → run_graduation_audit + possibly generate_graduation_roadmap.
   - "I want to become a Data Scientist, what am I missing and what courses should I take?" → compute_skill_gap + recommend_courses_to_close_gap.
   - "Tell me about AI track and what roles it leads to." → get_track_overview + get_roles_by_track.

3. **Better clarification behavior** — Use targeted clarification only when the system genuinely cannot choose safely. Avoid overusing generic clarification.
   - Good: "Do you mean Software Engineering as a course, a career role, or the Software Engineering track?"
   - Bad: "Could you clarify?"

4. **Better Composer behavior** — Answer directly first. Explain briefly. Avoid robotic phrasing and info dumps. Use course names first, avoid raw role/skill IDs. Avoid fake rules. Connect answers to the student's situation when appropriate. Make limitations sound professional, not broken.

5. **Better graceful handling for in-domain unsupported questions** — If the student asks something related to advising but not covered by a direct intent, still be useful: suggest what PathFinder can check, offer nearby supported options, ask a targeted clarification, or explain the available next step. The student should not feel "this chatbot is dumb" just because the wording was different.

6. **Session behavior where it improves the experience** — Support simple references: "it", "that course", "my track", "this role", "can I take it?", "what about this one?", "tell me more about it." Keep practical and demo-safe; no heavy session-memory rebuild unless necessary.

**Exit condition for Part B:** PathFinder feels like a real academic advising chatbot, not a rigid intent classifier. It handles natural phrasing, reasonable ambiguity, multi-intent questions, and helpful fallback behavior without losing academic correctness.

---

### Day 3 — Sunday, 28 June

#### SAE + Real UI Integration

**Due:** End of 28 June

**Main goal:** Complete new integrations and make the current product state feel flawless.

**A. Real UI integration**

Integrate the real UI with the backend. The UI must support:
- Student login / student selection.
- Chat view and response rendering.
- Citation rendering.
- Chat history or basic session continuity.
- Loading states and graceful error messages.
- Clean navigation.

The UI should no longer feel like a temporary engineering test interface.

**B. SAE integration**

Integrate the Student Analytics Engine as a separate dashboard flow. SAE must not overload the normal chatbot pipeline.

Expected flow: dashboard action from UI → SAE receives needed student/curriculum data → SAE returns structured analytics → UI renders visualizations/cards/charts.

Possible dashboard views: academic progress overview, credit accumulation, completed vs. remaining requirements, risk indicators, graduation timeline forecast, GPA/standing analytics, course progress insights.

**C. Product coherence**

Make sure Chatbot + SAE + UI feel like one product:
- Clear navigation and stable backend communication.
- Consistent student identity/session handling.
- No broken screens. No raw internal JSON shown to normal users.
- Graceful handling if one module is unavailable.

**Exit condition:** By end of Day 3, the integrated product works: chatbot + real UI + SAE dashboard path are connected and demoable.

---

### Day 4 — Monday, 29 June

#### Advanced Auditability + Deployment / Scaling Start

**Due:** End of 29 June

**Main goal:** Use the first half to harden auditability, logging, and explainability. Use the second half to design and begin actual deployment/scaling work.

**First Half: Advanced auditing, logging, and explainability**

Focus on:
- QU selected intent(s) and extracted/resolved entities.
- Routing decision, engine called, engine status, result status, Composer status.
- RAG citations, ALE reason codes.
- Data limitation vs. system failure.
- Safe logs with no sensitive student data leakage.

Also prepare explanation material for technical discussion: why the system is decoupled, why KG/RAG/ALE/SAE are separated, why QU and Composer are LLM stages, why academic decisions are deterministic, how hallucination risk is reduced, how the system can be audited.

**Exit condition for first half:** System is explainable and diagnosable enough for technical discussion.

**Second Half: Deployment and scaling start**

Deployment planning must cover: backend hosting, frontend hosting, Neo4j hosting/access, RAG artifacts, dataset access, API keys and environment variables, CORS, startup time, persistent storage, session storage, logs, fallback local demo.

Scaling/technology review must include: whether SQLite is acceptable for demo only, whether PostgreSQL should replace SQLite later, whether Neo4j Aura or hosted Neo4j is needed, whether RAG artifacts should be bundled or rebuilt, whether LLM provider should be Gemini/Groq/other, cost/rate-limit risks, deployment limitations.

**Exit condition for second half:** By end of Day 4, deployment has started, blockers are known, and the scaling strategy is clear.

---

### Day 5 — Tuesday, 30 June

#### Deployment Continuation, Final Verification, Freeze

**Due:** End of 30 June

**Main goal:** Continue deployment, verify the product works correctly, and freeze the final version.

**A. Deployment continuation**

- Continue actual deployment from Day 4.
- Verify: backend health, frontend can call backend, KG/RAG/data access, environment variables, LLM provider, session storage, citations and UI rendering.
- If deployment succeeds: test full demo path on deployed version; document deployed URLs and environment requirements.
- If deployment does not fully succeed: document exact blocker; prepare guaranteed local demo; prepare recorded demo and screenshots; keep deployment roadmap ready for discussion.

**B. Final product verification**

Run the final demo regression: student record, course info, policy, career guidance, track guidance, academic planning, SAE dashboard, real UI navigation, error handling.

**C. Freeze**

After final verification: no new features, no broad refactors, only emergency blocker fixes. Mark final demo version. Prepare final README/run steps and known limitations list.

**Exit condition:** By end of Day 5, PathFinder is ready to be shown live, deployed if possible, run locally as fallback, recorded, explained technically, defended in discussion, and presented at the CIS GP Fair.

---

### Side Tasks Outside This Plan

- Architecture explanation for thesis.
- Final intent list for supervisor/thesis.
- Feature framing for documentation.
- Thesis writing.
- Banner, commercial video, recorded demo editing, slides.

These are important but parallel. They should not interrupt the engineering plan unless a supervisor deadline requires them immediately.

---

### Priority Rules

**Highest priority:**
1. LLM provider works.
2. All intents work correctly end-to-end.
3. Chatbot feels intelligent and seamless.
4. SAE and real UI are integrated.
5. Advanced logs/auditability support technical defense.
6. Real deployment is attempted and completed if possible.
7. Local fallback is guaranteed.

**If time becomes tight:** Deployment is higher priority than perfect advanced logging.

**Can be simplified:** Advanced session memory, perfect scalability, complex UI animations, optional analytics views, deep production monitoring, non-demo edge cases.

**Cannot be simplified:** Academic correctness, no fake rules, stable core intents, real UI working, SAE path working, deployment attempt, local fallback, clear README/run steps.

**Final timeline summary:**
- 26 June: Fix LLM provider, then full intent-by-intent integration testing and fixing.
- 27 June: Retest fixes, then upgrade chatbot intelligence and natural conversation behavior.
- 28 June: Integrate SAE and real UI, making the product feel complete.
- 29 June: Advanced auditability/logging in the first half; deployment and scaling start in the second half.
- 30 June: Continue deployment, verify everything, freeze final version.

---

> **Note:** Sections below (Phase 3 through Phase 6) were the original planned phases before the Last Battle Plan was established. Their technical details are preserved for reference. The active remaining execution schedule is the Last Battle Plan above.

---

## Phase 3 — Real Chatbot Behavior and Seamless Experience

> **Status: SUPERSEDED — Absorbed into Last Battle Plan Days 1–2.** Goals from this section are addressed in Day 1 (LLM fix + intent-by-intent integration testing) and Day 2 (chatbot intelligence upgrade). Technical details below are preserved for reference.

### Goal

Make PathFinder feel like a real chatbot, not a rigid intent router. The system should always try to give the most useful supported answer possible, even when the student does not phrase the query perfectly.

### Cases the System Must Handle Well

```
Vague queries ("what should I do?")
Messy student language ("wanna know if i can take that physics thing")
Follow-up questions referencing prior context
Partially out-of-scope questions (answer the supported part)
Queries near a supported intent (user means something mappable)
Multi-step questions
Relative semester language: "next next fall", "two falls from now", "my 7th semester"
Unknown entity recovery (suggest closest match or ask specifically)
Natural response continuity across multi-turn sessions
```

### What to Improve

**Helpful fallback instead of dead-end out-of-scope:**
```
Bad:    "This is out of scope."
Better: "PathFinder helps with academic planning, courses, graduation, and career guidance.
         I can't help with tuition, but I can check your standing or plan your next semester —
         would either of those be useful?"
```

**Intent-near recovery:**
```
"How do I become a machine learning engineer?"
→ decompose into: get_role_profile + compute_skill_gap + recommend_courses_to_close_gap
```

**Clarification only when truly necessary:**
```
Clarification valid:    "I want data." (ambiguous: track? skill? role?)
Clarification not needed: "I want to become a data scientist." (clear enough — proceed)
```

**Relative semester language:**
```
Student in Fall 2026:
  "next next fall"          → Fall 2028
  "two falls from now"      → Fall 2028
  "my 7th semester"         → resolve from current level + expected progression
  "in three semesters"      → resolved from current semester + 3

Always resolved from student's current_semester (StudentContext), not system calendar date.
```

**Follow-up continuity:**
```
"Tell me about Advanced Physics."
"Can I take it?"
"What if I pass Elementary Physics?"
"Now plan my semester."
"Reset assumptions."
"Can I take it now?"
→ Each turn resolves correctly from session last_referenced and overrides.
```

### Phase 3 Done-Enough Bar

> The system usually gives the most useful supported answer possible, even when the student phrases the query imperfectly.

---

## Phase 4 — Advanced Logging, Tracing, Auditability, and Logic Control

> **Status: SUPERSEDED — Absorbed into Last Battle Plan Day 4, First Half.** Advanced auditability, logging, and explainability work is addressed in Day 4 First Half of the Last Battle Plan. Technical details below are preserved for reference.

### Goal

Upgrade from basic component-level logs (Phase 1) to full request-level traceability across the entire pipeline.

> **Important:** Basic component-level logging starts in Phase 1. Phase 4 is for advanced structured logging, cross-component tracing, and auditability features.

### What Logs Must Tell Us Per Request

```
Which intent was classified?
Which entities were resolved, from what raw mention?
Was student context used, and which type (base / effective / none)?
Which engine was called, with what input summary?
What did the engine return (status, reason_codes)?
What did Composer receive and what did it narrate?
What was the final answer status?
How long did each stage take?
```

### Focus Areas

```
Structured log format (JSON or structured key-value)
Request-level trace ID across all components
Latency per component (ms)
Decision summaries (safe, no PII)
Error categorization by component and type
Privacy-safe audit trail design
Future admin/audit dashboard direction
Observability roadmap (OpenTelemetry, dashboards)
```

### Privacy Rules

```
Never log: student name, national ID, full transcript, raw grade list
Never log: full StudentContext dump
Never log: student ID in production/public logs
Never log: full LLM prompt with sensitive context
Use: session_id and anonymized counts for tracing
```

### Phase 4 Done-Enough Bar

> When a wrong answer happens, we can determine exactly which component caused it — without guessing.

---

## Phase 5 — Scaling, Maintainability, and Production Readiness

> **Status: SUPERSEDED — Absorbed into Last Battle Plan Days 4–5.** Scaling/technology review is in Day 4 Second Half; production concerns and deployment are in Days 4–5 of the Last Battle Plan. Technical details below are preserved for reference.

### Goal

Find and fix the things that will become problems if EUI actually uses PathFinder at university scale.

---

### 1. Database and Persistence

**Current concern:** SQLite is acceptable for local demo, not for real university usage.

**Production direction:**
```
- PostgreSQL for sessions, audit logs, users, feedback, and admin data
- Neo4j remains for the Knowledge Graph
- Object or file storage for uploaded documents or generated reports if needed
```

---

### 2. Authentication and Authorization

```
- Student login / authenticated session
- Advisor / admin login
- Role-based access control
- Student can only access their own record
- Advisor can access assigned students
- Admin can manage data and run operations
```

---

### 3. Data Update Workflow

```
- Student record refresh each new semester
- Course catalogue updates
- Handbook updates
- Entity alias updates
- Role / skill mapping updates
- RAG re-ingestion workflow
- KG import workflow
- Validation step before publishing updated data
```

---

### 4. Performance

**Check:**
```
- Startup time — currently ~108 seconds (P3 from Phase 0)
- RAG model loading time
- KG connection time
- LLM timeout behavior
- RAG bundle extraction time
- Query latency by domain
- Cache effectiveness
```

**Improve:**
```
- Cache rule bundles at startup (not per-request)
- Cache course profiles per turn
- Cache courses-by-track per turn
- Lazy-load heavy RAG assets if possible
- Neo4j connection pooling
- Explicit timeout and retry policies
- Migrate from old Chroma import to langchain_chroma (deprecation from Phase 0)
```

---

### 5. Failure Handling and Graceful Degradation

The system must not crash because:
```
- LLM provider (Groq or otherwise) is down or rate-limited
- Neo4j is unavailable
- RAG index is missing
- One rule bundle failed to extract
- One course code is not found in KG
```

Every engine must have a clearly documented degradation behavior.

---

### 6. Admin Maintainability

Future admin operations that must be possible:
```
- Update the handbook and rerun RAG ingest
- Update the course catalogue
- Update entity aliases
- Inspect failed or incorrect queries
- View system analytics
- Correct bad role/skill mappings
- Export logs
```

---

### 7. SAE Integration Readiness

Prepare clean extension points for the Student Analysis Engine:
```
- New adapter following the same adapter contract pattern
- New intents registered in QU and Orchestrator if needed
- New API response fields if needed
- Composer packet extension for new intent types
- UI panels/cards for new result types
```

### Phase 5 Done-Enough Bar

> The system remains an MVP, but we know exactly what must change before real university production and have already fixed the most dangerous blocking issues: performance, failure handling, and data lifecycle.

---

## Phase 6 — Deployment Strategy and Execution

> **Status: SUPERSEDED — Absorbed into Last Battle Plan Days 4–5.** Deployment start is in Day 4 Second Half; deployment continuation, final verification, and freeze are in Day 5 of the Last Battle Plan. Technical details below are preserved for reference.

### Goal

Prepare two deployment tracks:
```
1. Supervisor / demo deployment — urgent, needed before presentation
2. Future real university production deployment — roadmap, not full execution yet
```

---

### 6A — Demo Deployment

**Required:**
```
- Clean, readable README
- .env.example with all required variables documented
- Backend run command
- UI run command
- Neo4j startup steps
- RAG ingest steps (how to rebuild index from scratch)
- Test student IDs for demo
- Demo query script (curated questions showing the system well)
- Known limitations documented honestly
- Recovery steps if LLM API fails during demo
```

**Demo environment:**
```
- FastAPI running locally
- Streamlit running locally
- Neo4j running locally
- RAG index prebuilt before demo
- SQLite acceptable for session storage at demo scale
```

**Done-Enough Bar:**
> The system can be run confidently in front of the supervisor with zero surprises.

---

### 6B — Future Production Deployment Roadmap

Produce a credible production roadmap. Full execution not required before presentation.

```
- Docker / Docker Compose for full system packaging
- PostgreSQL migration
- Hosted/managed Neo4j option
- Backend deployment target (cloud VM, container service, or university server)
- Frontend deployment target
- HTTPS / TLS
- Authentication integration
- Logging and monitoring setup
- Backup strategy
- Dev / staging / prod environment separation
- CI/CD pipeline
- Admin dashboard direction
```

**Done-Enough Bar:**
> A credible, professional production deployment roadmap exists.

---

## Final Phase Summary

```
Phase 0   — Baseline Reality Check                                        ✓ COMPLETE
            Confirmed system runs end-to-end at smoke-test level.
            Produced Baseline Failure Map with P1/P2/P3 issues carried forward.

Phase 1   — Component & Engine Audit                                      COMPLETE ✅
            Audited every component in isolation: KG, KGAdapter, RAG, RAGAdapter,
            ALE, ALEAdapter, SCP, Session Manager, QU, Orchestrator, Composer,
            API Gateway, Streamlit UI, shared schemas/config.
            Covered: scope/boundaries, input/output contracts, logic correctness,
            SWE quality, error handling, component-level logging, component tests,
            realistic cases from student records, P1 fixes.
            QU locked the active intent name as plan_semester. plan_next_semester remains a forbidden/stale intent. Relative semester resolution is carried to Orchestrator.
            11 technical documentation files produced. All components locked.
            See Step 12 closure and gateway/Documentation/Phase_1_Final_Review.md.

Phase 1.5 — Integration Readiness Check                             COMPLETE ✅
            9 contracts verified; 1 mismatch fixed (assumptions_cleared signal);
            50 new tests; 305 total pass. No P1 contract mismatch remains.

Phase 2   — Integration & Behavioral Testing                        PARTIAL COMPLETE ✅
            D6 enrichment fixed. Behavioral stabilization done (2026-06-25);
            session-memory patch rolled back; 1037 tests pass.
            Full intent-by-intent testing and intelligence upgrade → Last Battle Plan.

Last Battle Plan  — 26 June to 30 June 2026                        IN PROGRESS
            Day 1 (26 Jun): LLM provider fix + full intent-by-intent E2E testing.
            Day 2 (27 Jun): Retest fixes + chatbot intelligence upgrade.
            Day 3 (28 Jun): SAE + real UI integration.
            Day 4 (29 Jun): Advanced auditability/logging (AM) + deployment start (PM).
            Day 5 (30 Jun): Deployment continuation + final verification + freeze.

Phase 3   — Real Chatbot Behavior     SUPERSEDED — absorbed into Last Battle Plan Days 1–2
Phase 4   — Advanced Logging          SUPERSEDED — absorbed into Last Battle Plan Day 4 AM
Phase 5   — Scaling / Production      SUPERSEDED — absorbed into Last Battle Plan Days 4–5
Phase 6   — Deployment Strategy       SUPERSEDED — absorbed into Last Battle Plan Days 4–5
```

---

*This document is the single source of truth for the PathFinder execution plan. It is open to modification only when a genuine architectural refinement surfaces across phases.*
