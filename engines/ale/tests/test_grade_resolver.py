"""
Focused unit tests for grade_resolver.

Coverage:
  1. Letter normalization — case, whitespace, plus/minus variants
  2. Abs normalization — abs/ABS/Abs all resolve via grading scale
  3. P grade — resolves to None (gpa_neutral)
  4. Numeric grade points — actual float and string-encoded
  5. Percentage resolution — via injected grading scale, with boundary cases
  6. Invalid values — empty string, whitespace, unrecognized letter, out-of-range, NaN, inf
  7. derive_level() — Freshman/Sophomore/Junior/Senior boundary checks
"""

from __future__ import annotations

import pytest

from engines.ale.schemas import GradingScaleRules, PercentageRange, StudentLevelRules
from engines.ale.utils.grade_resolver import GradeResolutionError, derive_level, resolve_grade


CODE = "C-CS101"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _scale() -> GradingScaleRules:
    """Same percentage ranges as other ALE tests; adds Abs key."""
    return GradingScaleRules(
        letter_to_points={
            "A+": 4.0, "A": 4.0, "A-": 3.7,
            "B+": 3.3, "B": 3.0, "B-": 2.7,
            "C+": 2.3, "C": 2.0, "C-": 1.7,
            "D+": 1.3, "D": 1.0, "F": 0.0, "P": None, "Abs": 0.0,
        },
        percentage_to_letter=[
            PercentageRange(min_pct=97,  max_pct=100, letter="A+"),
            PercentageRange(min_pct=93,  max_pct=96,  letter="A"),
            PercentageRange(min_pct=90,  max_pct=92,  letter="A-"),
            PercentageRange(min_pct=87,  max_pct=89,  letter="B+"),
            PercentageRange(min_pct=83,  max_pct=86,  letter="B"),
            PercentageRange(min_pct=80,  max_pct=82,  letter="B-"),
            PercentageRange(min_pct=77,  max_pct=79,  letter="C+"),
            PercentageRange(min_pct=73,  max_pct=76,  letter="C"),
            PercentageRange(min_pct=70,  max_pct=72,  letter="C-"),
            PercentageRange(min_pct=67,  max_pct=69,  letter="D+"),
            PercentageRange(min_pct=60,  max_pct=66,  letter="D"),
            PercentageRange(min_pct=0,   max_pct=59,  letter="F"),
        ],
    )


def _level_rules() -> StudentLevelRules:
    """Matches thresholds used in generate_graduation_roadmap synthetic tests."""
    return StudentLevelRules(
        freshman_max_hours=30,
        sophomore_min_hours=31,
        sophomore_max_hours=60,
        junior_min_hours=61,
        junior_max_hours=90,
        senior_min_hours=91,
        senior_max_hours=133,
    )


# ---------------------------------------------------------------------------
# 1. Letter normalization
# ---------------------------------------------------------------------------

class TestLetterNormalization:
    def test_uppercase_A(self):
        assert resolve_grade("A", _scale(), CODE) == 4.0

    def test_lowercase_a(self):
        assert resolve_grade("a", _scale(), CODE) == 4.0

    def test_whitespace_A(self):
        assert resolve_grade(" A ", _scale(), CODE) == 4.0

    def test_uppercase_B_plus(self):
        assert resolve_grade("B+", _scale(), CODE) == 3.3

    def test_lowercase_b_plus(self):
        assert resolve_grade("b+", _scale(), CODE) == 3.3

    def test_lowercase_a_minus(self):
        assert resolve_grade("a-", _scale(), CODE) == 3.7


# ---------------------------------------------------------------------------
# 2. Abs normalization
# ---------------------------------------------------------------------------

class TestAbsNormalization:
    def test_Abs_canonical(self):
        assert resolve_grade("Abs", _scale(), CODE) == 0.0

    def test_abs_lowercase(self):
        assert resolve_grade("abs", _scale(), CODE) == 0.0

    def test_ABS_uppercase(self):
        assert resolve_grade("ABS", _scale(), CODE) == 0.0

    def test_Abs_with_whitespace(self):
        assert resolve_grade(" Abs ", _scale(), CODE) == 0.0


# ---------------------------------------------------------------------------
# 3. P grade — gpa_neutral
# ---------------------------------------------------------------------------

