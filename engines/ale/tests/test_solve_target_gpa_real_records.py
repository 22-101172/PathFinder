"""
Real-record regression tests for solve_target_gpa.

Students selected for stability:
  STU000004  Studying, Freshman, 16 PHs, CGPA≈3.64, cumulative_chs=16
             Stable historical transcript; CHs/CPS cannot change retroactively.
  STU000009  CHs=43 (replacement policy evidence — matches simulate_gpa_forward tests)

Selection rationale:
  STU000004 — first-semester Freshman with 16/133 PHs.
    (a) target=3.0 → already_met since CGPA=3.64 >= 3.0. Stable because CGPA cannot
        drop below 3.0 retroactively in the static anonymized dataset.
    (b) target=3.8 + 1 course (3cr) → impossible (required_average exceeds 4.0).
        Stability: max achievable CGPA with 1 new A+(4.0) course is ~3.696 < 3.8.
        Even with small floating-point variation in cumulative_cps, the required_average
        will always exceed 4.0 while cgpa < 3.8 and the added course is only 3cr.
    (c) Formula verification: target=3.7, 4cr course → dynamically compute expected
        required_average and verify it matches ALE output. Skipped if not applicable.

  STU000009 — used only to assert cumulative_chs=43 (replacement policy evidence).
    The key fact: registrar's GPA denominator=43 (unique course count, replacement
    policy). Additive policy would give ≥47. This test is shared with simulate_gpa_forward
    and confirms the replacement assumption underlying solve_target_gpa's core math.

Tests are skipped gracefully when the Excel file is not present.
No KG, RAG, or LLM calls — only solve_target_gpa and SCP.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engines.ale.functions.solve_target_gpa import solve_target_gpa
from engines.ale.schemas import (
    GradingScaleRules,
    GraduationRequirementRules,
    PercentageRange,
    PlannedCourseTarget,
    RetakeRules,
    SolveTargetGPAInput,
)

# ── File-availability guard ────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_EXCEL_PATH = _REPO_ROOT / "data" / "students_anonymous.xlsx"
_EXCEL_MISSING = not _EXCEL_PATH.is_file()

pytestmark = pytest.mark.skipif(
    _EXCEL_MISSING,
    reason=f"Student dataset not found at {_EXCEL_PATH}",
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


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def scp():
    from gateway.student_context_provider import get_context, load_excel
    load_excel(str(_EXCEL_PATH))
    return get_context


# ── Case 1: STU000004 — target already met ────────────────────────────────────
#
# Stability guarantee:
#   CGPA≈3.64 is a historical Fall 2025 snapshot. The static anonymized dataset
#   cannot be modified retroactively. As long as CGPA > 3.0 (margin = 0.64),
#   target=3.0 will always trigger already_met.

def test_real_stu000004_target_already_met(scp):
    ctx = scp("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")

    assert ctx.study_status == "Studying", "Fixture assumption: STU000004 must be Studying"
    assert ctx.cgpa is not None, "STU000004 must have a CGPA"
    assert ctx.cgpa >= 3.0, (
        f"Fixture assumption: STU000004 CGPA must be >= 3.0; got {ctx.cgpa}"
    )

    inp = SolveTargetGPAInput(
        current_cgpa=ctx.cgpa,
        gpa_counted_credits=ctx.cumulative_chs,
        current_quality_points=ctx.cumulative_cps,
        target_cgpa=3.0,
        planned_courses=[
            PlannedCourseTarget(
                course_code="HUM228",
                course_name="Humanities Elective",
                credits=3,
                attempt_type="first_attempt",
                has_cgpa_footprint=False,
            )
        ],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
        graduation_rules=_graduation_rules(),
        completed_regular_semesters=ctx.completed_regular_semesters,
    )
    out = solve_target_gpa(inp)

    assert out.status == "already_met", (
        f"Expected already_met for STU000004 target=3.0 with CGPA={ctx.cgpa}; got {out.status!r}"
    )
    assert out.delta_needed is not None
    assert out.delta_needed <= 0.0


# ── Case 2: STU000004 — target impossible with single small course ─────────────
#
# Stability guarantee:
#   cumulative_chs=16 and cumulative_cps≈58.24 (from CGPA≈3.64 × 16).
#   With target=3.8 and adding only 3cr:
#     max_achievable_cgpa = (cps + 4.0*3) / (16+3) ≈ 70.24/19 ≈ 3.696 < 3.8
#   So required_average > 4.0 → impossible (required_average_exceeds_maximum).
#   This holds for any cgpa < 3.8 (margin = 0.16+) and chs=16.

def test_real_stu000004_single_course_target_impossible(scp):
    ctx = scp("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")

    assert ctx.total_credit_hours_earned == 16, (
        f"Stability: expected 16 PHs; got {ctx.total_credit_hours_earned}"
    )
    if ctx.cgpa is None or ctx.cumulative_chs is None or ctx.cumulative_cps is None:
        pytest.skip("STU000004 missing GPA snapshot fields")

    assert ctx.cgpa < 3.8, (
        f"Fixture assumption: STU000004 CGPA must be < 3.8; got {ctx.cgpa}"
    )

    # Verify the impossibility analytically: max new CGPA with 1 course at A+(4.0)
    max_new_cgpa = (ctx.cumulative_cps + 4.0 * 3) / (ctx.cumulative_chs + 3)
    assert max_new_cgpa < 3.8, (
        f"Fixture assumption: adding 1 A+ course (3cr) to STU000004 must not reach 3.8; "
        f"got max={max_new_cgpa:.4f}"
    )

    inp = SolveTargetGPAInput(
        current_cgpa=ctx.cgpa,
        gpa_counted_credits=ctx.cumulative_chs,
        current_quality_points=ctx.cumulative_cps,
        target_cgpa=3.8,
        planned_courses=[
            PlannedCourseTarget(
                course_code="HUM228",
                course_name="Humanities Elective",
                credits=3,
                attempt_type="first_attempt",
                has_cgpa_footprint=False,
            )
        ],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
        graduation_rules=_graduation_rules(),
        completed_regular_semesters=ctx.completed_regular_semesters,
    )
    out = solve_target_gpa(inp)

    assert out.status == "impossible", (
        f"Expected impossible for STU000004 target=3.8 single course; got {out.status!r}"
    )
    assert "required_average_exceeds_maximum" in out.reason_codes
    assert out.maximum_reachable_cgpa is not None
    assert abs(out.maximum_reachable_cgpa - round(max_new_cgpa, 2)) < 0.01


# ── Case 3: STU000004 — formula verification for a solvable case ──────────────
#
# Stability guarantee:
#   With chs=16 and cgpa≈3.64, adding 4cr course at target=3.7:
#     req_new_pts = 3.7*(16+4) - cps = 74 - cps
#     req_avg = (74 - cps) / 4
#   cps ≈ 58.24 → req_avg ≈ (74-58.24)/4 = 3.94 ≤ 4.0 → solvable.
#   For this to be solvable: cps ≥ 74-16=58 (cgpa ≥ 3.625). Safe margin for cgpa≈3.64.
#   Test is skipped dynamically if the scenario is not solvable (defensive guard).

def test_real_stu000004_formula_verification_solvable(scp):
    ctx = scp("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")

    if ctx.cgpa is None or ctx.cumulative_chs is None or ctx.cumulative_cps is None:
        pytest.skip("STU000004 missing GPA snapshot fields")

    target = 3.7
    course_credits = 4

    if ctx.cgpa >= target:
        pytest.skip(f"STU000004 CGPA={ctx.cgpa} >= target={target} — already_met scenario")

    expected_final_denom = ctx.cumulative_chs + course_credits
    expected_req_new_pts = target * expected_final_denom - ctx.cumulative_cps
    expected_req_avg = expected_req_new_pts / course_credits

    if expected_req_avg > 4.0:
        pytest.skip(
            f"Computed required_average={expected_req_avg:.3f} > 4.0 — not a solvable case; "
            f"cps={ctx.cumulative_cps}"
        )
    if expected_req_new_pts <= 0:
        pytest.skip(f"required_new_points={expected_req_new_pts:.3f} ≤ 0 — already_met scenario")

    inp = SolveTargetGPAInput(
        current_cgpa=ctx.cgpa,
        gpa_counted_credits=ctx.cumulative_chs,
        current_quality_points=ctx.cumulative_cps,
        target_cgpa=target,
        planned_courses=[
            PlannedCourseTarget(
                course_code="HUM228",
                course_name="Humanities Elective (4cr)",
                credits=course_credits,
                attempt_type="first_attempt",
                has_cgpa_footprint=False,
            )
        ],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
        graduation_rules=_graduation_rules(),
        completed_regular_semesters=ctx.cumulative_chs,
    )
    out = solve_target_gpa(inp)

    assert out.status == "solvable", (
        f"Expected solvable; got {out.status!r} (required_avg={expected_req_avg:.3f})"
    )
    assert out.required_average_grade_points is not None
    assert abs(out.required_average_grade_points - expected_req_avg) < 0.001, (
        f"Formula mismatch: expected={expected_req_avg:.4f}, got={out.required_average_grade_points}"
    )


# ── Case 4: STU000009 — replacement policy evidence ───────────────────────────
#
# Stability guarantee:
#   cumulative_chs=43 is the registrar-computed GPA denominator using replacement policy.
#   If additive (each graded attempt counted separately), at least one 4cr retake would
#   push the denominator to 47 or higher.
#   This assertion is shared with test_simulate_gpa_forward_real_records and validates
#   the replacement assumption embedded in solve_target_gpa's Phase 3 math.

def test_real_stu000009_replacement_evidence(scp):
    ctx = scp("STU000009")
    if ctx is None:
        pytest.skip("STU000009 not found in dataset")

    if ctx.cumulative_chs is None:
        pytest.skip("STU000009 missing cumulative_chs field")

    assert ctx.cumulative_chs == 43, (
        f"Replacement policy evidence: expected cumulative_chs=43 (additive would give ≥47); "
        f"got {ctx.cumulative_chs}"
    )
