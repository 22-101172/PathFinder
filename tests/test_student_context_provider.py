"""
Unit and integration tests for gateway.student_context_provider.

Tests 1–11 use synthetic DataFrames injected directly into the module
globals — no real Excel file dependency.
Test 12 uses the real students_anonymous.xlsx (STU000001).
"""

import math
import re

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
