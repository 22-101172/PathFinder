"""ALE Adapter — stateless mapper between gateway contracts and the
ALE engine. No logic lives here. All rules come from rule_bundles
(caller provides, sourced from RAG). All course data comes from
kg_data (caller provides, sourced from KG). All student data comes
from StudentContext (sourced from Student Context Provider).
See ALE_Integration_Contract.md for the full contract."""

import logging

from gateway.models.schemas import StudentContext
from engines.ale.functions.simulate_gpa_forward import simulate_gpa_forward
from engines.ale.functions.solve_target_gpa import solve_target_gpa
from engines.ale.functions.check_course_eligibility import check_course_eligibility
from engines.ale.functions.run_graduation_audit import run_graduation_audit
from engines.ale.functions.generate_semester_plan import generate_semester_plan
from engines.ale.functions.generate_graduation_roadmap import generate_graduation_roadmap
from engines.ale.schemas import (
    SimulateGPAForwardInput, SolveTargetGPAInput,
    CheckCourseEligibilityInput, RunGraduationAuditInput,
    GenerateSemesterPlanInput, GenerateGraduationRoadmapInput,
    GradingScaleRules, RetakeRules, GraduationRequirementRules,
    AcademicWarningRules, HonorsRules, CreditLimitRules,
    SummerSemesterRules, CourseHistoryEntry, AvailableCourse,
    StudentLevelRules,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def call(
    operation: str,
    student_context: StudentContext,
    rule_bundles: dict,
    kg_data: dict = {},
    params: dict = {},
) -> dict:
    logger.info("ALE operation: %s", operation)
    try:
        return _dispatch(operation, student_context, rule_bundles, kg_data, params)
    except Exception as exc:
        logger.error("ALE operation %s failed: %s", operation, exc)
        return {"status": "error", "message": str(exc), "operation": operation}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_rules(rule_bundles: dict, key: str, model_class):
    try:
        return model_class(**rule_bundles[key])
    except (KeyError, Exception):
        raise ValueError(f"rule_bundles missing or invalid: '{key}'")


def _map_course_history(course_history) -> list[CourseHistoryEntry]:
    entries = []
    for r in course_history:
        entries.append(CourseHistoryEntry(
            course_code=r.course_code,
            semester=r.semester_taken,
            semester_type="summer" if "summer" in r.semester_taken.lower() else "regular",
            grade_points=None,
            credits=r.credit_hours,
            status=r.status,
        ))
    return entries



def _map_available_courses(kg_data: dict) -> list[AvailableCourse]:
    courses = []
    for c in kg_data.get("available_courses", []):
        track_raw = c.get("track")
        if isinstance(track_raw, dict):
            track_list = [track_raw["track_id"]]
        elif isinstance(track_raw, list):
            track_list = track_raw
        elif track_raw is not None:
            track_list = [track_raw]
        else:
            track_list = []

        sem_raw = c.get("semester_offering")
        sem_list = sem_raw if isinstance(sem_raw, list) else ([sem_raw] if sem_raw else [])

        courses.append(AvailableCourse(
            course_code=c.get("course_code"),
            name=c.get("name"),
            credits=c.get("credits"),
            level=c.get("level"),
            prerequisites=c.get("prerequisites", []),
            semester_offering=sem_list,
            track=track_list,
        ))
    return courses


def _derive_incomplete_flag(course_history) -> bool:
    return any(r.grade == "I" for r in course_history)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _dispatch(
    operation: str,
    sc: StudentContext,
    rule_bundles: dict,
    kg_data: dict,
    params: dict,
) -> dict:
    if operation == "simulate_gpa_forward":
        return _simulate_gpa_forward(sc, rule_bundles, kg_data, params)
    if operation == "solve_target_gpa":
        return _solve_target_gpa(sc, rule_bundles, kg_data, params)
    if operation == "check_course_eligibility":
        return _check_course_eligibility(sc, rule_bundles, kg_data, params)
    if operation == "run_graduation_audit":
        return _run_graduation_audit(sc, rule_bundles, kg_data, params)
    if operation == "generate_semester_plan":
        return _generate_semester_plan(sc, rule_bundles, kg_data, params)
    if operation == "generate_graduation_roadmap":
        return _generate_graduation_roadmap(sc, rule_bundles, kg_data, params)
    raise ValueError(f"Unknown ALE operation: '{operation}'")


# ---------------------------------------------------------------------------
# Operation methods
# ---------------------------------------------------------------------------

def _simulate_gpa_forward(sc: StudentContext, rule_bundles: dict, kg_data: dict, params: dict) -> dict:
    grading_scale = _parse_rules(rule_bundles, "grading_scale_rules", GradingScaleRules)
    retake_rules = _parse_rules(rule_bundles, "retake_rules", RetakeRules)

    inp = SimulateGPAForwardInput(
        current_cgpa=sc.cgpa,
        gpa_counted_credits=sc.cumulative_chs,
        current_quality_points=sc.cumulative_cps,
        planned_courses=params.get("planned_courses", []),
        grading_scale_rules=grading_scale,
        retake_rules=retake_rules,
        excluded_in_progress_courses=params.get("excluded_in_progress_courses", []),
    )
    return simulate_gpa_forward(inp).model_dump()


def _solve_target_gpa(sc: StudentContext, rule_bundles: dict, kg_data: dict, params: dict) -> dict:
    grading_scale = _parse_rules(rule_bundles, "grading_scale_rules", GradingScaleRules)
    retake_rules = _parse_rules(rule_bundles, "retake_rules", RetakeRules)
    graduation_rules = _parse_rules(rule_bundles, "graduation_requirement_rules", GraduationRequirementRules)

    inp = SolveTargetGPAInput(
        current_cgpa=sc.cgpa,
        gpa_counted_credits=sc.cumulative_chs,
        current_quality_points=sc.cumulative_cps,
        target_cgpa=params["target_cgpa"],
        planned_courses=params.get("planned_courses", []),
        grading_scale_rules=grading_scale,
        retake_rules=retake_rules,
        graduation_rules=graduation_rules,
        assumed_grade_per_semester=params.get("assumed_grade_per_semester", None),
        credits_per_semester=params.get("credits_per_semester", 18),
        planned_course_source=params.get("planned_course_source", "orchestrator"),
    )
    return solve_target_gpa(inp).model_dump()


def _check_course_eligibility(sc: StudentContext, rule_bundles: dict, kg_data: dict, params: dict) -> dict:
    retake_rules = _parse_rules(rule_bundles, "retake_rules", RetakeRules)

    inp = CheckCourseEligibilityInput(
        target_course_code=params["target_course_code"],
        target_course_prerequisites=kg_data.get("course_prerequisites", []),
        target_course_credit_threshold=kg_data.get("course_credit_threshold", None),
        completed_courses=sc.completed_courses,
        in_progress_courses=sc.in_progress_courses,
        retake_count=sc.retake_count,
        total_improve_retakes_used=sc.total_improve_retakes_used,
        cumulative_passed_hours=sc.total_credit_hours_earned,
        current_cgpa=sc.cgpa,
        attempt_type=params["attempt_type"],
        retake_rules=retake_rules,
    )
    return check_course_eligibility(inp).model_dump()


def _run_graduation_audit(sc: StudentContext, rule_bundles: dict, kg_data: dict, params: dict) -> dict:
    graduation_rules = _parse_rules(rule_bundles, "graduation_requirement_rules", GraduationRequirementRules)
    warning_rules = _parse_rules(rule_bundles, "academic_warning_rules", AcademicWarningRules)
    honors_rules = _parse_rules(rule_bundles, "honors_rules", HonorsRules)

    inp = RunGraduationAuditInput(
        study_status=sc.study_status,
        completed_courses=sc.completed_courses,
        failed_courses=sc.failed_courses,
        in_progress_courses=sc.in_progress_courses,
        current_cgpa=sc.cgpa,
        cumulative_passed_hours=sc.total_credit_hours_earned,
        consecutive_warnings=sc.consecutive_warnings,
        total_warnings=sc.total_warnings,
        military_status=sc.military_status,
        completed_regular_semesters=sc.completed_regular_semesters,
        zero_credit_courses_passed=sc.zero_credit_courses_passed,
        course_history=_map_course_history(sc.course_history),
        graduation_rules=graduation_rules,
        warning_rules=warning_rules,
        honors_rules=honors_rules,
    )
    return run_graduation_audit(inp).model_dump()


def _generate_semester_plan(sc: StudentContext, rule_bundles: dict, kg_data: dict, params: dict) -> dict:
    credit_limit_rules = _parse_rules(rule_bundles, "credit_limit_rules", CreditLimitRules)
    graduation_rules = _parse_rules(rule_bundles, "graduation_requirement_rules", GraduationRequirementRules)
    retake_rules = _parse_rules(rule_bundles, "retake_rules", RetakeRules)
    student_level_rules = _parse_rules(rule_bundles, "student_level_rules", StudentLevelRules)

    summer_rules = None
    if "summer_semester_rules" in rule_bundles:
        summer_rules = _parse_rules(rule_bundles, "summer_semester_rules", SummerSemesterRules)

    level_map = {1: "Freshman", 2: "Sophomore", 3: "Junior", 4: "Senior"}

    inp = GenerateSemesterPlanInput(
        study_status=sc.study_status,
        completed_courses=sc.completed_courses,
        failed_courses=sc.failed_courses,
        in_progress_courses=sc.in_progress_courses,
        retake_count=sc.retake_count,
        total_improve_retakes_used=sc.total_improve_retakes_used,
        current_cgpa=sc.cgpa,
        cumulative_passed_hours=sc.total_credit_hours_earned,
        student_level=level_map.get(sc.level, "Freshman"),
        official_track=sc.track_id,
        incomplete_grade_flag=_derive_incomplete_flag(sc.course_history),
        available_courses=_map_available_courses(kg_data),
        credit_limit_rules=credit_limit_rules,
        graduation_rules=graduation_rules,
        retake_rules=retake_rules,
        specialization_credit_threshold=params.get("specialization_credit_threshold", 60),
        summer_semester_rules=summer_rules,
        target_semester_type=params["target_semester_type"],
        target_track=params.get("target_track", None),
        target_credit_load=params.get("target_credit_load", None),
        max_credits_mode=params.get("max_credits_mode", False),
        student_level_rules=student_level_rules,
    )
    return generate_semester_plan(inp).model_dump()


def _generate_graduation_roadmap(sc: StudentContext, rule_bundles: dict, kg_data: dict, params: dict) -> dict:
    credit_limit_rules = _parse_rules(rule_bundles, "credit_limit_rules", CreditLimitRules)
    graduation_rules = _parse_rules(rule_bundles, "graduation_requirement_rules", GraduationRequirementRules)
    retake_rules = _parse_rules(rule_bundles, "retake_rules", RetakeRules)
    student_level_rules = _parse_rules(rule_bundles, "student_level_rules", StudentLevelRules)

    summer_rules = None
    if "summer_semester_rules" in rule_bundles:
        summer_rules = _parse_rules(rule_bundles, "summer_semester_rules", SummerSemesterRules)

    level_map = {1: "Freshman", 2: "Sophomore", 3: "Junior", 4: "Senior"}

    inp = GenerateGraduationRoadmapInput(
        study_status=sc.study_status,
        completed_courses=sc.completed_courses,
        failed_courses=sc.failed_courses,
        in_progress_courses=sc.in_progress_courses,
        retake_count=sc.retake_count,
        total_improve_retakes_used=sc.total_improve_retakes_used,
        current_cgpa=sc.cgpa,
        gpa_counted_credits=sc.cumulative_chs,
        current_quality_points=sc.cumulative_cps,
        cumulative_passed_hours=sc.total_credit_hours_earned,
        student_level=level_map.get(sc.level, "Freshman"),
        official_track=sc.track_id,
        incomplete_grade_flag=_derive_incomplete_flag(sc.course_history),
        zero_credit_courses_passed=sc.zero_credit_courses_passed,
        military_status=sc.military_status,
        completed_regular_semesters=sc.completed_regular_semesters,
        available_courses=_map_available_courses(kg_data),
        credit_limit_rules=credit_limit_rules,
        graduation_rules=graduation_rules,
        retake_rules=retake_rules,
        specialization_credit_threshold=params.get("specialization_credit_threshold", 60),
        summer_semester_rules=summer_rules,
        target_semester_type=params["target_semester_type"],
        starting_year=params["starting_year"],
        target_track=params.get("target_track", None),
        assumed_grade_per_pass=params.get("assumed_grade_per_pass", None),
        accelerated_mode=params.get("accelerated_mode", False),
        max_credits_mode=params.get("max_credits_mode", False),
        target_credit_load=params.get("target_credit_load", None),
        student_level_rules=student_level_rules,
    )
    return generate_graduation_roadmap(inp).model_dump()


class ALEAdapter:
    """Thin class wrapper — delegates to the module-level call() function."""
    def call(self, operation, student_context, rule_bundles, kg_data=None, params=None):
        return call(operation, student_context, rule_bundles, kg_data or {}, params or {})
