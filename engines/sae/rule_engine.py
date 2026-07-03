"""
Rule engine for the Student Analysis Engine (SAE).

All decision thresholds live in the ``rules`` dict so they can be replaced at
runtime by values retrieved from a RAG policy document without touching code.

DEFAULT_RULES contains temporary placeholder values for stand-alone testing
only — do not treat them as validated academic policy.

Feature dict contract
---------------------
F1–F3 require only the engineered feature keys produced by
``feature_engineering.compute_student_features``:
    gpa_slope, credit_pace_ratio, failed_core_rate, warning_risk, pass_rate

F4 and F6 also consume raw profile fields (Name, Level, Program, etc.).
Callers should pass a merged dict:
    {**student_row.to_dict(), **compute_student_features(student_row, regs)}

F5 accepts the DataFrame returned by ``feature_engineering.build_features``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Placeholder thresholds — REPLACE via rules injection in production
# ---------------------------------------------------------------------------
DEFAULT_RULES: dict[str, Any] = {
    # F1 – GPA trend
    "gpa_trend_improving_min":   0.05,   # slope > this  -> improving
    "gpa_trend_declining_max":   0.00,   # slope < this  -> declining (any negative slope)

    # F2 – Credit pace
    "credit_pace_behind_max":    0.80,   # ratio < this  -> behind schedule
    "credit_pace_ahead_min":     1.10,   # ratio > this  -> ahead of schedule

    # F3 – At-risk score
    "risk_weight_warning":        40,    # triggered when warning_risk == 1 (alone = moderate)
    "risk_weight_pass_rate":      25,    # triggered when pass_rate < threshold
    "risk_weight_failed_core":    25,    # triggered when failed_core_rate > 0.2
    "risk_weight_gpa_declining":  20,    # triggered when gpa trend is declining
    "risk_pass_rate_threshold":   0.75,  # minimum acceptable pass rate

    # F3 extended – new risk factors
    "risk_weight_consec_warning_bonus": 15,   # 3+ consecutive warnings bonus
    "risk_weight_prereq_blocking":      20,   # prereq blocking 3+ downstream
    "risk_weight_credits_behind":       15,   # credits >20 behind expected
    "risk_weight_chronic_failure":      15,   # same course failed 2+ times unresolved
    "risk_weight_sdi_overload":         10,   # SDI >2.5 when risk already ≥40
    "risk_consec_warning_threshold":    3,    # minimum consecutive warnings for bonus
    "risk_credits_behind_threshold":    20,   # credit gap threshold
    "risk_prereq_blocking_threshold":   3,    # minimum downstream courses blocked
    "risk_chronic_failure_threshold":   2,    # minimum same-course failures
    "risk_sdi_overload_threshold":      2.5,  # SDI threshold

    # F6 – Anomaly detection
    "anomaly_course_repeat_min":    3,   # same course attempted >= N times
    "anomaly_senior_credit_ratio":  0.5, # Senior PHs < Junior_expected * ratio
}

_LEVEL_EXPECTED_CREDITS: dict[str, int] = {
    "Freshman": 33, "Sophomore": 66, "Junior": 99, "Senior": 133,
}

CATEGORY_MAP: dict[str, str] = {
    "C-MA": "Mathematics",
    "C-PH": "Sciences",
    "C-CS": "Computer Science",
    "C-AI": "Artificial Intelligence",
    "C-SW": "Software Engineering",
    "C-DE": "Data Engineering",
    "HUM":  "Humanities & General",
    "C-TR": "Field Training",
    "C-GP": "Graduation Project",
}

SUBJECT_AREAS: dict[str, list[str]] = {
    "Math & Sciences":        ["C-MA", "C-PH", "C-ST"],
    "Computing & Technology": ["C-CS", "C-AI", "C-SW", "C-DE"],
    "Humanities & Electives": ["HUM", "C-TR", "C-GP"],
}


# ---------------------------------------------------------------------------
# F1 – GPA trend
# ---------------------------------------------------------------------------

def f1_gpa_trend(
    student_features: dict,
    rules: dict = DEFAULT_RULES,
) -> str:
    """
    Classify the direction of a student's GPA trajectory.

    Returns
    -------
    'improving' | 'declining' | 'stable'
    """
    slope = float(student_features.get("gpa_slope") or 0.0)
    if slope > rules["gpa_trend_improving_min"]:
        return "improving"
    if slope < rules["gpa_trend_declining_max"]:
        return "declining"
    return "stable"


# ---------------------------------------------------------------------------
# F2 – Credit pace
# ---------------------------------------------------------------------------

def f2_credit_pace(
    student_features: dict,
    rules: dict = DEFAULT_RULES,
) -> str:
    """
    Classify whether the student is on-track, behind, or ahead of the credit
    schedule expected for their current academic level.

    Returns
    -------
    'ahead' | 'on_track' | 'behind' | 'unknown'
    """
    ratio = student_features.get("credit_pace_ratio")
    if ratio is None or (isinstance(ratio, float) and pd.isna(ratio)):
        return "unknown"
    ratio = float(ratio)
    if ratio > rules["credit_pace_ahead_min"]:
        return "ahead"
    if ratio < rules["credit_pace_behind_max"]:
        return "behind"
    return "on_track"


# ---------------------------------------------------------------------------
# F3 – Risk flags (replaces weighted score system)
# ---------------------------------------------------------------------------

def f3_risk_flags(
    student_features: dict,
    rules: dict = DEFAULT_RULES,
) -> dict[str, Any]:
    """
    Derive a concise list of risk flags from a student's academic features.

    Only the two most actionable signals are checked:
    consecutive academic warnings and CGPA relative to the 2.0 minimum.

    Returns
    -------
    dict
        flags      : list[{type, severity, message}]
        risk_level : 'low' | 'moderate' | 'high'
    """
    flags: list[dict] = []

    consec_w = 0
    try:
        consec_w = int(
            student_features.get("Consecutive Warning")
            or student_features.get("consecutive_warning")
            or 0
        )
    except (TypeError, ValueError):
        pass

    if consec_w >= 4:
        flags.append({
            "type": "dismissal_active",
            "severity": "critical",
            "message": (
                f"{consec_w} consecutive academic warnings — program dismissal is in effect. "
                "Faculty council approval required to continue."
            ),
        })
    elif consec_w == 3:
        flags.append({
            "type": "dismissal_risk",
            "severity": "critical",
            "message": "3 consecutive academic warnings — one more warning results in automatic program dismissal.",
        })
    elif consec_w in (1, 2):
        flags.append({
            "type": "active_warning",
            "severity": "high",
            "message": f"{consec_w} consecutive academic warning{'s' if consec_w > 1 else ''} on record.",
        })

    official_cgpa: "float | None" = None
    try:
        v = (
            student_features.get("Cumulative GPA")
            or student_features.get("official_cgpa")
        )
        if v is not None:
            f = float(v)
            if not pd.isna(f):
                official_cgpa = f
    except (TypeError, ValueError):
        pass

    if official_cgpa is not None:
        if official_cgpa < 2.0:
            flags.append({
                "type": "cgpa_below_minimum",
                "severity": "critical",
                "message": (
                    f"CGPA {official_cgpa:.2f} is below the 2.0 minimum required "
                    "for graduation and graduation project registration."
                ),
            })
        elif 2.0 <= official_cgpa < 2.5:
            flags.append({
                "type": "cgpa_approaching_minimum",
                "severity": "high",
                "message": (
                    f"CGPA {official_cgpa:.2f} needs attention — below the 2.5 recommended threshold."
                ),
            })

    if any(f["severity"] == "critical" for f in flags):
        risk_level = "high"
    elif any(f["severity"] == "high" for f in flags):
        risk_level = "moderate"
    else:
        risk_level = "low"

    return {"flags": flags, "risk_level": risk_level}


# Keep legacy alias so any external callers don't break immediately
def f3_at_risk_score(student_features: dict, rules: dict = DEFAULT_RULES, **_kwargs) -> dict[str, Any]:
    result = f3_risk_flags(student_features, rules)
    return {"score": 0, "breakdown": {}, "risk_level": result["risk_level"]}


# ---------------------------------------------------------------------------
# F4 – Academic history summary
# ---------------------------------------------------------------------------

def f4_academic_summary(
    student_features: dict,
    rules: dict = DEFAULT_RULES,
) -> dict[str, Any]:
    """
    Return a clean, human-readable summary of a student's full academic profile.

    Caller responsibility
    --------------------
    ``student_features`` must be a merged dict of the raw student profile row
    *and* the engineered features, e.g.:
        {**student_row.to_dict(), **compute_student_features(student_row, regs)}

    Raw profile fields consumed: ID, Name, Program, Level, Study Status,
    Military Status, Current Semester CHs, Cumulative GPA, Last Semester GPA.
    """
    risk = f3_risk_flags(student_features, rules)

    return {
        "id":                   student_features.get("ID"),
        "name":                 student_features.get("Name"),
        "program":              student_features.get("Program"),
        "level":                student_features.get("Level"),
        "study_status":         student_features.get("Study Status"),
        "military_status":      student_features.get("Military Status"),
        "current_semester_chs": student_features.get("Current Semester CHs"),
        "cumulative_gpa":       student_features.get("Cumulative GPA"),
        "last_semester_gpa":    student_features.get("Last Semester GPA"),
        "gpa_trend":            f1_gpa_trend(student_features, rules),
        "credit_pace":          f2_credit_pace(student_features, rules),
        "risk_flags":           risk["flags"],
        "risk_level":           risk["risk_level"],
        "pass_rate":            student_features.get("pass_rate"),
        "failed_core_rate":     student_features.get("failed_core_rate"),
        "repeat_course_rate":   student_features.get("repeat_course_rate"),
        "is_graduated":         bool(student_features.get("is_graduated", 0)),
    }


# ---------------------------------------------------------------------------
# F5 – Advisor overview (all students, sorted by risk)
# ---------------------------------------------------------------------------

def f5_advisor_overview(
    all_student_features: "pd.DataFrame | list[dict]",
    rules: dict = DEFAULT_RULES,
) -> list[dict[str, Any]]:
    """
    Run F1–F3 for every student and return them sorted by risk score descending.

    Parameters
    ----------
    all_student_features
        Either the DataFrame returned by ``build_features()`` (indexed by ID)
        or a list of feature dicts each containing an 'ID' key.

    Returns
    -------
    List of dicts ordered highest-risk first, each containing:
        id, risk_score, risk_level, risk_breakdown, gpa_trend, credit_pace
    """
    if isinstance(all_student_features, pd.DataFrame):
        rows: list[dict] = [
            {"ID": idx, **row.to_dict()}
            for idx, row in all_student_features.iterrows()
        ]
    else:
        rows = list(all_student_features)

    results: list[dict[str, Any]] = []
    for feats in rows:
        risk = f3_risk_flags(feats, rules)
        results.append({
            "id":          feats.get("ID"),
            "risk_flags":  risk["flags"],
            "risk_level":  risk["risk_level"],
            "gpa_trend":   f1_gpa_trend(feats, rules),
            "credit_pace": f2_credit_pace(feats, rules),
        })

    _rl_order = {"high": 0, "moderate": 1, "low": 2}
    results.sort(key=lambda r: _rl_order.get(r["risk_level"], 3))
    return results


# ---------------------------------------------------------------------------
# F6 – Anomaly detection
# ---------------------------------------------------------------------------

def f6_anomaly_detection(
    student_features: dict,
    registrations: "pd.DataFrame | None" = None,
    rules: dict = DEFAULT_RULES,
) -> dict[str, Any]:
    """
    Flag unusual academic patterns that warrant advisor attention.

    Flags
    -----
    repeated_course
        Student attempted the same course >= ``anomaly_course_repeat_min`` times.
        Requires ``registrations`` DataFrame to be passed.

    undercredited_senior
        Student is a Senior but has fewer passed hours than
        ``Junior_expected * anomaly_senior_credit_ratio`` (default: 90 * 0.5 = 45).
        Derived from ``credit_pace_ratio`` — no raw PHs column required.

    silent_decline
        GPA trend is declining yet no academic warning has been recorded.
        This student may be slipping through without advisor intervention.

    Parameters
    ----------
    student_features
        Merged profile + engineered feature dict (same contract as F4).
        Level and credit_pace_ratio must be present for undercredited_senior.
    registrations
        The student's slice of the registrations DataFrame (optional).
        Needed only for the repeated_course check.

    Returns
    -------
    dict
        flagged  : bool          – True if any flag is raised
        flags    : list[str]     – list of triggered flag names
        details  : dict          – supporting data for each flag
    """
    flags: list[str] = []
    details: dict[str, Any] = {}

    # -- repeated course -------------------------------------------------------
    if registrations is not None and not registrations.empty:
        min_attempts = int(rules["anomaly_course_repeat_min"])
        course_counts = registrations["Course Code"].value_counts()
        chronic = course_counts[course_counts >= min_attempts]
        if not chronic.empty:
            flags.append("repeated_course")
            details["repeated_courses"] = {
                code: int(count) for code, count in chronic.items()
            }

    # -- undercredited senior --------------------------------------------------
    # credit_pace_ratio = Cumulative PHs / LEVEL_EXPECTED[Level]
    # For a Senior (expected=133): PHs < Junior_expected * ratio
    #   <=> credit_pace_ratio * 133 < 99 * ratio
    #   <=> credit_pace_ratio < (99 * ratio) / 133
    level = student_features.get("Level")
    if level == "Senior":
        ratio = student_features.get("credit_pace_ratio")
        if ratio is not None and not (isinstance(ratio, float) and pd.isna(ratio)):
            junior_expected = _LEVEL_EXPECTED_CREDITS["Junior"]
            senior_expected = _LEVEL_EXPECTED_CREDITS["Senior"]
            credit_ratio = rules["anomaly_senior_credit_ratio"]
            derived_threshold = (junior_expected * credit_ratio) / senior_expected
            if float(ratio) < derived_threshold:
                flags.append("undercredited_senior")
                estimated_phs = float(ratio) * senior_expected
                details["estimated_phs"] = round(estimated_phs, 1)
                details["minimum_expected_phs"] = junior_expected * credit_ratio
                details["credit_pace_ratio"] = ratio

    # -- silent GPA decline ----------------------------------------------------
    # Only flag when the decline is meaningful (slope < -0.05) AND the student
    # is actually approaching the risk zone (CGPA < 2.5).  A student at 3.5
    # with a tiny negative slope is fine — no intervention needed.
    _sd_slope = float(student_features.get("gpa_slope") or 0.0)
    _sd_cgpa  = float(
        student_features.get("Cumulative GPA")
        or student_features.get("official_cgpa")
        or 4.0
    )
    if (
        f1_gpa_trend(student_features, rules) == "declining"
        and not student_features.get("warning_risk", 0)
        and _sd_slope < -0.05
        and _sd_cgpa < 2.5
    ):
        flags.append("silent_decline")
        details["gpa_slope"] = student_features.get("gpa_slope")

    return {
        "flagged": bool(flags),
        "flags":   flags,
        "details": details,
    }


# ---------------------------------------------------------------------------
# f_course_risk_analytics – Population-level course risk
# ---------------------------------------------------------------------------

def f_course_risk_analytics(
    registrations_df: "pd.DataFrame",
    min_attempts: int = 10,
    credit_hours_map: "dict | None" = None,
) -> list[dict]:
    """
    Compute pass-rate risk analytics for every course in the dataset.

    Only conclusive first-attempt outcomes count toward pass rate:
    - PASSING: A+ through D-, P (pass/fail courses)
    - FAILING:  F, W, Abs
    - EXCLUDED from denominator: Con, I (incomplete — outcome still pending)

    Parameters
    ----------
    registrations_df
        Full registrations DataFrame (all students).
    min_attempts
        Courses with fewer conclusive outcomes than this are excluded.
    credit_hours_map
        If provided, courses with 0 credit hours are excluded before analysis.

    Returns
    -------
    List of dicts sorted by pass_rate_pct ascending (highest risk first).
    """
    from engines.sae.feature_engineering import GRADE_POINTS

    PASSING_GRADES = {"A+","A","A-","B+","B","B-","C+","C","C-","D+","D","D-","P"}
    FAILING_GRADES = {"F", "W", "ABS"}

    # Exclude 0-credit courses (remedial/non-credit courses not in the curriculum)
    if credit_hours_map is not None:
        zero_credit_codes = {code for code, ch in credit_hours_map.items() if ch == 0}
        if zero_credit_codes:
            registrations_df = registrations_df[
                ~registrations_df["Course Code"].isin(zero_credit_codes)
            ]

    # Exclude Graduation Project and Field Training — not meaningful for difficulty analysis
    registrations_df = registrations_df[
        ~registrations_df["Course Code"].str.startswith("C-GP", na=False) &
        ~registrations_df["Course Code"].str.startswith("C-TR", na=False)
    ]

    df = registrations_df[["Course Code", "Letter Grade", "Registration Status"]].copy()
    # First attempts only — retakes inflate pass rates since students who failed once
    # are more likely to pass on the second attempt, masking true course difficulty.
    df = df[~df["Registration Status"].fillna("").str.contains("Repeat|Improve", case=False, regex=True)]
    # Exclude in-progress registrations (no grade recorded yet)
    _VALID_OUTCOMES = {
        "A+","A","A-","B+","B","B-","C+","C","C-","D+","D","D-",
        "F","W","ABS","CON","CON.","CONC","I","P",
    }
    df["Letter Grade"] = df["Letter Grade"].astype(str).str.strip().str.upper()
    df = df[df["Letter Grade"].isin(_VALID_OUTCOMES)]
    df["grade_point"] = df["Letter Grade"].map(GRADE_POINTS)
    df["is_pass"]     = df["Letter Grade"].isin(PASSING_GRADES)
    df["is_fail"]     = df["Letter Grade"].isin(FAILING_GRADES)

    results: list[dict] = []
    insufficient: list[dict] = []
    for course_code, grp in df.groupby("Course Code"):
        n_pass  = int(grp["is_pass"].sum())
        n_fail  = int(grp["is_fail"].sum())
        n_total = n_pass + n_fail   # Con/I/Conc excluded from denominator

        if n_total < min_attempts:
            insufficient.append({
                "course_code":      str(course_code),
                "total_attempts":   n_total,
                "pass_count":       None,
                "fail_count":       None,
                "withdrawn_count":  None,
                "pass_rate_pct":    None,
                "avg_grade_points": None,
                "avg_letter_grade": None,
                "risk_category":    "insufficient_data",
                "note":             f"Fewer than {min_attempts} conclusive outcomes recorded",
            })
            continue

        pass_rate_pct = round(n_pass / n_total * 100, 1)

        # avg_grade_points: standard letter grades only (A+..F; P/W/Abs/Con/I have no numeric value)
        valid_gp = grp["grade_point"].dropna()
        avg_gp   = round(float(valid_gp.mean()), 2) if not valid_gp.empty else None
        avg_lg   = _gp_to_letter(avg_gp) if avg_gp is not None else None

        if pass_rate_pct >= 75:
            risk_category = "low"
        elif pass_rate_pct >= 50:
            risk_category = "medium"
        else:
            risk_category = "high"

        results.append({
            "course_code":      str(course_code),
            "total_attempts":   n_total,
            "pass_count":       n_pass,
            "fail_count":       n_fail,
            "withdrawn_count":  int(grp["Letter Grade"].isin({"W", "ABS"}).sum()),
            "pass_rate_pct":    pass_rate_pct,
            "avg_grade_points": avg_gp,
            "avg_letter_grade": avg_lg,
            "risk_category":    risk_category,
        })

    results.sort(key=lambda r: r["pass_rate_pct"])
    results.extend(sorted(insufficient, key=lambda r: r["course_code"]))

    # Debug: print top 5 riskiest courses
    print("\n[COURSE RISK] Top 5 riskiest (first-attempt, Con/I excluded from denominator):")
    for r in results[:5]:
        pct = f'{r["pass_rate_pct"]:.1f}%' if r["pass_rate_pct"] is not None else "N/A"
        print(
            f"  {r['course_code']:15s}  total={r['total_attempts']:4d}  "
            f"pass_rate={pct}"
        )

    return results


def f_course_risk_for_level(
    registrations_df: "pd.DataFrame",
    students_df: "pd.DataFrame",
    level: str,
    min_attempts: int = 5,
    credit_hours_map: "dict | None" = None,
) -> list[dict]:
    """
    Same as f_course_risk_analytics but restricted to registrations belonging
    to students who are currently at the given academic level.

    Parameters
    ----------
    level
        One of: 'Freshman', 'Sophomore', 'Junior', 'Senior'.
    min_attempts
        Lower default (5) than the population-wide function because the
        level-filtered subset is smaller.
    credit_hours_map
        If provided, courses with 0 credit hours are excluded before analysis.
    """
    level_ids  = set(students_df.loc[students_df["Level"] == level, "ID"])
    level_regs = registrations_df[registrations_df["ID"].isin(level_ids)]
    return f_course_risk_analytics(level_regs, min_attempts=min_attempts, credit_hours_map=credit_hours_map)


# ---------------------------------------------------------------------------
# f_cohort_comparison – Peer benchmarking within Level × Program
# ---------------------------------------------------------------------------

def f_cohort_comparison(
    student_id: str,
    features_df: "pd.DataFrame",
    students_df: "pd.DataFrame",
) -> dict:
    """
    Compare a student against peers at the same Level (all programs).

    Parameters
    ----------
    student_id
        The student to benchmark.
    features_df
        DataFrame returned by build_features(), indexed by student ID.
    students_df
        Raw student profile DataFrame (must include Cumulative GPA column).

    Returns
    -------
    dict with cohort statistics and the student's percentile ranks.
    If the cohort has fewer than 5 students, returns a note instead of stats.

    Percentile rank definition
    --------------------------
    gpa_percentile = 70 means the student's GPA is higher than 70 % of the
    cohort (i.e. they are in the top 30 %).
    """
    student_mask = students_df["ID"] == student_id
    if not student_mask.any():
        raise KeyError(f"Student '{student_id}' not found.")

    student_row = students_df[student_mask].iloc[0]
    level       = student_row.get("Level")

    cohort_mask = students_df["Level"] == level
    cohort_ids  = set(students_df.loc[cohort_mask, "ID"])
    cohort_size = len(cohort_ids)

    if cohort_size < 5:
        return {
            "student_id":  student_id,
            "level":       level,
            "cohort_size": cohort_size,
            "note": "Cohort too small for reliable comparison (fewer than 5 students).",
        }

    cohort_rows  = students_df[cohort_mask]
    cohort_gpas  = cohort_rows["Cumulative GPA"].dropna().astype(float)
    cohort_feats = features_df[features_df.index.isin(cohort_ids)]
    cohort_pr    = cohort_feats["pass_rate"].dropna()

    cohort_avg_gpa    = round(float(cohort_gpas.mean()),   3) if not cohort_gpas.empty else None
    cohort_median_gpa = round(float(cohort_gpas.median()), 3) if not cohort_gpas.empty else None
    cohort_avg_pr     = round(float(cohort_pr.mean()),     4) if not cohort_pr.empty  else None

    # Student's own GPA
    student_gpa = None
    try:
        raw = student_row.get("Cumulative GPA")
        if raw is not None and not pd.isna(float(raw)):
            student_gpa = float(raw)
    except (TypeError, ValueError):
        pass

    # Student's own pass rate
    student_pr = None
    if student_id in features_df.index:
        try:
            raw_pr = features_df.loc[student_id, "pass_rate"]
            if not pd.isna(raw_pr):
                student_pr = round(float(raw_pr), 4)
        except (TypeError, ValueError):
            pass

    # Percentile ranks
    gpa_pct = None
    if student_gpa is not None and not cohort_gpas.empty:
        gpa_pct = round(float((cohort_gpas <= student_gpa).mean() * 100), 1)

    pr_pct = None
    if student_pr is not None and not cohort_pr.empty:
        pr_pct = round(float((cohort_pr <= student_pr).mean() * 100), 1)

    return {
        "student_id":           student_id,
        "level":                level,
        "cohort_size":          cohort_size,
        "cohort_avg_gpa":       cohort_avg_gpa,
        "cohort_median_gpa":    cohort_median_gpa,
        "cohort_avg_pass_rate": cohort_avg_pr,
        "student_gpa":          student_gpa,
        "student_pass_rate":    student_pr,
        "gpa_percentile":       gpa_pct,
        "pass_rate_percentile": pr_pct,
    }


# ---------------------------------------------------------------------------
# f_mitigation_suggestions – Actionable advice from risk/anomaly/ML outputs
# ---------------------------------------------------------------------------

def f_mitigation_suggestions(
    risk_result: dict,
    anomaly_result: dict,
    prediction_result: dict,
) -> list[str]:
    """
    Map fired risk factors and anomalies to plain-English actionable suggestions.

    Parameters
    ----------
    risk_result
        Output of f3_at_risk_score (must contain 'breakdown' key).
    anomaly_result
        Output of f6_anomaly_detection (must contain 'flags' key).
    prediction_result
        Output of predict_graduation / _predict_single
        (must contain 'prediction' key; pass {} if unavailable).

    Returns
    -------
    Deduplicated list of up to 4 suggestions, ordered by severity.
    Always returns at least one suggestion (never empty).
    """
    # (priority, suggestion text) — lower priority number = shown first
    _RISK_MAP: dict[str, tuple[int, str]] = {
        "warning_risk": (
            1,
            "Schedule a meeting with your academic advisor to review your warning "
            "status and create a recovery plan.",
        ),
        "excess_failures": (
            2,
            "Prioritize retaking failed core courses before advancing — "
            "unresolved core failures block graduation.",
        ),
        "low_pass_rate": (
            3,
            "Consider reducing your course load next semester to focus on "
            "passing your current courses.",
        ),
        "gpa_declining": (
            4,
            "Your GPA trend is declining — identify which courses are pulling "
            "it down and seek tutoring support early.",
        ),
    }

    _ANOMALY_MAP: dict[str, tuple[int, str]] = {
        "silent_decline": (
            5,
            "Your GPA is dropping but no formal warning has been triggered yet — "
            "act now before it becomes a warning.",
        ),
        "undercredited_senior": (
            6,
            "You are behind on credits for your level — consider taking a summer "
            "semester or increasing your load.",
        ),
    }

    collected: list[tuple[int, str]] = []
    seen: set[str] = set()

    # Support both old breakdown format and new risk_flags format
    for key in risk_result.get("breakdown", {}):
        if key in _RISK_MAP and key not in seen:
            collected.append(_RISK_MAP[key])
            seen.add(key)
    for rf in risk_result.get("flags", []):
        ft = rf.get("type", "")
        if ft in ("active_warning", "dismissal_risk", "dismissal_active") and "warning_risk" not in seen:
            collected.append(_RISK_MAP.get("warning_risk", (1, rf.get("message", ""))))
            seen.add("warning_risk")
        elif ft == "cgpa_below_minimum" and "excess_failures" not in seen:
            collected.append((2, "Your CGPA is below 2.0 — prioritize improving grades to meet the minimum requirement."))
            seen.add("excess_failures")

    for flag in anomaly_result.get("flags", []):
        if flag in _ANOMALY_MAP and flag not in seen:
            collected.append(_ANOMALY_MAP[flag])
            seen.add(flag)

    if prediction_result.get("prediction") == 0 and "ml_high_risk" not in seen:
        collected.append((
            7,
            "Based on historical student patterns, your current trajectory puts "
            "you at elevated risk — speak to your advisor this semester.",
        ))
        seen.add("ml_high_risk")

    collected.sort(key=lambda x: x[0])
    result = [text for _, text in collected[:4]]

    if not result:
        result = ["Keep up your current performance — you are on a good academic track."]

    return result


# ---------------------------------------------------------------------------
# Semester arithmetic helpers
# ---------------------------------------------------------------------------

_SEASON_TO_IDX: dict[str, int]  = {"Spring": 0, "Summer": 1, "Fall": 2}
_SEASON_IDX_TO_NAME: dict[int, str] = {0: "Spring", 1: "Summer", 2: "Fall"}


def _sem_to_ordinal(sem: str) -> int:
    """'Fall 2024' → sortable integer (year*3 + season_index)."""
    parts = str(sem).strip().split()
    if len(parts) < 2:
        return 0
    try:
        return int(parts[1]) * 3 + _SEASON_TO_IDX.get(parts[0], 0)
    except ValueError:
        return 0


def _advance_semester(sem: str, n: int, include_summer: bool = False) -> str:
    """Return the semester that is *n* steps after *sem*.

    By default advances through Spring and Fall only, skipping Summer.
    Pass include_summer=True to advance through all three seasons.
    """
    if include_summer:
        new_ord    = _sem_to_ordinal(sem) + n
        year       = new_ord // 3
        season_idx = new_ord % 3
        return f"{_SEASON_IDX_TO_NAME[season_idx]} {year}"

    # Fall/Spring only: Spring YYYY = 2*YYYY, Fall YYYY = 2*YYYY+1
    # Summer is treated the same as Spring (the position before Fall of the same year)
    parts = str(sem).strip().split()
    if len(parts) < 2:
        return sem
    try:
        year = int(parts[1])
    except ValueError:
        return sem
    season = parts[0]

    if season == "Fall":
        fs_pos = year * 2 + 1
    else:  # Spring or Summer
        fs_pos = year * 2

    new_fs_pos  = fs_pos + n
    new_year    = new_fs_pos // 2
    new_season  = "Spring" if new_fs_pos % 2 == 0 else "Fall"
    return f"{new_season} {new_year}"


# ---------------------------------------------------------------------------
# f_trajectory_projection – Pure-math forward projections
# ---------------------------------------------------------------------------

def f_trajectory_projection(
    student_id: str,
    students_df: "pd.DataFrame",
    registrations_df: "pd.DataFrame",
    features_df: "pd.DataFrame",
) -> dict[str, Any]:
    """
    Compute three forward projections for a student using only arithmetic.
    No ML model is involved.

    Returns
    -------
    dict with keys:
        student_id, current_semester,
        gpa_projection, warning_projection, graduation_projection
    Each sub-dict has an 'unavailable' + 'reason' key when data is missing.
    """
    student_mask = students_df["ID"] == student_id
    if not student_mask.any():
        return {"student_id": student_id, "error": f"Student '{student_id}' not found."}

    student_row = students_df[student_mask].iloc[0]
    regs        = registrations_df[registrations_df["ID"] == student_id].copy()

    # ── Extract scalar inputs ─────────────────────────────────────────────────
    current_gpa: "float | None" = None
    try:
        raw = student_row.get("Cumulative GPA")
        if raw is not None and not (isinstance(raw, float) and pd.isna(raw)):
            current_gpa = float(raw)
    except (TypeError, ValueError):
        pass

    gpa_slope: "float | None" = None
    if student_id in features_df.index:
        try:
            raw = features_df.loc[student_id, "gpa_slope"]
            if raw is not None and not (isinstance(raw, float) and pd.isna(raw)):
                gpa_slope = float(raw)
        except (TypeError, ValueError, KeyError):
            pass

    cumulative_phs: "float | None" = None
    try:
        raw = student_row.get("Cumulative PHs")
        if raw is not None and not (isinstance(raw, float) and pd.isna(raw)):
            cumulative_phs = float(raw)
    except (TypeError, ValueError):
        pass

    current_semester: "str | None" = None
    n_semesters = 0
    if not regs.empty and "Semester" in regs.columns:
        valid_sems = regs["Semester"].dropna().tolist()
        if valid_sems:
            current_semester = max(valid_sems, key=_sem_to_ordinal)
        n_semesters = int(regs["Semester"].nunique())

    # ── Projection 1: GPA trajectory ─────────────────────────────────────────
    if current_gpa is not None and gpa_slope is not None:
        p2 = round(min(4.0, max(0.0, current_gpa + gpa_slope * 2)), 2)
        p4 = round(min(4.0, max(0.0, current_gpa + gpa_slope * 4)), 2)
        if gpa_slope > 0.005:
            trend_label = "If your GPA keeps improving at this rate"
        elif gpa_slope < -0.005:
            trend_label = "If your GPA keeps declining at this rate"
        else:
            trend_label = "If your GPA stays at its current level"
        gpa_projection: dict[str, Any] = {
            "gpa_in_2_semesters": p2,
            "gpa_in_4_semesters": p4,
            "trend_label":        trend_label,
            "current_gpa":        round(current_gpa, 2),
            "slope":              round(gpa_slope, 4),
        }
    else:
        gpa_projection = {"unavailable": True, "reason": "Insufficient data for GPA projection"}

    # ── Projection 2: Warning threshold ──────────────────────────────────────
    if current_gpa is not None and gpa_slope is not None:
        if current_gpa < 2.0:
            warning_projection: dict[str, Any] = {
                "semesters_until_warning": 0,
                "message":                 "Already below minimum GPA requirement",
                "semester_label":          None,
            }
        elif gpa_slope >= 0:
            warning_projection = {
                "semesters_until_warning": None,
                "message":                 "Not at risk of hitting warning threshold",
                "semester_label":          None,
            }
        else:
            sems = math.ceil((current_gpa - 2.0) / abs(gpa_slope))
            if sems > 10:
                warning_projection = {
                    "semesters_until_warning": None,
                    "message":                 "Not projected to reach warning threshold",
                    "semester_label":          None,
                }
            else:
                sem_lbl = _advance_semester(current_semester, sems) if current_semester else None
                warning_projection = {
                    "semesters_until_warning": sems,
                    "message":                 None,
                    "semester_label":          sem_lbl,
                }
    else:
        warning_projection = {"unavailable": True, "reason": "Insufficient data"}

    # ── Projection 3: Graduation timeline ────────────────────────────────────
    if cumulative_phs is not None and n_semesters > 0 and cumulative_phs > 0:
        # Use only completed past semesters for avg-credits to avoid
        # deflating the average with a current in-progress semester.
        if current_semester and not regs.empty:
            past_regs   = regs[regs["Semester"] != current_semester]
            n_completed = max(1, int(past_regs["Semester"].nunique()))
        else:
            n_completed = max(1, n_semesters)

        avg_credits       = cumulative_phs / n_completed
        credits_remaining = max(0.0, 133.0 - cumulative_phs)

        # Estimate credits the student is currently registered for
        current_sem_credits = 0
        if current_semester and not regs.empty:
            current_sem_credits = len(regs[regs["Semester"] == current_semester]) * 3

        next_semester = _advance_semester(current_semester, 1) if current_semester else None

        def _sem_label(n: int) -> str:
            if n > 20:
                return "Unlikely at current pace"
            if next_semester:
                return _advance_semester(next_semester, n - 1) if n > 0 else next_semester
            return "N/A"

        if credits_remaining <= 0:
            graduation_projection: dict[str, Any] = {
                "semesters_to_graduation":       0,
                "message":                       "Graduation requirements met on credits",
                "estimated_graduation_semester": current_semester or "N/A",
                "optimistic_semester":           current_semester or "N/A",
                "pessimistic_semester":          current_semester or "N/A",
                "avg_credits_per_semester":      round(avg_credits, 1),
                "near_graduation":               True,
                "uses_summer":                   False,
            }
        elif current_sem_credits > 0 and credits_remaining <= current_sem_credits:
            graduation_projection = {
                "semesters_to_graduation":       0,
                "message":                       "Expected to complete requirements this semester",
                "estimated_graduation_semester": current_semester or "N/A",
                "optimistic_semester":           current_semester or "N/A",
                "pessimistic_semester":          next_semester or "N/A",
                "avg_credits_per_semester":      round(avg_credits, 1),
                "near_graduation":               True,
                "uses_summer":                   False,
            }
        elif credits_remaining <= 20:
            graduation_projection = {
                "semesters_to_graduation":       1,
                "message":                       f"On track to graduate — approximately {int(credits_remaining)} credits remaining",
                "estimated_graduation_semester": next_semester or "N/A",
                "optimistic_semester":           next_semester or "N/A",
                "pessimistic_semester":          _advance_semester(current_semester, 2) if current_semester else "N/A",
                "avg_credits_per_semester":      round(avg_credits, 1),
                "near_graduation":               True,
                "uses_summer":                   False,
            }
        else:
            sems_normal = math.ceil(credits_remaining / avg_credits)
            sems_opt    = math.ceil(credits_remaining / (avg_credits * 1.2))
            sems_pess   = math.ceil(credits_remaining / (avg_credits * 0.8))

            graduation_projection = {
                "semesters_to_graduation":       sems_normal,
                "estimated_graduation_semester": _sem_label(sems_normal),
                "optimistic_semester":           _sem_label(sems_opt),
                "pessimistic_semester":          _sem_label(sems_pess),
                "avg_credits_per_semester":      round(avg_credits, 1),
                "near_graduation":               False,
                "uses_summer":                   False,
            }
    else:
        graduation_projection = {
            "unavailable": True,
            "reason":      "Insufficient credit or semester history",
        }

    if student_id == "STU000124":
        print(f"[DEBUG STU000124] graduation_projection = {graduation_projection}")

    return {
        "student_id":            student_id,
        "current_semester":      current_semester,
        "gpa_projection":        gpa_projection,
        "warning_projection":    warning_projection,
        "graduation_projection": graduation_projection,
    }


# ---------------------------------------------------------------------------
# f_semester_difficulty – Current semester load risk index
# ---------------------------------------------------------------------------

def f_semester_difficulty(
    student_id: str,
    students_df: "pd.DataFrame",
    registrations_df: "pd.DataFrame",
    course_risk_df: "list | None" = None,
) -> dict[str, Any]:
    """
    Compute the Semester Difficulty Index (SDI) for a student's current semester.

    SDI = sum of course risk points / number of active courses, ranging 1.0–3.0.
    Risk points: high=3, medium=2, low=1, unknown=2 (assumed medium).

    Parameters
    ----------
    course_risk_df
        Pre-computed list from f_course_risk_analytics.  If None, it is
        computed internally from registrations_df (expensive — pass a
        cached copy when calling for many students in batch).

    Returns
    -------
    dict with: course_list, sdi_score, course_count, high_risk_count,
               medium_risk_count, low_risk_count, flag, flag_message, semester_label.
    On error: dict with 'error' key.
    """
    student_mask = students_df["ID"] == student_id
    if not student_mask.any():
        return {"error": f"Student '{student_id}' not found.", "student_id": student_id}

    student_row = students_df[student_mask].iloc[0]
    regs = registrations_df[registrations_df["ID"] == student_id].copy()

    if regs.empty or "Semester" not in regs.columns:
        return {"error": "No registration records found.", "student_id": student_id}

    valid_sems = regs["Semester"].dropna().tolist()
    if not valid_sems:
        return {"error": "No valid semester records found.", "student_id": student_id}

    current_sem = max(valid_sems, key=_sem_to_ordinal)
    curr_regs = regs[regs["Semester"] == current_sem].copy()
    curr_regs["Letter Grade"] = (
        curr_regs["Letter Grade"].astype(str).str.strip().str.upper()
    )

    if "Registration Status" in curr_regs.columns:
        rs = curr_regs["Registration Status"].fillna("").astype(str)
    else:
        rs = pd.Series([""] * len(curr_regs), index=curr_regs.index)

    not_w         = curr_regs["Letter Grade"] != "W"
    not_withdrawn = ~rs.str.contains("Withdrawn", case=False, na=False)
    curr_regs     = curr_regs[not_w & not_withdrawn]

    if curr_regs.empty:
        return {
            "course_list":       [],
            "sdi_score":         0.0,
            "course_count":      0,
            "high_risk_count":   0,
            "medium_risk_count": 0,
            "low_risk_count":    0,
            "flag":              "OK",
            "flag_message":      "No active courses in the most recent semester.",
            "semester_label":    current_sem,
        }

    course_codes = curr_regs["Course Code"].dropna().unique().tolist()

    if course_risk_df is None:
        course_risk_data = f_course_risk_analytics(registrations_df)
    else:
        course_risk_data = course_risk_df

    if isinstance(course_risk_data, list):
        risk_lookup = {item["course_code"]: item["risk_category"] for item in course_risk_data}
        pr_lookup   = {item["course_code"]: item.get("pass_rate_pct") for item in course_risk_data}
    else:
        risk_lookup = {
            str(r["course_code"]): r["risk_category"]
            for _, r in course_risk_data.iterrows()
        }
        pr_lookup = {
            str(r["course_code"]): r.get("pass_rate_pct")
            for _, r in course_risk_data.iterrows()
        }

    _RISK_PTS = {"high": 3, "medium": 2, "low": 1}

    course_list: list[dict] = []
    total_points = 0
    high_count = medium_count = low_count = 0

    for code in course_codes:
        cat = risk_lookup.get(str(code))
        pr  = pr_lookup.get(str(code))
        if cat == "insufficient_data":
            course_list.append({
                "course_code":   str(code),
                "risk_category": "insufficient_data",
                "pass_rate_pct": None,
            })
            continue
        if cat is None:
            display_cat = "unknown"
            pts = 2
            medium_count += 1
        else:
            display_cat = cat
            pts = _RISK_PTS.get(cat, 2)
            if cat == "high":
                high_count += 1
            elif cat == "medium":
                medium_count += 1
            else:
                low_count += 1
        total_points += pts
        course_list.append({
            "course_code":   str(code),
            "risk_category": display_cat,
            "pass_rate_pct": pr,
        })

    n_courses = high_count + medium_count + low_count
    sdi = round(total_points / n_courses, 2) if n_courses > 0 else 0.0

    from engines.sae.feature_engineering import compute_student_features
    feats  = compute_student_features(student_row, regs)
    merged = {**student_row.to_dict(), **feats}

    gpa_trend  = f1_gpa_trend(merged)
    risk_level = f3_risk_flags(merged)["risk_level"]

    if risk_level == "high" and sdi > 2.5:
        flag = "CRITICAL"
        flag_message = "Critical load — high-risk student taking mostly difficult courses"
    elif risk_level == "moderate" and sdi > 2.2:
        flag = "WARNING"
        flag_message = "Heavy load — struggling student taking several difficult courses"
    elif gpa_trend == "declining" and sdi >= 2.0:
        flag = "CAUTION"
        flag_message = "Caution — declining GPA with a challenging course mix"
    else:
        flag = "OK"
        flag_message = "Semester load appears manageable"

    return {
        "course_list":       course_list,
        "sdi_score":         sdi,
        "course_count":      n_courses,
        "high_risk_count":   high_count,
        "medium_risk_count": medium_count,
        "low_risk_count":    low_count,
        "flag":              flag,
        "flag_message":      flag_message,
        "semester_label":    current_sem,
    }


# ---------------------------------------------------------------------------
# f_prerequisite_bottleneck – Curriculum prerequisite chain analysis
# ---------------------------------------------------------------------------

def f_prerequisite_bottleneck(
    student_id: str,
    registrations_df: "pd.DataFrame",
    curriculum_path: "str | Path" = Path(__file__).parent / "rules" / "curriculum_2026.json",
) -> dict[str, Any]:
    """
    Identify prerequisite bottlenecks — courses the student has never passed
    that block progress through the curriculum's prerequisite chain.

    Parameters
    ----------
    curriculum_path
        Path to the curriculum JSON (relative to CWD or absolute).

    Returns
    -------
    dict with: student_id, total_failed_unresolved, total_courses_currently_blocked,
               top_priority_retake, top_priority_unblocks, blockers (list of top 5).
    Returns dict with 'error' key if curriculum file is missing or malformed.
    """
    import json
    from pathlib import Path

    path = Path(curriculum_path)
    if not path.exists():
        return {
            "error": (
                f"Curriculum file not found at '{curriculum_path}'. "
                "Create the JSON file to enable prerequisite bottleneck analysis."
            ),
            "student_id": student_id,
        }

    try:
        with path.open(encoding="utf-8") as fh:
            curriculum = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        return {"error": f"Failed to load curriculum: {exc}", "student_id": student_id}

    courses_dict   = curriculum.get("courses", {})
    graduation_reqs = set(curriculum.get("graduation_requirements", []))

    # Build prerequisite map and reverse map
    prereq_map: dict[str, list[str]] = {
        code: data.get("prerequisites", [])
        for code, data in courses_dict.items()
    }
    reverse_map: dict[str, list[str]] = {code: [] for code in courses_dict}
    for code, prereqs in prereq_map.items():
        for prereq in prereqs:
            if prereq in reverse_map:
                reverse_map[prereq].append(code)
            else:
                reverse_map[prereq] = [code]

    # Find courses the student has never passed (best grade across all attempts = F)
    from engines.sae.feature_engineering import GRADE_POINTS

    student_regs = registrations_df[registrations_df["ID"] == student_id].copy()
    if student_regs.empty:
        return {
            "student_id":                     student_id,
            "total_failed_unresolved":        0,
            "total_courses_currently_blocked": 0,
            "top_priority_retake":            None,
            "top_priority_unblocks":          0,
            "blockers":                       [],
        }

    student_regs["_lg"] = (
        student_regs["Letter Grade"].astype(str).str.strip().str.upper()
    )
    student_regs["_gp"] = student_regs["_lg"].map(GRADE_POINTS)

    graded = student_regs.dropna(subset=["_gp"])
    if graded.empty:
        failed_courses: set[str] = set()
    else:
        best = graded.groupby("Course Code")["_gp"].max()
        failed_courses = set(best[best == 0.0].index.tolist())

    # Traverse reverse map to find all downstream courses blocked by each failure
    def _downstream(code: str, visited: "set[str] | None" = None) -> set[str]:
        if visited is None:
            visited = set()
        if code in visited:
            return visited
        visited.add(code)
        for child in reverse_map.get(code, []):
            _downstream(child, visited)
        return visited

    blockers_raw: list[dict[str, Any]] = []
    all_blocked: set[str] = set()

    for course in failed_courses:
        downstream_all  = _downstream(course) - {course}
        directly        = [c for c in reverse_map.get(course, []) if c in courses_dict]
        unlock_count    = len(directly)
        total_eventual  = len(downstream_all)
        blocks_grad     = bool(downstream_all & graduation_reqs)

        course_mask     = student_regs["Course Code"] == course
        failed_attempts = int((student_regs.loc[course_mask, "_lg"] == "F").sum())

        severity = "high" if unlock_count >= 2 else "medium" if unlock_count >= 1 else "low"
        priority_message = f"{course} is blocking {unlock_count} course(s) — recommended retake"
        all_blocked.update(downstream_all)

        blockers_raw.append({
            "course_code":                   course,
            "failed_attempts":               failed_attempts,
            "unlocks_next_semester":         directly,
            "unlock_count":                  unlock_count,
            "total_eventual_impact":         total_eventual,
            "severity":                      severity,
            "blocks_graduation_requirement": blocks_grad,
            "priority_message":              priority_message,
        })

    # Return ALL blockers with at least 1 unlock, sorted by unlock_count desc
    blockers_raw.sort(key=lambda x: x["unlock_count"], reverse=True)
    active_blockers = [b for b in blockers_raw if b["unlock_count"] >= 1]

    top_priority       = active_blockers[0]["course_code"]  if active_blockers else None
    top_priority_count = active_blockers[0]["unlock_count"] if active_blockers else 0

    return {
        "student_id":                     student_id,
        "total_failed_unresolved":        len(failed_courses),
        "total_courses_currently_blocked": len(all_blocked),
        "top_priority_retake":            top_priority,
        "top_priority_unblocks":          top_priority_count,
        "blockers":                       active_blockers,
    }


# ---------------------------------------------------------------------------
# f_generate_key_points – Structured academic status summary for advisor view
# ---------------------------------------------------------------------------

def f_generate_key_points(
    student_id: str,
    profile: dict,
    features: dict,
    risk_flags: "list | dict",
    anomaly_result: dict,
    bottleneck_result: dict,
    trajectory_result: dict,
    cohort_result: dict,
    rules: dict,
    *,
    cgpa_trend_history: "list | None" = None,
    semester_difficulty: "dict | None" = None,
    registrations: "pd.DataFrame | None" = None,
) -> list[dict]:
    """
    Return up to 8 key academic observations about a student, sorted by severity.

    Each item: {severity: 'critical'|'high'|'positive', emoji, message}.
    risk_flags may be either a list[dict] (new format) or a dict with a 'flags'
    key (old format) — both are handled.
    """
    # Normalise risk_flags to a plain list regardless of how it was passed
    if isinstance(risk_flags, dict):
        _flags: list[dict] = risk_flags.get("flags") or []
    else:
        _flags = list(risk_flags or [])

    points: list[dict] = []

    cgpa     = profile.get("official_cgpa")
    consec_w = int(profile.get("consecutive_warning") or 0)
    level    = str(profile.get("level") or "").strip()
    credits  = float(profile.get("official_credits_passed") or 0)

    a_details = (anomaly_result or {}).get("details") or {}
    repeated  = a_details.get("repeated_courses") or {}

    # ── CRITICAL from risk flags ──────────────────────────────────────────────
    for flag in _flags:
        if flag.get("severity") == "critical":
            points.append({"severity": "critical", "emoji": "🔴", "message": flag["message"]})

    # ── CRITICAL from anomalies ───────────────────────────────────────────────
    for code, n in repeated.items():
        if n >= 3:
            points.append({
                "severity": "critical", "emoji": "🔴",
                "message": f"{code} failed {n} times with no resolution — graduation blocker.",
            })
            break

    if level == "Senior" and credits < rules.get("graduation_project_credits_required", 80):
        points.append({
            "severity": "critical", "emoji": "🔴",
            "message": (
                f"Senior with only {int(credits)} credits — below the "
                f"{int(rules.get('graduation_project_credits_required', 80))}-credit threshold "
                "for graduation project registration."
            ),
        })

    # ── HIGH from risk flags ──────────────────────────────────────────────────
    for flag in _flags:
        if flag.get("severity") == "high":
            points.append({"severity": "high", "emoji": "🟠", "message": flag["message"]})

    # ── POSITIVE ──────────────────────────────────────────────────────────────
    has_critical = any(p["severity"] == "critical" for p in points)

    if cgpa_trend_history:
        graded_hist = [
            t for t in cgpa_trend_history
            if (t.get("cgpa_at_end_of_semester") or 0) > 0
        ]
        if len(graded_hist) >= 2:
            improving_n = 1
            for i in range(len(graded_hist) - 1, 0, -1):
                if (graded_hist[i]["cgpa_at_end_of_semester"] >
                        graded_hist[i - 1]["cgpa_at_end_of_semester"]):
                    improving_n += 1
                else:
                    break
            if improving_n >= 2:
                idx_start  = max(0, len(graded_hist) - improving_n)
                start_cgpa = graded_hist[idx_start]["cgpa_at_end_of_semester"]
                end_cgpa   = graded_hist[-1]["cgpa_at_end_of_semester"]
                points.append({
                    "severity": "positive", "emoji": "🟢",
                    "message": (
                        f"CGPA improved from {start_cgpa:.2f} to {end_cgpa:.2f} "
                        f"over the last {improving_n} graded semesters."
                    ),
                })

    if cgpa is not None and cgpa >= 3.0 and not has_critical:
        points.append({
            "severity": "positive", "emoji": "🟢",
            "message": f"Strong academic standing — CGPA {cgpa:.2f} is above 3.0.",
        })

    if consec_w == 0 and not has_critical:
        points.append({
            "severity": "positive", "emoji": "🟢",
            "message": "No academic warnings on record.",
        })

    _SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "positive": 3}
    points.sort(key=lambda p: _SEV_ORDER.get(p["severity"], 9))
    return points[:8]


# ---------------------------------------------------------------------------
# f_generate_talking_points – Advisor action sentences from key points
# ---------------------------------------------------------------------------

def f_generate_talking_points(key_points: list[dict]) -> list[str]:
    """
    Return exactly 3 action-oriented advisor talking points derived from key_points.
    """
    non_positive = [p for p in key_points if p["severity"] != "positive"]

    if not non_positive:
        return [
            "Acknowledge the strong academic performance and validate the student's effort.",
            "Discuss plans for remaining credits and confirm the graduation timeline.",
            "Review track selection and elective options for the final semesters.",
        ]

    talking: list[str] = []
    for kp in non_positive[:3]:
        msg = kp["message"]
        msg_lower = msg.lower()

        if "2.0" in msg and ("cgpa" in msg_lower or "gpa" in msg_lower):
            tp = (
                "Identify the specific courses pulling down the CGPA and build a "
                "targeted retake plan to reach 2.0 before the next semester."
            )
        elif "dismissal" in msg_lower:
            tp = (
                "Urgently review the academic warning status and establish the exact "
                "GPA improvement needed this semester to avoid dismissal proceedings."
            )
        elif "consecutive" in msg_lower and "warning" in msg_lower:
            tp = (
                "Review the academic warning requirements and the minimum GPA "
                "improvement needed to clear the active warning status."
            )
        elif "blocking" in msg_lower and "downstream" in msg_lower:
            parts  = msg.split()
            course = next((p for p in parts if "-" in p), parts[0] if parts else "this course")
            tp = (
                f"Investigate why {course} has not been resolved and whether "
                "tutoring, a different study approach, or scheduling help is needed."
            )
        elif "failed twice" in msg_lower:
            parts  = msg.split()
            course = parts[0] if parts else "this course"
            tp = (
                f"Create a concrete plan for resolving {course} — consider whether "
                "a tutoring referral or prerequisites review is appropriate."
            )
        elif "graduation blocker" in msg_lower:
            parts  = msg.split()
            course = parts[0] if parts else "this course"
            tp = (
                f"Prioritise a retake plan for {course} this semester — "
                "it is directly blocking graduation requirements."
            )
        elif "credits" in msg_lower and "behind" in msg_lower:
            tp = (
                "Discuss credit recovery options such as a summer session or an "
                "increased course load next semester to close the credit gap."
            )
        elif "pass rate" in msg_lower:
            tp = (
                "Identify which courses are contributing to the low pass rate and "
                "develop a targeted improvement plan for the next semester."
            )
        elif "first-attempt" in msg_lower and "failure" in msg_lower:
            tp = (
                "Review failed courses and plan a realistic retake schedule, "
                "ensuring prerequisites are met before re-enrollment."
            )
        elif "difficulty index" in msg_lower:
            tp = (
                "Discuss whether the current course load is manageable and consider "
                "whether any high-difficulty courses should be deferred."
            )
        elif "80-credit" in msg_lower or (
            "senior" in msg_lower and "credits" in msg_lower
        ):
            tp = (
                "Create a specific plan to reach the 80-credit threshold for "
                "graduation project registration this semester or next."
            )
        else:
            first_part = msg.split("—")[0].strip()
            for emoji_ch in ("🔴", "🟠", "🟡", "🟢"):
                first_part = first_part.replace(emoji_ch, "").strip()
            tp = f"Discuss and address: {first_part}."

        talking.append(tp)

    _FILLERS = [
        "Review upcoming semester course selection and confirm prerequisites are met.",
        "Discuss the graduation timeline and any remaining degree requirements.",
        "Encourage continued improvement and schedule a follow-up check-in.",
    ]
    for filler in _FILLERS:
        if len(talking) >= 3:
            break
        talking.append(filler)

    return talking[:3]


# ---------------------------------------------------------------------------
# f_course_track_performance – Per-track pass-rate breakdown
# ---------------------------------------------------------------------------

def f_course_track_performance(
    student_id: str,
    registrations_df: "pd.DataFrame",
    credit_hours_map: "dict | None" = None,
) -> dict[str, Any]:
    """
    Group a student's first-attempt graded courses by curriculum track and
    compute per-track statistics.

    Track mapping (by course code prefix):
        C-AI* → AI,  C-SW* → SWE,  C-DS* → DSE,  C-CY* → CYS,  other → GEN

    Returns
    -------
    dict with: tracks (dict), overall_best_track, overall_worst_track.
    Tracks with fewer than 2 graded courses are excluded.
    """
    from engines.sae.feature_engineering import GRADE_POINTS as _GP_TR

    student_regs = registrations_df[registrations_df["ID"] == student_id].copy()
    if student_regs.empty:
        return {"tracks": {}, "overall_best_track": None, "overall_worst_track": None}

    if "Registration Status" in student_regs.columns:
        first = student_regs[
            ~student_regs["Registration Status"].fillna("").str.contains(
                "Repeat|Improve", case=False, regex=True
            )
        ].copy()
    else:
        first = student_regs.copy()

    first["_lg"] = first["Letter Grade"].astype(str).str.strip().str.upper()
    first["_gp"] = first["_lg"].map(_GP_TR)
    graded = first.dropna(subset=["_gp"])

    if graded.empty:
        return {"tracks": {}, "overall_best_track": None, "overall_worst_track": None}

    def _track(code: str) -> str:
        c = str(code).strip().upper()
        if c.startswith("C-AI"):
            return "AI"
        if c.startswith("C-SW"):
            return "SWE"
        if c.startswith("C-DS"):
            return "DSE"
        if c.startswith("C-CY"):
            return "CYS"
        return "GEN"

    graded = graded.copy()
    graded["_track"] = graded["Course Code"].apply(_track)

    tracks: dict[str, Any] = {}
    for track, grp in graded.groupby("_track"):
        n = len(grp)
        if n < 2:
            continue
        n_passed = int((grp["_gp"] > 0.0).sum())
        tracks[str(track)] = {
            "courses_attempted": n,
            "courses_passed":    n_passed,
            "pass_rate":         round(n_passed / n, 3),
            "avg_grade_points":  round(float(grp["_gp"].mean()), 3),
        }

    if not tracks:
        return {"tracks": {}, "overall_best_track": None, "overall_worst_track": None}

    best  = max(tracks, key=lambda t: (tracks[t]["pass_rate"], tracks[t]["avg_grade_points"]))
    worst = min(tracks, key=lambda t: (tracks[t]["pass_rate"], tracks[t]["avg_grade_points"]))

    return {
        "tracks":               tracks,
        "overall_best_track":   best,
        "overall_worst_track":  worst if worst != best else None,
    }


# ---------------------------------------------------------------------------
# Grade-point → letter-grade conversion helper
# ---------------------------------------------------------------------------

def _gp_to_letter(gp: float) -> str:
    """Convert an EUI grade-point average to the nearest letter grade."""
    if gp >= 4.0: return "A+"
    if gp >= 3.7: return "A"
    if gp >= 3.4: return "A-"
    if gp >= 3.2: return "B+"
    if gp >= 3.0: return "B"
    if gp >= 2.8: return "B-"
    if gp >= 2.6: return "C+"
    if gp >= 2.4: return "C"
    if gp >= 2.2: return "C-"
    if gp >= 2.0: return "D+"
    if gp >= 1.5: return "D"
    if gp >= 1.0: return "D-"
    return "F"


# ---------------------------------------------------------------------------
# f_course_category_performance – Per-subject-area pass-rate breakdown
# ---------------------------------------------------------------------------

def f_course_category_performance(
    student_id: str,
    registrations_df: "pd.DataFrame",
    credit_hours_map: "dict | None" = None,
) -> dict[str, Any]:
    """
    Group a student's first-attempt graded courses into 3 subject areas and
    compute per-area statistics.

    Uses SUBJECT_AREAS (3 groups: Math & Sciences, Computing & Technology,
    Humanities & Electives).  Areas with fewer than 2 first-attempt graded
    courses are excluded.
    """
    from engines.sae.feature_engineering import GRADE_POINTS as _GP_CAT

    student_regs = registrations_df[registrations_df["ID"] == student_id].copy()
    if student_regs.empty:
        return {"categories": {}, "strongest_category": None, "weakest_category": None}

    if "Registration Status" in student_regs.columns:
        first = student_regs[
            ~student_regs["Registration Status"].fillna("").str.contains(
                "Repeat|Improve", case=False, regex=True
            )
        ].copy()
    else:
        first = student_regs.copy()

    first["_lg"] = first["Letter Grade"].astype(str).str.strip().str.upper()
    first["_gp"] = first["_lg"].map(_GP_CAT)
    graded = first.dropna(subset=["_gp"]).copy()

    if graded.empty:
        return {"categories": {}, "strongest_category": None, "weakest_category": None}

    def _get_area(code: str) -> "str | None":
        c = str(code).strip()
        for area_name, prefixes in SUBJECT_AREAS.items():
            for prefix in prefixes:
                if c.startswith(prefix):
                    return area_name
        return None

    graded["_cat"] = graded["Course Code"].apply(_get_area)
    graded = graded.dropna(subset=["_cat"])

    categories: dict[str, Any] = {}
    for area_name in SUBJECT_AREAS:
        grp = graded[graded["_cat"] == area_name]
        n = len(grp)
        if n < 1:
            continue
        n_passed  = int((grp["_gp"] > 0.0).sum())
        n_failed  = int((grp["_gp"] == 0.0).sum())
        pass_rate = n_passed / n

        if credit_hours_map:
            credit_hours_attempted = sum(
                credit_hours_map.get(str(code), 3)
                for code in grp["Course Code"].tolist()
            )
        else:
            credit_hours_attempted = n * 3

        if pass_rate >= 0.85:
            status = "strong"
        elif pass_rate >= 0.70:
            status = "adequate"
        elif pass_rate >= 0.50:
            status = "struggling"
        else:
            status = "critical"

        avg_gp = round(float(grp["_gp"].mean()), 3)
        categories[area_name] = {
            "courses_attempted":      n,
            "courses_passed":         n_passed,
            "courses_failed":         n_failed,
            "pass_rate":              round(pass_rate, 3),
            "avg_grade_points":       avg_gp,
            "avg_letter_grade":       _gp_to_letter(avg_gp),
            "credit_hours_attempted": credit_hours_attempted,
            "status":                 status,
        }

    if not categories:
        return {"categories": {}, "strongest_category": None, "weakest_category": None}

    sorted_cats = dict(sorted(categories.items(), key=lambda x: x[1]["pass_rate"]))

    best  = max(categories, key=lambda t: (categories[t]["pass_rate"], categories[t]["avg_grade_points"]))
    worst = min(categories, key=lambda t: (categories[t]["pass_rate"], categories[t]["avg_grade_points"]))

    return {
        "categories":         sorted_cats,
        "strongest_category": best,
        "weakest_category":   worst if worst != best else None,
    }
