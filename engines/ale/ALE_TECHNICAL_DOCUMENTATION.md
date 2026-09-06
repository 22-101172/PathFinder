# ALE Engine — Technical Documentation

> **Status:** COMPLETE / LOCKED — Phase 1 Step 3  
> **Last updated:** 2026-06-22

---

## 1. Overview

The **Academic Logic Engine (ALE)** is PathFinder's deterministic academic computation layer. It performs all rule-bound academic calculations that require structured input/output contracts, strict GPA arithmetic, and handbook-policy enforcement.

**Key properties:**
- Fully deterministic — given the same inputs and rule bundles, ALE always produces the same outputs.
- No external calls — ALE functions never call KG, RAG, LLM, Composer, or Session Manager.
- No hardcoded academic values — all thresholds, caps, and limits come from rule bundles injected at call time.
- Structured outputs — all functions return Pydantic models with `status`, `reason_codes`, `warnings`, and `required_data_missing` fields, enabling structured error handling at every layer.

**ALEAdapter** is the stateless contract bridge between the Orchestrator and the ALE functions. It maps `StudentContext`, `rule_bundles`, `kg_data`, and `params` into typed Pydantic input models and returns serialized result dicts.

---

## 2. Responsibility Boundaries

### ALE owns
- Course eligibility decisions (prerequisites, credit threshold, retake rules, in-progress guard)
- Graduation audit (all official requirements, warning/dismissal check, honors sub-result)
- GPA forward simulation (footprint-aware, retake-cap-aware)
- Target GPA solving (cap-aware distribution, multi-semester projection)
- Next-semester plan generation (eligibility filtering, credit caps, retake prioritization, multi-plan variants)
- Graduation roadmap simulation (multi-semester loop, warning projection, target-semester mode)

### ALE does NOT own
- Entity resolution (course codes, role names, track IDs) → QU / KG
- Course catalogue lookup and available-course pool construction → KG / Orchestrator
- Handbook policy extraction → RAG
- Session state mutation (what-if assumptions, session history) → Session Manager
- Natural-language response wording → Composer
- Current-semester inference → SCP / Orchestrator
- Track ID normalization → SCP / KG

---

## 3. Input Sources

| Input | Source | Responsibility |
|---|---|---|
| `StudentContext` | SCP / Session Manager | Official academic record — never mutated by ALE |
| `rule_bundles` | RAGAdapter → Orchestrator | Extracted from handbook; injected as Pydantic models |
| `kg_data` | KGAdapter → Orchestrator | Course pool, prerequisites, credit lookup |
| `params` | QU → Orchestrator | Intent-specific parameters (target CGPA, semester type, etc.) |

ALEAdapter maps all four sources into typed ALE input models. It performs no entity resolution and must not invent missing academic data.

---

## 4. Rule Bundle Models

All rule bundles are Pydantic models defined in `engines/ale/schemas.py`. They are never hardcoded inside ALE functions.

### `RetakeRules`
Governs retake grade caps and improve-retake quotas.
- `failed_first_retake_grade_cap`: Max letter grade on first retake after failure (Handbook: B)
- `improve_retake_first_attempt_cap`: Cap on first improve retake (Handbook: None — no cap)
- `improve_retake_subsequent_cap`: Cap on 2nd+ improve retake (Handbook: B)
- `improve_retake_max_courses_cgpa_above_2`: Max distinct courses for CGPA ≥ 2.0 improve retakes (Handbook: 3)
- `improve_retake_unlimited_below_cgpa`: Below this CGPA, improve retakes are unlimited (Handbook: 2.0)

### `CreditLimitRules`
Governs credit hour limits per regular semester.
- `cgpa_above_3_limit`: 21 credits (dean approval required)
- `cgpa_between_2_and_3_limit`: 18 credits
- `cgpa_between_1_and_2_limit`: 15 credits
- `cgpa_below_1_limit`: 12 credits
- `minimum_per_semester`: 9 credits (below this → advisor warning)
- `final_semester_override`: 21 credits (dean approval; regardless of CGPA)
- `incomplete_extra_course_allowed`: Whether student with Incomplete may register one extra course

