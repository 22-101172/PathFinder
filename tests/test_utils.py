"""
Unit tests for gateway.utils — pure utility functions.
No external dependencies, no adapters.
"""
from gateway.utils import get_next_semester, resolve_relative_semester_text


class TestGetNextSemester:

    def test_fall_to_spring(self):
        assert get_next_semester("Fall 2026") == "Spring 2027"

    def test_spring_to_fall(self):
        assert get_next_semester("Spring 2026") == "Fall 2026"

    def test_summer_to_fall(self):
        assert get_next_semester("Summer 2026") == "Fall 2026"


class TestResolveRelativeSemesterText:

    # ── Canonical spec cases ──────────────────────────────────────────────────

    def test_spring_2026_three_falls_from_now(self):
        assert resolve_relative_semester_text("3 Falls from now", "Spring 2026") == ("Fall", 2029)

    def test_spring_2026_third_fall_from_now(self):
        assert resolve_relative_semester_text("third Fall from now", "Spring 2026") == ("Fall", 2029)

    def test_fall_2026_next_fall(self):
        assert resolve_relative_semester_text("next Fall", "Fall 2026") == ("Fall", 2027)

    def test_summer_2026_two_springs_from_now(self):
        assert resolve_relative_semester_text("two Springs from now", "Summer 2026") == ("Spring", 2028)

    def test_invalid_phrase_returns_none(self):
        assert resolve_relative_semester_text("not-a-semester", "Spring 2026") is None

    # ── Additional coverage ───────────────────────────────────────────────────

    def test_case_insensitive(self):
        assert resolve_relative_semester_text("NEXT fall", "Fall 2026") == ("Fall", 2027)

    def test_next_spring_from_fall(self):
        assert resolve_relative_semester_text("next Spring", "Fall 2026") == ("Spring", 2027)

    def test_one_fall_from_now(self):
        assert resolve_relative_semester_text("1 Fall from now", "Spring 2026") == ("Fall", 2027)

    def test_first_fall_from_now(self):
        assert resolve_relative_semester_text("first Fall from now", "Spring 2026") == ("Fall", 2027)

    def test_singular_season_accepted(self):
        assert resolve_relative_semester_text("2 Fall from now", "Spring 2026") == ("Fall", 2028)

    def test_next_summer(self):
        assert resolve_relative_semester_text("next Summer", "Spring 2026") == ("Summer", 2027)

    def test_six_falls_from_now(self):
        assert resolve_relative_semester_text("6 Falls from now", "Fall 2024") == ("Fall", 2030)

    def test_seven_not_supported_returns_none(self):
        assert resolve_relative_semester_text("7 Falls from now", "Spring 2026") is None

    def test_empty_current_semester_returns_none(self):
        assert resolve_relative_semester_text("next Fall", "") is None

    def test_invalid_current_semester_returns_none(self):
        assert resolve_relative_semester_text("next Fall", "bad-format") is None
