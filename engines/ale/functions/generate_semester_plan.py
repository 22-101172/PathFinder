"""
ale/functions/generate_semester_plan.py
========================================
Implements generate_semester_plan following ALE_Step5_Algorithms.md Function 5,
phase by phase.

No grade resolution, no GPA math. Pure eligibility and priority scoring.
"""

from dataclasses import dataclass

from engines.ale.ale_schemas import (
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

# System-design algorithm constants
_HIGH_UNLOCK_THRESHOLD     = 3
_LIGHTER_LOAD_OFFSET       = 2   # Fallback offset (unused when bracket stepping works)
_MIN_LEVEL_FOCUSED_COURSES = 2   # Level-focused plan needs at least this many same-level courses
_DEFAULT_MAX_PLANS         = 3   # Default when requested_plan_count is None

_LEVEL_MAP = {"Freshman": 1, "Sophomore": 2, "Junior": 3, "Senior": 4}

# Non-universal zero-credit courses — excluded from normal semester plans.
# These are remedial fundamentals that are NOT required for every student.
# Field Training is NOT listed here; it may be a universal requirement.
NON_UNIVERSAL_ZERO_CREDIT_COURSES: frozenset[str] = frozenset({"HUM110", "C-MA110"})


def _is_offered_in_semester(semester_offering: list, target_semester_type: str) -> bool:
    if not semester_offering:
        return True
    return target_semester_type in semester_offering


@dataclass
class _EligibleCourse:
    course: AvailableCourse
    is_retake: bool
    unlock_score: int
    level_match: bool
    priority_level: str
    is_requested: bool = False


def generate_semester_plan(input: GenerateSemesterPlanInput) -> GenerateSemesterPlanOutput:
    """Generate semester course plans based on student state and eligibility."""

    # -----------------------------------------------------------------------
    # Phase 1 — Structural Validation
    # -----------------------------------------------------------------------

    required_data_missing: list[str] = []
    if input.study_status is None:
        required_data_missing.append("study_status")
    if input.completed_courses is None:
        required_data_missing.append("completed_courses")
    if input.failed_courses is None:
        required_data_missing.append("failed_courses")
    if input.in_progress_courses is None:
        required_data_missing.append("in_progress_courses")
    if input.current_cgpa is None:
        required_data_missing.append("current_cgpa")
    if input.cumulative_passed_hours is None:
        required_data_missing.append("cumulative_passed_hours")
    if input.student_level is None:
        required_data_missing.append("student_level")
    if input.available_courses is None:
        required_data_missing.append("available_courses")
    if input.target_semester_type is None:
        required_data_missing.append("target_semester_type")

    if required_data_missing:
        return _cannot_compute(["required_data_missing"], required_data_missing)

    _VALID_SEMESTER_TYPES = frozenset({"Fall", "Spring", "Summer"})
    if input.target_semester_type not in _VALID_SEMESTER_TYPES:
        return _cannot_compute(["invalid_target_semester_type"], [])

    if input.student_level not in _LEVEL_MAP:
        return _cannot_compute(["invalid_student_level"], [])

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
    active_credit_cap: int | None = None
    course_count_cap: int | None  = None

    if is_summer:
        summer_rules = input.summer_semester_rules
        course_count_cap = (
            summer_rules.cgpa_above_3_max_courses
            if input.current_cgpa >= summer_rules.cgpa_threshold_for_extra_course
            else summer_rules.default_max_courses
        )
        planning_target_credits = None
    else:
        if is_final_semester:
            active_credit_cap = credit_limit_rules.final_semester_override
            planning_target_credits = active_credit_cap
            warnings.append(
                "Final semester detected — dean approval required for credits above standard limit."
            )
        elif input.max_credits_mode:
            active_credit_cap = cgpa_bracket_max
            planning_target_credits = cgpa_bracket_max
        elif input.lighter_load_mode:
            lighter_target = max(
                credit_limit_rules.minimum_per_semester,
                _compute_lighter_bracket_cap(input.current_cgpa, credit_limit_rules),
            )
            active_credit_cap = lighter_target
            planning_target_credits = lighter_target
        elif input.target_credit_load is not None:
            if input.target_credit_load > cgpa_bracket_max:
                warnings.append(
                    f"Requested credit load exceeds your CGPA-based limit. "
                    f"Capped to {cgpa_bracket_max}."
                )
            active_credit_cap = min(input.target_credit_load, cgpa_bracket_max)
            planning_target_credits = active_credit_cap
        else:
            # Default: aim for the student's CGPA-bracket maximum
            active_credit_cap = cgpa_bracket_max
            planning_target_credits = cgpa_bracket_max

    # Minimum credit warning
    if not is_summer and input.target_credit_load is not None:
        if input.target_credit_load < credit_limit_rules.minimum_per_semester:
            warnings.append(
                f"Requested credit load ({input.target_credit_load} cr) is below the "
                f"minimum of {credit_limit_rules.minimum_per_semester} cr per semester — "
                "consult your academic advisor."
            )

    # -----------------------------------------------------------------------
    # Phase 4 — Eligibility Filtering + Phase 5 scoring
    # -----------------------------------------------------------------------

    student_level_int = _LEVEL_MAP[input.student_level]
    completed_set   = set(input.completed_courses)
    failed_set      = set(input.failed_courses)
    in_progress_set = set(input.in_progress_courses)

    # Resolve requested courses: match by code (case-insensitive) or name
    requested_set: set[str] = set()
    requested_codes: list[str] = []
    if input.requested_courses:
        code_to_course = {c.course_code.upper(): c for c in input.available_courses}
        name_to_code = {c.name.lower(): c.course_code for c in input.available_courses if c.name}
        for req in input.requested_courses:
            req_upper = req.strip().upper()
            req_lower = req.strip().lower()
            if req_upper in code_to_course:
                requested_codes.append(code_to_course[req_upper].course_code)
                requested_set.add(code_to_course[req_upper].course_code)
            elif req_lower in name_to_code:
                c = name_to_code[req_lower]
                requested_codes.append(c)
                requested_set.add(c)
            else:
                # Partial name match
                matches = [code for name, code in name_to_code.items() if req_lower in name]
                if len(matches) == 1:
                    requested_codes.append(matches[0])
                    requested_set.add(matches[0])

    # Detect in-progress failed courses (failed but currently in progress → don't re-plan)
    in_progress_failed: list[str] = [
        code for code in failed_set if code in in_progress_set
    ]

    # Effective completed set for prerequisite checking:
    # in-progress courses from the current term count as "will be completed"
    # when planning for the NEXT semester. This unlocks courses that require them.
    effective_completed_for_prereqs = completed_set | in_progress_set

    eligible_pool: list[_EligibleCourse] = []
    excluded_requested: list[dict] = []
    cnt_in_progress       = 0
    cnt_already_completed = 0
    cnt_missing_prereqs   = 0
    cnt_credit_threshold  = 0
    cnt_wrong_semester    = 0
    cnt_zero_credit_filtered = 0

    for course in input.available_courses:
        code = course.course_code
        is_requested = code in requested_set

        if not _is_offered_in_semester(course.semester_offering, input.target_semester_type):
            cnt_wrong_semester += 1
            if is_requested:
                excluded_requested.append({
                    "course_code": code,
                    "course_name": course.name,
                    "reason": "Not offered in the target semester."
                })
            continue

        if code in in_progress_set:
            cnt_in_progress += 1
            if is_requested:
                if code in failed_set:
                    excluded_requested.append({
                        "course_code": code,
                        "course_name": course.name,
                        "reason": "Currently in progress this semester — cannot be planned again."
                    })
                else:
                    excluded_requested.append({
                        "course_code": code,
                        "course_name": course.name,
                        "reason": "Already enrolled in this course this semester."
                    })
            continue

        if code in completed_set and code not in failed_set:
            cnt_already_completed += 1
            if is_requested:
                excluded_requested.append({
                    "course_code": code,
                    "course_name": course.name,
                    "reason": "Already completed and passed."
                })
            continue

        # Filter non-universal zero-credit fundamentals from normal plans
        # unless the student explicitly requested the course
        if (
            course.credits == 0
            and code in NON_UNIVERSAL_ZERO_CREDIT_COURSES
            and not is_requested
        ):
            cnt_zero_credit_filtered += 1
            continue

        # Use effective_completed_for_prereqs (includes in-progress) so that courses
        # unlocked by current-semester in-progress work are available for next-semester planning.
        missing_prereqs = [p for p in course.prerequisites if p not in effective_completed_for_prereqs]
        is_retake = code in failed_set

        if missing_prereqs and not is_retake:
            # Non-retake: standard prerequisite block
            cnt_missing_prereqs += 1
            if is_requested:
                prereq_str = ", ".join(missing_prereqs[:5])
                excluded_requested.append({
                    "course_code": code,
                    "course_name": course.name,
                    "reason": f"Missing prerequisites: {prereq_str}."
                })
            continue

        if missing_prereqs and is_retake:
            # Failed retake with missing prereqs: include with advisory warning
            # (student has historical attempt; advisor can clarify prerequisite recovery)
            pass  # Allow through — warning added below

        if course.credit_threshold is not None:
            if input.cumulative_passed_hours < course.credit_threshold:
                cnt_credit_threshold += 1
                if is_requested:
                    excluded_requested.append({
                        "course_code": code,
                        "course_name": course.name,
                        "reason": (
                            f"Credit threshold not met: need {course.credit_threshold} "
                            f"passed hours, have {input.cumulative_passed_hours}."
                        )
                    })
                continue

        # Eligible — compute priority metadata
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
            is_requested=is_requested,
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
            cgpa_bracket_max=cgpa_bracket_max,
            planning_target_credits=planning_target_credits if not is_summer else None,
            excluded_requested_courses=excluded_requested,
            in_progress_failed_courses=in_progress_failed,
        )

    # -----------------------------------------------------------------------
    # Phase 5 — Sort pools
    # -----------------------------------------------------------------------

    # Main priority sort: requested first, then retake, then unlock, then level match
    def _main_key(ec: _EligibleCourse):
        return (
            0 if ec.is_requested else 1,
            0 if ec.is_retake else 1,
            -ec.unlock_score,
            0 if ec.level_match else 1,
        )

    def _unlock_key(ec: _EligibleCourse):
        return (
            0 if ec.is_requested else 1,
            -ec.unlock_score,
            0 if ec.is_retake else 1,
            0 if ec.level_match else 1,
        )

    main_sorted = sorted(eligible_pool, key=_main_key)
    unlock_sorted = sorted(eligible_pool, key=_unlock_key)
    same_level_pool = [ec for ec in main_sorted if ec.level_match]

    # -----------------------------------------------------------------------
    # Phase 6 — Plan Generation
    # -----------------------------------------------------------------------

    # Determine how many plans to generate
    n_plans = min(input.requested_plan_count or _DEFAULT_MAX_PLANS, 5)

    retake_warning_courses: list[str] = []
    plans: list[SemesterPlan] = []
    seen_plan_sets: list[frozenset[str]] = []

    def _try_add_plan(pool, plan_id_prefix, label):
        nonlocal retake_warning_courses
        if len(plans) >= n_plans:
            return
        if is_summer:
            selected = pool[:course_count_cap]
        else:
            selected = _greedy_fill_credits(pool, active_credit_cap)
        if not selected:
            return
        codes = frozenset(ec.course.course_code for ec in selected)
        if codes in seen_plan_sets:
            return
        seen_plan_sets.append(codes)
        plan_idx = len(plans) + 1
        plan_id = f"plan_{plan_idx}"
        built = _build_plan(plan_id, label, selected)
        plans.append(built)
        # Collect retake warnings
        for ec in selected:
            if ec.is_retake and ec.course.course_code not in retake_warning_courses:
                retake_warning_courses.append(ec.course.course_code)

    _try_add_plan(main_sorted, "plan_1", "Recommended")
    _try_add_plan(unlock_sorted, "plan_2", "Unlock-Focused")
    if len(same_level_pool) >= _MIN_LEVEL_FOCUSED_COURSES:
        _try_add_plan(same_level_pool, "plan_3", "Level-Focused")

    # If still short of requested count, try reverse-of-unlock
    if len(plans) < n_plans:
        retake_first = [ec for ec in main_sorted if ec.is_retake]
        non_retake = [ec for ec in main_sorted if not ec.is_retake]
        alt_pool = retake_first + sorted(non_retake, key=lambda ec: ec.level_match, reverse=True)
        _try_add_plan(alt_pool, "plan_alt", "Retake-Priority")

    # -----------------------------------------------------------------------
    # Phase 7 — Warnings
    # -----------------------------------------------------------------------

    if not is_summer:
        eligible_total_credits = sum(ec.course.credits for ec in eligible_pool)
        if eligible_total_credits < credit_limit_rules.minimum_per_semester:
            warnings.append(
                "Total eligible credits below minimum — consult your academic advisor."
            )

    if input.incomplete_grade_flag:
        warnings.append(
            "You have an unresolved Incomplete grade. An additional course may be "
            "available with academic council approval."
        )

    if is_final_semester:
        warnings.append(
            "This appears to be your final semester — verify all non-course "
            "graduation requirements with the registrar."
        )

    if input.target_semester_type == "Summer":
        warnings.append(
            "Summer course availability is subject to faculty council announcement."
        )

    if input.official_track is None and input.target_track is not None:
        warnings.append(
            "No official track is assigned in your academic record. This plan is based "
            "on your stated target track and is advisory — confirm with your advisor."
        )

    if retake_warning_courses:
        codes_str = ", ".join(retake_warning_courses[:5])
        warnings.append(
            f"The following failed-course retakes are included in the plan: {codes_str}. "
            "Confirm with your academic advisor that prerequisite recovery does not affect registration."
        )

    if in_progress_failed:
        codes_str = ", ".join(in_progress_failed[:5])
        warnings.append(
            f"The following failed courses are already in progress this semester and are not "
            f"re-planned for the target semester: {codes_str}."
        )

    # Note if fewer distinct plans were found than requested
    if input.requested_plan_count and len(plans) < input.requested_plan_count:
        warnings.append(
            f"Only {len(plans)} distinct plan(s) are available — "
            f"fewer than the {input.requested_plan_count} requested."
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
        cgpa_bracket_max=cgpa_bracket_max,
        planning_target_credits=planning_target_credits if not is_summer else None,
        excluded_requested_courses=excluded_requested,
        in_progress_failed_courses=in_progress_failed,
        retake_warning_courses=retake_warning_courses,
        requested_plans_count=len(plans),
        requested_plans_requested=input.requested_plan_count,
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
    if current_cgpa >= _CGPA_HIGH:
        return credit_limit_rules.cgpa_above_3_limit
    if current_cgpa >= _CGPA_MID:
        return credit_limit_rules.cgpa_between_2_and_3_limit
    if current_cgpa >= _CGPA_LOW:
        return credit_limit_rules.cgpa_between_1_and_2_limit
    return credit_limit_rules.cgpa_below_1_limit


def _compute_lighter_bracket_cap(
    current_cgpa: float,
    credit_limit_rules: CreditLimitRules,
) -> int:
    """Return the next lower CGPA bracket's credit cap for lighter-load mode.

    If a student is in the 21-credit bracket (CGPA >= 3.0), lighter gives 18 (the 2.0-3.0 tier).
    If already at the 2.0-3.0 tier, lighter gives the 1.0-2.0 tier.
    If at the lowest bracket, return minimum_per_semester as the floor.
    """
    if current_cgpa >= _CGPA_HIGH:
        return credit_limit_rules.cgpa_between_2_and_3_limit
    if current_cgpa >= _CGPA_MID:
        return credit_limit_rules.cgpa_between_1_and_2_limit
    # Already at low bracket — minimum is the lightest valid load
    return credit_limit_rules.minimum_per_semester


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
