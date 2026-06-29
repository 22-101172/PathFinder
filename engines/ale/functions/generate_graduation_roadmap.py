"""
ale/functions/generate_graduation_roadmap.py
============================================
Implements generate_graduation_roadmap following ALE_Step5_Algorithms.md Function 6,
phase by phase.

Multi-semester simulation loop: absorbs in-progress courses, then projects semester-by-
semester course selection and GPA evolution until graduation requirements are met or a
terminal condition is reached.  No grade resolution — assumed_grade_per_pass is pre-
resolved to grade points by the orchestrator.

Supports two planning modes:
  - Graduation mode (default): simulates until graduation or a terminal stop.
  - Target-semester mode: simulates through a resolved target-end semester/year.
    Orchestrator/QU/utils resolve the target text; ALE only consumes resolved fields.

SCP/Orchestrator boundary (never inferred inside ALE):
  - current_semester: owned by SCP
  - starting semester/year: resolved by Orchestrator from ctx.current_semester + utils
  - target_end semester/year: resolved by Orchestrator from QU params + utils
"""

from dataclasses import dataclass

from engines.ale.ale_schemas import (
    AvailableCourse,
    CreditLimitRules,
    GenerateGraduationRoadmapInput,
    GenerateGraduationRoadmapOutput,
    GraduationRequirementRules,
    PlannedCourse,
    RoadmapSemester,
    SummerSemesterRules,
)
from engines.ale.utils.grade_resolver import derive_level

# CGPA bracket boundaries — inherent to CreditLimitRules field structure
_CGPA_HIGH = 3.0
_CGPA_MID  = 2.0
_CGPA_LOW  = 1.0

# System-design algorithm constants — ALE_Step5_Algorithms.md §Function6
_HIGH_UNLOCK_THRESHOLD = 3    # unlock_score >= 3 earns "high_unlock" priority
_DEFAULT_ASSUMED_GRADE = 2.6  # C+ — default grade assumed per pass when none provided
_MAX_LOOP_GUARD        = 300  # absolute safeguard; prevents infinite loops on bad data

_LEVEL_MAP             = {"Freshman": 1, "Sophomore": 2, "Junior": 3, "Senior": 4}
_VALID_SEMESTER_TYPES  = frozenset({"Fall", "Spring", "Summer"})
_YEAR_MIN, _YEAR_MAX   = 2000, 2100


def _semester_key(season: str, year: int) -> tuple[int, int]:
    """Chronological sort key (academic_year, season_rank).

    Academic-year convention used by QU handbook:
      Fall YYYY opens academic year YYYY  → key (YYYY, 0)
      Spring YYYY and Summer YYYY close year YYYY-1 → keys (YYYY-1, 1) and (YYYY-1, 2)

    Correct ordering: Fall 2025 < Spring 2026 < Summer 2026 < Fall 2026.
    """
    if season == "Fall":
        return (year, 0)
    if season == "Spring":
        return (year - 1, 1)
    return (year - 1, 2)  # Summer


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
# Internal per-course pool entry
# ---------------------------------------------------------------------------

