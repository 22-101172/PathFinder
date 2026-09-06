# PathFinder — Orchestrator Technical Documentation

**Status:** COMPLETE ✅ — PASS / ORCHESTRATOR LOCKED  
**Component:** Orchestrator  
**Phase:** Phase 1 (Step 7) Audit Complete

---

## 1. Component Summary

The Orchestrator is PathFinder's controlled execution layer. It sits between the Query Understanding (QU) component and the Response Composer, accepting an ordered `list[StructuredQuery]` and returning a `TurnWrapper` containing one `PerSQResult` per SQ.

Key principles of the Orchestrator:
* **No Parsing:** The Orchestrator does not parse or classify user text.
* **No Answer Generation:** The Orchestrator does not produce natural-language responses.
* **No Session Persistence:** The Orchestrator reads session state but never writes to the session store.
* **No Engine Logic Duplication:** ALE, KG, and RAG business logic remains in those engines. The Orchestrator only wires them together.
* **Isolation:** Each SQ is executed independently. A failure in one SQ does not cascade to others.

---

## 2. Responsibility Boundaries

### Orchestrator Owns:
* Intent-based dispatch to the correct domain handler.
* Selecting the correct student context type (base vs effective) per intent.
* Accumulating and applying session overrides for the current turn.
* Calling KG/RAG to gather curriculum data before ALE calls.
* Checking required rule bundles exist before any ALE call.
* Resolving relative semester phrases to structured (season, year) form.
* Passing `required_zero_credit_courses` to ALE graduation audit and roadmap.
* Patching missing transcript credit hours from KG data.
* Distinguishing KG adapter infrastructure errors from KG business not-found results.
* Blocking forbidden/stale intents before routing.
* Wrapping every SQ result in a `PerSQResult` with correct status, error code, and data.
* Building the `TurnWrapper` summary and turn-level status.
* Per-SQ and per-turn structured logging.

### Orchestrator Does NOT Own:
* **QU:** Intent classification, entity extraction, parameter parsing.
* **ALE:** Academic eligibility, GPA calculation, graduation planning logic.
* **KG:** Curriculum graph queries, role/skill/track lookups.
* **RAG:** Policy text retrieval and answer extraction.
* **SCP:** StudentContext construction from the student database.
* **Session Manager:** Session persistence, session read/write, `build_effective_context` (called but owned by Session Manager).
* **Composer:** Natural-language narration of results.

---

## 3. Files and Responsibilities

* `gateway/orchestrator.py`: The entire Orchestrator component. All routing, context selection, KG enrichment, ALE call preparation, and result wrapping.
* `gateway/utils.py`: `resolve_relative_semester_text()` and `get_next_semester()` — semester utilities called by the Orchestrator.
* `gateway/session_manager.py`: `build_effective_context()` and `_apply_overrides()` — imported and called by the Orchestrator.
* `tests/test_orchestrator.py`: 103 unit tests covering routing, context selection, rule bundle checks, KG enrichment, ALE dispatch, error taxonomy, and turn wrapper construction.
* `tests/test_utils.py`: 18 tests covering relative and absolute semester resolution utilities.

---

## 4. Inputs and Outputs

**Public entry point:** `Orchestrator.execute_turn()`

```python
def execute_turn(
    self,
    sqs: list[StructuredQuery],
    session: SessionState,
    rule_bundles: dict,
) -> TurnWrapper:
```

**Inputs:**
* `sqs`: Ordered list of `StructuredQuery` objects from QU. Each contains `intent`, `entities`, `params`, `session_overrides`, `student_referential_fallback`, and `original_text`.
* `session`: Current `SessionState` containing `student_context`, `overrides`, `session_id`, and `last_referenced`.
* `rule_bundles`: Dict of pre-loaded rule bundle objects (from RAG startup extraction).

**Output:** A `TurnWrapper` containing:
* `turn_id`, `session_id`, `timestamp`
* `results`: `list[PerSQResult]`, one entry per SQ
* `turn_status`: aggregated status for the full turn
* `has_error`, `has_clarification`, `has_informational`, `has_soft_no_evidence`

**Secondary public method:** `extract_last_referenced(sqs)` — returns a `LastReferenced` from the first SQ with resolved entities, or `None` to preserve the existing value.

---

## 5. Main Execution Flow

For each call to `execute_turn`:

1. **Turn overrides accumulation** — All per-SQ `session_overrides` are merged into `turn_overrides`. If any SQ contains a `clear` override action, prior session overrides are discarded.
2. **Effective context construction** — If a `StudentContext` is available, `build_effective_context(base_context, execution_overrides)` is called once for the entire turn.
3. **Per-SQ dispatch** — Each SQ is dispatched to `_execute_sq`. Exceptions are caught and wrapped as `engine_error` without propagating to other SQs.
4. **TurnWrapper construction** — `_build_turn_wrapper` aggregates per-SQ statuses into the turn-level `turn_status`.

