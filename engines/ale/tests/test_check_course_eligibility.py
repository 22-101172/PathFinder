"""
Focused unit tests for check_course_eligibility.

Tests call the ALE function directly — no adapter, no KG, no RAG, no LLM.
All rule values are inline fixtures matching the QU handbook constants.

Coverage:
  1.  eligible first attempt, all prerequisites met
  2.  not eligible — missing prerequisite
  3.  not eligible — credit threshold not met
  4.  eligible — credit threshold exactly met
  5.  target course in progress  → status="in_progress"
  6.  target course already completed (non-improve) → status="already_completed"
  7.  improve retake eligible below CGPA threshold (unlimited)
  8.  improve retake eligible with remaining slots (CGPA >= threshold)
  9.  improve retake cap exceeded → reason code "improve_retake_cap_exceeded"
  10. failed retake not blocked by retake_count; grade cap warning present
  11. inconsistent improve_retake for non-completed course → cannot_compute
  12. inconsistent failed_retake for completed course → cannot_compute
  13a. invalid attempt_type rejected by schema (Pydantic Literal)
  13b. invalid attempt_type bypassing Pydantic returns structured cannot_compute
"""

import pytest
from pydantic import ValidationError

from engines.ale.functions.check_course_eligibility import check_course_eligibility
from engines.ale.schemas import CheckCourseEligibilityInput, RetakeRules


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _rules() -> RetakeRules:
    return RetakeRules(
        failed_first_retake_grade_cap="B",
        improve_retake_first_attempt_cap=None,
        improve_retake_subsequent_cap="B",
        improve_retake_max_courses_cgpa_above_2=3,
        improve_retake_unlimited_below_cgpa=2.0,
    )


def _inp(**overrides) -> CheckCourseEligibilityInput:
    """Return a minimal valid first_attempt input; callers override specific fields."""
    defaults = dict(
        target_course_code="CS201",
        target_course_prerequisites=[],
        target_course_credit_threshold=None,
        completed_courses=[],
        in_progress_courses=[],
        retake_count={},
        total_improve_retakes_used=0,
        cumulative_passed_hours=0,
        current_cgpa=2.5,
        attempt_type="first_attempt",
        retake_rules=_rules(),
    )
    defaults.update(overrides)
    return CheckCourseEligibilityInput(**defaults)


# ── 1. Eligible first attempt — all prerequisites met ─────────────────────────

def test_eligible_first_attempt_all_prereqs_met():
    inp = _inp(
        target_course_prerequisites=["MA111", "CS111"],
        completed_courses=["MA111", "CS111"],
    )
    out = check_course_eligibility(inp)
    assert out.status == "eligible"
    assert out.missing_prerequisites == []
    assert set(out.completed_prerequisites) == {"MA111", "CS111"}
    assert out.reason_codes == []


# ── 2. Not eligible — missing prerequisite ────────────────────────────────────

def test_not_eligible_missing_prerequisite():
    inp = _inp(
        target_course_prerequisites=["CS111"],
        completed_courses=[],
    )
    out = check_course_eligibility(inp)
    assert out.status == "not_eligible"
    assert "missing_prerequisites" in out.reason_codes
    assert "CS111" in out.missing_prerequisites
    assert out.completed_prerequisites == []


# ── 3. Not eligible — credit threshold not met ────────────────────────────────

def test_not_eligible_credit_threshold_not_met():
    inp = _inp(
        target_course_credit_threshold=60,
        cumulative_passed_hours=45,
    )
    out = check_course_eligibility(inp)
    assert out.status == "not_eligible"
    assert "credit_threshold_not_met" in out.reason_codes
    assert out.credit_threshold_met is False
    assert out.credit_threshold_required == 60


# ── 4. Eligible — credit threshold exactly met ────────────────────────────────

def test_eligible_credit_threshold_exactly_met():
    inp = _inp(
        target_course_credit_threshold=60,
        cumulative_passed_hours=60,
    )
    out = check_course_eligibility(inp)
    assert out.status == "eligible"
    assert out.credit_threshold_met is True


# ── 5. Target course in progress ──────────────────────────────────────────────

def test_target_in_progress_returns_in_progress_status():
    inp = _inp(
        target_course_code="CS201",
        in_progress_courses=["CS201"],
    )
    out = check_course_eligibility(inp)
    assert out.status == "in_progress"
    assert "course_currently_in_progress" in out.reason_codes


# ── 6. Target already completed (non-improve attempt) ────────────────────────

def test_target_already_completed_returns_already_completed():
    inp = _inp(
        target_course_code="CS111",
        completed_courses=["CS111"],
        attempt_type="first_attempt",
    )
    out = check_course_eligibility(inp)
    assert out.status == "already_completed"


# ── 7. Improve retake — below CGPA threshold (unlimited) ─────────────────────

