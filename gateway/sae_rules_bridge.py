"""
gateway/sae_rules_bridge.py
============================
Converts PathFinder's 8 RAG Pydantic rule bundles into the flat dict format
SAE's rule engine expects (same key names as DEFAULT_RULES in
sae/providers/rules_provider.py).

Only keys that have a matching RAG source are emitted — SAE falls back to its
own EUI-calibrated defaults for anything not present, so partial dicts are safe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def build_sae_rules(rule_bundles: dict) -> dict:
    """
    Convert RAG rule bundles to a flat SAE rules dict.

    Parameters
    ----------
    rule_bundles : dict
        Keyed by bundle name (str) → Pydantic model instance or None.
        If a bundle is missing or None, its keys are simply omitted and
        SAE uses its own defaults — this function never raises.

    Returns
    -------
    dict
        Flat dict with a subset of DEFAULT_RULES keys, ready to be passed
        as the ``rules`` parameter to SAEAdapter methods.
        Returns an empty dict if rule_bundles is empty or all bundles are None.
    """
    if not rule_bundles:
        return {}

    out: dict = {}

    try:
        gs = rule_bundles.get("grading_scale_rules")
        if gs is not None:
            # letter_to_points: filter out None values (P-grade is gpa_neutral)
            out["grade_points"] = {k: v for k, v in gs.letter_to_points.items() if v is not None}
            # percentage_to_letter list → {letter: min_pct} mapping SAE expects
            out["grade_thresholds"] = {r.letter: int(r.min_pct) for r in gs.percentage_to_letter}
    except Exception:
        pass

    try:
        gr = rule_bundles.get("graduation_requirement_rules")
        if gr is not None:
            out["total_credits_required"] = gr.total_credits_required
            out["minimum_cgpa_graduation"] = gr.minimum_cgpa
            out["minimum_cgpa"] = gr.minimum_cgpa          # SAE alias used by risk engine
            out["minimum_semesters"] = gr.minimum_regular_semesters
            out["maximum_semesters"] = gr.maximum_regular_semesters
    except Exception:
        pass

    try:
        aw = rule_bundles.get("academic_warning_rules")
        if aw is not None:
            out["warning_cgpa_threshold"] = aw.cgpa_warning_threshold
            out["dismissal_consecutive_warnings"] = aw.max_consecutive_warnings
            out["dismissal_separate_warnings"] = aw.max_total_warnings
    except Exception:
        pass

    try:
        cl = rule_bundles.get("credit_limit_rules")
        if cl is not None:
            out["credit_limit_cgpa_3_plus"] = cl.cgpa_above_3_limit
            out["credit_limit_cgpa_2_to_3"] = cl.cgpa_between_2_and_3_limit
            out["credit_limit_cgpa_1_to_2"] = cl.cgpa_between_1_and_2_limit
            out["credit_limit_cgpa_below_1"] = cl.cgpa_below_1_limit
            out["credit_limit_minimum"] = cl.minimum_per_semester
    except Exception:
        pass

    try:
        ss = rule_bundles.get("summer_semester_rules")
        if ss is not None:
            out["summer_max_courses"] = ss.default_max_courses
            out["summer_max_courses_cgpa_3_plus"] = ss.cgpa_above_3_max_courses
    except Exception:
        pass

    try:
        hr = rule_bundles.get("honors_rules")
        if hr is not None:
            out["honors_min_cgpa"] = hr.minimum_cgpa_throughout
            out["honors_min_semesters"] = hr.minimum_semesters
            out["honors_max_semesters"] = hr.maximum_semesters
    except Exception:
        pass

    try:
        sl = rule_bundles.get("student_level_rules")
        if sl is not None:
            out["credits_end_of_level_1"] = sl.freshman_max_hours
            out["credits_end_of_level_2"] = sl.sophomore_max_hours
            out["credits_end_of_level_3"] = sl.junior_max_hours
            out["credits_end_of_level_4"] = sl.senior_max_hours
            # SAE aliases used by risk/anomaly engine
            out["sophomore_credits_threshold"] = sl.freshman_max_hours
            out["junior_credits_threshold"] = sl.sophomore_max_hours
            out["senior_credits_threshold"] = sl.junior_max_hours
    except Exception:
        pass

    try:
        rr = rule_bundles.get("retake_rules")
        if rr is not None:
            out["improvement_retake_max_courses"] = rr.improve_retake_max_courses_cgpa_above_2
    except Exception:
        pass

    return out