**Turn status derivation:**

| Condition | `turn_status` |
|---|---|
| All SQs `out_of_scope` | `out_of_scope` |
| Only errors, no productive results | `failed` |
| Only clarifications, no errors | `needs_clarification` |
| Mix of errors or clarifications with productive results | `partial_success` |
| No errors or clarifications | `completed` |

---

## 6. Intent Routing

All 26 locked intents are dispatched from `_execute_sq`:

| Domain | Intents | Handler |
|---|---|---|
| D1 Academic Planning | `plan_semester`, `generate_graduation_roadmap`, `run_graduation_audit`, `check_course_eligibility`, `simulate_gpa_forward`, `solve_target_gpa` | Per-intent methods |
| D2 Course Info | `get_course_info`, `get_course_prerequisites`, `get_skills_taught`, `search_courses_by_skill` | `_exec_d2_course` |
| D3 Career/Role | `get_role_profile`, `get_roles_by_track`, `compute_skill_gap`, `compute_alignment_score`, `recommend_courses_to_close_gap`, `find_best_matching_roles`, `estimate_alignment_improvement`, `get_focus_courses_for_target` | `_exec_d3_career` |
| D4 Track Guidance | `get_track_overview`, `compare_tracks`, `recommend_track_for_role`, `recommend_track_for_skill` | `_exec_d4_track` |
| D5 Policy | `policy_query` | `_exec_policy` |
| D6 Student Record | `get_student_record` | `_exec_student_record` |
| Control | `clarification_needed`, `out_of_scope` | Pass-through wrappers |

**Forbidden intent handling:** 14 stale/renamed intents (e.g., `plan_next_semester`, `handbook_query`, `check_eligibility`) are explicitly rejected before any routing with `validation_failed / intent` error.

---

## 7. Context Selection Rules

```
run_graduation_audit          → base_context only (never effective_context)
All other D1 Academic intents → effective_context
D3 student-aware intents      → effective_context
get_student_record            → effective_context
get_focus_courses_for_target  → effective_context

Conditional student intents (get_roles_by_track, get_track_overview, compare_tracks):
  → effective_context if sq.student_referential_fallback=True and base_context is available
  → no context otherwise (stateless)

D2 Course Info   → no context (always stateless)
D5 Policy/RAG    → no context (student data never passed to RAG)
D4 Track (non-referential) → no context
```

Student context is unavailable for student-required intents → `student_not_found` error, not a crash.

---

## 8. Rule Bundle Handling

Rule bundles are pre-loaded at system startup and passed in as a dict. The Orchestrator validates their presence before each ALE call.

| Intent | Required Bundles |
|---|---|
| `plan_semester` | `credit_limit_rules`, `graduation_requirement_rules` (+ `summer_semester_rules` if Summer) |
| `generate_graduation_roadmap` | `credit_limit_rules`, `graduation_requirement_rules`, `student_level_rules`, `grading_scale_rules` |
| `run_graduation_audit` | `graduation_requirement_rules`, `academic_warning_rules`, `honors_rules`, `grading_scale_rules` |
| `check_course_eligibility` | `retake_rules` |
| `simulate_gpa_forward` | `grading_scale_rules`, `retake_rules` |
| `solve_target_gpa` | `grading_scale_rules`, `retake_rules`, `graduation_requirement_rules` |
| `get_student_record` | `academic_warning_rules` (optional — degrades to `unknown` standing if absent) |

Missing required bundles → `engine_error / ale_adapter` with `missing_bundles` listed. Never an assumption, never a default.

---

## 9. KG Enrichment Responsibilities

The Orchestrator calls KG to gather data before ALE calls. It does not interpret KG results.

| KG Call | Used For |
|---|---|
| `get_courses_by_track(track_id)` | Injects `available_courses` into ALE for `plan_semester` and `generate_graduation_roadmap`; extracts `required_zero_credit_courses` for graduation audit and roadmap |
| `get_prerequisites(course_code, depth="direct")` | Injects prerequisites into ALE for `check_course_eligibility` |
| `get_course_profile(course_code)` | Retrieves real credit hours for `simulate_gpa_forward` and `solve_target_gpa`; builds `course_credit_lookup` for `run_graduation_audit` |

**Cache:** `_TurnCaches` holds `courses_by_track` and `course_profile_cache` per turn to avoid redundant KG calls for the same data within one turn.

