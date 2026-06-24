"""
Real-record regression tests for generate_graduation_roadmap.

Uses data/students_anonymous.xlsx when available; skipped otherwise.
ALE has no KG access — course pools are synthetic but anchored to
real student academic state (PHs, CGPA, level, completed/failed lists).

Key stability anchors:
  STU000004 — Freshman, CGPA≈3.64, PHs=16, Studying.  High-CGPA Freshman.
              Should: pass study-status gate, use max_credits cap with
              max_credits_mode=True, project multi-semester roadmap.

Other students: skipped gracefully if not found in dataset.
"""

from pathlib import Path

import pytest

from engines.ale.functions.generate_graduation_roadmap import generate_graduation_roadmap
from engines.ale.schemas import (
    AcademicWarningRules,
    AvailableCourse,
    CreditLimitRules,
    GenerateGraduationRoadmapInput,
    GraduationRequirementRules,
    StudentLevelRules,
    SummerSemesterRules,
)

# ---------------------------------------------------------------------------
# Dataset guard
# ---------------------------------------------------------------------------

_REPO_ROOT  = Path(__file__).parent.parent.parent.parent
_EXCEL_PATH = _REPO_ROOT / "data" / "students_anonymous.xlsx"
_EXCEL_MISSING = not _EXCEL_PATH.is_file()

