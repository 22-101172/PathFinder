"""
Focused synthetic unit tests for run_graduation_audit.

Tests call the ALE function directly — no adapter, no KG, no RAG, no LLM.
All rule values are inline fixtures matching QU handbook constants.

Coverage:
  1.  eligible to graduate — all checks pass (male, Done)
  2.  eligible to graduate — female (military_status=None, skipped)
  3.  not eligible — credit gap
  4.  not eligible — CGPA below 2.0
  5.  not eligible — fewer than 6 regular semesters
  6.  maximum regular semesters exceeded — appeal eligible (>= 80% credits)
  7.  maximum regular semesters exceeded — no appeal (< 80% credits)
  8.  warning dismissal — appeal eligible (consecutive warnings = 4)
  9.  warning dismissal — no appeal (total warnings = 6)
  10. military required but not completed
  11. military skipped when military_status is None (female)
  12. military skipped when military_training_required_for_males=False
  13. zero-credit blocker when must_pass_zero_credit_courses=True
  14. zero-credit skipped when must_pass_zero_credit_courses=False
  15. already_graduated status — honors computed
  16. not_auditable — Transferred Out
  17. not_auditable — Suspended
  18. not_auditable — Frozen
  19. honors passes when all criteria satisfied
  20. honors fails when student has any historical failed course attempt
"""

import pytest

from engines.ale.functions.run_graduation_audit import run_graduation_audit
from engines.ale.schemas import (
    AcademicWarningRules,
    CourseHistoryEntry,
    GraduationRequirementRules,
    HonorsRules,
    RunGraduationAuditInput,
)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _graduation_rules(**overrides) -> GraduationRequirementRules:
    defaults = dict(
        total_credits_required=133,
        minimum_cgpa=2.0,
        minimum_regular_semesters=6,
        maximum_regular_semesters=16,
        must_pass_zero_credit_courses=True,
        military_training_required_for_males=True,
    )
    defaults.update(overrides)
    return GraduationRequirementRules(**defaults)


def _warning_rules(**overrides) -> AcademicWarningRules:
    defaults = dict(
        cgpa_warning_threshold=2.0,
        max_consecutive_warnings=4,
        max_total_warnings=6,
        warning_exempt_first_semester=True,
        dismissal_extension_credits_percentage=0.80,
        dismissal_extension_extra_semesters=2,
        dismissal_extension_extra_summer_semesters=1,
    )
    defaults.update(overrides)
    return AcademicWarningRules(**defaults)


def _honors_rules(**overrides) -> HonorsRules:
    defaults = dict(
        minimum_cgpa_throughout=3.0,
        minimum_semesters=6,
        maximum_semesters=8,
        no_f_grade_allowed=True,
        no_disciplinary_penalties=True,
    )
    defaults.update(overrides)
    return HonorsRules(**defaults)


def _inp(**overrides) -> RunGraduationAuditInput:
    """Return a default passing (eligible) Studying student; callers override specifics."""
    defaults = dict(
        study_status="Studying",
        completed_courses=[],
        failed_courses=[],
        in_progress_courses=[],
        current_cgpa=3.5,
        cumulative_passed_hours=133,
        consecutive_warnings=0,
        total_warnings=0,
        military_status="Done",
        completed_regular_semesters=8,
        zero_credit_courses_passed=True,
        course_history=[],
        graduation_rules=_graduation_rules(),
        warning_rules=_warning_rules(),
        honors_rules=_honors_rules(),
    )
    defaults.update(overrides)
    return RunGraduationAuditInput(**defaults)


def _entry(code: str, semester: str, gp: float | None, credits: int, status: str = "passed") -> CourseHistoryEntry:
    return CourseHistoryEntry(
        course_code=code,
        semester=semester,
        semester_type="regular",
        grade_points=gp,
        credits=credits,
        status=status,
    )


# ── 1. Eligible — all checks pass (male, Done) ────────────────────────────────

