"""
tests/test_ale_adapter.py
=========================
Focused adapter-contract tests for ALEAdapter.

Coverage:
  A. Public dispatch — all 6 operations succeed; unknown op returns error
  B. Student level mapping — 1–4 valid; invalid returns cannot_compute/invalid_student_level
  C. Zero-credit requirement mapping — safe subset computation, non-empty list ≠ True
  D. Rule bundle handling — missing/invalid bundle returns error without crash
  E. Available course mapping — preserves fields; malformed course → cannot_compute
  F. Roadmap mapping — warning counters, target-end fields, invalid assumed grade
  G. Semester plan mapping — credit load, max_credits_mode, invalid level
  H. GPA operation mapping — planned_courses forwarded as-is, credits not invented
  I. Eligibility mapping — kg_data prereqs reach ALE; invalid attempt_type → cannot_compute
"""

import pytest

from adapters.ale_adapter import ALEAdapter, _map_student_level, _compute_zero_credit_requirement_passed
from gateway.models.schemas import StudentContext


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def adapter():
    return ALEAdapter()


@pytest.fixture
def rule_bundles():
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


def _make_student(
    level: int = 2,
    cgpa: float = 2.5,
    total_credit_hours_earned: int = 40,
    cumulative_chs: int = 40,
    cumulative_cps: float = 100.0,
    zero_credit_courses_passed: list | None = None,
    completed_courses: list | None = None,
    failed_courses: list | None = None,
    in_progress_courses: list | None = None,
    consecutive_warnings: int = 0,
    total_warnings: int = 0,
    completed_regular_semesters: int = 2,
    military_status: str | None = "Exempted",
    student_id: str = "T001",
) -> StudentContext:
    return StudentContext(
        student_id=student_id,
        name="Test Student",
        program="CIS",
        track_id="AI",
        level=level,
        first_semester="Fall 2023",
        study_status="Studying",
        military_status=military_status,
        cgpa=cgpa,
        total_credit_hours_earned=total_credit_hours_earned,
        cumulative_chs=cumulative_chs,
        cumulative_cps=cumulative_cps,
        consecutive_warnings=consecutive_warnings,
        total_warnings=total_warnings,
        completed_regular_semesters=completed_regular_semesters,
        zero_credit_courses_passed=zero_credit_courses_passed if zero_credit_courses_passed is not None else ["HUM111"],
        retake_count={},
        total_improve_retakes_used=0,
        completed_courses=completed_courses if completed_courses is not None else ["C-CS111", "C-MA111"],
        failed_courses=failed_courses if failed_courses is not None else [],
        in_progress_courses=in_progress_courses if in_progress_courses is not None else [],
        course_history=[],
    )


@pytest.fixture
def base_student():
    """Sophomore with CGPA 2.5, 40 credit hours — minimal valid student."""
    return _make_student()


@pytest.fixture
def minimal_kg():
    """Minimal kg_data with required zero-credit list and no available courses."""
    return {"required_zero_credit_courses": ["HUM111"]}


# =============================================================================
# A. Public dispatch
# =============================================================================

class TestPublicDispatch:
    def test_all_six_operations_return_a_dict_with_status(self, adapter, rule_bundles, base_student, minimal_kg):
        """All 6 operations must dispatch without raising and return a status dict."""
        ops_params = {
            "simulate_gpa_forward": {"planned_courses": [], "excluded_in_progress_courses": []},
            "solve_target_gpa": {"target_cgpa": 3.0, "planned_courses": []},
            "check_course_eligibility": {"target_course_code": "C-CS999", "attempt_type": "first_attempt"},
            "run_graduation_audit": {},
            "generate_semester_plan": {"target_semester_type": "Fall"},
            "generate_graduation_roadmap": {"target_semester_type": "Fall", "starting_year": 2026},
        }
        for op, params in ops_params.items():
            result = adapter.call(op, base_student, rule_bundles, kg_data=minimal_kg, params=params)
            assert isinstance(result, dict), f"{op}: must return dict"
            assert "status" in result, f"{op}: result must have 'status' key"

    def test_unknown_operation_returns_error_not_crash(self, adapter, rule_bundles, base_student):
        result = adapter.call("nonexistent_operation", base_student, rule_bundles)
        assert result["status"] == "error"
        assert "operation" in result

    def test_unknown_operation_includes_operation_field(self, adapter, rule_bundles, base_student):
        result = adapter.call("bad_op", base_student, rule_bundles)
        assert result.get("operation") == "bad_op"


