"""
ale/functions/generate_semester_plan.py
========================================
Implements generate_semester_plan following ALE_Step5_Algorithms.md Function 5,
phase by phase.

No grade resolution, no GPA math. Pure eligibility and priority scoring.
"""

from dataclasses import dataclass

from engines.ale.schemas import (
    AvailableCourse,
    CreditLimitRules,
    GenerateSemesterPlanInput,
    GenerateSemesterPlanOutput,
    GraduationRequirementRules,
    PlannedCourse,
    SemesterPlan,
    SummerSemesterRules,
)

# CGPA bracket boundaries — inherent to CreditLimitRules field structure
_CGPA_HIGH = 3.0
_CGPA_MID  = 2.0
_CGPA_LOW  = 1.0

# System-design algorithm constants — ALE_Step5_Algorithms.md §Function5 Phase5/6
_HIGH_UNLOCK_THRESHOLD     = 3   # unlock_score >= 3 earns "high_unlock" priority
_PLAN_B_MAX_CREDITS        = 12  # Lighter Load fixed credit cap
_MIN_LEVEL_FOCUSED_COURSES = 2   # Plan C requires at least this many same-level courses

_LEVEL_MAP = {"Freshman": 1, "Sophomore": 2, "Junior": 3, "Senior": 4}


def _is_offered_in_semester(semester_offering: list, target_semester_type: str) -> bool:
    """Return True when the course should be considered for target_semester_type.

    Empty semester_offering means the data is missing/unspecified; treat the
    course as available in every semester (MVP assumption).  A non-empty list
    is authoritative — the course is only offered in those semesters.
    """
    if not semester_offering:
        return True
    return target_semester_type in semester_offering


# ---------------------------------------------------------------------------
# Internal per-course data
# ---------------------------------------------------------------------------

