# ALE Integration Contract

This document is the authoritative handoff reference for every component that feeds the Academic Logic Engine (ALE). It defines what the ALE expects, what it returns, and who is responsible for providing each piece of data.

---

## Section 1 — Overview

The Academic Logic Engine performs deterministic rule-based computations over student academic state. It contains no LLM calls and no external I/O. Given validated inputs, it produces structured outputs that the Response Composer uses to generate a student-facing reply.

### 6 Operations

| Operation | Purpose |
|---|---|
| `simulate_gpa_forward` | Project hypothetical CGPA given a set of planned courses with assumed grades |
| `solve_target_gpa` | Determine what grades are needed across planned courses to reach a target CGPA |
| `check_course_eligibility` | Determine whether a student may register for or retake a specific course |
| `run_graduation_audit` | Check all graduation requirements and compute honors eligibility |
| `generate_semester_plan` | Generate up to three prioritised course plans for a single upcoming semester |
| `generate_graduation_roadmap` | Project a multi-semester simulation from current state to graduation |

### Three Data Sources

| Source | Provides | Who calls it |
|---|---|---|
| Student Context Provider (SCP) | `StudentContext` — all student academic state | Orchestrator (before ALE) |
| RAG Engine | Rule bundles — all handbook policy values | Orchestrator (before ALE, cache per session) |
| KG Engine | Course data — prerequisites, credit thresholds, curriculum | Orchestrator (before ALE, per operation) |

**The adapter is the only entry point.** No component may call ALE function modules directly. All calls go through `ale_adapter.call()`.

---

## Section 2 — Adapter Calling Convention

### Python Signature

```python
ale_adapter.call(operation, student_context, rule_bundles, kg_data={}, params={})
```

| Argument | Type | Description |
|---|---|---|
| `operation` | `str` | One of the 6 operation names (see Section 1) |
| `student_context` | `StudentContext` | Full student context object from SCP |
| `rule_bundles` | `dict` | Keyed rule dicts from RAG (see Section 4) |
| `kg_data` | `dict` | Course data from KG (see Section 5). Default `{}`. |
| `params` | `dict` | Operation-specific parameters. Default `{}`. |

### Return Value

Always a plain `dict`. Always contains a `"status"` key. On error:

```python
{"status": "error", "message": "<exception message>", "operation": "<operation name>"}
```

### Per-Operation kg_data and params Requirements

| Operation | kg_data keys | params keys |
|---|---|---|
| `simulate_gpa_forward` | — | `planned_courses` (required), `excluded_in_progress_courses` (optional, default `[]`) |
| `solve_target_gpa` | — | `target_cgpa` (required), `planned_courses` (required), `assumed_grade_per_semester` (optional), `credits_per_semester` (optional, default 18), `planned_course_source` (optional, default `"orchestrator"`) |
| `check_course_eligibility` | `course_prerequisites` (required), `course_credit_threshold` (optional) | `target_course_code` (required), `attempt_type` (required) |
| `run_graduation_audit` | — | — |
| `generate_semester_plan` | `available_courses` (required) | `target_semester_type` (required), `target_credit_load` (optional), `max_credits_mode` (optional, default `False`), `specialization_credit_threshold` (optional, default 60), `target_track` (optional) |
| `generate_graduation_roadmap` | `available_courses` (required) | `target_semester_type` (required), `starting_year` (required), `assumed_grade_per_pass` (optional), `accelerated_mode` (optional, default `False`), `max_credits_mode` (optional, default `False`), `target_credit_load` (optional), `specialization_credit_threshold` (optional, default 60), `target_track` (optional) |

---

## Section 3 — Student Context Provider Contract

The following table lists every `StudentContext` field consumed by ALE, the ALE input field it maps to, and which operations use it. Fields are populated by the Student Context Provider from registrar data.

### Field Mapping Table

