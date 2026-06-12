"""
tests/test_semester_offering_filter.py
Verifies consistent semester_offering filtering across generate_semester_plan
and generate_graduation_roadmap (Issue 7.1).

Design rule:
  - semester_offering = []   → available in ALL semesters (missing data assumed available)
  - semester_offering = [...] → available only in those semesters
"""

from engines.ale.functions.generate_semester_plan import generate_semester_plan
from engines.ale.functions.generate_graduation_roadmap import generate_graduation_roadmap
from engines.ale.schemas import (
    AvailableCourse,
    CreditLimitRules, GraduationRequirementRules, RetakeRules,
    StudentLevelRules,
    GenerateSemesterPlanInput,
    GenerateGraduationRoadmapInput,
)


# ---------------------------------------------------------------------------
# Rule bundles — values copied from smoke_test_ale_adapter.py fixtures
# ---------------------------------------------------------------------------

_CREDIT_LIMIT_RULES = CreditLimitRules(
    cgpa_above_3_limit=21,
    cgpa_between_2_and_3_limit=18,
    cgpa_between_1_and_2_limit=15,
    cgpa_below_1_limit=12,
    minimum_per_semester=9,
    final_semester_override=21,
    incomplete_extra_course_allowed=True,
)

_GRADUATION_RULES = GraduationRequirementRules(
    total_credits_required=133,
    minimum_cgpa=2.0,
    minimum_regular_semesters=6,
    maximum_regular_semesters=16,
    must_pass_zero_credit_courses=True,
    military_training_required_for_males=True,
)

_RETAKE_RULES = RetakeRules(
    failed_first_retake_grade_cap="B",
    improve_retake_first_attempt_cap=None,
    improve_retake_subsequent_cap="B",
    improve_retake_max_courses_cgpa_above_2=3,
    improve_retake_unlimited_below_cgpa=2.0,
)

_STUDENT_LEVEL_RULES = StudentLevelRules(
    freshman_max_hours=26,
    sophomore_min_hours=27,
    sophomore_max_hours=59,
    junior_min_hours=60,
    junior_max_hours=93,
    senior_min_hours=94,
    senior_max_hours=133,
)


# ---------------------------------------------------------------------------
# Course fixtures
# ---------------------------------------------------------------------------
# Course A: Fall + Spring — should be included when target is Fall
# Course B: Spring only   — must be excluded when target is Fall
# Course C: Fall only     — should be included when target is Fall
# Course D: empty list    — no restriction; the `if course.semester_offering and …`
#                           guard treats [] as "offered everywhere"

COURSE_A = AvailableCourse(
    course_code="C-A001",
    name="Course A (Fall+Spring)",
    credits=3,
    level=1,
    semester_offering=["Fall", "Spring"],
)

COURSE_B = AvailableCourse(
    course_code="C-B001",
    name="Course B (Spring only)",
    credits=3,
    level=1,
    semester_offering=["Spring"],
)

COURSE_C = AvailableCourse(
    course_code="C-C001",
    name="Course C (Fall only)",
    credits=3,
    level=1,
    semester_offering=["Fall"],
)

COURSE_D = AvailableCourse(
    course_code="C-D001",
    name="Course D (no restriction)",
    credits=3,
    level=1,
    semester_offering=[],
)


# ---------------------------------------------------------------------------
# Input builder
# ---------------------------------------------------------------------------

def _make_input(target_semester_type: str = "Fall") -> GenerateSemesterPlanInput:
    return GenerateSemesterPlanInput(
        study_status="Studying",
        completed_courses=[],
        failed_courses=[],
        in_progress_courses=[],
        total_improve_retakes_used=0,
        current_cgpa=2.5,
        cumulative_passed_hours=0,
        student_level="Freshman",
        official_track=None,
        incomplete_grade_flag=False,
        available_courses=[COURSE_A, COURSE_B, COURSE_C, COURSE_D],
        credit_limit_rules=_CREDIT_LIMIT_RULES,
        graduation_rules=_GRADUATION_RULES,
        retake_rules=_RETAKE_RULES,
        student_level_rules=_STUDENT_LEVEL_RULES,
        specialization_credit_threshold=60,
        target_semester_type=target_semester_type,
    )


# ---------------------------------------------------------------------------
# Helper: collect all course codes present in any plan
# ---------------------------------------------------------------------------

def _all_plan_codes(output) -> set[str]:
    codes: set[str] = set()
    for plan in (output.plans or []):
        for course in plan.courses:
            codes.add(course.course_code)
    return codes


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_spring_only_course_excluded_in_fall():
    output = generate_semester_plan(_make_input("Fall"))
    assert output.status == "plans_generated"
    assert "C-B001" not in _all_plan_codes(output)


def test_fall_courses_included_in_fall():
    output = generate_semester_plan(_make_input("Fall"))
    assert output.status == "plans_generated"
    codes = _all_plan_codes(output)
    assert "C-A001" in codes
    assert "C-C001" in codes


