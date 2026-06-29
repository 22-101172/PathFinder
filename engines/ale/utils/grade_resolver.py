"""
ale/utils/grade_resolver.py
===========================
Shared grade resolution used by simulate_gpa_forward and solve_target_gpa.

Converts any of the three accepted grade input formats into grade points (0.0–4.0).
Returns None for P-grade (caller treats course as gpa_neutral).
Raises GradeResolutionError for invalid inputs — caller maps this to cannot_compute.

String inputs are normalized before lookup:
  - Whitespace stripped.
  - Numeric strings (e.g. "3.7", "90") parsed and routed to _resolve_numeric.
  - "abs" / "ABS" variants normalized to "Abs" before grading-scale lookup.
  - Other letters uppercased (preserves +/-) before grading-scale lookup.
"""

import math

from engines.ale.ale_schemas import GradingScaleRules, StudentLevelRules


class GradeResolutionError(Exception):
    """Raised when a grade input cannot be resolved to grade points."""

    def __init__(self, course_code: str, grade_input: str | float) -> None:
        self.course_code = course_code
        self.grade_input = grade_input
        super().__init__(
            f"Invalid grade input '{grade_input}' for course '{course_code}'"
        )


def resolve_grade(
    grade_input: str | float,
    grading_scale: GradingScaleRules,
    course_code: str,
) -> float | None:
    """Resolve a grade input (letter, percentage, or grade points) to grade points.

    Returns:
        float: Grade points in 0.0–4.0
        None:  Grade is P (pass/fail — gpa_neutral, excluded from all GPA math)

    Raises:
        GradeResolutionError: Unrecognized letter, out-of-range number, or
                              percentage that does not match any configured range
    """
    if isinstance(grade_input, str):
        return _resolve_string(grade_input, grading_scale, course_code)
    try:
        return _resolve_numeric(float(grade_input), grading_scale, course_code)
    except (TypeError, ValueError):
        raise GradeResolutionError(course_code, grade_input)


def _resolve_string(
    raw: str,
    grading_scale: GradingScaleRules,
    course_code: str,
) -> float | None:
    s = raw.strip()
    if not s:
        raise GradeResolutionError(course_code, raw)
    # Numeric string: "3.7", "90", "4.0" — route to numeric resolver
    try:
        return _resolve_numeric(float(s), grading_scale, course_code)
    except ValueError:
        pass
    return _resolve_letter(_normalize_letter(s), grading_scale, course_code)


def _normalize_letter(s: str) -> str:
    """Normalize a grade letter to its canonical form.

    "abs"/"ABS"/"Abs" → "Abs" (grading scale canonical key).
    All other strings uppercased so "a" → "A", "b+" → "B+", "p" → "P".
    """
    if s.lower() == "abs":
        return "Abs"
    return s.upper()


def _resolve_letter(
    letter: str,
    grading_scale: GradingScaleRules,
    course_code: str,
) -> float | None:
    if letter not in grading_scale.letter_to_points:
        raise GradeResolutionError(course_code, letter)
    # letter_to_points[letter] is None for P — caller treats as gpa_neutral
    return grading_scale.letter_to_points[letter]


def _resolve_numeric(
    value: float,
    grading_scale: GradingScaleRules,
    course_code: str,
) -> float | None:
    if not math.isfinite(value):
        raise GradeResolutionError(course_code, value)
    if 0.0 <= value <= 4.0:
        return value
    if 4.0 < value <= 100.0:
        return _resolve_percentage(value, grading_scale, course_code)
    raise GradeResolutionError(course_code, value)


def _resolve_percentage(
    pct: float,
    grading_scale: GradingScaleRules,
    course_code: str,
) -> float | None:
    """Scan percentage_to_letter ranges top-down and resolve matched letter to grade points."""
    for range_entry in grading_scale.percentage_to_letter:
        if range_entry.min_pct <= pct <= range_entry.max_pct:
            # Delegate to letter resolution so P-grade is handled consistently
            return _resolve_letter(range_entry.letter, grading_scale, course_code)
    raise GradeResolutionError(course_code, pct)


def derive_level(passed_hours: int, rules: StudentLevelRules) -> str:
    """Derive student level from passed hours using injected handbook rules.
    Source: CIS Handbook — Student Level thresholds.
    Never hardcode the hour boundaries here. Always use rules fields.
    """
    if passed_hours <= rules.freshman_max_hours:
        return "Freshman"
    elif passed_hours <= rules.sophomore_max_hours:
        return "Sophomore"
    elif passed_hours <= rules.junior_max_hours:
        return "Junior"
    else:
        return "Senior"

