"""
Orchestrator — controlled execution layer between QU and Composer.

Accepts an ordered list[StructuredQuery] from QU, executes each SQ against
the appropriate engine (KG / RAG / ALE / assembly), and returns a TurnWrapper
containing ordered PerSQResult objects.

Responsibilities:
- Intent-based dispatch (never engine_pattern-based)
- Build effective_context from previous + current-turn session overrides
- Enforce run_graduation_audit uses base StudentContext only
- Wrap every SQ result in PerSQResult; never cascade failures

Does NOT:
- Call understand_query()
- Save or update session state
- Compose natural-language answers
- Duplicate ALE/KG/RAG business logic
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from gateway.models.schemas import (
    EntitySet, LastReferenced, PerSQResult, SessionOverrides,
    SessionState, StudentContext, StructuredQuery, TurnWrapper,
)
from gateway.session_manager import _apply_overrides, build_effective_context
from gateway.utils import get_next_semester

logger = logging.getLogger(__name__)

# ── Intent sets ───────────────────────────────────────────────────────────────

_D1_ALE_INTENTS = frozenset({
    "plan_semester", "generate_graduation_roadmap", "run_graduation_audit",
    "check_course_eligibility", "simulate_gpa_forward", "solve_target_gpa",
})

_D2_KG_INTENTS = frozenset({
    "get_course_info", "get_course_prerequisites", "get_skills_taught",
    "search_courses_by_skill",
})

_D3_CAREER_INTENTS = frozenset({
    "get_role_profile", "get_roles_by_track", "compute_skill_gap",
    "compute_alignment_score", "recommend_courses_to_close_gap",
    "find_best_matching_roles", "estimate_alignment_improvement",
    "get_focus_courses_for_target",
})

_D3_STUDENT_AWARE = frozenset({
    "compute_skill_gap", "compute_alignment_score",
    "recommend_courses_to_close_gap", "find_best_matching_roles",
    "estimate_alignment_improvement", "get_focus_courses_for_target",
})

_D4_TRACK_INTENTS = frozenset({
    "get_track_overview", "compare_tracks",
    "recommend_track_for_role", "recommend_track_for_skill",
})

_STUDENT_REQUIRED_INTENTS = (
    _D1_ALE_INTENTS
    | _D3_STUDENT_AWARE
    | {"get_student_record"}
)

_KG_ADAPTER_ERRORS = frozenset({"kg_unavailable", "unknown_operation", "bad_params", "kg_error"})


# ── Turn-level cache ──────────────────────────────────────────────────────────

@dataclass
class _TurnCaches:
    courses_by_track: dict = field(default_factory=dict)
    course_profile_cache: dict = field(default_factory=dict)
    track_overview_cache: dict = field(default_factory=dict)


# ── Result builders ───────────────────────────────────────────────────────────

def _ok(sq_index: int, intent: str, data: dict, **flags) -> PerSQResult:
    return PerSQResult(sq_index=sq_index, intent=intent, status="success", data=data, **flags)


def _err(sq_index: int, intent: str, code: str, category: Optional[str], detail: str) -> PerSQResult:
    return PerSQResult(
        sq_index=sq_index, intent=intent, status="error",
        error_code=code, error_category=category, error_detail=detail,
    )


def _info(sq_index: int, intent: str, data: dict, **flags) -> PerSQResult:
    return PerSQResult(sq_index=sq_index, intent=intent, status="informational", data=data, **flags)


def _soft(sq_index: int, intent: str, data: dict, citations: Optional[list] = None) -> PerSQResult:
    return PerSQResult(sq_index=sq_index, intent=intent, status="soft_no_evidence",
                       data=data, citations=citations)


def _clarif(sq_index: int, intent: str, prompt: str) -> PerSQResult:
    return PerSQResult(sq_index=sq_index, intent=intent, status="clarification_needed",
                       clarification_prompt=prompt)


def _oos(sq_index: int, intent: str) -> PerSQResult:
    return PerSQResult(sq_index=sq_index, intent=intent, status="out_of_scope",
                       scope_explanation="This query is outside PathFinder's academic advising scope.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _has_assumptions(overrides: SessionOverrides) -> bool:
    return bool(
        overrides.added_courses
        or overrides.assumed_passed_courses
        or overrides.assumed_failed_courses
        or overrides.course_override_type != "none"
    )


def _missing_bundles(rule_bundles: dict, *keys: str) -> list[str]:
    return [k for k in keys if rule_bundles.get(k) is None]


def _missing_ctx_fields(ctx: StudentContext, *fields: str) -> list[str]:
    return [f for f in fields if getattr(ctx, f, None) is None]


def _is_kg_adapter_error(result: dict) -> bool:
    return "error" in result and result["error"] in _KG_ADAPTER_ERRORS


def _collect_turn_overrides(sqs: list[StructuredQuery]) -> tuple[SessionOverrides, bool]:
    """
    Accumulate all per-SQ overrides for this turn.
    Returns (merged_overrides, had_clear).
    had_clear=True means at least one SQ issued a clear action — the caller must
    skip merging with session.overrides so the clear takes effect.
    """
    result = SessionOverrides()
    had_clear = False
    for sq in sqs:
        if sq.session_overrides.override_action == "clear":
            had_clear = True
        result = _apply_overrides(result, sq.session_overrides)
    return result, had_clear


def _build_turn_wrapper(
    turn_id: str,
    session_id: str,
    timestamp: str,
    results: list[PerSQResult],
) -> TurnWrapper:
    statuses = {r.status for r in results}
    has_error = "error" in statuses
    has_clarification = "clarification_needed" in statuses
    has_informational = "informational" in statuses
    has_soft = "soft_no_evidence" in statuses

    productive = statuses - {"error", "clarification_needed"}

    if statuses == {"out_of_scope"}:
        turn_status = "out_of_scope"
    elif has_error and not productive:
        turn_status = "failed"
    elif has_clarification and not has_error:
        turn_status = "needs_clarification"
    elif has_error or has_clarification:
        turn_status = "partial_success"
    else:
        turn_status = "completed"

    return TurnWrapper(
        turn_id=turn_id,
        session_id=session_id,
        timestamp=timestamp,
        results=results,
        result_count=len(results),
        turn_status=turn_status,
        has_error=has_error,
        has_clarification=has_clarification,
        has_informational=has_informational,
        has_soft_no_evidence=has_soft,
    )


# ── Orchestrator ──────────────────────────────────────────────────────────────

class Orchestrator:

    def __init__(self, kg_adapter, rag_adapter, ale_adapter):
        self._kg = kg_adapter
        self._rag = rag_adapter
        self._ale = ale_adapter
        logger.info("Orchestrator: initialised")

    # ── Public API ─────────────────────────────────────────────────────────────

    def execute_turn(
        self,
        sqs: list[StructuredQuery],
        session: SessionState,
        rule_bundles: dict,
    ) -> TurnWrapper:
        """
        Execute an ordered list of StructuredQuery objects and return a TurnWrapper.
        One SQ failing does not cascade.
        Does not write to the session store.
        """
        turn_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # Build execution-time overrides: previous session + current turn
        turn_overrides, had_clear = _collect_turn_overrides(sqs)
        if had_clear:
            # A clear action in this turn discards all prior session overrides
            execution_overrides = turn_overrides
        else:
            execution_overrides = _apply_overrides(session.overrides, turn_overrides)

        # Build effective_context once for the entire turn
        base_context: Optional[StudentContext] = session.student_context
        effective_context: Optional[StudentContext] = None
        if base_context is not None:
            effective_context = build_effective_context(base_context, execution_overrides)

        has_active = _has_assumptions(execution_overrides)
        caches = _TurnCaches()

        results: list[PerSQResult] = []
        for idx, sq in enumerate(sqs):
            logger.info("Orchestrator: SQ[%d] intent=%s entity=%s",
                        idx, sq.intent,
                        sq.entities.course_code or sq.entities.role_id or sq.entities.track_id or "-")
            try:
                result = self._execute_sq(
                    idx, sq,
                    base_context=base_context,
                    effective_context=effective_context,
                    has_active_assumptions=has_active,
                    rule_bundles=rule_bundles,
                    caches=caches,
                )
            except Exception as exc:
                logger.exception("Orchestrator: unhandled error SQ[%d] intent=%s", idx, sq.intent)
                result = _err(idx, sq.intent, "engine_error", None,
                              f"Unexpected error: {type(exc).__name__}")
            logger.info("Orchestrator: SQ[%d] intent=%s → status=%s",
                        idx, sq.intent, result.status)
            results.append(result)

        return _build_turn_wrapper(turn_id, session.session_id, timestamp, results)

    def extract_last_referenced(self, sqs: list[StructuredQuery]) -> Optional[LastReferenced]:
        """Return LastReferenced from the first SQ with resolved entities, or None to preserve existing."""
        for sq in sqs:
            e = sq.entities
            if e.course_code or e.role_id or e.track_id:
                return LastReferenced(
                    course_code=e.course_code,
                    role_id=e.role_id,
                    track_id=e.track_id,
                )
        return None

    # ── Central dispatcher ────────────────────────────────────────────────────

    def _execute_sq(
        self,
        sq_index: int,
        sq: StructuredQuery,
        base_context: Optional[StudentContext],
        effective_context: Optional[StudentContext],
        has_active_assumptions: bool,
        rule_bundles: dict,
        caches: _TurnCaches,
    ) -> PerSQResult:
        intent = sq.intent

        # Control intents — pass-through
        if intent == "clarification_needed":
            return _clarif(sq_index, intent, sq.original_text or "Please clarify your question.")
        if intent == "out_of_scope":
            return _oos(sq_index, intent)

        # Student-context check for student-aware intents
        if intent in _STUDENT_REQUIRED_INTENTS:
            if base_context is None:
                return _err(sq_index, intent, "student_not_found", None,
                            "Student context is unavailable for this query.")
            ctx = effective_context if effective_context is not None else base_context
        else:
            ctx = None

        # Dispatch
        if intent == "plan_semester":
            return self._exec_plan_semester(sq_index, sq, ctx, rule_bundles, caches, has_active_assumptions)
        if intent == "generate_graduation_roadmap":
            return self._exec_graduation_roadmap(sq_index, sq, ctx, rule_bundles, caches, has_active_assumptions)
        if intent == "run_graduation_audit":
            return self._exec_graduation_audit(sq_index, sq, base_context, rule_bundles, caches, has_active_assumptions)
        if intent == "check_course_eligibility":
            return self._exec_check_eligibility(sq_index, sq, ctx, rule_bundles, caches, has_active_assumptions)
        if intent == "simulate_gpa_forward":
            return self._exec_simulate_gpa(sq_index, sq, ctx, rule_bundles, caches, has_active_assumptions)
        if intent == "solve_target_gpa":
            return self._exec_solve_target_gpa(sq_index, sq, ctx, rule_bundles, caches, has_active_assumptions)
        if intent in _D2_KG_INTENTS:
            return self._exec_d2_course(sq_index, sq)
        if intent in _D3_CAREER_INTENTS:
            return self._exec_d3_career(sq_index, sq, ctx, has_active_assumptions)
        if intent in _D4_TRACK_INTENTS:
            return self._exec_d4_track(sq_index, sq, ctx)
        if intent == "policy_query":
            return self._exec_policy(sq_index, sq)
        if intent == "get_student_record":
            return self._exec_student_record(sq_index, sq, ctx, rule_bundles, has_active_assumptions)

        return _err(sq_index, intent, "validation_failed", "result_shape",
                    f"Unrecognised intent: {intent!r}")

    # ── Domain 1 — Academic Planning ─────────────────────────────────────────

    def _exec_plan_semester(self, sq_index, sq, ctx, bundles, caches, assumptions_active) -> PerSQResult:
        intent = "plan_semester"

        if getattr(ctx, "study_status", None) == "Graduated":
            return _info(sq_index, intent, {
                "status": "already_graduated",
                "message": "You have already graduated. Semester planning is not applicable.",
            })

        missing = _missing_ctx_fields(ctx, "cgpa", "cumulative_chs")
        if missing:
            return _err(sq_index, intent, "validation_failed", "field_value",
                        f"Missing student data: {missing}")

        target_semester_type = sq.params.get("target_semester_type")
        if not target_semester_type:
            if ctx.current_semester:
                nxt = get_next_semester(ctx.current_semester)
                target_semester_type = nxt.split()[0]
            else:
                target_semester_type = "Fall"

        required_bundles = ["credit_limit_rules", "graduation_requirement_rules"]
        if target_semester_type.lower() == "summer":
            required_bundles.append("summer_semester_rules")
        missing_b = _missing_bundles(bundles, *required_bundles)
        if missing_b:
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        f"Missing rule bundles: {missing_b}")

        track_id = sq.params.get("target_track") or ctx.track_id
        kg_data, kg_err = self._get_courses_by_track(track_id, caches)
        if kg_err:
            return kg_err._replace(sq_index=sq_index, intent=intent) if hasattr(kg_err, '_replace') else _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
        if "error" in kg_data:
            return _info(sq_index, intent, kg_data)

        params = {
            "target_semester_type": target_semester_type,
            "target_track": sq.params.get("target_track"),
            "target_credit_load": sq.params.get("target_credit_load"),
            "max_credits_mode": sq.params.get("max_credits_mode", False),
        }

        ale_result = self._ale.call("generate_semester_plan", ctx, bundles,
                                    {"available_courses": kg_data.get("courses", [])}, params)
        if ale_result.get("status") == "error":
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        ale_result.get("message", "ALE error."))
        if ale_result.get("status") == "cannot_compute":
            return _info(sq_index, intent, ale_result)

        return _ok(sq_index, intent, ale_result,
                   assumptions_active=True if assumptions_active else None)

    def _exec_graduation_roadmap(self, sq_index, sq, ctx, bundles, caches, assumptions_active) -> PerSQResult:
        intent = "generate_graduation_roadmap"

        if getattr(ctx, "study_status", None) == "Graduated":
            return _info(sq_index, intent, {
                "status": "already_graduated",
                "message": "You have already graduated. There is no remaining roadmap to generate.",
            })

        missing = _missing_ctx_fields(ctx, "cgpa", "cumulative_chs", "cumulative_cps")
        if missing:
            return _err(sq_index, intent, "validation_failed", "field_value",
                        f"Missing student data: {missing}")

        required_bundles = [
            "credit_limit_rules", "graduation_requirement_rules",
            "student_level_rules", "grading_scale_rules",
        ]
        missing_b = _missing_bundles(bundles, *required_bundles)
        if missing_b:
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        f"Missing rule bundles: {missing_b}")

        if ctx.current_semester:
            nxt = get_next_semester(ctx.current_semester)
            target_semester_type = nxt.split()[0]
            starting_year = int(nxt.split()[1])
        else:
            from gateway.utils import get_current_semester
            cur = get_current_semester()
            nxt = get_next_semester(cur)
            target_semester_type = nxt.split()[0]
            starting_year = int(nxt.split()[1])

        track_id = sq.params.get("target_track") or ctx.track_id
        kg_data, kg_err = self._get_courses_by_track(track_id, caches)
        if kg_err:
            return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
        if "error" in kg_data:
            return _info(sq_index, intent, kg_data)

        params = {
            "target_semester_type": target_semester_type,
            "starting_year": starting_year,
            "target_track": sq.params.get("target_track"),
            "accelerated_mode": sq.params.get("accelerated_mode", False),
            "max_credits_mode": sq.params.get("max_credits_mode", False),
            "target_credit_load": sq.params.get("target_credit_load"),
            "assumed_grade_per_pass": sq.params.get("assumed_grade_per_pass"),
        }

        ale_result = self._ale.call("generate_graduation_roadmap", ctx, bundles,
                                    {"available_courses": kg_data.get("courses", [])}, params)
        if ale_result.get("status") == "error":
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        ale_result.get("message", "ALE error."))
        if ale_result.get("status") == "cannot_compute":
            return _info(sq_index, intent, ale_result)

        return _ok(sq_index, intent, ale_result,
                   assumptions_active=True if assumptions_active else None)

    def _exec_graduation_audit(self, sq_index, sq, base_ctx, bundles, caches, has_active_assumptions) -> PerSQResult:
        intent = "run_graduation_audit"

        if base_ctx is None:
            return _err(sq_index, intent, "student_not_found", None, "Student context unavailable.")

        required_bundles = [
            "graduation_requirement_rules", "academic_warning_rules",
            "honors_rules", "grading_scale_rules",
        ]
        missing_b = _missing_bundles(bundles, *required_bundles)
        if missing_b:
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        f"Missing rule bundles: {missing_b}")

        # Build course_credit_lookup from KG for transcript codes
        course_credit_lookup: dict[str, int] = {}
        unique_codes = list({r.course_code for r in base_ctx.course_history})
        for code in unique_codes:
            if code not in caches.course_profile_cache:
                result = self._kg.call("get_course_profile", {"course_code": code})
                caches.course_profile_cache[code] = result
            profile = caches.course_profile_cache.get(code, {})
            credits = profile.get("credits")
            if credits is not None:
                course_credit_lookup[code] = credits

        ale_result = self._ale.call(
            "run_graduation_audit", base_ctx, bundles,
            {"course_credit_lookup": course_credit_lookup}, {},
        )
        if ale_result.get("status") == "error":
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        ale_result.get("message", "ALE error."))
        if ale_result.get("status") == "cannot_compute":
            return _info(sq_index, intent, ale_result)

        return _ok(sq_index, intent, ale_result,
                   assumptions_excluded=True if has_active_assumptions else None)

    def _exec_check_eligibility(self, sq_index, sq, ctx, bundles, caches, assumptions_active) -> PerSQResult:
        intent = "check_course_eligibility"

        missing_b = _missing_bundles(bundles, "retake_rules")
        if missing_b:
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        f"Missing rule bundles: {missing_b}")

        target_code = sq.entities.course_code
        if not target_code:
            return _clarif(sq_index, intent, "Which course would you like to check eligibility for?")

        # Derive attempt type
        history_codes = [r.course_code for r in ctx.course_history]
        if target_code not in history_codes:
            attempt_type = "first_attempt"
        elif target_code in ctx.failed_courses:
            attempt_type = "failed_retake"
        elif target_code in ctx.completed_courses:
            attempt_type = "improve_retake"
        else:
            attempt_type = "first_attempt"

        if attempt_type == "improve_retake" and ctx.cgpa is None:
            return _err(sq_index, intent, "validation_failed", "field_value",
                        "CGPA required for improve-retake eligibility check.")

        # KG: get prerequisites
        prereq_result = self._kg.call("get_prerequisites", {
            "course_code": target_code, "depth": "direct",
        })
        if _is_kg_adapter_error(prereq_result):
            return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")

        direct_prereqs = [
            p["course_code"] for p in prereq_result.get("direct_prerequisites", [])
        ]
        credit_threshold = None
        for item in prereq_result.get("non_course_prerequisites", []):
            if item.get("type") == "CREDIT_THRESHOLD":
                credit_threshold = item.get("value")
                break

        kg_data = {
            "course_prerequisites": direct_prereqs,
            "course_credit_threshold": credit_threshold,
        }
        params = {
            "target_course_code": target_code,
            "attempt_type": attempt_type,
        }

        ale_result = self._ale.call("check_course_eligibility", ctx, bundles, kg_data, params)
        if ale_result.get("status") == "error":
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        ale_result.get("message", "ALE error."))
        if ale_result.get("status") == "cannot_compute":
            return _info(sq_index, intent, ale_result)

        # Enrich missing/completed prerequisites with course names from KG data
        prereq_name_map = {
            p["course_code"]: p.get("name") or p["course_code"]
            for p in prereq_result.get("direct_prerequisites", [])
            if p.get("course_code")
        }
        for field in ("missing_prerequisites", "completed_prerequisites"):
            raw = ale_result.get(field) or []
            if raw and isinstance(raw[0], str):
                ale_result[field] = [
                    {"course_code": c, "name": prereq_name_map.get(c, c)}
                    for c in raw
                ]

        return _ok(sq_index, intent, ale_result,
                   assumptions_active=True if assumptions_active else None)

    def _exec_simulate_gpa(self, sq_index, sq, ctx, bundles, caches, assumptions_active) -> PerSQResult:
        intent = "simulate_gpa_forward"

        missing = _missing_ctx_fields(ctx, "cgpa", "cumulative_chs", "cumulative_cps")
        if missing:
            return _err(sq_index, intent, "validation_failed", "field_value",
                        f"Missing student data: {missing}")

        missing_b = _missing_bundles(bundles, "grading_scale_rules", "retake_rules")
        if missing_b:
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        f"Missing rule bundles: {missing_b}")

        # Resolve planned courses: from params or current in-progress
        expected_grades: dict = sq.params.get("expected_grades", {})
        if sq.params.get("planned_courses"):
            planned_course_codes = sq.params["planned_courses"]
        elif expected_grades:
            planned_course_codes = list(expected_grades.keys())
        elif ctx.in_progress_courses:
            planned_course_codes = list(ctx.in_progress_courses)
        else:
            return _clarif(sq_index, intent,
                           "Which courses are you planning to take? Please specify course codes or names.")

        # KG: get_course_profile per planned course for real credits
        from engines.ale.schemas import PlannedCourseGPA
        planned_course_gpa_list = []
        for code in planned_course_codes:
            if code not in caches.course_profile_cache:
                profile = self._kg.call("get_course_profile", {"course_code": code})
                caches.course_profile_cache[code] = profile
            profile = caches.course_profile_cache.get(code, {})
            if _is_kg_adapter_error(profile):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")

            credits = profile.get("credits") or 3
            expected_grade = expected_grades.get(code)
            # footprint = course already affected CGPA (graded); in-progress (no terminal grade) is NOT a footprint
            has_footprint = (code in ctx.failed_courses or code in ctx.completed_courses)
            old_grade = None
            is_retake = False
            retake_type = "first_attempt"
            improve_retake_number = None
            if has_footprint:
                if code in ctx.failed_courses:
                    retake_type = "failed_retake"
                    for r in reversed(ctx.course_history):
                        if r.course_code == code and r.grade:
                            old_grade = r.grade
                            break
                elif code in ctx.completed_courses:
                    retake_type = "improve_retake"
                    is_retake = True
                    improve_retake_number = ctx.retake_count.get(code, 1)
                    for r in reversed(ctx.course_history):
                        if r.course_code == code and r.grade:
                            old_grade = r.grade
                            break

            planned_course_gpa_list.append(PlannedCourseGPA(
                course_code=code,
                course_name=profile.get("name", code),
                credits=credits,
                expected_grade=expected_grade,
                attempt_type=retake_type,
                has_cgpa_footprint=has_footprint,
                old_grade=old_grade,
                improve_retake_number=improve_retake_number,
                is_currently_in_progress=(code in ctx.in_progress_courses),
            ))

        params = {
            "current_cgpa": ctx.cgpa,
            "gpa_counted_credits": ctx.cumulative_chs,
            "current_quality_points": ctx.cumulative_cps,
            "planned_courses": planned_course_gpa_list,
            "excluded_in_progress_courses": [],
        }

        ale_result = self._ale.call("simulate_gpa_forward", ctx, bundles, {}, params)
        if ale_result.get("status") == "error":
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        ale_result.get("message", "ALE error."))
        if ale_result.get("status") == "cannot_compute":
            return _info(sq_index, intent, ale_result)

        return _ok(sq_index, intent, ale_result,
                   assumptions_active=True if assumptions_active else None)

    def _exec_solve_target_gpa(self, sq_index, sq, ctx, bundles, caches, assumptions_active) -> PerSQResult:
        intent = "solve_target_gpa"

        target_cgpa = sq.params.get("target_gpa") or sq.params.get("target_cgpa")
        if target_cgpa is None:
            return _clarif(sq_index, intent, "What target GPA are you aiming for?")

        missing = _missing_ctx_fields(ctx, "cgpa", "cumulative_chs", "cumulative_cps")
        if missing:
            return _err(sq_index, intent, "validation_failed", "field_value",
                        f"Missing student data: {missing}")

        missing_b = _missing_bundles(bundles, "grading_scale_rules", "retake_rules",
                                     "graduation_requirement_rules")
        if missing_b:
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        f"Missing rule bundles: {missing_b}")

        # If already met, pass empty planned_courses — ALE returns already_met shape
        from engines.ale.schemas import PlannedCourseTarget
        planned_courses_target = []

        if ctx.cgpa is None or target_cgpa > ctx.cgpa:
            source_codes = sq.params.get("planned_courses") or ctx.in_progress_courses or []
            planned_course_source = (
                "explicit_user_courses" if sq.params.get("planned_courses") else
                "current_in_progress_courses" if ctx.in_progress_courses else
                "none"
            )
            for code in source_codes:
                if code not in caches.course_profile_cache:
                    profile = self._kg.call("get_course_profile", {"course_code": code})
                    caches.course_profile_cache[code] = profile
                profile = caches.course_profile_cache.get(code, {})
                if _is_kg_adapter_error(profile):
                    return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
                credits = profile.get("credits") or 3
                related_completed = None
                historical_grade = None
                if code in ctx.completed_courses or code in ctx.failed_courses:
                    for r in reversed(ctx.course_history):
                        if r.course_code == code and r.grade:
                            related_completed = code
                            historical_grade = r.grade
                            break
                # footprint = course already affected CGPA; in-progress (no terminal grade) is NOT a footprint
                has_footprint = (code in ctx.failed_courses or code in ctx.completed_courses)
                if code in ctx.failed_courses:
                    attempt_type = "failed_retake"
                elif code in ctx.completed_courses:
                    attempt_type = "improve_retake"
                else:
                    attempt_type = "first_attempt"
                planned_courses_target.append(PlannedCourseTarget(
                    course_code=code,
                    course_name=profile.get("name", code),
                    credits=credits,
                    attempt_type=attempt_type,
                    has_cgpa_footprint=has_footprint,
                    related_completed_course=related_completed,
                    historical_grade=historical_grade,
                ))
        else:
            planned_course_source = "already_met"

        params = {
            "current_cgpa": ctx.cgpa,
            "gpa_counted_credits": ctx.cumulative_chs,
            "current_quality_points": ctx.cumulative_cps,
            "target_cgpa": float(target_cgpa),
            "planned_courses": planned_courses_target,
            "assumed_grade_per_semester": sq.params.get("assumed_grade_per_semester", None),
            "credits_per_semester": sq.params.get("credits_per_semester", 18),
            "planned_course_source": planned_course_source,
        }

        ale_result = self._ale.call("solve_target_gpa", ctx, bundles, {}, params)
        if ale_result.get("status") == "error":
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        ale_result.get("message", "ALE error."))
        if ale_result.get("status") == "cannot_compute":
            return _info(sq_index, intent, ale_result)

        return _ok(sq_index, intent, ale_result,
                   assumptions_active=True if assumptions_active else None)

    # ── Domain 2 — Course Info ────────────────────────────────────────────────

    def _exec_d2_course(self, sq_index: int, sq: StructuredQuery) -> PerSQResult:
        intent = sq.intent

        if intent == "get_course_info":
            code = sq.entities.course_code
            if not code:
                return _clarif(sq_index, intent, "Which course would you like to know about?")
            result = self._kg.call("get_course_profile", {"course_code": code})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result)

        if intent == "get_course_prerequisites":
            code = sq.entities.course_code
            if not code:
                return _clarif(sq_index, intent, "Which course's prerequisites would you like to see?")
            depth = sq.params.get("depth", "direct")
            result = self._kg.call("get_prerequisites", {"course_code": code, "depth": depth})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result)

        if intent == "get_skills_taught":
            code = sq.entities.course_code
            if not code:
                return _clarif(sq_index, intent, "Which course would you like to see skills for?")
            result = self._kg.call("get_skills_taught", {"course_code": code})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result)

        if intent == "search_courses_by_skill":
            skill = sq.entities.skill_id
            if not skill:
                return _clarif(sq_index, intent, "Which skill are you looking for courses about?")
            result = self._kg.call("search_courses_by_skill", {"skill_ids": [skill]})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result)

        return _err(sq_index, intent, "validation_failed", "result_shape",
                    f"Unhandled D2 intent: {intent}")

    # ── Domain 3 — Career / Role ──────────────────────────────────────────────

    def _exec_d3_career(
        self, sq_index: int, sq: StructuredQuery,
        ctx: Optional[StudentContext], assumptions_active: bool,
    ) -> PerSQResult:
        intent = sq.intent
        e = sq.entities
        completed = ctx.completed_courses if ctx else []

        # Empty completed courses early return for student-aware intents (except get_focus)
        if intent in (_D3_STUDENT_AWARE - {"get_focus_courses_for_target"}):
            if not completed:
                return _info(sq_index, intent,
                             {"reason": "no_completed_courses",
                              "message": "No completed courses available for this analysis."})

        if intent == "get_role_profile":
            role = e.role_id
            if not role:
                return _clarif(sq_index, intent, "Which role would you like to know about?")
            result = self._kg.call("get_role_profile", {"role_id": role})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result)

        if intent == "get_roles_by_track":
            track = e.track_id or (ctx.track_id if ctx else None)
            if not track:
                return _clarif(sq_index, intent, "Which track would you like to see roles for?")
            result = self._kg.call("get_roles_by_track", {"track_id": track})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result)

        if intent == "compute_skill_gap":
            role = e.role_id
            if not role:
                return _clarif(sq_index, intent, "Which role would you like to check your skill gap for?")
            result = self._kg.call("compute_skill_gap", {
                "role_id": role, "completed_courses": completed,
            })
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result,
                       assumptions_active=True if assumptions_active else None)

        if intent == "compute_alignment_score":
            role = e.role_id
            if not role:
                return _clarif(sq_index, intent, "Which role would you like to compute alignment for?")
            result = self._kg.call("compute_alignment_score", {
                "role_id": role, "completed_courses": completed,
            })
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result,
                       assumptions_active=True if assumptions_active else None)

        if intent == "recommend_courses_to_close_gap":
            role = e.role_id
            if not role:
                return _clarif(sq_index, intent, "Which role are you trying to close the gap for?")
            result = self._kg.call("recommend_courses_to_close_gap", {
                "role_id": role, "completed_courses": completed,
            })
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result,
                       assumptions_active=True if assumptions_active else None)

        if intent == "find_best_matching_roles":
            result = self._kg.call("find_best_matching_roles", {"completed_courses": completed})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result,
                       assumptions_active=True if assumptions_active else None)

        if intent == "estimate_alignment_improvement":
            role = e.role_id
            if not role:
                return _clarif(sq_index, intent, "Which role are you estimating improvement for?")
            planned = sq.params.get("planned_courses") or (ctx.in_progress_courses if ctx else [])
            if not planned:
                return _clarif(sq_index, intent, "Which courses are you planning to take?")
            result = self._kg.call("estimate_alignment_improvement", {
                "role_id": role, "completed_courses": completed, "planned_courses": planned,
            })
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result,
                       assumptions_active=True if assumptions_active else None)

        if intent == "get_focus_courses_for_target":
            target_id = e.role_id or e.track_id
            target_type = "role" if e.role_id else "track"
            if not target_id:
                target_id = ctx.track_id if ctx else None
                target_type = "track"
            if not target_id:
                return _clarif(sq_index, intent, "Which role or track are you focusing on?")
            result = self._kg.call("get_focus_courses_for_target", {
                "target_id": target_id,
                "target_type": target_type,
                "completed_courses": completed,
            })
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result,
                       assumptions_active=True if assumptions_active else None)

        return _err(sq_index, intent, "validation_failed", "result_shape",
                    f"Unhandled D3 intent: {intent}")

    # ── Domain 4 — Track Guidance ─────────────────────────────────────────────

    def _exec_d4_track(
        self, sq_index: int, sq: StructuredQuery,
        ctx: Optional[StudentContext],
    ) -> PerSQResult:
        intent = sq.intent
        e = sq.entities

        if intent == "get_track_overview":
            track = e.track_id or (ctx.track_id if ctx else None)
            if not track:
                return _clarif(sq_index, intent, "Which track would you like an overview of?")
            result = self._kg.call("get_track_overview", {"track_id": track})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result)

        if intent == "compare_tracks":
            track1 = e.track_id or (ctx.track_id if ctx else None)
            track2 = (sq.secondary_entities.track_id if sq.secondary_entities else None)
            if not track1 or not track2:
                return _clarif(sq_index, intent, "Which two tracks would you like to compare?")
            if track1 == track2:
                return _info(sq_index, intent, {
                    "error": "identical_tracks_provided",
                    "message": "Please specify two different tracks to compare.",
                })
            result = self._kg.call("compare_tracks", {"track_id_1": track1, "track_id_2": track2})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result)

        if intent == "recommend_track_for_role":
            role = e.role_id
            if not role:
                return _clarif(sq_index, intent, "Which role would you like a track recommendation for?")
            result = self._kg.call("recommend_track_for_role", {"role_id": role})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result)

        if intent == "recommend_track_for_skill":
            skill = e.skill_id
            if not skill:
                return _clarif(sq_index, intent, "Which skill would you like a track recommendation for?")
            result = self._kg.call("recommend_track_for_skill", {"skill_id": skill})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result)

        return _err(sq_index, intent, "validation_failed", "result_shape",
                    f"Unhandled D4 intent: {intent}")

    # ── Domain 5 — Policy ─────────────────────────────────────────────────────

    def _exec_policy(self, sq_index: int, sq: StructuredQuery) -> PerSQResult:
        intent = "policy_query"
        original_text = (sq.original_text or "").strip()
        if not original_text:
            return _clarif(sq_index, intent, "Which policy are you asking about?")

        result = self._rag.execute(original_text)
        answer = result.get("answer", "")
        facts = result.get("extracted_facts", [])
        citations = result.get("citations", [])

        # Detect adapter-level failure
        if not facts and any(
            kw in answer.lower()
            for kw in ("unavailable", "error occurred", "cannot")
        ):
            return _err(sq_index, intent, "engine_error", "rag_adapter",
                        "RAG engine unavailable.")

        if not facts:
            return _soft(sq_index, intent,
                         {"answer": answer, "extracted_facts": []},
                         citations=citations or None)

        return _ok(sq_index, intent,
                   {"answer": answer, "extracted_facts": facts},
                   citations=citations or None)

    # ── Domain 6 — Student Record ─────────────────────────────────────────────

    def _exec_student_record(
        self, sq_index: int, sq: StructuredQuery,
        ctx: StudentContext, rule_bundles: dict,
        assumptions_active: bool,
    ) -> PerSQResult:
        intent = "get_student_record"

        # Compute academic_standing from rule bundles
        warning_rules = rule_bundles.get("academic_warning_rules")
        if ctx.cgpa is None:
            academic_standing = "unknown"
            academic_standing_reason = "cgpa_not_available"
        elif warning_rules is None or not hasattr(warning_rules, "cgpa_warning_threshold"):
            academic_standing = "unknown"
            academic_standing_reason = "missing_academic_warning_rules"
        else:
            threshold = warning_rules.cgpa_warning_threshold
            if ctx.cgpa < threshold or ctx.consecutive_warnings > 0:
                academic_standing = "warning"
            else:
                academic_standing = "good"
            academic_standing_reason = None

        snapshot = {
            "track_id": ctx.track_id,
            "level": ctx.level,
            "cgpa": ctx.cgpa,
            "academic_standing": academic_standing,
            "academic_standing_reason": academic_standing_reason,
            "study_status": ctx.study_status,
            "total_credit_hours_earned": ctx.total_credit_hours_earned,
            "current_semester": ctx.current_semester,
            "consecutive_warnings": ctx.consecutive_warnings,
            "total_warnings": ctx.total_warnings,
            "completed_courses": ctx.completed_courses,
            "in_progress_courses": ctx.in_progress_courses,
            "failed_courses": ctx.failed_courses,
            "first_semester": ctx.first_semester,
        }

        return _ok(sq_index, intent, snapshot,
                   override_state_active=True if assumptions_active else None)

    # ── KG helper ─────────────────────────────────────────────────────────────

    def _get_courses_by_track(
        self, track_id: str, caches: _TurnCaches,
    ) -> tuple[dict, Optional[PerSQResult]]:
        """Return (kg_result, None) on success or (empty_dict, error_result) on adapter error."""
        if track_id not in caches.courses_by_track:
            result = self._kg.call("get_courses_by_track", {"track_id": track_id})
            if _is_kg_adapter_error(result):
                return {}, _err(0, "plan_semester", "engine_error", "kg_adapter", "KG unavailable.")
            caches.courses_by_track[track_id] = result
        return caches.courses_by_track[track_id], None