def test_eligible_all_checks_pass_male():
    inp = _inp()
    out = run_graduation_audit(inp)

    assert out.status == "eligible", f"Expected eligible; got {out.status!r}"
    assert out.reason_codes == []

    check_names = {c.name for c in out.checks}
    assert "warning_dismissal_check" in check_names
    assert "maximum_semesters_check" in check_names
    assert "military_check" in check_names
    assert "cgpa_check" in check_names
    assert "credits_check" in check_names
    assert "semesters_check" in check_names
    assert "zero_credit_check" in check_names
    assert all(c.passed for c in out.checks)


# ── 2. Eligible — female (military_status=None, check omitted) ────────────────

def test_eligible_female_military_check_omitted():
    inp = _inp(military_status=None)
    out = run_graduation_audit(inp)

    assert out.status == "eligible"
    check_names = [c.name for c in out.checks]
    assert "military_check" not in check_names


# ── 3. Not eligible — credit gap ─────────────────────────────────────────────

def test_not_eligible_credit_gap():
    inp = _inp(cumulative_passed_hours=100)
    out = run_graduation_audit(inp)

    assert out.status == "not_eligible"
    assert "credits_incomplete" in out.reason_codes

    credits_check = next(c for c in out.checks if c.name == "credits_check")
    assert credits_check.passed is False
    assert credits_check.actual_value == 100
    assert credits_check.required_value == 133


# ── 4. Not eligible — CGPA below 2.0 ─────────────────────────────────────────

def test_not_eligible_cgpa_below_minimum():
    inp = _inp(current_cgpa=1.8)
    out = run_graduation_audit(inp)

    assert out.status == "not_eligible"
    assert "cgpa_below_minimum" in out.reason_codes

    cgpa_check = next(c for c in out.checks if c.name == "cgpa_check")
    assert cgpa_check.passed is False


# ── 5. Not eligible — fewer than 6 regular semesters ────────────────────────

def test_not_eligible_fewer_than_minimum_semesters():
    inp = _inp(completed_regular_semesters=5)
    out = run_graduation_audit(inp)

    assert out.status == "not_eligible"
    assert "minimum_regular_semesters_not_met" in out.reason_codes

    sem_check = next(c for c in out.checks if c.name == "semesters_check")
    assert sem_check.passed is False
    assert sem_check.actual_value == 5
    assert sem_check.required_value == 6


# ── 6. Maximum semesters exceeded — appeal eligible ──────────────────────────
#
# 133 * 0.80 = 106.4 → appeal threshold
# Student has 110 passed hours (>= 106.4) → dismissed_but_appeal_eligible

def test_maximum_semesters_exceeded_appeal_eligible():
    inp = _inp(
        completed_regular_semesters=17,   # > 16
        cumulative_passed_hours=110,       # >= 106.4
    )
    out = run_graduation_audit(inp)

    assert out.status == "dismissed_but_appeal_eligible", (
        f"Expected dismissed_but_appeal_eligible; got {out.status!r}"
    )
    assert "dismissed_but_appeal_eligible" in out.reason_codes
    assert "maximum_regular_semesters_exceeded" in out.reason_codes

    max_sem_check = next(c for c in out.checks if c.name == "maximum_semesters_check")
    assert max_sem_check.passed is False
    assert max_sem_check.actual_value == 17
    assert max_sem_check.required_value == 16


# ── 7. Maximum semesters exceeded — no appeal ────────────────────────────────
#
# Student has 90 passed hours (< 106.4) → dismissed_no_appeal

def test_maximum_semesters_exceeded_no_appeal():
    inp = _inp(
        completed_regular_semesters=20,
        cumulative_passed_hours=90,        # < 106.4
    )
    out = run_graduation_audit(inp)

    assert out.status == "dismissed_no_appeal"
    assert "dismissed_no_appeal" in out.reason_codes
    assert "maximum_regular_semesters_exceeded" in out.reason_codes


# ── 8. Warning dismissal — appeal eligible (consecutive = 4) ─────────────────

def test_warning_dismissal_appeal_eligible():
    inp = _inp(
        consecutive_warnings=4,            # >= max_consecutive_warnings (4)
        cumulative_passed_hours=110,       # >= 106.4
    )
    out = run_graduation_audit(inp)

    assert out.status == "dismissed_but_appeal_eligible"
    assert "dismissed_but_appeal_eligible" in out.reason_codes
    # Warning dismissal reason should NOT include maximum_regular_semesters_exceeded
    assert "maximum_regular_semesters_exceeded" not in out.reason_codes

    warn_check = next(c for c in out.checks if c.name == "warning_dismissal_check")
    assert warn_check.passed is False


