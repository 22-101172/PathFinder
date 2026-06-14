"""
Unit and integration tests for gateway.student_context_provider.

Tests 1–11 use synthetic DataFrames injected directly into the module
globals — no real Excel file dependency.
Test 12 uses the real students_anonymous.xlsx (STU000001).
Tests 13–22 are regression tests for bug fixes (SCP data-correctness pass).
"""

import io
import math
import re
from unittest.mock import patch

import openpyxl
import pandas as pd
import pytest

import gateway.student_context_provider as scp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REG_COLS = ["ID", "Semester", "Course Code", "Registration Status", "Letter Grade"]


def _inject(data_rows: list[dict], reg_rows: list[dict]) -> None:
    """Inject synthetic DataFrames into the SCP module globals."""
    scp._df_data = pd.DataFrame(data_rows)
    scp._df_reg  = (
        pd.DataFrame(reg_rows)
        if reg_rows
        else pd.DataFrame(columns=_REG_COLS)
    )


_BASE_DATA = {
    "ID": "TST001",
    "Name": "Test Student",
    "Program": "CIS Program - Artificial Intelligence Track",
    "First Semester": "Fall 2022",
    "Military Status": "Not Yet Deferred",
    "Level": "Senior",
    "Study Status": "Studying",
    "Consecutive Warning": float("nan"),
    "Total Warnings": float("nan"),
    "Last Semester Warning": float("nan"),
    "Last Semester GPA": 3.0,
    "Last Semester CHs": 15.0,
    "Last Semester CPs": 45.0,
    "Last Semester PHs": 15.0,
    "Cumulative GPA": 3.0,
    "Cumulative CHs": 90.0,
    "Cumulative CPs": 270.0,
    "Cumulative PHs": 90.0,
    "Current Semester CHs": 18,
}

def _reg(course_code, reg_status, grade, semester="Fall 2023"):
    return {
        "ID": "TST001",
        "Semester": semester,
        "Course Code": course_code,
        "Registration Status": reg_status,
        "Letter Grade": grade,
    }


# ---------------------------------------------------------------------------
# Test 1 — Student not found
# ---------------------------------------------------------------------------

def test_student_not_found_returns_none():
    _inject([_BASE_DATA], [])
    result = scp.get_context("NONEXISTENT")
    assert result is None


# ---------------------------------------------------------------------------
# Test 2 — Zero registration rows
# ---------------------------------------------------------------------------

def test_zero_registrations_returns_empty_context():
    _inject([_BASE_DATA], [])
    ctx = scp.get_context("TST001")
    assert ctx is not None
    assert ctx.completed_courses == []
    assert ctx.failed_courses == []
    assert ctx.in_progress_courses == []
    assert ctx.course_history == []
    assert ctx.retake_count == {}
    assert ctx.total_improve_retakes_used == 0
    assert ctx.completed_regular_semesters == 0
    assert ctx.zero_credit_courses_passed == []


# ---------------------------------------------------------------------------
# Test 3 — Con grade
# ---------------------------------------------------------------------------