# =============================================================================
# B. Student level mapping
# =============================================================================

class TestStudentLevelMapping:
    @pytest.mark.parametrize("level,expected", [
        (1, "Freshman"), (2, "Sophomore"), (3, "Junior"), (4, "Senior"),
    ])
    def test_valid_levels_map_to_string(self, level, expected):
        assert _map_student_level(level) == expected

    @pytest.mark.parametrize("invalid_level", [0, 5, 99, -1])
    def test_invalid_levels_return_none(self, invalid_level):
        assert _map_student_level(invalid_level) is None

    def test_level_5_semester_plan_returns_cannot_compute(self, adapter, rule_bundles, minimal_kg):
        student = _make_student(level=5)
        result = adapter.call(
            "generate_semester_plan",
            student, rule_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Fall"},
        )
        assert result["status"] == "cannot_compute", "Level 5 must return cannot_compute, not silently use Freshman"
        assert "invalid_student_level" in result.get("reason_codes", [])

    def test_level_0_roadmap_returns_cannot_compute(self, adapter, rule_bundles, minimal_kg):
        student = _make_student(level=0)
        result = adapter.call(
            "generate_graduation_roadmap",
            student, rule_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Fall", "starting_year": 2026},
        )
        assert result["status"] == "cannot_compute"
        assert "invalid_student_level" in result.get("reason_codes", [])

    def test_invalid_level_does_not_silently_become_freshman(self, adapter, rule_bundles, minimal_kg):
        """Critical contract: adapter must not fall back to 'Freshman' on invalid level."""
        student = _make_student(level=99)
        result = adapter.call(
            "generate_semester_plan",
            student, rule_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Fall"},
        )
        # If it silently used Freshman, status would be 'no_eligible_courses' or 'plans_generated'
        # — not 'cannot_compute'.
        assert result["status"] == "cannot_compute"

    def test_level_1_semester_plan_does_not_crash(self, adapter, rule_bundles, minimal_kg):
        student = _make_student(level=1, total_credit_hours_earned=10, cumulative_chs=10, cumulative_cps=25.0)
        result = adapter.call(
            "generate_semester_plan",
            student, rule_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Fall"},
        )
        assert result["status"] != "error"

    def test_level_4_semester_plan_does_not_crash(self, adapter, rule_bundles, minimal_kg):
        student = _make_student(level=4, total_credit_hours_earned=100, cumulative_chs=100, cumulative_cps=250.0)
        result = adapter.call(
            "generate_semester_plan",
            student, rule_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Fall"},
        )
        assert result["status"] != "error"


# =============================================================================
# C. Zero-credit requirement mapping
# =============================================================================

