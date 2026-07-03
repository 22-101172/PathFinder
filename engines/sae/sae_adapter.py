"""
SAEAdapter — unified entry point for all SAE data and analytics operations.

Composes a student provider and a rules provider into a single facade so
callers never need to juggle multiple imports.  Passing providers at
construction enables dependency injection for testing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engines.sae.cgpa_calculator import (
    compute_cgpa_per_semester,
    get_cgpa_display_data,
    simulate_cgpa,
    GRADE_POINTS,
)
from engines.sae.providers.student_context_provider import FakeStudentContextProvider
from engines.sae.providers.rules_provider import FakeRulesProvider

# ---------------------------------------------------------------------------
# Default data paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent.parent

_DEFAULT_STUDENT_PATHS = [
    _ROOT / "data" / "students_anonymous.xlsx",
    _ROOT / "data" / "students_anonymous (1).xlsx",
    _ROOT / "students_anonymous.xlsx",
    _ROOT / "students_anonymous (1).xlsx",
]

_DEFAULT_CATALOGUE_PATH = _ROOT / "data" / "Course_Catalogue_Correct_Version.xlsx"


def _find_students_file() -> Path:
    for p in _DEFAULT_STUDENT_PATHS:
        if p.exists():
            return p
    searched = "\n  ".join(str(p) for p in _DEFAULT_STUDENT_PATHS)
    raise FileNotFoundError(f"Could not locate students Excel file.  Searched:\n  {searched}")


# ---------------------------------------------------------------------------
# Percentage → letter grade conversion
# ---------------------------------------------------------------------------

def _pct_to_letter(pct: float, grade_scale: dict[str, int]) -> str:
    """Convert a percentage to letter grade using the provided scale."""
    ordered = sorted(grade_scale.items(), key=lambda x: x[1], reverse=True)
    for letter, threshold in ordered:
        if pct >= threshold:
            return letter
    return "F"


def _parse_grade_value(val: Any, grade_scale: dict[str, int]) -> tuple[str, bool]:
    """
    Return (letter_grade, was_converted_from_percentage).

    Accepts:
      - str letter grade ("A", "B+", …)
      - numeric percentage (85, 72.5) — any number > 4.0 treated as percentage
      - str percentage ("85%", "72.5%")
    """
    if isinstance(val, str):
        stripped = val.strip()
        if stripped.endswith("%"):
            pct = float(stripped[:-1])
            return _pct_to_letter(pct, grade_scale), True
        # Treat as letter grade
        return stripped.upper(), False
    if isinstance(val, (int, float)):
        fval = float(val)
        if fval > 4.0:
            return _pct_to_letter(fval, grade_scale), True
        # Ambiguous small float — treat as percentage only if not a grade point
        return _pct_to_letter(fval, grade_scale), True
    return str(val).strip().upper(), False


# ---------------------------------------------------------------------------
# SAEAdapter
# ---------------------------------------------------------------------------

class SAEAdapter:
    """
    Unified facade over student data, registration history, CGPA analytics,
    and institutional rules.

    Parameters
    ----------
    student_provider
        Any object implementing the FakeStudentContextProvider interface.
        Defaults to FakeStudentContextProvider with the project's Excel file.
    rules_provider
        Any object implementing the FakeRulesProvider interface.
        Defaults to FakeRulesProvider (institutional constants).
    """

    def __init__(self, student_provider=None, rules_provider=None) -> None:
        if student_provider is None:
            students_path = _find_students_file()
            student_provider = FakeStudentContextProvider(
                students_xlsx_path=students_path,
                catalogue_xlsx_path=_DEFAULT_CATALOGUE_PATH,
            )
        if rules_provider is None:
            rules_provider = FakeRulesProvider()

        self.sp = student_provider
        self.rp = rules_provider
        self.credit_hours_map: dict[str, float] = self.sp.get_credit_hours_map()

    # ------------------------------------------------------------------ #
    # Full student context                                                  #
    # ------------------------------------------------------------------ #

    def get_student_full_context(self, student_id: str) -> dict[str, Any] | None:
        """
        Return all data needed to analyse or advise one student.

        Returns None if the student is not found.

        Keys
        ----
        profile          : dict — from sp.get_student_profile()
        registrations    : list[dict] — chronological registration history
        current_courses  : list[dict] — most-recent-semester courses
        cgpa_trend_history : list[dict] — per-semester CGPA trajectory
        official_cgpa    : float | None — from stored records (authoritative)
        credits_completed : float — computed from catalogue credit hours
        rules            : dict — institutional policy constants
        """
        profile = self.sp.get_student_profile(student_id)
        if profile is None:
            return None

        registrations  = self.sp.get_registrations(student_id)
        current_courses = self.sp.get_current_semester_courses(student_id)
        trend          = compute_cgpa_per_semester(
            student_id, self.sp.regs_df, self.credit_hours_map
        )
        for entry in trend:
            entry["is_computed"] = True

        display = get_cgpa_display_data(
            student_id,
            self.sp.regs_df,
            self.credit_hours_map,
            profile["official_cgpa"],
        )

        return {
            "profile":           profile,
            "registrations":     registrations,
            "current_courses":   current_courses,
            "cgpa_trend_history": trend,
            "official_cgpa":     display["official_cgpa"],
            "credits_completed": display["credits_completed"],
            "rules":             self.rp.get_rules(),
        }

    # ------------------------------------------------------------------ #
    # All students summary                                                  #
    # ------------------------------------------------------------------ #

    def get_all_students_summary(self) -> list[dict[str, Any]]:
        """
        Return lightweight profile data for every active (non-graduated) student.

        Does NOT compute CGPA or features — all values come directly from stored
        records so this is fast even for 700+ students.

        Keys per entry: id, name, program, level, study_status, official_cgpa,
                        credits_completed (Cumulative PHs), consecutive_warning.
        """
        result = []
        for p in self.sp.get_all_active_students():
            result.append({
                "id":                 p["id"],
                "name":               p["name"],
                "program":            p["program"],
                "level":              p["level"],
                "study_status":       p["study_status"],
                "official_cgpa":      p["official_cgpa"],
                "credits_completed":  p["official_credits_passed"],
                "consecutive_warning": p["consecutive_warning"],
            })
        return result

    # ------------------------------------------------------------------ #
    # GPA simulation                                                        #
    # ------------------------------------------------------------------ #

    def simulate_semester_gpa(
        self,
        student_id: str,
        hypothetical_grades: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Project CGPA after hypothetical grades for current/ungraded courses.

        hypothetical_grades values may be:
          - letter grades  : "A", "B+", …
          - numeric pct    : 85, 72.5  (any value > 4.0 treated as percentage)
          - string pct     : "85%", "72.5%"

        Returns
        -------
        current_official_cgpa  : float | None — stored CGPA
        projected_cgpa         : float — after applying hypothetical grades
        cgpa_change            : float — projected - current (computed baseline)
        converted_grades       : dict  — {course: letter} for pct inputs only
        per_course_breakdown   : list[dict] — course-level detail
        """
        grade_scale = self.rp.get_grading_scale()
        profile = self.sp.get_student_profile(student_id)
        official_cgpa = profile["official_cgpa"] if profile else None

        # Convert all inputs to letter grades
        converted: dict[str, str] = {}
        letter_grades: dict[str, str] = {}
        for code, val in hypothetical_grades.items():
            letter, was_converted = _parse_grade_value(val, grade_scale)
            letter_grades[code] = letter
            if was_converted:
                converted[code] = letter

        sim = simulate_cgpa(
            student_id, self.sp.regs_df, self.credit_hours_map, letter_grades
        )

        # Reshape breakdown to the specified format
        breakdown = [
            {
                "course_code":  entry["course_code"],
                "credit_hours": entry["credit_hours"],
                "grade":        entry["grade"],
                "grade_points": entry["grade_point"],
                "contribution": entry["grade_points_earned"],
            }
            for entry in sim["per_course_breakdown"]
        ]

        return {
            "current_official_cgpa": official_cgpa,
            "projected_cgpa":        sim["projected_cgpa"],
            "cgpa_change":           sim["cgpa_change"],
            "converted_grades":      converted,
            "per_course_breakdown":  breakdown,
        }

    # ------------------------------------------------------------------ #
    # Rules                                                                 #
    # ------------------------------------------------------------------ #

    def get_all_registrations(self) -> "pd.DataFrame":
        """Return registrations for ALL students including graduated ones."""
        return self.sp.get_all_registrations()

    def get_rules(self) -> dict:
        """Return the institutional policy rules dict."""
        return self.rp.get_rules()

    # ------------------------------------------------------------------ #
    # Cohort statistics                                                     #
    # ------------------------------------------------------------------ #

    def get_cohort_stats(self, student_id: str) -> dict[str, Any]:
        """
        Return the student's standing within their level cohort (all programs).

        Keys
        ----
        student_id       : str
        level            : str
        student_cgpa     : float | None
        cohort_size      : int  — active students at the same level
        cohort_avg_cgpa  : float
        cohort_median_cgpa : float
        student_percentile : float — % of cohort with strictly lower CGPA
        is_top_half      : bool
        """
        profile = self.sp.get_student_profile(student_id)
        if profile is None:
            return {"error": f"Student {student_id!r} not found"}

        cohort = self.sp.get_cohort(profile["level"])
        valid  = [s for s in cohort if s["official_cgpa"] is not None]

        if not valid:
            return {
                "student_id": student_id,
                "level": profile["level"],
                "cohort_size": len(cohort),
                "error": "No cohort members with a recorded CGPA",
            }

        cgpas = [s["official_cgpa"] for s in valid]
        n = len(cgpas)
        avg = round(sum(cgpas) / n, 3)

        sorted_cgpas = sorted(cgpas)
        if n % 2 == 0:
            median = round((sorted_cgpas[n // 2 - 1] + sorted_cgpas[n // 2]) / 2, 3)
        else:
            median = round(sorted_cgpas[n // 2], 3)

        student_cgpa = profile["official_cgpa"]
        if student_cgpa is not None:
            lower_count = sum(1 for c in cgpas if c < student_cgpa)
            percentile  = round(lower_count / n * 100, 1)
            is_top_half = percentile >= 50.0
        else:
            percentile  = None
            is_top_half = None

        return {
            "student_id":          student_id,
            "level":               profile["level"],
            "student_cgpa":        student_cgpa,
            "cohort_size":         n,
            "cohort_avg_cgpa":     avg,
            "cohort_median_cgpa":  median,
            "student_percentile":  percentile,
            "is_top_half":         is_top_half,
        }