@dataclass
class _PoolEntry:
    """Scored eligible course for a single roadmap pass."""
    course: AvailableCourse
    is_retake: bool
    unlock_score: int
    level_match: bool
    priority_level: str


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def generate_graduation_roadmap(
    input: GenerateGraduationRoadmapInput,
) -> GenerateGraduationRoadmapOutput:
    """Project a multi-semester graduation roadmap based on student state."""

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
    if input.gpa_counted_credits is None:       # type: ignore[comparison-overlap]
        required_data_missing.append("gpa_counted_credits")
    if input.current_quality_points is None:    # type: ignore[comparison-overlap]
        required_data_missing.append("current_quality_points")
    if input.cumulative_passed_hours is None:   # type: ignore[comparison-overlap]
        required_data_missing.append("cumulative_passed_hours")
    if input.student_level is None:             # type: ignore[comparison-overlap]
        required_data_missing.append("student_level")
    if input.available_courses is None:         # type: ignore[comparison-overlap]
        required_data_missing.append("available_courses")
    if input.target_semester_type is None:      # type: ignore[comparison-overlap]
        required_data_missing.append("target_semester_type")
    if input.starting_year is None:             # type: ignore[comparison-overlap]
        required_data_missing.append("starting_year")

    if required_data_missing:
        return _cannot_compute(["required_data_missing"], required_data_missing)

    # Summer rules required when accelerated mode is on or first pass is Summer
    needs_summer_rules = input.accelerated_mode or input.target_semester_type == "Summer"
    if needs_summer_rules and input.summer_semester_rules is None:
        return _cannot_compute(["missing_summer_rules"], [])

    # Belt-and-suspenders guards — protect against model_construct() bypass of Literal
    if input.target_semester_type not in _VALID_SEMESTER_TYPES:  # type: ignore[comparison-overlap]
        return _cannot_compute(["invalid_target_semester_type"], [])

    if input.student_level not in _LEVEL_MAP:  # type: ignore[comparison-overlap]
        return _cannot_compute(["invalid_student_level"], [])

    if not (_YEAR_MIN <= input.starting_year <= _YEAR_MAX):
        return _cannot_compute(["invalid_starting_year"], [])

    # Target-end semester validation (both fields or neither)
    has_end_type = input.target_end_semester_type is not None
    has_end_year = input.target_end_year is not None
    if has_end_type != has_end_year:
        return _cannot_compute(["incomplete_target_end_semester"], [])

    if has_end_type:
        if input.target_end_semester_type not in _VALID_SEMESTER_TYPES:  # type: ignore[comparison-overlap]
            return _cannot_compute(["invalid_target_end_semester_type"], [])
        if not (_YEAR_MIN <= input.target_end_year <= _YEAR_MAX):  # type: ignore[operator]
            return _cannot_compute(["invalid_target_end_year"], [])
        if _semester_key(input.target_end_semester_type, input.target_end_year) < _semester_key(  # type: ignore[arg-type]
            input.target_semester_type, input.starting_year
        ):
            return _cannot_compute(["target_end_before_start"], [])

    # -----------------------------------------------------------------------
    # Phase 2 — Study Status Gate
    # -----------------------------------------------------------------------

    if input.study_status != "Studying":
        return GenerateGraduationRoadmapOutput(
            status="not_applicable",
            reason_codes=[f"study_status_{input.study_status.lower().replace(' ', '_')}"],
        )

    # -----------------------------------------------------------------------
    # Phase 3 — Pre-Loop State Initialization
    # -----------------------------------------------------------------------

    graduation_rules   = input.graduation_rules
    credit_limit_rules = input.credit_limit_rules

    assumed_grade: float = (
        input.assumed_grade_per_pass
        if input.assumed_grade_per_pass is not None
        else _DEFAULT_ASSUMED_GRADE
    )

    sim_completed:   set[str] = set(input.completed_courses)
    sim_failed:      set[str] = set(input.failed_courses)
    sim_passed_hrs:  int      = input.cumulative_passed_hours
    sim_quality_pts: float    = float(input.current_quality_points)
    sim_gpa_denom:   float    = float(input.gpa_counted_credits)
    sim_cgpa:        float    = input.current_cgpa
    regular_passes:  int      = 0

    course_lookup: dict[str, AvailableCourse] = {
        c.course_code: c for c in input.available_courses
    }

    global_warnings: list[str] = []

    # Warning simulation state — only active when warning_rules is provided
    sim_consecutive_warnings: int    = input.consecutive_warnings
    sim_total_warnings: int          = input.total_warnings
    warning_limit_reached_in_semester: str | None = None
    _do_warning_sim: bool            = input.warning_rules is not None

    # Absorb in-progress courses — assumed passed before the loop begins
    if input.in_progress_courses:
        for code in input.in_progress_courses:
            ac = course_lookup.get(code)
            if ac is None:
                global_warnings.append(
                    f"In-progress course {code} not found in available courses — "
                    "credit hours excluded from projection."
                )
                sim_completed.add(code)   # still mark done so it is not re-selected
                continue
            sim_completed.add(code)
            sim_quality_pts += assumed_grade * ac.credits
            sim_gpa_denom   += ac.credits
            sim_passed_hrs  += ac.credits
        if sim_gpa_denom > 0:
            sim_cgpa = round(sim_quality_pts / sim_gpa_denom, 2)
        global_warnings.append(
            "In-progress courses assumed passed. Roadmap changes if any are not passed."
        )

    # Non-course blockers — military and zero-credit are readiness gaps, not loop blockers.
    # Evaluated against rules so rule toggles can disable them.
    non_course_blockers: list[str] = []
    if (
        graduation_rules.military_training_required_for_males
        and input.military_status is not None
        and input.military_status not in ("Done", "Exempted")
    ):
        non_course_blockers.append("military_training_required")
    if graduation_rules.must_pass_zero_credit_courses and not input.zero_credit_courses_passed:
        non_course_blockers.append("zero_credit_courses_required")
    # cgpa_below_minimum: evaluated in Phase 5 using final sim_cgpa, not initial CGPA

    # -----------------------------------------------------------------------
    # Phase 4 — Simulation Loop
    # -----------------------------------------------------------------------

    semester_plans: list[RoadmapSemester] = []
    current_season = input.target_semester_type
    current_year   = input.starting_year
    pass_number    = 0
    loop_guard     = 0

    # Edge case: all graduation requirements already met after absorbing in-progress
    already_done = (
        sim_passed_hrs >= graduation_rules.total_credits_required
        and (input.completed_regular_semesters + regular_passes)
            >= graduation_rules.minimum_regular_semesters
        and sim_cgpa >= graduation_rules.minimum_cgpa
    )
    if already_done:
        global_warnings.append("All graduation requirements already met.")

    while (
        sim_passed_hrs < graduation_rules.total_credits_required
        or (input.completed_regular_semesters + regular_passes)
            < graduation_rules.minimum_regular_semesters
    ):
        loop_guard += 1
        if loop_guard > _MAX_LOOP_GUARD:
            return _cannot_compute(["infinite_loop_guard"], [])

        # Target-end mode: stop BEFORE planning any semester beyond the requested target
        if has_end_type:
            if _semester_key(current_season, current_year) > _semester_key(
                input.target_end_semester_type, input.target_end_year  # type: ignore[arg-type]
            ):
                gaps = _build_remaining_gaps(
                    sim_passed_hrs,
                    input.completed_regular_semesters + regular_passes,
                    graduation_rules,
                    sim_cgpa=sim_cgpa,
                )
                return GenerateGraduationRoadmapOutput(
                    status="cannot_complete_projection",
                    reason_codes=["target_semester_reached_not_graduated"],
                    warnings=global_warnings,
                    total_passes=len(semester_plans),
                    non_course_blockers=non_course_blockers,
                    remaining_graduation_gaps=gaps,
                    semester_mode="accelerated" if input.accelerated_mode else "regular",
                    credit_mode=_determine_credit_mode(input),
                    simulation_grade_used=assumed_grade,
                    semester_plans=semester_plans,
                    target_reached_without_graduation=True,
                    projected_consecutive_warnings=sim_consecutive_warnings if _do_warning_sim else None,
                    projected_total_warnings=sim_total_warnings if _do_warning_sim else None,
                    warning_limit_reached_in_semester=warning_limit_reached_in_semester,
                )

        pass_number    += 1
        is_summer_pass  = (current_season == "Summer")
        is_regular_pass = not is_summer_pass

        # Safeguard — maximum regular semesters reached
        if is_regular_pass and (
            input.completed_regular_semesters + regular_passes
            >= graduation_rules.maximum_regular_semesters
        ):
            gaps = _build_remaining_gaps(
                sim_passed_hrs,
                input.completed_regular_semesters + regular_passes,
                graduation_rules,
                sim_cgpa=sim_cgpa,
            )
            return GenerateGraduationRoadmapOutput(
                status="cannot_complete_projection",
                reason_codes=["maximum_semesters_reached"],
                warnings=global_warnings,
                total_passes=len(semester_plans),
                non_course_blockers=non_course_blockers,
                remaining_graduation_gaps=gaps,
                semester_mode="accelerated" if input.accelerated_mode else "regular",
                credit_mode=_determine_credit_mode(input),
                simulation_grade_used=assumed_grade,
                semester_plans=semester_plans,
                projected_consecutive_warnings=sim_consecutive_warnings if _do_warning_sim else None,
                projected_total_warnings=sim_total_warnings if _do_warning_sim else None,
                warning_limit_reached_in_semester=warning_limit_reached_in_semester,
            )

        # Credit / course-count cap for this pass
        is_final_pass      = False
        active_credit_cap: int | None = None
        course_count_cap:  int | None = None

        if is_summer_pass:
            summer_rules = input.summer_semester_rules  # non-None guaranteed by Phase 1
            course_count_cap = (
                summer_rules.cgpa_above_3_max_courses   # type: ignore[union-attr]
                if sim_cgpa >= summer_rules.cgpa_threshold_for_extra_course  # type: ignore[union-attr]
                else summer_rules.default_max_courses   # type: ignore[union-attr]
            )
        else:
            cgpa_bracket_max = _compute_cgpa_bracket_max(sim_cgpa, credit_limit_rules)

            # Final pass: credits remaining fit within final_semester_override AND
            # this pass will satisfy minimum regular semester count
            semesters_after = input.completed_regular_semesters + regular_passes + 1
            credits_remaining = graduation_rules.total_credits_required - sim_passed_hrs
            is_final_pass = (
                credits_remaining <= credit_limit_rules.final_semester_override
                and semesters_after >= graduation_rules.minimum_regular_semesters
            )

            if is_final_pass:
                active_credit_cap = credit_limit_rules.final_semester_override
            elif input.max_credits_mode:
                active_credit_cap = cgpa_bracket_max
            elif input.target_credit_load is not None:
                active_credit_cap = min(input.target_credit_load, cgpa_bracket_max)
            else:
                if sim_cgpa >= _CGPA_MID:
                    active_credit_cap = credit_limit_rules.cgpa_between_2_and_3_limit
                elif sim_cgpa >= _CGPA_LOW:
                    active_credit_cap = credit_limit_rules.cgpa_between_1_and_2_limit
                else:
                    active_credit_cap = credit_limit_rules.cgpa_below_1_limit

        # Eligible pool for this pass
        student_level_int = _LEVEL_MAP[derive_level(sim_passed_hrs, input.student_level_rules)]
        pool: list[_PoolEntry] = []

        for ac in input.available_courses:
            code = ac.course_code

            if not _is_offered_in_semester(ac.semester_offering, current_season):
                continue

            # Skip if completed and not a pending retake
            if code in sim_completed and code not in sim_failed:
                continue

            if any(p not in sim_completed for p in ac.prerequisites):
                continue

            if ac.credit_threshold is not None and sim_passed_hrs < ac.credit_threshold:
                continue

            is_retake = code in sim_failed
            unlock_score = sum(
                1 for other in input.available_courses
                if code in other.prerequisites and other.course_code not in sim_completed
            )
            level_match    = (ac.level == student_level_int)
            priority_level = _assign_priority_level(is_retake, unlock_score, level_match)

            pool.append(_PoolEntry(
                course=ac,
                is_retake=is_retake,
                unlock_score=unlock_score,
                level_match=level_match,
                priority_level=priority_level,
            ))

        if not pool:
            gaps = _build_remaining_gaps(
                sim_passed_hrs,
                input.completed_regular_semesters + regular_passes,
                graduation_rules,
                sim_cgpa=sim_cgpa,
            )
            return GenerateGraduationRoadmapOutput(
                status="blocked",
                reason_codes=["no_eligible_courses"],
                warnings=global_warnings,
                total_passes=len(semester_plans),
                non_course_blockers=non_course_blockers,
                remaining_graduation_gaps=gaps,
                semester_mode="accelerated" if input.accelerated_mode else "regular",
                credit_mode=_determine_credit_mode(input),
                simulation_grade_used=assumed_grade,
                semester_plans=semester_plans,
                projected_consecutive_warnings=sim_consecutive_warnings if _do_warning_sim else None,
                projected_total_warnings=sim_total_warnings if _do_warning_sim else None,
                warning_limit_reached_in_semester=warning_limit_reached_in_semester,
            )

        # Sort by priority tier
        sorted_pool = sorted(
            pool,
            key=lambda e: (
                0 if e.is_retake else 1,
                -e.unlock_score,
                0 if e.level_match else 1,
            ),
        )

        # Select courses for this pass
        if is_summer_pass:
            selected: list[_PoolEntry] = sorted_pool[:course_count_cap]
        else:
            selected = _greedy_fill(sorted_pool, active_credit_cap)  # type: ignore[arg-type]

        # Defensive: if nothing fits the cap, treat as blocked
        if not selected:
            gaps = _build_remaining_gaps(
                sim_passed_hrs,
                input.completed_regular_semesters + regular_passes,
                graduation_rules,
                sim_cgpa=sim_cgpa,
            )
            return GenerateGraduationRoadmapOutput(
                status="blocked",
                reason_codes=["no_courses_fit_credit_cap"],
                warnings=global_warnings,
                total_passes=len(semester_plans),
                non_course_blockers=non_course_blockers,
                remaining_graduation_gaps=gaps,
                semester_mode="accelerated" if input.accelerated_mode else "regular",
                credit_mode=_determine_credit_mode(input),
                simulation_grade_used=assumed_grade,
                semester_plans=semester_plans,
                projected_consecutive_warnings=sim_consecutive_warnings if _do_warning_sim else None,
                projected_total_warnings=sim_total_warnings if _do_warning_sim else None,
                warning_limit_reached_in_semester=warning_limit_reached_in_semester,
            )

        # State update — GPA evolves before we compute per-pass warnings
        credits_this_pass = sum(e.course.credits for e in selected)
        retake_cap_applied = False
        cap_pts = input.failed_retake_grade_cap_points

        for entry in selected:
            if entry.is_retake:
                if cap_pts is not None and assumed_grade > cap_pts:
                    applied = cap_pts
                    retake_cap_applied = True
                else:
                    applied = assumed_grade
                # Replacement: old failed attempt had 0 quality points; just add new
                sim_quality_pts += applied * entry.course.credits
                # sim_gpa_denom unchanged — credits already counted in denominator
                sim_failed.discard(entry.course.course_code)
            else:
                sim_quality_pts += assumed_grade * entry.course.credits
                sim_gpa_denom   += entry.course.credits
            sim_completed.add(entry.course.course_code)

        sim_passed_hrs += credits_this_pass
        if sim_gpa_denom > 0:
            sim_cgpa = round(sim_quality_pts / sim_gpa_denom, 2)

        if is_regular_pass:
            regular_passes += 1

        # Warning progression simulation — regular semesters only; Summer excluded
        if _do_warning_sim and is_regular_pass:
            warn_rules = input.warning_rules  # type: ignore[union-attr]
            if sim_cgpa < warn_rules.cgpa_warning_threshold:
                sim_consecutive_warnings += 1
                sim_total_warnings += 1
            else:
                sim_consecutive_warnings = 0
            if (
                sim_consecutive_warnings >= warn_rules.max_consecutive_warnings
                or sim_total_warnings >= warn_rules.max_total_warnings
            ):
                warning_limit_reached_in_semester = f"{current_season} {current_year}"
                gaps = _build_remaining_gaps(
                    sim_passed_hrs,
                    input.completed_regular_semesters + regular_passes,
                    graduation_rules,
                    sim_cgpa=sim_cgpa,
                )
                return GenerateGraduationRoadmapOutput(
                    status="cannot_complete_projection",
                    reason_codes=["projected_warning_limit_reached"],
                    warnings=global_warnings,
                    total_passes=len(semester_plans),
                    non_course_blockers=non_course_blockers,
                    remaining_graduation_gaps=gaps,
                    semester_mode="accelerated" if input.accelerated_mode else "regular",
                    credit_mode=_determine_credit_mode(input),
                    simulation_grade_used=assumed_grade,
                    semester_plans=semester_plans,
                    projected_consecutive_warnings=sim_consecutive_warnings,
                    projected_total_warnings=sim_total_warnings,
                    warning_limit_reached_in_semester=warning_limit_reached_in_semester,
                )

        # Per-pass warnings (computed after state update so sim_cgpa is current)
        pass_warnings: list[str] = []

        # 1. Pass 1 + unresolved Incomplete grade
        if pass_number == 1 and input.incomplete_grade_flag:
            pass_warnings.append(
                "You have an unresolved Incomplete grade. An additional course may be "
                "available with academic council approval."
            )

        # 2. Retake grade cap was applied this pass
        if retake_cap_applied:
            pass_warnings.append(
                "Failed-retake courses have a maximum grade cap. "
                "Projected CGPA may be lower than estimated without the cap."
            )

        # 3. Simulated CGPA below graduation minimum
        if sim_cgpa < graduation_rules.minimum_cgpa:
            pass_warnings.append(
                f"Simulated CGPA after this semester ({sim_cgpa}) is below the minimum "
                f"graduation requirement ({graduation_rules.minimum_cgpa}). "
                "Consult your academic advisor."
            )

        # 4. Final pass — non-course graduation requirements
        if is_final_pass:
            pass_warnings.append(
                "This appears to be your final semester — verify all non-course "
                "graduation requirements with the registrar."
            )

        # 5. Summer pass
        if is_summer_pass:
            pass_warnings.append(
                "Summer course availability is subject to faculty council announcement."
            )

        # 6. Eligible credits below semester minimum (non-summer only — summer uses course-count cap)
        if not is_summer_pass:
            pool_credits = sum(e.course.credits for e in pool)
            if pool_credits < credit_limit_rules.minimum_per_semester:
                pass_warnings.append(
                    "Total eligible credits below minimum — consult your academic advisor."
                )

        planned_courses = [_build_planned_course(e) for e in selected]

        semester_plans.append(RoadmapSemester(
            pass_number=pass_number,
            semester_label=f"{current_season} {current_year}",
            semester_type="summer" if is_summer_pass else "regular",
            courses=planned_courses,
            total_credits=credits_this_pass,
            simulated_cgpa_after=sim_cgpa,
            cumulative_passed_hours_after=sim_passed_hrs,
            is_final_semester=is_final_pass,
            warnings=pass_warnings,
        ))

        current_season, current_year = _advance_semester(
            current_season, current_year, input.accelerated_mode
        )

    # -----------------------------------------------------------------------
    # Phase 5 — Output Assembly
    # -----------------------------------------------------------------------

    # Final CGPA check — graduation requires meeting minimum CGPA threshold
    if sim_cgpa < graduation_rules.minimum_cgpa:
        non_course_blockers.append("cgpa_below_minimum")
        gaps = _build_remaining_gaps(
            sim_passed_hrs,
            input.completed_regular_semesters + regular_passes,
            graduation_rules,
            sim_cgpa=sim_cgpa,
        )
        return GenerateGraduationRoadmapOutput(
            status="cannot_complete_projection",
            reason_codes=["cgpa_below_minimum"],
            warnings=global_warnings,
            total_passes=len(semester_plans),
            non_course_blockers=non_course_blockers,
            remaining_graduation_gaps=gaps,
            semester_mode="accelerated" if input.accelerated_mode else "regular",
            credit_mode=_determine_credit_mode(input),
            simulation_grade_used=assumed_grade,
            semester_plans=semester_plans,
            projected_consecutive_warnings=sim_consecutive_warnings if _do_warning_sim else None,
            projected_total_warnings=sim_total_warnings if _do_warning_sim else None,
            warning_limit_reached_in_semester=warning_limit_reached_in_semester,
        )

    projected_graduation_semester = (
        semester_plans[-1].semester_label if semester_plans else None
    )

    return GenerateGraduationRoadmapOutput(
        status="complete",
        warnings=global_warnings,
        total_passes=len(semester_plans),
        projected_graduation_semester=projected_graduation_semester,
        non_course_blockers=non_course_blockers,
        semester_mode="accelerated" if input.accelerated_mode else "regular",
        credit_mode=_determine_credit_mode(input),
        simulation_grade_used=assumed_grade,
        semester_plans=semester_plans,
        projected_consecutive_warnings=sim_consecutive_warnings if _do_warning_sim else None,
        projected_total_warnings=sim_total_warnings if _do_warning_sim else None,
        warning_limit_reached_in_semester=warning_limit_reached_in_semester,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _cannot_compute(
    reason_codes: list[str],
    required_data_missing: list[str],
) -> GenerateGraduationRoadmapOutput:
    return GenerateGraduationRoadmapOutput(
        status="cannot_compute",
        reason_codes=reason_codes,
        required_data_missing=required_data_missing,
    )


def _compute_cgpa_bracket_max(
    cgpa: float,
    credit_limit_rules: CreditLimitRules,
) -> int:
    """Map CGPA to its maximum allowed credit bracket (handbook values via rule bundle)."""
    if cgpa >= _CGPA_HIGH:
        return credit_limit_rules.cgpa_above_3_limit
    if cgpa >= _CGPA_MID:
        return credit_limit_rules.cgpa_between_2_and_3_limit
    if cgpa >= _CGPA_LOW:
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


def _advance_semester(season: str, year: int, accelerated: bool) -> tuple[str, int]:
    """Return (next_season, next_year) following academic calendar conventions.

    Regular:     Fall → Spring (+1 yr) → Fall (same yr) → ...
    Accelerated: Fall → Spring (+1 yr) → Summer (same yr) → Fall (same yr) → ...
    """
    if season == "Fall":
        return "Spring", year + 1
    if season == "Spring":
        return ("Summer", year) if accelerated else ("Fall", year)
    # Summer — only reachable in accelerated mode
    return "Fall", year


def _greedy_fill(pool: list[_PoolEntry], credit_cap: int) -> list[_PoolEntry]:
    """Iterate pool in priority order, adding courses that fit within the remaining cap."""
    selected: list[_PoolEntry] = []
    total = 0
    for entry in pool:
        if total + entry.course.credits <= credit_cap:
            selected.append(entry)
            total += entry.course.credits
    return selected


def _build_planned_course(entry: _PoolEntry) -> PlannedCourse:
    reason_map = {
        "retake":      "Previously failed — retake required",
        "high_unlock": f"Unlocks {entry.unlock_score} future course(s)",
        "level_match": "Matches your current academic level",
        "standard":    "Available elective for your track",
    }
    return PlannedCourse(
        course_code=entry.course.course_code,
        course_name=entry.course.name,
        credits=entry.course.credits,
        reason=reason_map[entry.priority_level],
        priority_level=entry.priority_level,
        is_retake=entry.is_retake,
    )


def _build_remaining_gaps(
    sim_passed_hrs: int,
    total_regular_semesters: int,
    graduation_rules: GraduationRequirementRules,
    sim_cgpa: float | None = None,
) -> list[str]:
    gaps: list[str] = []
    credit_gap = graduation_rules.total_credits_required - sim_passed_hrs
    if credit_gap > 0:
        gaps.append(f"{credit_gap} credit hour(s) still needed")
    semester_gap = graduation_rules.minimum_regular_semesters - total_regular_semesters
    if semester_gap > 0:
        gaps.append(f"{semester_gap} regular semester(s) still needed")
    if sim_cgpa is not None and sim_cgpa < graduation_rules.minimum_cgpa:
        gaps.append(
            f"CGPA ({sim_cgpa}) below graduation minimum ({graduation_rules.minimum_cgpa})"
        )
    return gaps


def _determine_credit_mode(input: GenerateGraduationRoadmapInput) -> str:
    if input.max_credits_mode:
        return "max_credits"
    if input.target_credit_load is not None:
        return "student_specified"
    return "default"