pytestmark = pytest.mark.skipif(
    _EXCEL_MISSING,
    reason=f"Student dataset not found at {_EXCEL_PATH}",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def scp():
    from gateway.student_context_provider import get_context, load_excel
    load_excel(str(_EXCEL_PATH))
    return get_context


# ---------------------------------------------------------------------------
# Shared rule bundles
# ---------------------------------------------------------------------------

def _grad_rules() -> GraduationRequirementRules:
    return GraduationRequirementRules(
        total_credits_required=133,
        minimum_cgpa=2.0,
        minimum_regular_semesters=6,
        maximum_regular_semesters=16,
        must_pass_zero_credit_courses=True,
        military_training_required_for_males=True,
    )


def _credit_rules() -> CreditLimitRules:
    return CreditLimitRules(
        cgpa_above_3_limit=21,
        cgpa_between_2_and_3_limit=18,
        cgpa_between_1_and_2_limit=15,
        cgpa_below_1_limit=12,
        minimum_per_semester=9,
        final_semester_override=21,
        incomplete_extra_course_allowed=True,
    )


def _summer_rules() -> SummerSemesterRules:
    return SummerSemesterRules(
        default_max_courses=2,
        cgpa_above_3_max_courses=3,
        cgpa_threshold_for_extra_course=3.0,
    )


def _level_rules() -> StudentLevelRules:
    return StudentLevelRules(
        freshman_max_hours=26,
        sophomore_min_hours=27,
        sophomore_max_hours=59,
        junior_min_hours=60,
        junior_max_hours=93,
        senior_min_hours=94,
        senior_max_hours=133,
    )


def _many_courses(n: int = 40, credits_each: int = 3) -> list[AvailableCourse]:
    return [
        AvailableCourse(
            course_code=f"C{i:03d}",
            name=f"Course {i}",
            credits=credits_each,
            level=2,
            semester_offering=[],
            prerequisites=[],
            credit_threshold=None,
            track=["CS"],
        )
        for i in range(1, n + 1)
    ]


def _build_inp(
    ctx,
    starting_semester: str = "Fall 2026",
    max_credits_mode: bool = False,
    accelerated_mode: bool = False,
    target_end_semester_type: str | None = None,
    target_end_year: int | None = None,
) -> GenerateGraduationRoadmapInput:
    parts = starting_semester.split()
    season, year = parts[0], int(parts[1])

    level_map = {1: "Freshman", 2: "Sophomore", 3: "Junior", 4: "Senior"}

    return GenerateGraduationRoadmapInput(
        study_status=ctx.study_status,
        completed_courses=ctx.completed_courses,
        failed_courses=ctx.failed_courses,
        in_progress_courses=ctx.in_progress_courses,
        retake_count=ctx.retake_count,
        total_improve_retakes_used=ctx.total_improve_retakes_used,
        current_cgpa=ctx.cgpa or 0.0,
        gpa_counted_credits=ctx.cumulative_chs or 0,
        current_quality_points=ctx.cumulative_cps or 0.0,
        cumulative_passed_hours=ctx.total_credit_hours_earned or 0,
        student_level=level_map.get(ctx.level, "Freshman"),
        official_track=ctx.track_id,
        incomplete_grade_flag=any(r.grade == "I" for r in ctx.course_history),
        zero_credit_courses_passed=bool(ctx.zero_credit_courses_passed),
        military_status=ctx.military_status,
        completed_regular_semesters=ctx.completed_regular_semesters,
        consecutive_warnings=ctx.consecutive_warnings,
        total_warnings=ctx.total_warnings,
        available_courses=_many_courses(40, 3),
        credit_limit_rules=_credit_rules(),
        graduation_rules=_grad_rules(),
        summer_semester_rules=_summer_rules() if accelerated_mode else None,
        target_semester_type=season,
        starting_year=year,
        accelerated_mode=accelerated_mode,
        max_credits_mode=max_credits_mode,
        assumed_grade_per_pass=3.0,
        student_level_rules=_level_rules(),
        target_end_semester_type=target_end_semester_type,
        target_end_year=target_end_year,
    )


# ---------------------------------------------------------------------------
# STU000004 — Freshman, CGPA≈3.64, PHs=16, Studying
# ---------------------------------------------------------------------------

class TestSTU000004:

    def test_study_status_gate_passes(self, scp):
        ctx = scp("STU000004")
        if ctx is None:
            pytest.skip("STU000004 not found in dataset")
        assert ctx.study_status == "Studying"
        inp = _build_inp(ctx)
        out = generate_graduation_roadmap(inp)
        assert out.status != "not_applicable"

    def test_is_freshman_with_16_phs(self, scp):
        ctx = scp("STU000004")
        if ctx is None:
            pytest.skip("STU000004 not found in dataset")
        assert ctx.total_credit_hours_earned == 16

    def test_roadmap_produces_multiple_semesters(self, scp):
        ctx = scp("STU000004")
        if ctx is None:
            pytest.skip("STU000004 not found in dataset")
        inp = _build_inp(ctx)
        out = generate_graduation_roadmap(inp)
        # 133-16=117 credits remaining → many semesters needed
        assert out.total_passes > 3

    def test_default_credit_cap_applied_not_max(self, scp):
        # CGPA≈3.64 ≥ 2.0 but NO max_credits_mode → default path → mid bracket (18)
        ctx = scp("STU000004")
        if ctx is None:
            pytest.skip("STU000004 not found in dataset")
        inp = _build_inp(ctx, max_credits_mode=False)
        out = generate_graduation_roadmap(inp)
        if out.semester_plans:
            assert out.semester_plans[0].total_credits <= 18

    def test_max_credits_mode_allows_higher_cap(self, scp):
        # CGPA≈3.64 ≥ 3.0 + max_credits_mode=True → cgpa_above_3_limit (21)
        ctx = scp("STU000004")
        if ctx is None:
            pytest.skip("STU000004 not found in dataset")
        inp = _build_inp(ctx, max_credits_mode=True)
        out = generate_graduation_roadmap(inp)
        if out.semester_plans:
            assert out.semester_plans[0].total_credits <= 21

    def test_completed_courses_not_reselected(self, scp):
        ctx = scp("STU000004")
        if ctx is None:
            pytest.skip("STU000004 not found in dataset")
        inp = _build_inp(ctx)
        out = generate_graduation_roadmap(inp)
        # No course from completed_courses should appear in any planned semester
        for sem in out.semester_plans:
            for course in sem.courses:
                assert course.course_code not in ctx.completed_courses

    def test_target_semester_mode_stops_early(self, scp):
        # Request only Fall 2026 + Spring 2027 (2 semesters)
        ctx = scp("STU000004")
        if ctx is None:
            pytest.skip("STU000004 not found in dataset")
        inp = _build_inp(
            ctx,
            starting_semester="Fall 2026",
            target_end_semester_type="Spring",
            target_end_year=2027,
        )
        out = generate_graduation_roadmap(inp)
        # Student has 16 PHs → definitely won't graduate in 2 semesters
        assert out.status == "cannot_complete_projection"
        assert "target_semester_reached_not_graduated" in out.reason_codes
        assert out.total_passes <= 2
        assert out.remaining_graduation_gaps is not None


# ---------------------------------------------------------------------------
# STU000026 — may not be in dataset
# ---------------------------------------------------------------------------

class TestSTU000026:

    def test_study_status_stable(self, scp):
        ctx = scp("STU000026")
        if ctx is None:
            pytest.skip("STU000026 not found in dataset")
        inp = _build_inp(ctx)
        out = generate_graduation_roadmap(inp)
        if ctx.study_status != "Studying":
            assert out.status == "not_applicable"
        else:
            assert out.status in ("complete", "cannot_complete_projection", "blocked")


# ---------------------------------------------------------------------------
# Non-Studying student check (uses any non-studying record if available)
# ---------------------------------------------------------------------------

class TestNonStudying:

    def test_graduated_student_returns_not_applicable(self, scp):
        # Try a known graduated student ID — falls back to skip if not found
        for sid in ("STU000041", "STU000050", "STU000099"):
            ctx = scp(sid)
            if ctx is not None and ctx.study_status == "Graduated":
                inp = _build_inp(ctx)
                out = generate_graduation_roadmap(inp)
                assert out.status == "not_applicable"
                return
        pytest.skip("No graduated student found in dataset for this test")