# ── 9. Warning dismissal — no appeal (total = 6) ────────────────────────────

def test_warning_dismissal_no_appeal():
    inp = _inp(
        total_warnings=6,                  # >= max_total_warnings (6)
        cumulative_passed_hours=90,        # < 106.4
    )
    out = run_graduation_audit(inp)

    assert out.status == "dismissed_no_appeal"
    assert "dismissed_no_appeal" in out.reason_codes
    assert "maximum_regular_semesters_exceeded" not in out.reason_codes


# ── 10. Military required but not completed ───────────────────────────────────

def test_military_required_but_not_completed():
    inp = _inp(military_status="Deferred")
    out = run_graduation_audit(inp)

    assert out.status == "not_eligible"
    assert "military_training_required" in out.reason_codes

    mil_check = next(c for c in out.checks if c.name == "military_check")
    assert mil_check.passed is False
    assert mil_check.actual_value == "Deferred"


# ── 11. Military skipped when military_status is None ────────────────────────

def test_military_skipped_when_military_status_none():
    # Female student: military_status=None, rule=True → still skipped (None means female)
    inp = _inp(military_status=None)
    out = run_graduation_audit(inp)

    assert out.status == "eligible"
    check_names = [c.name for c in out.checks]
    assert "military_check" not in check_names


# ── 12. Military skipped when rule is False ───────────────────────────────────

def test_military_skipped_when_rule_false():
    # Rule disabled: even a student with Deferred status passes
    inp = _inp(
        military_status="Deferred",
        graduation_rules=_graduation_rules(military_training_required_for_males=False),
    )
    out = run_graduation_audit(inp)

    assert out.status == "eligible"
    check_names = [c.name for c in out.checks]
    assert "military_check" not in check_names


# ── 13. Zero-credit blocker when must_pass_zero_credit_courses=True ───────────

def test_zero_credit_blocker_when_rule_true():
    inp = _inp(zero_credit_courses_passed=False)
    out = run_graduation_audit(inp)

    assert out.status == "not_eligible"
    assert "zero_credit_requirements_not_met" in out.reason_codes

    zc_check = next(c for c in out.checks if c.name == "zero_credit_check")
    assert zc_check.passed is False


# ── 14. Zero-credit skipped when must_pass_zero_credit_courses=False ──────────

def test_zero_credit_skipped_when_rule_false():
    inp = _inp(
        zero_credit_courses_passed=False,
        graduation_rules=_graduation_rules(must_pass_zero_credit_courses=False),
    )
    out = run_graduation_audit(inp)

    assert out.status == "eligible"
    check_names = [c.name for c in out.checks]
    assert "zero_credit_check" not in check_names


# ── 15. already_graduated status — honors computed ────────────────────────────

def test_already_graduated_status():
    inp = _inp(
        study_status="Graduated",
        current_cgpa=3.8,
        completed_regular_semesters=8,
        course_history=[
            _entry("CS111", "Fall 2022", 4.0, 3),
            _entry("CS112", "Spring 2023", 3.7, 3),
        ],
    )
    out = run_graduation_audit(inp)

    assert out.status == "already_graduated"
    assert out.honors_status is not None
    assert len(out.honors_checks) == 4
    assert out.checks == []
    assert out.next_steps == []


# ── 16–18. not_auditable — Transferred Out, Suspended, Frozen ─────────────────

@pytest.mark.parametrize("study_status,expected_reason", [
    ("Transferred Out", "study_status_transferred_out"),
    ("Suspended",       "study_status_suspended"),
    ("Frozen",          "study_status_frozen"),
])
def test_not_auditable_statuses(study_status, expected_reason):
    inp = _inp(study_status=study_status)
    out = run_graduation_audit(inp)

    assert out.status == "not_auditable", (
        f"Expected not_auditable for {study_status!r}; got {out.status!r}"
    )
    assert expected_reason in out.reason_codes
    assert out.honors_status is None
    assert out.honors_checks == []