| StudentContext field | ALE input field | Operations |
|---|---|---|
| `cgpa` | `current_cgpa` | all 6 |
| `cumulative_chs` | `gpa_counted_credits` | simulate_gpa_forward, solve_target_gpa, generate_graduation_roadmap |
| `cumulative_cps` | `current_quality_points` | simulate_gpa_forward, solve_target_gpa, generate_graduation_roadmap |
| `total_credit_hours_earned` | `cumulative_passed_hours` | all 6 |
| `level` | `student_level` (mapped: 1→Freshman, 2→Sophomore, 3→Junior, 4→Senior) | generate_semester_plan, generate_graduation_roadmap |
| `track_id` | `official_track` | generate_semester_plan, generate_graduation_roadmap |
| `study_status` | `study_status` | run_graduation_audit, generate_semester_plan, generate_graduation_roadmap |
| `completed_courses` | `completed_courses` | check_course_eligibility, run_graduation_audit, generate_semester_plan, generate_graduation_roadmap |
| `failed_courses` | `failed_courses` | run_graduation_audit, generate_semester_plan, generate_graduation_roadmap |
| `in_progress_courses` | `in_progress_courses` | simulate_gpa_forward, solve_target_gpa, check_course_eligibility, run_graduation_audit, generate_semester_plan, generate_graduation_roadmap |
| `consecutive_warnings` | `consecutive_warnings` | run_graduation_audit |
| `total_warnings` | `total_warnings` | run_graduation_audit |
| `military_status` | `military_status` | run_graduation_audit, generate_graduation_roadmap |
| `course_history` | `course_history` (via `_map_course_history()`) | run_graduation_audit, generate_semester_plan (incomplete flag derivation), generate_graduation_roadmap (same) |

### New Fields (Added for ALE — Safe Defaults)

These four fields were added to `StudentContext` with safe defaults. They default to values that will not break existing queries but will produce conservative results until the SCP is rebuilt to compute them properly.

#### `completed_regular_semesters: int = 0`

**Used by:** `run_graduation_audit`, `generate_graduation_roadmap`

**Semantics:** Count of distinct Fall and Spring semesters in which the student had at least one registered course. Summer semesters are excluded.

**How to compute:** Scan `course_history`, extract unique semester labels that contain "Fall" or "Spring", count distinct values.

**Current default risk:** `run_graduation_audit` will always pass the minimum-semester check (6 semesters) as long as `completed_regular_semesters` = 0 is treated as "not enough". Defaulting to 0 is safe — it fails the check, never falsely reports eligibility.

---

#### `zero_credit_courses_passed: bool = False`

**Used by:** `run_graduation_audit`

**Semantics:** `True` only if all required 0-credit courses in the student's programme have been passed.

**How to compute:** Requires knowing which courses are 0-credit — this comes from KG. The orchestrator must cross-reference the student's `completed_courses` against the set of 0-credit courses returned by KG and pre-compute this flag before calling ALE.

**Current default:** `False` — the audit will report `zero_credit_check` as not passed. This is safe (conservative).

---

#### `retake_count: dict[str, int] = {}`

**Used by:** `check_course_eligibility`

**Semantics:** Maps course code → number of times the student has attempted that course (all attempts, including the current registration if in-progress).

**How to compute:** Scan `course_history`, group by `course_code`, count rows per group. Must be computed from raw registration data before status mapping collapses individual attempt tags.

**Current default:** `{}` — all courses appear to have 0 prior attempts. This will never incorrectly block a student but will not enforce retake limits.

---

#### `total_improve_retakes_used: int = 0`

**Used by:** `check_course_eligibility`

**Semantics:** Lifetime count of improve-retake attempts used by the student across all courses. A student with CGPA ≥ 2.0 may only use 3 improve retakes total.

**How to compute:** Must be derived from raw registration tags (the "improve retake" tag) before status mapping collapses the distinction between regular attempts and improve retakes. Once the status is mapped to `passed`/`failed`, the information is lost.

**Current default:** `0` — no improve retake limit is enforced. Safe but permissive.

---

### Known Gap — Credit Hours Hardcoded to 3

The SCP currently assumes `credit_hours = 3` for all courses when building `course_history`. This means the `credits` field on each `CourseHistoryEntry` passed to `run_graduation_audit` is always 3, regardless of the actual course credit weight.

**Impact:** The honors CGPA trajectory computation in `run_graduation_audit` will produce inaccurate results for any student whose history includes courses with credits ≠ 3 (e.g. 1-credit labs, 4-credit courses). GPA simulation operations (`simulate_gpa_forward`, `solve_target_gpa`) use `planned_courses` credits provided directly by the orchestrator and are not affected by this bug.

**Fix:** Real credit hour values must come from KG. The SCP must call `get_course_profile(course_code)` (or a batch equivalent) to resolve actual credits for each course in `course_history`.

---