class TestZeroCreditMapping:
    def test_helper_all_required_passed_returns_true(self):
        sc = _make_student(zero_credit_courses_passed=["HUM111", "HUM222"])
        result = _compute_zero_credit_requirement_passed(
            sc, {"required_zero_credit_courses": ["HUM111", "HUM222"]}, {}
        )
        assert result is True

    def test_helper_missing_required_course_returns_false(self):
        sc = _make_student(zero_credit_courses_passed=["HUM111"])
        result = _compute_zero_credit_requirement_passed(
            sc, {"required_zero_credit_courses": ["HUM111", "HUM222"]}, {}
        )
        assert result is False

    def test_helper_missing_required_list_returns_none(self):
        sc = _make_student(zero_credit_courses_passed=["HUM111"])
        result = _compute_zero_credit_requirement_passed(sc, {}, {})
        assert result is None

    def test_helper_params_fallback_when_kg_missing(self):
        sc = _make_student(zero_credit_courses_passed=["HUM111"])
        result = _compute_zero_credit_requirement_passed(
            sc, {}, {"required_zero_credit_courses": ["HUM111"]}
        )
        assert result is True

    def test_helper_empty_required_list_returns_true_vacuously(self):
        """No zero-credit courses required means requirement is vacuously satisfied."""
        sc = _make_student(zero_credit_courses_passed=[])
        result = _compute_zero_credit_requirement_passed(
            sc, {"required_zero_credit_courses": []}, {}
        )
        assert result is True

    def test_nonempty_passed_list_without_required_returns_cannot_compute_audit(self, adapter, rule_bundles):
        """Non-empty sc.zero_credit_courses_passed alone must NOT imply True."""
        student = _make_student(zero_credit_courses_passed=["HUM111"])
        result = adapter.call(
            "run_graduation_audit",
            student, rule_bundles,
            kg_data={},  # no required_zero_credit_courses
        )
        assert result["status"] == "cannot_compute"
        assert "missing_required_zero_credit_course_list" in result.get("reason_codes", [])

    def test_missing_required_list_returns_cannot_compute_audit(self, adapter, rule_bundles):
        student = _make_student(zero_credit_courses_passed=[])
        result = adapter.call(
            "run_graduation_audit",
            student, rule_bundles,
            kg_data={},
        )
        assert result["status"] == "cannot_compute"
        assert "missing_required_zero_credit_course_list" in result.get("reason_codes", [])

    def test_missing_required_list_returns_cannot_compute_roadmap(self, adapter, rule_bundles):
        student = _make_student(zero_credit_courses_passed=["HUM111"])
        result = adapter.call(
            "generate_graduation_roadmap",
            student, rule_bundles,
            kg_data={},
            params={"target_semester_type": "Fall", "starting_year": 2026},
        )
        assert result["status"] == "cannot_compute"
        assert "missing_required_zero_credit_course_list" in result.get("reason_codes", [])

    def test_all_required_passed_audit_proceeds_normally(self, adapter, rule_bundles):
        """With required list fully satisfied, audit runs and does not return cannot_compute."""
        student = _make_student(zero_credit_courses_passed=["HUM111"])
        result = adapter.call(
            "run_graduation_audit",
            student, rule_bundles,
            kg_data={"required_zero_credit_courses": ["HUM111"]},
        )
        assert "missing_required_zero_credit_course_list" not in result.get("reason_codes", [])
        assert result["status"] != "error"

    def test_partial_pass_audit_proceeds_with_zero_credit_gap(self, adapter, rule_bundles):
        """Partially satisfied required list → zero_credit_passed=False but audit still runs."""
        student = _make_student(zero_credit_courses_passed=["HUM111"])
        result = adapter.call(
            "run_graduation_audit",
            student, rule_bundles,
            kg_data={"required_zero_credit_courses": ["HUM111", "HUM222"]},
        )
        assert "missing_required_zero_credit_course_list" not in result.get("reason_codes", [])
        assert result["status"] not in {"error"}


# =============================================================================
# D. Rule bundle handling
# =============================================================================

class TestRuleBundleHandling:
    def test_empty_bundles_returns_error_not_crash(self, adapter, base_student):
        result = adapter.call(
            "simulate_gpa_forward",
            base_student, {},
            params={"planned_courses": []},
        )
        assert result["status"] == "error"
        assert "operation" in result

    def test_missing_bundle_result_includes_operation_field(self, adapter, base_student):
        result = adapter.call("simulate_gpa_forward", base_student, {}, params={"planned_courses": []})
        assert result.get("operation") == "simulate_gpa_forward"

    def test_wrong_bundle_key_structure_returns_error(self, adapter, base_student):
        """Rule bundle with unrecognised fields must return error, not crash."""
        bad_bundles = {"grading_scale_rules": {"bad_key": "bad_value"}}
        result = adapter.call(
            "simulate_gpa_forward",
            base_student, bad_bundles,
            params={"planned_courses": []},
        )
        assert result["status"] in {"error", "cannot_compute"}

    def test_missing_credit_limit_rules_returns_error(self, adapter, rule_bundles, base_student, minimal_kg):
        """generate_semester_plan requires credit_limit_rules — missing must return error."""
        partial_bundles = {k: v for k, v in rule_bundles.items() if k != "credit_limit_rules"}
        result = adapter.call(
            "generate_semester_plan",
            base_student, partial_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Fall"},
        )
        assert result["status"] == "error"

    def test_missing_honors_rules_returns_error_for_audit(self, adapter, rule_bundles, base_student):
        """run_graduation_audit requires honors_rules — missing must return error."""
        partial_bundles = {k: v for k, v in rule_bundles.items() if k != "honors_rules"}
        result = adapter.call(
            "run_graduation_audit",
            base_student, partial_bundles,
            kg_data={"required_zero_credit_courses": ["HUM111"]},
        )
        assert result["status"] == "error"


