"""
Rules providers for the SAE.

DEFAULT_RULES holds the institutional policy constants (EUI handbook values).
FakeRulesProvider wraps them for uniform access; a live provider would fetch
these from a policy database or config service.
"""

from __future__ import annotations

GRADE_POINTS: dict[str, float] = {
    'A+': 4.0, 'A': 3.7, 'A-': 3.4,
    'B+': 3.2, 'B': 3.0, 'B-': 2.8,
    'C+': 2.6, 'C': 2.4, 'C-': 2.2,
    'D+': 2.0, 'D': 1.5, 'D-': 1.0,
    'F': 0.0
}

GRADE_THRESHOLDS: dict[str, int] = {
    'A+': 96, 'A': 92, 'A-': 88,
    'B+': 84, 'B': 80, 'B-': 76,
    'C+': 72, 'C': 68, 'C-': 64,
    'D+': 60, 'D': 55, 'D-': 50,
    'F': 0
}

DEFAULT_RULES: dict = {
    # Graduation requirements
    'total_credits_required': 133,
    'minimum_cgpa_graduation': 2.0,
    'minimum_semesters': 6,
    'maximum_semesters': 16,

    # Registration credit limits by CGPA bracket
    'credit_limit_cgpa_3_plus': 21,
    'credit_limit_cgpa_2_to_3': 18,
    'credit_limit_cgpa_1_to_2': 15,
    'credit_limit_cgpa_below_1': 12,
    'credit_limit_minimum': 9,

    # Summer limits
    'summer_max_courses': 2,
    'summer_max_courses_cgpa_3_plus': 3,

    # Warning rules
    'warning_cgpa_threshold': 2.0,
    'dismissal_consecutive_warnings': 4,
    'dismissal_separate_warnings': 6,

    # Credit thresholds for levels (expected at END of each level)
    'credits_end_of_level_1': 33,
    'credits_end_of_level_2': 66,
    'credits_end_of_level_3': 99,
    'credits_end_of_level_4': 133,

    # Expected credits per semester (133 / 8 regular semesters)
    'expected_credits_per_semester': 16.625,

    # Honors requirements
    'honors_min_cgpa': 3.0,
    'honors_max_semesters': 8,
    'honors_min_semesters': 6,

    # Graduation project / field training
    'graduation_project_min_credits': 80,
    'field_training_min_credits': 59,

    # Kept for backward compat with engine/rule_engine refs
    'graduation_project_credits_required': 80,
    'field_training_credits_required': 59,

    # Attendance
    'min_attendance_percentage': 75,

    # Retake rules
    'improvement_retake_max_courses': 3,

    # GPA trend thresholds (used by rule_engine F1)
    'gpa_trend_improving_min': 0.05,
    'gpa_trend_declining_max': 0.00,

    # Credit pace thresholds (used by rule_engine F2)
    'credit_pace_behind_max': 0.80,
    'credit_pace_ahead_min': 1.10,

    # Risk scoring weights
    'risk_weight_warning': 40,
    'risk_weight_pass_rate': 25,
    'risk_weight_failed_core': 25,
    'risk_weight_gpa_declining': 20,
    'risk_weight_consecutive_warnings_bonus': 15,
    'risk_weight_prereq_blocker': 20,
    'risk_weight_credits_behind': 15,
    'risk_weight_repeated_failure': 15,
    'risk_weight_sdi_overload': 10,

    # Aliases used by rule_engine.py
    'risk_weight_consec_warning_bonus': 15,
    'risk_weight_prereq_blocking': 20,
    'risk_weight_chronic_failure': 15,

    # Risk thresholds
    'risk_pass_rate_threshold': 0.75,
    'risk_failure_rate_threshold': 0.20,
    'moderate_risk_floor': 40,
    'high_risk_floor': 70,
    'risk_cgpa_floor_override': 2.0,
    'risk_credits_behind_threshold': 20,
    'risk_consec_warning_threshold': 3,
    'risk_prereq_blocking_threshold': 3,
    'risk_chronic_failure_threshold': 2,
    'risk_sdi_overload_threshold': 2.5,

    # Anomaly detection
    'anomaly_course_repeat_min': 3,
    'anomaly_senior_credit_ratio': 0.5,

    # Aliases for risk floors used elsewhere
    'minimum_cgpa': 2.0,
    'good_standing_cgpa': 2.5,
    'sophomore_credits_threshold': 33,
    'junior_credits_threshold': 66,
    'senior_credits_threshold': 99,

    # Grading
    'grade_points': GRADE_POINTS,
    'grade_thresholds': GRADE_THRESHOLDS,
}


class FakeRulesProvider:
    """Returns the hard-coded institutional rules."""

    def get_rules(self) -> dict:
        return DEFAULT_RULES.copy()

    def get_grading_scale(self) -> dict:
        """Return {letter_grade: minimum_percentage} mapping."""
        return GRADE_THRESHOLDS.copy()
