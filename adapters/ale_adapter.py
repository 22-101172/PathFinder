"""ALE Adapter — stateless mapper between gateway contracts and the
ALE engine. No logic lives here. All rules come from rule_bundles
(caller provides, sourced from RAG). All course data comes from
kg_data (caller provides, sourced from KG). All student data comes
from StudentContext (sourced from Student Context Provider).
See ALE_Integration_Contract.md for the full contract."""

import logging
import time

from pydantic import ValidationError

from gateway.models.schemas import StudentContext
from engines.ale.functions.simulate_gpa_forward import simulate_gpa_forward
from engines.ale.functions.solve_target_gpa import solve_target_gpa
from engines.ale.functions.check_course_eligibility import check_course_eligibility
from engines.ale.functions.run_graduation_audit import run_graduation_audit
from engines.ale.functions.generate_semester_plan import generate_semester_plan
from engines.ale.functions.generate_graduation_roadmap import generate_graduation_roadmap
from engines.ale.utils.grade_resolver import GradeResolutionError, resolve_grade
from engines.ale.ale_schemas import (
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
# Logging helpers
# ---------------------------------------------------------------------------

# Scalar param keys safe to log verbatim (no PII)
_SAFE_SCALAR_PARAM_KEYS = {
    "target_course_code", "attempt_type", "target_cgpa",
    "target_semester_type", "starting_year",
    "target_end_semester_type", "target_end_year",
    "accelerated_mode", "max_credits_mode", "target_credit_load",
    "planned_course_source",
}

# Result list fields worth counting in the summary log
_RESULT_COUNT_FIELDS = {
    "plans", "semester_plans", "checks", "per_course_breakdown",
    "grade_overrides", "gpa_neutral_courses", "default_distribution",
    "personalized_distribution", "honors_checks", "non_course_blockers",
    "next_steps",
}


def _duration_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _summarize_params(params: dict) -> dict:
    """Build a PII-safe log-friendly summary of ALE operation params."""
    try:
        summary: dict = {}
        for k, v in params.items():
            if k in _SAFE_SCALAR_PARAM_KEYS:
                summary[k] = v
            elif isinstance(v, list):
                preview = [
                    c.get("course_code") if isinstance(c, dict) else str(c)
                    for c in v[:3]
                ]
                summary[k] = {"count": len(v), "preview": preview}
            elif isinstance(v, dict):
                summary[k] = {"_type": "dict", "keys": list(v.keys())[:5]}
        return summary
    except Exception:
        return {}


def _summarize_kg_data(kg_data: dict) -> dict:
    """Build a PII-safe log-friendly summary of kg_data passed to the adapter."""
    try:
        summary: dict = {}
        av = kg_data.get("available_courses")
        if isinstance(av, list):
            preview = [c.get("course_code") for c in av[:3] if isinstance(c, dict)]
            summary["available_courses"] = {"count": len(av), "preview": preview}
        zc = kg_data.get("required_zero_credit_courses")
        if zc is not None:
            summary["required_zero_credit_courses"] = {"count": len(zc), "values": list(zc)[:5]}
        ccl = kg_data.get("course_credit_lookup")
        if isinstance(ccl, dict):
            summary["course_credit_lookup"] = {"count": len(ccl)}
        prereqs = kg_data.get("course_prerequisites")
        if prereqs is not None:
            summary["course_prerequisites"] = {"count": len(prereqs), "preview": list(prereqs)[:3]}
        cct = kg_data.get("course_credit_threshold")
        if cct is not None:
            summary["course_credit_threshold"] = cct
        return summary
    except Exception:
        return {}


def _summarize_result(result: dict) -> dict:
    """Build a compact log-friendly summary of an ALE operation result."""
    try:
        summary: dict = {}
        reason_codes = result.get("reason_codes")
        if reason_codes:
            summary["reason_codes"] = reason_codes
        rdm = result.get("required_data_missing")
        if rdm:
            summary["required_data_missing_count"] = len(rdm)
        warnings = result.get("warnings")
        if warnings:
            summary["warnings_count"] = len(warnings)
        counts: dict = {}
        for field in _RESULT_COUNT_FIELDS:
            val = result.get(field)
            if isinstance(val, list):
                counts[field] = len(val)
        if counts:
            summary["counts"] = counts
        for scalar in ("projected_cgpa", "projected_graduation_semester", "honors_status"):
            if result.get(scalar) is not None:
                summary[scalar] = result[scalar]
        return summary
    except Exception:
        return {}


def _as_dict(value):
    """Normalize a rule bundle value to a plain dict."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


_LEVEL_MAP = {1: "Freshman", 2: "Sophomore", 3: "Junior", 4: "Senior"}


def _map_student_level(level) -> str | None:
    """Map integer SCP level (1–4) to ALE string level. Returns None for invalid values."""
    return _LEVEL_MAP.get(level)


def _compute_zero_credit_requirement_passed(
    sc: StudentContext,
    kg_data: dict,
    params: dict,
) -> bool | None:
    """
    Determine whether all required zero-credit courses have been passed.

    sc.zero_credit_courses_passed is a list of passed course codes — NOT a boolean.
    A non-empty list does NOT imply the requirement is satisfied; the required course
    list must be explicitly provided via kg_data or params.

    Returns:
        True   — all required zero-credit courses are in sc.zero_credit_courses_passed
        False  — at least one required zero-credit course is missing
        None   — required list unavailable; caller must return cannot_compute
    """
    required = kg_data.get("required_zero_credit_courses")
    if required is None:
        required = params.get("required_zero_credit_courses")
    if required is None:
        return None
    return set(required).issubset(set(sc.zero_credit_courses_passed))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def call(
    operation: str,
    student_context: StudentContext,
    rule_bundles: dict,
    kg_data: dict | None = None,
    params: dict | None = None,
    trace_id: str = "",
) -> dict:
    start = time.perf_counter()
    kg_data = kg_data or {}
    params = params or {}
    logger.info(
        "ALEAdapter.call start trace_id=%s operation=%s params=%s kg_data=%s",
        trace_id, operation,
        _summarize_params(params),
        _summarize_kg_data(kg_data),
    )
    try:
        result = _dispatch(operation, student_context, rule_bundles, kg_data, params)
        status = result.get("status", "unknown")
        summary = _summarize_result(result)
        log = logger.warning if status in {"cannot_compute", "error"} else logger.info
        log(
            "ALEAdapter.call result trace_id=%s operation=%s status=%s "
            "summary=%s duration_ms=%d",
            trace_id, operation, status, summary, _duration_ms(start),
        )
        return result
    except ValidationError as exc:
        logger.error(
            "ALEAdapter.call operation=%s: input validation failed — missing or invalid "
            "StudentContext field or rule bundle: %s", operation, exc
        )
        result = {
            "status": "cannot_compute",
            "reason_codes": ["invalid_input"],
            "required_data_missing": [],
            "message": str(exc),
            "operation": operation,
        }
        logger.warning(
            "ALEAdapter.call result trace_id=%s operation=%s status=cannot_compute "
            "reason_codes=%s duration_ms=%d",
            trace_id, operation, ["invalid_input"], _duration_ms(start),
        )
        return result
    except ValueError as exc:
        logger.error("ALEAdapter.call operation=%s: bad value or unknown operation: %s", operation, exc)
        result = {"status": "error", "message": str(exc), "operation": operation}
        logger.warning(
            "ALEAdapter.call result trace_id=%s operation=%s status=error duration_ms=%d",
            trace_id, operation, _duration_ms(start),
        )
        return result
    except Exception as exc:
        logger.error("ALEAdapter.call operation=%s failed unexpectedly: %s", operation, exc)
        result = {"status": "error", "message": str(exc), "operation": operation}
        logger.warning(
            "ALEAdapter.call result trace_id=%s operation=%s status=error duration_ms=%d",
            trace_id, operation, _duration_ms(start),
        )
        return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_rules(rule_bundles: dict, key: str, model_class):
    try:
        bundle_dict = _as_dict(rule_bundles[key])
        return model_class(**bundle_dict)
    except (KeyError, Exception):
        logger.warning("ALEAdapter: rule bundle missing or invalid: '%s'", key)
        raise ValueError(f"rule_bundles missing or invalid: '{key}'")


def _map_course_history(
    course_history,
    grading_scale_rules: dict,
    course_credit_lookup: dict[str, int] | None = None,
) -> list[CourseHistoryEntry]:
    """
    NOTE: credit_hours patching is orchestrator responsibility.
    Course transcript credit_hours are currently 0 (SCP sentinel).
    Until orchestrator patches them from KG, honors CGPA trajectory
    will be incomplete.
    """
    _NO_POINTS_GRADES = {"P", "Con", "I", "W"}
    _NO_POINTS_STATUSES = {"in_progress", "incomplete", "withdrawn"}
    letter_to_points = grading_scale_rules.get("letter_to_points", {})

    entries = []
    for r in course_history:
        grade = r.grade
        status = r.status
        if (
            grade is None
            or grade in _NO_POINTS_GRADES
            or status in _NO_POINTS_STATUSES
            or grade not in letter_to_points
        ):
            grade_points = None
        else:
            grade_points = letter_to_points[grade]

        real_credits = (
            course_credit_lookup.get(r.course_code, r.credit_hours)
            if course_credit_lookup
            else r.credit_hours
        )
        # credit_hours=None means SCP has no authoritative credit data.
        # Fall back to 0 so CourseHistoryEntry.credits (int) is satisfied.
        # Orchestrator must patch credits from KG before ALE graduation audit.
        safe_credits: int = real_credits if real_credits is not None else 0

        entries.append(CourseHistoryEntry(
            course_code=r.course_code,
            semester=r.semester_taken,
            semester_type="summer" if "summer" in r.semester_taken.lower() else "regular",
            grade_points=grade_points,
            credits=safe_credits,
            status=status,
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
            credit_threshold=c.get("credit_threshold"),
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
        completed_regular_semesters=sc.completed_regular_semesters,
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
    grading_scale = _parse_rules(rule_bundles, "grading_scale_rules", GradingScaleRules)
    graduation_rules = _parse_rules(rule_bundles, "graduation_requirement_rules", GraduationRequirementRules)
    warning_rules = _parse_rules(rule_bundles, "academic_warning_rules", AcademicWarningRules)
    honors_rules = _parse_rules(rule_bundles, "honors_rules", HonorsRules)

    zero_credit_passed = _compute_zero_credit_requirement_passed(sc, kg_data, params)
    if zero_credit_passed is None:
        logger.warning(
            "ALEAdapter.run_graduation_audit: required_zero_credit_courses not provided "
            "in kg_data or params — returning cannot_compute"
        )
        return {
            "status": "cannot_compute",
            "reason_codes": ["missing_required_zero_credit_course_list"],
            "required_data_missing": [],
            "message": "Cannot determine zero-credit requirement: required_zero_credit_courses not provided.",
            "operation": "run_graduation_audit",
        }

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
        zero_credit_courses_passed=zero_credit_passed,
        course_history=_map_course_history(
            sc.course_history,
            grading_scale.model_dump(),
            course_credit_lookup=kg_data.get("course_credit_lookup"),
        ),
        graduation_rules=graduation_rules,
        warning_rules=warning_rules,
        honors_rules=honors_rules,
    )
    return run_graduation_audit(inp).model_dump()


def _generate_semester_plan(sc: StudentContext, rule_bundles: dict, kg_data: dict, params: dict) -> dict:
    credit_limit_rules = _parse_rules(rule_bundles, "credit_limit_rules", CreditLimitRules)
    graduation_rules = _parse_rules(rule_bundles, "graduation_requirement_rules", GraduationRequirementRules)

    summer_rules = None
    if "summer_semester_rules" in rule_bundles:
        summer_rules = _parse_rules(rule_bundles, "summer_semester_rules", SummerSemesterRules)

    student_level = _map_student_level(sc.level)
    if student_level is None:
        logger.warning(
            "ALEAdapter.generate_semester_plan: invalid student level=%r (expected 1–4)", sc.level
        )
        return {
            "status": "cannot_compute",
            "reason_codes": ["invalid_student_level"],
            "required_data_missing": ["student_level"],
            "message": f"Student level '{sc.level}' is not a valid academic level (expected 1–4).",
            "operation": "generate_semester_plan",
        }

    inp = GenerateSemesterPlanInput(
        study_status=sc.study_status,
        completed_courses=sc.completed_courses,
        failed_courses=sc.failed_courses,
        in_progress_courses=sc.in_progress_courses,
        retake_count=sc.retake_count,
        total_improve_retakes_used=sc.total_improve_retakes_used,
        current_cgpa=sc.cgpa,
        cumulative_passed_hours=sc.total_credit_hours_earned,
        student_level=student_level,
        official_track=sc.track_id,
        incomplete_grade_flag=_derive_incomplete_flag(sc.course_history),
        available_courses=_map_available_courses(kg_data),
        credit_limit_rules=credit_limit_rules,
        graduation_rules=graduation_rules,
        summer_semester_rules=summer_rules,
        target_semester_type=params["target_semester_type"],
        target_track=params.get("target_track", None),
        target_credit_load=params.get("target_credit_load", None),
        max_credits_mode=params.get("max_credits_mode", False),
    )
    return generate_semester_plan(inp).model_dump()


def _generate_graduation_roadmap(sc: StudentContext, rule_bundles: dict, kg_data: dict, params: dict) -> dict:
    grading_scale = _parse_rules(rule_bundles, "grading_scale_rules", GradingScaleRules)
    credit_limit_rules = _parse_rules(rule_bundles, "credit_limit_rules", CreditLimitRules)
    graduation_rules = _parse_rules(rule_bundles, "graduation_requirement_rules", GraduationRequirementRules)
    student_level_rules = _parse_rules(rule_bundles, "student_level_rules", StudentLevelRules)

    summer_rules = None
    if "summer_semester_rules" in rule_bundles:
        summer_rules = _parse_rules(rule_bundles, "summer_semester_rules", SummerSemesterRules)

    warning_rules = None
    if "academic_warning_rules" in rule_bundles:
        warning_rules = _parse_rules(rule_bundles, "academic_warning_rules", AcademicWarningRules)

    # Pre-resolve failed-retake grade cap to grade points so ALE stays grade-scale-free
    failed_retake_grade_cap_points: float | None = None
    if "retake_rules" in rule_bundles:
        try:
            retake_rules = _parse_rules(rule_bundles, "retake_rules", RetakeRules)
            cap_pts = resolve_grade(
                retake_rules.failed_first_retake_grade_cap, grading_scale, "_retake_cap"
            )
            failed_retake_grade_cap_points = cap_pts  # None only for P-grade cap (not applicable here)
        except Exception:
            pass  # cap stays None — ALE will not apply a cap

    student_level = _map_student_level(sc.level)
    if student_level is None:
        logger.warning(
            "ALEAdapter.generate_graduation_roadmap: invalid student level=%r (expected 1–4)", sc.level
        )
        return {
            "status": "cannot_compute",
            "reason_codes": ["invalid_student_level"],
            "required_data_missing": ["student_level"],
            "message": f"Student level '{sc.level}' is not a valid academic level (expected 1–4).",
            "operation": "generate_graduation_roadmap",
        }

    zero_credit_passed = _compute_zero_credit_requirement_passed(sc, kg_data, params)
    if zero_credit_passed is None:
        logger.warning(
            "ALEAdapter.generate_graduation_roadmap: required_zero_credit_courses not provided "
            "in kg_data or params — returning cannot_compute"
        )
        return {
            "status": "cannot_compute",
            "reason_codes": ["missing_required_zero_credit_course_list"],
            "required_data_missing": [],
            "message": "Cannot determine zero-credit requirement: required_zero_credit_courses not provided.",
            "operation": "generate_graduation_roadmap",
        }

    raw_grade = params.get("assumed_grade_per_pass", None)
    resolved_grade: float | None = None

    if raw_grade is not None:
        try:
            resolved_grade = resolve_grade(raw_grade, grading_scale, "_assumed_grade")
            if resolved_grade is None:
                return {
                    "status": "cannot_compute",
                    "reason_codes": ["invalid_assumed_grade"],
                    "required_data_missing": [],
                    "message": "P/pass grade cannot be used as the assumed GPA grade for roadmap simulation.",
                    "operation": "generate_graduation_roadmap",
                }
        except GradeResolutionError:
            return {
                "status": "cannot_compute",
                "reason_codes": ["invalid_assumed_grade"],
                "required_data_missing": [],
                "message": f"Cannot resolve assumed grade '{raw_grade}' to grade points.",
                "operation": "generate_graduation_roadmap",
            }

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
        student_level=student_level,
        official_track=sc.track_id,
        incomplete_grade_flag=_derive_incomplete_flag(sc.course_history),
        zero_credit_courses_passed=zero_credit_passed,
        military_status=sc.military_status,
        completed_regular_semesters=sc.completed_regular_semesters,
        consecutive_warnings=sc.consecutive_warnings,
        total_warnings=sc.total_warnings,
        available_courses=_map_available_courses(kg_data),
        credit_limit_rules=credit_limit_rules,
        graduation_rules=graduation_rules,
        summer_semester_rules=summer_rules,
        warning_rules=warning_rules,
        failed_retake_grade_cap_points=failed_retake_grade_cap_points,
        target_semester_type=params["target_semester_type"],
        starting_year=params["starting_year"],
        target_end_semester_type=params.get("target_end_semester_type"),
        target_end_year=params.get("target_end_year"),
        target_track=params.get("target_track", None),
        assumed_grade_per_pass=resolved_grade,
        accelerated_mode=params.get("accelerated_mode", False),
        max_credits_mode=params.get("max_credits_mode", False),
        target_credit_load=params.get("target_credit_load", None),
        student_level_rules=student_level_rules,
    )
    return generate_graduation_roadmap(inp).model_dump()


class ALEAdapter:
    """Thin class wrapper — delegates to the module-level call() function."""
    def call(self, operation, student_context, rule_bundles, kg_data=None, params=None, trace_id=""):
        return call(operation, student_context, rule_bundles, kg_data or {}, params or {}, trace_id=trace_id)
