from __future__ import annotations
import logging
from typing import Optional

import pandas as pd

from gateway.models.schemas import CourseRecord, StudentContext
from gateway.utils import get_current_semester

logger = logging.getLogger(__name__)

# Canonical KG track IDs only — must match Neo4j track nodes exactly.
# SCP maps program strings to these IDs.  Any program not in this map is
# "unsupported" and will receive track_id=None / track_status="unsupported".
PROGRAM_TO_TRACK: dict[str, str] = {
    "artificial intelligence": "AI",
    "cyber security": "CYS",
    "cybersecurity": "CYS",
    "data science and engineering": "DSE",
    "data science": "DSE",
    "software engineering": "SWE",
    "general program": "GEN",
    "general": "GEN",
}

# Programs explicitly known to exist in registrar data but NOT mapped to any KG
# track.  Computer Science is the current known case.  Carry-forward: supervisor
# must confirm whether CS records should map to an existing KG track.
_UNSUPPORTED_PROGRAMS: frozenset[str] = frozenset({"computer science"})

# Level string → integer for ALE input
LEVEL_MAP: dict[str, int] = {
    "freshman": 1,
    "sophomore": 2,
    "junior": 3,
    "senior": 4,
}

# Best-outcome priority — used to track whether a student ever passed a course
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
_global_current_semester: Optional[str] = None  # Set once by load_excel()

# Minimum active blank-registered rows for a semester to qualify as the
# global current semester. Prevents stale blank rows from old semesters winning.
_ACTIVE_BLANK_THRESHOLD: int = 100