# ── 19. Honors passes when all criteria satisfied ─────────────────────────────
#
# 8 regular semesters (within 6-8), CGPA always >= 3.0, no failures

def test_honors_passes_when_all_criteria_satisfied():
    history = [
        _entry("CS111", "Fall 2022",   3.3, 3),
        _entry("CS112", "Spring 2023", 3.7, 3),
        _entry("CS211", "Fall 2023",   3.0, 3),
        _entry("CS212", "Spring 2024", 3.5, 3),
        _entry("CS311", "Fall 2024",   4.0, 3),
        _entry("CS312", "Spring 2025", 3.3, 3),
        _entry("CS411", "Fall 2025",   3.7, 3),
        _entry("CS412", "Spring 2026", 3.3, 3),
    ]
    inp = _inp(
        current_cgpa=3.5,
        cumulative_passed_hours=133,
        completed_regular_semesters=8,
        course_history=history,
    )
    out = run_graduation_audit(inp)

    assert out.status == "eligible"
    # CGPA throughout, semester count, no_failures → all pass
    cgpa_check = next(c for c in out.honors_checks if c.name == "cgpa_throughout")
    assert cgpa_check.passed is True
    no_fail_check = next(c for c in out.honors_checks if c.name == "no_failures")
    assert no_fail_check.passed is True
    sem_check = next(c for c in out.honors_checks if c.name == "semester_count")
    assert sem_check.passed is True
    # Overall honors status: not_eligible only if a check explicitly fails
    # (disciplinary is None → cannot_fully_verify)
    assert out.honors_status != "not_eligible"


# ── 20. Honors fails when student has any historical failed attempt ────────────

def test_honors_fails_when_student_has_failed_course():
    history = [
        _entry("CS111", "Fall 2022",   0.0, 3, status="failed"),
        _entry("CS111", "Spring 2023", 3.3, 3, status="passed"),   # retake passed
        _entry("CS112", "Spring 2023", 3.7, 3),
    ]
    inp = _inp(course_history=history)
    out = run_graduation_audit(inp)

    assert out.status == "eligible"   # graduation not blocked by old failure
    assert out.honors_status == "not_eligible"

    no_fail_check = next(c for c in out.honors_checks if c.name == "no_failures")
    assert no_fail_check.passed is False


# ── Bonus: multiple simultaneous not_eligible reasons ─────────────────────────

def test_multiple_reason_codes_when_multiple_checks_fail():
    inp = _inp(
        current_cgpa=1.5,               # cgpa_below_minimum
        cumulative_passed_hours=80,     # credits_incomplete
        completed_regular_semesters=4,  # minimum_regular_semesters_not_met
        zero_credit_courses_passed=False,  # zero_credit_requirements_not_met
    )
    out = run_graduation_audit(inp)

    assert out.status == "not_eligible"
    assert "cgpa_below_minimum" in out.reason_codes
    assert "credits_incomplete" in out.reason_codes
    assert "minimum_regular_semesters_not_met" in out.reason_codes
    assert "zero_credit_requirements_not_met" in out.reason_codes


# ── maximum_semesters_check always present in Studying output ─────────────────

def test_maximum_semesters_check_present_when_eligible():
    inp = _inp()
    out = run_graduation_audit(inp)

    max_check = next((c for c in out.checks if c.name == "maximum_semesters_check"), None)
    assert max_check is not None, "maximum_semesters_check should always appear for Studying students"
    assert max_check.passed is True
    assert max_check.actual_value == 8
    assert max_check.required_value == 16


# ── warning_dismissed=True takes priority even without max semester exceeded ───

def test_warning_dismissed_and_max_semester_both_true():
    # Both conditions true: 17 semesters AND 4 consecutive warnings, enough credits for appeal
    inp = _inp(
        completed_regular_semesters=17,
        consecutive_warnings=4,
        cumulative_passed_hours=110,
    )
    out = run_graduation_audit(inp)

    assert out.status == "dismissed_but_appeal_eligible"
    assert "dismissed_but_appeal_eligible" in out.reason_codes
    assert "maximum_regular_semesters_exceeded" in out.reason_codes
