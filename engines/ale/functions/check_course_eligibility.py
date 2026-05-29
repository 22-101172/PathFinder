"""
ale/functions/check_course_eligibility.py
==========================================
Implements check_course_eligibility following ALE_Step5_Algorithms.md Function 3,
phase by phase.

No GPA math, no grade resolution. Pure eligibility logic against prerequisites,
credit thresholds, retake rules, and in-progress state.
"""

from engines.ale.schemas import (
    CheckCourseEligibilityInput,
    CheckCourseEligibilityOutput,
    RetakeRules,
)


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def check_course_eligibility(
    input: CheckCourseEligibilityInput,
) -> CheckCourseEligibilityOutput:
    """Determine whether a student is eligible to take or retake a specific course."""

    # -----------------------------------------------------------------------
    # Phase 1 — Structural Validation
    # -----------------------------------------------------------------------

    # Step 1 — required field presence
    required_data_missing: list[str] = []
    if input.target_course_code is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("target_course_code")
    if input.completed_courses is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("completed_courses")
    if input.in_progress_courses is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("in_progress_courses")
    if input.cumulative_passed_hours is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("cumulative_passed_hours")
    if input.attempt_type is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("attempt_type")

    if required_data_missing:
        return _cannot_compute(["required_data_missing"], required_data_missing, input)

    # Step 2 — contradiction checks
    if (
        input.attempt_type == "improve_retake"
        and input.target_course_code not in input.completed_courses
    ):
        return _cannot_compute(
            ["inconsistent_attempt_type_course_not_completed"], [], input
        )

    if (
        input.attempt_type == "failed_retake"
        and input.target_course_code in input.completed_courses
    ):
        return _cannot_compute(
            ["inconsistent_attempt_type_course_already_passed"], [], input
        )

    # -----------------------------------------------------------------------
    # Phase 2 — Sequential Eligibility Checks (first match wins)
    # -----------------------------------------------------------------------

    retake_rules = input.retake_rules
    warnings: list[str] = []
    reason_codes: list[str] = []

    # Initialise prerequisite and threshold outputs (populated in Check C if reached)
    completed_prerequisites: list[str] = []
    missing_prerequisites: list[str] = []
    credit_threshold_required: int | None = input.target_course_credit_threshold
    credit_threshold_met: bool | None = None

    # Check A — In-Progress Guard (Step 3)
    if input.target_course_code in input.in_progress_courses:
        return CheckCourseEligibilityOutput(
            status="in_progress",
            reason_codes=["course_currently_in_progress"],
            warnings=[],
            required_data_missing=[],
            target_course_code=input.target_course_code,
            missing_prerequisites=[],
            completed_prerequisites=[],
            credit_threshold_required=credit_threshold_required,
            credit_threshold_met=None,
            retake_count_for_course=input.retake_count.get(input.target_course_code, 0),
            max_retake_cap=None,
            attempt_type=input.attempt_type,
        )

    # Check B — Already Completed (Step 4)
    if input.target_course_code in input.completed_courses:
        # Sub-case B1 — improve retake
        if input.attempt_type == "improve_retake":
            if input.current_cgpa < retake_rules.improve_retake_unlimited_below_cgpa:
                warnings.append(
                    "No improve retake course limit applies below CGPA "
                    f"{retake_rules.improve_retake_unlimited_below_cgpa}."
                )
                status = "eligible"
                max_retake_cap = None
            elif (
                input.total_improve_retakes_used
                >= retake_rules.improve_retake_max_courses_cgpa_above_2
            ):
                status = "retake_cap_exceeded"
                max_retake_cap = retake_rules.improve_retake_max_courses_cgpa_above_2
            else:
                remaining = (
                    retake_rules.improve_retake_max_courses_cgpa_above_2
                    - input.total_improve_retakes_used
                )
                warnings.append(f"{remaining} improve retake slot(s) remaining.")
                status = "eligible"
                max_retake_cap = None

            return CheckCourseEligibilityOutput(
                status=status,
                reason_codes=[],
                warnings=warnings,
                required_data_missing=[],
                target_course_code=input.target_course_code,
                missing_prerequisites=[],
                completed_prerequisites=[],
                credit_threshold_required=credit_threshold_required,
                credit_threshold_met=None,
                retake_count_for_course=input.retake_count.get(input.target_course_code, 0),
                max_retake_cap=max_retake_cap,
                attempt_type=input.attempt_type,
            )

        # Sub-case B2 — any other attempt type
        return CheckCourseEligibilityOutput(
            status="already_completed",
            reason_codes=[],
            warnings=[],
            required_data_missing=[],
            target_course_code=input.target_course_code,
            missing_prerequisites=[],
            completed_prerequisites=[],
            credit_threshold_required=credit_threshold_required,
            credit_threshold_met=None,
            retake_count_for_course=input.retake_count.get(input.target_course_code, 0),
            max_retake_cap=None,
            attempt_type=input.attempt_type,
        )

    # Check C — Prerequisites and Credit Threshold (Step 5 — only reached here)

    # Step 5a — split prerequisites
    completed_prerequisites = [
        p for p in input.target_course_prerequisites if p in input.completed_courses
    ]
    missing_prerequisites = [
        p for p in input.target_course_prerequisites if p not in input.completed_courses
    ]

    # Step 5b — credit threshold
    if input.target_course_credit_threshold is not None:
        credit_threshold_met = (
            input.cumulative_passed_hours >= input.target_course_credit_threshold
        )

    # Step 6 — determine status
    prerequisites_satisfied = len(missing_prerequisites) == 0
    threshold_satisfied = (
        input.target_course_credit_threshold is None or credit_threshold_met
    )

    if prerequisites_satisfied and threshold_satisfied:
        status = "eligible"
    else:
        status = "not_eligible"
        if missing_prerequisites:
            reason_codes.append("missing_prerequisites")
        if input.target_course_credit_threshold is not None and not credit_threshold_met:
            reason_codes.append("credit_threshold_not_met")

    # -----------------------------------------------------------------------
    # Phase 3 — Retake Count
    # -----------------------------------------------------------------------

    retake_count_for_course = input.retake_count.get(input.target_course_code, 0)

    # -----------------------------------------------------------------------
    # Phase 4 — Warnings
    # -----------------------------------------------------------------------

    # Warning 1 — B cap reminder for failed retake
    if input.attempt_type == "failed_retake":
        warnings.append(
            "B grade cap applies on first retake after failure. Does not affect eligibility."
        )

    # Warning 2 — in-progress missing prerequisites
    for prereq in missing_prerequisites:
        if prereq in input.in_progress_courses:
            warnings.append(
                f"Currently taking {prereq} — may be satisfied next semester if passed."
            )

    # -----------------------------------------------------------------------
    # Phase 5 — Output Assembly
    # -----------------------------------------------------------------------

    return CheckCourseEligibilityOutput(
        status=status,
        reason_codes=reason_codes,
        warnings=warnings,
        required_data_missing=[],
        target_course_code=input.target_course_code,
        missing_prerequisites=missing_prerequisites,
        completed_prerequisites=completed_prerequisites,
        credit_threshold_required=credit_threshold_required,
        credit_threshold_met=credit_threshold_met,
        retake_count_for_course=retake_count_for_course,
        max_retake_cap=None,
        attempt_type=input.attempt_type,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _cannot_compute(
    reason_codes: list[str],
    required_data_missing: list[str],
    inp: CheckCourseEligibilityInput,
) -> CheckCourseEligibilityOutput:
    return CheckCourseEligibilityOutput(
        status="cannot_compute",
        reason_codes=reason_codes,
        required_data_missing=required_data_missing,
        target_course_code=inp.target_course_code or "",
        missing_prerequisites=[],
        completed_prerequisites=[],
        credit_threshold_required=inp.target_course_credit_threshold,
        credit_threshold_met=None,
        retake_count_for_course=0,
        max_retake_cap=None,
        attempt_type=inp.attempt_type or "",
    )

