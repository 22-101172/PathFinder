import numpy as np
import pandas as pd

from sae.data_loader import load_data

# EUI grading scale — must match cgpa_calculator.GRADE_POINTS
GRADE_POINTS: dict[str, float] = {
    "A+": 4.0, "A": 3.7, "A-": 3.4,
    "B+": 3.2, "B": 3.0, "B-": 2.8,
    "C+": 2.6, "C": 2.4, "C-": 2.2,
    "D+": 2.0, "D": 1.5, "D-": 1.0,
    "F":  0.0,
}

# Credits expected at end of each level — kept for reference / backward compat
# Do NOT use for pace ratio or credits-behind; use semester-based expected instead.
LEVEL_EXPECTED_CREDITS: dict[str, int] = {
    "Freshman":  33,
    "Sophomore": 66,
    "Junior":    99,
    "Senior":    133,
}

_SEASON_ORDER = {"Spring": 0, "Summer": 1, "Fall": 2}

_CREDITS_PER_SEMESTER = 16.625  # 133 / 8 regular (Fall/Spring) semesters


def _semester_to_ordinal(sem: str) -> int:
    """'Fall 2022' -> sortable integer (year * 3 + season index)."""
    parts = sem.strip().split()
    season, year = parts[0], int(parts[1])
    return year * 3 + _SEASON_ORDER.get(season, 0)


def _gpa_slope(regs: pd.DataFrame, debug_id: "str | None" = None) -> float:
    """
    Linear slope of average grade-points across semesters.
    Positive = improving, negative = declining, 0 = stable or insufficient data.
    Uses unweighted per-semester average (course credits not available).

    The most recent semester is excluded if it contains ANY ungraded courses
    (i.e. the current in-progress semester), preventing a false low data point.
    """
    df = regs[["Semester", "Letter Grade"]].copy()
    df["gp"] = df["Letter Grade"].map(GRADE_POINTS)
    df = df.dropna(subset=["Semester"])
    if df.empty:
        return 0.0

    df["sem_ord"] = df["Semester"].apply(_semester_to_ordinal)

    # Exclude any semester that has ANY ungraded course (null, Con, I, W, etc.).
    ungraded_sems = set(df[df["gp"].isna()]["sem_ord"].unique())
    df = df[~df["sem_ord"].isin(ungraded_sems)]

    df = df.dropna(subset=["gp"])
    if df.empty:
        return 0.0

    sem_gpa = df.groupby("sem_ord")["gp"].mean()

    # Restrict to the most recent 3 semesters with actual grades.
    if len(sem_gpa) > 3:
        sem_gpa = sem_gpa.iloc[-3:]

    if debug_id:
        print(f"[DEBUG {debug_id}] per-semester GPA for slope: {dict(sem_gpa)}")

    if len(sem_gpa) < 2:
        return 0.0

    x = sem_gpa.index.values.astype(float)
    y = sem_gpa.values
    x_c = x - x.mean()
    denom = (x_c ** 2).sum()
    if denom == 0:
        return 0.0
    slope = float((x_c * (y - y.mean())).sum() / denom)
    return round(slope, 4)


def _count_completed_fall_spring(regs: pd.DataFrame) -> int:
    """
    Count Fall/Spring semesters that are fully graded (not in-progress).

    A semester is considered in-progress if it has ANY ungraded course
    (letter grade is null, W, I, Abs, Con, or not in GRADE_POINTS).
    """
    if regs.empty or "Semester" not in regs.columns:
        return 0

    sems = regs["Semester"].dropna().unique().tolist()
    if not sems:
        return 0

    current_sem = max(sems, key=_semester_to_ordinal)

    # A semester is in-progress if it has any ungraded entry
    def _is_in_progress(sem: str) -> bool:
        rows = regs[regs["Semester"] == sem]
        grades = rows["Letter Grade"].astype(str).str.strip().str.upper()
        return any(g not in GRADE_POINTS and g not in ("W", "P") for g in grades)

    current_in_progress = _is_in_progress(current_sem)

    count = 0
    for sem in sems:
        parts = str(sem).strip().split()
        if len(parts) < 2 or parts[0] not in ("Fall", "Spring"):
            continue
        if current_in_progress and sem == current_sem:
            continue
        count += 1

    return count