## Section 4 — RAG Engine Contract (Rule Bundles)

The orchestrator must call the RAG engine to obtain structured rule bundles and pass them as the `rule_bundles` dict to `ale_adapter.call()`.

**Caching:** Rule bundles change only when the academic handbook changes. The orchestrator should cache the full `rule_bundles` dict per session (TTL: per session). Re-fetching on every call is unnecessary overhead.

**Current status:** `RAGAdapter.get_rule_bundles()` currently returns a `not_implemented` error. The RAG developer must implement structured extraction that returns the format below.

---

### `grading_scale`

```python
{
    "letter_to_points": {
        "A+": 4.0,
        "A":  4.0,
        "A-": 3.7,
        "B+": 3.3,
        "B":  3.0,
        "B-": 2.7,
        "C+": 2.3,
        "C":  2.0,
        "C-": 1.7,
        "D+": 1.3,
        "D":  1.0,
        "F":  0.0,
        "P":  None
    },
    "percentage_to_letter": [
        {"min_pct": 97, "max_pct": 100, "letter": "A+"},
        {"min_pct": 93, "max_pct":  96, "letter": "A"},
        {"min_pct": 90, "max_pct":  92, "letter": "A-"},
        {"min_pct": 87, "max_pct":  89, "letter": "B+"},
        {"min_pct": 83, "max_pct":  86, "letter": "B"},
        {"min_pct": 80, "max_pct":  82, "letter": "B-"},
        {"min_pct": 77, "max_pct":  79, "letter": "C+"},
        {"min_pct": 73, "max_pct":  76, "letter": "C"},
        {"min_pct": 70, "max_pct":  72, "letter": "C-"},
        {"min_pct": 67, "max_pct":  69, "letter": "D+"},
        {"min_pct": 60, "max_pct":  66, "letter": "D"},
        {"min_pct":  0, "max_pct":  59, "letter": "F"}
    ]
}
```

---

### `retake_rules`

```python
{
    "failed_first_retake_grade_cap":           "B",
    "improve_retake_first_attempt_cap":        None,
    "improve_retake_subsequent_cap":           "B",
    "improve_retake_max_courses_cgpa_above_2": 3,
    "improve_retake_unlimited_below_cgpa":     2.0
}
```

---

### `graduation_rules`

```python
{
    "total_credits_required":        133,
    "minimum_cgpa":                  2.0,
    "minimum_regular_semesters":     6,
    "maximum_regular_semesters":     16,
    "must_pass_zero_credit_courses": True,
    "military_training_required_for_males": True
}
```

---

### `warning_rules`

```python
{
    "cgpa_warning_threshold":                    2.0,
    "max_consecutive_warnings":                  4,
    "max_total_warnings":                        6,
    "warning_exempt_first_semester":             True,
    "dismissal_extension_credits_percentage":    0.80,
    "dismissal_extension_extra_semesters":       2,
    "dismissal_extension_extra_summer_semesters": 1
}
```

---

### `honors_rules`

```python
{
    "minimum_cgpa_throughout":   3.0,
    "minimum_semesters":         6,
    "maximum_semesters":         8,
    "no_f_grade_allowed":        True,
    "no_disciplinary_penalties": True
}
```

---

### `credit_limit_rules`

```python
{
    "cgpa_above_3_limit":            21,
    "cgpa_between_2_and_3_limit":    18,
    "cgpa_between_1_and_2_limit":    15,
    "cgpa_below_1_limit":            12,
    "minimum_per_semester":           9,
    "final_semester_override":        21,
    "incomplete_extra_course_allowed": True
}
```

---

### `summer_rules`

```python
{
    "default_max_courses":           2,
    "cgpa_above_3_max_courses":      3,
    "cgpa_threshold_for_extra_course": 3.0
}
```

`summer_rules` is optional. The adapter passes `None` to the ALE if the key is absent from `rule_bundles`. The ALE will return `cannot_compute` with `reason_code: missing_summer_rules` if a Summer plan is requested without summer rules.

---

### `student_level_rules`

```python
{
    "freshman_max_hours":  26,
    "sophomore_min_hours": 27,
    "sophomore_max_hours": 59,
    "junior_min_hours":    60,
    "junior_max_hours":    93,
    "senior_min_hours":    94,
    "senior_max_hours":    133
}
```

