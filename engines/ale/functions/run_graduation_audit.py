"""
ale/functions/run_graduation_audit.py
======================================
Implements run_graduation_audit following ALE_Step5_Algorithms.md Function 4,
phase by phase.

Performs graduation eligibility checking and honors sub-computation.
No GPA math or grade resolution — all inputs are pre-computed by the orchestrator.
"""

from itertools import groupby

from engines.ale.schemas import (
    AcademicWarningRules,
    AuditCheck,
    CourseHistoryEntry,
    GraduationRequirementRules,
    HonorsCheck,
    HonorsRules,
    RunGraduationAuditInput,
    RunGraduationAuditOutput,
)

_NOT_AUDITABLE_STATUSES = frozenset({"Transferred Out", "Suspended", "Frozen"})


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def run_graduation_audit(input: RunGraduationAuditInput) -> RunGraduationAuditOutput:
    """Audit graduation eligibility and compute honors sub-results for a student."""

    # -----------------------------------------------------------------------
    # Phase 1 — Structural Validation
    # -----------------------------------------------------------------------

    required_data_missing: list[str] = []
    if input.study_status is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("study_status")
    if input.completed_courses is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("completed_courses")
    if input.in_progress_courses is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("in_progress_courses")
    if input.current_cgpa is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("current_cgpa")
    if input.cumulative_passed_hours is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("cumulative_passed_hours")
    if input.consecutive_warnings is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("consecutive_warnings")
    if input.total_warnings is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("total_warnings")
    if input.completed_regular_semesters is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("completed_regular_semesters")
    if input.course_history is None:  # type: ignore[comparison-overlap]
        required_data_missing.append("course_history")

    if required_data_missing:
        return _cannot_compute(required_data_missing)

    # -----------------------------------------------------------------------
    # Phase 2 — Study Status Gate
    # -----------------------------------------------------------------------

    if input.study_status in _NOT_AUDITABLE_STATUSES:
        reason = f"study_status_{input.study_status.lower().replace(' ', '_')}"
        return RunGraduationAuditOutput(
            status="not_auditable",
            reason_codes=[reason],
            current_cgpa=None,
            honors_status=None,
            honors_checks=[],
        )

    if input.study_status == "Graduated":
        honors_checks, honors_status = _compute_honors(
            input.course_history,
            input.completed_regular_semesters,
            input.honors_rules,
            is_graduated=True,
        )
        return RunGraduationAuditOutput(
            status="already_graduated",
            current_cgpa=input.current_cgpa,
            honors_status=honors_status,
            honors_checks=honors_checks,
        )

    # study_status == "Studying" — proceed to full audit

    # -----------------------------------------------------------------------
    # Phase 3 — Graduation Checks (all checks run; no short-circuit)
    # -----------------------------------------------------------------------

    graduation_rules = input.graduation_rules
    warning_rules = input.warning_rules
    checks: list[AuditCheck] = []
    dismissed = False
    dismissal_kind = ""

    # Check 1 — Warning Dismissal
    dismissed = (
        input.consecutive_warnings >= warning_rules.max_consecutive_warnings
        or input.total_warnings >= warning_rules.max_total_warnings
    )
    warning_actual = (
        f"{input.consecutive_warnings} consecutive, {input.total_warnings} total"
    )
    warning_required = (
        f"< {warning_rules.max_consecutive_warnings} consecutive "
        f"AND < {warning_rules.max_total_warnings} total"
    )
    if dismissed:
        appeal_threshold = (
            graduation_rules.total_credits_required
            * warning_rules.dismissal_extension_credits_percentage
        )
        dismissal_kind = (
            "dismissed_but_appeal_eligible"
            if input.cumulative_passed_hours >= appeal_threshold
            else "dismissed_no_appeal"
        )
        dismissal_gap = (
            "Dismissed — appeal eligible"
            if dismissal_kind == "dismissed_but_appeal_eligible"
            else "Dismissed — no appeal"
        )
        checks.append(AuditCheck(
            name="warning_dismissal_check",
            passed=False,
            actual_value=warning_actual,
            required_value=warning_required,
            gap=dismissal_gap,
        ))
    else:
        checks.append(AuditCheck(
            name="warning_dismissal_check",
            passed=True,
            actual_value=warning_actual,
            required_value=warning_required,
            gap=None,
        ))

    # Check 2 — Military Training (skipped entirely when military_status is None → female)
    if input.military_status is not None:
        military_passed = input.military_status in ("Done", "Exempted")
        checks.append(AuditCheck(
            name="military_check",
            passed=military_passed,
            actual_value=input.military_status,
            required_value="Done or Exempted",
            gap=None if military_passed else "Military training not completed",
        ))

    # Check 3 — Minimum CGPA
    cgpa_passed = input.current_cgpa >= graduation_rules.minimum_cgpa
    checks.append(AuditCheck(
        name="cgpa_check",
        passed=cgpa_passed,
        actual_value=input.current_cgpa,
        required_value=graduation_rules.minimum_cgpa,
        gap=(
            None if cgpa_passed
            else f"{graduation_rules.minimum_cgpa - input.current_cgpa:.2f} points below minimum"
        ),
    ))

    # Check 4 — Credit Hours
    credits_passed = input.cumulative_passed_hours >= graduation_rules.total_credits_required
    checks.append(AuditCheck(
        name="credits_check",
        passed=credits_passed,
        actual_value=input.cumulative_passed_hours,
        required_value=graduation_rules.total_credits_required,
        gap=(
            None if credits_passed
            else (
                f"{graduation_rules.total_credits_required - input.cumulative_passed_hours}"
                " credits remaining"
            )
        ),
    ))

    # Check 5 — Minimum Regular Semesters
    semesters_passed = (
        input.completed_regular_semesters >= graduation_rules.minimum_regular_semesters
    )
    checks.append(AuditCheck(
        name="semesters_check",
        passed=semesters_passed,
        actual_value=input.completed_regular_semesters,
        required_value=graduation_rules.minimum_regular_semesters,
        gap=(
            None if semesters_passed
            else (
                f"{graduation_rules.minimum_regular_semesters - input.completed_regular_semesters}"
                " semester(s) remaining"
            )
        ),
    ))

    # Check 6 — Zero-Credit Courses
    zero_credit_passed = input.zero_credit_courses_passed
    checks.append(AuditCheck(
        name="zero_credit_check",
        passed=zero_credit_passed,
        actual_value=input.zero_credit_courses_passed,
        required_value=True,
        gap=None if zero_credit_passed else "Zero-credit course requirements not met",
    ))

    # -----------------------------------------------------------------------
    # Phase 4 — Final Status and Next Steps
    # Phase 5 — Honors Computation (runs for both Studying and dismissed)
    # -----------------------------------------------------------------------

    honors_checks, honors_status = _compute_honors(
        input.course_history,
        input.completed_regular_semesters,
        input.honors_rules,
        is_graduated=False,
    )

    if dismissed:
        return RunGraduationAuditOutput(
            status=dismissal_kind,
            reason_codes=[dismissal_kind],
            current_cgpa=input.current_cgpa,
            checks=checks,
            next_steps=["Contact academic advising regarding dismissal."],
            honors_status=honors_status,
            honors_checks=honors_checks,
        )

    all_passed = all(c.passed for c in checks)
    final_status = "eligible" if all_passed else "not_eligible"
    next_steps = _build_next_steps(checks, graduation_rules, input, final_status)

    # -----------------------------------------------------------------------
    # Phase 6 — Output Assembly
    # -----------------------------------------------------------------------

    return RunGraduationAuditOutput(
        status=final_status,
        reason_codes=[],
        current_cgpa=input.current_cgpa,
        checks=checks,
        next_steps=next_steps,
        honors_status=honors_status,
        honors_checks=honors_checks,
    )


