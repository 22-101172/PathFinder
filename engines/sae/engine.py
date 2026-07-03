"""
SAE Engine — single entry point for all analytical functions.

Now powered by SAEAdapter for data access.  The engine never reads files
directly; it delegates all I/O to the adapter layer.

All public functions return plain JSON-serializable dicts or lists.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import pandas as pd

from engines.sae.sae_adapter import SAEAdapter
from engines.sae.feature_engineering import compute_student_features, build_features
from engines.sae.rule_engine import (
    f1_gpa_trend,
    f2_credit_pace,
    f3_risk_flags,
    f6_anomaly_detection,
    f_cohort_comparison,
    f_course_risk_analytics,
    f_course_risk_for_level,
    f_mitigation_suggestions,
    f_trajectory_projection,
    f_semester_difficulty,
    f_prerequisite_bottleneck,
    f_generate_key_points,
    f_generate_talking_points,
    f_course_track_performance,
    f_course_category_performance,
)

# ── Cache config ──────────────────────────────────────────────────────────────
_CACHE_PATH = Path(__file__).parent / "cache" / "overview_cache.json"
CACHE_MAX_AGE_HOURS = 24

# ── Module-level lazy singletons ──────────────────────────────────────────────
_adapter: "SAEAdapter | None" = None
_features_df_cache: "pd.DataFrame | None" = None


def _get_adapter() -> SAEAdapter:
    global _adapter
    if _adapter is None:
        _adapter = SAEAdapter()
    return _adapter


def _get_features_df() -> pd.DataFrame:
    global _features_df_cache
    if _features_df_cache is None:
        a = _get_adapter()
        _features_df_cache = build_features(a.sp.students_df, a.sp.regs_df)
    return _features_df_cache


# ── JSON serialisation helpers ────────────────────────────────────────────────

def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    try:
        import numpy as np
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            v = float(obj)
            return None if not math.isfinite(v) else v
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return [_clean(x) for x in obj.tolist()]
    except ImportError:
        pass
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def _safe_float(val) -> "float | None":
    if val is None:
        return None
    try:
        f = float(val)
        return None if not math.isfinite(f) else f
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> "int | None":
    f = _safe_float(val)
    return None if f is None else int(f)


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_load() -> "list | None":
    if not _CACHE_PATH.exists():
        return None
    age_hours = (time.time() - _CACHE_PATH.stat().st_mtime) / 3600
    if age_hours > CACHE_MAX_AGE_HOURS:
        return None
    try:
        with _CACHE_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _cache_save(data: list) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _CACHE_PATH.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception:
        pass


# ── Course name loader ─────────────────────────────────────────────────────────

def _load_course_names() -> dict[str, str]:
    try:
        path = Path(__file__).parent / "data" / "Course_Catalogue_Correct_Version.xlsx"
        if not path.exists():
            return {}
        df = pd.read_excel(path, sheet_name="Course Catalogue2")
        df.columns = df.columns.str.strip()
        result: dict[str, str] = {}
        for _, row in df.iterrows():
            code = str(row.get("Course Code", "")).strip()
            name = str(row.get("Course Name", "")).strip()
            if code and name and name.lower() not in ("nan", "none", ""):
                result[code] = name
        return result
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def analyze_student(
    student_id: str,
    rules: "dict | None" = None,
) -> dict[str, Any]:
    """
    Full single-student analysis.

    Returns None-safe dict with keys:
        profile, official_cgpa, credits_completed, cgpa_trend_history,
        current_courses, gpa_trend, credit_pace, risk_score, risk_level,
        risk_breakdown, anomalies, suggestions, trajectory,
        semester_difficulty, prerequisite_bottleneck, cohort_stats, rules

    Returns {"error": "..."} if student_id is not found.
    """
    adapter = _get_adapter()
    ctx = adapter.get_student_full_context(student_id)
    if ctx is None:
        return {"error": f"Student '{student_id}' not found"}

    if rules is None:
        rules = ctx["rules"]

    stu_df  = adapter.sp.students_df
    regs_df = adapter.sp.regs_df
    stu_row = stu_df[stu_df["ID"] == student_id].iloc[0]
    regs    = regs_df[regs_df["ID"] == student_id]

    raw_feats = compute_student_features(stu_row, regs)
    merged    = {**stu_row.to_dict(), **raw_feats}

    anomalies = f6_anomaly_detection(merged, regs, rules)
    sem_diff  = f_semester_difficulty(student_id, stu_df, regs_df)
    prereq_bn = f_prerequisite_bottleneck(student_id, regs_df)

    risk = f3_risk_flags(merged, rules)
    rl   = risk["risk_level"]

    mini_feats = pd.DataFrame([raw_feats]).set_index("ID")
    trajectory = f_trajectory_projection(student_id, stu_df, regs_df, mini_feats)

    has_issues = (
        rl in ("moderate", "high")
        or anomalies.get("flagged")
        or (isinstance(prereq_bn, dict) and prereq_bn.get("total_failed_unresolved", 0) > 0)
    )
    suggestions = f_mitigation_suggestions(risk, anomalies, {}) if has_issues else []

    cohort_stats  = adapter.get_cohort_stats(student_id)
    category_perf = f_course_category_performance(student_id, regs_df, adapter.credit_hours_map)

    return _clean({
        "profile":                 ctx["profile"],
        "official_cgpa":           ctx["official_cgpa"],
        "credits_completed":       ctx["credits_completed"],
        "expected_credits":        raw_feats.get("expected_credits"),
        "cgpa_trend_history":      ctx["cgpa_trend_history"],
        "current_courses":         ctx["current_courses"],
        "registrations":           ctx["registrations"],
        "gpa_trend":               f1_gpa_trend(merged, rules),
        "credit_pace":             f2_credit_pace(merged, rules),
        "risk_flags":              risk["flags"],
        "risk_level":              rl,
        "anomalies":               anomalies,
        "suggestions":             suggestions,
        "trajectory":              trajectory,
        "semester_difficulty":     sem_diff,
        "prerequisite_bottleneck": prereq_bn,
        "cohort_stats":            cohort_stats,
        "category_performance":    category_perf,
        "rules":                   rules,
    })


def get_advisor_overview(
    rules: "dict | None" = None,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """
    All active (non-graduated) students with risk scores and immediate-action flags.

    Sorted: immediate_action=True first (by risk_score desc), then others.
    Cached to disk for 24 hours.

    Each item: id, name, level, program, study_status, official_cgpa,
               credits_completed, consecutive_warning, risk_score, risk_level,
               risk_breakdown, gpa_trend, credit_pace,
               immediate_action (bool), immediate_reason (str | None).
    """
    if not force_refresh:
        cached = _cache_load()
        if cached is not None:
            return cached

    adapter = _get_adapter()
    if rules is None:
        rules = adapter.get_rules()

    active_df  = adapter.sp._active_df
    regs_df    = adapter.sp.regs_df
    active_ids = set(active_df["ID"])

    # Pre-compute per-student course repeat counts (3+ attempts = flag)
    repeat_fail_map: dict[str, list[str]] = {}
    for sid, grp in regs_df[regs_df["ID"].isin(active_ids)].groupby("ID"):
        counts  = grp["Course Code"].value_counts()
        chronic = counts[counts >= 3]
        if not chronic.empty:
            repeat_fail_map[str(sid)] = [str(c) for c in chronic.index.tolist()]

    features_df = build_features(active_df, regs_df)

    result: list[dict[str, Any]] = []
    for sid, feats_row in features_df.iterrows():
        sid = str(sid)
        if sid not in active_ids:
            continue
        stu_mask = active_df["ID"] == sid
        if not stu_mask.any():
            continue
        stu_row = active_df[stu_mask].iloc[0]

        cgpa_f   = _safe_float(stu_row.get("Cumulative GPA"))
        phs_f    = _safe_float(stu_row.get("Cumulative PHs"))
        level    = str(stu_row.get("Level", "")).strip()
        consec_w = _safe_int(stu_row.get("Consecutive Warning")) or 0

        feats = {
            "ID": sid,
            **feats_row.to_dict(),
            "Level":              level,
            "Cumulative PHs":     phs_f,
            "Cumulative GPA":     cgpa_f,
            "Consecutive Warning": consec_w,
        }
        risk = f3_risk_flags(feats, rules)

        immediate        = False
        immediate_reason: "str | None" = None

        if consec_w > 0:
            immediate        = True
            immediate_reason = (
                f"Active academic warning — {consec_w} consecutive "
                f"warning{'s' if consec_w > 1 else ''}"
            )
        elif cgpa_f is not None and cgpa_f < 2.0:
            immediate        = True
            immediate_reason = f"CGPA below minimum ({cgpa_f:.2f})"
        elif sid in repeat_fail_map:
            codes            = ", ".join(repeat_fail_map[sid][:2])
            immediate        = True
            immediate_reason = f"Repeated course failure: {codes}"
        elif level == "Senior" and phs_f is not None and phs_f < 80:
            immediate        = True
            immediate_reason = (
                f"Senior below graduation project threshold ({phs_f:.0f} / 80 credits)"
            )

        result.append(_clean({
            "id":                  sid,
            "name":                str(stu_row.get("Name", "")),
            "level":               level,
            "program":             str(stu_row.get("Program", "")),
            "study_status":        str(stu_row.get("Study Status", "")),
            "official_cgpa":       cgpa_f,
            "credits_completed":   phs_f,
            "consecutive_warning": consec_w,
            "risk_flags":          risk["flags"],
            "risk_level":          risk["risk_level"],
            "gpa_trend":           f1_gpa_trend(feats, rules),
            "credit_pace":         f2_credit_pace(feats, rules),
            "immediate_action":    immediate,
            "immediate_reason":    immediate_reason,
        }))

    # Immediate-action students first; within each group sort by consec warnings desc then cgpa asc
    result.sort(key=lambda x: (
        0 if x["immediate_action"] else 1,
        -(x.get("consecutive_warning") or 0),
        x.get("official_cgpa") or 4.0,
    ))
    _cache_save(result)
    return result


def simulate_gpa(
    student_id: str,
    hypothetical_grades: dict,
) -> dict[str, Any]:
    """
    Project CGPA after hypothetical grades for current/ungraded courses.

    hypothetical_grades values may be letter grades, numeric percentages (>4 treated
    as percentage), or strings ending in '%'.

    Returns: current_official_cgpa, projected_cgpa, cgpa_change,
             converted_grades, per_course_breakdown.
    """
    adapter = _get_adapter()
    return _clean(adapter.simulate_semester_gpa(student_id, hypothetical_grades))


def get_course_risk(
    level: "str | None" = None,
) -> list[dict[str, Any]]:
    """
    Population-level course pass-rate analytics, optionally filtered by level.

    Includes course_name from catalogue when available.
    """
    adapter      = _get_adapter()
    all_regs_df  = adapter.get_all_registrations()   # includes graduated students
    stu_df       = adapter.sp.students_df
    chmap        = adapter.credit_hours_map

    if level:
        data = f_course_risk_for_level(all_regs_df, stu_df, level, min_attempts=5, credit_hours_map=chmap)
    else:
        data = f_course_risk_analytics(all_regs_df, min_attempts=10, credit_hours_map=chmap)

    course_names = _load_course_names()
    for item in data:
        item["course_name"] = course_names.get(item["course_code"], "")

    return _clean(data)


def get_cohort_stats(student_id: str) -> dict[str, Any]:
    """
    Cohort standing for a student within their level+program group.

    Returns student_percentile = fraction of cohort with strictly lower
    official_cgpa × 100.  E.g. 6.2 means the student outperforms 6.2% of cohort.
    """
    adapter = _get_adapter()
    return _clean(adapter.get_cohort_stats(student_id))


def get_category_performance(student_id: str) -> dict[str, Any]:
    """Course category performance breakdown for a student."""
    adapter = _get_adapter()
    if not (adapter.sp.students_df["ID"] == student_id).any():
        raise KeyError(f"Student '{student_id}' not found")
    return _clean(f_course_category_performance(student_id, adapter.sp.regs_df, adapter.credit_hours_map))


def get_cohort_comparison(student_id: str) -> dict[str, Any]:
    """Compare a student against peers in the same Level and Program."""
    adapter = _get_adapter()
    stu_df  = adapter.sp.students_df
    if not (stu_df["ID"] == student_id).any():
        raise KeyError(f"Student '{student_id}' not found")
    return _clean(f_cohort_comparison(student_id, _get_features_df(), stu_df))


def get_trajectory(student_id: str) -> dict[str, Any]:
    """Pure-math trajectory projections for a single student."""
    adapter = _get_adapter()
    stu_df  = adapter.sp.students_df
    if not (stu_df["ID"] == student_id).any():
        raise KeyError(f"Student '{student_id}' not found")
    regs    = adapter.sp.regs_df[adapter.sp.regs_df["ID"] == student_id]
    stu_row = stu_df[stu_df["ID"] == student_id].iloc[0]
    feats   = compute_student_features(stu_row, regs)
    mini_df = pd.DataFrame([feats]).set_index("ID")
    return _clean(f_trajectory_projection(student_id, stu_df, adapter.sp.regs_df, mini_df))


def get_semester_difficulty(student_id: str) -> dict[str, Any]:
    """Semester Difficulty Index for a student's current semester."""
    adapter = _get_adapter()
    if not (adapter.sp.students_df["ID"] == student_id).any():
        raise KeyError(f"Student '{student_id}' not found")
    return _clean(f_semester_difficulty(student_id, adapter.sp.students_df, adapter.sp.regs_df))