Source: CIS Handbook — Student Level classification by passed credit hours.
Required by: `generate_semester_plan`, `generate_graduation_roadmap`.

---

## Section 5 — KG Engine Contract

The orchestrator must call the KG engine before calling ALE for operations that require course data. The KG is the authoritative source of course structure; the ALE never calls KG directly.

### `get_course_profile(course_code)`

**Used before:** `check_course_eligibility`

The orchestrator calls this once for the target course and passes the results in `kg_data`.

**ALE expects from the result:**

| kg_data key | Type | Description |
|---|---|---|
| `course_prerequisites` | `list[str]` | Direct prerequisite course codes |
| `course_credit_threshold` | `int \| None` | Minimum passed credit hours required as an additional prerequisite, if any |

---

### `get_courses_by_track(track_id)`

**Used before:** `generate_semester_plan`, `generate_graduation_roadmap`

**Current status: IMPLEMENTED in KG engine and mirrored in the integration codebase.**

The orchestrator calls this once and passes the result list as `kg_data["available_courses"]`.

**Required return format** — list of dicts, each containing:

```python
{
    "course_code":        str,          # e.g. "CS101"
    "name":               str,          # full course name
    "credits":            int,          # actual credit hours (never hardcode 3)
    "level":              int,          # 1 (Freshman) | 2 (Sophomore) | 3 (Junior) | 4 (Senior)
    "prerequisites":      list[str],    # direct prerequisite course codes
    "semester_offering":  list[str],    # e.g. ["Fall", "Spring"] | ["Summer"] | ["Fall"]
    "track":              dict,          # {track_id: str, name: str}
    "credit_threshold":   int | None    # min passed hours prerequisite, if any
}
```

> **Note:** The ALE adapter normalises the raw KG output before constructing `AvailableCourse` objects. Specifically:
> - `course_code` key is passed through directly.
> - `semester_offering` (`list[str]`) is collapsed to a single string by the adapter: `["Fall","Spring"]` → `"Both"`, `["Summer"]` → `"Summer"`, `["Fall"]` → `"Fall"`, `["Spring"]` → `"Spring"`.
> - `track` (`dict`) is reduced to `track["track_id"]` (plain string).
>
> KG developers should return the raw format above. The adapter handles the rest.

**Important requirements:**
- Include **all** courses in the track, including 0-credit courses and summer-only courses. The ALE handles filtering internally based on `semester_offering` and `credits`.
- The `semester_offering` field drives which passes a course appears in during roadmap simulation. A course not listed for a given semester type will be skipped in that pass.
- `credits` must reflect actual values from the course catalogue. Do not default to 3.

---

## Section 6 — Orchestrator Responsibilities Per Operation

For each operation, the orchestrator must assemble the following before calling `ale_adapter.call()`.

### `simulate_gpa_forward`

| Step | Action |
|---|---|
| KG call | None required |
| rule_bundles keys | `grading_scale`, `retake_rules` |
| params | `planned_courses`: list of `PlannedCourseGPA`-compatible dicts (course_code, course_name, credits, expected_grade, attempt_type, has_cgpa_footprint, old_grade, improve_retake_number, is_currently_in_progress). `excluded_in_progress_courses`: list of course codes silently excluded from snapshot (optional). |

---

### `solve_target_gpa`

| Step | Action |
|---|---|
| KG call | Optional: `get_course_profile()` for each planned course to populate `related_completed_course` and `historical_grade` for personalized distribution |
| rule_bundles keys | `grading_scale`, `retake_rules`, `graduation_rules` |
| params | `target_cgpa` (required). `planned_courses`: list of `PlannedCourseTarget`-compatible dicts. `assumed_grade_per_semester`, `credits_per_semester`, `planned_course_source` (all optional). |

---

### `check_course_eligibility`

| Step | Action |
|---|---|
| KG call | `get_course_profile(target_course_code)` → extract `course_prerequisites` and `course_credit_threshold` into `kg_data` |
| rule_bundles keys | `retake_rules` |
| params | `target_course_code` (required), `attempt_type` (required: `first_attempt` \| `failed_retake` \| `improve_retake`) |

---

### `run_graduation_audit`

| Step | Action |
|---|---|
| KG call | None required (zero_credit_courses_passed is pre-computed by SCP or orchestrator from KG data separately) |
| rule_bundles keys | `graduation_rules`, `warning_rules`, `honors_rules` |
| params | None required |