# ---------------------------------------------------------------------------
# Private helpers — Phase 4
# ---------------------------------------------------------------------------

def _build_next_steps(
    checks: list[AuditCheck],
    graduation_rules: GraduationRequirementRules,
    input: RunGraduationAuditInput,
    status: str,
) -> list[str]:
    if status == "eligible":
        return ["Submit graduation application to registrar."]

    steps: list[str] = []
    for check in checks:
        if check.passed:
            continue
        if check.name == "credits_check":
            remaining = (
                graduation_rules.total_credits_required - input.cumulative_passed_hours
            )
            steps.append(f"Complete {remaining} more credit hour(s).")
        elif check.name == "cgpa_check":
            gap = graduation_rules.minimum_cgpa - input.current_cgpa
            steps.append(
                f"Raise CGPA by {gap:.2f} points to meet the "
                f"{graduation_rules.minimum_cgpa} minimum."
            )
        elif check.name == "semesters_check":
            remaining = (
                graduation_rules.minimum_regular_semesters
                - input.completed_regular_semesters
            )
            steps.append(f"Complete {remaining} more regular semester(s).")
        elif check.name == "military_check":
            steps.append("Complete or obtain exemption for military training.")
        elif check.name == "zero_credit_check":
            steps.append("Ensure all zero-credit course requirements are met.")
        elif check.name == "warning_dismissal_check":
            steps.append("Contact academic advising regarding dismissal.")
    return steps


# ---------------------------------------------------------------------------
# Private helpers — Phase 5 (Honors)
# ---------------------------------------------------------------------------

