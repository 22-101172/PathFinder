"""
Smoke tests for the ALE adapter integration.

Verifies that:
  - All imports resolve (Task 1 file copy succeeded)
  - Field mapping compiles (Task 2 adapter and schema changes are correct)
  - All 6 ALE operations return a valid ALE status (never "error")

No business-logic correctness is checked here. Inputs are minimal,
hand-crafted dicts based on STU000001 data.
"""

import pytest

from adapters.ale_adapter import ALEAdapter
from gateway.models.schemas import StudentContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def adapter():
    return ALEAdapter()


@pytest.fixture
def student_ctx():
    return StudentContext(
        student_id="STU000001",
        name="Test Student",
        program="CIS Program - Artificial Intelligence Track",
        track_id="AI",
        level=4,
        first_semester="Fall 2022",
        study_status="Studying",
        current_semester="Spring 2026",
        military_status="Not Yet Deferred",
        cgpa=2.63,
        academic_standing="good",
        total_credit_hours_earned=112,
        cumulative_chs=112,
        cumulative_cps=294.0,
        last_semester_gpa=None,
        last_semester_chs=None,
        last_semester_cps=None,
        last_semester_phs=None,
        current_semester_chs=18,
        consecutive_warnings=0,
        total_warnings=0,
        completed_regular_semesters=6,
        zero_credit_courses_passed=False,
        retake_count={},
        total_improve_retakes_used=0,
        completed_courses=[
            "C-CS111", "C-CS112", "C-CS213", "C-MA111",
            "C-MA112", "C-PH111", "HUM111", "C-AI311",
            "C-AI321", "C-CS316", "C-SW311", "C-CS495",
        ],
        failed_courses=[],
        in_progress_courses=["C-CS435", "C-AI415"],
        planned_courses=[],
        course_history=[],
    )


@pytest.fixture
def rule_bundles():
    return {
        "grading_scale": {
            "letter_to_points": {
                "A+": 4.0, "A": 4.0, "A-": 3.7,
                "B+": 3.3, "B": 3.0, "B-": 2.7,
                "C+": 2.3, "C": 2.0, "C-": 1.7,
                "D+": 1.3, "D": 1.0, "F": 0.0, "P": None,
            },
            "percentage_to_letter": [
                {"min_pct": 97, "max_pct": 100, "letter": "A+"},
                {"min_pct": 93, "max_pct": 96,  "letter": "A"},
                {"min_pct": 90, "max_pct": 92,  "letter": "A-"},
                {"min_pct": 87, "max_pct": 89,  "letter": "B+"},
                {"min_pct": 83, "max_pct": 86,  "letter": "B"},
                {"min_pct": 80, "max_pct": 82,  "letter": "B-"},
                {"min_pct": 77, "max_pct": 79,  "letter": "C+"},
                {"min_pct": 73, "max_pct": 76,  "letter": "C"},
                {"min_pct": 70, "max_pct": 72,  "letter": "C-"},
                {"min_pct": 67, "max_pct": 69,  "letter": "D+"},
                {"min_pct": 60, "max_pct": 66,  "letter": "D"},
                {"min_pct":  0, "max_pct": 59,  "letter": "F"},
            ],
        },
        "retake_rules": {
            "failed_first_retake_grade_cap": "B",
            "improve_retake_first_attempt_cap": None,
            "improve_retake_subsequent_cap": "B",
            "improve_retake_max_courses_cgpa_above_2": 3,
            "improve_retake_unlimited_below_cgpa": 2.0,
        },
        "graduation_rules": {
            "total_credits_required": 133,
            "minimum_cgpa": 2.0,
            "minimum_regular_semesters": 6,
            "maximum_regular_semesters": 16,
            "must_pass_zero_credit_courses": True,
            "military_training_required_for_males": True,
        },
        "warning_rules": {
            "cgpa_warning_threshold": 2.0,
            "max_consecutive_warnings": 4,
            "max_total_warnings": 6,
            "warning_exempt_first_semester": True,
            "dismissal_extension_credits_percentage": 0.80,
            "dismissal_extension_extra_semesters": 2,
            "dismissal_extension_extra_summer_semesters": 1,
        },
        "honors_rules": {
            "minimum_cgpa_throughout": 3.0,
            "minimum_semesters": 6,
            "maximum_semesters": 8,
            "no_f_grade_allowed": True,
            "no_disciplinary_penalties": True,
        },
        "credit_limit_rules": {
            "cgpa_above_3_limit": 21,
            "cgpa_between_2_and_3_limit": 18,
            "cgpa_between_1_and_2_limit": 15,
            "cgpa_below_1_limit": 12,
            "minimum_per_semester": 9,
            "final_semester_override": 21,
            "incomplete_extra_course_allowed": True,
        },
        "summer_rules": {
            "default_max_courses": 2,
            "cgpa_above_3_max_courses": 3,
            "cgpa_threshold_for_extra_course": 3.0,
        },
    }


