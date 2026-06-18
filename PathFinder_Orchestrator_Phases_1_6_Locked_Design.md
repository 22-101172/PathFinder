# PathFinder Orchestrator — Phases 1–6 Complete Design Lock

**Document scope:** Complete Orchestrator architecture locked during the June 2026 planning session.  
**Authority:** This document supersedes any conflicting assumptions in `PathFinder_Orchestrator_Handoff.md`. If this file conflicts with the older handoff, follow this file.  
**Status:** **Phases 1–6 locked.** Architecture complete, validated, and ready for Query Understanding (QU) handoff.  
**Implementation status:** Orchestrator design is locked, but Orchestrator implementation is **not** happening now. The next major workstream is QU planning and implementation.

---

## Table of Contents

1. [Phase 1 — Intent Taxonomy](#phase-1--intent-taxonomy)
2. [Phase 2 — Routing Logic](#phase-2--routing-logic)
3. [Phase 3 — Enrichment & Patching](#phase-3--enrichment--patching)
4. [Phase 4 — Error Handling](#phase-4--error-handling)
5. [Phase 5 — Schemas & Result Wrappers](#phase-5--schemas--result-wrappers)
6. [Phase 6 — Final Validation & Readiness](#phase-6--final-validation--readiness)
7. [Design Decisions That Override the Original Handoff](#design-decisions-that-override-the-original-handoff)
8. [Required Code Reconciliations Before Orchestrator Implementation](#required-code-reconciliations-before-orchestrator-implementation)
9. [Next Step — QU Planning & Implementation](#next-step--qu-planning--implementation)

---

## Phase 1 — Intent Taxonomy

### Multi-SQ Architecture

- QU outputs `list[StructuredQuery]`, one per independent intent per user turn.
- Orchestrator executes each SQ deterministically and independently.
- Results remain ordered according to QU output order.
- Composer receives the ordered result list and produces one coherent final answer.
- QU should preserve the logical order of sub-questions from the original user turn.
- The aggregate wrapper shape is defined in [Phase 5](#phase-5--schemas--result-wrappers).

Example:

```text
User: "Can I graduate, and if not give me a roadmap?"
QU output order:
1. run_graduation_audit
2. generate_graduation_roadmap
```

### Intent Count

| Category | Count |
|---|---:|
| Domain 1–6 executable intents | 24 |
| QU control intents | 2 |
| **Total intents** | **26** |
| Orchestrator error codes | Not intents |

---

### Domain 1 — Academic Planning (6 intents)

Pattern: KG enrichment + cached RAG rule bundles + ALE.

| Intent | Notes |
|---|---|
| `plan_semester` | Replaces older `plan_next_semester`. Handles explicit semester or defaults to next semester. |
| `generate_graduation_roadmap` | Multi-semester roadmap to graduation. |
| `run_graduation_audit` | Official graduation audit. Uses base StudentContext, not session assumptions. |
| `check_course_eligibility` | Eligibility for a target course. |
| `simulate_gpa_forward` | GPA projection using planned/current courses and expected grades. |
| `solve_target_gpa` | Required grades to reach a target CGPA. |

Removed intent:

| Removed | Replacement |
|---|---|
| `graduation_audit_with_roadmap` | QU emits two SQs: `run_graduation_audit` then `generate_graduation_roadmap`. Composer combines the answer. |

---

### Domain 2 — Course Information (4 intents)

Pattern: KG-only, stateless, no StudentContext, no RAG, no ALE.

| Intent | KG operation |
|---|---|
| `get_course_info` | `get_course_profile` |
| `get_course_prerequisites` | `get_prerequisites` |
| `get_skills_taught` | `get_skills_taught` |
| `search_courses_by_skill` | `search_courses_by_skill` |

`get_courses_by_track` is **not** a user-facing Domain 2 intent. It is internal enrichment for Domain 1 planning.

---

### Domain 3 — Career & Role Guidance (8 intents)

Pattern: KG-only. Some intents are stateless; others inject student course lists from `effective_context`.

| Intent | KG operation | Student-aware? |
|---|---|---|
| `get_role_profile` | `get_role_profile` | No |
| `get_roles_by_track` | `get_roles_by_track` | Conditional: only for “my track” fallback |
| `compute_skill_gap` | `compute_skill_gap` | Yes: `completed_courses` |
| `compute_alignment_score` | `compute_alignment_score` | Yes: `completed_courses` |
| `recommend_courses_to_close_gap` | `recommend_courses_to_close_gap` | Yes: `completed_courses` |
| `find_best_matching_roles` | `find_best_matching_roles` | Yes: `completed_courses` |
| `estimate_alignment_improvement` | `estimate_alignment_improvement` | Yes: `completed_courses` + `planned_courses` |
| `get_focus_courses_for_target` | `get_focus_courses_for_target` | Yes: `completed_courses`, empty allowed |

---

### Domain 4 — Track Guidance (4 intents)

Pattern: KG-only. OP12 and OP13 may use the student’s track for “my track” references; OP14 and OP15 are stateless.

| Intent | KG operation | Student-aware? |
|---|---|---|
| `get_track_overview` | `get_track_overview` | Conditional track fallback |
| `compare_tracks` | `compare_tracks` | Conditional track fallback for either side |
| `recommend_track_for_role` | `recommend_track_for_role` | No |
| `recommend_track_for_skill` | `recommend_track_for_skill` | No |

---

### Domain 5 — Policy & Handbook (1 intent)

Pattern: RAG-only, no KG, no ALE, no StudentContext.

| Intent | Engine call |
|---|---|
| `policy_query` | `rag.execute(sub_query=sq.original_text)` |

---

### Domain 6 — Student Record (1 intent)

Pattern: No engine call. Assembled directly from `effective_context`.

| Intent | Engine call |
|---|---|
| `get_student_record` | None |

---

### System / Control

| Name | Type | Producer | Handling |
|---|---|---|---|
| `clarification_needed` | QU control intent | QU | Orchestrator wraps/pass-through; no engine call. |
| `out_of_scope` | QU control intent | QU | Orchestrator wraps/pass-through; no engine call. |
| `student_not_found` | Orchestrator error code | Orchestrator | Per-SQ error when StudentContext is required but unavailable. It does not block stateless SQs in the same turn. |
| `engine_error` | Orchestrator error code | Orchestrator | Per-SQ error for adapter-level engine failures. |
| `validation_failed` | Orchestrator error code | Orchestrator | Per-SQ error for unexpected result shape or invalid field value. |

`student_not_found`, `engine_error`, and `validation_failed` are **not QU intents**.

---

## Phase 2 — Routing Logic

### Shared Turn Preparation

1. Load session before Orchestrator.
2. Load `StudentContext` and `SessionOverrides` from session state when needed.
3. Build `effective_context = build_effective_context(session.student_context, session.overrides)` before any student-aware ALE/KG/assembly intent.
4. RAG rule bundles are loaded once at app startup with `rag.get_rule_bundles()` and cached globally.
5. `academic_standing` is computed inline for Domain 6 using cached `academic_warning_rules.cgpa_warning_threshold`.

### Current Semester Contract

- `StudentContext.current_semester: str | None` must exist.
- SCP should populate it using:
  1. True in-progress course rows.
  2. Latest registration semester.
  3. System-clock fallback `get_current_semester()`.
- Orchestrator reads `effective_context.current_semester`.
- `get_next_semester(current_semester: str) -> str` must exist in `gateway/utils.py`.
- `get_next_semester` rules:
  - `Fall YYYY` → `Spring YYYY+1`
  - `Spring YYYY` → `Fall YYYY`
  - `Summer YYYY` → `Fall YYYY`

### Adapter Contracts

```python
kg.call(operation: str, params: dict) -> dict
```

KG adapter-level failures include `kg_unavailable`, `unknown_operation`, `bad_params`, and `kg_error`. Query-level business results such as `course_not_found` are not adapter failures.

```python
rag.execute(sub_query: str, student_context=None) -> dict
```

RAG returns:

```python
{
    "answer": str,
    "extracted_facts": list[str],
    "citations": list[dict],
}
```

`student_context` exists in the adapter signature but must not be forwarded to RAG core. Never pass StudentContext to RAG.

```python
rag.get_rule_bundles() -> dict[str, BaseModel | None]
```

Returns all eight rule-bundle keys. Failed bundles are `None`; total failure may return `{}`. Cache once at app startup.

```python
ale.call(
    operation: str,
    student_context: StudentContext,
    rule_bundles: dict,
    kg_data: dict | None = None,
    params: dict | None = None,
) -> dict
```

ALE returns model dumps on success, `cannot_compute` on data/validation limits, and `error` on infrastructure failures.

---

## Phase 3 — Enrichment & Patching

### Shared Phase 3 Rules

#### Rule Bundle Cache

- Rule bundles are cached once at app startup.
- They are not fetched per session, per query, or per intent.
- Required bundles must be checked before ALE calls.

#### Session-Level Caches

| Cache | Key | Used by | Lifespan |
|---|---|---|---|
| `courses_by_track` | `track_id` | `plan_semester`, `generate_graduation_roadmap` | Session |
| `course_profile_cache` | `course_code` | GPA ops and audit lookup | Session |
| `course_credit_lookup` | audit-level dict | `run_graduation_audit` | Session |
| `track_overview_cache` | `track_id` | `get_track_overview` | Session |

#### Override Semantics

`build_effective_context()` applies session assumptions to course lists only. It does not recalculate or mutate official GPA/warning totals.

| Override type | Effect |
|---|---|
| `assumed_done` | Adds `added_courses` to `completed_courses`; removes them from failed/in-progress. |
| `planned` | Adds `added_courses` to `in_progress_courses`. |
| `assumed_failed` | Adds `assumed_failed_courses` to failed; removes from completed/in-progress/zero-credit passed. |
| `assumed_passed` | Adds `assumed_passed_courses` to completed; removes from failed/in-progress. |
| `gpa_scenario` | Deferred; not part of Phase 3 MVP. |

Never affected by overrides:

- `cgpa`
- `cumulative_chs`
- `cumulative_cps`
- `consecutive_warnings`
- `total_warnings`

---

### Domain 1 — Academic Planning

#### Intent 1: `plan_semester`

**Pattern:** KG → cached RAG rules → ALE.

**StudentContext:** Uses `effective_context`.

**Pre-checks:**

- `effective_context.cgpa is not None`
- `effective_context.cumulative_chs is not None`
- `credit_limit_rules` non-None
- `graduation_requirement_rules` non-None
- `summer_semester_rules` non-None if target semester is Summer

**KG enrichment:**

```python
kg_result = kg.call("get_courses_by_track", {"track_id": effective_context.track_id})
kg_courses = kg_result["courses"]
kg_data = {"available_courses": kg_courses}
```

`kg_result["courses"]` contains raw KG course dicts. Orchestrator does not manually construct `AvailableCourse`; ALE Adapter maps `kg_data["available_courses"]` internally.

**Target semester resolution:**

```python
if sq.params.get("target_semester_type"):
    target_semester_type = sq.params["target_semester_type"]
else:
    next_sem = get_next_semester(effective_context.current_semester)
    target_semester_type = next_sem.split()[0]
```

**Params:**

```python
params = {
    "target_semester_type": target_semester_type,
    "target_track": sq.params.get("target_track"),
    "target_credit_load": sq.params.get("target_credit_load"),
    "max_credits_mode": sq.params.get("max_credits_mode", False),
}
```

Student fields come from `student_context=effective_context`; do not duplicate them in `params`.

**Student level:** Plan generation needs student level for priority scoring. Orchestrator does not compute it with hardcoded thresholds. ALE Adapter maps `effective_context.level` internally. If future level derivation from credits is needed, use `student_level_rules`, not hardcoded cutoffs.

**Flag:** If relevant overrides are active, set `assumptions_active=True`.

---

#### Intent 2: `generate_graduation_roadmap`

**Pattern:** KG → cached RAG rules → ALE.

**StudentContext:** Uses `effective_context`.

**Pre-checks:**

- `effective_context.cgpa is not None`
- `effective_context.cumulative_chs is not None`
- `effective_context.cumulative_cps is not None`
- `credit_limit_rules`, `graduation_requirement_rules`, `student_level_rules`, `grading_scale_rules` non-None
- `summer_semester_rules` non-None if accelerated mode or Summer start requires it

**KG enrichment:** Same `get_courses_by_track` cache as `plan_semester`.

```python
kg_data = {"available_courses": kg_result["courses"]}
```

**Semester/year derivation:**

```python
next_sem = get_next_semester(effective_context.current_semester)
target_semester_type = next_sem.split()[0]
starting_year = int(next_sem.split()[1])
```

**Assumed grade:** Resolve `assumed_grade_per_pass` using grading rules. Reject pass-only `P`. Default is `2.6` if absent.

**Params:**

```python
params = {
    "target_semester_type": target_semester_type,
    "starting_year": starting_year,
    "target_track": sq.params.get("target_track"),
    "accelerated_mode": sq.params.get("accelerated_mode", False),
    "max_credits_mode": sq.params.get("max_credits_mode", False),
    "target_credit_load": sq.params.get("target_credit_load"),
    "assumed_grade_per_pass": assumed_grade_per_pass,
}
```

Roadmap credit handling:

- Future course credits come from `available_courses[].credits` via `get_courses_by_track`.
- `course_credit_lookup` is not used by roadmap.
- `course_credit_lookup` is audit-only for historical transcript patching.

**Flag:** If relevant overrides are active, set `assumptions_active=True`.

---

#### Intent 3: `run_graduation_audit`

**Pattern:** KG → cached RAG rules → ALE.

**StudentContext:** Uses base `StudentContext`, not `effective_context`.

**Reason:** Graduation audit must reflect official transcript state. Session assumptions do not recalculate official GPA/credits and must not alter audit authority.

**Pre-checks:**

- `graduation_requirement_rules` non-None
- `academic_warning_rules` non-None
- `honors_rules` non-None
- `grading_scale_rules` non-None

**KG enrichment:** Build `course_credit_lookup` by calling `get_course_profile` for unique transcript course codes.

```python
audit_context = base_student_context
course_credit_lookup = {}
for code in unique_codes_from(audit_context.course_history):
    kg_result = kg.call("get_course_profile", {"course_code": code})
    credits = kg_result.get("credits")
    if credits is not None:
        course_credit_lookup[code] = credits
```

Partial KG failures are tolerated: unresolved codes are excluded and the audit proceeds with warning metadata.

**ALE input:**

```python
kg_data = {"course_credit_lookup": course_credit_lookup}
params = {}
ale.call(
    operation="run_graduation_audit",
    student_context=audit_context,
    rule_bundles=bundles,
    kg_data=kg_data,
    params=params,
)
```

Orchestrator does not manually build `CourseHistoryEntry`; ALE Adapter maps `student_context.course_history` internally and patches credits using `course_credit_lookup`.

**Flag:** If session overrides exist, set `assumptions_excluded=True` so Composer warns that official audit excludes session assumptions.

---

#### Intent 4: `check_course_eligibility`

**Pattern:** KG → cached RAG rules → ALE.

**StudentContext:** Uses `effective_context`.

**Pre-checks:**

- `retake_rules` non-None
- If `attempt_type == "improve_retake"` and `effective_context.cgpa is None`, return cannot-compute before ALE.

**Attempt type derivation:**

```python
if target_course_code not in [e.course_code for e in effective_context.course_history]:
    attempt_type = "first_attempt"
elif target_course_code in effective_context.failed_courses:
    attempt_type = "failed_retake"
elif target_course_code in effective_context.completed_courses:
    attempt_type = "improve_retake"
else:
    attempt_type = "first_attempt"
```

**KG enrichment:**

```python
kg_result = kg.call("get_prerequisites", {
    "course_code": target_course_code,
    "depth": "direct",
})

direct_prerequisite_codes = [
    p["course_code"] for p in kg_result.get("direct_prerequisites", [])
]

credit_threshold = None
for item in kg_result.get("non_course_prerequisites", []):
    if item.get("type") == "CREDIT_THRESHOLD":
        credit_threshold = item.get("value")
        break
```

`direct_prerequisites` is a list of dicts from KG, not raw strings. Orchestrator extracts course codes before calling ALE.

**ALE input:**

```python
kg_data = {
    "course_prerequisites": direct_prerequisite_codes,
    "course_credit_threshold": credit_threshold,
}
params = {
    "target_course_code": target_course_code,
    "attempt_type": attempt_type,
}
```

No separate `get_course_profile` call is needed for the credit threshold.

**Flag:** If relevant overrides are active, set `assumptions_active=True`.

---

#### Intent 5: `simulate_gpa_forward`

**Pattern:** KG → cached RAG rules → ALE.

**StudentContext:** Uses `effective_context`.

**Pre-checks:**

- `effective_context.cgpa is not None`
- `effective_context.cumulative_chs is not None`
- `effective_context.cumulative_cps is not None`
- `grading_scale_rules` non-None
- `retake_rules` non-None

**Planned course resolution:**

```python
if sq.params.get("planned_courses"):
    planned_course_codes = sq.params["planned_courses"]
    planned_course_source = "explicit_user_courses"
elif effective_context.in_progress_courses:
    planned_course_codes = effective_context.in_progress_courses
    planned_course_source = "current_in_progress_courses"
else:
    return clarification_needed
```

**KG enrichment:** `get_course_profile` per planned course to fetch real credits. SCP credit sentinel cannot be used for GPA math.

**Build `PlannedCourseGPA`:**

Each object includes:

- `course_code`
- `course_name`
- `credits` from KG
- `expected_grade` from SQ params
- `attempt_type`
- `has_cgpa_footprint`
- `old_grade` for retake/improve cases
- `improve_retake_number`
- `is_currently_in_progress`

**Params:**

```python
params = {
    "current_cgpa": effective_context.cgpa,
    "gpa_counted_credits": effective_context.cumulative_chs,
    "current_quality_points": effective_context.cumulative_cps,
    "planned_courses": planned_course_gpa_list,
    "excluded_in_progress_courses": excluded_in_progress_courses,
}
```

**Flag:** If relevant overrides are active, set `assumptions_active=True`.

---

#### Intent 6: `solve_target_gpa`

**Pattern:** KG → cached RAG rules → ALE.

**StudentContext:** Uses `effective_context`.

**Pre-checks:**

- `target_cgpa` present
- `effective_context.cgpa is not None`
- `effective_context.cumulative_chs is not None`
- `effective_context.cumulative_cps is not None`
- `grading_scale_rules`, `retake_rules`, `graduation_requirement_rules` non-None

**Already-met edge case:**

If `target_cgpa <= effective_context.cgpa`, call ALE with `planned_courses=[]` so ALE returns the standard `already_met` output shape.

**Planned course fallback:** Same as `simulate_gpa_forward`, unless target already met.

**KG required path:** `get_course_profile` per planned course for credits.

**KG optional path:** `get_prerequisites(..., depth="direct")` per planned course for personalization. Failure degrades gracefully; skip personalization for that course.

**Build `PlannedCourseTarget`:** Similar to `PlannedCourseGPA`, but no `expected_grade`; includes `related_completed_course` and `historical_grade` if available.

**Params:**

```python
params = {
    "current_cgpa": effective_context.cgpa,
    "gpa_counted_credits": effective_context.cumulative_chs,
    "current_quality_points": effective_context.cumulative_cps,
    "target_cgpa": target_cgpa,
    "planned_courses": planned_course_target_list,
    "assumed_grade_per_semester": sq.params.get("assumed_grade_per_semester", 3.0),
    "credits_per_semester": sq.params.get("credits_per_semester", 18),
    "planned_course_source": planned_course_source,
}
```

**Flag:** If relevant overrides are active, set `assumptions_active=True`.

---

### Domain 2 — Course Information

Pattern: KG-only, stateless, no caching required.

| Intent | QU responsibility | KG call | Notes |
|---|---|---|---|
| `get_course_info` | Resolve `course_code` | `get_course_profile(course_code)` | Return KG result as-is. |
| `get_course_prerequisites` | Resolve `course_code`, choose `depth` | `get_prerequisites(course_code, depth)` | `depth` defaults to `direct`. |
| `get_skills_taught` | Resolve `course_code` | `get_skills_taught(course_code)` | Empty skill list is informational. |
| `search_courses_by_skill` | Resolve `skill_ids` | `search_courses_by_skill(skill_ids)` | Distinguish unrecognized skills from recognized skills with no mapped courses. |

If Composer needs skill display names, QU should preserve resolved display names or original skill text in SQ metadata. Otherwise Composer may only have skill IDs.

---

### Domain 3 — Career & Role Guidance

Pattern: KG-only, mixed stateless/student-aware.

#### Empty Completed Courses Rule

| Intent | Behavior when `completed_courses=[]` |
|---|---|
| OP7 `compute_skill_gap` | Skip KG; return informational `reason="no_completed_courses"`. |
| OP8 `compute_alignment_score` | Skip KG; return informational. |
| OP9 `recommend_courses_to_close_gap` | Skip KG; return informational. |
| OP11 `find_best_matching_roles` | Skip KG; return informational. |
| OP10 `estimate_alignment_improvement` | First skip KG with informational if no completed courses; then check planned-course fallback. |
| OP17 `get_focus_courses_for_target` | Empty completed courses allowed. |

#### Intents

| Intent | KG call | Params | Notes |
|---|---|---|---|
| `get_role_profile` | `get_role_profile` | `role_id` | Stateless. |
| `get_roles_by_track` | `get_roles_by_track` | `track_id` | Explicit track or “my track” fallback. Result keys: `track`, `track_id`, `results`, `total_results`. |
| `compute_skill_gap` | `compute_skill_gap` | `role_id`, `completed_courses` | Result includes covered/missing skills. |
| `compute_alignment_score` | `compute_alignment_score` | `role_id`, `completed_courses` | Result includes `alignment_score`, `alignment_percentage`, `covered_weight`, `total_weight`. |
| `recommend_courses_to_close_gap` | `recommend_courses_to_close_gap` | `role_id`, `completed_courses` | No inline eligibility filtering; Composer adds prerequisite/availability disclaimer. |
| `find_best_matching_roles` | `find_best_matching_roles` | `completed_courses` | Result key `ranked_roles`. |
| `estimate_alignment_improvement` | `estimate_alignment_improvement` | `role_id`, `completed_courses`, `planned_courses` | Planned courses: explicit → fallback to `effective_context.in_progress_courses` → clarification. |
| `get_focus_courses_for_target` | `get_focus_courses_for_target` | `target_id`, `target_type`, `completed_courses` | `target_type` is `role` or `track`; “my track” fallback allowed. |

Student-aware Domain 3 intents use `effective_context`; if relevant overrides are active, set `assumptions_active=True`.

---

### Domain 4 — Track Guidance

Pattern: KG-only.

| Intent | KG call | Fallback / pre-check | Caching |
|---|---|---|---|
| `get_track_overview` | `get_track_overview(track_id)` | Explicit track or “my track” fallback to `effective_context.track_id`. | Cache per session per `track_id`. |
| `compare_tracks` | `compare_tracks(track_id_1, track_id_2)` | Either side may use “my track” fallback. If IDs equal, skip KG and ask for different track. | No caching required. |
| `recommend_track_for_role` | `recommend_track_for_role(role_id)` | Stateless. | No caching required. |
| `recommend_track_for_skill` | `recommend_track_for_skill(skill_id)` | Stateless. | No caching required. |

Domain 4 does not use mutated course assumptions. OP12/OP13 may only use `effective_context.track_id` as an identity fallback.

---

### Domain 5 — Policy & Handbook

Pattern: RAG-only.

#### `policy_query`

**QU contract:** `sq.original_text` must be a focused, self-contained handbook question, not necessarily the raw user turn. It must avoid student IDs and personal record data.

Examples:

| User phrasing | `sq.original_text` |
|---|---|
| “Can I retake CIS111?” | “What are the rules for retaking a course?” |
| “What happens if my CGPA drops below 2.0?” | “What are the academic standing rules for low CGPA?” |

**RAG call:**

```python
rag.execute(sub_query=sq.original_text)
```

**No StudentContext:** Never pass student data to RAG.

**Citations:** Forward unchanged to Composer.

**Blank original_text:** Orchestrator returns `clarification_needed` before calling RAG.

**Empty extracted_facts:** If query is valid and RAG returns no facts, treat as `soft_no_evidence`. This is not an engine error and does not prove the handbook lacks the answer.

**RAG unavailable/API/Chroma failure:** Treat as `engine_error` with `error_category="rag_adapter"`.

---

### Domain 6 — Student Record

Pattern: Assembly-only from `effective_context`.

#### `get_student_record`

**Engine call:** None.

**requested_fields:** Optional QU parameter and Composer presentation hint only. Orchestrator always builds the full snapshot and never filters by `requested_fields`.

**Exposed fields:**

| Field | Source | Nullable? |
|---|---|---|
| `track_id` | `effective_context.track_id` | Yes |
| `level` | `effective_context.level` | No: must be 1–4 |
| `cgpa` | `effective_context.cgpa` | Yes |
| `academic_standing` | Computed inline | No |
| `academic_standing_reason` | Computed inline | Yes |
| `study_status` | `effective_context.study_status` | Depends on SCP but key must exist |
| `total_credit_hours_earned` | `effective_context.total_credit_hours_earned` | No |
| `current_semester` | `effective_context.current_semester` | Yes |
| `consecutive_warnings` | `effective_context.consecutive_warnings` | No |
| `total_warnings` | `effective_context.total_warnings` | No |
| `completed_courses` | `effective_context.completed_courses` | Empty list allowed |
| `in_progress_courses` | `effective_context.in_progress_courses` | Empty list allowed |
| `failed_courses` | `effective_context.failed_courses` | Empty list allowed |
| `first_semester` | `effective_context.first_semester` | Yes |

**Never expose:**

- `student_id`
- `name`
- `military_status`
- `cumulative_chs`
- `cumulative_cps`
- `retake_count`
- `total_improve_retakes_used`

**Academic standing computation:**

```python
warning_rules = cached_rule_bundles.get("academic_warning_rules")

if effective_context.cgpa is None:
    academic_standing = "unknown"
    academic_standing_reason = "cgpa_not_available"
elif warning_rules is None or not hasattr(warning_rules, "cgpa_warning_threshold"):
    academic_standing = "unknown"
    academic_standing_reason = "missing_academic_warning_rules"
else:
    threshold = warning_rules.cgpa_warning_threshold
    if effective_context.cgpa < threshold or effective_context.consecutive_warnings > 0:
        academic_standing = "warning"
    else:
        academic_standing = "good"
    academic_standing_reason = None
```

No hardcoded `2.0`. Missing rule bundle means `unknown`, not guessed.

**Course names/details:** If the user asks for names/details, QU should decompose into `get_student_record` + Domain 2 `get_course_info` SQs where needed, or Composer suggests asking for details. Composer does not call engines by itself.

**Flag:** If session overrides are active and course lists may reflect them, set `override_state_active=True`.

---

## Phase 4 — Error Handling

### Locked Taxonomy

| Category | Status / code | Meaning |
|---|---|---|
| Student context missing | `status="error"`, `error_code="student_not_found"` | Student-aware SQ requires StudentContext but it is unavailable. |
| Adapter failure | `status="error"`, `error_code="engine_error"` | KG/RAG/ALE adapter unavailable or exception-level failure. |
| Result validation failure | `status="error"`, `error_code="validation_failed"` | Result shape or field value invalid. |
| Clarification | `status="clarification_needed"` | Missing/ambiguous required input. |
| Out of scope | `status="out_of_scope"` | QU determined unsupported query. |
| Business result | `status="informational"` | Valid query, but no entity/data or guard condition. |
| Soft no-evidence | `status="soft_no_evidence"` | Valid query, no clear evidence found. |

### StudentContext Requirement

StudentContext is required only for student-aware intents:

- Domain 1: all six intents.
- Domain 3: OP7–OP11 and OP17; OP5 and explicit OP6 are stateless.
- Domain 4: OP12/OP13 only if student-referential fallback is needed.
- Domain 6: required.
- Domain 2 and Domain 5: never required.

If StudentContext is missing for a student-aware SQ, that SQ gets `student_not_found`. Other SQs continue.

### SQ Validity

- Missing `intent`: QU contract bug; return `clarification_needed` for that SQ and log internally.
- Missing `params`: treat as empty dict.
- Blank `original_text` for `policy_query`: `clarification_needed`; do not call RAG.
- Missing required resolved IDs: `clarification_needed` or `validation_failed`, depending on whether it is user-fixable or an internal QU bug.

### Adapter-Level Engine Failures

| Adapter | Examples | Result |
|---|---|---|
| KG | Neo4j unavailable, unknown operation, adapter exception | `engine_error`, `error_category="kg_adapter"` |
| RAG | Groq/Chroma unavailable, adapter initialization failed | `engine_error`, `error_category="rag_adapter"` |
| ALE | ALE exception, import failure, marshaling bug | `engine_error`, `error_category="ale_adapter"` |

Do not expose stack traces, raw adapter errors, or internal operation names to the user.

### Query-Level Business Results

KG not-found/no-data results are not engine errors. They become `informational` results.

Examples:

- `course_not_found`
- `role_not_found`
- `track_not_found`
- `skill_not_found`
- `no_courses_provided`
- `no_valid_courses_provided`
- `identical_tracks_provided`

Composer decides whether to ask for clarification, suggest alternatives, or explain no data.

### Soft No-Evidence

RAG empty facts from a valid non-blank query is `soft_no_evidence`.

- Do not assert the handbook lacks the answer.
- Do not treat it as engine failure.
- Composer explains uncertainty and suggests advisor follow-up if needed.

### Post-Execution Validation

Validate required keys and allowed nullability by domain.

| Domain | Required validation |
|---|---|
| Domain 1 | ALE result has `status`; if `success`, required output fields per operation exist. |
| Domain 2 | KG result shape matches operation. Empty lists allowed where contract permits. |
| Domain 3 | KG result shape matches operation; optional fields tolerated. |
| Domain 4 | KG result shape matches operation. |
| Domain 5 | `answer`, `extracted_facts`, `citations` exist. Empty facts/citations allowed. |
| Domain 6 | Exposed fields exist; nullable fields may be `None`; `level` must be 1–4; warnings non-negative. |

If validation fails, return `validation_failed` with `error_category="result_shape"` or `field_value`.

### Multi-SQ Behavior

- Every SQ normally produces one `PerSQResult`.
- One SQ failing does not cascade.
- Stateless SQs ignore missing StudentContext, but can still fail their own pre-checks.
- No silent skips.
- Catastrophic interruption before wrapping all SQs is the only case for partial execution handling.

### Logging

Log safe developer details only:

- Timestamp
- Intent/operation
- Error code/category
- Non-PII params such as course codes, role IDs, track IDs
- Adapter response or stack trace only for internal logs

Never log:

- Student ID
- Student name
- Personal GPA/grades
- Full request/response payloads containing personal data

---

## Phase 5 — Schemas & Result Wrappers

### Per-SQ Result Wrapper

```python
{
    "sq_index": int,
    "intent": str,
    "status": str,  # success, error, clarification_needed, out_of_scope, informational, soft_no_evidence

    "data": dict | None,

    "error_code": str | None,        # student_not_found, engine_error, validation_failed
    "error_category": str | None,    # kg_adapter, rag_adapter, ale_adapter, result_shape, field_value
    "error_detail": str | None,      # Safe Composer-facing explanation

    "clarification_prompt": str | None,
    "scope_explanation": str | None,

    "assumptions_active": bool | None,
    "assumptions_excluded": bool | None,
    "override_state_active": bool | None,

    "citations": list[dict] | None,
}
```

### Per-SQ Status Taxonomy

| Status | Meaning |
|---|---|
| `success` | Intent executed successfully. |
| `error` | Adapter or validation failure. |
| `clarification_needed` | Required info missing or ambiguous. |
| `out_of_scope` | Query outside PathFinder scope. |
| `informational` | Valid business result, not an error. |
| `soft_no_evidence` | Valid query but no clear evidence/data found. |

### Aggregate Turn Wrapper

```python
{
    "turn_id": str,
    "session_id": str,  # Session identifier, not student_id, contains no PII
    "timestamp": str,

    "results": list[PerSQResult],
    "result_count": int,

    "turn_status": str,  # completed, needs_clarification, partial_success, failed, out_of_scope
    "has_error": bool,
    "has_clarification": bool,
    "has_informational": bool,
    "has_soft_no_evidence": bool,

    "turn_summary": str | None,
}
```

### Turn Status Taxonomy

| Turn status | When used |
|---|---|
| `completed` | All results are non-blocking: `success`, `informational`, or `soft_no_evidence`. |
| `needs_clarification` | At least one clarification and no errors. |
| `partial_success` | At least one productive result and at least one error or clarification. |
| `failed` | All executable SQs resulted in `error`. |
| `out_of_scope` | All SQs are `out_of_scope`. |

### Flags

| Flag | Used by | Meaning |
|---|---|---|
| `assumptions_active` | Domain 1–3 student-aware intents except audit | Effective context used active relevant session assumptions. Caution flag, not proof that output changed. |
| `assumptions_excluded` | `run_graduation_audit` | Audit is official and excludes session assumptions. |
| `override_state_active` | `get_student_record` | Snapshot may reflect session assumptions. |

### Citations

Domain 5 only.

```python
{
    "source": "CIS Handbook",
    "page": int | None,
    "text": str,
}
```

Composer should include citations and never suppress them for policy answers.

### Ordered Delivery

Composer receives the aggregate turn wrapper and iterates `results` in order. It presents successes, informational results, soft no-evidence, errors, clarifications, and out-of-scope results in one coherent response.

---

## Phase 6 — Final Validation & Readiness

### Cross-Phase Coherence Checks

#### 1. StudentContext Flow

- SCP creates base StudentContext.
- Session Manager stores StudentContext and SessionOverrides.
- Orchestrator builds `effective_context` for student-aware intents.
- RAG never receives StudentContext.
- Domain 6 exposes only safe snapshot fields.

#### 2. Rule Bundle Flow

- RAG extracts eight rule bundles.
- Bundles cached at app startup.
- Domain 1 uses bundles for ALE and pre-checks.
- Domain 6 uses `academic_warning_rules` for `academic_standing`.
- No hardcoded academic thresholds.

#### 3. Entity Resolution Boundary

- QU resolves course, role, skill, and track entities.
- Orchestrator receives resolved IDs.
- Orchestrator only handles student-referential fallbacks for “my track” style intents.
- KG receives IDs, not raw user text.

#### 4. Caching Strategy

- App-startup: rule bundles.
- Session-level: planning courses by track, course profile credits, audit credit lookup, track overview.
- No caching required for Domain 2, most Domain 3, Domain 5, Domain 6.

#### 5. Override Semantics

- Domain 1: most intents use `effective_context`; audit uses base StudentContext.
- Domain 2: stateless, no overrides.
- Domain 3: student-aware intents use `effective_context`.
- Domain 4: only track identity fallback; no course-list assumptions.
- Domain 5: stateless, no overrides.
- Domain 6: uses `effective_context`; course-list overrides may affect snapshot.
- Official GPA/warning fields are never recalculated by overrides.

#### 6. Error Handling

- StudentContext missing is per-SQ and intent-specific.
- Adapter-level failures use `engine_error`.
- KG not-found/no-data uses `informational`.
- RAG empty facts use `soft_no_evidence`.
- Result-shape failures use `validation_failed`.
- Multi-SQ execution does not cascade.

#### 7. Result Composition

- Every SQ normally produces one result.
- Results are ordered.
- Aggregate wrapper carries turn-level status and booleans.
- Composer has enough metadata to synthesize one response.

### Implementation Prerequisites

Before Orchestrator implementation starts:

1. QU contract finalized.
2. Phase 5 Pydantic schemas added.
3. `get_next_semester()` implemented in `gateway/utils.py`.
4. `StudentContext.current_semester` added and populated by SCP.
5. App-startup rule-bundle loading hook implemented.
6. Existing KG/RAG/ALE adapter contracts rechecked against this document.

### Orchestrator Readiness Checklist

- [ ] Phases 1–6 read by implementation agent/team.
- [ ] All intent routing reviewed against locked specs.
- [ ] Phase 3 enrichments understood.
- [ ] Phase 4 error taxonomy understood.
- [ ] Phase 5 wrapper schemas implemented as Pydantic models later.
- [ ] Required code reconciliations completed before Orchestrator implementation.
- [ ] QU contract complete.

---

## Design Decisions That Override the Original Handoff

| Topic | Locked decision |
|---|---|
| Handoff authority | This MD supersedes the older handoff on conflict. |
| Intent `plan_next_semester` | Renamed to `plan_semester`. |
| `graduation_audit_with_roadmap` | Removed; use two SQs. |
| Domain 6 | One intent only: `get_student_record`. |
| `get_courses_by_track` | Internal enrichment only, not user-facing. |
| Current semester | SCP/data-detected first; system clock only fallback. |
| StudentContext to RAG | Prohibited. |
| RAG empty facts | Soft no-evidence, not definitive absence and not engine error. |
| Academic standing threshold | Uses RAG rule bundle; no hardcoded `2.0`. |
| Audit overrides | Audit uses base context only; assumptions excluded. |
| Domain 6 overrides | Domain 6 uses `effective_context`; snapshot may reflect assumptions. |
| Multi-SQ failures | No cascade; every SQ normally gets a result. |
| QU vs Composer | QU builds StructuredQuery list; Response Composer is separate. |

---

## Required Code Reconciliations Before Orchestrator Implementation

These are not Orchestrator implementation tasks yet, but must be done before coding Orchestrator.

1. **`gateway/utils.py`**
   - Add `get_next_semester(current_semester: str) -> str`.

2. **StudentContext schema**
   - Add `current_semester: str | None = None`.

3. **SCP**
   - Populate `current_semester` using true in-progress rows, then latest registration semester, then `get_current_semester()`.

4. **Phase 5 schemas**
   - Add Pydantic models for `PerSQResult` and `TurnWrapper` later.

5. **App startup**
   - Load and cache `rag.get_rule_bundles()` once.

---

## Next Step — QU Planning & Implementation

Orchestrator design is locked. Do **not** implement Orchestrator now.

Next workstream:

- QU planning.
- QU implementation.
- QU must output ordered `list[StructuredQuery]`.
- QU responsibilities:
  - intent classification
  - compound-turn decomposition
  - entity resolution
  - parameter extraction
  - student-referential detection
  - `StructuredQuery` list building

Response Composer remains a separate component after QU/Orchestrator contracts are stable.

---

**Final status:** Orchestrator planning Phases 1–6 are locked. This document is the current authority for Orchestrator design.
