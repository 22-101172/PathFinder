"""
Real-record regression tests for run_graduation_audit.

Students selected for stability:
  STU000004  Studying, Freshman, 1 semester, 16 PHs, CGPA 3.64 — clear credit gap
  STU000026  Graduated, 133 PHs, CGPA 3.98, Military Done
  STU000041  Suspended — not_auditable status

Selection rationale:
  STU000004 — first-semester Freshman with 16/133 PHs.
    Credit gap (16 vs 133) and min-semester gap (1 vs 6) will never be erased
    retroactively in the static anonymized dataset.
  STU000026 — already graduated (study_status="Graduated") in the Excel.
    Status cannot change retroactively.
  STU000041 — study_status="Suspended".
    Not-auditable short-circuit requires no additional ALE data.

Rule bundles are provided inline (no live RAG call).
Course history is passed as empty list for graduation tests; the function's
honors branch then operates on zero entries (vacuously passes CGPA check,
honors_status=cannot_fully_verify). This is intentional — we are testing
graduation-level status and reason codes, not honors computation.

Tests are skipped gracefully when the Excel file is not present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engines.ale.functions.run_graduation_audit import run_graduation_audit
from engines.ale.ale_schemas import (
    AcademicWarningRules,
    GraduationRequirementRules,
    HonorsRules,
    RunGraduationAuditInput,
)

# ── File-availability guard ────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_EXCEL_PATH = _REPO_ROOT / "data" / "students_anonymous.xlsx"
_EXCEL_MISSING = not _EXCEL_PATH.is_file()

pytestmark = pytest.mark.skipif(
    _EXCEL_MISSING,
    reason=f"Student dataset not found at {_EXCEL_PATH}",
)

# ── Inline rule bundles ────────────────────────────────────────────────────────

def _graduation_rules() -> GraduationRequirementRules:
    return GraduationRequirementRules(
        total_credits_required=133,
        minimum_cgpa=2.0,
        minimum_regular_semesters=6,
        maximum_regular_semesters=16,
        must_pass_zero_credit_courses=True,
        military_training_required_for_males=True,
    )


def _warning_rules() -> AcademicWarningRules:
    return AcademicWarningRules(
        cgpa_warning_threshold=2.0,
        max_consecutive_warnings=4,
        max_total_warnings=6,
        warning_exempt_first_semester=True,
        dismissal_extension_credits_percentage=0.80,
        dismissal_extension_extra_semesters=2,
        dismissal_extension_extra_summer_semesters=1,
    )


def _honors_rules() -> HonorsRules:
    return HonorsRules(
        minimum_cgpa_throughout=3.0,
        minimum_semesters=6,
        maximum_semesters=8,
        no_f_grade_allowed=True,
        no_disciplinary_penalties=True,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def scp():
    from gateway.student_context_provider import get_context, load_excel
    load_excel(str(_EXCEL_PATH))
    return get_context


def _build(ctx, **overrides) -> RunGraduationAuditInput:
    """Build RunGraduationAuditInput from a real StudentContext."""
    defaults = dict(
        study_status=ctx.study_status,
        completed_courses=ctx.completed_courses,
        failed_courses=ctx.failed_courses,
        in_progress_courses=ctx.in_progress_courses,
        current_cgpa=ctx.cgpa if ctx.cgpa is not None else 0.0,
        cumulative_passed_hours=ctx.total_credit_hours_earned,
        consecutive_warnings=ctx.consecutive_warnings,
        total_warnings=ctx.total_warnings,
        military_status=ctx.military_status,
        completed_regular_semesters=ctx.completed_regular_semesters,
        zero_credit_courses_passed=bool(ctx.zero_credit_courses_passed),
        course_history=[],   # empty: honors vacuously computed
        graduation_rules=_graduation_rules(),
        warning_rules=_warning_rules(),
        honors_rules=_honors_rules(),
    )
    defaults.update(overrides)
    return RunGraduationAuditInput(**defaults)


# ── Case 1: STU000004 — Studying freshman, clear credit gap ──────────────────
#
# Stability guarantee:
#   total_credit_hours_earned=16 is a historical Fall 2025 transcript fact.
#   completed_regular_semesters=1 is likewise historical.
#   Neither can change retroactively in the static anonymised Excel file.
#   The credit gap (16 < 133) is so large (117 PHs short) that no conceivable
#   data patch would make STU000004 eligible retroactively.

def test_real_stu000004_studying_not_eligible_credit_gap(scp):
    ctx = scp("STU000004")
    if ctx is None:
        pytest.skip("STU000004 not found in dataset")

    assert ctx.study_status == "Studying", (
        "Fixture assumption violated: STU000004 must be Studying"
    )
    assert ctx.total_credit_hours_earned < 133, (
        f"Fixture assumption violated: STU000004 PHs ({ctx.total_credit_hours_earned}) must be below 133"
    )
    assert ctx.completed_regular_semesters < 6, (
        f"Fixture assumption violated: STU000004 semesters ({ctx.completed_regular_semesters}) must be below 6"
    )

    inp = _build(ctx)
    out = run_graduation_audit(inp)

    assert out.status == "not_eligible", (
        f"Expected not_eligible for STU000004; got {out.status!r}"
    )
    assert "credits_incomplete" in out.reason_codes, (
        f"Expected credits_incomplete in reason_codes; got {out.reason_codes}"
    )
    assert "minimum_regular_semesters_not_met" in out.reason_codes, (
        f"Expected minimum_regular_semesters_not_met in reason_codes; got {out.reason_codes}"
    )

    credits_check = next(c for c in out.checks if c.name == "credits_check")
    assert credits_check.passed is False
    assert credits_check.actual_value == 16
    assert credits_check.required_value == 133


# ── Case 2: STU000026 — Graduated ────────────────────────────────────────────
#
# Stability guarantee:
#   study_status="Graduated" is a terminal state in the registrar's system.
#   The function returns already_graduated without running any graduation checks.

def test_real_stu000026_already_graduated(scp):
    ctx = scp("STU000026")
    if ctx is None:
        pytest.skip("STU000026 not found in dataset")

    assert ctx.study_status == "Graduated", (
        f"Fixture assumption violated: STU000026 study_status must be Graduated; got {ctx.study_status!r}"
    )

    inp = _build(ctx)
    out = run_graduation_audit(inp)

    assert out.status == "already_graduated", (
        f"Expected already_graduated for STU000026; got {out.status!r}"
    )
    assert out.checks == [], "already_graduated must not populate graduation checks"
    assert out.honors_status is not None, "Honors must be computed for graduated students"
    assert len(out.honors_checks) == 4


# ── Case 3: STU000041 — Suspended ────────────────────────────────────────────
#
# Stability guarantee:
#   study_status="Suspended" is a terminal administrative status.
#   The function short-circuits to not_auditable immediately.

def test_real_stu000041_suspended_not_auditable(scp):
    ctx = scp("STU000041")
    if ctx is None:
        pytest.skip("STU000041 not found in dataset")

    assert ctx.study_status == "Suspended", (
        f"Fixture assumption violated: STU000041 study_status must be Suspended; got {ctx.study_status!r}"
    )

    inp = _build(ctx)
    out = run_graduation_audit(inp)

    assert out.status == "not_auditable", (
        f"Expected not_auditable for STU000041; got {out.status!r}"
    )
    assert "study_status_suspended" in out.reason_codes
    assert out.honors_status is None
    assert out.honors_checks == []