### `SummerSemesterRules`
Governs Summer course count limits (not credit hours).
- `default_max_courses`: 2
- `cgpa_above_3_max_courses`: 3
- `cgpa_threshold_for_extra_course`: 3.0

### `GraduationRequirementRules`
Official graduation requirements.
- `total_credits_required`: 133
- `minimum_cgpa`: 2.0
- `minimum_regular_semesters`: 6
- `maximum_regular_semesters`: 16 (exceeded → dismissal)
- `must_pass_zero_credit_courses`: True
- `military_training_required_for_males`: True

### `AcademicWarningRules`
Governs warning issuance and dismissal.
- `cgpa_warning_threshold`: 2.0
- `max_consecutive_warnings`: 4 → dismissal
- `max_total_warnings`: 6 → dismissal
- `warning_exempt_first_semester`: True
- `dismissal_extension_credits_percentage`: 0.80 (must have passed 80% of total credits to appeal)
- `dismissal_extension_extra_semesters`: 2 (granted on successful appeal)
- `dismissal_extension_extra_summer_semesters`: 1

### `HonorsRules`
Honors degree eligibility criteria.
- `minimum_cgpa_throughout`: 3.0 (must never drop below this at any semester boundary)
- `minimum_semesters`: 6
- `maximum_semesters`: 8
- `no_f_grade_allowed`: True
- `no_disciplinary_penalties`: True (cannot be verified by system — always `cannot_verify`)

### `GradingScaleRules`
Maps between letter grades, percentages, and grade points.
- `letter_to_points`: `dict[str, float | None]` — `None` for P-grade (GPA-neutral)
- `percentage_to_letter`: ordered list of `PercentageRange` — scan top-down to find match

### `StudentLevelRules`
Credit hour thresholds for student academic level.
- `freshman_max_hours`: 26
- `sophomore_min_hours`/`sophomore_max_hours`: 27–59
- `junior_min_hours`/`junior_max_hours`: 60–93
- `senior_min_hours`/`senior_max_hours`: 94–133

---

## 5. Function Contracts

### A. `check_course_eligibility`

**File:** `engines/ale/functions/check_course_eligibility.py`  
**Input model:** `CheckCourseEligibilityInput`  
**Output model:** `CheckCourseEligibilityOutput`

**Purpose:** Determines whether a student is eligible to take or retake a specific course, based on prerequisites, credit threshold, retake rules, and current enrollment state.

**Key statuses:**
| Status | Meaning |
|---|---|
| `eligible` | Student may register for the course |
| `not_eligible` | One or more prerequisites or threshold not met |
| `in_progress` | Student is currently enrolled in this course |
| `already_completed` | Student has already passed this course (first-attempt guard) |
| `retake_cap_exceeded` | Student has used all allowed improve retakes for this course |
| `cannot_compute` | Required input data missing or invalid |

**Key reason codes:**
- `required_data_missing` — target_course_code, completed_courses, or attempt_type missing
- `invalid_attempt_type` — not one of first_attempt / failed_retake / improve_retake
- `missing_prerequisites` — one or more prereqs not in completed_courses or in_progress_courses
- `credit_threshold_not_met` — cumulative passed hours below required threshold
- `improve_retake_cap_exceeded` — student has used max improve retakes

**Logic notes:**
- `attempt_type` is `Literal["first_attempt", "failed_retake", "improve_retake"]` — validated by Pydantic and by a belt-and-suspenders guard inside the function.
- Prerequisites can be satisfied by in-progress enrollment (`in_progress_courses`), not just completed courses.
- Credit threshold is checked using `cumulative_passed_hours`, not total registered hours.
- The function does not invent a "max failed retake" count — there is no such cap in the handbook for failed retakes. Only improve retakes have a quota.
- `retake_count` per course is passed from StudentContext; the function does not count attempts itself.

**Tests:** `test_check_course_eligibility.py` (14 synthetic) + `test_check_course_eligibility_real_records.py` (5 real-record)

---

### B. `run_graduation_audit`

**File:** `engines/ale/functions/run_graduation_audit.py`  
**Input model:** `RunGraduationAuditInput`  
**Output model:** `RunGraduationAuditOutput`