def _expected_credits_by_semesters(regs: pd.DataFrame) -> float:
    """
    Semester-based expected credits: completed_fall_spring_semesters × 16.625, capped at 133.
    """
    n = _count_completed_fall_spring(regs)
    return min(133.0, n * _CREDITS_PER_SEMESTER)


def _credit_pace_ratio(student: pd.Series, regs: pd.DataFrame) -> "float | None":
    """Cumulative PHs / semester-based expected credits."""
    expected = _expected_credits_by_semesters(regs)
    if expected <= 0:
        return None
    cum_phs = student["Cumulative PHs"] or 0.0
    return round(float(cum_phs) / expected, 4)


def _failed_core_rate(regs: pd.DataFrame) -> float:
    """Fraction of first-attempt registrations where the final grade is F (retakes excluded)."""
    if regs.empty:
        return 0.0
    if "Registration Status" in regs.columns:
        first = regs[~regs["Registration Status"].fillna("").str.contains("Repeat|Improve", case=False, regex=True)]
    else:
        first = regs
    if first.empty:
        return 0.0
    fails = (first["Letter Grade"].astype(str).str.strip().str.upper() == "F").sum()
    return round(float(fails / len(first)), 4)


def _repeat_course_rate(regs: pd.DataFrame) -> float:
    """Fraction of distinct courses the student has repeated or improved."""
    if regs.empty:
        return 0.0
    status = regs["Registration Status"].fillna("")
    is_repeat = status.str.contains("Repeat|Improve", case=False, regex=True)
    repeats = regs.loc[is_repeat, "Course Code"].nunique()
    total_distinct = regs["Course Code"].nunique()
    if total_distinct == 0:
        return 0.0
    return round(float(repeats / total_distinct), 4)


def _warning_risk(student: pd.Series) -> int:
    """1 if the student has an active consecutive academic warning."""
    consec = student["Consecutive Warning"] or 0
    return 1 if consec > 0 else 0


def _pass_rate(regs: pd.DataFrame) -> float:
    """First-attempt pass rate: fraction of graded first-attempt courses that are not F (retakes excluded)."""
    if regs.empty:
        return 1.0
    if "Registration Status" in regs.columns:
        first = regs[~regs["Registration Status"].fillna("").str.contains("Repeat|Improve", case=False, regex=True)]
    else:
        first = regs
    if first.empty:
        return 1.0
    grades  = first["Letter Grade"].astype(str).str.strip().str.upper()
    graded  = grades.map(GRADE_POINTS).notna()
    if not graded.any():
        return 1.0
    passed  = (grades[graded] != "F").sum()
    return round(float(passed / int(graded.sum())), 4)


def compute_student_features(student: pd.Series, regs: pd.DataFrame) -> dict:
    """Return feature dict for a single student row + their registrations slice."""
    sid = str(student["ID"])
    debug = sid if sid == "STU000124" else None
    expected_credits = _expected_credits_by_semesters(regs)
    return {
        "ID":                  student["ID"],
        "gpa_slope":           _gpa_slope(regs, debug_id=debug),
        "credit_pace_ratio":   _credit_pace_ratio(student, regs),
        "expected_credits":    expected_credits,
        "failed_core_rate":    _failed_core_rate(regs),
        "repeat_course_rate":  _repeat_course_rate(regs),
        "warning_risk":        _warning_risk(student),
        "pass_rate":           _pass_rate(regs),
        "is_graduated":        1 if student["Study Status"] == "Graduated" else 0,
        "Cumulative GPA":      student.get("Cumulative GPA"),
    }


def build_features(
    students_df: pd.DataFrame,
    registrations_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute the full feature matrix for every student.

    Returns a DataFrame indexed by ID with one row per student.
    """
    reg_by_id = {
        sid: grp
        for sid, grp in registrations_df.groupby("ID", sort=False)
    }
    empty = pd.DataFrame(columns=registrations_df.columns)

    records = [
        compute_student_features(row, reg_by_id.get(row["ID"], empty))
        for _, row in students_df.iterrows()
    ]

    return pd.DataFrame(records).set_index("ID")
