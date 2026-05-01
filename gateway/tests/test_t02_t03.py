"""
tests/test_t02_t03.py
─────────────────────
Acceptance tests for:
  T02 — StudentContextProvider  (T1–T6)
  T03 — SessionManager          (T7–T12)

Run from the gateway/ directory:
  python -m pytest tests/test_t02_t03.py -v

All 12 tests must pass before T02 / T03 are considered done.
"""

import pytest

from models.schemas import StudentContext
from session_manager import SessionManager
from student_context_provider import StudentContextProvider

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def provider():
    """One StudentContextProvider shared across all T02 tests (populates cache once)."""
    return StudentContextProvider()


@pytest.fixture(scope="module")
def student(provider: StudentContextProvider) -> StudentContext:
    """
    Load S_000123 once for the entire test module.
    Module scope means the cache hit test (T6) sees the already-cached object.
    """
    ctx = provider.get_student("S_000123")
    assert ctx is not None, (
        "S_000123 could not be loaded — verify student_profile.json path and contents."
    )
    return ctx


@pytest.fixture
def manager(provider: StudentContextProvider) -> SessionManager:
    """Fresh SessionManager per test — no session state leaks between T7–T12."""
    return SessionManager(provider)


# ── T02: StudentContextProvider ───────────────────────────────────────────────


class TestStudentContextProvider:

    def test_T1_returns_correct_identity_fields(self, student: StudentContext) -> None:
        """get_student('S_000123') returns a StudentContext with the expected identity fields."""
        assert isinstance(student, StudentContext)
        assert student.student_id == "S_000123"
        assert student.name == "Seif Elislam"
        assert student.cgpa == 2.85
        assert student.track_id == "AI"

    def test_T2_derived_views_are_disjoint_and_correct(self, student: StudentContext) -> None:
        """completed / failed / in_progress are disjoint and each code lands in exactly one bucket."""
        all_codes = (
            set(student.completed_courses)
            | set(student.failed_courses)
            | set(student.in_progress_courses)
        )
        # Disjoint check: union size equals sum of individual sizes
        total = (
            len(student.completed_courses)
            + len(student.failed_courses)
            + len(student.in_progress_courses)
        )
        assert total == len(all_codes), "A course appears in more than one bucket"

        # Known failed course from student_profile.json
        assert "C-CS218" in student.failed_courses
        assert "C-CS218" not in student.completed_courses
        assert "C-CS218" not in student.in_progress_courses

        # Known in-progress courses from student_profile.json (Spring 2025)
        for code in ("C-AI321", "C-CS316", "HUM228"):
            assert code in student.in_progress_courses, f"{code} should be in_progress"
            assert code not in student.completed_courses
            assert code not in student.failed_courses

        # Spot-check a completed course
        assert "C-CS111" in student.completed_courses

    def test_T3_credit_hours_remaining_formula(self, student: StudentContext) -> None:
        """credit_hours_remaining == 133 − total_credit_hours_earned."""
        expected = 133 - student.total_credit_hours_earned
        assert student.credit_hours_remaining == expected, (
            f"Expected {expected}, got {student.credit_hours_remaining}. "
            f"total_credit_hours_earned={student.total_credit_hours_earned}"
        )

    def test_T4_max_credit_hours_for_cgpa_2_85(self, student: StudentContext) -> None:
        """CGPA 2.85 falls in the ≥2.0 tier → max 18 credit hours per semester."""
        assert student.cgpa == 2.85
        assert student.max_credit_hours_allowed == 18

    def test_T5_invalid_id_returns_none_without_raising(
        self, provider: StudentContextProvider
    ) -> None:
        """get_student with an unknown ID must return None — not raise."""
        result = None
        try:
            result = provider.get_student("DOES_NOT_EXIST")
        except Exception as exc:
            pytest.fail(f"get_student raised an unexpected exception: {exc}")
        assert result is None

    def test_T6_cache_returns_same_python_object(
        self, provider: StudentContextProvider
    ) -> None:
        """
        The second call for the same student_id must return the identical object (cache hit).
        Using `is` verifies that no new object was created — the provider did not re-read the file.
        """
        ctx1 = provider.get_student("S_000123")
        ctx2 = provider.get_student("S_000123")
        assert "S_000123" in provider._cache, "Cache was not populated after first call"
        assert ctx1 is ctx2, (
            "Cache miss: two different objects were returned for the same student_id. "
            "The provider re-read the file instead of returning the cached object."
        )


# ── T03: SessionManager ───────────────────────────────────────────────────────


class TestSessionManager:

    def test_T7_new_session_id_has_correct_prefix(self, manager: SessionManager) -> None:
        """get_or_create_session without session_id returns a 'sess_'-prefixed string."""
        sid = manager.get_or_create_session("S_000123")
        assert isinstance(sid, str)
        assert sid.startswith("sess_"), f"Expected prefix 'sess_', got: {sid!r}"

    def test_T8_existing_session_id_is_returned_unchanged(
        self, manager: SessionManager
    ) -> None:
        """Passing an existing session_id back returns the same id — session is reused."""
        sid = manager.get_or_create_session("S_000123")
        sid2 = manager.get_or_create_session("S_000123", session_id=sid)
        assert sid2 == sid

    def test_T9_override_added_courses_appear_in_planned(
        self, manager: SessionManager, student: StudentContext
    ) -> None:
        """After apply_overrides, build_effective_context includes the hypothetical course."""
        sid = manager.get_or_create_session("S_000123")
        manager.apply_overrides(sid, {"added_courses": ["C-AI401"], "target_role": None})
        effective = manager.build_effective_context(student, sid)
        assert "C-AI401" in effective.planned_courses

    def test_T10_base_context_is_not_mutated(
        self, manager: SessionManager, student: StudentContext
    ) -> None:
        """
        build_effective_context must not mutate base_context.
        planned_courses on the original StudentContext must remain [].
        """
        sid = manager.get_or_create_session("S_000123")
        manager.apply_overrides(sid, {"added_courses": ["C-AI401"], "target_role": None})
        _ = manager.build_effective_context(student, sid)
        assert student.planned_courses == [], (
            "base_context.planned_courses was mutated — violation of the immutability rule. "
            "build_effective_context must use model_copy(), never in-place modification."
        )

    def test_T11_overrides_accumulate_across_two_turns(
        self, manager: SessionManager, student: StudentContext
    ) -> None:
        """
        Two apply_overrides calls must accumulate — both courses appear in planned_courses.
        Simulates a student adding hypothetical courses across consecutive turns.
        """
        sid = manager.get_or_create_session("S_000123")
        manager.apply_overrides(sid, {"added_courses": ["C-AI401"], "target_role": None})
        manager.apply_overrides(sid, {"added_courses": ["C-AI501"], "target_role": None})
        effective = manager.build_effective_context(student, sid)
        assert "C-AI401" in effective.planned_courses, "First override was lost"
        assert "C-AI501" in effective.planned_courses, "Second override was not applied"

    def test_T12_update_last_referenced_only_touches_given_keys(
        self, manager: SessionManager
    ) -> None:
        """
        update_last_referenced updates only the keys that are explicitly provided.
        Keys that were not passed must remain None.
        """
        sid = manager.get_or_create_session("S_000123")
        manager.update_last_referenced(sid, role_id="ROLE_DS")
        session = manager.get_session(sid)
        assert session is not None
        assert session.last_referenced["role_id"] == "ROLE_DS"
        assert session.last_referenced["course_code"] is None, (
            "course_code was unexpectedly updated"
        )
        assert session.last_referenced["workflow"] is None, (
            "workflow was unexpectedly updated"
        )