**Purpose:** Audits whether a currently-studying student is eligible to graduate, given their official academic record and all graduation requirements. Also computes honors eligibility as a sub-result.

**Key statuses:**
| Status | Meaning |
|---|---|
| `eligible` | All graduation requirements are met |
| `not_eligible` | One or more requirements not yet met |
| `already_graduated` | `study_status == "Graduated"` — no audit needed |
| `not_auditable` | `study_status` is Transferred Out, Suspended, or Frozen |
| `dismissed_but_appeal_eligible` | Warning dismissal but student has passed ≥80% of required credits |
| `dismissed_no_appeal` | Warning dismissal, below credit threshold for appeal |
| `cannot_compute` | Required bundles or student data missing |

**Checks performed (when status = Studying):**
- `cgpa_check`: current CGPA ≥ minimum_cgpa
- `credits_check`: cumulative passed hours ≥ total_credits_required
- `semesters_check`: completed regular semesters ≥ minimum_regular_semesters
- `maximum_semesters_check`: completed regular semesters ≤ maximum_regular_semesters
- `military_check`: military_status ∈ {Done, Exempted} (males only, when toggle enabled)
- `zero_credit_check`: zero_credit_courses_passed = True (when toggle enabled)
- `warning_dismissal_check`: consecutive and total warnings within limits

**Honors sub-result** (always computed for studying students regardless of graduation eligibility):
- `cgpa_throughout`: CGPA never dropped below 3.0 at any semester boundary (uses `course_history`)
- `semester_count`: completed between min and max regular semesters
- `no_failures`: no F or Abs grade in transcript
- `no_disciplinary_penalties`: always `cannot_verify` (registrar-level data unavailable)

**Important notes:**
- Always operates on the **official** `StudentContext` — never what-if assumptions.
- `zero_credit_courses_passed` is a **boolean** in ALE input (pre-computed by ALEAdapter from the required list). ALEAdapter must receive `required_zero_credit_courses` from `kg_data` or `params` to compute this correctly.
- `course_history` is only needed for the honors sub-result (CGPA trajectory). Graduation eligibility uses the scalar fields directly.

**Tests:** `test_run_graduation_audit.py` (21 synthetic) + `test_run_graduation_audit_real_records.py` (3 real-record)

---

### C. `simulate_gpa_forward`

**File:** `engines/ale/functions/simulate_gpa_forward.py`  
**Input model:** `SimulateGPAForwardInput`  
**Output model:** `SimulateGPAForwardOutput`

**Purpose:** Simulates the CGPA impact of a hypothetical set of planned courses and grades. Respects footprint-replacement policy and retake grade caps.

**Key statuses:**
| Status | Meaning |
|---|---|
| `projected` | Simulation completed; `projected_cgpa` populated |
| `cannot_compute` | Missing required inputs or empty course list |

**Key reason codes:**
- `missing_gpa_counted_credits` — `cumulative_chs` is None
- `missing_current_quality_points` — `cumulative_cps` is None
- `no_planned_courses` — empty `planned_courses` list
- `invalid_input` — Pydantic validation failure

**GPA arithmetic:**
- **Footprint replacement** (`has_cgpa_footprint=True`): subtract old quality points from numerator; denominator unchanged; add new quality points. This is the CIS replacement policy confirmed by STU000009 anchor.
- **First-attempt addition** (`has_cgpa_footprint=False`): add quality points to numerator; add credits to denominator.
- **GPA-neutral courses**: P-grade or 0-credit courses — excluded from both numerator and denominator.

**Retake grade caps:**
- Failed retake: grade capped at `failed_first_retake_grade_cap` (B = 3.0).
- Improve retake first attempt: no cap.
- Improve retake subsequent: capped at `improve_retake_subsequent_cap` (B = 3.0).
- `grade_overrides` list in output records all applied caps.

**Zero-credit anomaly:** If `credits=0` and grade is not P, the function adds quality points to the numerator but no credits to the denominator, which changes CGPA non-intuitively. This is an edge-case to document to the student, not a bug.

**Carry-forward note (Orchestrator):** `credits = profile.get("credits") or 3` silently defaults 0-credit courses to 3. This must be fixed in the Orchestrator before zero-credit courses are included in GPA simulations.

