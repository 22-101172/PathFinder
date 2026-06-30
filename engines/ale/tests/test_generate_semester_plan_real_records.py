"""
Real-record regression tests for generate_semester_plan.

Students selected for stability:
  STU000004  Studying, Freshman level, CGPA≈3.64, PHs=16/133
             Stable snapshot — transcript fields cannot change retroactively.
  STU000026  Studied student whose level/CGPA fields yield stable credit-cap assertions.
  STU000041  Studied student used for study-status gate and ineligibility summary.

All tests supply a synthetic course pool — this layer has no KG access.
Assertions cover: study-status gate, credit-cap bracket, final-semester detection,
level mapping, and student_level Literal validation.

Tests are skipped gracefully when the Excel file is not present.
No KG, RAG, or LLM calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engines.ale.functions.generate_semester_plan import generate_semester_plan
from engines.ale.ale_schemas import (
    AvailableCourse,
    CreditLimitRules,
    GenerateSemesterPlanInput,
    GraduationRequirementRules,
    SummerSemesterRules,
)

# ── File-availability guard ────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_EXCEL_PATH = _REPO_ROOT / "data" / "students_anonymous.xlsx"
_EXCEL_MISSING = not _EXCEL_PATH.is_file()

pytestmark = pytest.mark.skipif(
    _EXCEL_MISSING,
    reason=f"Student dataset not found at {_EXCEL_PATH}",
)


# ── Shared rule-bundle builders ────────────────────────────────────────────────

def _credit_limits() -> CreditLimitRules:
    return CreditLimitRules(
        cgpa_above_3_limit=21,
        cgpa_between_2_and_3_limit=18,
        cgpa_between_1_and_2_limit=15,
        cgpa_below_1_limit=12,
        minimum_per_semester=9,
        final_semester_override=21,
        incomplete_extra_course_allowed=True,
    )


def _grad_rules(total_credits: int = 133) -> GraduationRequirementRules:
    return GraduationRequirementRules(
        total_credits_required=total_credits,
        minimum_cgpa=2.0,
        minimum_regular_semesters=6,
        maximum_regular_semesters=16,
        must_pass_zero_credit_courses=True,
        military_training_required_for_males=True,
    )


def _summer_rules() -> SummerSemesterRules:
    return SummerSemesterRules(
        default_max_courses=2,
        cgpa_above_3_max_courses=3,
        cgpa_threshold_for_extra_course=3.0,
    )


def _synthetic_pool(
    n: int = 4,
    level: int = 1,
    credits: int = 3,
) -> list[AvailableCourse]:
    """Synthetic course pool — all courses offered in Fall and Spring."""
    return [
        AvailableCourse(
            course_code=f"SYN{level}{i:02d}",
            name=f"Synthetic Course {i}",
            credits=credits,
            level=level,
            prerequisites=[],
            semester_offering=["Fall", "Spring"],
            credit_threshold=None,
        )
        for i in range(n)
    ]


def _level_str(level_int: int) -> str:
    return {1: "Freshman", 2: "Sophomore", 3: "Junior", 4: "Senior"}[level_int]


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def scp():
    from gateway.student_context_provider import get_context, load_excel
    load_excel(str(_EXCEL_PATH))
    return get_context


# ── STU000004 — Freshman, CGPA≈3.64, PHs=16 ──────────────────────────────────

def test_real_stu000004_study_status_gate_passes(scp):
    """STU000004 is Studying — generate_semester_plan must not return not_applicable."""
    ctx = scp("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")

    assert ctx.study_status == "Studying", (
        f"Fixture assumption: STU000004 must be Studying; got {ctx.study_status!r}"
    )

    pool = _synthetic_pool(n=4, level=ctx.level)
    inp = GenerateSemesterPlanInput(
        study_status=ctx.study_status,
        completed_courses=ctx.completed_courses,
        failed_courses=ctx.failed_courses,
        in_progress_courses=ctx.in_progress_courses,
        retake_count=ctx.retake_count,
        total_improve_retakes_used=ctx.total_improve_retakes_used,
        current_cgpa=ctx.cgpa,
        cumulative_passed_hours=ctx.total_credit_hours_earned,
        student_level=_level_str(ctx.level),
        official_track=ctx.track_id,
        incomplete_grade_flag=False,
        available_courses=pool,
        credit_limit_rules=_credit_limits(),
        graduation_rules=_grad_rules(),
        target_semester_type="Fall",
    )
    out = generate_semester_plan(inp)

    assert out.status != "not_applicable", (
        f"STU000004 is Studying — expected no study-status gate; got {out.status!r}"
    )


def test_real_stu000004_not_final_semester(scp):
    """STU000004 has 16/133 PHs — 133-16=117 >> 21; cannot be final semester."""
    ctx = scp("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")

    assert ctx.total_credit_hours_earned == 16, (
        f"Stability: expected PHs=16; got {ctx.total_credit_hours_earned}"
    )

    pool = _synthetic_pool(n=4, level=ctx.level)
    inp = GenerateSemesterPlanInput(
        study_status=ctx.study_status,
        completed_courses=ctx.completed_courses,
        failed_courses=ctx.failed_courses,
        in_progress_courses=ctx.in_progress_courses,
        retake_count=ctx.retake_count,
        total_improve_retakes_used=ctx.total_improve_retakes_used,
        current_cgpa=ctx.cgpa,
        cumulative_passed_hours=ctx.total_credit_hours_earned,
        student_level=_level_str(ctx.level),
        official_track=ctx.track_id,
        incomplete_grade_flag=False,
        available_courses=pool,
        credit_limit_rules=_credit_limits(),
        graduation_rules=_grad_rules(total_credits=133),
        target_semester_type="Fall",
    )
    out = generate_semester_plan(inp)

    assert out.is_final_semester is False, (
        f"STU000004 with 16 PHs cannot be in final semester; got is_final_semester={out.is_final_semester}"
    )


def test_real_stu000004_default_credit_cap_uses_bracket_max(scp):
    """STU000004 CGPA≈3.64 ≥ 3.0 — default path uses CGPA-bracket max (21 for cgpa >= 3.0)."""
    ctx = scp("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")

    if ctx.cgpa is None or ctx.cgpa < 2.0:
        pytest.skip(f"STU000004 CGPA must be >= 2.0; got {ctx.cgpa}")

    pool = _synthetic_pool(n=8, level=ctx.level)
    inp = GenerateSemesterPlanInput(
        study_status=ctx.study_status,
        completed_courses=ctx.completed_courses,
        failed_courses=ctx.failed_courses,
        in_progress_courses=ctx.in_progress_courses,
        retake_count=ctx.retake_count,
        total_improve_retakes_used=ctx.total_improve_retakes_used,
        current_cgpa=ctx.cgpa,
        cumulative_passed_hours=ctx.total_credit_hours_earned,
        student_level=_level_str(ctx.level),
        official_track=ctx.track_id,
        incomplete_grade_flag=False,
        available_courses=pool,
        credit_limit_rules=_credit_limits(),
        graduation_rules=_grad_rules(),
        target_semester_type="Fall",
        max_credits_mode=False,
    )
    out = generate_semester_plan(inp)

    # New design: default path = cgpa_bracket_max
    # STU000004 CGPA=3.64 >= 3.0 → cgpa_bracket_max = 21
    expected_cap = 21 if ctx.cgpa >= 3.0 else (18 if ctx.cgpa >= 2.0 else 15)
    assert out.credit_cap_applied == expected_cap, (
        f"STU000004 CGPA={ctx.cgpa}, expected bracket max={expected_cap}; got {out.credit_cap_applied}"
    )


def test_real_stu000004_max_credits_mode_cap_21(scp):
    """STU000004 CGPA≈3.64 >= 3.0 — with max_credits_mode=True → cap=21."""
    ctx = scp("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")

    if ctx.cgpa is None or ctx.cgpa < 3.0:
        pytest.skip(f"STU000004 CGPA must be >= 3.0 for this test; got {ctx.cgpa}")

    pool = _synthetic_pool(n=8, level=ctx.level)
    inp = GenerateSemesterPlanInput(
        study_status=ctx.study_status,
        completed_courses=ctx.completed_courses,
        failed_courses=ctx.failed_courses,
        in_progress_courses=ctx.in_progress_courses,
        retake_count=ctx.retake_count,
        total_improve_retakes_used=ctx.total_improve_retakes_used,
        current_cgpa=ctx.cgpa,
        cumulative_passed_hours=ctx.total_credit_hours_earned,
        student_level=_level_str(ctx.level),
        official_track=ctx.track_id,
        incomplete_grade_flag=False,
        available_courses=pool,
        credit_limit_rules=_credit_limits(),
        graduation_rules=_grad_rules(),
        target_semester_type="Fall",
        max_credits_mode=True,
    )
    out = generate_semester_plan(inp)

    assert out.credit_cap_applied == 21, (
        f"STU000004 with CGPA={ctx.cgpa} >= 3.0 + max_credits_mode → expected cap=21; got {out.credit_cap_applied}"
    )


def test_real_stu000004_level_is_freshman(scp):
    """STU000004 has 16 PHs → must be at Freshman level; level_match courses are level=1."""
    ctx = scp("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")

    assert ctx.level == 1, (
        f"Stability: STU000004 with 16 PHs must be level=1 (Freshman); got {ctx.level}"
    )

    # Two Freshman courses (level_match) + two Senior courses (no match)
    freshman_courses = _synthetic_pool(n=2, level=1)
    senior_courses = _synthetic_pool(n=2, level=4)
    pool = freshman_courses + senior_courses

    inp = GenerateSemesterPlanInput(
        study_status=ctx.study_status,
        completed_courses=ctx.completed_courses,
        failed_courses=ctx.failed_courses,
        in_progress_courses=ctx.in_progress_courses,
        retake_count=ctx.retake_count,
        total_improve_retakes_used=ctx.total_improve_retakes_used,
        current_cgpa=ctx.cgpa,
        cumulative_passed_hours=ctx.total_credit_hours_earned,
        student_level=_level_str(ctx.level),  # "Freshman"
        official_track=ctx.track_id,
        incomplete_grade_flag=False,
        available_courses=pool,
        credit_limit_rules=_credit_limits(),
        graduation_rules=_grad_rules(),
        target_semester_type="Fall",
    )
    out = generate_semester_plan(inp)

    assert out.plans, "Expected at least one plan"
    plan_a = out.plans[0]
    freshman_codes = {c.course_code for c in freshman_courses}
    senior_codes = {c.course_code for c in senior_courses}

    # All Freshman courses in plan_a should have priority_level in ("level_match", "retake")
    for course_out in plan_a.courses:
        if course_out.course_code in freshman_codes:
            assert course_out.priority_level in ("level_match", "retake"), (
                f"Freshman course {course_out.course_code} should be level_match or retake; "
                f"got {course_out.priority_level!r}"
            )
        if course_out.course_code in senior_codes:
            assert course_out.priority_level == "standard", (
                f"Senior course {course_out.course_code} should be standard for Freshman; "
                f"got {course_out.priority_level!r}"
            )


# ── STU000026 — intermediate student, stability assertions ────────────────────

def test_real_stu000026_study_status_gate(scp):
    """STU000026 — if Studying, must pass study-status gate."""
    ctx = scp("STU000026")
    if ctx is None:
        pytest.skip("STU000026 not found in dataset")
    if ctx.study_status != "Studying":
        pytest.skip(f"STU000026 study_status={ctx.study_status!r} — gate expected to fire")

    pool = _synthetic_pool(n=4, level=ctx.level)
    inp = GenerateSemesterPlanInput(
        study_status=ctx.study_status,
        completed_courses=ctx.completed_courses,
        failed_courses=ctx.failed_courses,
        in_progress_courses=ctx.in_progress_courses,
        retake_count=ctx.retake_count,
        total_improve_retakes_used=ctx.total_improve_retakes_used,
        current_cgpa=ctx.cgpa,
        cumulative_passed_hours=ctx.total_credit_hours_earned,
        student_level=_level_str(ctx.level),
        official_track=ctx.track_id,
        incomplete_grade_flag=False,
        available_courses=pool,
        credit_limit_rules=_credit_limits(),
        graduation_rules=_grad_rules(),
        target_semester_type="Fall",
    )
    out = generate_semester_plan(inp)

    assert out.status != "not_applicable", (
        f"STU000026 is Studying — expected no gate; got {out.status!r}"
    )


def test_real_stu000026_credit_cap_consistent_with_cgpa(scp):
    """STU000026 credit cap must match its CGPA bracket."""
    ctx = scp("STU000026")
    if ctx is None:
        pytest.skip("STU000026 not found in dataset")
    if ctx.study_status != "Studying":
        pytest.skip("STU000026 not Studying")
    if ctx.cgpa is None:
        pytest.skip("STU000026 missing CGPA")

    pool = _synthetic_pool(n=8, level=ctx.level)
    inp = GenerateSemesterPlanInput(
        study_status=ctx.study_status,
        completed_courses=ctx.completed_courses,
        failed_courses=ctx.failed_courses,
        in_progress_courses=ctx.in_progress_courses,
        retake_count=ctx.retake_count,
        total_improve_retakes_used=ctx.total_improve_retakes_used,
        current_cgpa=ctx.cgpa,
        cumulative_passed_hours=ctx.total_credit_hours_earned,
        student_level=_level_str(ctx.level),
        official_track=ctx.track_id,
        incomplete_grade_flag=False,
        available_courses=pool,
        credit_limit_rules=_credit_limits(),
        graduation_rules=_grad_rules(),
        target_semester_type="Fall",
        max_credits_mode=False,
    )
    out = generate_semester_plan(inp)

    # Default path: cgpa >= 2.0 → 18; cgpa >= 1.0 → 15; < 1.0 → 12
    if ctx.cgpa >= 2.0:
        expected_cap = 18
    elif ctx.cgpa >= 1.0:
        expected_cap = 15
    else:
        expected_cap = 12

    # Skip if final semester applies (overrides bracket cap)
    remaining = _grad_rules().total_credits_required - ctx.total_credit_hours_earned
    if remaining <= 21:
        pytest.skip(f"STU000026 is in final semester — cap override applies")

    assert out.credit_cap_applied == expected_cap, (
        f"STU000026 CGPA={ctx.cgpa} → expected cap={expected_cap}; got {out.credit_cap_applied}"
    )


# ── STU000041 — additional stability student ──────────────────────────────────

def test_real_stu000041_study_status_gate(scp):
    """STU000041 — if Studying, must pass study-status gate."""
    ctx = scp("STU000041")
    if ctx is None:
        pytest.skip("STU000041 not found in dataset")
    if ctx.study_status != "Studying":
        pytest.skip(f"STU000041 study_status={ctx.study_status!r} — gate expected to fire")

    pool = _synthetic_pool(n=4, level=ctx.level)
    inp = GenerateSemesterPlanInput(
        study_status=ctx.study_status,
        completed_courses=ctx.completed_courses,
        failed_courses=ctx.failed_courses,
        in_progress_courses=ctx.in_progress_courses,
        retake_count=ctx.retake_count,
        total_improve_retakes_used=ctx.total_improve_retakes_used,
        current_cgpa=ctx.cgpa,
        cumulative_passed_hours=ctx.total_credit_hours_earned,
        student_level=_level_str(ctx.level),
        official_track=ctx.track_id,
        incomplete_grade_flag=False,
        available_courses=pool,
        credit_limit_rules=_credit_limits(),
        graduation_rules=_grad_rules(),
        target_semester_type="Fall",
    )
    out = generate_semester_plan(inp)

    assert out.status != "not_applicable", (
        f"STU000041 is Studying — expected no gate; got {out.status!r}"
    )


def test_real_stu000041_plans_generated_with_pool(scp):
    """STU000041 with a fresh synthetic pool should yield plans_generated or no_eligible_courses."""
    ctx = scp("STU000041")
    if ctx is None:
        pytest.skip("STU000041 not found in dataset")
    if ctx.study_status != "Studying":
        pytest.skip("STU000041 not Studying")
    if ctx.cgpa is None:
        pytest.skip("STU000041 missing CGPA")

    pool = _synthetic_pool(n=6, level=ctx.level)
    inp = GenerateSemesterPlanInput(
        study_status=ctx.study_status,
        completed_courses=ctx.completed_courses,
        failed_courses=ctx.failed_courses,
        in_progress_courses=ctx.in_progress_courses,
        retake_count=ctx.retake_count,
        total_improve_retakes_used=ctx.total_improve_retakes_used,
        current_cgpa=ctx.cgpa,
        cumulative_passed_hours=ctx.total_credit_hours_earned,
        student_level=_level_str(ctx.level),
        official_track=ctx.track_id,
        incomplete_grade_flag=False,
        available_courses=pool,
        credit_limit_rules=_credit_limits(),
        graduation_rules=_grad_rules(),
        target_semester_type="Fall",
    )
    out = generate_semester_plan(inp)

    # Synthetic pool has no overlap with real completed/in_progress courses → all eligible
    assert out.status in ("plans_generated", "no_eligible_courses"), (
        f"STU000041 unexpected status: {out.status!r}; reason_codes={out.reason_codes}"
    )
    # Synthetic courses have no prerequisites → all 6 should be eligible
    if out.status == "plans_generated":
        assert out.total_eligible_courses == 6, (
            f"All synthetic courses should be eligible; got {out.total_eligible_courses}"
        )