def _compute_honors(
    course_history: list[CourseHistoryEntry],
    completed_regular_semesters: int,
    honors_rules: HonorsRules,
    is_graduated: bool,
) -> tuple[list[HonorsCheck], str]:
    honors_checks = [
        _honors_cgpa_trajectory(course_history, honors_rules),
        _honors_semester_count(completed_regular_semesters, honors_rules, is_graduated),
        _honors_no_failures(course_history),
        _honors_disciplinary(),
    ]

    honors_status = "cannot_fully_verify"
    for check in honors_checks:
        if check.passed is False:
            honors_status = "not_eligible"
            break

    return honors_checks, honors_status


def _honors_cgpa_trajectory(
    course_history: list[CourseHistoryEntry],
    honors_rules: HonorsRules,
) -> HonorsCheck:
    """Step 13 — compute CGPA at every semester boundary using replacement-aware grouping."""
    sorted_history = sorted(course_history, key=_semester_sort_key)
    course_grade_map: dict[str, tuple[float, int]] = {}  # code → (grade_points, credits)
    min_cgpa_seen: float | None = None
    passed = True
    any_boundary = False

    for _, semester_entries in groupby(sorted_history, key=lambda e: e.semester):
        for entry in semester_entries:
            if (
                entry.status in ("passed", "failed")
                and entry.credits > 0
                and entry.grade_points is not None
            ):
                # Latest graded attempt always overwrites previous — replacement-aware
                course_grade_map[entry.course_code] = (entry.grade_points, entry.credits)

        if course_grade_map:
            total_qp = sum(gp * cr for gp, cr in course_grade_map.values())
            total_cr = sum(cr for _, cr in course_grade_map.values())
            if total_cr > 0:
                boundary_cgpa = round(total_qp / total_cr, 2)
                any_boundary = True
                if min_cgpa_seen is None or boundary_cgpa < min_cgpa_seen:
                    min_cgpa_seen = boundary_cgpa
                if boundary_cgpa < honors_rules.minimum_cgpa_throughout:
                    passed = False

    # Step 13.6 — no boundaries yet → vacuously True
    actual_value = (
        f"Min CGPA recorded: {min_cgpa_seen:.2f}" if any_boundary
        else "No graded semesters yet"
    )
    return HonorsCheck(
        name="cgpa_throughout",
        passed=passed,
        actual_value=actual_value,
        required_value=honors_rules.minimum_cgpa_throughout,
    )


def _honors_semester_count(
    completed_regular_semesters: int,
    honors_rules: HonorsRules,
    is_graduated: bool,
) -> HonorsCheck:
    """Step 14 — check whether semester count is within the allowed 6–8 range."""
    required_value = (
        f"{honors_rules.minimum_semesters}–{honors_rules.maximum_semesters} semesters"
    )

    if is_graduated:
        passed: bool | None = (
            honors_rules.minimum_semesters
            <= completed_regular_semesters
            <= honors_rules.maximum_semesters
        )
        note = None
    elif completed_regular_semesters > honors_rules.maximum_semesters:
        passed = False  # definitively eliminated — cannot go back below the cap
        note = None
    elif completed_regular_semesters >= honors_rules.minimum_semesters:
        passed = True   # currently in valid range
        note = None
    else:
        passed = None   # below minimum but still achievable
        note = "Still achievable"

    return HonorsCheck(
        name="semester_count",
        passed=passed,
        actual_value=completed_regular_semesters,
        required_value=required_value,
        note=note,
    )


def _honors_no_failures(course_history: list[CourseHistoryEntry]) -> HonorsCheck:
    """Step 15 — any entry with status=failed (includes Abs) disqualifies from honors."""
    has_failure = any(entry.status == "failed" for entry in course_history)
    passed = not has_failure
    return HonorsCheck(
        name="no_failures",
        passed=passed,
        actual_value="No failures on record" if passed else "F or Abs grade found on record",
        required_value="No F or Abs grades",
    )


def _honors_disciplinary() -> HonorsCheck:
    """Step 16 — disciplinary check is always unverifiable by the system."""
    return HonorsCheck(
        name="no_disciplinary_penalties",
        passed=None,
        actual_value=None,
        required_value="No disciplinary record",
        note="Contact registrar office to verify.",
    )


def _semester_sort_key(entry: CourseHistoryEntry) -> tuple[int, int]:
    """Parse 'Season YYYY' into (year, season_index) for chronological sorting."""
    _SEASON_ORDER = {"Spring": 0, "Summer": 1, "Fall": 2}
    parts = entry.semester.split()
    try:
        year = int(parts[1])
    except (IndexError, ValueError):
        year = 0
    season = _SEASON_ORDER.get(parts[0] if parts else "", 3)
    return (year, season)


# ---------------------------------------------------------------------------
# Private helpers — early exits
# ---------------------------------------------------------------------------

def _cannot_compute(required_data_missing: list[str]) -> RunGraduationAuditOutput:
    return RunGraduationAuditOutput(
        status="cannot_compute",
        reason_codes=["required_data_missing"],
        required_data_missing=required_data_missing,
        current_cgpa=None,
        honors_status=None,
        honors_checks=[],
    )

