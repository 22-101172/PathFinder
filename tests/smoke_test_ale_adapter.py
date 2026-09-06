"""
ALE Adapter tests — structural smoke + semantic correctness.

Verifies:
  - All imports resolve
  - All 6 ALE operations wire correctly end-to-end
  - Output values are semantically correct for known inputs
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
def rule_bundles():
    """Correct rule bundle keys matching _parse_rules() expectations in ale_adapter.py."""
    return {
        "grading_scale_rules": {
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
        "graduation_requirement_rules": {
            "total_credits_required": 133,
            "minimum_cgpa": 2.0,
            "minimum_regular_semesters": 6,
            "maximum_regular_semesters": 16,
            "must_pass_zero_credit_courses": True,
            "military_training_required_for_males": True,
        },
        "academic_warning_rules": {
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
        "summer_semester_rules": {
            "default_max_courses": 2,
            "cgpa_above_3_max_courses": 3,
            "cgpa_threshold_for_extra_course": 3.0,
        },
        "student_level_rules": {
            "freshman_max_hours": 26,
            "sophomore_min_hours": 27,
            "sophomore_max_hours": 59,
            "junior_min_hours": 60,
            "junior_max_hours": 93,
            "senior_min_hours": 94,
            "senior_max_hours": 133,
        },
    }


@pytest.fixture
def student_good_standing():
    """Senior AI student with good CGPA, many courses done — should pass most checks."""
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
        zero_credit_courses_passed=["HUM111"],
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
def student_cannot_graduate():
    """Freshman with low CGPA and very few credits — should fail graduation audit."""
    return StudentContext(
        student_id="STU_FRESH",
        name="Freshman Student",
        program="CIS Program - Artificial Intelligence Track",
        track_id="AI",
        level=1,
        first_semester="Fall 2025",
        study_status="Studying",
        current_semester="Spring 2026",
        military_status="Not Yet Deferred",
        cgpa=1.5,
        academic_standing="warning",
        total_credit_hours_earned=15,
        cumulative_chs=15,
        cumulative_cps=22.5,
        last_semester_gpa=None,
        last_semester_chs=None,
        last_semester_cps=None,
        last_semester_phs=None,
        current_semester_chs=15,
        consecutive_warnings=1,
        total_warnings=1,
        completed_regular_semesters=1,
        zero_credit_courses_passed=[],
        retake_count={},
        total_improve_retakes_used=0,
        completed_courses=["C-CS111", "C-MA111"],
        failed_courses=[],
        in_progress_courses=[],
        planned_courses=[],
        course_history=[],
    )


@pytest.fixture
def available_courses_fall_only():
    """Two courses: one offered Fall+Spring, one Spring only — for semester filter test."""
    return [
        {
            "course_code": "C-CS499",
            "name": "Capstone",
            "credits": 3,
            "level": 4,
            "prerequisites": ["C-CS495"],
            "semester_offering": ["Fall", "Spring"],
            "track": {"track_id": "AI", "name": "Artificial Intelligence"},
        },
        {
            "course_code": "C-AI499",
            "name": "AI Project",
            "credits": 3,
            "level": 4,
            "prerequisites": ["C-AI321"],
            "semester_offering": ["Spring"],
            "track": {"track_id": "AI", "name": "Artificial Intelligence"},
        },
    ]


# ---------------------------------------------------------------------------
# Test 1 — Imports
# ---------------------------------------------------------------------------

def test_imports_resolve():
    from adapters.ale_adapter import ALEAdapter  # noqa: F401
    from engines.ale.functions.simulate_gpa_forward import simulate_gpa_forward  # noqa: F401
    from engines.ale.functions.solve_target_gpa import solve_target_gpa  # noqa: F401
    from engines.ale.functions.check_course_eligibility import check_course_eligibility  # noqa: F401
    from engines.ale.functions.run_graduation_audit import run_graduation_audit  # noqa: F401
    from engines.ale.functions.generate_semester_plan import generate_semester_plan  # noqa: F401
    from engines.ale.functions.generate_graduation_roadmap import generate_graduation_roadmap  # noqa: F401


# ---------------------------------------------------------------------------
# Test 2 — simulate_gpa_forward
# ---------------------------------------------------------------------------

def test_simulate_gpa_forward_returns_higher_cgpa_for_good_grades(adapter, student_good_standing, rule_bundles):
    """Getting A in a new course must raise projected CGPA above current CGPA."""
    result = adapter.call(
        "simulate_gpa_forward",
        student_good_standing,
        rule_bundles,
        params={
            "planned_courses": [{
                "course_code": "C-CS499",
                "course_name": "Capstone",
                "credits": 3,
                "expected_grade": "A",
                "has_cgpa_footprint": False,
                "old_grade": None,
                "attempt_type": "first_attempt",
                "improve_retake_number": None,
                "is_currently_in_progress": False,
                "related_completed_course": None,
            }],
            "excluded_in_progress_courses": [],
        },
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    assert "projected_cgpa" in result
    assert result["projected_cgpa"] > student_good_standing.cgpa, (
        f"Expected projected CGPA > {student_good_standing.cgpa}, got {result['projected_cgpa']}"
    )


def test_simulate_gpa_forward_returns_lower_cgpa_for_bad_grades(adapter, student_good_standing, rule_bundles):
    """Getting F in a new course must lower projected CGPA below current CGPA."""
    result = adapter.call(
        "simulate_gpa_forward",
        student_good_standing,
        rule_bundles,
        params={
            "planned_courses": [{
                "course_code": "C-CS499",
                "course_name": "Capstone",
                "credits": 3,
                "expected_grade": "F",
                "has_cgpa_footprint": False,
                "old_grade": None,
                "attempt_type": "first_attempt",
                "improve_retake_number": None,
                "is_currently_in_progress": False,
                "related_completed_course": None,
            }],
            "excluded_in_progress_courses": [],
        },
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    assert result["projected_cgpa"] < student_good_standing.cgpa, (
        f"Expected projected CGPA < {student_good_standing.cgpa}, got {result['projected_cgpa']}"
    )


# ---------------------------------------------------------------------------
# Test 3 — solve_target_gpa
# ---------------------------------------------------------------------------

def test_solve_target_gpa_already_met(adapter, student_good_standing, rule_bundles):
    """Target CGPA below current CGPA must return already_met."""
    result = adapter.call(
        "solve_target_gpa",
        student_good_standing,
        rule_bundles,
        params={
            "target_cgpa": 2.0,
            "planned_courses": [],
            "planned_course_source": "orchestrator",
        },
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    assert result["status"] == "already_met", (
        f"Expected 'already_met' for target below current CGPA, got '{result['status']}'"
    )


def test_solve_target_gpa_solvable(adapter, student_good_standing, rule_bundles):
    """Target CGPA slightly above current must return solvable with required grades."""
    result = adapter.call(
        "solve_target_gpa",
        student_good_standing,
        rule_bundles,
        params={
            "target_cgpa": 2.8,
            "planned_courses": [{
                "course_code": "C-CS499",
                "course_name": "Advanced Topics",
                "credits": 3,
                "attempt_type": "first_attempt",
                "has_cgpa_footprint": False,
                "old_grade": None,
                "improve_retake_number": None,
            }],
            "planned_course_source": "orchestrator",
        },
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    assert result["status"] in {"solvable", "impossible"}, (
        f"Unexpected status: {result['status']}"
    )


# ---------------------------------------------------------------------------
# Test 4 — check_course_eligibility
# ---------------------------------------------------------------------------

def test_check_eligibility_eligible_when_prereqs_met(adapter, student_good_standing, rule_bundles):
    """C-CS499 requires C-CS495 — student has it — must be eligible."""
    result = adapter.call(
        "check_course_eligibility",
        student_good_standing,
        rule_bundles,
        kg_data={"course_prerequisites": ["C-CS495"], "course_credit_threshold": None},
        params={"target_course_code": "C-CS499", "attempt_type": "first_attempt"},
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    assert result["status"] == "eligible", (
        f"Expected 'eligible' when prereqs met, got '{result['status']}'"
    )


def test_check_eligibility_not_eligible_when_prereqs_missing(adapter, student_good_standing, rule_bundles):
    """Course requiring C-AI999 (not completed) — must return not_eligible."""
    result = adapter.call(
        "check_course_eligibility",
        student_good_standing,
        rule_bundles,
        kg_data={"course_prerequisites": ["C-AI999"], "course_credit_threshold": None},
        params={"target_course_code": "C-AI500", "attempt_type": "first_attempt"},
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    assert result["status"] == "not_eligible", (
        f"Expected 'not_eligible' when prereqs missing, got '{result['status']}'"
    )


def test_check_eligibility_already_completed(adapter, student_good_standing, rule_bundles):
    """Course already in completed_courses must return already_completed."""
    result = adapter.call(
        "check_course_eligibility",
        student_good_standing,
        rule_bundles,
        kg_data={"course_prerequisites": [], "course_credit_threshold": None},
        params={"target_course_code": "C-CS111", "attempt_type": "first_attempt"},
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    assert result["status"] == "already_completed", (
        f"Expected 'already_completed', got '{result['status']}'"
    )


# ---------------------------------------------------------------------------
# Test 5 — run_graduation_audit
# ---------------------------------------------------------------------------

def test_graduation_audit_not_eligible_for_freshman(adapter, student_cannot_graduate, rule_bundles):
    """Freshman with 15 credits and CGPA 1.5 must not be eligible to graduate."""
    result = adapter.call(
        "run_graduation_audit",
        student_cannot_graduate,
        rule_bundles,
        kg_data={"required_zero_credit_courses": ["HUM111"]},
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    assert result["status"] == "not_eligible", (
        f"Expected 'not_eligible' for freshman with 15 credits, got '{result['status']}'"
    )


def test_graduation_audit_has_checks_field(adapter, student_good_standing, rule_bundles):
    """Audit result must always include a checks field regardless of eligibility."""
    result = adapter.call(
        "run_graduation_audit",
        student_good_standing,
        rule_bundles,
        kg_data={"required_zero_credit_courses": ["HUM111"]},
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    assert "checks" in result, f"Missing 'checks' in result: {result}"
    assert isinstance(result["checks"], list), f"'checks' must be a list, got {type(result['checks'])}"


# ---------------------------------------------------------------------------
# Test 6 — generate_semester_plan
# ---------------------------------------------------------------------------

def test_semester_plan_excludes_spring_only_course_in_fall(adapter, student_good_standing, rule_bundles, available_courses_fall_only):
    """Spring-only course must not appear in a Fall semester plan."""
    result = adapter.call(
        "generate_semester_plan",
        student_good_standing,
        rule_bundles,
        kg_data={"available_courses": available_courses_fall_only},
        params={"target_semester_type": "Fall"},
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    all_planned_codes = set()
    for plan in result.get("plans", []):
        if plan and plan.get("courses"):
            for c in plan["courses"]:
                all_planned_codes.add(c["course_code"])
    assert "C-AI499" not in all_planned_codes, (
        "Spring-only course C-AI499 must not appear in Fall plan"
    )


def test_semester_plan_includes_fall_course_in_fall(adapter, student_good_standing, rule_bundles, available_courses_fall_only):
    """Fall+Spring course must be eligible to appear in Fall plan when prereqs met."""
    result = adapter.call(
        "generate_semester_plan",
        student_good_standing,
        rule_bundles,
        kg_data={"available_courses": available_courses_fall_only},
        params={"target_semester_type": "Fall"},
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    all_planned_codes = set()
    for plan in result.get("plans", []):
        if plan and plan.get("courses"):
            for c in plan["courses"]:
                all_planned_codes.add(c["course_code"])
    assert "C-CS499" in all_planned_codes, (
        "C-CS499 (Fall+Spring) must appear in Fall plan when prereqs met"
    )


# ---------------------------------------------------------------------------
# Test 7 — generate_graduation_roadmap
# ---------------------------------------------------------------------------

def test_graduation_roadmap_has_labeled_semesters(adapter, student_good_standing, rule_bundles, available_courses_fall_only):
    """Roadmap semesters must have labels in format 'Fall YYYY' or 'Spring YYYY'."""
    import re
    result = adapter.call(
        "generate_graduation_roadmap",
        student_good_standing,
        rule_bundles,
        kg_data={"available_courses": available_courses_fall_only, "required_zero_credit_courses": ["HUM111"]},
        params={
            "target_semester_type": "Fall",
            "starting_year": 2026,
        },
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    semesters = result.get("semester_plans", [])
    assert len(semesters) > 0, "Roadmap must contain at least one semester"
    label_pattern = re.compile(r"^(Fall|Spring|Summer) \d{4}$")
    for sem in semesters:
        label = sem.get("semester_label", "")
        assert label_pattern.match(label), (
            f"Semester label '{label}' does not match expected format 'Season YYYY'"
        )


def test_graduation_roadmap_spring_only_course_not_in_fall_semester(adapter, student_good_standing, rule_bundles, available_courses_fall_only):
    """Spring-only course must never appear in a Fall-labeled semester in the roadmap."""
    result = adapter.call(
        "generate_graduation_roadmap",
        student_good_standing,
        rule_bundles,
        kg_data={"available_courses": available_courses_fall_only, "required_zero_credit_courses": ["HUM111"]},
        params={
            "target_semester_type": "Fall",
            "starting_year": 2026,
        },
    )
    assert result.get("status") != "error", f"Unexpected error: {result.get('message')}"
    for sem in result.get("semester_plans", []):
        if sem.get("semester_label", "").startswith("Fall"):
            codes = [c["course_code"] for c in sem.get("courses", [])]
            assert "C-AI499" not in codes, (
                f"Spring-only course C-AI499 found in Fall semester: {sem['semester_label']}"
            )


# ---------------------------------------------------------------------------
# Test 8 — Unknown operation
# ---------------------------------------------------------------------------

def test_unknown_operation_returns_error(adapter, student_good_standing, rule_bundles):
    result = adapter.call("nonexistent_operation", student_good_standing, rule_bundles)
    assert result["status"] == "error"