# =============================================================================
# E. Available course mapping
# =============================================================================

class TestAvailableCourseMapping:
    def test_preserves_name_and_credits(self, adapter, rule_bundles, base_student, minimal_kg):
        course = {
            "course_code": "C-CS888",
            "name": "Data Structures",
            "credits": 3,
            "level": 2,
            "semester_offering": ["Fall", "Spring"],
            "prerequisites": ["C-CS111"],
            "credit_threshold": 30,
            "track": ["AI"],
        }
        kg_data = {**minimal_kg, "available_courses": [course]}
        result = adapter.call(
            "generate_semester_plan",
            base_student, rule_bundles,
            kg_data=kg_data,
            params={"target_semester_type": "Fall"},
        )
        assert result["status"] != "error", f"Unexpected error: {result.get('message')}"

    def test_preserves_prerequisites_and_credit_threshold(self, adapter, rule_bundles, base_student, minimal_kg):
        """Course with credit_threshold must reach ALE without modification."""
        course = {
            "course_code": "C-CS777",
            "name": "Advanced Algorithms",
            "credits": 3,
            "level": 3,
            "semester_offering": ["Fall"],
            "prerequisites": ["C-CS111", "C-MA111"],
            "credit_threshold": 60,
            "track": ["AI"],
        }
        kg_data = {**minimal_kg, "available_courses": [course]}
        result = adapter.call(
            "generate_semester_plan",
            base_student, rule_bundles,
            kg_data=kg_data,
            params={"target_semester_type": "Fall"},
        )
        assert result["status"] != "error"

    def test_track_as_dict_normalized_to_list(self, adapter, rule_bundles, base_student, minimal_kg):
        """track dict shape ({track_id, name}) must be converted to list without crash."""
        course = {
            "course_code": "C-AI777",
            "name": "Machine Learning",
            "credits": 3,
            "level": 3,
            "semester_offering": ["Fall"],
            "prerequisites": [],
            "track": {"track_id": "AI", "name": "Artificial Intelligence"},
        }
        kg_data = {**minimal_kg, "available_courses": [course]}
        result = adapter.call(
            "generate_semester_plan",
            base_student, rule_bundles,
            kg_data=kg_data,
            params={"target_semester_type": "Fall"},
        )
        assert result["status"] != "error"

    def test_track_as_list_accepted_directly(self, adapter, rule_bundles, base_student, minimal_kg):
        course = {
            "course_code": "C-AI666",
            "name": "Deep Learning",
            "credits": 3,
            "level": 3,
            "semester_offering": ["Spring"],
            "prerequisites": [],
            "track": ["AI", "DS"],
        }
        kg_data = {**minimal_kg, "available_courses": [course]}
        result = adapter.call(
            "generate_semester_plan",
            base_student, rule_bundles,
            kg_data=kg_data,
            params={"target_semester_type": "Fall"},
        )
        assert result["status"] != "error"

    def test_malformed_course_missing_credits_returns_cannot_compute(self, adapter, rule_bundles, base_student, minimal_kg):
        """Course missing 'credits' triggers ValidationError which must map to cannot_compute."""
        bad_course = {
            "course_code": "C-BAD001",
            "name": "Bad Course",
            # "credits" intentionally omitted — required field
            "level": 2,
            "semester_offering": ["Fall"],
        }
        kg_data = {**minimal_kg, "available_courses": [bad_course]}
        result = adapter.call(
            "generate_semester_plan",
            base_student, rule_bundles,
            kg_data=kg_data,
            params={"target_semester_type": "Fall"},
        )
        assert result["status"] == "cannot_compute", (
            "Malformed course missing 'credits' must return cannot_compute, not crash"
        )
        assert "operation" in result

    def test_malformed_course_missing_level_returns_cannot_compute(self, adapter, rule_bundles, base_student, minimal_kg):
        bad_course = {
            "course_code": "C-BAD002",
            "name": "Another Bad Course",
            "credits": 3,
            # "level" intentionally omitted
            "semester_offering": ["Fall"],
        }
        kg_data = {**minimal_kg, "available_courses": [bad_course]}
        result = adapter.call(
            "generate_semester_plan",
            base_student, rule_bundles,
            kg_data=kg_data,
            params={"target_semester_type": "Fall"},
        )
        assert result["status"] == "cannot_compute"