**Tests:** `test_simulate_gpa_forward.py` (21 synthetic) + `test_simulate_gpa_forward_real_records.py` (3 real-record)

---

### D. `solve_target_gpa`

**File:** `engines/ale/functions/solve_target_gpa.py`  
**Input model:** `SolveTargetGPAInput`  
**Output model:** `SolveTargetGPAOutput`

**Purpose:** Computes the average grade points a student needs to achieve across a set of planned courses to reach a target CGPA. When the target is unreachable this semester, produces a multi-semester projection.

**Key statuses:**
| Status | Meaning |
|---|---|
| `already_met` | Current CGPA ≥ target — no action needed |
| `solvable` | Required average is achievable; `required_average_grade_points` populated |
| `impossible` | Required average exceeds 4.0 even in current semester; `multi_semester_projection` populated |
| `cannot_compute` | Missing required data |

**Key logic:**
- Footprint courses: same replacement policy as `simulate_gpa_forward`.
- **Cap-aware distribution:** The equal distribution is adjusted so no course's target exceeds its `max_grade_points` (the grade-points ceiling at its retake cap). Excess points from capped courses are redistributed iteratively to uncapped courses.
- **Multi-semester projection** (`status=impossible`): projects how many additional semesters at a given assumed grade are needed to reach the target. Uses `remaining_sems = max(0, maximum_regular_semesters - completed_regular_semesters)` when `completed_regular_semesters` is provided.
- **Personalized distribution:** Advisory heuristic only — not handbook policy. Lower-grade historical prerequisites pull the target down; higher-grade push it up. The personalized distribution may be invalid if it cannot satisfy the overall required average within caps.

**Carry-forward notes (Orchestrator):**
- `old_grade` must be set for footprint courses (improve/failed retake) in params.
- `improve_retake_number` must be computed correctly for each planned course.

**Tests:** `test_solve_target_gpa.py` (32 synthetic) + `test_solve_target_gpa_real_records.py` (4 real-record)

---

### E. `generate_semester_plan`

**File:** `engines/ale/functions/generate_semester_plan.py`  
**Input model:** `GenerateSemesterPlanInput`  
**Output model:** `GenerateSemesterPlanOutput`

**Purpose:** Generates up to three course plan variants for a single upcoming semester (Fall, Spring, or Summer), subject to eligibility filtering, credit/course-count caps, and retake prioritization.

**Product scope (by design):** Plans one semester only. Multi-semester planning uses `generate_graduation_roadmap`.

**Key statuses:**
| Status | Meaning |
|---|---|
| `plans_generated` | At least one plan variant produced |
| `no_eligible_courses` | Eligibility filtering left no takeable courses |
| `not_applicable` | Student not in Studying status |
| `cannot_compute` | Invalid input values or missing rule bundles |

**Regular semester logic (Fall/Spring):**
- Credit cap applied from `CreditLimitRules` based on current CGPA bracket.
- `target_credit_load` overrides the cap if provided (unless `max_credits_mode=True`).
- `max_credits_mode=True` always applies the maximum CGPA-bracket cap.
- Final-semester warning issued when `final_semester_override` credit cap would apply.
- Minimum credit warning issued when `target_credit_load < minimum_per_semester`.
- Course eligibility: all prerequisites in completed/in-progress; credit threshold met.

**Summer semester logic:**
- Uses `SummerSemesterRules.default_max_courses` (or higher for CGPA ≥ threshold).
- Course-count cap, not credit-hour cap.
- Minimum-credit check does NOT apply to Summer.

**Retake prioritization:** Failed courses (retakes of failing grades) are always prioritized in all plan variants.

**Plan variants:**
- Plan A: Recommended — up to CGPA-bracket max (or specified load).
- Plan B: Lighter Load — up to 12 credits (fixed). Omitted if fewer than 2 eligible courses.
- Plan C: Level Focused — courses from the student's own level only. Omitted if fewer than 2 same-level courses available.