class TestPGrade:
    def test_P_uppercase(self):
        assert resolve_grade("P", _scale(), CODE) is None

    def test_p_lowercase(self):
        assert resolve_grade("p", _scale(), CODE) is None

    def test_P_with_whitespace(self):
        assert resolve_grade(" P ", _scale(), CODE) is None


# ---------------------------------------------------------------------------
# 4. Numeric grade points (actual float and string-encoded)
# ---------------------------------------------------------------------------

class TestNumericGradePoints:
    def test_float_3_7(self):
        assert resolve_grade(3.7, _scale(), CODE) == 3.7

    def test_string_3_7(self):
        assert resolve_grade("3.7", _scale(), CODE) == 3.7

    def test_float_4_0(self):
        assert resolve_grade(4.0, _scale(), CODE) == 4.0

    def test_string_4_0(self):
        assert resolve_grade("4.0", _scale(), CODE) == 4.0

    def test_float_0_0(self):
        assert resolve_grade(0.0, _scale(), CODE) == 0.0

    def test_string_0_0(self):
        assert resolve_grade("0.0", _scale(), CODE) == 0.0


# ---------------------------------------------------------------------------
# 5. Percentage resolution
# ---------------------------------------------------------------------------

class TestPercentages:
    def test_float_90(self):
        # 90–92 maps to A- → 3.7
        assert resolve_grade(90, _scale(), CODE) == 3.7

    def test_string_90(self):
        assert resolve_grade("90", _scale(), CODE) == 3.7

    def test_float_100(self):
        # 97–100 maps to A+ → 4.0
        assert resolve_grade(100, _scale(), CODE) == 4.0

    def test_string_100(self):
        assert resolve_grade("100", _scale(), CODE) == 4.0

    def test_boundary_96(self):
        # 93–96 maps to A → 4.0
        assert resolve_grade(96, _scale(), CODE) == 4.0

    def test_boundary_95_9(self):
        # still in 93–96 range
        assert resolve_grade(95.9, _scale(), CODE) == 4.0

    def test_boundary_92(self):
        # 90–92 upper edge → A- → 3.7
        assert resolve_grade(92, _scale(), CODE) == 3.7

    def test_boundary_50(self):
        # 0–59 → F → 0.0
        assert resolve_grade(50, _scale(), CODE) == 0.0

    def test_boundary_49_9(self):
        # still in 0–59
        assert resolve_grade(49.9, _scale(), CODE) == 0.0


# ---------------------------------------------------------------------------
# 6. Invalid values — must raise GradeResolutionError
# ---------------------------------------------------------------------------

class TestInvalidValues:
    def test_empty_string(self):
        with pytest.raises(GradeResolutionError):
            resolve_grade("", _scale(), CODE)

    def test_whitespace_only(self):
        with pytest.raises(GradeResolutionError):
            resolve_grade("   ", _scale(), CODE)

    def test_unrecognized_letter(self):
        with pytest.raises(GradeResolutionError):
            resolve_grade("hello", _scale(), CODE)

    def test_negative_number(self):
        with pytest.raises(GradeResolutionError):
            resolve_grade(-1, _scale(), CODE)

    def test_above_100(self):
        with pytest.raises(GradeResolutionError):
            resolve_grade(101, _scale(), CODE)

    def test_nan(self):
        with pytest.raises(GradeResolutionError):
            resolve_grade(float("nan"), _scale(), CODE)

    def test_inf(self):
        with pytest.raises(GradeResolutionError):
            resolve_grade(float("inf"), _scale(), CODE)


# ---------------------------------------------------------------------------
# 7. derive_level()
# ---------------------------------------------------------------------------

class TestDeriveLevel:
    def test_freshman_lower(self):
        assert derive_level(0, _level_rules()) == "Freshman"

    def test_freshman_upper_boundary(self):
        assert derive_level(30, _level_rules()) == "Freshman"

    def test_sophomore_lower_boundary(self):
        assert derive_level(31, _level_rules()) == "Sophomore"

    def test_sophomore_upper_boundary(self):
        assert derive_level(60, _level_rules()) == "Sophomore"

    def test_junior_lower_boundary(self):
        assert derive_level(61, _level_rules()) == "Junior"

    def test_junior_upper_boundary(self):
        assert derive_level(90, _level_rules()) == "Junior"

    def test_senior_lower_boundary(self):
        assert derive_level(91, _level_rules()) == "Senior"

    def test_senior_upper_boundary(self):
        assert derive_level(133, _level_rules()) == "Senior"
