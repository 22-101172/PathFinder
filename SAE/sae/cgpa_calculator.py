"""
CGPA Calculator for the Student Analysis Engine.

Credit-hour source priority
---------------------------
1. data/Course_Catalogue_Correct_Version.xlsx  (sheet: 'Course Catalogue2')
2. engines/ale/rules/curriculum_2026.json       (fallback, credits field)

All CGPA functions use best-grade-per-course logic: when a student retakes
a course, the highest grade point across all attempts is used, replacing the
original attempt.  W, I, Abs, Con, P, and null grades are excluded entirely
from both the numerator and denominator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Grade point table
# ---------------------------------------------------------------------------

GRADE_POINTS: dict[str, float] = {
    "A+": 4.0, "A": 3.7, "A-": 3.4,
    "B+": 3.2, "B": 3.0, "B-": 2.8,
    "C+": 2.6, "C": 2.4, "C-": 2.2,
    "D+": 2.0, "D": 1.5, "D-": 1.0,
    "F":  0.0,
}

_SAE_ROOT = Path(__file__).parent.parent
_CATALOGUE_PATH = _SAE_ROOT / "data" / "Course_Catalogue_Correct_Version.xlsx"
_CATALOGUE_SHEET = "Course Catalogue2"
_JSON_FALLBACK   = _SAE_ROOT / "engines" / "ale" / "rules" / "curriculum_2026.json"

_SEASON_ORD = {"Spring": 0, "Summer": 1, "Fall": 2}


def _sem_ordinal(sem: str) -> int:
    parts = str(sem).strip().split()
    if len(parts) < 2:
        return 0
    try:
        return int(parts[1]) * 3 + _SEASON_ORD.get(parts[0], 0)
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Credit-hour loader
# ---------------------------------------------------------------------------

def load_credit_hours_map(
    catalogue_path: str | Path = _CATALOGUE_PATH,
    json_fallback: str | Path = _JSON_FALLBACK,
) -> dict[str, float]:
    """
    Return {course_code: credit_hours} from the course catalogue.

    Tries the Excel catalogue first; falls back to the curriculum JSON.
    Always returns a dict (never raises).

    Returns
    -------
    dict mapping course_code (str) -> credit_hours (float).
    """
    path = Path(catalogue_path)

    # ── Try Excel catalogue ───────────────────────────────────────────────
    if path.exists():
        try:
            df = pd.read_excel(path, sheet_name=_CATALOGUE_SHEET)
            # Normalise column names: look for code and credit-hours columns
            df.columns = df.columns.str.strip()
            code_col = next(
                (c for c in df.columns
                 if any(k in c.lower() for k in ("code", "course code", "course_code"))),
                None,
            )
            credit_col = next(
                (c for c in df.columns
                 if any(k in c.lower() for k in ("credit", "ch", "hours", "unit"))),
                None,
            )
            if code_col and credit_col:
                result: dict[str, float] = {}
                for _, row in df.iterrows():
                    code = str(row[code_col]).strip()
                    try:
                        chs = float(row[credit_col])
                        if pd.notna(chs) and chs >= 0:
                            result[code] = chs
                    except (TypeError, ValueError):
                        pass
                if result:
                    return result
        except Exception:
            pass  # fall through to JSON

    # ── Fall back to curriculum JSON ──────────────────────────────────────
    jpath = Path(json_fallback)
    if jpath.exists():
        try:
            with jpath.open(encoding="utf-8") as fh:
                curriculum = json.load(fh)
            result = {}
            for code, data in curriculum.get("courses", {}).items():
                try:
                    chs = float(data.get("credits", 3))
                    if chs > 0:
                        result[code] = chs
                except (TypeError, ValueError):
                    pass
            if result:
                return result
        except Exception:
            pass

    # ── Last resort: assume 3 credit hours for every course ──────────────
    return {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _best_grades(
    regs: pd.DataFrame,
    up_to_ordinal: int | None = None,
) -> dict[str, float]:
    """
    Return {course_code: best_grade_point} for all graded courses in regs.

    Parameters
    ----------
    regs
        Registration rows for a single student (already filtered by ID).
    up_to_ordinal
        If given, only include rows whose semester ordinal is <= this value.
    """
    df = regs.copy()
    df["_lg"] = df["Letter Grade"].astype(str).str.strip().str.upper()
    df["_gp"] = df["_lg"].map(GRADE_POINTS)

    if up_to_ordinal is not None and "Semester" in df.columns:
        df["_ord"] = df["Semester"].apply(_sem_ordinal)
        df = df[df["_ord"] <= up_to_ordinal]

    graded = df.dropna(subset=["_gp"])
    if graded.empty:
        return {}

    best = graded.groupby("Course Code")["_gp"].max()
    return best.to_dict()


def _cgpa_from_best(
    best: dict[str, float],
    credit_map: dict[str, float],
    default_credits: float = 3.0,
) -> tuple[float, float, float]:
    """
    Compute CGPA from a best-grade dict.

    Returns
    -------
    (cgpa, total_credit_hours, total_grade_points)
    """
    total_ch = 0.0
    total_cp = 0.0
    for code, gp in best.items():
        ch = credit_map.get(code, default_credits)
        total_ch += ch
        total_cp += gp * ch

    cgpa = round(total_cp / total_ch, 2) if total_ch > 0 else 0.0
    return cgpa, round(total_ch, 2), round(total_cp, 2)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def compute_cgpa(
    student_id: str,
    registrations_df: pd.DataFrame,
    credit_hours_map: dict[str, float],
    default_credits: float = 3.0,
) -> dict[str, Any]:
    """
    Compute cumulative GPA for one student using best-grade-per-course logic.

    For each course the student has attempted, the highest grade point across
    all attempts is used.  W / I / Abs / Con / P / null entries are excluded.

    Parameters
    ----------
    credit_hours_map
        {course_code: credit_hours}.  Courses not in the map use default_credits.
    default_credits
        Fallback credit hours when a course is not in credit_hours_map.

    Returns
    -------
    dict
        cgpa                   : float (2 dp)
        total_credit_hours     : float
        total_grade_points     : float
        courses_counted        : int
        credit_source          : 'catalogue' | 'fallback'
        per_course             : list of {course_code, best_grade, grade_point,
                                          credit_hours, grade_points_earned}
    """
    regs = registrations_df[registrations_df["ID"] == student_id]
    best = _best_grades(regs)

    if not best:
        return {
            "cgpa": 0.0,
            "total_credit_hours": 0.0,
            "total_grade_points": 0.0,
            "courses_counted": 0,
            "credit_source": "none",
            "per_course": [],
        }

    # Reverse lookup: grade_point -> letter (for display)
    _GP_TO_LETTER: dict[float, str] = {}
    for lt, gp in GRADE_POINTS.items():
        _GP_TO_LETTER.setdefault(gp, lt)

    per_course = []
    total_ch = 0.0
    total_cp = 0.0
    missing_from_map = 0

    for code, gp in sorted(best.items()):
        if code in credit_hours_map:
            ch = credit_hours_map[code]
        else:
            ch = default_credits
            missing_from_map += 1
        earned = gp * ch
        total_ch += ch
        total_cp += earned
        per_course.append({
            "course_code":        code,
            "best_grade":         _GP_TO_LETTER.get(gp, "?"),
            "grade_point":        gp,
            "credit_hours":       ch,
            "grade_points_earned": round(earned, 2),
        })

    cgpa = round(total_cp / total_ch, 2) if total_ch > 0 else 0.0
    credit_source = (
        "fallback" if missing_from_map == len(best)
        else "catalogue" if missing_from_map == 0
        else "mixed"
    )

    return {
        "cgpa":               cgpa,
        "total_credit_hours": round(total_ch, 2),
        "total_grade_points": round(total_cp, 2),
        "courses_counted":    len(best),
        "credit_source":      credit_source,
        "per_course":         per_course,
    }


def compute_cgpa_per_semester(
    student_id: str,
    registrations_df: pd.DataFrame,
    credit_hours_map: dict[str, float],
    default_credits: float = 3.0,
) -> list[dict[str, Any]]:
    """
    Compute the CUMULATIVE GPA at the end of each semester, in order.

    At each semester, uses the best grade for each course seen so far
    (retakes that appear in a later semester will only improve the running
    CGPA from that semester onward).

    Returns
    -------
    List of dicts sorted chronologically:
        semester_label                : str
        cgpa_at_end_of_semester       : float
        credits_completed_by_semester : float
        new_courses_this_semester     : list[str]
    """
    regs = registrations_df[registrations_df["ID"] == student_id].copy()
    if regs.empty or "Semester" not in regs.columns:
        return []

    regs["_lg"]  = regs["Letter Grade"].astype(str).str.strip().str.upper()
    regs["_gp"]  = regs["_lg"].map(GRADE_POINTS)
    regs["_ord"] = regs["Semester"].apply(_sem_ordinal)

    semesters = sorted(regs["Semester"].dropna().unique(), key=_sem_ordinal)

    results = []
    for sem in semesters:
        sem_ord = _sem_ordinal(sem)

        # Skip semesters where every course is ungraded (null, W, I, Con, Abs only).
        # Such semesters are still in progress and would just duplicate the previous
        # CGPA value on the chart, making the progression misleading.
        sem_rows_all = regs[regs["Semester"] == sem]
        if not sem_rows_all["_gp"].notna().any():
            continue

        best     = _best_grades(regs, up_to_ordinal=sem_ord)
        cgpa, total_ch, _ = _cgpa_from_best(best, credit_hours_map, default_credits)

        # Courses that first appear this semester (first graded attempt)
        sem_rows = regs[(regs["Semester"] == sem) & regs["_gp"].notna()]
        new_in_sem = [
            c for c in sem_rows["Course Code"].unique()
            if pd.notna(c) and str(c).strip()
        ]

        results.append({
            "semester_label":                sem,
            "cgpa_at_end_of_semester":       cgpa,
            "credits_completed_by_semester": total_ch,
            "new_courses_this_semester":     new_in_sem,
        })

    return results


def simulate_cgpa(
    student_id: str,
    registrations_df: pd.DataFrame,
    credit_hours_map: dict[str, float],
    hypothetical_grades: dict[str, str],
    default_credits: float = 3.0,
) -> dict[str, Any]:
    """
    Project CGPA if the student achieves hypothetical_grades in current courses.

    Parameters
    ----------
    hypothetical_grades
        {course_code: letter_grade} for courses not yet graded.

    Returns
    -------
    dict
        current_cgpa      : float  — CGPA from completed courses only
        projected_cgpa    : float  — CGPA after adding hypothetical grades
        cgpa_change       : float  — projected - current (positive = improvement)
        per_course_breakdown : list of {course_code, source, grade, grade_point,
                                        credit_hours, grade_points_earned}
    """
    regs = registrations_df[registrations_df["ID"] == student_id]
    existing_best = _best_grades(regs)

    current_cgpa, _, _ = _cgpa_from_best(existing_best, credit_hours_map, default_credits)

    # Merge: hypothetical grades overlay existing (only for new/ungraded courses)
    combined_best: dict[str, float] = dict(existing_best)
    for code, letter in hypothetical_grades.items():
        lg = str(letter).strip().upper()
        gp = GRADE_POINTS.get(lg)
        if gp is None:
            continue
        # Use best of existing (if any) and hypothetical
        existing_gp = existing_best.get(code)
        combined_best[code] = max(existing_gp, gp) if existing_gp is not None else gp

    projected_cgpa, _, _ = _cgpa_from_best(combined_best, credit_hours_map, default_credits)

    _GP_TO_LETTER: dict[float, str] = {}
    for lt, gp in GRADE_POINTS.items():
        _GP_TO_LETTER.setdefault(gp, lt)

    breakdown = []
    for code, gp in sorted(combined_best.items()):
        ch = credit_hours_map.get(code, default_credits)
        source = "hypothetical" if code in hypothetical_grades and code not in existing_best else \
                 "improved"     if code in hypothetical_grades and code in existing_best and \
                                   GRADE_POINTS.get(hypothetical_grades[code].strip().upper(), -1) > existing_best.get(code, -1) else \
                 "existing"
        breakdown.append({
            "course_code":         code,
            "source":              source,
            "grade":               _GP_TO_LETTER.get(gp, "?"),
            "grade_point":         gp,
            "credit_hours":        ch,
            "grade_points_earned": round(gp * ch, 2),
        })

    return {
        "current_cgpa":       current_cgpa,
        "projected_cgpa":     projected_cgpa,
        "cgpa_change":        round(projected_cgpa - current_cgpa, 2),
        "per_course_breakdown": breakdown,
    }


def get_cgpa_display_data(
    student_id: str,
    registrations_df: pd.DataFrame,
    credit_hours_map: dict[str, float],
    official_cgpa: float | None,
    default_credits: float = 3.0,
) -> dict[str, Any]:
    """
    Combine the authoritative official CGPA with computed trend and credit data.

    The official_cgpa is passed in from the stored records (SIS / Excel) and is
    used as-is.  The per-semester trend is computed from registration history so
    advisors can see the trajectory even when the absolute values differ slightly
    from official.  Credits completed are computed from credit-hour lookup and
    match the stored Cumulative CHs exactly when the catalogue is present.

    Parameters
    ----------
    official_cgpa
        The Cumulative GPA as stored in the student records (authoritative).
        Pass None for students with no completed graded courses.

    Returns
    -------
    dict with:
        official_cgpa      : float | None — from SIS/records; shown to students
        cgpa_trend_history : list[dict]   — per-semester trajectory; each entry
                             carries  is_computed=True  so the UI can label it
                             "GPA Trend (estimated from records)"
        credits_completed  : float        — total CHs from best-grade computation;
                             matches stored Cumulative CHs when catalogue is loaded
        courses_completed  : int          — distinct graded courses
    """
    computed = compute_cgpa(student_id, registrations_df, credit_hours_map, default_credits)
    trend    = compute_cgpa_per_semester(student_id, registrations_df, credit_hours_map, default_credits)

    for entry in trend:
        entry["is_computed"] = True

    return {
        "official_cgpa":      official_cgpa,
        "cgpa_trend_history": trend,
        "credits_completed":  computed["total_credit_hours"],
        "courses_completed":  computed["courses_counted"],
    }


def compute_percentage_to_grade(percentage: float) -> str:
    """
    Convert a numeric percentage (0–100) to a letter grade (EUI scale).

    Boundaries
    ----------
    96+      A+    92-95   A     88-91   A-
    84-87    B+    80-83   B     76-79   B-
    72-75    C+    68-71   C     64-67   C-
    60-63    D+    55-59   D     50-54   D-
    < 50     F
    """
    p = float(percentage)
    if p >= 96: return "A+"
    if p >= 92: return "A"
    if p >= 88: return "A-"
    if p >= 84: return "B+"
    if p >= 80: return "B"
    if p >= 76: return "B-"
    if p >= 72: return "C+"
    if p >= 68: return "C"
    if p >= 64: return "C-"
    if p >= 60: return "D+"
    if p >= 55: return "D"
    if p >= 50: return "D-"
    return "F"
