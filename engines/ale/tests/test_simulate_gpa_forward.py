"""
Synthetic unit tests for simulate_gpa_forward.

21 tests covering:
  - Formula verification for first_attempt (single and multiple courses)
  - Grade resolution from letter, percentage float, and grade-points float
  - Retake grade caps: failed_retake (B cap), improve_retake #1 (no cap), improve_retake #2 (B cap)
  - GPA-neutral courses: P-grade and zero-credit anomaly
  - In-progress note propagation
  - All structural validation guards (missing fields, invalid inputs)
  - Invalid attempt_type via Pydantic Literal and model_construct() bypass

All tests use a fixed baseline: current_cgpa=2.0, gpa_counted_credits=15, current_quality_points=30.0.
No SCP, KG, RAG, or LLM calls.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engines.ale.functions.simulate_gpa_forward import simulate_gpa_forward
from engines.ale.schemas import (
    GradingScaleRules,
    PercentageRange,
    PlannedCourseGPA,
    RetakeRules,
    SimulateGPAForwardInput,
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


def _inp(**overrides) -> SimulateGPAForwardInput:
    """Baseline: CPs=30, CHs=15, cgpa=2.0. Override any field via kwargs."""
    defaults: dict = dict(
        current_cgpa=2.0,
        gpa_counted_credits=15,
        current_quality_points=30.0,
        planned_courses=[],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
        excluded_in_progress_courses=[],
    )
    defaults.update(overrides)
    return SimulateGPAForwardInput(**defaults)


def _course(**overrides) -> PlannedCourseGPA:
    """Default: 3cr, grade B, first_attempt, no footprint."""
    defaults: dict = dict(
        course_code="C-CS101",
        course_name="Test Course",
        credits=3,
        expected_grade="B",
        attempt_type="first_attempt",
        has_cgpa_footprint=False,
    )
    defaults.update(overrides)
    return PlannedCourseGPA(**defaults)


# ── Group 1: Formula verification ─────────────────────────────────────────────

def test_first_attempt_single_course():
    # CPs=30, CHs=15; add 3cr B(3.0)
    # new_QP = 30 + 9 = 39 / new_CHs = 18 → projected = round(39/18, 2) = 2.17
    inp = _inp(planned_courses=[_course()])
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert out.projected_cgpa == 2.17
    assert out.delta == 0.17
    assert out.per_course_breakdown[0].footprint_action == "addition"


def test_first_attempt_multiple_courses():
    # Add 3cr B(3.0) and 3cr A+(4.0)
    # new_QP = 30+9+12 = 51 / new_CHs = 21 → projected = round(51/21, 2) = 2.43
    courses = [
        _course(course_code="C-CS101", expected_grade="B"),
        _course(course_code="C-CS102", expected_grade="A+"),
    ]
    inp = _inp(planned_courses=courses)
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert out.projected_cgpa == 2.43
    assert out.delta == 0.43


# ── Group 2: Grade resolution formats ─────────────────────────────────────────

def test_percentage_grade_resolves_to_letter():
    # 83.0 → B → 3.0; same formula as single-course first_attempt → projected=2.17
    inp = _inp(planned_courses=[_course(expected_grade=83.0)])
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert out.projected_cgpa == 2.17


def test_grade_points_float_input():
    # 3.0 (already grade points 0.0–4.0); same result
    inp = _inp(planned_courses=[_course(expected_grade=3.0)])
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert out.projected_cgpa == 2.17


# ── Group 3: Retake grade caps ─────────────────────────────────────────────────

def test_failed_retake_replacement():
    # old=F(0.0), new=C(2.0), 3cr; subtract 0, add 6; CHs unchanged at 15
    # new_QP=36 / 15 → projected=2.4
    course = _course(
        expected_grade="C",
        attempt_type="failed_retake",
        has_cgpa_footprint=True,
        old_grade="F",
    )
    inp = _inp(planned_courses=[course])
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert out.projected_cgpa == 2.4
    assert out.delta == 0.4
    assert out.per_course_breakdown[0].footprint_action == "replacement"


def test_failed_retake_grade_cap_a_plus_to_b():
    # A+(4.0) capped at B(3.0); old=F; new_QP=39/15 → projected=2.6; GradeOverride emitted
    course = _course(
        expected_grade="A+",
        attempt_type="failed_retake",
        has_cgpa_footprint=True,
        old_grade="F",
    )
    inp = _inp(planned_courses=[course])
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert out.projected_cgpa == 2.6
    assert len(out.grade_overrides) == 1
    assert out.grade_overrides[0].applied_grade == "B"
    assert out.grade_overrides[0].requested_grade == "A+"


def test_improve_retake_first_no_cap():
    # #1 improve retake: improve_retake_first_attempt_cap=None → full A+(4.0) applied
    # old=B(3.0); subtract 9, add 12; new_QP=33/15 → projected=2.2; no override
    course = _course(
        expected_grade="A+",
        attempt_type="improve_retake",
        has_cgpa_footprint=True,
        old_grade="B",
        improve_retake_number=1,
    )
    inp = _inp(planned_courses=[course])
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert out.projected_cgpa == 2.2
    assert out.grade_overrides == []


def test_improve_retake_second_capped_at_b():
    # #2 improve retake: cap at B(3.0); old=B-(2.7)
    # subtract 8.1, add 9; new_QP=30.9/15 → projected=2.06; GradeOverride emitted
    course = _course(
        expected_grade="A+",
        attempt_type="improve_retake",
        has_cgpa_footprint=True,
        old_grade="B-",
        improve_retake_number=2,
    )
    inp = _inp(planned_courses=[course])
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert out.projected_cgpa == 2.06
    assert len(out.grade_overrides) == 1
    assert out.grade_overrides[0].applied_grade == "B"


# ── Group 4: GPA-neutral and warnings ─────────────────────────────────────────

def test_p_grade_gpa_neutral():
    # P-grade → gpa_neutral; CGPA and denominator unchanged
    inp = _inp(planned_courses=[_course(expected_grade="P")])
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert out.projected_cgpa == 2.0
    assert out.delta == 0.0
    assert any("pass/fail" in w for w in out.warnings)


def test_zero_credit_anomaly_warning():
    # 0cr with non-P grade → data anomaly; treated as gpa_neutral; warning emitted
    inp = _inp(planned_courses=[_course(credits=0, expected_grade="B")])
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert any("zero_credit_course_unexpected_grade" in w for w in out.warnings)
    assert "C-CS101" in out.gpa_neutral_courses


def test_in_progress_note_set():
    inp = _inp(planned_courses=[_course(is_currently_in_progress=True)])
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert out.per_course_breakdown[0].in_progress_note is not None


def test_all_gpa_neutral_multiple_p_grade_courses():
    # All courses P-grade → "All provided courses are pass/fail" warning; delta=0
    courses = [
        _course(course_code="C-CS101", expected_grade="P"),
        _course(course_code="C-CS102", expected_grade="P"),
    ]
    inp = _inp(planned_courses=courses)
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    assert out.projected_cgpa == 2.0
    assert out.delta == 0.0
    assert any("All provided courses are pass/fail" in w for w in out.warnings)


# ── Group 5: Structural validation guards ─────────────────────────────────────

def test_empty_planned_courses():
    inp = _inp(planned_courses=[])
    out = simulate_gpa_forward(inp)

    assert out.status == "cannot_compute"
    assert "empty_planned_courses" in out.reason_codes


def test_missing_expected_grade_model_construct():
    course = PlannedCourseGPA.model_construct(
        course_code="C-CS101",
        course_name="Test Course",
        credits=3,
        expected_grade=None,
        attempt_type="first_attempt",
        has_cgpa_footprint=False,
    )
    inp = _inp(planned_courses=[course])
    out = simulate_gpa_forward(inp)

    assert out.status == "cannot_compute"
    assert any("missing_expected_grade" in r for r in out.required_data_missing)


def test_missing_credits_model_construct():
    course = PlannedCourseGPA.model_construct(
        course_code="C-CS101",
        course_name="Test Course",
        credits=None,
        expected_grade="B",
        attempt_type="first_attempt",
        has_cgpa_footprint=False,
    )
    inp = _inp(planned_courses=[course])
    out = simulate_gpa_forward(inp)

    assert out.status == "cannot_compute"
    assert any("missing_credits" in r for r in out.required_data_missing)


def test_missing_old_grade_for_footprint_course():
    # has_cgpa_footprint=True but old_grade=None (Pydantic allows it; ALE catches it)
    course = _course(
        attempt_type="failed_retake",
        has_cgpa_footprint=True,
        old_grade=None,
    )
    inp = _inp(planned_courses=[course])
    out = simulate_gpa_forward(inp)

    assert out.status == "cannot_compute"
    assert any("missing_old_grade" in r for r in out.required_data_missing)


def test_missing_current_quality_points_model_construct():
    inp = SimulateGPAForwardInput.model_construct(
        current_cgpa=2.0,
        gpa_counted_credits=15,
        current_quality_points=None,
        planned_courses=[_course()],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
        excluded_in_progress_courses=[],
    )
    out = simulate_gpa_forward(inp)

    assert out.status == "cannot_compute"
    assert "missing_current_quality_points" in out.reason_codes


def test_missing_gpa_counted_credits_model_construct():
    # Without the Step 4b guard, line 159 (`running_denominator = input.gpa_counted_credits`)
    # would crash with TypeError when gpa_counted_credits is None.
    inp = SimulateGPAForwardInput.model_construct(
        current_cgpa=2.0,
        gpa_counted_credits=None,
        current_quality_points=30.0,
        planned_courses=[_course()],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
        excluded_in_progress_courses=[],
    )
    out = simulate_gpa_forward(inp)

    assert out.status == "cannot_compute"
    assert "missing_gpa_counted_credits" in out.reason_codes


def test_invalid_grade_string_cannot_compute():
    inp = _inp(planned_courses=[_course(expected_grade="ZZZTOTAL_INVALID")])
    out = simulate_gpa_forward(inp)

    assert out.status == "cannot_compute"
    assert any("invalid_grade_input" in r for r in out.reason_codes)


# ── Group 6: attempt_type validation ──────────────────────────────────────────

def test_invalid_attempt_type_pydantic_literal_rejects():
    # Literal["first_attempt", "failed_retake", "improve_retake"] enforced at construction
    with pytest.raises(ValidationError):
        PlannedCourseGPA(
            course_code="C-CS101",
            course_name="Test Course",
            credits=3,
            expected_grade="B",
            attempt_type="garbage",
            has_cgpa_footprint=False,
        )


def test_invalid_attempt_type_model_construct_cannot_compute():
    # model_construct() bypasses Pydantic; Step 4c guard catches it in the function
    course = PlannedCourseGPA.model_construct(
        course_code="C-CS101",
        course_name="Test Course",
        credits=3,
        expected_grade="B",
        attempt_type="garbage",
        has_cgpa_footprint=False,
    )
    inp = _inp(planned_courses=[course])
    out = simulate_gpa_forward(inp)

    assert out.status == "cannot_compute"
    assert "invalid_attempt_type_C-CS101" in out.reason_codes
