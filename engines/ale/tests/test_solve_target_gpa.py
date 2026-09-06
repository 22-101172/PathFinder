"""
Synthetic unit tests for solve_target_gpa.

32 tests covering:
  A. Basic statuses / structural validation (10 tests)
  B. First-attempt target GPA math (3 tests)
  C. Replacement retake math (3 tests)
  D. Cap-aware distribution (5 tests)
  E. Personalized distribution (5 tests)
  F. Multi-semester projection (4 tests)
  G. Invalid attempt_type (2 tests)

All tests use a fixed baseline unless overridden:
  current_cgpa=2.0, gpa_counted_credits=15, current_quality_points=30.0
No SCP, KG, RAG, or LLM calls.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engines.ale.functions.solve_target_gpa import solve_target_gpa
from engines.ale.ale_schemas import (
    GradingScaleRules,
    GraduationRequirementRules,
    PercentageRange,
    PlannedCourseTarget,
    RetakeRules,
    SolveTargetGPAInput,
)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _grading_scale() -> GradingScaleRules:
    return GradingScaleRules(
        letter_to_points={
            "A+": 4.0, "A": 4.0, "A-": 3.7,
            "B+": 3.3, "B": 3.0, "B-": 2.7,
            "C+": 2.3, "C": 2.0, "C-": 1.7,
            "D+": 1.3, "D": 1.0, "F": 0.0, "P": None,
        },
        percentage_to_letter=[
            PercentageRange(min_pct=97, max_pct=100, letter="A+"),
            PercentageRange(min_pct=93, max_pct=96,  letter="A"),
            PercentageRange(min_pct=90, max_pct=92,  letter="A-"),
            PercentageRange(min_pct=87, max_pct=89,  letter="B+"),
            PercentageRange(min_pct=83, max_pct=86,  letter="B"),
            PercentageRange(min_pct=80, max_pct=82,  letter="B-"),
            PercentageRange(min_pct=77, max_pct=79,  letter="C+"),
            PercentageRange(min_pct=73, max_pct=76,  letter="C"),
            PercentageRange(min_pct=70, max_pct=72,  letter="C-"),
            PercentageRange(min_pct=67, max_pct=69,  letter="D+"),
            PercentageRange(min_pct=60, max_pct=66,  letter="D"),
            PercentageRange(min_pct=0,  max_pct=59,  letter="F"),
        ],
    )


def _retake_rules() -> RetakeRules:
    return RetakeRules(
        failed_first_retake_grade_cap="B",
        improve_retake_first_attempt_cap=None,
        improve_retake_subsequent_cap="B",
        improve_retake_max_courses_cgpa_above_2=3,
        improve_retake_unlimited_below_cgpa=2.0,
    )


def _graduation_rules() -> GraduationRequirementRules:
    return GraduationRequirementRules(
        total_credits_required=133,
        minimum_cgpa=2.0,
        minimum_regular_semesters=6,
        maximum_regular_semesters=16,
        must_pass_zero_credit_courses=True,
        military_training_required_for_males=True,
    )


def _inp(**overrides) -> SolveTargetGPAInput:
    """Baseline: CPs=30, CHs=15, cgpa=2.0, target=2.2. Override any field via kwargs."""
    defaults: dict = dict(
        current_cgpa=2.0,
        gpa_counted_credits=15,
        current_quality_points=30.0,
        target_cgpa=2.2,
        planned_courses=[],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
        graduation_rules=_graduation_rules(),
        credits_per_semester=18,
        planned_course_source="explicit_user_courses",
    )
    defaults.update(overrides)
    return SolveTargetGPAInput(**defaults)


def _course(**overrides) -> PlannedCourseTarget:
    """Default: 3cr, first_attempt, no footprint, no personalization data."""
    defaults: dict = dict(
        course_code="C-CS101",
        course_name="Test Course",
        credits=3,
        attempt_type="first_attempt",
        has_cgpa_footprint=False,
    )
    defaults.update(overrides)
    return PlannedCourseTarget(**defaults)


def _by_code(distribution, code: str):
    """Find a CourseTarget in a distribution by course_code."""
    for ct in distribution:
        if ct.course_code == code:
            return ct
    raise KeyError(f"{code} not found in distribution")


# ── Group A: Basic statuses / structural validation ────────────────────────────

def test_already_met():
    # current_cgpa=2.0, target=1.5 → already met at Step 1b
    inp = _inp(target_cgpa=1.5, planned_courses=[_course()])
    out = solve_target_gpa(inp)

    assert out.status == "already_met"
    assert out.delta_needed == round(1.5 - 2.0, 2)
    assert out.required_average_grade_points is None
    assert out.default_distribution is None


def test_invalid_target_cgpa_zero():
    inp = _inp(target_cgpa=0.0, planned_courses=[_course()])
    out = solve_target_gpa(inp)

    assert out.status == "cannot_compute"
    assert "invalid_target_cgpa" in out.reason_codes


def test_invalid_target_cgpa_above_four():
    inp = _inp(target_cgpa=4.1, planned_courses=[_course()])
    out = solve_target_gpa(inp)

    assert out.status == "cannot_compute"
    assert "invalid_target_cgpa" in out.reason_codes


def test_empty_planned_courses():
    inp = _inp(planned_courses=[])
    out = solve_target_gpa(inp)

    assert out.status == "cannot_compute"
    assert "empty_planned_courses" in out.reason_codes


def test_missing_current_quality_points():
    inp = SolveTargetGPAInput.model_construct(
        current_cgpa=2.0,
        gpa_counted_credits=15,
        current_quality_points=None,
        target_cgpa=2.2,
        planned_courses=[_course()],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
        graduation_rules=_graduation_rules(),
        credits_per_semester=18,
        planned_course_source="explicit_user_courses",
    )
    out = solve_target_gpa(inp)

    assert out.status == "cannot_compute"
    assert "missing_current_quality_points" in out.reason_codes


def test_missing_gpa_counted_credits():
    # Without Step 5b guard, line computing final_denominator would crash with TypeError
    inp = SolveTargetGPAInput.model_construct(
        current_cgpa=2.0,
        gpa_counted_credits=None,
        current_quality_points=30.0,
        target_cgpa=2.2,
        planned_courses=[_course()],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
        graduation_rules=_graduation_rules(),
        credits_per_semester=18,
        planned_course_source="explicit_user_courses",
    )
    out = solve_target_gpa(inp)

    assert out.status == "cannot_compute"
    assert "missing_gpa_counted_credits" in out.reason_codes


def test_missing_credits():
    course = PlannedCourseTarget.model_construct(
        course_code="C-CS101",
        course_name="Test Course",
        credits=None,
        attempt_type="first_attempt",
        has_cgpa_footprint=False,
    )
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "cannot_compute"
    assert any("missing_credits" in r for r in out.required_data_missing)


def test_missing_old_grade_for_footprint():
    # has_cgpa_footprint=True but old_grade defaults to None — Pydantic allows it; ALE catches it
    course = _course(
        attempt_type="failed_retake",
        has_cgpa_footprint=True,
        old_grade=None,
    )
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "cannot_compute"
    assert any("missing_old_grade" in r for r in out.required_data_missing)


def test_invalid_old_grade():
    # Unrecognized old_grade string
    course = _course(
        attempt_type="failed_retake",
        has_cgpa_footprint=True,
        old_grade="ZZZBAD",
    )
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "cannot_compute"
    assert any("invalid_old_grade" in r for r in out.required_data_missing)


def test_all_courses_gpa_neutral():
    # 0cr course → gpa_neutral → all_courses_gpa_neutral
    course = _course(credits=0)
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "cannot_compute"
    assert "all_courses_gpa_neutral" in out.reason_codes


# ── Group B: First-attempt target GPA math ────────────────────────────────────

def test_first_attempt_single_course_solvable():
    # CPs=30, CHs=15, cgpa=2.0, target=2.2, single 3cr first_attempt
    # final_denom = 15+3=18; req_new_pts = 2.2*18-30 = 9.6; req_avg = 9.6/3 = 3.2
    inp = _inp(planned_courses=[_course()])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    assert abs(out.required_average_grade_points - 3.2) < 0.001
    assert out.delta_needed == round(2.2 - 2.0, 2)
    assert out.required_average_letter is not None
    assert out.default_distribution is not None
    assert len(out.default_distribution) == 1


def test_first_attempt_multiple_courses_solvable():
    # CPs=30, CHs=15, cgpa=2.0, target=2.5, two 3cr first_attempt
    # final_denom = 15+6=21; req_new_pts = 2.5*21-30 = 22.5; req_avg = 22.5/6 = 3.75
    courses = [
        _course(course_code="C-CS101"),
        _course(course_code="C-CS102"),
    ]
    inp = _inp(target_cgpa=2.5, planned_courses=courses)
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    assert abs(out.required_average_grade_points - 3.75) < 0.001
    assert len(out.default_distribution) == 2


def test_first_attempt_impossible_required_average_exceeds_max():
    # CPs=30, CHs=15, cgpa=2.0, target=3.0, single 3cr
    # req_new_pts = 3.0*18-30 = 24; req_avg = 24/3 = 8.0 > 4.0
    inp = _inp(target_cgpa=3.0, planned_courses=[_course()])
    out = solve_target_gpa(inp)

    assert out.status == "impossible"
    assert "required_average_exceeds_maximum" in out.reason_codes
    assert out.maximum_reachable_cgpa is not None
    # max_reachable = (30 + 4.0*3) / 18 = 42/18 = 2.33
    assert abs(out.maximum_reachable_cgpa - round(42 / 18, 2)) < 0.01


# ── Group C: Replacement retake math ──────────────────────────────────────────

def test_failed_retake_replacement_denominator_unchanged():
    # CPs=30, CHs=15, cgpa=2.0, target=2.2, 3cr failed_retake old=F
    # adjusted_qp = 30 - 0*3 = 30; final_denom = 15 (footprint → CHs unchanged)
    # req_new_pts = 2.2*15 - 30 = 3; req_avg = 3/3 = 1.0
    course = _course(
        attempt_type="failed_retake",
        has_cgpa_footprint=True,
        old_grade="F",
    )
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    assert abs(out.required_average_grade_points - 1.0) < 0.001


def test_improve_retake_first_no_cap():
    # CPs=30, CHs=15, cgpa=2.0, target=2.2, 3cr improve_retake #1, old=C(2.0)
    # adjusted_qp = 30 - 2.0*3 = 24; final_denom = 15 (footprint)
    # req_new_pts = 2.2*15 - 24 = 9; req_avg = 9/3 = 3.0 = B
    # improve_retake #1 has no cap → solvable at B
    course = _course(
        attempt_type="improve_retake",
        has_cgpa_footprint=True,
        old_grade="C",
        improve_retake_number=1,
    )
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    assert abs(out.required_average_grade_points - 3.0) < 0.001


def test_improve_retake_subsequent_capped_impossible():
    # CPs=30, CHs=15, cgpa=2.0, target=2.9, 3cr improve_retake #2, old=D+(1.3)
    # adjusted_qp = 30 - 1.3*3 = 26.1; final_denom = 15
    # req_new_pts = 2.9*15 - 26.1 = 43.5 - 26.1 = 17.4; req_avg = 17.4/3 = 5.8 > 4.0
    # Also: max_qp = B(3.0)*3 = 9; 9 < 17.4 → impossible_due_to_retake_caps
    # Step 16 checks required_average > max_overall_gp first: 5.8 > 4.0 → required_average_exceeds_maximum
    course = _course(
        attempt_type="improve_retake",
        has_cgpa_footprint=True,
        old_grade="D+",
        improve_retake_number=2,
    )
    inp = _inp(target_cgpa=2.9, planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "impossible"
    # Two capped improve-retakes where max grade B can't deliver required_average
    assert out.reason_codes  # contains some impossibility reason


# ── Group D: Cap-aware distribution ───────────────────────────────────────────

def test_cap_aware_required_avg_below_all_caps_equal_distribution():
    # CPs=30, CHs=15, cgpa=2.0, target=2.2
    # Course A: 3cr failed_retake old=F (cap B=3.0)
    # Course B: 3cr first_attempt (uncapped)
    # adjusted_qp=30; final_denom=15+3=18; req_new_pts=9.6; req_avg=9.6/6=1.6 < B(3.0)
    # Equal distribution valid: both get 1.6
    course_a = _course(
        course_code="C-RET101", attempt_type="failed_retake",
        has_cgpa_footprint=True, old_grade="F",
    )
    course_b = _course(course_code="C-NEW101")
    inp = _inp(planned_courses=[course_a, course_b])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    assert out.default_distribution is not None
    a = _by_code(out.default_distribution, "C-RET101")
    b = _by_code(out.default_distribution, "C-NEW101")
    assert abs(a.target_grade_points - b.target_grade_points) < 0.001
    assert a.target_grade_points <= 3.0  # does not exceed B cap


def test_cap_aware_redistribution_to_uncapped_course():
    # CPs=30, CHs=15, cgpa=2.0, target=2.8
    # Course A: 3cr failed_retake old=F (cap B=3.0)
    # Course B: 3cr first_attempt (cap 4.0)
    # adjusted_qp=30; final_denom=15+3=18; req_new_pts=2.8*18-30=20.4; req_avg=3.4
    # req_avg=3.4 > B cap(3.0) → Course A capped at 3.0
    # rem_qp=20.4-9=11.4, rem_creds=3; Course B gets 11.4/3=3.8
    course_a = _course(
        course_code="C-RET101", attempt_type="failed_retake",
        has_cgpa_footprint=True, old_grade="F",
    )
    course_b = _course(course_code="C-NEW101")
    inp = _inp(target_cgpa=2.8, planned_courses=[course_a, course_b])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    assert out.default_distribution is not None

    a = _by_code(out.default_distribution, "C-RET101")
    b = _by_code(out.default_distribution, "C-NEW101")

    assert abs(a.target_grade_points - 3.0) < 0.001, "Capped course must equal its cap"
    assert abs(b.target_grade_points - 3.8) < 0.001, "Uncapped course must absorb the shortfall"


def test_cap_aware_impossible_due_to_retake_caps():
    # Two 3cr failed_retakes (old=F, cap B=3.0), target=3.5
    # adjusted_qp=30; final_denom=15 (both footprint); req_new_pts=3.5*15-30=22.5
    # max_qp = 3.0*3+3.0*3=18 < 22.5 → impossible_due_to_retake_caps
    course_a = _course(
        course_code="C-RET101", attempt_type="failed_retake",
        has_cgpa_footprint=True, old_grade="F",
    )
    course_b = _course(
        course_code="C-RET102", attempt_type="failed_retake",
        has_cgpa_footprint=True, old_grade="F",
    )
    inp = _inp(target_cgpa=3.5, planned_courses=[course_a, course_b])
    out = solve_target_gpa(inp)

    assert out.status == "impossible"
    assert "impossible_due_to_retake_caps" in out.reason_codes
    assert out.default_distribution is None


def test_cap_aware_no_target_exceeds_max_grade_points():
    # Same as redistribution test — verify no target exceeds the course's retake cap
    course_a = _course(
        course_code="C-RET101", attempt_type="failed_retake",
        has_cgpa_footprint=True, old_grade="F",
    )
    course_b = _course(course_code="C-NEW101")
    inp = _inp(target_cgpa=2.8, planned_courses=[course_a, course_b])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    a = _by_code(out.default_distribution, "C-RET101")
    b = _by_code(out.default_distribution, "C-NEW101")
    assert a.target_grade_points <= 3.0, "failed_retake cap is B(3.0)"
    assert b.target_grade_points <= 4.0, "first_attempt cap is overall max(4.0)"


def test_cap_aware_total_qp_matches_required_new_points():
    # Verify the distribution exactly sums to required_new_points (within float tolerance)
    course_a = _course(
        course_code="C-RET101", attempt_type="failed_retake",
        has_cgpa_footprint=True, old_grade="F",
    )
    course_b = _course(course_code="C-NEW101")
    target = 2.8
    inp = _inp(target_cgpa=target, planned_courses=[course_a, course_b])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    # req_new_pts = 2.8*18 - 30 = 20.4
    req_new_pts = target * (15 + 3) - 30.0  # final_denom = 18 (course_b adds 3)
    total_dist_qp = sum(ct.target_grade_points * ct.credits for ct in out.default_distribution)
    assert abs(total_dist_qp - req_new_pts) < 1e-6


# ── Group E: Personalized distribution ────────────────────────────────────────

def test_personalized_high_history_pushes_up():
    # historical A-(3.7) >= 3.4 → raw_target = min(3.2 + 0.4, 4.0) = 3.6
    # required_average = 3.2 (from B1 calc)
    course = _course(
        related_completed_course="C-CS001",
        historical_grade="A-",  # 3.7 >= HIGH_HISTORY_THRESHOLD(3.4)
    )
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    assert out.personalized_distribution is not None
    pd = out.personalized_distribution[0]
    assert pd.is_personalized is True
    assert abs(pd.target_grade_points - 3.6) < 0.001


def test_personalized_low_history_pulls_down():
    # historical C(2.0) <= 2.4 → raw_target = max(3.2 - 0.4, 0.0) = 2.8
    course = _course(
        related_completed_course="C-CS001",
        historical_grade="C",  # 2.0 <= LOW_HISTORY_THRESHOLD(2.4)
    )
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    assert out.personalized_distribution is not None
    pd = out.personalized_distribution[0]
    assert pd.is_personalized is True
    assert abs(pd.target_grade_points - 2.8) < 0.001


def test_personalized_middle_history_no_change():
    # historical B(3.0), between 2.4 and 3.4 → raw_target = required_average = 3.2
    course = _course(
        related_completed_course="C-CS001",
        historical_grade="B",  # 3.0 in (2.4, 3.4)
    )
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    assert out.personalized_distribution is not None
    pd = out.personalized_distribution[0]
    assert pd.is_personalized is False
    assert abs(pd.target_grade_points - 3.2) < 0.001  # no adjustment


def test_personalized_respects_cap():
    # failed_retake (cap B=3.0), high historical → raw_target capped at max_grade_points=B(3.0)
    # required_average = 1.0 (from C1); raw_target = min(1.0+0.4, 3.0) = 1.4
    # 1.4 <= 3.0 → no GradeOverride, but is_personalized=True
    course = _course(
        attempt_type="failed_retake",
        has_cgpa_footprint=True,
        old_grade="F",
        related_completed_course="C-CS001",
        historical_grade="A-",  # high → push up
    )
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    assert out.personalized_distribution is not None
    pd = out.personalized_distribution[0]
    assert pd.target_grade_points <= 3.0, "Personalized target must not exceed failed_retake cap B"


def test_personalized_distribution_invalid_when_low_history_lowers_total():
    # low historical lowers target → total personalized QP < required_new_points
    # required_average=3.2, low historical(C=2.0) → personalized=2.8
    # personalized_qp = 2.8*3 = 8.4 < required_new_pts(9.6) → valid=False
    course = _course(
        related_completed_course="C-CS001",
        historical_grade="C",  # low
    )
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "solvable"
    assert out.personalized_distribution_valid is False
    assert any("advisory" in w.lower() for w in out.warnings)


# ── Group F: Multi-semester projection ────────────────────────────────────────

def test_multi_semester_reachable_within_remaining():
    # Impossible single-course target; multi-semester projects it as reachable
    # CPs=30, CHs=15, cgpa=2.0, target=2.8, 3cr course → impossible (req_avg=6.8>4.0)
    # Projection with B(3.0), 18cr/sem, max_sems=16, completed_sems=2 → remaining=14
    # After 4 sems: (30+4*54)/(15+4*18)=246/87=2.83 >= 2.8 → reachable in 4 sems
    course = _course()
    inp = _inp(
        target_cgpa=2.8,
        planned_courses=[course],
        assumed_grade_per_semester="B",
        credits_per_semester=18,
        completed_regular_semesters=2,
    )
    out = solve_target_gpa(inp)

    assert out.status == "impossible"
    assert out.multi_semester_projection is not None
    assert out.multi_semester_projection.status == "reachable"
    assert out.multi_semester_projection.semesters_needed == 4
    assert out.multi_semester_projection.assumed_grade_letter == "B"


def test_multi_semester_cannot_reach_within_program():
    # Same impossible setup, but only 3 remaining sems (max=5, completed=2)
    # After 3 sems: (30+3*54)/(15+3*18)=192/69≈2.78 < 2.8 → cannot_reach
    course = _course()
    inp = _inp(
        target_cgpa=2.8,
        planned_courses=[course],
        assumed_grade_per_semester="B",
        credits_per_semester=18,
        completed_regular_semesters=2,
        graduation_rules=GraduationRequirementRules(
            total_credits_required=133,
            minimum_cgpa=2.0,
            minimum_regular_semesters=6,
            maximum_regular_semesters=5,
            must_pass_zero_credit_courses=True,
            military_training_required_for_males=True,
        ),
    )
    out = solve_target_gpa(inp)

    assert out.status == "impossible"
    assert out.multi_semester_projection is not None
    assert out.multi_semester_projection.status == "cannot_reach_within_program"
    assert out.multi_semester_projection.semesters_needed is None


def test_multi_semester_invalid_credits_per_semester():
    # credits_per_semester=0 → cannot_compute (validated before projection loop)
    course = _course()
    inp = SolveTargetGPAInput.model_construct(
        current_cgpa=2.0,
        gpa_counted_credits=15,
        current_quality_points=30.0,
        target_cgpa=2.8,
        planned_courses=[course],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
        graduation_rules=_graduation_rules(),
        credits_per_semester=0,
        planned_course_source="explicit_user_courses",
        completed_regular_semesters=None,
    )
    out = solve_target_gpa(inp)

    assert out.status == "cannot_compute"
    assert "invalid_credits_per_semester" in out.reason_codes


def test_multi_semester_remaining_sems_used_correctly():
    # Verify that completed_regular_semesters limits projection correctly
    # With completed_sems=0 and max=16 (remaining=16) → reachable in 4 sems
    # With completed_sems=13 and max=16 (remaining=3) → cannot_reach
    course = _course()
    base_overrides = dict(
        target_cgpa=2.8,
        planned_courses=[course],
        assumed_grade_per_semester="B",
        credits_per_semester=18,
    )

    inp_full = _inp(**{**base_overrides, "completed_regular_semesters": 0})
    out_full = solve_target_gpa(inp_full)

    inp_almost_done = _inp(**{**base_overrides, "completed_regular_semesters": 13})
    out_almost_done = solve_target_gpa(inp_almost_done)

    assert out_full.multi_semester_projection.status == "reachable"
    assert out_almost_done.multi_semester_projection.status == "cannot_reach_within_program"


# ── Group G: Invalid attempt_type ─────────────────────────────────────────────

def test_invalid_attempt_type_pydantic_literal_rejects():
    # Literal["first_attempt", "failed_retake", "improve_retake"] enforced at construction
    with pytest.raises(ValidationError):
        PlannedCourseTarget(
            course_code="C-CS101",
            course_name="Test Course",
            credits=3,
            attempt_type="garbage",
            has_cgpa_footprint=False,
        )


def test_invalid_attempt_type_model_construct_cannot_compute():
    # model_construct() bypasses Pydantic; Step 5c guard catches it in the function
    course = PlannedCourseTarget.model_construct(
        course_code="C-CS101",
        course_name="Test Course",
        credits=3,
        attempt_type="garbage",
        has_cgpa_footprint=False,
    )
    inp = _inp(planned_courses=[course])
    out = solve_target_gpa(inp)

    assert out.status == "cannot_compute"
    assert "invalid_attempt_type_C-CS101" in out.reason_codes