@pytest.fixture
def minimal_planned_courses():
    """One PlannedCourseGPA-compatible dict (also used as PlannedCourseTarget for solve_target_gpa)."""
    return [
        {
            "course_code": "C-CS499",
            "course_name": "Capstone",
            "credits": 3,
            "expected_grade": "B",
            "has_cgpa_footprint": False,
            "old_grade": None,
            "attempt_type": "first_attempt",
            "improve_retake_number": None,
            "is_currently_in_progress": False,
            "related_completed_course": None,
        }
    ]


@pytest.fixture
def minimal_available_courses():
    """
    Two AvailableCourse-compatible dicts.
    semester_offering is a list[str] as required by AvailableCourse schema.
    C-CS499 is offered in both Fall and Spring; C-AI499 only in Spring.
    """
    return [
        {
            "course_code": "C-CS499",
            "name": "Capstone",
            "credits": 3,
            "level": 4,
            "prerequisites": ["C-CS495"],
            "semester_offering": ["Fall", "Spring"],
            "track": ["AI"],
        },
        {
            "course_code": "C-AI499",
            "name": "AI Project",
            "credits": 3,
            "level": 4,
            "prerequisites": ["C-AI321"],
            "semester_offering": ["Spring"],
            "track": ["AI"],
        },
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_imports_resolve():
    """Verify that all ALE module imports succeed. Fails if Task 1 file copy is broken."""
    from adapters.ale_adapter import ALEAdapter  # noqa: F401
    from engines.ale.functions.simulate_gpa_forward import simulate_gpa_forward  # noqa: F401
    from engines.ale.functions.solve_target_gpa import solve_target_gpa  # noqa: F401
    from engines.ale.functions.check_course_eligibility import check_course_eligibility  # noqa: F401
    from engines.ale.functions.run_graduation_audit import run_graduation_audit  # noqa: F401
    from engines.ale.functions.generate_semester_plan import generate_semester_plan  # noqa: F401
    from engines.ale.functions.generate_graduation_roadmap import generate_graduation_roadmap  # noqa: F401


def test_simulate_gpa_forward(adapter, student_ctx, rule_bundles, minimal_planned_courses, smoke_results):
    result = adapter.call(
        "simulate_gpa_forward",
        student_ctx,
        rule_bundles,
        params={
            "planned_courses": minimal_planned_courses,
            "excluded_in_progress_courses": [],
        },
    )
    status = result.get("status", "N/A")
    passed = status != "error"
    smoke_results["simulate_gpa_forward"] = (status, "PASS" if passed else "FAIL")

    assert passed, f"simulate_gpa_forward returned error: {result.get('message')}"
    assert "projected_cgpa" in result, f"Missing 'projected_cgpa' in result: {result}"


def test_solve_target_gpa(adapter, student_ctx, rule_bundles, minimal_planned_courses, smoke_results):
    # planned_courses is a PlannedCourseTarget-compatible dict; extra fields (expected_grade,
    # is_currently_in_progress) are silently ignored by Pydantic.
    result = adapter.call(
        "solve_target_gpa",
        student_ctx,
        rule_bundles,
        params={
            "target_cgpa": 3.0,
            "planned_courses": minimal_planned_courses,
            "planned_course_source": "orchestrator",
        },
    )
    status = result.get("status", "N/A")
    passed = status != "error"
    smoke_results["solve_target_gpa"] = (status, "PASS" if passed else "FAIL")

    assert passed, f"solve_target_gpa returned error: {result.get('message')}"
    valid_statuses = {"solvable", "impossible", "already_met", "cannot_compute"}
    assert status in valid_statuses, f"Unexpected status '{status}'"


def test_check_course_eligibility(adapter, student_ctx, rule_bundles, smoke_results):
    result = adapter.call(
        "check_course_eligibility",
        student_ctx,
        rule_bundles,
        kg_data={
            "course_prerequisites": ["C-CS495"],
            "course_credit_threshold": None,
        },
        params={
            "target_course_code": "C-CS499",
            "attempt_type": "first_attempt",
        },
    )
    status = result.get("status", "N/A")
    passed = status != "error"
    smoke_results["check_course_eligibility"] = (status, "PASS" if passed else "FAIL")

    assert passed, f"check_course_eligibility returned error: {result.get('message')}"
    valid_statuses = {
        "eligible", "not_eligible", "already_completed",
        "in_progress", "retake_cap_exceeded", "cannot_compute",
    }
    assert status in valid_statuses, f"Unexpected status '{status}'"


def test_run_graduation_audit(adapter, student_ctx, rule_bundles, smoke_results):
    result = adapter.call(
        "run_graduation_audit",
        student_ctx,
        rule_bundles,
    )
    status = result.get("status", "N/A")
    passed = status != "error"
    smoke_results["run_graduation_audit"] = (status, "PASS" if passed else "FAIL")

    assert passed, f"run_graduation_audit returned error: {result.get('message')}"
    valid_statuses = {
        "eligible", "not_eligible", "already_graduated",
        "not_auditable", "dismissed_but_appeal_eligible",
        "dismissed_no_appeal", "cannot_compute",
    }
    assert status in valid_statuses, f"Unexpected status '{status}'"
    assert "checks" in result, f"Missing 'checks' key in result: {result}"


def test_generate_semester_plan(adapter, student_ctx, rule_bundles, minimal_available_courses, smoke_results):
    result = adapter.call(
        "generate_semester_plan",
        student_ctx,
        rule_bundles,
        kg_data={"available_courses": minimal_available_courses},
        params={
            "target_semester_type": "Fall",
            "specialization_credit_threshold": 60,
        },
    )
    status = result.get("status", "N/A")
    passed = status != "error"
    smoke_results["generate_semester_plan"] = (status, "PASS" if passed else "FAIL")

    assert passed, f"generate_semester_plan returned error: {result.get('message')}"
    valid_statuses = {
        "plans_generated", "not_applicable",
        "no_eligible_courses", "cannot_compute",
    }
    assert status in valid_statuses, f"Unexpected status '{status}'"


def test_generate_graduation_roadmap(adapter, student_ctx, rule_bundles, minimal_available_courses, smoke_results):
    result = adapter.call(
        "generate_graduation_roadmap",
        student_ctx,
        rule_bundles,
        kg_data={"available_courses": minimal_available_courses},
        params={
            "target_semester_type": "Fall",
            "starting_year": 2026,
            "specialization_credit_threshold": 60,
        },
    )
    status = result.get("status", "N/A")
    passed = status != "error"
    smoke_results["generate_graduation_roadmap"] = (status, "PASS" if passed else "FAIL")

    assert passed, f"generate_graduation_roadmap returned error: {result.get('message')}"
    valid_statuses = {
        "complete", "blocked",
        "cannot_complete_projection", "cannot_compute", "not_applicable",
    }
    assert status in valid_statuses, f"Unexpected status '{status}'"


def test_unknown_operation_returns_error(adapter, student_ctx, rule_bundles):
    result = adapter.call("nonexistent_operation", student_ctx, rule_bundles)
    assert result["status"] == "error", f"Expected 'error', got '{result['status']}'"