**KG error vs business not-found:** `_is_kg_adapter_error()` checks if the error code is a known adapter error (`kg_unavailable`, `unknown_operation`, `bad_params`, `kg_error`). These return `engine_error`. All other results with an `error` field are treated as business not-found and returned as `informational`.

**`required_zero_credit_courses`:** Extracted from the KG courses-by-track result and passed explicitly to ALE for both `generate_graduation_roadmap` and `run_graduation_audit`. No fake defaults are used.

**Missing credit hours:** For `run_graduation_audit`, transcript course codes are looked up individually via `get_course_profile`. Only courses with confirmed credits are added to `course_credit_lookup`. ALE receives no sentinel or placeholder values.

---

## 10. ALE Call Responsibilities

The Orchestrator is responsible for building the `ctx`, `bundles`, `kg_data`, and `params` arguments for each ALE call. ALE business logic is never duplicated in the Orchestrator.

**Track resolution priority (plan_semester, generate_graduation_roadmap):**
```
1. sq.params["target_track"]      (explicit user request)
2. sq.entities.track_id           (QU-resolved entity)
3. ctx.track_id                   (student's official track)
```

If the resolved track belongs to an unsupported-track student and was not explicitly provided, the Orchestrator returns an `informational` result with `reason_code=unsupported_track` before calling ALE.

**Graduated student gate:** `plan_semester` and `generate_graduation_roadmap` return early `informational` if `ctx.study_status == "Graduated"`.

**ALE result shapes handled:**

| ALE status | Orchestrator action |
|---|---|
| `error` | Returns `engine_error / ale_adapter` |
| `cannot_compute` | Returns `informational` with ALE data |
| Any other (including `success`) | Returns `success` with ALE data |

---

## 11. Policy / RAG Boundary

The Orchestrator calls `self._rag.execute(original_text)` for `policy_query`. It passes only the rewritten self-contained question string — no student data, no session state.

RAG result handling:

| RAG result | Orchestrator action |
|---|---|
| `error = "empty_query"` | `clarification_needed` |
| Any other `error` | `engine_error / rag_adapter` |
| Has `extracted_facts` | `success` |
| Empty `extracted_facts` | `soft_no_evidence` |

---

## 12. Session Override Handling

Session overrides accumulate per SQ within the turn via `_collect_turn_overrides`. If any SQ contains `override_action="clear"`, prior session-level overrides are fully discarded and only the current turn's overrides apply.

Effective context is built once per turn from `execution_overrides` (merged session + turn). The `run_graduation_audit` handler always uses `base_context` regardless of active assumptions, and signals `assumptions_excluded=True` in the result flags when assumptions are present.

---

## 13. Error / Status Taxonomy

`PerSQResult.status` values:

| Status | Meaning |
|---|---|
| `success` | Engine returned a valid result |
| `informational` | Business-level not-found or not-applicable (not an error) |
| `soft_no_evidence` | RAG found no supporting facts for the policy question |
| `clarification_needed` | Missing required entity; user must clarify |
| `out_of_scope` | Intent is outside PathFinder's scope |
| `error` | Infrastructure failure or validation failure |

`PerSQResult.error_code` values (when `status="error"`):

| Code | Meaning |
|---|---|
| `engine_error` | Adapter/infrastructure failure (KG, ALE, RAG) |
| `student_not_found` | StudentContext unavailable for a student-required intent |
| `validation_failed` | Invalid intent, missing required entity, or bad field value |

`PerSQResult.error_category` values (when `error_code="validation_failed"` or `"engine_error"`):

| Category | Meaning |
|---|---|
| `intent` | The intent name itself is invalid or forbidden |
| `field_value` | A required field has an invalid or missing value |
| `result_shape` | The engine returned an unexpected shape |
| `ale_adapter` | ALE adapter is the failure source |
| `kg_adapter` | KG adapter is the failure source |
| `rag_adapter` | RAG adapter is the failure source |

---

## 14. Relative Semester Resolution

QU produces one of two semester shapes:
* **Explicit:** `target_semester="Fall 2027"`, `semester_resolution_source="explicit"`
* **Relative:** `target_semester_text="3 Falls from now"`, `semester_resolution_source="relative"`

The Orchestrator resolves relative phrases to `(season, year)` using `utils.resolve_relative_semester_text(text, ctx.current_semester)`. It uses `ctx.current_semester`, not the machine date. This ensures resolution is based on the student's academic calendar, not wall-clock time.

**Supported relative patterns (case-insensitive):**
* `next Fall / next Spring / next Summer` → season, current_year + 1
* `N Fall(s)/Spring(s)/Summer(s) from now` → N in 1–6, digit, word, or ordinal form
* Examples: `"2 falls from now"`, `"third Spring from now"`, `"one Summer from now"`