---

### `generate_semester_plan`

| Step | Action |
|---|---|
| KG call | `get_courses_by_track(student_context.track_id)` → pass result list as `kg_data["available_courses"]` |
| rule_bundles keys | `credit_limit_rules`, `graduation_rules`, `retake_rules`, `student_level_rules`, `summer_rules` (required only when `target_semester_type == "Summer"`) |
| params | `target_semester_type` (required). `target_credit_load`, `max_credits_mode`, `specialization_credit_threshold`, `target_track` (all optional). |

---

### `generate_graduation_roadmap`

| Step | Action |
|---|---|
| KG call | `get_courses_by_track(student_context.track_id)` → pass result list as `kg_data["available_courses"]`. Exclude Field Training or other non-standard entries as needed. |
| rule_bundles keys | `grading_scale`, `retake_rules`, `graduation_rules`, `warning_rules`, `honors_rules`, `credit_limit_rules`, `student_level_rules`, `summer_rules` (required only when `accelerated_mode=True` or `target_semester_type == "Summer"`) |
| params | `target_semester_type` (required), `starting_year` (required, calendar year of first pass). `assumed_grade_per_pass`, `accelerated_mode`, `max_credits_mode`, `target_credit_load`, `specialization_credit_threshold`, `target_track` (all optional). |

---

## Section 7 — ALE Output Status Values

The Response Composer must handle every status value listed below. No other status values are produced by the current ALE implementation.

### `simulate_gpa_forward`

| Status | Meaning |
|---|---|
| `projected` | GPA simulation completed. `projected_cgpa` and `delta` are populated. |
| `cannot_compute` | Missing or invalid input (see `reason_codes` and `required_data_missing`). No projection produced. |

### `solve_target_gpa`

| Status | Meaning |
|---|---|
| `solvable` | Target is reachable in this semester. `required_average_grade_points` and distributions are populated. |
| `impossible` | Target cannot be reached this semester given the planned courses and retake caps. `maximum_reachable_cgpa` and `multi_semester_projection` are populated. |
| `already_met` | Student already meets the target CGPA. No action required. |
| `cannot_compute` | Missing or invalid input. |

### `check_course_eligibility`

| Status | Meaning |
|---|---|
| `eligible` | Student may register for the course. |
| `not_eligible` | One or more prerequisites or credit thresholds not met. See `missing_prerequisites` and `credit_threshold_met`. |
| `already_completed` | Student has already passed this course and `attempt_type` is not `improve_retake`. |
| `in_progress` | Student is currently registered for this course. |
| `retake_cap_exceeded` | Student has used all available improve-retake slots. |
| `cannot_compute` | Missing or invalid input, or contradictory attempt_type vs. course history. |

### `run_graduation_audit`

| Status | Meaning |
|---|---|
| `eligible` | All graduation requirements met. Student may apply to graduate. |
| `not_eligible` | One or more requirements not met. See `checks` and `next_steps`. |
| `already_graduated` | Student has already graduated. Honors computation is still returned. |
| `not_auditable` | Student is Transferred Out, Suspended, or Frozen. No audit performed. |
| `dismissed_but_appeal_eligible` | Student is dismissed but has passed ≥80% of required credits — appeal may be available. |
| `dismissed_no_appeal` | Student is dismissed and does not meet the appeal credit threshold. |
| `cannot_compute` | Missing required input fields. |

### `generate_semester_plan`

| Status | Meaning |
|---|---|
| `plans_generated` | At least one plan was produced. `plans` list is populated. |
| `no_eligible_courses` | No courses passed eligibility filtering. See `ineligibility_summary`. |
| `not_applicable` | Student is not in `Studying` status (Graduated, Suspended, etc.). |
| `cannot_compute` | Missing required input or summer plan requested without summer rules. |

### `generate_graduation_roadmap`

| Status | Meaning |
|---|---|
| `complete` | Full roadmap projected to graduation. `semester_plans` and `projected_graduation_semester` are populated. |
| `cannot_complete_projection` | Simulation hit the maximum regular semester limit (16) before graduation requirements were met. |
| `blocked` | No eligible courses found in a pass, or no courses fit the credit cap. Roadmap is partial. |
| `not_applicable` | Student is not in `Studying` status. |
| `cannot_compute` | Missing required input, summer plan requested without summer rules, or internal loop guard triggered. |