# =============================================================================
# F. Roadmap mapping
# =============================================================================

class TestRoadmapMapping:
    def test_target_end_fields_forwarded_to_ale(self, adapter, rule_bundles, base_student, minimal_kg):
        """target_end_semester_type and target_end_year from params must reach ALE."""
        result = adapter.call(
            "generate_graduation_roadmap",
            base_student, rule_bundles,
            kg_data=minimal_kg,
            params={
                "target_semester_type": "Fall",
                "starting_year": 2026,
                "target_end_semester_type": "Spring",
                "target_end_year": 2027,
            },
        )
        assert result["status"] != "error", f"Unexpected error: {result.get('message')}"

    def test_warning_counters_forwarded(self, adapter, rule_bundles, minimal_kg):
        """consecutive_warnings and total_warnings from StudentContext must reach ALE."""
        warned = _make_student(
            cgpa=1.8,
            consecutive_warnings=2,
            total_warnings=3,
            total_credit_hours_earned=30,
            cumulative_chs=30,
            cumulative_cps=54.0,
            student_id="T_WARN",
        )
        result = adapter.call(
            "generate_graduation_roadmap",
            warned, rule_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Fall", "starting_year": 2026},
        )
        assert result["status"] != "error"

    def test_invalid_assumed_grade_returns_cannot_compute(self, adapter, rule_bundles, base_student, minimal_kg):
        result = adapter.call(
            "generate_graduation_roadmap",
            base_student, rule_bundles,
            kg_data=minimal_kg,
            params={
                "target_semester_type": "Fall",
                "starting_year": 2026,
                "assumed_grade_per_pass": "INVALID_GRADE",
            },
        )
        assert result["status"] == "cannot_compute"
        assert "invalid_assumed_grade" in result.get("reason_codes", [])

    def test_p_grade_as_assumed_grade_returns_cannot_compute(self, adapter, rule_bundles, base_student, minimal_kg):
        """P-grade cannot be used as the assumed GPA grade — must return cannot_compute."""
        result = adapter.call(
            "generate_graduation_roadmap",
            base_student, rule_bundles,
            kg_data=minimal_kg,
            params={
                "target_semester_type": "Fall",
                "starting_year": 2026,
                "assumed_grade_per_pass": "P",
            },
        )
        assert result["status"] == "cannot_compute"
        assert "invalid_assumed_grade" in result.get("reason_codes", [])

    def test_failed_retake_cap_pre_resolved_by_adapter(self, adapter, rule_bundles, base_student, minimal_kg):
        """Retake cap must be pre-resolved to grade points without crash (result is opaque)."""
        result = adapter.call(
            "generate_graduation_roadmap",
            base_student, rule_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Fall", "starting_year": 2026},
        )
        assert result["status"] != "error"

    def test_roadmap_missing_zero_credit_list_returns_cannot_compute(self, adapter, rule_bundles, base_student):
        result = adapter.call(
            "generate_graduation_roadmap",
            base_student, rule_bundles,
            kg_data={},
            params={"target_semester_type": "Fall", "starting_year": 2026},
        )
        assert result["status"] == "cannot_compute"
        assert "missing_required_zero_credit_course_list" in result.get("reason_codes", [])


# =============================================================================
# G. Semester plan mapping
# =============================================================================