def get_prerequisite_bottleneck(student_id: str) -> dict[str, Any]:
    """Prerequisite bottleneck analysis for a single student."""
    adapter = _get_adapter()
    if not (adapter.sp.students_df["ID"] == student_id).any():
        raise KeyError(f"Student '{student_id}' not found")
    return _clean(f_prerequisite_bottleneck(student_id, adapter.sp.regs_df))


def get_alerts(
    student_id: str,
    rules: "dict | None" = None,
) -> list[dict[str, Any]]:
    """Active alerts for a single student, sorted high → medium → low."""
    result = analyze_student(student_id, rules)
    if "error" in result:
        return []

    alerts: list[dict] = []
    _f3_labels = {
        "warning_risk":    ("academic_warning",  "high",   "Active academic warning on record."),
        "low_pass_rate":   ("low_pass_rate",      "high",   "Pass rate below the 75% threshold."),
        "excess_failures": ("excess_failures",    "medium", "More than 20% of first-attempt courses failed."),
        "gpa_declining":   ("gpa_declining",      "medium", "Cumulative GPA is on a declining trend."),
    }
    for criterion in result.get("risk_breakdown", {}):
        if criterion in _f3_labels:
            atype, sev, msg = _f3_labels[criterion]
            alerts.append({"type": atype, "severity": sev, "message": msg})

    _f6_labels = {
        "repeated_course":      ("repeated_course",      "medium", "Same course attempted 3+ times."),
        "undercredited_senior": ("undercredited_senior", "medium", "Senior has fewer passed credits than expected."),
        "silent_decline":       ("silent_decline",       "low",    "GPA declining with no active warning — proactive outreach advised."),
    }
    for flag in result.get("anomalies", {}).get("flags", []):
        if flag in _f6_labels:
            atype, sev, msg = _f6_labels[flag]
            alerts.append({"type": atype, "severity": sev, "message": msg})

    _order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: _order.get(a["severity"], 9))
    return alerts


