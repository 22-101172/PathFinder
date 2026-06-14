"""
ale/functions/solve_target_gpa.py
===================================
Implements solve_target_gpa following ALE_Step5_Algorithms.md Function 2,
phase by phase.
"""

from dataclasses import dataclass

from engines.ale.schemas import (
    CourseTarget,
    GradeOverride,
    GradingScaleRules,
    GraduationRequirementRules,
    MultiSemesterProjection,
    PlannedCourseTarget,
    RetakeRules,
    SolveTargetGPAInput,
    SolveTargetGPAOutput,
)
from engines.ale.utils.grade_resolver import GradeResolutionError, resolve_grade


# ---------------------------------------------------------------------------
# Internal per-course state — populated during Phase 2
# ---------------------------------------------------------------------------

@dataclass
class _CourseState:
    course: PlannedCourseTarget
    is_gpa_neutral: bool
    old_grade_points: float | None        # resolved; None if no footprint, P-grade, or failed
    historical_grade_points: float | None # resolved; None if no history or resolution failed
    max_grade_points: float               # per-course cap ceiling from retake rules


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def solve_target_gpa(input: SolveTargetGPAInput) -> SolveTargetGPAOutput:
    """Compute what grades a student needs in planned courses to reach a target CGPA."""

    grading_scale = input.grading_scale_rules
    retake_rules = input.retake_rules
    graduation_rules = input.graduation_rules
    max_overall_gp = _max_overall_grade_points(grading_scale)

    # -----------------------------------------------------------------------
    # Phase 1 — Structural Validation
    # -----------------------------------------------------------------------

    # Step 1
    if input.target_cgpa <= 0 or input.target_cgpa > 4.0:
        return _cannot_compute(["invalid_target_cgpa"], [], input)

    # Step 1b — early exit if target already met
    if input.current_cgpa >= input.target_cgpa:
        return SolveTargetGPAOutput(
            status="already_met",
            current_cgpa=input.current_cgpa,
            target_cgpa=input.target_cgpa,
            delta_needed=round(input.target_cgpa - input.current_cgpa, 2),
            required_average_grade_points=None,
            required_average_letter=None,
            gpa_neutral_courses=[],
            planned_course_source=input.planned_course_source,
        )

    # Step 2
    if not input.planned_courses:
        return _cannot_compute(["empty_planned_courses"], [], input)

    # Step 3
    required_data_missing: list[str] = []
    for course in input.planned_courses:
        if course.credits is None:  # type: ignore[comparison-overlap]
            required_data_missing.append(f"missing_credits_{course.course_code}")
        if course.has_cgpa_footprint and course.old_grade is None:
            required_data_missing.append(f"missing_old_grade_{course.course_code}")

    # Step 4
    if required_data_missing:
        return _cannot_compute(["required_data_missing"], required_data_missing, input)

    # Step 5
    if input.current_quality_points is None:  # type: ignore[comparison-overlap]
        return _cannot_compute(["missing_current_quality_points"], [], input)

    # -----------------------------------------------------------------------
    # Phase 2 — Grade Resolution
    # -----------------------------------------------------------------------

    course_states: list[_CourseState] = []
    gpa_neutral_courses: list[str] = []

    for course in input.planned_courses:
        is_gpa_neutral = course.credits == 0  # Step 7: zero-credit → gpa_neutral
        old_grade_points: float | None = None

        # Step 6 — resolve old_grade for footprint courses
        if course.has_cgpa_footprint:
            try:
                old_grade_points = resolve_grade(
                    course.old_grade,  # type: ignore[arg-type]
                    grading_scale,
                    course.course_code,
                )
            except GradeResolutionError:
                required_data_missing.append(f"invalid_old_grade_{course.course_code}")
            else:
                # Edge case: old_grade is P → course was never in GPA denominator
                if old_grade_points is None:
                    is_gpa_neutral = True

        # Step 8 — resolve historical_grade (advisory; failure is non-fatal)
        historical_grade_points: float | None = None
        if course.related_completed_course is not None and course.historical_grade is not None:
            try:
                historical_grade_points = resolve_grade(
                    course.historical_grade, grading_scale, course.course_code
                )
            except GradeResolutionError:
                historical_grade_points = None  # treat as no history

        if is_gpa_neutral:
            gpa_neutral_courses.append(course.course_code)

        course_states.append(_CourseState(
            course=course,
            is_gpa_neutral=is_gpa_neutral,
            old_grade_points=old_grade_points,
            historical_grade_points=historical_grade_points,
            max_grade_points=_course_max_grade_points(
                course, retake_rules, grading_scale, max_overall_gp
            ),
        ))

    if required_data_missing:
        return _cannot_compute(["required_data_missing"], required_data_missing, input)

    # -----------------------------------------------------------------------
    # Phase 3 — Core Math
    # -----------------------------------------------------------------------

    # Step 9
    adjusted_quality_points: float = input.current_quality_points - sum(
        state.old_grade_points * state.course.credits
        for state in course_states
        if state.course.has_cgpa_footprint and state.old_grade_points is not None
    )

    # Step 10
    final_denominator: float = input.gpa_counted_credits + sum(
        state.course.credits
        for state in course_states
        if not state.course.has_cgpa_footprint and not state.is_gpa_neutral
    )

    # Step 11
    required_new_points = (input.target_cgpa * final_denominator) - adjusted_quality_points

    # Step 12
    total_planned_credits = sum(
        state.course.credits
        for state in course_states
        if not state.is_gpa_neutral
    )
    if total_planned_credits == 0:
        return _cannot_compute(["all_courses_gpa_neutral"], [], input)

    # Step 13
    required_average = required_new_points / total_planned_credits

    # -----------------------------------------------------------------------
    # Phase 4 — Status Determination
    # -----------------------------------------------------------------------

    delta_needed = round(input.target_cgpa - input.current_cgpa, 2)
    warnings: list[str] = []

    # Step 14
    if required_new_points <= 0:
        return SolveTargetGPAOutput(
            status="already_met",
            current_cgpa=input.current_cgpa,
            target_cgpa=input.target_cgpa,
            delta_needed=delta_needed,
            required_average_grade_points=None,
            required_average_letter=None,
            gpa_neutral_courses=gpa_neutral_courses,
            planned_course_source=input.planned_course_source,
        )

    # Step 15 — per-course max for impossibility check
    max_possible_quality_points = sum(
        state.max_grade_points * state.course.credits
        for state in course_states
        if not state.is_gpa_neutral
    )

    # Step 16 — two-stage impossibility check (in order)
    maximum_reachable_cgpa: float | None = None

    if required_average > max_overall_gp:
        status = "impossible"
        reason_codes = ["required_average_exceeds_maximum"]
    elif required_new_points > max_possible_quality_points:
        status = "impossible"
        reason_codes = ["impossible_due_to_retake_caps"]
    else:
        status = "solvable"
        reason_codes = []

    # Step 17
    if status == "impossible":
        maximum_reachable_cgpa = round(
            (adjusted_quality_points + max_possible_quality_points) / final_denominator, 2
        )

    # -----------------------------------------------------------------------
    # Phase 5 — Distribution Assembly (only when solvable)
    # -----------------------------------------------------------------------

    default_distribution: list[CourseTarget] | None = None
    personalized_distribution: list[CourseTarget] | None = None
    personalized_distribution_valid: bool | None = None
    grade_overrides: list[GradeOverride] = []
    required_average_letter: str | None = None

    if status == "solvable":
        # Step 18 — map required_average to letter, rounding UP to guarantee target is met
        required_average_letter = _grade_points_to_letter_round_up(required_average, grading_scale)

        # Step 19 — Layer 1: default equal distribution
        default_distribution = [
            CourseTarget(
                course_code=state.course.course_code,
                course_name=state.course.course_name,
                credits=state.course.credits,
                target_grade_points=required_average,
                target_grade_letter=required_average_letter,
                is_personalized=False,
            )
            for state in course_states
            if not state.is_gpa_neutral
        ]

        # Steps 20–22 — Layer 2: personalized distribution
        has_any_history = any(
            s.historical_grade_points is not None for s in course_states
        )

        if has_any_history:
            personalized_targets: list[CourseTarget] = []

            for state in course_states:
                if state.is_gpa_neutral:
                    continue

                # System-designed thresholds — ALE_Step5 §Phase5
                _HIGH_HISTORY_THRESHOLD = 3.2  # B+ — strong prerequisite performance → push up
                _LOW_HISTORY_THRESHOLD  = 2.4  # C  — weak prerequisite performance  → pull down
                _PERSONALIZATION_DELTA  = 0.4  # adjustment magnitude (±)

                if state.historical_grade_points is not None:
                    if state.historical_grade_points >= _HIGH_HISTORY_THRESHOLD:
                        raw_target = min(
                            required_average + _PERSONALIZATION_DELTA,
                            state.max_grade_points,
                        )
                        is_personalized = True
                    elif state.historical_grade_points <= _LOW_HISTORY_THRESHOLD:
                        raw_target = max(
                            required_average - _PERSONALIZATION_DELTA,
                            0.0,
                        )
                        is_personalized = True
                    else:
                        raw_target = required_average
                        is_personalized = False
                else:
                    raw_target = required_average
                    is_personalized = False

                # Step 21 — apply retake cap to personalized target
                # is_personalized is already set above; cap does not change it
                capped_target, override = _cap_grade_points_for_course(
                    state.course, raw_target, grading_scale, retake_rules
                )
                if override is not None:
                    grade_overrides.append(override)

                personalized_targets.append(CourseTarget(
                    course_code=state.course.course_code,
                    course_name=state.course.course_name,
                    credits=state.course.credits,
                    target_grade_points=capped_target,
                    target_grade_letter=_grade_points_to_letter_round_up(
                        capped_target, grading_scale
                    ),
                    is_personalized=is_personalized,
                ))

            personalized_distribution = personalized_targets

            # Step 22 — validate total quality points
            total_personalized_qp = sum(
                ct.target_grade_points * ct.credits for ct in personalized_targets
            )
            if total_personalized_qp < required_new_points:
                personalized_distribution_valid = False
                warnings.append(
                    "Personalized distribution does not mathematically satisfy"
                    " target — advisory only."
                )
            else:
                personalized_distribution_valid = True

    # -----------------------------------------------------------------------
    # Phase 6 — Multi-Semester Projection (only when impossible)
    # -----------------------------------------------------------------------

    multi_semester_projection: MultiSemesterProjection | None = None

    if status == "impossible":
        assumed_grade_points, assumed_grade_letter = _resolve_assumed_grade(
            input.assumed_grade_per_semester, grading_scale
        )

        # Step 24 — initialize from stable current snapshot
        proj_qp: float = input.current_quality_points
        proj_denom: float = float(input.gpa_counted_credits)
        proj_cgpa: float = input.current_cgpa
        semester_count = 0
        projected_cgpa_per_semester: list[float] = []
        projection_status = "reachable"

        # Step 25 — iterate
        while proj_cgpa < input.target_cgpa:
            proj_qp    += assumed_grade_points * input.credits_per_semester
            proj_denom += input.credits_per_semester
            proj_cgpa   = round(proj_qp / proj_denom, 2)
            semester_count += 1
            projected_cgpa_per_semester.append(proj_cgpa)

            if semester_count >= graduation_rules.maximum_regular_semesters:
                projection_status = "cannot_reach_within_program"
                break

        multi_semester_projection = MultiSemesterProjection(
            status=projection_status,
            semesters_needed=semester_count if projection_status == "reachable" else None,
            assumed_grade_points=assumed_grade_points,
            assumed_grade_letter=assumed_grade_letter,
            credits_per_semester_used=input.credits_per_semester,
            projected_cgpa_per_semester=projected_cgpa_per_semester,
        )

    # -----------------------------------------------------------------------
    # Phase 7 — Output Assembly
    # -----------------------------------------------------------------------

    return SolveTargetGPAOutput(
        status=status,
        reason_codes=reason_codes,
        warnings=warnings,
        required_data_missing=[],
        current_cgpa=input.current_cgpa,
        target_cgpa=input.target_cgpa,
        delta_needed=delta_needed,
        required_average_grade_points=required_average if status == "solvable" else None,
        required_average_letter=required_average_letter,
        gpa_neutral_courses=gpa_neutral_courses,
        default_distribution=default_distribution,
        personalized_distribution=personalized_distribution,
        personalized_distribution_valid=personalized_distribution_valid,
        grade_overrides=grade_overrides,
        maximum_reachable_cgpa=maximum_reachable_cgpa,
        multi_semester_projection=multi_semester_projection,
        planned_course_source=input.planned_course_source,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _cannot_compute(
    reason_codes: list[str],
    required_data_missing: list[str],
    inp: SolveTargetGPAInput,
) -> SolveTargetGPAOutput:
    return SolveTargetGPAOutput(
        status="cannot_compute",
        reason_codes=reason_codes,
        required_data_missing=required_data_missing,
        current_cgpa=inp.current_cgpa,
        target_cgpa=inp.target_cgpa,
        delta_needed=None,
        required_average_grade_points=None,
        required_average_letter=None,
        planned_course_source=inp.planned_course_source,
    )


def _max_overall_grade_points(grading_scale: GradingScaleRules) -> float:
    """Maximum grade points in the grading scale — excludes P-grade (None entries)."""
    return max(v for v in grading_scale.letter_to_points.values() if v is not None)


def _course_max_grade_points(
    course: PlannedCourseTarget,
    retake_rules: RetakeRules,
    grading_scale: GradingScaleRules,
    max_overall_gp: float,
) -> float:
    """Per-course maximum achievable grade points considering retake caps (Step 15)."""
    if course.attempt_type == "first_attempt":
        return max_overall_gp

    if course.attempt_type == "failed_retake":
        cap = grading_scale.letter_to_points.get(retake_rules.failed_first_retake_grade_cap)
        return cap if cap is not None else max_overall_gp

    if course.attempt_type == "improve_retake":
        number = course.improve_retake_number if course.improve_retake_number is not None else 1
        if number >= 2:
            cap = grading_scale.letter_to_points.get(retake_rules.improve_retake_subsequent_cap)
            return cap if cap is not None else max_overall_gp
        # num=1: improve_retake_first_attempt_cap=None means no cap (handbook)
        if retake_rules.improve_retake_first_attempt_cap is None:
            return max_overall_gp
        cap = grading_scale.letter_to_points.get(retake_rules.improve_retake_first_attempt_cap)
        return cap if cap is not None else max_overall_gp

    return max_overall_gp


def _cap_grade_points_for_course(
    course: PlannedCourseTarget,
    grade_points: float,
    grading_scale: GradingScaleRules,
    retake_rules: RetakeRules,
) -> tuple[float, GradeOverride | None]:
    """Apply retake cap to a target grade points value (Step 21).

    Returns (capped grade points, GradeOverride) if a cap was applied, else (original, None).
    """
    cap_letter: str | None = None
    cap_reason = ""

    if course.attempt_type == "failed_retake":
        cap_letter = retake_rules.failed_first_retake_grade_cap
        cap_reason = (
            f"First retake after failure — maximum grade is "
            f"{retake_rules.failed_first_retake_grade_cap}"
        )

    elif course.attempt_type == "improve_retake":
        number = course.improve_retake_number if course.improve_retake_number is not None else 1
        if number == 1:
            if retake_rules.improve_retake_first_attempt_cap is None:
                return grade_points, None
            cap_letter = retake_rules.improve_retake_first_attempt_cap
            cap_reason = "First improve retake cap applied"
        else:
            cap_letter = retake_rules.improve_retake_subsequent_cap
            cap_reason = (
                f"Subsequent improve retake — maximum grade is "
                f"{retake_rules.improve_retake_subsequent_cap}"
            )

    if cap_letter is None:
        return grade_points, None

    cap_points = grading_scale.letter_to_points.get(cap_letter)
    if cap_points is None:
        # Cap letter maps to P — unexpected configuration; do not cap
        return grade_points, None

    if grade_points > cap_points:
        override = GradeOverride(
            course_code=course.course_code,
            requested_grade=grade_points,
            applied_grade=cap_letter,
            cap_reason=cap_reason,
        )
        return cap_points, override

    return grade_points, None


def _grade_points_to_letter_round_up(
    target_points: float,
    grading_scale: GradingScaleRules,
) -> str:
    """Find the minimum grade point value >= target_points and return its letter.

    Rounding UP guarantees meeting the target: this letter achieves at least the
    required grade points.
    """
    valid_entries = sorted(
        [
            (pts, letter)
            for letter, pts in grading_scale.letter_to_points.items()
            if pts is not None
        ],
        key=lambda x: x[0],
    )
    for pts, letter in valid_entries:
        if pts >= target_points:
            return letter
    # target_points exceeds the highest configured grade — return the maximum
    return valid_entries[-1][1]


def _resolve_assumed_grade(
    assumed_grade: str | float | None,
    grading_scale: GradingScaleRules,
) -> tuple[float, str]:
    """Resolve the multi-semester projection assumed grade to (grade_points, letter).

    Defaults to B if None, unrecognized, or P-grade. Default is a system design
    choice — ALE_Step5 §Phase6.
    """
    _DEFAULT_LETTER = "B"

    def _default() -> tuple[float, str]:
        pts = grading_scale.letter_to_points.get(_DEFAULT_LETTER)
        if pts is None:
            pts = 0.0  # degenerate: B not configured in grading scale
        return pts, _DEFAULT_LETTER

    if assumed_grade is None:
        return _default()

    if isinstance(assumed_grade, str):
        if assumed_grade not in grading_scale.letter_to_points:
            return _default()
        pts = grading_scale.letter_to_points[assumed_grade]
        if pts is None:  # P-grade
            return _default()
        return pts, assumed_grade

    # Numeric input
    try:
        pts = resolve_grade(float(assumed_grade), grading_scale, "_projection")
    except GradeResolutionError:
        return _default()

    if pts is None:  # P-grade from numeric (should not occur)
        return _default()

    # Find nearest letter for display
    letter = min(
        (
            (abs(v - pts), ltr)
            for ltr, v in grading_scale.letter_to_points.items()
            if v is not None
        ),
        key=lambda x: x[0],
    )[1]
    return pts, letter