**Strict validation:**
- `student_level` must be one of `Literal["Freshman", "Sophomore", "Junior", "Senior"]`. Invalid → `cannot_compute / invalid_student_level`.
- `target_semester_type` must be one of `Literal["Fall", "Spring", "Summer"]`. Invalid → `cannot_compute / invalid_target_semester_type`.
- Belt-and-suspenders guards inside the function provide additional safety even if Pydantic validation is bypassed.

**Tests:** `test_generate_semester_plan.py` (57 synthetic) + `test_generate_semester_plan_real_records.py` (9 real-record)

---

### F. `generate_graduation_roadmap`

**File:** `engines/ale/functions/generate_graduation_roadmap.py`  
**Input model:** `GenerateGraduationRoadmapInput`  
**Output model:** `GenerateGraduationRoadmapOutput`

**Purpose:** Simulates a complete multi-semester graduation path by iterating semester-by-semester from a given starting semester/year until all graduation requirements are met or a terminal stop condition is reached.

**Two operating modes:**

**Graduation mode (default):** Simulates until all graduation requirements are satisfied or a terminal condition is hit (max semesters, CGPA below minimum, warning dismissal, no eligible courses).

**Target-semester mode:** Simulates through a caller-specified end semester/year. Stops regardless of whether graduation requirements are met. Reports `target_reached_without_graduation=True` if the target semester passed before graduation.

**Key statuses:**
| Status | Meaning |
|---|---|
| `complete` | Graduation requirements projected to be met |
| `cannot_complete_projection` | Terminal stop condition reached before graduation |
| `blocked` | Non-course blocker prevents graduation (military, zero-credit) |
| `not_applicable` | Student not in Studying status |
| `cannot_compute` | Invalid input or missing required data |

**Key `cannot_compute` reason codes:**
- `invalid_student_level` — level not in 1–4 (caught by ALEAdapter before ALE call)
- `invalid_target_semester_type` — not Fall/Spring/Summer
- `invalid_starting_year` — outside 2000–2100
- `incomplete_target_end_semester` — only one of target_end_semester_type/target_end_year provided
- `target_end_before_start` — target end is before the starting semester

**`cannot_complete_projection` reason codes:**
- `max_semesters_reached` — `completed_regular_semesters + simulated_regular ≥ maximum_regular_semesters`
- `cgpa_below_minimum` — projected CGPA at graduation below 2.0
- `no_eligible_courses_in_pass` — no takeable courses in a given pass
- `projected_warning_limit_reached` — consecutive or total warning limit hit during simulation

**Non-course blockers** (appear in `non_course_blockers`, not as course-selection gaps):
- `military_training_required` — male student with military status not Done/Exempted (only when `military_training_required_for_males=True`)
- `zero_credit_courses_required` — zero-credit requirement not satisfied (only when `must_pass_zero_credit_courses=True`)

**GPA simulation during roadmap:**
- Each semester pass simulates the GPA impact of selected courses at `assumed_grade_per_pass` (pre-resolved to grade points by adapter; defaults to C+ = 2.6 inside ALE when None).
- Failed-retake courses: grade capped at `failed_retake_grade_cap_points` (pre-resolved by adapter from `retake_rules.failed_first_retake_grade_cap`).

**Warning projection:**
- Tracks `sim_consecutive_warnings` and `sim_total_warnings` across regular passes.
- Summer passes do not trigger or reset warnings.
- When projected warnings hit dismissal threshold → `cannot_complete_projection / projected_warning_limit_reached`.
- Output includes `projected_consecutive_warnings`, `projected_total_warnings`, `warning_limit_reached_in_semester`.

**Max-semester enforcement:**
- `completed_regular_semesters + simulated_regular_passes ≥ maximum_regular_semesters` → stop.

**In-progress course absorption:**
- In-progress courses are treated as the first pass (semester 0) — their credits are counted toward graduation requirements, but no new courses are planned for that pass.

**Sequence logic:**
- Regular mode: alternates Fall ↔ Spring; skips Summer unless `accelerated_mode=True`.
- Accelerated mode: includes Summer semesters in the sequence.
- `_semester_key(season, year)` helper provides chronological ordering using academic-year convention (Fall YYYY → `(YYYY, 0)`, Spring YYYY → `(YYYY-1, 1)`, Summer YYYY → `(YYYY-1, 2)`).

