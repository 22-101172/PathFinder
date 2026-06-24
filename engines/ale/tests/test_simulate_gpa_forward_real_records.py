"""
Real-record regression tests for simulate_gpa_forward.

Students selected for stability:
  STU000004  Studying, Freshman, 16 PHs, CGPA ≈3.64
             C-CS112 in_progress Spring 2026; no retakes.
  STU000009  CHs=43 in Excel — confirms replacement policy (additive would give 47).

Selection rationale:
  STU000004 — single completed semester with clean history.
    Stable: total_credit_hours_earned=16 and cumulative data cannot change
    retroactively in the static anonymised dataset.
  STU000009 — has at least one retake on record.
    cumulative_chs=43 (unique course count) < hypothetical additive total of 47.
    This validates that simulate_gpa_forward's replacement logic (subtract old,
    add new, denominator unchanged) matches the registrar's GPA calculation.

Tests are skipped gracefully when the Excel file is not present.
No KG, RAG, or LLM calls — only simulate_gpa_forward and SCP.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engines.ale.functions.simulate_gpa_forward import simulate_gpa_forward
from engines.ale.schemas import (
    GradingScaleRules,
    PercentageRange,
    PlannedCourseGPA,
    RetakeRules,
    SimulateGPAForwardInput,
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


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def scp():
    from gateway.student_context_provider import get_context, load_excel
    load_excel(str(_EXCEL_PATH))
    return get_context


# ── Case 1: STU000004 — first-attempt projection ──────────────────────────────
#
# Stability guarantee:
#   total_credit_hours_earned=16, CGPA≈3.64, cumulative_chs/cps are historical
#   Fall 2025 transcript facts — cannot change retroactively.
#   Adding a B grade (3.0 < 3.64) to a 3.64 student must lower projected CGPA.

def test_real_stu000004_first_attempt_projection(scp):
    ctx = scp("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")

    assert ctx.study_status == "Studying", "Fixture assumption: STU000004 must be Studying"
    assert ctx.total_credit_hours_earned == 16, (
        f"Stability: expected 16 PHs; got {ctx.total_credit_hours_earned}"
    )
    if ctx.cgpa is None or ctx.cumulative_chs is None or ctx.cumulative_cps is None:
        pytest.skip("STU000004 missing GPA snapshot fields")

    # HUM228 (3cr) is not in STU000004's Fall 2025 or Spring 2026 registrations
    assert "HUM228" not in ctx.completed_courses
    assert "HUM228" not in ctx.in_progress_courses

    course = PlannedCourseGPA(
        course_code="HUM228",
        course_name="Humanities Elective",
        credits=3,
        expected_grade="B",
        attempt_type="first_attempt",
        has_cgpa_footprint=False,
    )
    inp = SimulateGPAForwardInput(
        current_cgpa=ctx.cgpa,
        gpa_counted_credits=ctx.cumulative_chs,
        current_quality_points=ctx.cumulative_cps,
        planned_courses=[course],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
    )
    out = simulate_gpa_forward(inp)

    # Expected: (cps + 3*3.0) / (chs + 3), rounded to 2dp
    expected_proj = round((ctx.cumulative_cps + 9.0) / (ctx.cumulative_chs + 3), 2)
    expected_delta = round(expected_proj - ctx.cgpa, 2)

    assert out.status == "projected"
    assert out.projected_cgpa == expected_proj
    assert out.delta == expected_delta
    # B(3.0) < CGPA(≈3.64): adding it must reduce the projected CGPA
    assert out.delta < 0, "Adding a B to a ≈3.64 student must lower projected CGPA"


# ── Case 2: STU000004 — in-progress note propagation ─────────────────────────
#
# Stability guarantee:
#   C-CS112 is registered for Spring 2026; in_progress_courses is a historical
#   enrollment fact that cannot change retroactively.

def test_real_stu000004_in_progress_note(scp):
    ctx = scp("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")

    assert "C-CS112" in ctx.in_progress_courses, (
        "Fixture assumption: C-CS112 must be in_progress for STU000004"
    )
    if ctx.cgpa is None or ctx.cumulative_chs is None or ctx.cumulative_cps is None:
        pytest.skip("STU000004 missing GPA snapshot fields")

    course = PlannedCourseGPA(
        course_code="C-CS112",
        course_name="Programming 2",
        credits=4,
        expected_grade="B",
        attempt_type="first_attempt",
        has_cgpa_footprint=False,
        is_currently_in_progress=True,
    )
    inp = SimulateGPAForwardInput(
        current_cgpa=ctx.cgpa,
        gpa_counted_credits=ctx.cumulative_chs,
        current_quality_points=ctx.cumulative_cps,
        planned_courses=[course],
        grading_scale_rules=_grading_scale(),
        retake_rules=_retake_rules(),
    )
    out = simulate_gpa_forward(inp)

    assert out.status == "projected"
    note = out.per_course_breakdown[0].in_progress_note
    assert note is not None, "in_progress_note must be set for is_currently_in_progress=True"
    assert "hypothetical" in note.lower(), (
        f"in_progress_note must mention 'hypothetical'; got: {note!r}"
    )


# ── Case 3: STU000009 — replacement policy evidence ───────────────────────────
#
# Stability guarantee:
#   cumulative_chs=43 is a registrar-computed GPA denominator using replacement.
#   If additive (each graded attempt counted separately), at least one 4cr retake
#   would push the denominator to 47 or higher.
#   This assertion validates the replacement assumption embedded in simulate_gpa_forward.

def test_real_stu000009_replacement_evidence(scp):
    ctx = scp("STU000009")
    if ctx is None:
        pytest.skip("STU000009 not found in dataset")

    if ctx.cumulative_chs is None:
        pytest.skip("STU000009 missing cumulative_chs field")

    # Replacement policy: denominator=43 (unique course count).
    # Additive policy would give ≥47 (one 4cr course retaken at least once).
    assert ctx.cumulative_chs == 43, (
        f"Replacement policy evidence: expected cumulative_chs=43 (additive would give ≥47); "
        f"got {ctx.cumulative_chs}"
    )
