from __future__ import annotations
import logging
from typing import Optional

import pandas as pd

from gateway.models.schemas import CourseRecord, StudentContext
from gateway.utils import get_current_semester

logger = logging.getLogger(__name__)

# Track ID normalization — not a rule, just naming consistency
# across all system components (QU, KG, ALE, Composer)
PROGRAM_TO_TRACK: dict[str, str] = {
    "artificial intelligence": "AI",
    "cyber security": "Cyber",
    "cybersecurity": "Cyber",
    "data science and engineering": "Data Science",
    "data science": "Data Science",
    "computer science": "CS",
    "software engineering": "SW",
    "general program": "General",
    "general": "General",
}

# Level string → integer for ALE input
# Source: Excel Level column values as written by the registrar
LEVEL_MAP: dict[str, int] = {
    "freshman": 1,
    "sophomore": 2,
    "junior": 3,
    "senior": 4,
}

# Best-outcome priority — higher wins when a student has multiple
# registration rows for the same course
_OUTCOME_PRIORITY: dict[str, int] = {
    "passed":      6,
    "repeated":    5,
    "incomplete":  4,
    "failed":      3,
    "in_progress": 2,
    "withdrawn":   1,
}

_df_data: Optional[pd.DataFrame] = None
_df_reg: Optional[pd.DataFrame] = None


def load_excel(path: str) -> None:
    """
    Load the student registrar Excel file into module-level DataFrames.
    Must be called once at startup before any get_context() call.
    Both sheets are loaded: "data" (one row per student) and
    "registrations" (one row per course registration attempt).
    """
    global _df_data, _df_reg
    logger.info("SCP | loading %s", path)
    _df_data = pd.read_excel(path, sheet_name="data")
    _df_reg  = pd.read_excel(path, sheet_name="registrations")
    logger.info(
        "SCP | loaded %d students, %d registration rows",
        len(_df_data), len(_df_reg),
    )



def _parse_track(program_str: str) -> str:
    """
    Normalizes the raw program string from the Excel to a short track ID
    used consistently across all system components.
    This is naming normalization only — not a policy rule.
    Falls back to "General" if no match found.
    """
    if not program_str:
        return "General"
    lower = program_str.lower()
    for key, track_id in PROGRAM_TO_TRACK.items():
        if key in lower:
            return track_id
    return "General"


def _safe_float(val) -> Optional[float]:
    """Parse a value to float, returning None for NaN or unparseable."""
    try:
        v = float(val)
        return None if pd.isna(v) else round(v, 3)
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> Optional[int]:
    """Parse a value to int, returning None for NaN or unparseable."""
    try:
        v = float(val)
        return None if pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return None