def test_empty_semester_offering_not_excluded():
    output = generate_semester_plan(_make_input("Fall"))
    assert output.status == "plans_generated"
    assert "C-D001" in _all_plan_codes(output)


def test_ineligibility_summary_reports_wrong_semester():
    output = generate_semester_plan(_make_input("Fall"))
    assert output.ineligibility_summary is not None
    assert "not offered this semester" in output.ineligibility_summary
    assert "1 excluded" in output.ineligibility_summary


# ---------------------------------------------------------------------------
# Issue 7.1 explicit-naming tests (semester_plan)
# ---------------------------------------------------------------------------

def test_empty_semester_offering_included_in_semester_plan():
    """[] semester_offering must be treated as available in every semester."""
    output = generate_semester_plan(_make_input("Fall"))
    assert output.status == "plans_generated"
    assert "C-D001" in _all_plan_codes(output), (
        "Course with empty semester_offering must appear in Fall plan"
    )


def test_spring_only_excluded_from_fall_plan():
    """["Spring"] course must not appear in a Fall semester plan."""
    output = generate_semester_plan(_make_input("Fall"))
    assert output.status == "plans_generated"
    assert "C-B001" not in _all_plan_codes(output), (
        "Spring-only course must not appear in Fall plan"
    )


# ---------------------------------------------------------------------------
# Roadmap graduation rules and helpers (minimal: 9 credits, 1 semester)
# ---------------------------------------------------------------------------

_ROADMAP_GRAD_RULES = GraduationRequirementRules(
    total_credits_required=9,
    minimum_cgpa=2.0,
    minimum_regular_semesters=1,
    maximum_regular_semesters=16,
    must_pass_zero_credit_courses=True,
    military_training_required_for_males=True,
)

# 9-credit courses — one pass to graduate
_COURSE_EMPTY_9 = AvailableCourse(
    course_code="C-EMPTY9",
    name="Course (no restriction, 9cr)",
    credits=9,
    level=1,
    semester_offering=[],
)

_COURSE_FALL_9 = AvailableCourse(
    course_code="C-FALL9",
    name="Course (Fall only, 9cr)",
    credits=9,
    level=1,
    semester_offering=["Fall"],
)

_COURSE_SPRING_9 = AvailableCourse(
    course_code="C-SPRING9",
    name="Course (Spring only, 9cr)",
    credits=9,
    level=1,
    semester_offering=["Spring"],
)


def _make_roadmap_input(
    target_semester_type: str,
    courses: list,
) -> GenerateGraduationRoadmapInput:
    return GenerateGraduationRoadmapInput(
        study_status="Studying",
        completed_courses=[],
        failed_courses=[],
        in_progress_courses=[],
        total_improve_retakes_used=0,
        current_cgpa=2.5,
        gpa_counted_credits=0,
        current_quality_points=0.0,
        cumulative_passed_hours=0,
        student_level="Freshman",
        official_track=None,
        incomplete_grade_flag=False,
        zero_credit_courses_passed=True,
        military_status=None,
        completed_regular_semesters=0,
        available_courses=courses,
        credit_limit_rules=_CREDIT_LIMIT_RULES,
        graduation_rules=_ROADMAP_GRAD_RULES,
        retake_rules=_RETAKE_RULES,
        specialization_credit_threshold=60,
        target_semester_type=target_semester_type,
        starting_year=2026,
        student_level_rules=_STUDENT_LEVEL_RULES,
    )


def _all_roadmap_codes(output) -> set[str]:
    codes: set[str] = set()
    for sem in (output.semester_plans or []):
        for course in sem.courses:
            codes.add(course.course_code)
    return codes


def _codes_in_season(output, season: str) -> set[str]:
    codes: set[str] = set()
    for sem in (output.semester_plans or []):
        if sem.semester_label.startswith(season):
            for course in sem.courses:
                codes.add(course.course_code)
    return codes


# ---------------------------------------------------------------------------
# Issue 7.1 roadmap tests
# ---------------------------------------------------------------------------

def test_empty_semester_offering_included_in_roadmap():
    """[] semester_offering must be treated as available in every semester by roadmap."""
    output = generate_graduation_roadmap(_make_roadmap_input("Fall", [_COURSE_EMPTY_9]))
    assert output.status == "complete", f"Unexpected status: {output.status}"
    assert "C-EMPTY9" in _all_roadmap_codes(output), (
        "Course with empty semester_offering must appear in roadmap"
    )


def test_fall_only_excluded_from_spring_roadmap():
    """["Fall"] course must not appear in any Spring-labeled semester in the roadmap."""
    # Start from Spring — COURSE_FALL_9 must be excluded; COURSE_EMPTY_9 carries graduation
    output = generate_graduation_roadmap(
        _make_roadmap_input("Spring", [_COURSE_FALL_9, _COURSE_EMPTY_9])
    )
    assert output.status == "complete", f"Unexpected status: {output.status}"
    spring_codes = _codes_in_season(output, "Spring")
    assert "C-FALL9" not in spring_codes, (
        "Fall-only course must not appear in a Spring semester of the roadmap"
    )
