"""
ale/functions/simulate_gpa_forward.py
======================================
Implements simulate_gpa_forward following the Step 5 algorithm exactly,
phase by phase.
"""

from dataclasses import dataclass

from engines.ale.ale_schemas import (
    CourseContribution,
    GradeOverride,
    GradingScaleRules,
    PlannedCourseGPA,
    RetakeRules,
    SimulateGPAForwardInput,
    SimulateGPAForwardOutput,
)
from engines.ale.utils.grade_resolver import GradeResolutionError, resolve_grade


# ---------------------------------------------------------------------------
# Internal per-course state — populated during Phases 2 and 3
# ---------------------------------------------------------------------------

@dataclass
class _CourseState:
    course: PlannedCourseGPA
    is_gpa_neutral: bool
    resolved_grade_points: float | None   # expected_grade resolved; None = P-grade
    old_grade_points: float | None        # old_grade resolved; None if not applicable or P
    applied_grade_points: float | None    # after retake cap; None if gpa_neutral
    grade_override: GradeOverride | None
    in_progress_note: str | None
    anomaly_warning: str | None           # zero-credit with unexpected non-P grade


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def simulate_gpa_forward(input: SimulateGPAForwardInput) -> SimulateGPAForwardOutput:
    """Simulate projected CGPA given a set of planned courses with hypothetical grades."""

    # -----------------------------------------------------------------------
    # Phase 1 — Structural Validation
    # -----------------------------------------------------------------------

    # Step 1
    if not input.planned_courses:
        return _cannot_compute(["empty_planned_courses"], [], input.current_cgpa)

    # Step 2
    required_data_missing: list[str] = []
    for course in input.planned_courses:
        if course.credits is None:  # type: ignore[comparison-overlap]
            required_data_missing.append(f"missing_credits_{course.course_code}")
        if course.expected_grade is None:  # type: ignore[comparison-overlap]
            required_data_missing.append(f"missing_expected_grade_{course.course_code}")
        if course.has_cgpa_footprint and course.old_grade is None:
            required_data_missing.append(f"missing_old_grade_{course.course_code}")

    # Step 3
    if required_data_missing:
        return _cannot_compute(["required_data_missing"], required_data_missing, input.current_cgpa)

    # Step 4
    if input.current_quality_points is None:  # type: ignore[comparison-overlap]
        return _cannot_compute(["missing_current_quality_points"], [], input.current_cgpa)

    # Step 4b — gpa_counted_credits guard (model_construct() bypass protection)
    if input.gpa_counted_credits is None:  # type: ignore[comparison-overlap]
        return _cannot_compute(["missing_gpa_counted_credits"], [], input.current_cgpa)

    # Step 4c — attempt_type validity guard (belt-and-suspenders against model_construct() bypass)
    _VALID_ATTEMPT_TYPES = frozenset({"first_attempt", "failed_retake", "improve_retake"})
    for course in input.planned_courses:
        if course.attempt_type not in _VALID_ATTEMPT_TYPES:
            return _cannot_compute(
                [f"invalid_attempt_type_{course.course_code}"],
                [],
                input.current_cgpa,
            )

    # -----------------------------------------------------------------------
    # Phase 2 — Grade Resolution  +  Phase 3 — Retake Grade Cap
    # (interleaved per course to avoid a second pass)
    # -----------------------------------------------------------------------

    grading_scale = input.grading_scale_rules
    retake_rules = input.retake_rules

    course_states: list[_CourseState] = []
    grade_overrides: list[GradeOverride] = []
    gpa_neutral_courses: list[str] = []

    for course in input.planned_courses:

        # Phase 2 — resolve expected_grade (Step 5 / Step 6)
        try:
            resolved_grade_points = resolve_grade(
                course.expected_grade, grading_scale, course.course_code
            )
        except GradeResolutionError:
            return _cannot_compute(
                [f"invalid_grade_input_{course.course_code}"], [], input.current_cgpa
            )

        is_gpa_neutral = resolved_grade_points is None  # P-grade → gpa_neutral

        # Step 7 — zero-credit course with non-P grade: data anomaly → gpa_neutral
        anomaly_warning: str | None = None
        if course.credits == 0 and not is_gpa_neutral:
            is_gpa_neutral = True
            anomaly_warning = f"zero_credit_course_unexpected_grade_{course.course_code}"

        # Phase 2 — resolve old_grade (same three-format logic, only when has_cgpa_footprint)
        old_grade_points: float | None = None
        if course.has_cgpa_footprint:
            try:
                old_grade_points = resolve_grade(
                    course.old_grade,  # type: ignore[arg-type]
                    grading_scale,
                    course.course_code,
                )
            except GradeResolutionError:
                return _cannot_compute(
                    [f"invalid_grade_input_{course.course_code}"], [], input.current_cgpa
                )
            # Edge case: old_grade is P → course was never in GPA denominator; treat as neutral
            if old_grade_points is None:
                is_gpa_neutral = True

        # Phase 3 — apply retake grade cap (Steps 8–10)
        applied_grade_points = resolved_grade_points
        grade_override: GradeOverride | None = None

        if not is_gpa_neutral and applied_grade_points is not None:
            applied_grade_points, grade_override = _apply_retake_cap(
                course, applied_grade_points, grading_scale, retake_rules
            )
            if grade_override is not None:
                grade_overrides.append(grade_override)

        if is_gpa_neutral:
            gpa_neutral_courses.append(course.course_code)

        in_progress_note: str | None = None
        if course.is_currently_in_progress:
            in_progress_note = (
                "Currently in progress — grade is hypothetical until officially released."
            )

        course_states.append(_CourseState(
            course=course,
            is_gpa_neutral=is_gpa_neutral,
            resolved_grade_points=resolved_grade_points,
            old_grade_points=old_grade_points,
            applied_grade_points=applied_grade_points,
            grade_override=grade_override,
            in_progress_note=in_progress_note,
            anomaly_warning=anomaly_warning,
        ))

    # -----------------------------------------------------------------------
    # Phase 4 — GPA Computation
    # -----------------------------------------------------------------------

    all_gpa_neutral = all(s.is_gpa_neutral for s in course_states)

    # Step 11
    running_quality_points: float = input.current_quality_points
    running_denominator: float = input.gpa_counted_credits

    if not all_gpa_neutral:
        # Step 12
        for state in course_states:
            if state.is_gpa_neutral:
                continue
            if state.course.has_cgpa_footprint:
                # Replacement — EUI replacement policy: subtract old, add new; denominator unchanged
                running_quality_points -= state.old_grade_points * state.course.credits  # type: ignore[operator]
                running_quality_points += state.applied_grade_points * state.course.credits  # type: ignore[operator]
            else:
                # Addition — new entry in GPA
                running_quality_points += state.applied_grade_points * state.course.credits  # type: ignore[operator]
                running_denominator += state.course.credits

        # Step 13
        if running_denominator == 0:
            return _cannot_compute(["zero_gpa_denominator"], [], input.current_cgpa)

        projected_cgpa = round(running_quality_points / running_denominator, 2)
        delta = round(projected_cgpa - input.current_cgpa, 2)
    else:
        # Step 14 — all courses gpa_neutral: CGPA unchanged, status still projected
        projected_cgpa = input.current_cgpa
        delta = 0.0

    # -----------------------------------------------------------------------
    # Phase 5 — Warnings Assembly
    # -----------------------------------------------------------------------

    warnings: list[str] = []

    # Anomaly warnings from Phase 2 (zero-credit with non-P grade)
    for state in course_states:
        if state.anomaly_warning is not None:
            warnings.append(state.anomaly_warning)

    # 1. Excluded in-progress courses
    if input.excluded_in_progress_courses:
        course_list = ", ".join(input.excluded_in_progress_courses)
        warnings.append(
            "Projection uses your official CGPA snapshot. The following in-progress courses "
            f"are excluded and will affect your GPA when grades are released: {course_list}."
        )

    # 2. Per-course in-progress note is in CourseContribution.in_progress_note — no global warning

    # 3. Grade overrides applied
    if grade_overrides:
        warnings.append("One or more grades were adjusted downward due to retake caps.")

    # 4 / 5. Pass/fail warning — mutually exclusive
    if all_gpa_neutral:
        warnings.append("All provided courses are pass/fail. No change to CGPA.")
    elif gpa_neutral_courses:
        warnings.append("One or more courses are pass/fail and do not affect CGPA.")

    # -----------------------------------------------------------------------
    # Phase 6 — Output Assembly
    # -----------------------------------------------------------------------

    per_course_breakdown = [_build_course_contribution(state) for state in course_states]

    return SimulateGPAForwardOutput(
        status="projected",
        reason_codes=[],
        warnings=warnings,
        required_data_missing=[],
        current_cgpa=input.current_cgpa,
        projected_cgpa=projected_cgpa,
        delta=delta,
        per_course_breakdown=per_course_breakdown,
        grade_overrides=grade_overrides,
        gpa_neutral_courses=gpa_neutral_courses,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _cannot_compute(
    reason_codes: list[str],
    required_data_missing: list[str],
    current_cgpa: float,
) -> SimulateGPAForwardOutput:
    return SimulateGPAForwardOutput(
        status="cannot_compute",
        reason_codes=reason_codes,
        required_data_missing=required_data_missing,
        current_cgpa=current_cgpa,
        projected_cgpa=None,
        delta=None,
    )


def _apply_retake_cap(
    course: PlannedCourseGPA,
    resolved_points: float,
    grading_scale: GradingScaleRules,
    retake_rules: RetakeRules,
) -> tuple[float, GradeOverride | None]:
    """Apply retake grade cap if the attempt type requires one (Steps 8–10).

    Returns the (possibly capped) grade points and a GradeOverride record if a cap was applied.
    """
    # Step 8 — first attempt: no cap
    if course.attempt_type == "first_attempt":
        return resolved_points, None

    cap_letter: str | None = None
    cap_reason: str = ""

    # Step 9 — failed retake: cap at failed_first_retake_grade_cap (handbook: B)
    if course.attempt_type == "failed_retake":
        cap_letter = retake_rules.failed_first_retake_grade_cap
        cap_reason = (
            f"First retake after failure — maximum grade is "
            f"{retake_rules.failed_first_retake_grade_cap}"
        )

    # Step 10 — improve retake: depends on attempt number
    elif course.attempt_type == "improve_retake":
        number = course.improve_retake_number if course.improve_retake_number is not None else 1
        if number == 1:
            # improve_retake_first_attempt_cap is None → no cap (handbook)
            if retake_rules.improve_retake_first_attempt_cap is None:
                return resolved_points, None
            cap_letter = retake_rules.improve_retake_first_attempt_cap
            cap_reason = "First improve retake cap applied"
        else:  # number >= 2
            cap_letter = retake_rules.improve_retake_subsequent_cap
            cap_reason = (
                f"Subsequent improve retake — maximum grade is "
                f"{retake_rules.improve_retake_subsequent_cap}"
            )

    if cap_letter is None:
        return resolved_points, None

    cap_points = grading_scale.letter_to_points.get(cap_letter)
    if cap_points is None:
        # Cap letter maps to P — unexpected rules configuration; do not cap
        return resolved_points, None

    if resolved_points > cap_points:
        override = GradeOverride(
            course_code=course.course_code,
            requested_grade=course.expected_grade,
            applied_grade=cap_letter,
            cap_reason=cap_reason,
        )
        return cap_points, override

    return resolved_points, None


def _build_course_contribution(state: _CourseState) -> CourseContribution:
    if state.is_gpa_neutral:
        footprint_action = "gpa_neutral"
        quality_points_contribution = None
    elif state.course.has_cgpa_footprint:
        footprint_action = "replacement"
        quality_points_contribution = (
            state.applied_grade_points * state.course.credits  # type: ignore[operator]
        )
    else:
        footprint_action = "addition"
        quality_points_contribution = (
            state.applied_grade_points * state.course.credits  # type: ignore[operator]
        )

    return CourseContribution(
        course_code=state.course.course_code,
        course_name=state.course.course_name,
        credits=state.course.credits,
        input_grade=state.course.expected_grade,
        resolved_grade_points=state.resolved_grade_points,
        applied_grade_points=state.applied_grade_points,
        quality_points_contribution=quality_points_contribution,
        footprint_action=footprint_action,
        in_progress_note=state.in_progress_note,
    )