def _clean_grade(raw) -> Optional[str]:
    """
    Normalizes a raw letter grade value from the Excel cell.
    Returns None if the cell is empty, NaN, or missing.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    return None if s.lower() in ("", "nan") else s


def _map_status(reg_status: str, grade: Optional[str]) -> str:
    """
    Maps a raw registration status tag set and letter grade to a
    normalized CourseRecord status.

    Status semantics:
      passed     — course completed with a passing grade (including Con
                   and P grades). Counts toward completed_courses.
      repeated   — passed via a repeat retake attempt.
      failed     — course attempted and failed (F, Abs, or Failed tag).
      incomplete — taken in a past semester, grade unresolved (I grade).
                   Counts as an attempt. Treated as ongoing for planning.
      in_progress— currently being taken this semester (no grade yet).
      withdrawn  — student dropped the course (W grade or Withdrawn/
                   Forced Withdraw tag). Does NOT count as an attempt.

    Con grade semantics:
      Con means the course is done but its grade depends on a subsequent
      course (e.g. graduation project Part A waiting for Part B).
      It is treated as passed so that prerequisites for the next part
      are satisfied immediately.

    Order of checks matters — do not reorder.
    """
    tags = {t.strip().lower() for t in reg_status.split(",")}
    g = grade or ""

    # Withdrawn — tag or explicit W grade (not an attempt)
    if "withdrawn" in tags or "forced withdraw" in tags or g == "W":
        return "withdrawn"

    # Con — graduation project spanning semesters, treated as passed
    if g == "Con":
        return "passed"

    # No grade yet — currently in progress this semester
    if not g:
        return "in_progress"

    # Incomplete — taken in a past semester, unresolved
    if g == "I":
        return "incomplete"

    # P grade — zero-credit pass-only course, treated as passed
    if g == "P":
        return "passed"

    # Failed outcomes
    if g in ("F", "Abs") or "failed" in tags:
        return "failed"

    # Passed via repeat retake
    if "repeat" in tags and "succeeded" in tags:
        return "repeated"

    # Passed normally or via improve retake
    if "succeeded" in tags:
        return "passed"

    # Safe fallback
    return "failed"


def _compute_retake_count(student_regs: pd.DataFrame) -> dict[str, int]:
    """
    Counts how many times the student has genuinely attempted each course.
    Withdrawn rows are excluded — a withdrawal is not an attempt.
    All other rows (passed, failed, incomplete, in_progress, Con) count.

    IMPORTANT: Must be called on raw registration data BEFORE _map_status()
    is applied, because once statuses are mapped the withdrawal distinction
    based on tags could be lost if we rely on status alone.
    """
    counts: dict[str, int] = {}
    for _, row in student_regs.iterrows():
        code = str(row.get("Course Code", "")).strip()
        if not code or code.lower() == "nan":
            continue

        reg_status = str(row.get("Registration Status", "")).strip()
        grade = _clean_grade(row.get("Letter Grade"))
        tags = {t.strip().lower() for t in reg_status.split(",")}

        # Skip withdrawals — not real attempts
        if "withdrawn" in tags or "forced withdraw" in tags or grade == "W":
            continue

        counts[code] = counts.get(code, 0) + 1
    return counts


def _compute_improve_retakes(student_regs: pd.DataFrame) -> int:
    """
    Counts the total lifetime improve-retake attempts used by the student.
    The ALE checks this against the improve-retake cap rule from RAG.

    IMPORTANT: Must be called on raw registration data BEFORE _map_status()
    because the Improve tag is the only signal — once statuses are mapped
    the distinction between regular and improve retakes is lost.
    """
    count = 0
    for _, row in student_regs.iterrows():
        reg_status = str(row.get("Registration Status", "")).strip()
        tags = {t.strip().lower() for t in reg_status.split(",")}
        if "improve" in tags or "repeat for improvement" in tags:
            count += 1
    return count


def _compute_regular_semesters(student_regs: pd.DataFrame) -> int:
    """
    Counts distinct Fall and Spring semesters in which the student had
    at least one non-withdrawn registration.

    Rules:
      - Summer semesters excluded entirely (not regular semesters)
      - A semester where ALL registrations were withdrawn does not count
      - A single non-withdrawn row in a semester is enough to count it
    """
    valid: set[str] = set()
    for _, row in student_regs.iterrows():
        semester = str(row.get("Semester", "")).strip()
        if not semester or semester.lower() == "nan":
            continue
        if "Fall" not in semester and "Spring" not in semester:
            continue  # Skip Summer

        reg_status = str(row.get("Registration Status", "")).strip()
        grade = _clean_grade(row.get("Letter Grade"))
        tags = {t.strip().lower() for t in reg_status.split(",")}

        if "withdrawn" in tags or "forced withdraw" in tags or grade == "W":
            continue  # This row is a withdrawal — skip it

        valid.add(semester)
    return len(valid)


def _compute_zero_credit_passed(student_regs: pd.DataFrame) -> list[str]:
    """
    Returns course codes where the student received a P grade.
    P = pass-only grade for zero-credit mandatory courses.

    The orchestrator cross-references this list against KG to determine
    whether all required zero-credit courses have been completed,
    then resolves to a boolean for the ALE graduation audit.
    """
    result: list[str] = []
    for _, row in student_regs.iterrows():
        grade = _clean_grade(row.get("Letter Grade"))
        if grade == "P":
            code = str(row.get("Course Code", "")).strip()
            if code and code.lower() != "nan":
                result.append(code)
    return result


def get_context(student_id: str) -> Optional[StudentContext]:
    """
    Builds and returns a StudentContext for the given student ID.
    Returns None if the student is not found in the data sheet.
    Raises RuntimeError if load_excel() has not been called.

    All fields are derived from Excel data only — no rules applied here.
    Rule-dependent fields (academic_standing, credit_hours patch,
    grade_points patch, zero_credit bool) are the orchestrator's
    responsibility.

    Edge cases handled:
      - Student with zero registration rows → empty lists, zero counts
      - Student with only withdrawn rows → same as zero registrations
      - Con grade → treated as passed (prerequisite satisfaction)
      - I grade → treated as incomplete (counts as attempt, ongoing)
      - P grade → treated as passed + added to zero_credit_courses_passed
      - NaN or missing Excel cells → safe defaults via _safe_float/_safe_int
    """
    if _df_data is None or _df_reg is None:
        raise RuntimeError("Call load_excel() before get_context()")

    row = _df_data[_df_data["ID"].astype(str) == str(student_id)]
    if row.empty:
        logger.warning("SCP | student %s not found", student_id)
        return None

    r = row.iloc[0]

    # --- Scalar fields from data sheet ---
    cgpa          = _safe_float(r.get("Cumulative GPA"))
    consecutive   = _safe_int(r.get("Consecutive Warning")) or 0
    total_warnings_val = _safe_int(r.get("Total Warnings")) or 0

    mil_raw = str(r.get("Military Status", "") or "").strip()
    military_status = (
        None if not mil_raw or mil_raw.lower() in ("nan", "") else mil_raw
    )

    # --- Registration rows for this student ---
    student_regs = _df_reg[
        _df_reg["ID"].astype(str) == str(student_id)
    ].copy()

    # --- Derived fields from raw registrations (BEFORE status mapping) ---
    # These must run on raw data because _map_status() loses tag detail
    retake_count              = _compute_retake_count(student_regs)
    total_improve_retakes_used = _compute_improve_retakes(student_regs)
    completed_regular_semesters = _compute_regular_semesters(student_regs)
    zero_credit_courses_passed  = _compute_zero_credit_passed(student_regs)

    # --- Build course_history and best-outcome resolution ---
    course_history: list[CourseRecord] = []
    best_outcome: dict[str, str] = {}

    for _, reg in student_regs.iterrows():
        code = str(reg.get("Course Code", "")).strip()
        if not code or code.lower() == "nan":
            continue

        reg_status = str(reg.get("Registration Status", "")).strip()
        grade      = _clean_grade(reg.get("Letter Grade"))
        semester   = str(reg.get("Semester", "")).strip()

        status = _map_status(reg_status, grade)

        course_history.append(CourseRecord(
            course_code=code,
            credit_hours=0,   # sentinel — orchestrator patches via KG
            grade=grade,
            semester_taken=semester,
            status=status,
        ))

        # Keep best outcome per course using priority map
        current = best_outcome.get(code, "withdrawn")
        if _OUTCOME_PRIORITY.get(status, 0) > _OUTCOME_PRIORITY.get(current, 0):
            best_outcome[code] = status

    # --- Derive course lists from best outcomes ---
    completed   = [c for c, s in best_outcome.items() if s in ("passed", "repeated")]
    failed      = [c for c, s in best_outcome.items() if s == "failed"]
    in_progress = [c for c, s in best_outcome.items() if s in ("in_progress", "incomplete")]
    # Note: withdrawn courses are intentionally excluded from all lists

    ctx = StudentContext(
        student_id=str(r["ID"]),
        name=str(r.get("Name", "")).strip(),
        program=str(r.get("Program", "")).strip(),
        track_id=_parse_track(str(r.get("Program", ""))),
        level=LEVEL_MAP.get(str(r.get("Level", "")).strip().lower(), 1),
        first_semester=str(r.get("First Semester", "")).strip(),
        study_status=str(r.get("Study Status", "Studying")).strip(),
        military_status=military_status,
        cgpa=cgpa,
        last_semester_gpa=_safe_float(r.get("Last Semester GPA")),
        total_credit_hours_earned=_safe_int(r.get("Cumulative PHs")) or 0,
        cumulative_chs=_safe_int(r.get("Cumulative CHs")),
        cumulative_cps=_safe_float(r.get("Cumulative CPs")),
        last_semester_chs=_safe_int(r.get("Last Semester CHs")),
        last_semester_cps=_safe_float(r.get("Last Semester CPs")),
        last_semester_phs=_safe_int(r.get("Last Semester PHs")),
        current_semester_chs=_safe_int(r.get("Current Semester CHs")) or 0,
        consecutive_warnings=consecutive,
        total_warnings=total_warnings_val,
        last_semester_warning=_safe_int(r.get("Last Semester Warning")),
        course_history=course_history,
        completed_courses=completed,
        failed_courses=failed,
        in_progress_courses=in_progress,
        completed_regular_semesters=completed_regular_semesters,
        zero_credit_courses_passed=zero_credit_courses_passed,
        retake_count=retake_count,
        total_improve_retakes_used=total_improve_retakes_used,
    )

    logger.info(
        "SCP | %s | track=%s level=%d cgpa=%s "
        "completed=%d failed=%d in_progress=%d "
        "regular_sems=%d improve_retakes=%d zero_credit=%d",
        student_id, ctx.track_id, ctx.level, ctx.cgpa,
        len(ctx.completed_courses), len(ctx.failed_courses),
        len(ctx.in_progress_courses), ctx.completed_regular_semesters,
        ctx.total_improve_retakes_used, len(ctx.zero_credit_courses_passed),
    )
    return ctx
