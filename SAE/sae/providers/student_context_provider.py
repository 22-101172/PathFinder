"""
Student context providers for the SAE.

FakeStudentContextProvider — loads real anonymised data from the project Excel
snapshot, mimicking a live Student Information System (SIS) API.  The 'Fake'
name signals that it reads a static file rather than hitting a live endpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from sae.cgpa_calculator import load_credit_hours_map, _sem_ordinal


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> int | None:
    f = _safe_float(val)
    return None if f is None else int(f)


def _safe_str(val, default: str = "") -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return str(val).strip()


class FakeStudentContextProvider:
    """
    Provides student profile and registration data backed by the anonymised
    Excel snapshot.

    Data is loaded once at construction and cached in memory.  The interface
    mirrors what a live SIS provider would expose so the rest of the engine
    can swap backends without changes.
    """

    EXCLUDED_STATUSES = ["Graduated", "Suspended", "Frozen", "Transferred Out"]

    def __init__(self, students_xlsx_path: str | Path, catalogue_xlsx_path: str | Path) -> None:
        students_xlsx_path = Path(students_xlsx_path)
        catalogue_xlsx_path = Path(catalogue_xlsx_path)

        # ── Load students sheet ───────────────────────────────────────────
        students_raw = pd.read_excel(students_xlsx_path, sheet_name="data")
        students_raw = students_raw.dropna(subset=["Program", "Level"]).reset_index(drop=True)

        # ── Load registrations sheet ──────────────────────────────────────
        regs_raw = pd.read_excel(students_xlsx_path, sheet_name="registrations")
        regs_raw = regs_raw.loc[:, regs_raw.columns.notna()]
        regs_raw = regs_raw[[c for c in regs_raw.columns if not str(c).startswith("Unnamed")]]

        # Keep the unfiltered copy for course-risk analytics (includes graduated students)
        self._all_regs_df: pd.DataFrame = regs_raw.copy()

        valid_ids = set(students_raw["ID"])
        regs_raw = regs_raw[regs_raw["ID"].isin(valid_ids)].reset_index(drop=True)

        self.students_df: pd.DataFrame = students_raw
        self.regs_df: pd.DataFrame = regs_raw

        # ── Credit hours map ──────────────────────────────────────────────
        self.credit_hours_map: dict[str, float] = load_credit_hours_map(catalogue_xlsx_path)

        # ── Active pool: exclude non-active statuses ──────────────────────
        self._active_df: pd.DataFrame = self.students_df[
            ~self.students_df["Study Status"].astype(str).str.strip().isin(self.EXCLUDED_STATUSES)
        ]

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _row_to_profile(self, row: pd.Series) -> dict[str, Any]:
        return {
            "id":                      _safe_str(row.get("ID")),
            "name":                    _safe_str(row.get("Name")),
            "program":                 _safe_str(row.get("Program")),
            "level":                   _safe_str(row.get("Level")),
            "study_status":            _safe_str(row.get("Study Status")),
            "military_status":         _safe_str(row.get("Military Status")),
            "consecutive_warning":     _safe_int(row.get("Consecutive Warning")),
            "total_warnings":          _safe_int(row.get("Total Warnings")),
            "official_cgpa":           _safe_float(row.get("Cumulative GPA")),
            "official_credits_passed": _safe_float(row.get("Cumulative PHs")),
        }

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def get_student_profile(self, student_id: str) -> dict[str, Any] | None:
        """
        Return a profile dict for one student, or None if not found.

        Keys: id, name, program, level, study_status, military_status,
              consecutive_warning, total_warnings, official_cgpa (stored),
              official_credits_passed (Cumulative PHs — passed credits).
        """
        mask = self.students_df["ID"] == student_id
        if not mask.any():
            return None
        return self._row_to_profile(self.students_df[mask].iloc[0])

    def get_registrations(self, student_id: str) -> list[dict[str, Any]]:
        """
        Return all registration records for a student, sorted chronologically.

        Each dict: semester, course_code, letter_grade, registration_status.
        """
        rows = self.regs_df[self.regs_df["ID"] == student_id]
        if rows.empty:
            return []

        result = []
        for _, row in rows.iterrows():
            lg_raw = row["Letter Grade"]
            lg = None if pd.isna(lg_raw) else _safe_str(lg_raw) or None
            result.append({
                "semester":            _safe_str(row.get("Semester")),
                "course_code":         _safe_str(row.get("Course Code")),
                "letter_grade":        lg,
                "registration_status": _safe_str(row.get("Registration Status")),
            })

        result.sort(key=lambda r: _sem_ordinal(r["semester"]))
        return result

    def get_current_semester_courses(self, student_id: str) -> list[dict[str, Any]]:
        """
        Return courses for the student's most recent semester only.

        Each dict: course_code, credit_hours, letter_grade (None if ungraded),
                   registration_status, is_graded.
        """
        rows = self.regs_df[self.regs_df["ID"] == student_id].copy()
        if rows.empty:
            return []

        rows["_ord"] = rows["Semester"].apply(lambda s: _sem_ordinal(str(s)))
        max_ord = rows["_ord"].max()
        current = rows[rows["_ord"] == max_ord]

        result = []
        for _, row in current.iterrows():
            code = _safe_str(row.get("Course Code"))
            lg_raw = row["Letter Grade"]
            lg = None if pd.isna(lg_raw) else _safe_str(lg_raw) or None
            result.append({
                "course_code":         code,
                "credit_hours":        self.credit_hours_map.get(code, 3.0),
                "letter_grade":        lg,
                "registration_status": _safe_str(row.get("Registration Status")),
                "is_graded":           lg is not None,
            })
        return result

    def get_credit_hours_map(self) -> dict[str, float]:
        """Return the {course_code: credit_hours} catalogue map."""
        return self.credit_hours_map

    def get_all_active_students(self) -> list[dict[str, Any]]:
        """Return profile dicts for all non-graduated students."""
        return [self._row_to_profile(row) for _, row in self._active_df.iterrows()]

    def get_cohort(self, level: str) -> list[dict[str, Any]]:
        """Return profile dicts for all active students at the same level."""
        mask = self._active_df["Level"].astype(str).str.strip() == level.strip()
        return [self._row_to_profile(row) for _, row in self._active_df[mask].iterrows()]

    # ------------------------------------------------------------------ #
    # Advisor simulation                                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _advisor_idx(student_id: str) -> int:
        """Deterministic 0-9 index for advisor assignment based on student ID."""
        return sum(ord(c) for c in str(student_id)) % 10

    def get_all_advisors(self) -> list[str]:
        """Return the 10 simulated advisor IDs (ADV001–ADV010)."""
        return [f"ADV{str(i + 1).zfill(3)}" for i in range(10)]

    def get_all_registrations(self) -> "pd.DataFrame":
        """Return registrations for ALL students including graduated ones."""
        return self._all_regs_df

    def get_advisor_students(self, advisor_id: str) -> list[dict[str, Any]]:
        """Return profile dicts for all active students assigned to this advisor."""
        target_idx = int(advisor_id.replace("ADV", "")) - 1
        result = []
        for _, row in self._active_df.iterrows():
            sid = _safe_str(row.get("ID"))
            if self._advisor_idx(sid) == target_idx:
                result.append(self._row_to_profile(row))
        return result