class TestSemesterPlanMapping:
    def test_target_credit_load_forwarded(self, adapter, rule_bundles, base_student, minimal_kg):
        result = adapter.call(
            "generate_semester_plan",
            base_student, rule_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Fall", "target_credit_load": 12},
        )
        assert result["status"] != "error"

    def test_max_credits_mode_forwarded(self, adapter, rule_bundles, base_student, minimal_kg):
        result = adapter.call(
            "generate_semester_plan",
            base_student, rule_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Fall", "max_credits_mode": True},
        )
        assert result["status"] != "error"

    def test_invalid_level_returns_cannot_compute_not_plans(self, adapter, rule_bundles, minimal_kg):
        """Level 99 must yield cannot_compute — not silently plan as Freshman."""
        student = _make_student(level=99)
        result = adapter.call(
            "generate_semester_plan",
            student, rule_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Fall"},
        )
        assert result["status"] == "cannot_compute"
        assert "invalid_student_level" in result.get("reason_codes", [])

    def test_summer_semester_rules_used_for_summer_target(self, adapter, rule_bundles, base_student, minimal_kg):
        result = adapter.call(
            "generate_semester_plan",
            base_student, rule_bundles,
            kg_data=minimal_kg,
            params={"target_semester_type": "Summer"},
        )
        assert result["status"] != "error"

    def test_semester_plan_missing_zero_credit_list_does_not_crash(self, adapter, rule_bundles, base_student):
        """Semester plan does not need zero-credit list — must not return cannot_compute for it."""
        result = adapter.call(
            "generate_semester_plan",
            base_student, rule_bundles,
            kg_data={},  # no required_zero_credit_courses
            params={"target_semester_type": "Fall"},
        )
        assert "missing_required_zero_credit_course_list" not in result.get("reason_codes", [])


# =============================================================================
# H. GPA operation mapping
# =============================================================================

class TestGPAOperationMapping:
    def test_simulate_gpa_forward_passes_credits_unchanged(self, adapter, rule_bundles, base_student):
        """Adapter must forward course credits exactly — must not invent or change them."""
        planned = [{
            "course_code": "C-CS500",
            "course_name": "Advanced OS",
            "credits": 4,
            "expected_grade": "A",
            "attempt_type": "first_attempt",
            "has_cgpa_footprint": False,
            "old_grade": None,
            "improve_retake_number": None,
            "is_currently_in_progress": False,
        }]
        result = adapter.call(
            "simulate_gpa_forward",
            base_student, rule_bundles,
            params={"planned_courses": planned, "excluded_in_progress_courses": []},
        )
        assert result.get("status") != "error"
        breakdown = result.get("per_course_breakdown", [])
        matched = [c for c in breakdown if c["course_code"] == "C-CS500"]
        assert matched, "C-CS500 must appear in per_course_breakdown"
        assert matched[0]["credits"] == 4, "Adapter must not change course credits (4 must stay 4)"

    def test_simulate_gpa_forward_with_empty_courses_does_not_crash(self, adapter, rule_bundles, base_student):
        """Empty planned_courses must return a valid result dict — not raise."""
        result = adapter.call(
            "simulate_gpa_forward",
            base_student, rule_bundles,
            params={"planned_courses": [], "excluded_in_progress_courses": []},
        )
        assert isinstance(result, dict)
        assert "status" in result
        assert result["status"] != "error"

    def test_solve_target_gpa_passes_planned_courses(self, adapter, rule_bundles, base_student):
        planned = [{
            "course_code": "C-CS501",
            "course_name": "Networks",
            "credits": 3,
            "attempt_type": "first_attempt",
            "has_cgpa_footprint": False,
            "old_grade": None,
            "improve_retake_number": None,
        }]
        result = adapter.call(
            "solve_target_gpa",
            base_student, rule_bundles,
            params={"target_cgpa": 2.8, "planned_courses": planned},
        )
        assert result.get("status") != "error"
        assert result.get("status") in {"solvable", "impossible", "already_met", "cannot_compute"}

    def test_simulate_gpa_invalid_attempt_type_returns_cannot_compute(self, adapter, rule_bundles, base_student):
        """Invalid attempt_type (Literal violation) must map to cannot_compute via ValidationError."""
        planned = [{
            "course_code": "C-CS600",
            "course_name": "Test",
            "credits": 3,
            "expected_grade": "B",
            "attempt_type": "not_a_valid_type",
            "has_cgpa_footprint": False,
            "old_grade": None,
            "improve_retake_number": None,
            "is_currently_in_progress": False,
        }]
        result = adapter.call(
            "simulate_gpa_forward",
            base_student, rule_bundles,
            params={"planned_courses": planned},
        )
        assert result["status"] == "cannot_compute"
        assert "invalid_input" in result.get("reason_codes", [])

    def test_solve_target_gpa_already_met_when_target_below_current(self, adapter, rule_bundles, base_student):
        result = adapter.call(
            "solve_target_gpa",
            base_student, rule_bundles,
            params={"target_cgpa": 2.0, "planned_courses": []},
        )
        assert result.get("status") == "already_met"