def _load_and_validate_excel(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load and validate student Excel file.
    Raises with clear messages if file or schema is wrong.

    Returns:
      (df_data, df_reg): validated DataFrames

    Raises:
      FileNotFoundError: if path does not exist
      ValueError: if required sheets or columns are missing
    """
    import os

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Student data file not found: {path}\n"
            f"Expected path: {os.path.abspath(path)}"
        )

    try:
        df_data = pd.read_excel(path, sheet_name="data")
        df_reg = pd.read_excel(path, sheet_name="registrations")
    except ValueError as exc:
        raise ValueError(
            f"Excel file is missing required sheets. Error: {exc}\n"
            f"Expected sheets: 'data', 'registrations'"
        ) from exc

    required_data_cols = {
        "ID", "Name", "Program", "Level", "Study Status",
        "Cumulative GPA", "Consecutive Warning", "Total Warnings",
        "Military Status", "First Semester",
        "Cumulative PHs", "Cumulative CHs", "Cumulative CPs",
        "Last Semester GPA", "Last Semester CHs", "Last Semester CPs",
        "Last Semester PHs", "Last Semester Warning", "Current Semester CHs",
    }

    missing_data_cols = required_data_cols - set(df_data.columns)
    if missing_data_cols:
        raise ValueError(
            f"Data sheet missing columns: {sorted(missing_data_cols)}\n"
            f"Available columns: {sorted(set(df_data.columns))}"
        )

    required_reg_cols = {
        "ID", "Course Code", "Semester", "Registration Status", "Letter Grade",
    }

    missing_reg_cols = required_reg_cols - set(df_reg.columns)
    if missing_reg_cols:
        raise ValueError(
            f"Registrations sheet missing columns: {sorted(missing_reg_cols)}\n"
            f"Available columns: {sorted(set(df_reg.columns))}"
        )

    if df_data.empty:
        logger.warning("SCP | data sheet is empty (no student records)")
    if df_reg.empty:
        logger.warning("SCP | registrations sheet is empty (no enrollment records)")

    logger.info(
        "SCP | Excel validation passed: %d students, %d registration rows",
        len(df_data), len(df_reg),
    )

    return df_data, df_reg


def load_excel(path: str) -> None:
    """
    Load the student registrar Excel file into module-level DataFrames.
    Must be called once at startup before any get_context() call.

    Validates file existence and schema before loading.
    Raises FileNotFoundError or ValueError with clear messages if validation fails.

    Both sheets are loaded: "data" (one row per student) and
    "registrations" (one row per course registration attempt).

    Also computes _global_current_semester from the full registrations sheet.
    """
    global _df_data, _df_reg, _global_current_semester

    logger.info("SCP | validating and loading %s", path)
    _df_data, _df_reg = _load_and_validate_excel(path)

    _global_current_semester, _infer_method = _compute_global_current_semester(_df_reg)

    logger.info(
        "SCP | loaded %d students, %d registration rows | "
        "global_current_semester=%r (%s)",
        len(_df_data), len(_df_reg), _global_current_semester, _infer_method,
    )


def _parse_track(program_str: str) -> tuple[Optional[str], str, Optional[str]]:
    """
    Map a raw registrar program string to KG canonical track info.

    Returns (track_id, track_status, track_error_code).
    KG canonical track IDs: AI, CYS, DSE, SWE, GEN.
    Computer Science and other unknown/unsupported programs get track_id=None.

    Carry-forward: Orchestrator must block track-dependent planning/roadmap/
    recommendation flows when track_status is "unsupported", because KG has
    no curriculum data for unsupported programs.
    """
    if not program_str or str(program_str).strip().lower() in ("", "nan"):
        logger.warning("SCP | blank/missing program — cannot determine track")
        return None, "unsupported", "unsupported_track"

    lower = program_str.lower()

    # Check explicitly unsupported programs first (before generic unknown fallback)
    for key in _UNSUPPORTED_PROGRAMS:
        if key in lower:
            logger.warning(
                "SCP | program %r is not a KG track (unsupported) — "
                "supervisor must confirm handling for this program",
                program_str,
            )
            return None, "unsupported", "unsupported_track"

    # Check supported track mappings (longer keys before shorter to avoid partial match)
    for key, track_id in PROGRAM_TO_TRACK.items():
        if key in lower:
            return track_id, "supported", None

    # Unknown program — not in any known map
    logger.warning(
        "SCP | unknown program %r — no KG track mapping found (treating as unsupported)",
        program_str,
    )
    return None, "unsupported", "unsupported_track"


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


_GRADE_CASE_MAP: dict[str, str] = {
    "abs": "Abs",
    "con": "Con",
    "p":   "P",
    "f":   "F",
    "i":   "I",
    "w":   "W",
}


def _clean_grade(raw) -> Optional[str]:
    """
    Normalizes a raw letter grade value from the Excel cell.
    Returns None if the cell is empty, NaN, or missing.
    Canonical casing is enforced for known token grades (P, Con, I, F, Abs, W)
    so that grade comparisons in _map_status() are case-insensitive in practice.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if s.lower() in ("", "nan"):
        return None
    return _GRADE_CASE_MAP.get(s.lower(), s)


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

    Order of checks:
      All explicit outcomes (grades, tags) are checked BEFORE implicit
      statuses (blank = in_progress). This ensures a failed tag with
      blank grade is caught as failed, not misclassified as in_progress.
    """
    tags = {t.strip().lower() for t in reg_status.split(",")}
    g = grade or ""

    # Withdrawn — tag or explicit W grade (not an attempt)
    if "withdrawn" in tags or "forced withdraw" in tags or g == "W":
        return "withdrawn"

    # Con — graduation project spanning semesters, treated as passed
    if g == "Con":
        return "passed"

    # P grade — zero-credit pass-only course, treated as passed
    if g == "P":
        return "passed"

    # Incomplete — taken in a past semester, unresolved
    if g == "I":
        return "incomplete"

    # Failed outcomes — must be checked before blank grade
    if g in ("F", "Abs") or "failed" in tags:
        return "failed"

    # Passed via repeat retake
    if "repeat" in tags and "succeeded" in tags:
        return "repeated"

    # Passed normally or via improve retake
    if "succeeded" in tags:
        return "passed"

    # No grade yet — currently in progress this semester
    if not g:
        return "in_progress"

    # Safe fallback — log warning for unusual combos
    logger.warning(
        "SCP | unusual status/grade combo: tags=%s, grade=%s — defaulting to failed",
        tags, grade
    )
    return "failed"


def _compute_retake_count(student_regs: pd.DataFrame) -> dict[str, int]:
    """
    Counts how many times the student has genuinely attempted each course.
    Withdrawn rows are excluded — a withdrawal is not an attempt.
    All other rows (passed, failed, incomplete, in_progress, Con) count.

    IMPORTANT: Must be called on raw registration data BEFORE _map_status()
    is applied, because once statuses are mapped the withdrawal distinction
    based on tags could be lost if we rely on status alone.

    Note: retake_count is the non-withdrawn attempt count, not the improve-retake
    count. Orchestrator must not use retake_count as improve_retake_number.
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
    Counts distinct course codes where the student has used an improve-retake slot.

    Policy: the improve-retake cap is course-count based (one slot per course),
    not raw-attempt based. A student who improves the same course twice uses one
    slot, not two.

    Withdrawn improve-retake rows are excluded: no confirmed handbook policy
    states that a withdrawn improve-retake consumes a slot.  Conservative
    documented behavior applies.

    IMPORTANT: Must be called on raw registration data BEFORE _map_status()
    because the Improve tag is the only signal.
    """
    improved_courses: set[str] = set()
    for _, row in student_regs.iterrows():
        reg_status = str(row.get("Registration Status", "")).strip()
        grade = _clean_grade(row.get("Letter Grade"))
        tags = {t.strip().lower() for t in reg_status.split(",")}

        # Skip withdrawn rows — not confirmed to consume a slot
        if "withdrawn" in tags or "forced withdraw" in tags or grade == "W":
            continue

        if "improve" in tags or "repeat for improvement" in tags:
            code = str(row.get("Course Code", "")).strip()
            if code and code.lower() != "nan":
                improved_courses.add(code)

    return len(improved_courses)


def _semester_key(semester: str) -> tuple:
    """
    Sortable key for chronological semester ordering.
    Fall YYYY → (YYYY, 2), Spring YYYY → (YYYY, 1), Summer YYYY → (YYYY, 0).
    Unparseable → (-1, 0) so it sorts earliest.
    """
    parts = semester.strip().split()
    if len(parts) != 2:
        return (-1, 0)
    season, year_str = parts[0], parts[1]
    try:
        year = int(year_str)
    except ValueError:
        return (-1, 0)
    return (year, {"Fall": 2, "Spring": 1, "Summer": 0}.get(season, -1))


def _latest_semester(semesters: list[str]) -> str:
    """Return the chronologically latest semester from a list."""
    return max(semesters, key=_semester_key)


def _is_active_blank(reg_status: str, grade: Optional[str]) -> bool:
    """
    True when a registration row represents an active, not-yet-graded enrollment
    in the current semester.

    Conditions (all must hold):
      1. Letter Grade is blank (None)
      2. Registration Status contains "Registered"
      3. Registration Status does NOT contain any terminal outcome tag:
         Succeeded, Failed, Withdrawn, Forced Withdraw

    This distinguishes genuinely in-progress rows from old blank rows that
    appear in past semesters with terminal tags like "Succeeded, Registered".
    """
    if grade is not None:
        return False
    tags = {t.strip().lower() for t in reg_status.split(",")}
    if "registered" not in tags:
        return False
    terminal = {"succeeded", "failed", "withdrawn", "forced withdraw"}
    return not tags.intersection(terminal)


def _compute_global_current_semester(
    df_reg: pd.DataFrame,
) -> tuple[Optional[str], str]:
    """
    Infer the current academic semester once from the full registrations sheet.

    Algorithm:
      1. Count active blank-registered rows per semester (see _is_active_blank).
      2. If the semester with the highest count has >= _ACTIVE_BLANK_THRESHOLD
         active rows, that is the global current semester.
      3. Fallback: chronologically latest non-withdrawn row across all students.
      4. Final fallback: system clock via get_current_semester().

    Returns:
      (semester, inference_method)
      where inference_method is one of:
        "active_blank_threshold"       — majority of active enrollments
        "latest_non_withdrawn_global"  — no threshold met; latest completed row
        "system_clock"                 — no usable registrar data at all
    """
    active_counts: dict[str, int] = {}
    all_non_withdrawn: list[str] = []

    for _, row in df_reg.iterrows():
        semester = str(row.get("Semester", "")).strip()
        if not semester or semester.lower() == "nan":
            continue

        reg_status = str(row.get("Registration Status", "")).strip()
        grade = _clean_grade(row.get("Letter Grade"))
        tags = {t.strip().lower() for t in reg_status.split(",")}

        if "withdrawn" not in tags and "forced withdraw" not in tags and grade != "W":
            all_non_withdrawn.append(semester)

        if _is_active_blank(reg_status, grade):
            active_counts[semester] = active_counts.get(semester, 0) + 1

    if active_counts:
        best_sem = max(active_counts, key=lambda s: active_counts[s])
        best_count = active_counts[best_sem]
        if best_count >= _ACTIVE_BLANK_THRESHOLD:
            logger.info(
                "SCP | global current_semester=%r via active_blank_threshold (count=%d)",
                best_sem, best_count,
            )
            return best_sem, "active_blank_threshold"

    if all_non_withdrawn:
        chosen = _latest_semester(all_non_withdrawn)
        logger.info(
            "SCP | global current_semester=%r via latest_non_withdrawn_global",
            chosen,
        )
        return chosen, "latest_non_withdrawn_global"

    chosen = get_current_semester()
    logger.warning(
        "SCP | global current_semester=%r via system_clock (no registrar data)", chosen
    )
    return chosen, "system_clock"


def _infer_current_semester(student_regs: pd.DataFrame) -> tuple[Optional[str], str]:
    """
    Infer the student's current academic semester from registration data.

    Algorithm:
      1. Map statuses for all non-withdrawn rows.
      2. Count in-progress rows per semester (majority vote).
      3. Pick the semester with the highest count; break ties chronologically.
      4. Guard: if no semester has in-progress rows, fall back to the
         chronologically latest non-withdrawn semester.
      5. Final fallback: utils.get_current_semester() when registrar data
         provides no signal at all.

    Returns:
      (semester, inference_method)
      where inference_method is one of:
        "majority_vote"         — multiple in-progress rows, one semester won
        "single_inprogress"     — only one in-progress row found
        "latest_non_withdrawn"  — no in-progress rows; latest completed used
        "system_clock"          — no registrar data available
    """
    in_progress_counts: dict[str, int] = {}
    all_non_withdrawn: list[str] = []

    for _, row in student_regs.iterrows():
        semester = str(row.get("Semester", "")).strip()
        if not semester or semester.lower() == "nan":
            continue
        reg_status_raw = str(row.get("Registration Status", "")).strip()
        grade_raw = _clean_grade(row.get("Letter Grade"))
        tags = {t.strip().lower() for t in reg_status_raw.split(",")}

        if "withdrawn" in tags or "forced withdraw" in tags or grade_raw == "W":
            continue

        all_non_withdrawn.append(semester)

        if _map_status(reg_status_raw, grade_raw) == "in_progress":
            in_progress_counts[semester] = in_progress_counts.get(semester, 0) + 1

    if in_progress_counts:
        max_count = max(in_progress_counts.values())
        top = [s for s, c in in_progress_counts.items() if c == max_count]
        chosen = _latest_semester(top)
        method = "majority_vote" if max_count > 1 else "single_inprogress"
        logger.info(
            "SCP | current_semester=%r via %s (count=%d)", chosen, method, max_count
        )
        return chosen, method

    if all_non_withdrawn:
        chosen = _latest_semester(all_non_withdrawn)
        logger.info(
            "SCP | current_semester=%r via latest_non_withdrawn (no in-progress rows)",
            chosen,
        )
        return chosen, "latest_non_withdrawn"

    chosen = get_current_semester()
    logger.warning(
        "SCP | current_semester=%r via system_clock (no registrar data)", chosen
    )
    return chosen, "system_clock"


def _compute_regular_semesters(
    student_regs: pd.DataFrame, current_sem: Optional[str]
) -> int:
    """
    Counts distinct Fall and Spring semesters that are complete.
    Excludes the SCP-inferred current semester (not the system clock).

    Rules:
      - Summer semesters excluded entirely (not regular semesters)
      - current_sem excluded — only count completed semesters
      - A semester where ALL registrations were withdrawn does not count
      - A single non-withdrawn row in a completed semester is enough to count it
    """
    valid: set[str] = set()

    for _, row in student_regs.iterrows():
        semester = str(row.get("Semester", "")).strip()
        if not semester or semester.lower() == "nan":
            continue

        # Skip current semester — it is not complete yet
        if current_sem and semester == current_sem:
            continue

        # Skip Summer
        if "Fall" not in semester and "Spring" not in semester:
            continue

        reg_status = str(row.get("Registration Status", "")).strip()
        grade = _clean_grade(row.get("Letter Grade"))
        tags = {t.strip().lower() for t in reg_status.split(",")}

        # Skip withdrawn rows
        if "withdrawn" in tags or "forced withdraw" in tags or grade == "W":
            continue

        valid.add(semester)

    return len(valid)


def _compute_zero_credit_passed(student_regs: pd.DataFrame) -> list[str]:
    """
    Returns unique course codes where the student received a P grade.
    P = pass-only grade for zero-credit mandatory courses.

    De-duplicated: if a student has multiple P records for the same course
    (data entry anomaly), it appears once in the result.

    The orchestrator cross-references this list against KG to determine
    whether all required zero-credit courses have been completed, then
    resolves to a boolean for the ALE graduation audit.
    """
    result: set[str] = set()

    for _, row in student_regs.iterrows():
        grade = _clean_grade(row.get("Letter Grade"))
        if grade == "P":
            code = str(row.get("Course Code", "")).strip()
            if code and code.lower() != "nan":
                result.add(code)

    return sorted(list(result))


def _get_study_status(row) -> str:
    """
    Safely extract and normalize Study Status field from Excel row.
    Returns "Studying" as default for blank/missing/NaN cells.

    Handles the edge case where pandas returns NaN for blank cells,
    and str(NaN) produces the string "nan", not None.
    """
    raw = str(row.get("Study Status", "") or "").strip()
    if not raw or raw.lower() in ("nan", ""):
        return "Studying"
    return raw


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
      - Unsupported/unknown programs → track_id=None, track_status="unsupported"
      - Invalid/blank level → level=None (ALEAdapter returns cannot_compute)
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

    # Level: do not silently default invalid/blank level to Freshman.
    # ALEAdapter._map_student_level returns None for invalid level, and
    # the ALE function returns cannot_compute / invalid_student_level.
    level_raw = str(r.get("Level", "") or "").strip().lower()
    level = LEVEL_MAP.get(level_raw)
    if level is None:
        logger.warning(
            "SCP | %s: invalid or blank level=%r — setting level=None",
            student_id, level_raw or "(blank)",
        )

    # --- Registration rows for this student ---
    student_regs = _df_reg[
        _df_reg["ID"].astype(str) == str(student_id)
    ].copy()

    # --- Global current semester (computed once at load_excel() time) ---
    # Falls back to system clock only when SCP was loaded without real data
    # (e.g. unit tests that inject synthetic DataFrames without calling load_excel).
    current_semester_val = (
        _global_current_semester
        if _global_current_semester is not None
        else get_current_semester()
    )

    # --- Derived fields from raw registrations (BEFORE status mapping) ---
    retake_count               = _compute_retake_count(student_regs)
    total_improve_retakes_used = _compute_improve_retakes(student_regs)
    # Always exclude the global current semester from the completed count.
    # If _global_current_semester is None (synthetic tests), nothing is excluded.
    completed_regular_semesters = _compute_regular_semesters(
        student_regs,
        _global_current_semester,
    )
    zero_credit_courses_passed = _compute_zero_credit_passed(student_regs)

    # --- Build course_history and outcome tracking ---
    course_history: list[CourseRecord] = []

    # best_outcome: highest-priority outcome per course (detects if ever passed)
    best_outcome: dict[str, str] = {}

    # latest_meaningful_status: most recent NON-WITHDRAWN outcome per course.
    # Used for failed_courses: "latest meaningful state is failed"
    latest_meaningful_status: dict[str, str] = {}

    for _, reg in student_regs.iterrows():
        code = str(reg.get("Course Code", "")).strip()
        if not code or code.lower() == "nan":
            continue

        reg_status = str(reg.get("Registration Status", "")).strip()
        grade      = _clean_grade(reg.get("Letter Grade"))
        semester   = str(reg.get("Semester", "")).strip()

        status = _map_status(reg_status, grade)

        # credit_hours=None: credits are not authoritative from registrar data.
        # Orchestrator/KG must patch via course_credit_lookup before ALE calls.
        course_history.append(CourseRecord(
            course_code=code,
            credit_hours=None,
            grade=grade,
            semester_taken=semester,
            status=status,
        ))

        # Track best outcome (passed/repeated win over everything)
        current = best_outcome.get(code, "withdrawn")
        if _OUTCOME_PRIORITY.get(status, 0) > _OUTCOME_PRIORITY.get(current, 0):
            best_outcome[code] = status

        # Track latest meaningful (non-withdrawn) outcome for failed detection
        if status != "withdrawn":
            latest_meaningful_status[code] = status

    # --- Derive course lists ---
    # completed: ever achieved passed or repeated (best historical outcome)
    completed = [c for c, s in best_outcome.items() if s in ("passed", "repeated")]
    completed_set = set(completed)

    # failed: latest meaningful state is "failed" AND course was never completed
    # Using latest_meaningful_status (not best_outcome) ensures a later failed
    # attempt is not hidden behind an older incomplete.
    failed = [
        c for c, s in latest_meaningful_status.items()
        if s == "failed" and c not in completed_set
    ]

    # in_progress: student's active blank-registered rows in the global current
    # semester only.  Old blank rows from completed semesters, I-grade rows, and
    # rows with terminal tags (Succeeded/Failed/Withdrawn) are explicitly excluded.
    # When _global_current_semester is None (synthetic tests without load_excel),
    # in_progress_courses is always empty.
    in_progress_set: set[str] = set()
    if _global_current_semester:
        for _, reg in student_regs.iterrows():
            semester = str(reg.get("Semester", "")).strip()
            if semester != _global_current_semester:
                continue
            reg_status = str(reg.get("Registration Status", "")).strip()
            grade = _clean_grade(reg.get("Letter Grade"))
            if _is_active_blank(reg_status, grade):
                code = str(reg.get("Course Code", "")).strip()
                if code and code.lower() != "nan":
                    in_progress_set.add(code)

    in_progress = sorted(list(in_progress_set))

    # --- Track normalization ---
    program_str = str(r.get("Program", "") or "").strip()
    track_id, track_status, track_error_code = _parse_track(program_str)

    ctx = StudentContext(
        student_id=str(r["ID"]),
        name=str(r.get("Name", "")).strip(),
        program=program_str,
        track_id=track_id,
        track_status=track_status,
        track_error_code=track_error_code,
        level=level,
        first_semester=str(r.get("First Semester", "")).strip(),
        study_status=_get_study_status(r),
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
        current_semester=current_semester_val,
        zero_credit_courses_passed=zero_credit_courses_passed,
        retake_count=retake_count,
        total_improve_retakes_used=total_improve_retakes_used,
    )

    logger.info(
        "SCP | %s | track=%s track_status=%s level=%s cgpa=%s study_status=%s "
        "completed=%d failed=%d in_progress=%d "
        "regular_sems=%d current_sem=%r improve_retakes=%d zero_credit=%d",
        student_id,
        ctx.track_id, ctx.track_status, ctx.level, ctx.cgpa, ctx.study_status,
        len(ctx.completed_courses), len(ctx.failed_courses),
        len(ctx.in_progress_courses), ctx.completed_regular_semesters,
        ctx.current_semester, ctx.total_improve_retakes_used,
        len(ctx.zero_credit_courses_passed),
    )
    return ctx