**Tests:** `test_generate_graduation_roadmap.py` (66 synthetic) + `test_generate_graduation_roadmap_real_records.py` (9 real-record, 1 skipped)

---

## 6. Shared Utility: `grade_resolver.py`

**File:** `engines/ale/utils/grade_resolver.py`

**Purpose:** Converts any supported grade input format into grade points (0.0–4.0) or `None` (for GPA-neutral P-grade).

**Accepted formats:**
- **Letter grade**: `"A"`, `"B+"`, `"C-"`, `"D"`, `"F"`, `"P"`, `"Abs"`
- **Numeric grade points**: `3.7`, `2.0`, `0.0` (float or string-encoded)
- **Percentage**: `90.0`, `77`, `60` (range 4.0–100.0 mapped via `percentage_to_letter` ranges)

**Normalization:**
- Whitespace stripped.
- Numeric strings (`"3.7"`, `"90"`) parsed as float and routed to numeric resolver.
- `"abs"` / `"ABS"` / `"Abs"` → normalized to `"Abs"` before grading-scale lookup.
- All other strings uppercased (`"b+"` → `"B+"`).

**Returns:**
- `float` (0.0–4.0) for all valid grade inputs that affect GPA.
- `None` for P-grade (GPA-neutral; caller excludes from all GPA math).

**Raises:** `GradeResolutionError(course_code, grade_input)` for:
- Unrecognized letter grade.
- Numeric value outside 0.0–100.0 range.
- Non-finite float (`NaN`, `inf`).
- Empty or whitespace-only string.
- Percentage value not matched by any configured range.

**`derive_level(passed_hours, rules)` helper:**
Derives the student's academic level string from passed credit hours using injected `StudentLevelRules`. Never hardcodes the hour boundaries.

**Tests:** `test_grade_resolver.py` (43 tests)

---

## 7. ALEAdapter Contract

**File:** `adapters/ale_adapter.py`

### Public interface

```python
call(
    operation: str,
    student_context: StudentContext,
    rule_bundles: dict,
    kg_data: dict | None = None,
    params: dict | None = None,
) -> dict
```

### Supported operations

| Operation | ALE Function |
|---|---|
| `simulate_gpa_forward` | `simulate_gpa_forward()` |
| `solve_target_gpa` | `solve_target_gpa()` |
| `check_course_eligibility` | `check_course_eligibility()` |
| `run_graduation_audit` | `run_graduation_audit()` |
| `generate_semester_plan` | `generate_semester_plan()` |
| `generate_graduation_roadmap` | `generate_graduation_roadmap()` |

### Rule bundle parsing

Each operation method calls `_parse_rules(rule_bundles, key, ModelClass)` for each required bundle. If the key is missing or the dict fails Pydantic validation, `_parse_rules` logs a warning and raises `ValueError` → caller's `call()` returns `{"status": "error", ...}`.

### StudentContext mapping

Key field mappings:

| `StudentContext` field | ALE input field | Notes |
|---|---|---|
| `cgpa` | `current_cgpa` | |
| `cumulative_chs` | `gpa_counted_credits` | GPA denominator |
| `cumulative_cps` | `current_quality_points` | GPA numerator |
| `total_credit_hours_earned` | `cumulative_passed_hours` | Passed PHs only |
| `level` (int 1–4) | `student_level` (str) | Mapped via `_map_student_level()` |
| `zero_credit_courses_passed` (list) | `zero_credit_courses_passed` (bool) | Computed via `_compute_zero_credit_requirement_passed()` |
| `course_history` (list[CourseRecord]) | `course_history` (list[CourseHistoryEntry]) | Mapped via `_map_course_history()` |

### Student level mapping (`_map_student_level`)

Maps `StudentContext.level` (integer 1–4) to ALE string level:
- `1 → "Freshman"`, `2 → "Sophomore"`, `3 → "Junior"`, `4 → "Senior"`
- Invalid level → `None` → adapter returns `cannot_compute / invalid_student_level`
- **No silent fallback to Freshman** — invalid levels must be diagnosed, not silently promoted.

### Zero-credit requirement mapping (`_compute_zero_credit_requirement_passed`)