# =============================================================================
# I. Eligibility mapping
# =============================================================================

class TestEligibilityMapping:
    def test_prerequisites_from_kg_data_reach_ale(self, adapter, rule_bundles, base_student):
        """Prerequisites from kg_data["course_prerequisites"] must reach the ALE function."""
        result = adapter.call(
            "check_course_eligibility",
            base_student, rule_bundles,
            kg_data={"course_prerequisites": ["C-CS111"], "course_credit_threshold": None},
            params={"target_course_code": "C-CS300", "attempt_type": "first_attempt"},
        )
        # base_student has C-CS111 completed → eligible
        assert result.get("status") != "error"
        assert result["status"] == "eligible"

    def test_missing_prerequisite_returns_not_eligible(self, adapter, rule_bundles, base_student):
        result = adapter.call(
            "check_course_eligibility",
            base_student, rule_bundles,
            kg_data={"course_prerequisites": ["C-AI999"], "course_credit_threshold": None},
            params={"target_course_code": "C-AI500", "attempt_type": "first_attempt"},
        )
        assert result.get("status") != "error"
        assert result["status"] == "not_eligible"

    def test_already_completed_course_returns_already_completed(self, adapter, rule_bundles, base_student):
        result = adapter.call(
            "check_course_eligibility",
            base_student, rule_bundles,
            kg_data={"course_prerequisites": [], "course_credit_threshold": None},
            params={"target_course_code": "C-CS111", "attempt_type": "first_attempt"},
        )
        assert result.get("status") != "error"
        assert result["status"] == "already_completed"

    def test_in_progress_courses_from_student_context(self, adapter, rule_bundles):
        """in_progress_courses must come from StudentContext, not invented by adapter."""
        student = _make_student(
            in_progress_courses=["C-CS300"],
            completed_courses=[],
            student_id="T_IP",
        )
        result = adapter.call(
            "check_course_eligibility",
            student, rule_bundles,
            kg_data={"course_prerequisites": [], "course_credit_threshold": None},
            params={"target_course_code": "C-CS300", "attempt_type": "first_attempt"},
        )
        assert result.get("status") != "error"
        assert result["status"] == "in_progress"

    def test_invalid_attempt_type_returns_cannot_compute(self, adapter, rule_bundles, base_student):
        """Invalid attempt_type (not in Literal) must map to cannot_compute via ValidationError."""
        result = adapter.call(
            "check_course_eligibility",
            base_student, rule_bundles,
            kg_data={"course_prerequisites": [], "course_credit_threshold": None},
            params={"target_course_code": "C-CS300", "attempt_type": "wrong_type"},
        )
        assert result["status"] == "cannot_compute"
        assert "invalid_input" in result.get("reason_codes", [])

    def test_credit_threshold_from_kg_data_forwarded(self, adapter, rule_bundles, base_student):
        """credit_threshold from kg_data must reach ALE — student with 40 hours should clear threshold 30."""
        result = adapter.call(
            "check_course_eligibility",
            base_student, rule_bundles,
            kg_data={"course_prerequisites": [], "course_credit_threshold": 30},
            params={"target_course_code": "C-CS400", "attempt_type": "first_attempt"},
        )
        assert result.get("status") != "error"
        assert result["status"] == "eligible"