def test_con_grade_treated_as_passed():
    _inject(
        [_BASE_DATA],
        [_reg("C-CS495", "Continuing, Registered", "Con")],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    record = next(r for r in ctx.course_history if r.course_code == "C-CS495")
    assert record.status == "passed"
    assert "C-CS495" in ctx.completed_courses
    assert "C-CS495" not in ctx.in_progress_courses


# ---------------------------------------------------------------------------
# Test 4 — I grade (incomplete)
# ---------------------------------------------------------------------------

def test_i_grade_treated_as_incomplete():
    _inject(
        [_BASE_DATA],
        [_reg("C-CS435", "Registered", "I", semester="Fall 2022")],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    record = next(r for r in ctx.course_history if r.course_code == "C-CS435")
    assert record.status == "incomplete"
    assert "C-CS435" in ctx.in_progress_courses
    assert ctx.retake_count.get("C-CS435", 0) == 1


# ---------------------------------------------------------------------------
# Test 5 — Withdrawn (W grade and Withdrawn tag)
# ---------------------------------------------------------------------------

def test_withdrawn_w_grade_excluded_from_all_lists():
    _inject(
        [_BASE_DATA],
        [_reg("C-MA112", "Registered", "W")],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    record = next(r for r in ctx.course_history if r.course_code == "C-MA112")
    assert record.status == "withdrawn"
    assert "C-MA112" not in ctx.completed_courses
    assert "C-MA112" not in ctx.failed_courses
    assert "C-MA112" not in ctx.in_progress_courses
    assert ctx.retake_count.get("C-MA112", 0) == 0


def test_withdrawn_tag_excluded_from_all_lists():
    _inject(
        [_BASE_DATA],
        [_reg("C-MA112", "Withdrawn, Registered", None)],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    record = next(r for r in ctx.course_history if r.course_code == "C-MA112")
    assert record.status == "withdrawn"
    assert "C-MA112" not in ctx.completed_courses
    assert "C-MA112" not in ctx.failed_courses
    assert "C-MA112" not in ctx.in_progress_courses
    assert ctx.retake_count.get("C-MA112", 0) == 0


# ---------------------------------------------------------------------------
# Test 6 — P grade (zero-credit pass)
# ---------------------------------------------------------------------------

def test_p_grade_passed_and_in_zero_credit_list():
    _inject(
        [_BASE_DATA],
        [_reg("HUM011", "Succeeded, Registered", "P")],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    record = next(r for r in ctx.course_history if r.course_code == "HUM011")
    assert record.status == "passed"
    assert "HUM011" in ctx.completed_courses
    assert "HUM011" in ctx.zero_credit_courses_passed


# ---------------------------------------------------------------------------
# Test 7 — Improve tag counted in total_improve_retakes_used
# ---------------------------------------------------------------------------

def test_improve_tag_counted_in_improve_retakes():
    _inject(
        [_BASE_DATA],
        [
            _reg("C-CS111", "Fresh, Succeeded, Registered", "B", semester="Fall 2022"),
            _reg("C-CS111", "Improve, Succeeded, Registered", "A-", semester="Spring 2023"),
        ],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    assert ctx.total_improve_retakes_used == 1


# ---------------------------------------------------------------------------
# Test 8 — Only-withdrawn semester not counted
# ---------------------------------------------------------------------------

def test_all_withdrawn_semester_not_counted():
    _inject(
        [_BASE_DATA],
        [
            _reg("C-CS111", "Fresh, Succeeded, Registered", "B", semester="Spring 2023"),
            _reg("C-MA111", "Withdrawn, Registered", None, semester="Fall 2022"),
        ],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    # Fall 2022 has only withdrawn rows — should not be counted
    # Spring 2023 has a valid row — should be counted
    assert ctx.completed_regular_semesters == 1


# ---------------------------------------------------------------------------
# Test 9 — Summer not counted in completed_regular_semesters
# ---------------------------------------------------------------------------

def test_summer_not_counted_in_regular_semesters():
    _inject(
        [_BASE_DATA],
        [
            _reg("C-CS111", "Fresh, Succeeded, Registered", "B", semester="Fall 2022"),
            _reg("C-MA111", "Fresh, Succeeded, Registered", "C", semester="Spring 2023"),
            _reg("C-PH111", "Fresh, Succeeded, Registered", "B+", semester="Summer 2023"),
        ],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    assert ctx.completed_regular_semesters == 2


# ---------------------------------------------------------------------------
# Test 10 — Best outcome priority (failed then passed → completed)
# ---------------------------------------------------------------------------

def test_best_outcome_failed_then_passed():
    _inject(
        [_BASE_DATA],
        [
            _reg("C-CS213", "Fresh, Failed, Registered", "F", semester="Fall 2022"),
            _reg("C-CS213", "Repeat, Succeeded, Registered", "B", semester="Spring 2023"),
        ],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    assert "C-CS213" in ctx.completed_courses
    assert "C-CS213" not in ctx.failed_courses


# ---------------------------------------------------------------------------
# Test 11 — get_current_semester() format
# ---------------------------------------------------------------------------

def test_get_current_semester_format():
    result = scp.get_current_semester()
    assert isinstance(result, str)
    pattern = r"^(Fall|Spring|Summer) \d{4}$"
    assert re.match(pattern, result), f"Unexpected format: {result!r}"


# ---------------------------------------------------------------------------
# Test 12 — Real data: STU000001
# ---------------------------------------------------------------------------

def test_real_data_stu000001():
    import os
    excel_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "students_anonymous.xlsx"
    )
    scp.load_excel(excel_path)
    ctx = scp.get_context("STU000001")
    assert ctx is not None
    assert ctx.student_id == "STU000001"
    assert len(ctx.completed_courses) > 0
    assert ctx.track_id == "AI"
    assert ctx.level == 4
    assert ctx.cgpa is not None
    assert ctx.cgpa > 0.0


# ---------------------------------------------------------------------------
# Helpers for regression tests
# ---------------------------------------------------------------------------

def _make_excel_bytes(data_rows: list[dict], reg_rows: list[dict]) -> bytes:
    """Build an in-memory .xlsx with 'data' and 'registrations' sheets."""
    wb = openpyxl.Workbook()

    # data sheet
    ws_data = wb.active
    ws_data.title = "data"
    data_cols = [
        "ID", "Name", "Program", "Level", "Study Status",
        "Cumulative GPA", "Consecutive Warning", "Total Warnings",
        "Military Status", "First Semester",
        "Cumulative PHs", "Cumulative CHs", "Cumulative CPs",
        "Last Semester GPA", "Last Semester CHs", "Last Semester CPs",
        "Last Semester PHs", "Last Semester Warning", "Current Semester CHs",
    ]
    ws_data.append(data_cols)
    for row in data_rows:
        ws_data.append([row.get(c, "") for c in data_cols])

    # registrations sheet
    ws_reg = wb.create_sheet("registrations")
    reg_cols = ["ID", "Course Code", "Semester", "Registration Status", "Letter Grade"]
    ws_reg.append(reg_cols)
    for row in reg_rows:
        ws_reg.append([row.get(c, "") for c in reg_cols])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Test 13 — completed_regular_semesters excludes current semester
# ---------------------------------------------------------------------------

def test_completed_regular_semesters_excludes_current_semester():
    current_sem = scp.get_current_semester()
    _inject(
        [_BASE_DATA],
        [
            _reg("C-CS111", "Fresh, Succeeded, Registered", "B", semester="Fall 2024"),
            _reg("C-CS112", "Fresh, Succeeded, Registered", "B", semester="Spring 2025"),
            _reg("C-CS113", "Registered", None, semester=current_sem),
        ],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    assert ctx.completed_regular_semesters == 2, (
        f"Expected 2 (current semester excluded), got {ctx.completed_regular_semesters}"
    )


# ---------------------------------------------------------------------------
# Test 14 — _map_status: Failed tag + blank grade → "failed", not "in_progress"
# ---------------------------------------------------------------------------

def test_map_status_failed_tag_with_blank_grade():
    result = scp._map_status("Failed, Registered", None)
    assert result == "failed", (
        f"Expected 'failed' for Failed tag + blank grade, got {result!r}"
    )


# ---------------------------------------------------------------------------
# Test 15 — in_progress_courses shows current retakes of previously passed course
# ---------------------------------------------------------------------------

def test_in_progress_courses_shows_current_retakes():
    current_sem = scp.get_current_semester()
    _inject(
        [_BASE_DATA],
        [
            _reg("C-AI321", "Fresh, Succeeded, Registered", "B+", semester="Fall 2024"),
            _reg("C-AI321", "Improve, Registered", None, semester=current_sem),
        ],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    assert "C-AI321" in ctx.completed_courses, "Expected C-AI321 in completed_courses"
    assert "C-AI321" in ctx.in_progress_courses, (
        "Expected C-AI321 in in_progress_courses (current retake)"
    )


# ---------------------------------------------------------------------------
# Test 16 — in_progress_courses excludes resolved incomplete
# ---------------------------------------------------------------------------

def test_in_progress_courses_excludes_resolved_incomplete():
    _inject(
        [_BASE_DATA],
        [
            _reg("C-AI321", "Registered", "I", semester="Fall 2024"),
            _reg("C-AI321", "Repeat, Succeeded, Registered", "B", semester="Spring 2025"),
        ],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    assert "C-AI321" in ctx.completed_courses, "Expected C-AI321 in completed_courses"
    assert "C-AI321" not in ctx.in_progress_courses, (
        "Old resolved incomplete must not remain in in_progress_courses"
    )


# ---------------------------------------------------------------------------
# Test 17 — in_progress_courses includes unresolved incomplete
# ---------------------------------------------------------------------------

def test_in_progress_courses_includes_unresolved_incomplete():
    _inject(
        [_BASE_DATA],
        [
            _reg("C-AI321", "Registered", "I", semester="Fall 2024"),
        ],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    assert "C-AI321" in ctx.in_progress_courses, (
        "Unresolved incomplete must appear in in_progress_courses"
    )


# ---------------------------------------------------------------------------
# Test 18 — blank Study Status → "Studying"
# ---------------------------------------------------------------------------

def test_study_status_blank_becomes_studying():
    data_blank = dict(_BASE_DATA)
    data_blank["Study Status"] = ""
    _inject([data_blank], [])
    ctx = scp.get_context("TST001")
    assert ctx is not None
    assert ctx.study_status == "Studying"

    data_nan = dict(_BASE_DATA)
    data_nan["Study Status"] = float("nan")
    _inject([data_nan], [])
    ctx2 = scp.get_context("TST001")
    assert ctx2 is not None
    assert ctx2.study_status == "Studying"


# ---------------------------------------------------------------------------
# Test 19 — zero_credit_courses_passed deduplicated
# ---------------------------------------------------------------------------

def test_zero_credit_passed_deduplicated():
    _inject(
        [_BASE_DATA],
        [
            _reg("C-MANDATORY-001", "Succeeded, Registered", "P"),
            _reg("C-MANDATORY-001", "Succeeded, Registered", "P"),
        ],
    )
    ctx = scp.get_context("TST001")
    assert ctx is not None
    assert ctx.zero_credit_courses_passed.count("C-MANDATORY-001") == 1, (
        "Duplicate P records must be deduplicated"
    )


# ---------------------------------------------------------------------------
# Test 20 — load_excel raises FileNotFoundError for missing file
# ---------------------------------------------------------------------------

def test_load_excel_file_not_found():
    with pytest.raises(FileNotFoundError) as exc_info:
        scp.load_excel("nonexistent_path/students.xlsx")
    assert "not found" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Test 21 — load_excel raises ValueError for missing sheet
# ---------------------------------------------------------------------------

def test_load_excel_missing_sheet(tmp_path):
    # Excel with only a 'data' sheet — 'registrations' is absent
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(["ID"])
    xls_path = tmp_path / "missing_sheet.xlsx"
    wb.save(str(xls_path))

    with pytest.raises(ValueError) as exc_info:
        scp.load_excel(str(xls_path))
    msg = str(exc_info.value).lower()
    assert "sheet" in msg or "registrations" in msg


# ---------------------------------------------------------------------------
# Test 22 — load_excel raises ValueError for missing required columns
# ---------------------------------------------------------------------------

def test_load_excel_missing_required_columns(tmp_path):
    # 'data' sheet is missing the 'ID' column
    wb = openpyxl.Workbook()
    ws_data = wb.active
    ws_data.title = "data"
    ws_data.append(["Name"])  # ID is absent

    ws_reg = wb.create_sheet("registrations")
    ws_reg.append(["ID", "Course Code", "Semester", "Registration Status", "Letter Grade"])

    xls_path = tmp_path / "missing_cols.xlsx"
    wb.save(str(xls_path))

    with pytest.raises(ValueError) as exc_info:
        scp.load_excel(str(xls_path))
    msg = str(exc_info.value)
    assert "ID" in msg or "missing" in msg.lower()