`StudentContext.zero_credit_courses_passed` is a **list of passed course codes** — not a boolean. A non-empty list does NOT imply all zero-credit requirements are satisfied.

The adapter computes:
```python
set(required_zero_credit_courses).issubset(set(sc.zero_credit_courses_passed))
```

The required course list must be provided by the Orchestrator via `kg_data["required_zero_credit_courses"]` or `params["required_zero_credit_courses"]`.

If not provided → `cannot_compute / missing_required_zero_credit_course_list` for `run_graduation_audit` and `generate_graduation_roadmap`.

Semester planning (`generate_semester_plan`) does **not** require this — it plans courses without a graduation-readiness gate.

### Available course mapping (`_map_available_courses`)

Maps `kg_data["available_courses"]` to `list[AvailableCourse]`. Handles:
- `track` as dict `{track_id, name}` → converted to `[track_id]` list
- `track` as list → used directly
- `semester_offering` as list or scalar → always a list

Malformed course (missing required fields like `credits`, `level`) → `ValidationError` → `call()` maps to `cannot_compute / invalid_input`.

### Error taxonomy

| Condition | Status | Source |
|---|---|---|
| `ValidationError` (bad input model) | `cannot_compute / invalid_input` | Pydantic validation failure at ALE input construction |
| Missing/invalid rule bundle | `error` | `_parse_rules()` raises `ValueError` |
| Unknown operation name | `error` | `_dispatch()` raises `ValueError` |
| Invalid student level | `cannot_compute / invalid_student_level` | `_map_student_level()` returns None |
| Missing zero-credit list | `cannot_compute / missing_required_zero_credit_course_list` | `_compute_zero_credit_requirement_passed()` returns None |
| Unexpected exception | `error` | Outer `except Exception` handler |
| Invalid assumed grade in roadmap | `cannot_compute / invalid_assumed_grade` | `GradeResolutionError` in grade resolution |

### Logging behavior

ALEAdapter logs:
- **Start**: `ALEAdapter.call start operation=X params={safe_summary} kg_data={safe_summary}` — `INFO`
- **Result**: `ALEAdapter.call result operation=X status=X summary={...} duration_ms=N` — `INFO` on success, `WARNING` on cannot_compute or error
- **Rule bundle failures**: `ALEAdapter: rule bundle missing or invalid: 'key_name'` — `WARNING`
- **Invalid level**: `ALEAdapter.{op}: invalid student level=N (expected 1–4)` — `WARNING`
- **Missing zero-credit list**: `ALEAdapter.{op}: required_zero_credit_courses not provided ...` — `WARNING`
- **ValidationError**: `ALEAdapter.call operation=X: input validation failed ...` — `ERROR`
- **ValueError / unexpected**: `ALEAdapter.call operation=X: ...` — `ERROR`

What is never logged:
- Student names, student IDs
- Full transcript or course_history dumps
- Full grade lists or quality points arrays
- Full planned_courses or available_courses payloads
- Raw StudentContext objects

---

## 8. Testing Summary

### ALE core function tests

```
pytest engines/ale/tests/ -v
```

| Test file | Synthetic | Real-record | Total |
|---|---|---|---|
| `test_check_course_eligibility.py` | 14 | — | 14 |
| `test_check_course_eligibility_real_records.py` | — | 5 | 5 |
| `test_run_graduation_audit.py` | 21 | — | 21 |
| `test_run_graduation_audit_real_records.py` | — | 3 | 3 |
| `test_simulate_gpa_forward.py` | 21 | — | 21 |
| `test_simulate_gpa_forward_real_records.py` | — | 3 | 3 |
| `test_solve_target_gpa.py` | 32 | — | 32 |
| `test_solve_target_gpa_real_records.py` | — | 4 | 4 |
| `test_generate_semester_plan.py` | 57 | — | 57 |
| `test_generate_semester_plan_real_records.py` | — | 9 | 9 |
| `test_generate_graduation_roadmap.py` | 66 | — | 66 |
| `test_generate_graduation_roadmap_real_records.py` | — | 9 | 9 |
| `test_grade_resolver.py` | 43 | — | 43 |
| **Total** | **254** | **33** | **287** |