**Resolution flow for `plan_semester`:**
1. Use `target_semester_type` if present.
2. Parse `target_semester` or `target_semester_text` — try explicit split first, then `resolve_relative_semester_text`.
3. If no semester provided, derive from `ctx.current_semester` via `get_next_semester`.
4. Default to `Fall` if no current semester is available.
5. If resolution fails, return `validation_failed / field_value` — no silent fallback.

**Resolution flow for `generate_graduation_roadmap`:**
* `target_end_semester_type` / `target_end_year` resolved from params if provided.
* Starting semester always derived from `ctx.current_semester` (or machine date as last resort).

ALE receives only resolved semester fields (`target_semester_type`, `starting_year`, `target_end_semester_type`, `target_end_year`). Raw natural-language semester phrases are never forwarded to ALE.

---

## 15. Logging Design

All log entries use structured key=value format compatible with log aggregation tools.

**Turn-level log points:**

| Log key | Fields | Level |
|---|---|---|
| `Orchestrator.turn_start` | `session` (first 8 chars), `sq_count`, `assumptions_active`, `had_clear`, `base_context`, `effective_context` | INFO |
| `Orchestrator.sq_start` | `index`, `intent`, primary entity (course/role/track or `-`) | INFO |
| `Orchestrator.sq_result` | `index`, `intent`, `status`, `error_code`, `error_category`, `has_data`, `citations`, `duration_ms` | INFO |
| `Orchestrator.turn_result` | `status`, `results`, `error`, `clarification`, `soft`, `duration_ms` | INFO |
| `Orchestrator.sq_context` | `index`, `intent`, `context_mode`, `referential_fallback`, `assumptions_active` | DEBUG |
| `Orchestrator.semester_resolved` | `intent`, `method` (explicit/relative), `raw`, `resolved` | DEBUG |
| `Orchestrator.semester_resolution_failed` | `intent`, `raw` | WARNING |

**Privacy guarantees:**
* No student ID, name, CGPA, grade, or transcript data is logged.
* Raw user text is not logged.
* `session` is logged as first 8 characters of `session_id` only.
* `raw` semester text is capped at 80 characters via `%.80s` format.
* Entity values logged are curriculum/career IDs (course codes, role IDs, track IDs) — not student-specific data.

---

## 16. Test Coverage Summary

```
tests/test_orchestrator.py    103 passed, 0 failed
tests/test_utils.py            18 passed, 0 failed
Total                         121 passed, 0 failed
```

**Test categories:**
* Forbidden intent rejection
* Control intent pass-through (`clarification_needed`, `out_of_scope`)
* Per-intent routing for all 26 locked intents
* Context selection: base vs effective vs none vs conditional
* `run_graduation_audit` always uses base context
* `has_active_assumptions` flag propagation
* Rule bundle checks and missing bundle error paths
* KG enrichment: `courses_by_track`, `course_profile_cache`, `required_zero_credit_courses`
* KG adapter error vs business not-found distinction
* Semester resolution: explicit, relative, fallback, failure
* Unsupported track blocking
* Graduated student early return
* Turn override accumulation and clear behavior
* `TurnWrapper` turn status derivation
* Per-SQ error isolation (one SQ failure does not cascade)
* Logging (smoke-level, does not assert on privacy fields in isolation)

---

## 17. Known Carry-Forwards (Non-Blocking)

These items are documented but explicitly deferred beyond Phase 1 component scope:

| ID | Item | Deferred To |
|---|---|---|
| ORC-CF-1 | Full end-to-end chatbot behavior and intent-behavior matrix | Phase 2 integration |
| ORC-CF-2 | Composer narration correctness for Orchestrator result shapes | Step 8 Composer audit |
| ORC-CF-3 | API/UI integration testing | Later phases |
| ORC-CF-4 | `improve_retake_number` MVP approximation: Orchestrator treats first improve-retake as sequence #1; precise per-course improve-retake count requires a future SCP field | SCP enhancement |
| ORC-CF-5 | Turn-level cache is per-call, not per-session; repeat queries within one session refetch KG data | Phase 5 optimization |

---

## 18. Final Verdict

**Status:** COMPLETE ✅ — PASS / ORCHESTRATOR LOCKED

The Orchestrator correctly routes all 26 locked intents, enforces the correct context type per intent, validates rule bundles before ALE calls, enriches ALE with real KG data (no fake defaults), distinguishes infrastructure errors from business not-found, handles relative and explicit semester resolution, and isolates per-SQ failures. Logging is privacy-safe and structured. 121 tests pass. No P1 issues remain open.