@dataclass
class _EligibleCourse:
    """Fully scored eligible course — populated in Phase 4, sorted in Phase 5."""
    course: AvailableCourse
    is_retake: bool
    unlock_score: int
    level_match: bool
    priority_level: str


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def generate_semester_plan(input: GenerateSemesterPlanInput) -> GenerateSemesterPlanOutput:
    """Generate up to three semester course plans based on student state and eligibility."""

    # -----------------------------------------------------------------------
    # Phase 1 — Structural Validation
    # -----------------------------------------------------------------------

    required_data_missing: list[str] = []
    if input.study_status is None:              # type: ignore[comparison-overlap]
        required_data_missing.append("study_status")
    if input.completed_courses is None:         # type: ignore[comparison-overlap]
        required_data_missing.append("completed_courses")
    if input.failed_courses is None:            # type: ignore[comparison-overlap]
        required_data_missing.append("failed_courses")
    if input.in_progress_courses is None:       # type: ignore[comparison-overlap]
        required_data_missing.append("in_progress_courses")
    if input.current_cgpa is None:              # type: ignore[comparison-overlap]
        required_data_missing.append("current_cgpa")
    if input.cumulative_passed_hours is None:   # type: ignore[comparison-overlap]
        required_data_missing.append("cumulative_passed_hours")
    if input.student_level is None:             # type: ignore[comparison-overlap]
        required_data_missing.append("student_level")
    if input.available_courses is None:         # type: ignore[comparison-overlap]
        required_data_missing.append("available_courses")
    if input.target_semester_type is None:      # type: ignore[comparison-overlap]
        required_data_missing.append("target_semester_type")

    if required_data_missing:
        return _cannot_compute(["required_data_missing"], required_data_missing)

    if input.target_semester_type == "Summer" and input.summer_semester_rules is None:
        return _cannot_compute(["missing_summer_rules"], [])

    # -----------------------------------------------------------------------
    # Phase 2 — Study Status Gate
    # -----------------------------------------------------------------------

    if input.study_status != "Studying":
        return GenerateSemesterPlanOutput(
            status="not_applicable",
            reason_codes=[f"study_status_{input.study_status.lower().replace(' ', '_')}"],
        )

    # -----------------------------------------------------------------------
    # Phase 3 — Credit Cap and Final Semester Detection
    # -----------------------------------------------------------------------

    credit_limit_rules = input.credit_limit_rules
    graduation_rules   = input.graduation_rules
    is_summer = input.target_semester_type == "Summer"
    cgpa_bracket_max = _compute_cgpa_bracket_max(input.current_cgpa, credit_limit_rules)

    is_final_semester = (
        graduation_rules.total_credits_required - input.cumulative_passed_hours
        <= credit_limit_rules.final_semester_override
    )

    warnings: list[str] = []
    active_credit_cap: int | None = None   # None for Summer (course-count based)
    course_count_cap: int | None  = None   # None for non-Summer

    if is_summer:
        summer_rules = input.summer_semester_rules  # non-None guaranteed by Phase 1
        course_count_cap = (
            summer_rules.cgpa_above_3_max_courses
            if input.current_cgpa >= summer_rules.cgpa_threshold_for_extra_course
            else summer_rules.default_max_courses
        )
    else:
        if is_final_semester:
            active_credit_cap = credit_limit_rules.final_semester_override
            warnings.append(
                "Final semester detected — dean approval required for credits above standard limit."
            )
        elif input.max_credits_mode:
            active_credit_cap = cgpa_bracket_max
        elif input.target_credit_load is not None:
            if input.target_credit_load > cgpa_bracket_max:
                warnings.append(
                    f"Requested credit load exceeds your CGPA-based limit. "
                    f"Capped to {cgpa_bracket_max}."
                )
            active_credit_cap = min(input.target_credit_load, cgpa_bracket_max)
        else:
            # Default: 18 for CGPA >= 2.0, 15 for >= 1.0, 12 for < 1.0
            if input.current_cgpa >= _CGPA_MID:
                active_credit_cap = credit_limit_rules.cgpa_between_2_and_3_limit
            elif input.current_cgpa >= _CGPA_LOW:
                active_credit_cap = credit_limit_rules.cgpa_between_1_and_2_limit
            else:
                active_credit_cap = credit_limit_rules.cgpa_below_1_limit

    # -----------------------------------------------------------------------
    # Phase 4 — Eligibility Filtering + Phase 5 scoring (interleaved)
    # -----------------------------------------------------------------------

    student_level_int = _LEVEL_MAP.get(input.student_level, 1)
    completed_set   = set(input.completed_courses)
    failed_set      = set(input.failed_courses)
    in_progress_set = set(input.in_progress_courses)

    eligible_pool: list[_EligibleCourse] = []
    cnt_in_progress       = 0
    cnt_already_completed = 0
    cnt_missing_prereqs   = 0
    cnt_credit_threshold  = 0
    cnt_wrong_semester    = 0

    for course in input.available_courses:
        code = course.course_code

        if not _is_offered_in_semester(course.semester_offering, input.target_semester_type):
            cnt_wrong_semester += 1
            continue

        if code in in_progress_set:
            cnt_in_progress += 1
            continue

        if code in completed_set and code not in failed_set:
            cnt_already_completed += 1
            continue

        missing_prereqs = [p for p in course.prerequisites if p not in completed_set]
        if missing_prereqs:
            cnt_missing_prereqs += 1
            continue

        if course.credit_threshold is not None:
            if input.cumulative_passed_hours < course.credit_threshold:
                cnt_credit_threshold += 1
                continue

        # Eligible — compute Phase 5 metadata immediately
        is_retake = code in failed_set
        unlock_score = sum(
            1 for other in input.available_courses
            if code in other.prerequisites and other.course_code not in completed_set
        )
        level_match = (course.level == student_level_int)
        priority_level = _assign_priority_level(is_retake, unlock_score, level_match)

        eligible_pool.append(_EligibleCourse(
            course=course,
            is_retake=is_retake,
            unlock_score=unlock_score,
            level_match=level_match,
            priority_level=priority_level,
        ))

    ineligibility_summary = _build_ineligibility_summary(
        cnt_in_progress, cnt_already_completed,
        cnt_missing_prereqs, cnt_credit_threshold,
        cnt_wrong_semester,
    )

    if not eligible_pool:
        return GenerateSemesterPlanOutput(
            status="no_eligible_courses",
            reason_codes=["no_eligible_courses"],
            target_semester_type=input.target_semester_type,
            is_final_semester=is_final_semester,
            total_eligible_courses=0,
            ineligibility_summary=ineligibility_summary,
        )

    # -----------------------------------------------------------------------
    # Phase 5 — Sort (metadata already computed above)
    # -----------------------------------------------------------------------

    sorted_pool = sorted(
        eligible_pool,
        key=lambda ec: (
            0 if ec.is_retake else 1,   # retake first
            -ec.unlock_score,           # higher unlock score first
            0 if ec.level_match else 1, # level match before non-match
        ),
    )

    # -----------------------------------------------------------------------
    # Phase 6 — Plan Generation
    # -----------------------------------------------------------------------

    plans: list[SemesterPlan] = []

    # Plan A — Recommended: greedy fill from full priority-sorted pool
    if is_summer:
        plan_a_courses = sorted_pool[:course_count_cap]
    else:
        plan_a_courses = _greedy_fill_credits(sorted_pool, active_credit_cap)  # type: ignore[arg-type]

    if plan_a_courses:
        plans.append(_build_plan("plan_a", "Recommended", plan_a_courses))

    plan_a_codes = {ec.course.course_code for ec in plan_a_courses}

    # Plan B — Lighter Load (non-summer, active cap > 15, re-sorted by unlock_score only)
    plan_b_codes: set[str] = set()
    if not is_summer and active_credit_cap is not None and active_credit_cap > _PLAN_B_MAX_CREDITS:
        pool_by_unlock = sorted(eligible_pool, key=lambda ec: -ec.unlock_score)
        plan_b_courses = _greedy_fill_credits(pool_by_unlock, _PLAN_B_MAX_CREDITS)
        plan_b_codes = {ec.course.course_code for ec in plan_b_courses}
        if plan_b_courses and plan_b_codes != plan_a_codes:
            plans.append(_build_plan("plan_b", "Lighter Load", plan_b_courses))
        else:
            plan_b_codes = set()  # not added — treat as non-existent for Plan C dedup

    # Plan C — Level Focused: same-level courses only, up to cap
    same_level_pool = [ec for ec in sorted_pool if ec.course.level == student_level_int]
    if len(same_level_pool) >= _MIN_LEVEL_FOCUSED_COURSES:
        if is_summer:
            plan_c_courses = same_level_pool[:course_count_cap]
        else:
            plan_c_courses = _greedy_fill_credits(same_level_pool, active_credit_cap)  # type: ignore[arg-type]
        plan_c_codes = {ec.course.course_code for ec in plan_c_courses}
        if (
            plan_c_courses
            and plan_c_codes != plan_a_codes
            and plan_c_codes != plan_b_codes
        ):
            plans.append(_build_plan("plan_c", "Level Focused", plan_c_courses))

    # -----------------------------------------------------------------------
    # Phase 7 — Warnings
    # -----------------------------------------------------------------------

    # 1. Eligible credits below semester minimum
    eligible_total_credits = sum(ec.course.credits for ec in eligible_pool)
    if eligible_total_credits < credit_limit_rules.minimum_per_semester:
        warnings.append(
            "Total eligible credits below minimum — consult your academic advisor."
        )

    # 2. Unresolved Incomplete grade
    if input.incomplete_grade_flag:
        warnings.append(
            "You have an unresolved Incomplete grade. An additional course may be "
            "available with academic council approval."
        )

    # 3. Final semester — non-course requirements reminder
    if is_final_semester:
        warnings.append(
            "This appears to be your final semester — verify all non-course "
            "graduation requirements with the registrar."
        )

    # 4. Summer availability subject to faculty council
    if input.target_semester_type == "Summer":
        warnings.append(
            "Summer course availability is subject to faculty council announcement."
        )

    # 5. Unofficial track advisory — student has no official track assignment in the record
    if input.official_track is None and input.target_track is not None:
        warnings.append(
            "No official track is assigned in your academic record. This plan is based "
            "on your stated target track and is advisory — confirm with your advisor."
        )

    # -----------------------------------------------------------------------
    # Phase 8 — Output Assembly
    # -----------------------------------------------------------------------

    return GenerateSemesterPlanOutput(
        status="plans_generated",
        warnings=warnings,
        target_semester_type=input.target_semester_type,
        credit_cap_applied=active_credit_cap,
        course_count_cap_applied=course_count_cap,
        is_final_semester=is_final_semester,
        total_eligible_courses=len(eligible_pool),
        ineligibility_summary=ineligibility_summary,
        plans=plans,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _cannot_compute(
    reason_codes: list[str],
    required_data_missing: list[str],
) -> GenerateSemesterPlanOutput:
    return GenerateSemesterPlanOutput(
        status="cannot_compute",
        reason_codes=reason_codes,
        required_data_missing=required_data_missing,
    )


def _compute_cgpa_bracket_max(
    current_cgpa: float,
    credit_limit_rules: CreditLimitRules,
) -> int:
    """Map CGPA to its maximum allowed credit bracket (handbook values via rule bundle)."""
    if current_cgpa >= _CGPA_HIGH:
        return credit_limit_rules.cgpa_above_3_limit
    if current_cgpa >= _CGPA_MID:
        return credit_limit_rules.cgpa_between_2_and_3_limit
    if current_cgpa >= _CGPA_LOW:
        return credit_limit_rules.cgpa_between_1_and_2_limit
    return credit_limit_rules.cgpa_below_1_limit


def _assign_priority_level(
    is_retake: bool,
    unlock_score: int,
    level_match: bool,
) -> str:
    if is_retake:
        return "retake"
    if unlock_score >= _HIGH_UNLOCK_THRESHOLD:
        return "high_unlock"
    if level_match:
        return "level_match"
    return "standard"


def _greedy_fill_credits(
    pool: list[_EligibleCourse],
    credit_cap: int,
) -> list[_EligibleCourse]:
    """Iterate pool in order, adding each course that fits within the remaining credit cap."""
    selected: list[_EligibleCourse] = []
    total = 0
    for ec in pool:
        if total + ec.course.credits <= credit_cap:
            selected.append(ec)
            total += ec.course.credits
    return selected


def _build_plan(
    plan_id: str,
    plan_label: str,
    courses: list[_EligibleCourse],
) -> SemesterPlan:
    return SemesterPlan(
        plan_id=plan_id,
        plan_label=plan_label,
        courses=[_build_planned_course(ec) for ec in courses],
        total_credits=sum(ec.course.credits for ec in courses),
    )


def _build_planned_course(ec: _EligibleCourse) -> PlannedCourse:
    reason_map = {
        "retake":       "Previously failed — retake required",
        "high_unlock":  f"Unlocks {ec.unlock_score} future courses",
        "level_match":  "Matches your current academic level",
        "standard":     "Available elective for your track",
    }
    return PlannedCourse(
        course_code=ec.course.course_code,
        course_name=ec.course.name,
        credits=ec.course.credits,
        reason=reason_map[ec.priority_level],
        priority_level=ec.priority_level,
        is_retake=ec.is_retake,
    )


def _build_ineligibility_summary(
    in_progress: int,
    already_completed: int,
    missing_prereqs: int,
    credit_threshold: int,
    wrong_semester: int,
) -> str | None:
    parts: list[str] = []
    if already_completed:
        parts.append(f"{already_completed} course(s) already completed")
    if in_progress:
        parts.append(f"{in_progress} currently in progress")
    if missing_prereqs:
        parts.append(f"{missing_prereqs} excluded — missing prerequisites")
    if credit_threshold:
        parts.append(f"{credit_threshold} excluded — credit threshold not met")
    if wrong_semester:
        parts.append(f"{wrong_semester} excluded — not offered this semester")
    return ("; ".join(parts) + ".") if parts else None