**Latest result:** `284 passed, 5 skipped` (5 skips = pre-existing "no graduated student in dataset" skips)

### ALEAdapter focused tests

```
pytest tests/test_ale_adapter.py -v
```

59 tests across 9 groups:
- A: Public dispatch (3)
- B: Student level mapping (7)
- C: Zero-credit requirement mapping (10)
- D: Rule bundle handling (5)
- E: Available course mapping (7)
- F: Roadmap mapping (6)
- G: Semester plan mapping (5)
- H: GPA operation mapping (5)
- I: Eligibility mapping (6)

**Latest result:** `59 passed`

### ALEAdapter smoke tests

```
pytest tests/smoke_test_ale_adapter.py -v
```

15 tests covering all 6 operations end-to-end with semantic assertions.

**Latest result:** `15 passed`

### Combined total

```
358 passed, 5 skipped, 0 failed
```

---

## 9. Logging Summary

### What is logged

| Event | Level | Content |
|---|---|---|
| Operation start | INFO | operation name, safe param summary, safe kg_data summary |
| Operation success | INFO | operation, status, result counts, key scalars, duration_ms |
| cannot_compute result | WARNING | operation, status, reason_codes, duration_ms |
| error result | WARNING | operation, status, duration_ms |
| ValidationError | ERROR | operation, validation error message |
| Missing rule bundle | WARNING | bundle key name only |
| Invalid student level | WARNING | raw level value only |
| Missing zero-credit list | WARNING | operation context only |

### What is never logged

- Student names or student IDs (PII)
- Full course_history, transcript dumps
- Full grade arrays, quality points sequences
- Full planned_courses or available_courses lists (counts + 3-item preview only)
- Raw StudentContext objects
- Academic record values that could identify a specific student

### Safe param summarizer

`_summarize_params(params)` logs only keys from `_SAFE_SCALAR_PARAM_KEYS`:
```
target_course_code, attempt_type, target_cgpa, target_semester_type,
starting_year, target_end_semester_type, target_end_year,
accelerated_mode, max_credits_mode, target_credit_load, planned_course_source
```

List params are summarized as `{count: N, preview: [first 3 codes]}`.

### Safe kg_data summarizer

`_summarize_kg_data(kg_data)` logs:
- `available_courses`: `{count: N, preview: [first 3 codes]}`
- `required_zero_credit_courses`: `{count: N, values: [...]}`
- `course_credit_lookup`: `{count: N}` (keys only)
- `course_prerequisites`: `{count: N, preview: [first 3 codes]}`
- `course_credit_threshold`: scalar

---

## 10. Carry-Forward Integration Register

These items are confirmed non-ALE issues that must be addressed in later Phase 1 steps.

| # | Item | Target step |
|---|---|---|
| ALE-A | Orchestrator must pass `required_zero_credit_courses` in `kg_data` for `run_graduation_audit` and `generate_graduation_roadmap` calls | Step 7 — Orchestrator audit |
| ALE-B | Orchestrator `credits = profile.get("credits") or 3` silently defaults 0-credit courses to 3 — wrong for GPA math | Step 7 — Orchestrator audit |
| ALE-C | `old_grade` not set for target-GPA footprint courses in Orchestrator → `solve_target_gpa` returns `cannot_compute` for retake GPA queries | Step 7 — Orchestrator audit |
| ALE-D | `improve_retake_number` not computed for `PlannedCourseTarget` in Orchestrator | Step 7 — Orchestrator audit |
| ALE-E | Orchestrator must build complete `available_courses` pool for semester plan and roadmap (all track courses, not just a subset) | Step 7 — Orchestrator audit |
| ALE-F | Orchestrator must handle Summer target-end validation and accelerated mode selection correctly | Step 7 — Orchestrator audit |
| ALE-G | SCP `current_semester` inference needs final audit (used for starting_year derivation in roadmap) | Step 4 — SCP audit |
| ALE-H | Track ID normalization must be consistent across SCP/QU/KG/Orchestrator before roadmap `official_track` can be trusted | Step 7 — Orchestrator audit |
| ALE-I | Composer must display readable course names (not just codes) and frame GPA projections as advisory simulations, not official registration plans | Step 8 — Composer audit |
