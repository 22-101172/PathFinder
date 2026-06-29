"""
Real-record regression tests for check_course_eligibility.

Student: STU000004  (Freshman, General Program, CGPA 3.64, PHs=16)
  Completed  Fall 2025:   C-CS111, C-MA111, C-PH111, HUM111, HUM126
  In-progress Spring 2026: C-CS112, C-MA112, C-PH112, C-PH113, HUM224
  No retakes, no failures.

Selection rationale
  First-semester Freshman with a clean single-attempt record.  All five
  core eligibility outcomes can be exercised against one student without
  any ambiguity introduced by retakes, failures, or overlapping states.
  The Fall 2025 transcript is closed and cannot change retroactively.

KG facts used — sourced from engines/kg/data/prerequisites.csv (no Neo4j):
  C-PH112  → prereq: C-PH111            row 5   (COURSE)
  C-PH111  → no prerequisites            (no row)
  C-CS219  → prereq: C-CS112            row 27  (COURSE)
  C-CS215  → prereqs: C-CS111, C-MA111  rows 12–13 (COURSE x2)
  C-CS351  → credit threshold: 59 PHs   row 20  (CREDIT_THRESHOLD;
                                                   value parsed from
                                                   "Passing 59 Credit Hours")

Student context is loaded via the real SCP (student_context_provider) so that
SCP parsing logic is implicitly tested at the same time.
Tests are skipped gracefully if the Excel file is not present.
Tests call check_course_eligibility() directly — no adapter, no KG, no LLM.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from engines.ale.functions.check_course_eligibility import check_course_eligibility
from engines.ale.ale_schemas import CheckCourseEligibilityInput, RetakeRules

# ── File-availability guard ────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_EXCEL_PATH = _REPO_ROOT / "data" / "students_anonymous.xlsx"
_EXCEL_MISSING = not _EXCEL_PATH.is_file()

pytestmark = pytest.mark.skipif(
    _EXCEL_MISSING,
    reason=f"Student dataset not found at {_EXCEL_PATH}",
)

# ── KG course facts (locked catalogue — no Neo4j) ─────────────────────────────

# C-PH112 direct prerequisites (from prerequisites.csv row 5)
_C_PH112_PREREQS: list[str] = ["C-PH111"]
_C_PH112_CREDIT_THRESHOLD = None

# C-PH111 has no prerequisites (no row in prerequisites.csv)
_C_PH111_PREREQS: list[str] = []
_C_PH111_CREDIT_THRESHOLD = None

# C-CS219 direct prerequisites (from prerequisites.csv row 27)
_C_CS219_PREREQS: list[str] = ["C-CS112"]
_C_CS219_CREDIT_THRESHOLD = None

# C-CS215 direct prerequisites (from prerequisites.csv rows 12–13)
_C_CS215_PREREQS: list[str] = ["C-CS111", "C-MA111"]
_C_CS215_CREDIT_THRESHOLD = None

# C-CS351 credit threshold (from prerequisites.csv row 20;
# "Passing 59 Credit Hours" → parsed integer 59)
_C_CS351_PREREQS: list[str] = []
_C_CS351_CREDIT_THRESHOLD: int = 59

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def stu000004():
    """
    Load STU000004 through the real SCP code.

    Student ID: STU000004
    Loads once per test module to avoid repeated Excel I/O.
    """
    from gateway.student_context_provider import get_context, load_excel

    load_excel(str(_EXCEL_PATH))
    ctx = get_context("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")
    return ctx


@pytest.fixture(scope="module")
def retake_rules():
    return RetakeRules(
        failed_first_retake_grade_cap="B",
        improve_retake_first_attempt_cap=None,
        improve_retake_subsequent_cap="B",
        improve_retake_max_courses_cgpa_above_2=3,
        improve_retake_unlimited_below_cgpa=2.0,
    )


def _build(ctx, retake_rules, target_code, prereqs, credit_threshold, attempt_type):
    """Construct CheckCourseEligibilityInput from a real StudentContext."""
    return CheckCourseEligibilityInput(
        target_course_code=target_code,
        target_course_prerequisites=prereqs,
        target_course_credit_threshold=credit_threshold,
        completed_courses=ctx.completed_courses,
        in_progress_courses=ctx.in_progress_courses,
        retake_count=ctx.retake_count,
        total_improve_retakes_used=ctx.total_improve_retakes_used,
        cumulative_passed_hours=ctx.total_credit_hours_earned,
        current_cgpa=ctx.cgpa,
        attempt_type=attempt_type,
        retake_rules=retake_rules,
    )


# ── Case 1: C-PH112 currently in progress ─────────────────────────────────────
#
# STU000004 registered for C-PH112 in Spring 2026 (no grade yet).
# SCP maps this to in_progress_courses.
# Orchestrator would derive attempt_type="first_attempt" (course is in
# history but not completed or failed — the last-else branch).
# ALE must short-circuit at Check A and return status="in_progress".

def test_real_c_ph112_in_progress(stu000004, retake_rules):
    ctx = stu000004
    assert "C-PH112" in ctx.in_progress_courses, (
        "Fixture assumption violated: C-PH112 should be in in_progress_courses for STU000004"
    )

    inp = _build(ctx, retake_rules, "C-PH112", _C_PH112_PREREQS, _C_PH112_CREDIT_THRESHOLD,
                 attempt_type="first_attempt")
    out = check_course_eligibility(inp)

    assert out.status == "in_progress", (
        f"Expected status='in_progress' for C-PH112; got '{out.status}'"
    )
    assert "course_currently_in_progress" in out.reason_codes


# ── Case 2: C-PH111 already completed ────────────────────────────────────────
#
# STU000004 passed C-PH111 in Fall 2025 (grade B, Succeeded tag).
# SCP maps this to completed_courses.
# We use attempt_type="first_attempt" to exercise the ALE "already_completed"
# guard (sub-case B2).  The realistic Orchestrator would derive "improve_retake"
# for a completed course; that path is already covered by the synthetic unit
# tests.  This test verifies the ALE correctly identifies the completed state
# from real data, regardless of upstream attempt_type.

def test_real_c_ph111_already_completed(stu000004, retake_rules):
    ctx = stu000004
    assert "C-PH111" in ctx.completed_courses, (
        "Fixture assumption violated: C-PH111 should be in completed_courses for STU000004"
    )

    inp = _build(ctx, retake_rules, "C-PH111", _C_PH111_PREREQS, _C_PH111_CREDIT_THRESHOLD,
                 attempt_type="first_attempt")
    out = check_course_eligibility(inp)

    assert out.status == "already_completed", (
        f"Expected status='already_completed' for C-PH111; got '{out.status}'"
    )


# ── Case 3: C-CS219 not eligible — C-CS112 in progress, not yet completed ────
#
# C-CS219 requires C-CS112 (KG fact).
# STU000004 has C-CS112 in_progress (Spring 2026) but NOT in completed_courses.
# ALE must return not_eligible with missing_prerequisites=["C-CS112"].
# Additionally, since C-CS112 is in in_progress_courses, the function adds a
# per-missing-prerequisite warning ("Currently taking C-CS112 — may be satisfied
# next semester if passed.").
# Orchestrator would derive attempt_type="first_attempt" (C-CS219 never registered).

def test_real_c_cs219_not_eligible_missing_prereq(stu000004, retake_rules):
    ctx = stu000004
    assert "C-CS112" in ctx.in_progress_courses, (
        "Fixture assumption violated: C-CS112 should be in_progress for STU000004"
    )
    assert "C-CS112" not in ctx.completed_courses, (
        "Fixture assumption violated: C-CS112 must NOT be completed yet for STU000004"
    )
    assert "C-CS219" not in ctx.completed_courses, (
        "Fixture assumption violated: C-CS219 must not be completed for STU000004"
    )
    assert "C-CS219" not in ctx.in_progress_courses, (
        "Fixture assumption violated: C-CS219 must not be in-progress for STU000004"
    )

    inp = _build(ctx, retake_rules, "C-CS219", _C_CS219_PREREQS, _C_CS219_CREDIT_THRESHOLD,
                 attempt_type="first_attempt")
    out = check_course_eligibility(inp)

    assert out.status == "not_eligible", (
        f"Expected status='not_eligible' for C-CS219; got '{out.status}'"
    )
    assert "missing_prerequisites" in out.reason_codes
    assert "C-CS112" in out.missing_prerequisites
    # Warning: currently taking the missing prerequisite
    assert any("C-CS112" in w for w in out.warnings), (
        "Expected a warning about currently taking C-CS112; got none"
    )


# ── Case 4: C-CS215 eligible — C-CS111 and C-MA111 both completed ─────────────
#
# C-CS215 requires C-CS111 AND C-MA111 (KG fact, two COURSE rows).
# STU000004 completed both in Fall 2025.
# C-CS215 has never been registered by this student.
# ALE must return eligible with both courses in completed_prerequisites.
# Orchestrator would derive attempt_type="first_attempt".

def test_real_c_cs215_eligible_prereqs_met(stu000004, retake_rules):
    ctx = stu000004
    for prereq in ("C-CS111", "C-MA111"):
        assert prereq in ctx.completed_courses, (
            f"Fixture assumption violated: {prereq} should be completed for STU000004"
        )
    assert "C-CS215" not in ctx.completed_courses, (
        "Fixture assumption violated: C-CS215 must not be completed for STU000004"
    )
    assert "C-CS215" not in ctx.in_progress_courses, (
        "Fixture assumption violated: C-CS215 must not be in-progress for STU000004"
    )

    inp = _build(ctx, retake_rules, "C-CS215", _C_CS215_PREREQS, _C_CS215_CREDIT_THRESHOLD,
                 attempt_type="first_attempt")
    out = check_course_eligibility(inp)

    assert out.status == "eligible", (
        f"Expected status='eligible' for C-CS215; got '{out.status}'"
    )
    assert out.missing_prerequisites == []
    assert set(out.completed_prerequisites) == {"C-CS111", "C-MA111"}


# ── Case 5: C-CS351 not eligible — credit threshold 59 PHs not met ───────────
#
# C-CS351 requires 59 passed credit hours (KG CREDIT_THRESHOLD fact).
# STU000004 has cumulative_passed_hours = 16 (one semester done).
# ALE must return not_eligible with credit_threshold_not_met.
# No course prerequisites for C-CS351.

def test_real_c_cs351_credit_threshold_not_met(stu000004, retake_rules):
    ctx = stu000004
    assert ctx.total_credit_hours_earned < _C_CS351_CREDIT_THRESHOLD, (
        f"Fixture assumption violated: student PHs ({ctx.total_credit_hours_earned}) "
        f"must be below threshold ({_C_CS351_CREDIT_THRESHOLD})"
    )
    assert "C-CS351" not in ctx.completed_courses
    assert "C-CS351" not in ctx.in_progress_courses

    inp = _build(ctx, retake_rules, "C-CS351", _C_CS351_PREREQS, _C_CS351_CREDIT_THRESHOLD,
                 attempt_type="first_attempt")
    out = check_course_eligibility(inp)

    assert out.status == "not_eligible", (
        f"Expected status='not_eligible' for C-CS351; got '{out.status}'"
    )
    assert "credit_threshold_not_met" in out.reason_codes
    assert out.credit_threshold_met is False
    assert out.credit_threshold_required == _C_CS351_CREDIT_THRESHOLD