def test_improve_retake_eligible_below_cgpa_threshold():
    # CGPA 1.5 < 2.0 → unlimited improve retakes regardless of used count
    inp = _inp(
        target_course_code="CS111",
        completed_courses=["CS111"],
        attempt_type="improve_retake",
        current_cgpa=1.5,
        total_improve_retakes_used=5,  # above normal cap — must not block
    )
    out = check_course_eligibility(inp)
    assert out.status == "eligible"
    assert any("no improve retake" in w.lower() for w in out.warnings)


# ── 8. Improve retake — eligible with remaining slots (CGPA ≥ threshold) ─────

def test_improve_retake_eligible_with_remaining_slots():
    # CGPA 2.5 ≥ 2.0, 2 of 3 slots used → 1 remaining
    inp = _inp(
        target_course_code="CS111",
        completed_courses=["CS111"],
        attempt_type="improve_retake",
        current_cgpa=2.5,
        total_improve_retakes_used=2,
    )
    out = check_course_eligibility(inp)
    assert out.status == "eligible"
    assert any("remaining" in w.lower() for w in out.warnings)


# ── 9. Improve retake cap exceeded ────────────────────────────────────────────

def test_improve_retake_cap_exceeded_returns_correct_status_and_reason_code():
    # CGPA 2.5 ≥ 2.0, 3 of 3 slots used → cap exactly met → exceeded
    inp = _inp(
        target_course_code="CS111",
        completed_courses=["CS111"],
        attempt_type="improve_retake",
        current_cgpa=2.5,
        total_improve_retakes_used=3,
    )
    out = check_course_eligibility(inp)
    assert out.status == "retake_cap_exceeded"
    assert "improve_retake_cap_exceeded" in out.reason_codes
    assert out.max_retake_cap == 3


# ── 10. Failed retake — not blocked by retake_count ──────────────────────────

def test_failed_retake_not_blocked_by_retake_count():
    # retake_count=5 for the course — handbook has NO failed-retake attempt cap.
    # Function must return eligible and only warn about the grade cap.
    inp = _inp(
        target_course_code="CS111",
        completed_courses=[],
        in_progress_courses=[],
        attempt_type="failed_retake",
        retake_count={"CS111": 5},
    )
    out = check_course_eligibility(inp)
    assert out.status == "eligible", (
        f"Failed retake must not be blocked by retake_count; got status={out.status!r}"
    )
    assert any("grade cap" in w.lower() for w in out.warnings), (
        "Expected grade cap warning for failed_retake attempt"
    )


# ── 11. Inconsistent improve_retake — course not completed ───────────────────

def test_inconsistent_improve_retake_for_non_completed_course():
    inp = _inp(
        target_course_code="CS200",
        completed_courses=[],  # CS200 never completed
        attempt_type="improve_retake",
    )
    out = check_course_eligibility(inp)
    assert out.status == "cannot_compute"
    assert "inconsistent_attempt_type_course_not_completed" in out.reason_codes


# ── 12. Inconsistent failed_retake — course already passed ───────────────────

def test_inconsistent_failed_retake_for_completed_course():
    inp = _inp(
        target_course_code="CS111",
        completed_courses=["CS111"],
        attempt_type="failed_retake",
    )
    out = check_course_eligibility(inp)
    assert out.status == "cannot_compute"
    assert "inconsistent_attempt_type_course_already_passed" in out.reason_codes


# ── 13a. Invalid attempt_type rejected by Pydantic Literal ───────────────────

def test_invalid_attempt_type_rejected_by_pydantic_schema():
    with pytest.raises(ValidationError):
        CheckCourseEligibilityInput(
            target_course_code="CS201",
            target_course_prerequisites=[],
            target_course_credit_threshold=None,
            completed_courses=[],
            in_progress_courses=[],
            retake_count={},
            total_improve_retakes_used=0,
            cumulative_passed_hours=0,
            current_cgpa=2.5,
            attempt_type="nonsense_type",
            retake_rules=_rules(),
        )


# ── 13b. Invalid attempt_type bypassing Pydantic → structured cannot_compute ──

def test_invalid_attempt_type_bypassing_pydantic_returns_cannot_compute():
    # model_construct() skips Pydantic validation; the function guard must catch it.
    inp = CheckCourseEligibilityInput.model_construct(
        target_course_code="CS201",
        target_course_prerequisites=[],
        target_course_credit_threshold=None,
        completed_courses=[],
        in_progress_courses=[],
        retake_count={},
        total_improve_retakes_used=0,
        cumulative_passed_hours=0,
        current_cgpa=2.5,
        attempt_type="nonsense_type",
        retake_rules=_rules(),
    )
    out = check_course_eligibility(inp)
    assert out.status == "cannot_compute"
    assert "invalid_attempt_type" in out.reason_codes