def refresh_overview_cache(rules: "dict | None" = None) -> int:
    """Force-recompute and cache the advisor overview. Returns count cached."""
    data = get_advisor_overview(rules=rules, force_refresh=True)
    return len(data)


def get_advisor_analysis(student_id: str) -> dict[str, Any]:
    """
    Collect all data needed for the Advisor Analysis tab.

    Runs a full analyze_student(), then computes key_points, talking_points,
    and track_performance from the result.

    Returns dict with error key if student not found.
    Keys: profile, official_cgpa, risk_score, risk_level, risk_breakdown,
          cgpa_trend_history, cohort_stats, semester_difficulty,
          prerequisite_bottleneck, key_points, talking_points, track_performance.
    """
    result = analyze_student(student_id)
    if "error" in result:
        return result

    adapter = _get_adapter()
    stu_df  = adapter.sp.students_df
    regs_df = adapter.sp.regs_df
    stu_row = stu_df[stu_df["ID"] == student_id].iloc[0]
    regs    = regs_df[regs_df["ID"] == student_id]

    raw_feats = compute_student_features(stu_row, regs)
    merged    = {**stu_row.to_dict(), **raw_feats}

    key_points = f_generate_key_points(
        student_id=student_id,
        profile=result["profile"],
        features=merged,
        risk_flags=result.get("risk_flags", []),
        anomaly_result=result["anomalies"],
        bottleneck_result=result["prerequisite_bottleneck"],
        trajectory_result=result["trajectory"],
        cohort_result=result["cohort_stats"],
        rules=result["rules"],
        cgpa_trend_history=result["cgpa_trend_history"],
        semester_difficulty=result["semester_difficulty"],
        registrations=regs,
    )

    talking_points = f_generate_talking_points(key_points)

    track_perf = f_course_track_performance(
        student_id, regs_df, adapter.credit_hours_map
    )

    return _clean({
        "profile":                 result["profile"],
        "official_cgpa":           result["official_cgpa"],
        "expected_credits":        result.get("expected_credits"),
        "risk_flags":              result.get("risk_flags", []),
        "risk_level":              result["risk_level"],
        "cgpa_trend_history":      result["cgpa_trend_history"],
        "cohort_stats":            result["cohort_stats"],
        "semester_difficulty":     result["semester_difficulty"],
        "prerequisite_bottleneck": result["prerequisite_bottleneck"],
        "trajectory":              result["trajectory"],
        "registrations":           result.get("registrations", []),
        "key_points":              key_points,
        "talking_points":          talking_points,
        "track_performance":       track_perf,
        "category_performance":    result.get("category_performance"),
    })
