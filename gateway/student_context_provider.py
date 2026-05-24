from __future__ import annotations
import logging
import os
from typing import Optional

import pandas as pd

from gateway.models.schemas import CourseRecord, StudentContext

logger = logging.getLogger(__name__)

PROGRAM_TO_TRACK = {
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

LEVEL_MAP = {
    "freshman": 1,
    "sophomore": 2,
    "junior": 3,
    "senior": 4,
}

CURRENT_SEMESTER = "Spring 2026"

_df_data: Optional[pd.DataFrame] = None
_df_reg: Optional[pd.DataFrame] = None


def load_excel(path: str) -> None:
    global _df_data, _df_reg
    logger.info("StudentContextProvider: loading %s", path)
    _df_data = pd.read_excel(path, sheet_name="data")
    _df_reg = pd.read_excel(path, sheet_name="registrations")
    logger.info("StudentContextProvider: %d students, %d registration rows", len(_df_data), len(_df_reg))


def _parse_track(program_str: str) -> str:
    if not program_str:
        return "General"
    lower = program_str.lower()
    for key, track_id in PROGRAM_TO_TRACK.items():
        if key in lower:
            return track_id
    return "General"


def _compute_standing(cgpa: Optional[float], consecutive: int, total: int) -> str:
    if consecutive >= 4 or total >= 6:
        return "dismissed"
    if cgpa is not None and cgpa < 2.0:
        return "warning"
    return "good"


def _map_status(reg_status: str, grade: Optional[str]) -> str:
    tags = {t.strip().lower() for t in reg_status.split(",")}
    no_grade = not grade or str(grade).strip() in ("", "nan")

    if no_grade or (grade and str(grade).strip() == "Con"):
        return "in_progress"
    if "withdrawn" in tags or "forced withdraw" in tags:
        return "withdrawn"
    if grade and str(grade).strip() == "Abs":
        return "failed"
    if "failed" in tags:
        return "failed"
    if "repeat" in tags and "succeeded" in tags:
        return "repeated"
    if "improve" in tags and "succeeded" in tags:
        return "passed"
    if "succeeded" in tags:
        return "passed"
    return "failed"


def _safe_float(val) -> Optional[float]:
    try:
        v = float(val)
        return None if pd.isna(v) else round(v, 3)
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> Optional[int]:
    try:
        v = float(val)
        return None if pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return None


def get_context(student_id: str) -> Optional[StudentContext]:
    if _df_data is None or _df_reg is None:
        raise RuntimeError("Call load_excel() before get_context()")

    row = _df_data[_df_data["ID"].astype(str) == str(student_id)]
    if row.empty:
        logger.warning("StudentContextProvider: student %s not found", student_id)
        return None

    r = row.iloc[0]

    cgpa = _safe_float(r.get("Cumulative GPA"))
    consecutive = _safe_int(r.get("Consecutive Warning")) or 0
    total_warnings = _safe_int(r.get("Total Warnings")) or 0

    mil_raw = str(r.get("Military Status", "") or "").strip()
    military_status = None if not mil_raw or mil_raw.lower() in ("nan", "") else mil_raw

    student_regs = _df_reg[_df_reg["ID"].astype(str) == str(student_id)].copy()

    course_history: list[CourseRecord] = []
    best_outcome: dict[str, str] = {}

    for _, reg in student_regs.iterrows():
        code = str(reg.get("Course Code", "")).strip()
        if not code or code.lower() == "nan":
            continue
        reg_status = str(reg.get("Registration Status", "")).strip()
        grade_raw = reg.get("Letter Grade")
        grade = None if pd.isna(grade_raw) else str(grade_raw).strip()
        if grade and grade.lower() == "nan":
            grade = None
        semester = str(reg.get("Semester", "")).strip()

        status = _map_status(reg_status, grade)

        record = CourseRecord(
            course_code=code,
            credit_hours=3,
            grade=grade,
            semester_taken=semester,
            status=status,
        )
        course_history.append(record)

        current_best = best_outcome.get(code)
        if status in ("passed", "repeated"):
            best_outcome[code] = status
        elif status == "failed" and current_best not in ("passed", "repeated"):
            best_outcome[code] = "failed"
        elif status == "in_progress" and current_best is None:
            best_outcome[code] = "in_progress"

    completed = [c for c, s in best_outcome.items() if s in ("passed", "repeated")]
    failed = [c for c, s in best_outcome.items() if s == "failed"]
    in_progress = [c for c, s in best_outcome.items() if s == "in_progress"]

    ctx = StudentContext(
        student_id=str(r["ID"]),
        name=str(r.get("Name", "")).strip(),
        program=str(r.get("Program", "")).strip(),
        track_id=_parse_track(str(r.get("Program", ""))),
        level=LEVEL_MAP.get(str(r.get("Level", "")).strip().lower(), 1),
        first_semester=str(r.get("First Semester", "")).strip(),
        study_status=str(r.get("Study Status", "Studying")).strip(),
        current_semester=CURRENT_SEMESTER,
        military_status=military_status,
        cgpa=cgpa,
        academic_standing=_compute_standing(cgpa, consecutive, total_warnings),
        last_semester_gpa=_safe_float(r.get("Last Semester GPA")),
        total_credit_hours_earned=_safe_int(r.get("Cumulative PHs")) or 0,
        cumulative_chs=_safe_int(r.get("Cumulative CHs")),
        cumulative_cps=_safe_float(r.get("Cumulative CPs")),
        last_semester_chs=_safe_int(r.get("Last Semester CHs")),
        last_semester_cps=_safe_float(r.get("Last Semester CPs")),
        last_semester_phs=_safe_int(r.get("Last Semester PHs")),
        current_semester_chs=_safe_int(r.get("Current Semester CHs")) or 0,
        consecutive_warnings=consecutive,
        total_warnings=total_warnings,
        last_semester_warning=_safe_int(r.get("Last Semester Warning")),
        course_history=course_history,
        completed_courses=completed,
        failed_courses=failed,
        in_progress_courses=in_progress,
        planned_courses=[],
    )

    logger.info(
        "StudentContextProvider: %s | track=%s level=%d cgpa=%s standing=%s completed=%d",
        student_id, ctx.track_id, ctx.level, ctx.cgpa, ctx.academic_standing, len(ctx.completed_courses)
    )
    return ctx
