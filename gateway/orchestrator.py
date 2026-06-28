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
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

_TRACE = os.getenv("PATHFINDER_TRACE", "").lower() in ("true", "1", "yes")

from gateway.models.schemas import (
    EntitySet, LastReferenced, PerSQResult, SessionOverrides,
    SessionState, StudentContext, StructuredQuery, TurnWrapper,
)
from gateway.session_manager import _apply_overrides, build_effective_context
from gateway.utils import get_next_semester, resolve_relative_semester_text

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

_LEVEL_DISPLAY: dict[int, str] = {1: "Freshman", 2: "Sophomore", 3: "Junior", 4: "Senior"}

_D6_SCALAR_FOCUSES: frozenset[str] = frozenset({
    "cgpa", "academic_level", "academic_standing", "academic_warnings",
    "probation_status", "completed_credits", "last_semester_gpa",
    "study_status", "track", "current_semester",
})

_D6_COMPLETED_ONLY_FOCUSES: frozenset[str] = frozenset({"completed_courses"})
_D6_IN_PROGRESS_ONLY_FOCUSES: frozenset[str] = frozenset({"in_progress_courses"})
_D6_FAILED_ONLY_FOCUSES: frozenset[str] = frozenset({"failed_courses"})
_D6_NO_ENRICH_FOCUSES: frozenset[str] = frozenset({"assumption_acknowledgement", "reset_assumptions"})
_D6_COURSE_STATUS_CHECK_FOCUSES: frozenset[str] = frozenset({"course_status_check"})
_D6_FAILED_HISTORY_FOCUSES: frozenset[str] = frozenset({"failed_course_history"})

_FORBIDDEN_INTENTS = frozenset({
    "plan_next_semester", "get_prerequisites", "handbook_query",
    "check_eligibility", "simulate_gpa", "generate_semester_plan",
    "mixed_course_policy", "get_courses_in_track", "get_track_courses_for_role",
    "get_roles_by_skill", "graduation_audit_with_roadmap",
    "compare_courses", "rank_courses", "get_course_profile",
})

# Intents that only need student context when the query is student-referential
_CONDITIONAL_STUDENT_INTENTS = frozenset({
    "get_roles_by_track", "get_track_overview", "compare_tracks",
})

# ── Route trace: engine + operation labels ────────────────────────────────────

_INTENT_ENGINE_OP: dict[str, tuple[str, str]] = {
    "get_course_info": ("KG", "get_course_profile"),
    "get_course_prerequisites": ("KG", "get_prerequisites"),
    "get_skills_taught": ("KG", "get_skills_taught"),
    "search_courses_by_skill": ("KG", "search_courses_by_skill"),
    "get_role_profile": ("KG", "get_role_profile"),
    "get_roles_by_track": ("KG", "get_roles_by_track"),
    "compute_skill_gap": ("KG", "compute_skill_gap"),
    "compute_alignment_score": ("KG", "compute_alignment_score"),
    "recommend_courses_to_close_gap": ("KG", "recommend_courses_to_close_gap"),
    "find_best_matching_roles": ("KG", "find_best_matching_roles"),
    "estimate_alignment_improvement": ("KG", "estimate_alignment_improvement"),
    "get_focus_courses_for_target": ("KG", "get_focus_courses_for_target"),
    "get_track_overview": ("KG", "get_track_overview"),
    "compare_tracks": ("KG", "compare_tracks"),
    "recommend_track_for_role": ("KG", "recommend_track_for_role"),
    "recommend_track_for_skill": ("KG", "recommend_track_for_skill"),
    "get_student_record": ("StudentContext", "student_record_snapshot"),
    "policy_query": ("RAG", "execute_policy_query"),
    "plan_semester": ("ALE", "generate_semester_plan"),
    "generate_graduation_roadmap": ("ALE", "generate_graduation_roadmap"),
    "run_graduation_audit": ("ALE", "run_graduation_audit"),
    "check_course_eligibility": ("ALE", "check_course_eligibility"),
    "simulate_gpa_forward": ("ALE", "simulate_gpa_forward"),
    "solve_target_gpa": ("ALE", "solve_target_gpa"),
}


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


def _is_unsupported_track(ctx: Optional[StudentContext]) -> bool:
    return ctx is not None and getattr(ctx, "track_status", "supported") == "unsupported"


def _unsupported_track_result(sq_index: int, intent: str, ctx: StudentContext) -> PerSQResult:
    return _info(sq_index, intent, {
        "status": "not_applicable",
        "reason_code": "unsupported_track",
        "track_status": getattr(ctx, "track_status", "unsupported"),
        "track_error_code": getattr(ctx, "track_error_code", None),
        "message": "Your academic track is not currently supported for this planning operation.",
    })


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
        trace_id: str = "",
    ) -> TurnWrapper:
        """
        Execute an ordered list of StructuredQuery objects and return a TurnWrapper.
        One SQ failing does not cascade.
        Does not write to the session store.
        """
        turn_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        _turn_t0 = time.monotonic()

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

        _session_short = session.session_id[:8] if session.session_id else "unknown"
        logger.info(
            "Orchestrator.turn_start trace_id=%s session=%s sq_count=%d "
            "assumptions_active=%s had_clear=%s base_context=%s effective_context=%s",
            trace_id, _session_short, len(sqs), has_active, had_clear,
            base_context is not None, effective_context is not None,
        )

        results: list[PerSQResult] = []
        for idx, sq in enumerate(sqs):
            logger.info(
                "Orchestrator.sq_start trace_id=%s index=%d intent=%s entity=%s",
                trace_id, idx, sq.intent,
                sq.entities.course_code or sq.entities.role_id or sq.entities.track_id or "-",
            )
            _sq_t0 = time.monotonic()
            try:
                result = self._execute_sq(
                    idx, sq,
                    base_context=base_context,
                    effective_context=effective_context,
                    has_active_assumptions=has_active,
                    had_clear=had_clear,
                    rule_bundles=rule_bundles,
                    caches=caches,
                    execution_overrides=execution_overrides,
                    trace_id=trace_id,
                )
            except Exception as exc:
                logger.exception("Orchestrator: unhandled error SQ[%d] intent=%s", idx, sq.intent)
                result = _err(idx, sq.intent, "engine_error", None,
                              f"Unexpected error: {type(exc).__name__}")
            _sq_ms = int((time.monotonic() - _sq_t0) * 1000)
            logger.info(
                "Orchestrator.sq_result trace_id=%s index=%d intent=%s status=%s "
                "error_code=%s error_category=%s has_data=%s citations=%d duration_ms=%d",
                trace_id, idx, sq.intent, result.status,
                result.error_code, result.error_category,
                result.data is not None, len(result.citations or []),
                _sq_ms,
            )
            results.append(result)

        wrapper = _build_turn_wrapper(turn_id, session.session_id, timestamp, results)
        _turn_ms = int((time.monotonic() - _turn_t0) * 1000)
        logger.info(
            "Orchestrator.turn_result trace_id=%s status=%s results=%d "
            "error=%s clarification=%s soft_no_evidence=%s duration_ms=%d",
            trace_id, wrapper.turn_status, wrapper.result_count,
            wrapper.has_error, wrapper.has_clarification, wrapper.has_soft_no_evidence,
            _turn_ms,
        )
        return wrapper

    def extract_last_referenced(self, sqs: list[StructuredQuery]) -> Optional[LastReferenced]:
        """Return LastReferenced from the first SQ with resolved entities, or None to preserve existing."""
        for sq in sqs:
            e = sq.entities
            if e.course_code or e.role_id or e.track_id or e.skill_id:
                return LastReferenced(
                    course_code=e.course_code,
                    role_id=e.role_id,
                    track_id=e.track_id,
                    skill_id=e.skill_id,
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
        had_clear: bool,
        rule_bundles: dict,
        caches: _TurnCaches,
        execution_overrides: Optional[SessionOverrides] = None,
        trace_id: str = "",
    ) -> PerSQResult:
        intent = sq.intent

        # Control intents — pass-through
        if intent == "clarification_needed":
            return _clarif(sq_index, intent, sq.original_text or "Please clarify your question.")
        if intent == "out_of_scope":
            return _oos(sq_index, intent)

        # Reject forbidden/stale intents before routing
        if intent in _FORBIDDEN_INTENTS:
            return _err(sq_index, intent, "validation_failed", "intent",
                        f"Stale or unsupported intent: {intent!r}. Use the active equivalent.")

        # Student-context check for student-aware intents
        if intent in _STUDENT_REQUIRED_INTENTS:
            if base_context is None:
                return _err(sq_index, intent, "student_not_found", None,
                            "Student context is unavailable for this query.")
            ctx = effective_context if effective_context is not None else base_context
            _ctx_mode = "base_context" if intent == "run_graduation_audit" else "effective_context"
        elif (intent in _CONDITIONAL_STUDENT_INTENTS
              and sq.student_referential_fallback
              and base_context is not None):
            ctx = effective_context if effective_context is not None else base_context
            _ctx_mode = "conditional_effective_context"
        else:
            ctx = None
            _ctx_mode = "none"

        logger.debug(
            "Orchestrator.sq_context index=%d intent=%s context_mode=%s "
            "referential_fallback=%s assumptions_active=%s",
            sq_index, intent, _ctx_mode, sq.student_referential_fallback, has_active_assumptions,
        )

        if _TRACE:
            record_focus = sq.params.get("record_focus", "N/A")
            entity_display = (
                sq.entities.course_code or sq.entities.role_id
                or sq.entities.track_id or sq.entities.skill_id or "N/A"
            )
            _engine, _operation = _INTENT_ENGINE_OP.get(intent, ("KG", intent))
            logger.info(
                "Orchestrator.route_trace[%d] trace_id=%s intent=%s context_mode=%s\n"
                "  entity: %s\n"
                "  record_focus: %s\n"
                "  engine: %s operation: %s\n"
                "  assumptions_active: %s",
                sq_index, trace_id, intent, _ctx_mode,
                entity_display, record_focus, _engine, _operation, has_active_assumptions,
            )

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
            return self._exec_student_record(sq_index, sq, ctx, rule_bundles, has_active_assumptions, caches, had_clear, execution_overrides)

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

        # Early unsupported-track gate: block before field validation when no explicit track provided
        if not sq.params.get("target_track") and not sq.entities.track_id and _is_unsupported_track(ctx):
            return _unsupported_track_result(sq_index, intent, ctx)

        missing = _missing_ctx_fields(ctx, "cgpa", "cumulative_chs")
        if missing:
            return _err(sq_index, intent, "validation_failed", "field_value",
                        f"Missing student data: {missing}")

        target_semester_type = sq.params.get("target_semester_type")
        if not target_semester_type:
            raw = sq.params.get("target_semester") or sq.params.get("target_semester_text")
            if raw:
                parts = str(raw).strip().split()
                if parts and parts[0] in ("Fall", "Spring", "Summer"):
                    target_semester_type = parts[0]
                    logger.debug(
                        "Orchestrator.semester_resolved intent=%s method=explicit raw=%.80s resolved=%s",
                        intent, repr(raw), target_semester_type,
                    )
                else:
                    resolved = resolve_relative_semester_text(str(raw), ctx.current_semester or "")
                    if resolved:
                        target_semester_type = resolved[0]
                        logger.debug(
                            "Orchestrator.semester_resolved intent=%s method=relative raw=%.80s resolved=%s",
                            intent, repr(raw), target_semester_type,
                        )
                    else:
                        logger.warning(
                            "Orchestrator.semester_resolution_failed intent=%s raw=%.80s",
                            intent, repr(raw),
                        )
                        return _err(sq_index, intent, "validation_failed", "field_value",
                                    f"Cannot parse target semester: {raw!r}. "
                                    f"Expected 'Fall YYYY' or a relative phrase like '3 Falls from now'.")
            elif ctx.current_semester:
                nxt = get_next_semester(ctx.current_semester)
                target_semester_type = nxt.split()[0]
            else:
                target_semester_type = "Fall"
        if target_semester_type not in ("Fall", "Spring", "Summer"):
            return _err(sq_index, intent, "validation_failed", "field_value",
                        f"Invalid target_semester_type: {target_semester_type!r}. Expected Fall, Spring, or Summer.")

        required_bundles = ["credit_limit_rules", "graduation_requirement_rules"]
        if target_semester_type.lower() == "summer":
            required_bundles.append("summer_semester_rules")
        missing_b = _missing_bundles(bundles, *required_bundles)
        if missing_b:
            return _err(sq_index, intent, "engine_error", "ale_adapter",
                        f"Missing rule bundles: {missing_b}")

        # Track resolution priority: explicit param → explicit entity → student track
        track_id = sq.params.get("target_track") or sq.entities.track_id or ctx.track_id
        if not track_id:
            if _is_unsupported_track(ctx):
                return _unsupported_track_result(sq_index, intent, ctx)
            return _err(sq_index, intent, "validation_failed", "field_value",
                        "No academic track available for semester planning.")
        if not sq.params.get("target_track") and not sq.entities.track_id and _is_unsupported_track(ctx):
            return _unsupported_track_result(sq_index, intent, ctx)
        kg_data, kg_err = self._get_courses_by_track(track_id, caches)
        if kg_err:
            return kg_err._replace(sq_index=sq_index, intent=intent) if hasattr(kg_err, '_replace') else _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
        if "error" in kg_data:
            return _info(sq_index, intent, kg_data)

        params = {
            "target_semester_type": target_semester_type,
            "target_track": track_id,  # resolved: params → entities.track_id → ctx.track_id
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

        # Early unsupported-track gate: block before field validation when no explicit track provided
        if not sq.params.get("target_track") and not sq.entities.track_id and _is_unsupported_track(ctx):
            return _unsupported_track_result(sq_index, intent, ctx)

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

        # Track resolution priority: explicit param → explicit entity → student track
        track_id = sq.params.get("target_track") or sq.entities.track_id or ctx.track_id
        if not track_id:
            if _is_unsupported_track(ctx):
                return _unsupported_track_result(sq_index, intent, ctx)
            return _err(sq_index, intent, "validation_failed", "field_value",
                        "No academic track available for roadmap generation.")
        if not sq.params.get("target_track") and not sq.entities.track_id and _is_unsupported_track(ctx):
            return _unsupported_track_result(sq_index, intent, ctx)
        kg_data, kg_err = self._get_courses_by_track(track_id, caches)
        if kg_err:
            return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
        if "error" in kg_data:
            return _info(sq_index, intent, kg_data)

        required_zero_credit_courses = [
            c["course_code"]
            for c in kg_data.get("courses", [])
            if c.get("credits") == 0
        ]

        # Target-semester mode: parse explicit target like "Fall 2027" or "3 Falls from now"
        target_end_semester_type: Optional[str] = None
        target_end_year: Optional[int] = None
        raw_target_semester = sq.params.get("target_semester") or sq.params.get("target_semester_text")
        if raw_target_semester:
            parts = str(raw_target_semester).strip().split()
            if len(parts) == 2 and parts[0] in ("Fall", "Spring", "Summer"):
                try:
                    target_end_semester_type = parts[0]
                    target_end_year = int(parts[1])
                    logger.debug(
                        "Orchestrator.semester_resolved intent=%s method=explicit raw=%.80s resolved=%s %d",
                        intent, repr(raw_target_semester), target_end_semester_type, target_end_year,
                    )
                except (ValueError, IndexError):
                    return _err(sq_index, intent, "validation_failed", "field_value",
                                f"Cannot parse target semester: {raw_target_semester!r}")
            else:
                resolved = resolve_relative_semester_text(
                    str(raw_target_semester), ctx.current_semester or ""
                )
                if resolved:
                    target_end_semester_type, target_end_year = resolved
                    logger.debug(
                        "Orchestrator.semester_resolved intent=%s method=relative raw=%.80s resolved=%s %d",
                        intent, repr(raw_target_semester), target_end_semester_type, target_end_year,
                    )
                else:
                    logger.warning(
                        "Orchestrator.semester_resolution_failed intent=%s raw=%.80s",
                        intent, repr(raw_target_semester),
                    )
                    return _err(sq_index, intent, "validation_failed", "field_value",
                                f"Cannot parse target semester: {raw_target_semester!r}")

        params = {
            "target_semester_type": target_semester_type,
            "starting_year": starting_year,
            "target_track": track_id,  # resolved: params → entities.track_id → ctx.track_id
            "accelerated_mode": sq.params.get("accelerated_mode", False),
            "max_credits_mode": sq.params.get("max_credits_mode", False),
            "target_credit_load": sq.params.get("target_credit_load"),
            "assumed_grade_per_pass": sq.params.get("assumed_grade_per_pass"),
            "target_end_semester_type": target_end_semester_type,
            "target_end_year": target_end_year,
        }

        ale_result = self._ale.call("generate_graduation_roadmap", ctx, bundles,
                                    {
                                        "available_courses": kg_data.get("courses", []),
                                        "required_zero_credit_courses": required_zero_credit_courses,
                                    }, params)
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

        # Gate: audit requires a known track to determine required zero-credit courses
        if not base_ctx.track_id or _is_unsupported_track(base_ctx):
            return _unsupported_track_result(sq_index, intent, base_ctx)

        # Fetch required zero-credit courses for the student's official track
        zc_kg_data, zc_kg_err = self._get_courses_by_track(base_ctx.track_id, caches)
        if zc_kg_err:
            return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
        if "error" in zc_kg_data:
            return _info(sq_index, intent, {
                "status": "not_applicable",
                "reason_code": "track_curriculum_unavailable",
                "message": "Cannot obtain required zero-credit courses for graduation audit.",
            })
        required_zero_credit_courses = [
            c["course_code"]
            for c in zc_kg_data.get("courses", [])
            if c.get("credits") == 0
        ]

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
            {
                "course_credit_lookup": course_credit_lookup,
                "required_zero_credit_courses": required_zero_credit_courses,
            }, {},
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
            if "error" in profile:
                return _info(sq_index, intent, {
                    "status": "cannot_compute",
                    "reason_codes": ["course_not_found"],
                    "message": f"Course {code!r} was not found in the catalogue. Cannot simulate GPA.",
                })
            credits_raw = profile.get("credits")
            if credits_raw is None:
                return _info(sq_index, intent, {
                    "status": "cannot_compute",
                    "reason_codes": ["missing_course_credits"],
                    "message": f"Credit hours for course {code!r} are unavailable. Cannot simulate GPA.",
                })
            credits = credits_raw
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
                    # SCP retake_count tracks total non-withdrawn attempts, not the
                    # improve-retake sequence. MVP-safe: treat as first improve retake;
                    # precise count requires a future SCP per-course improve-retake field.
                    improve_retake_number = 1
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
                if "error" in profile:
                    return _info(sq_index, intent, {
                        "status": "cannot_compute",
                        "reason_codes": ["course_not_found"],
                        "message": f"Course {code!r} was not found in the catalogue. Cannot solve target GPA.",
                    })
                credits_raw = profile.get("credits")
                if credits_raw is None:
                    return _info(sq_index, intent, {
                        "status": "cannot_compute",
                        "reason_codes": ["missing_course_credits"],
                        "message": f"Credit hours for course {code!r} are unavailable. Cannot solve target GPA.",
                    })
                credits = credits_raw
                # footprint = course already affected CGPA; in-progress (no terminal grade) is NOT a footprint
                has_footprint = (code in ctx.failed_courses or code in ctx.completed_courses)
                if code in ctx.failed_courses:
                    attempt_type = "failed_retake"
                elif code in ctx.completed_courses:
                    attempt_type = "improve_retake"
                else:
                    attempt_type = "first_attempt"
                old_grade = None
                if has_footprint:
                    for r in reversed(ctx.course_history):
                        if r.course_code == code and r.grade:
                            old_grade = r.grade
                            break
                # SCP retake_count tracks total non-withdrawn attempts, not the
                # improve-retake sequence. MVP-safe: treat as first improve retake;
                # precise count requires a future SCP per-course improve-retake field.
                improve_retake_number = 1 if attempt_type == "improve_retake" else None
                planned_courses_target.append(PlannedCourseTarget(
                    course_code=code,
                    course_name=profile.get("name", code),
                    credits=credits,
                    attempt_type=attempt_type,
                    has_cgpa_footprint=has_footprint,
                    old_grade=old_grade,
                    improve_retake_number=improve_retake_number,
                    related_completed_course=None,
                    historical_grade=None,
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
                candidate = sq.params.get("attempted_candidate")
                if candidate:
                    return _clarif(sq_index, intent,
                        f"I couldn't find a course named '{candidate}' in the current CIS catalogue. "
                        f"Could you provide the exact course name or code?")
                return _clarif(sq_index, intent, "Which course would you like to know about?")
            result = self._kg.call("get_course_profile", {"course_code": code})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                candidate = sq.params.get("attempted_candidate") or code
                return _info(sq_index, intent, {**result, "attempted_candidate": candidate})
            return _ok(sq_index, intent, result)

        if intent == "get_course_prerequisites":
            code = sq.entities.course_code
            if not code:
                candidate = sq.params.get("attempted_candidate")
                if candidate:
                    return _clarif(sq_index, intent,
                        f"I couldn't find a course named '{candidate}' in the current CIS catalogue. "
                        f"Could you provide the exact course name or code?")
                return _clarif(sq_index, intent, "Which course's prerequisites would you like to see?")
            depth = sq.params.get("depth", "direct")
            result = self._kg.call("get_prerequisites", {"course_code": code, "depth": depth})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                candidate = sq.params.get("attempted_candidate") or code
                return _info(sq_index, intent, {**result, "attempted_candidate": candidate})
            return _ok(sq_index, intent, result)

        if intent == "get_skills_taught":
            code = sq.entities.course_code
            if not code:
                candidate = sq.params.get("attempted_candidate")
                if candidate:
                    return _clarif(sq_index, intent,
                        f"I couldn't find a course named '{candidate}' in the current CIS catalogue. "
                        f"Could you provide the exact course name or code?")
                return _clarif(sq_index, intent, "Which course would you like to see skills for?")
            result = self._kg.call("get_skills_taught", {"course_code": code})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                candidate = sq.params.get("attempted_candidate") or code
                return _info(sq_index, intent, {**result, "attempted_candidate": candidate})
            return _ok(sq_index, intent, result)

        if intent == "search_courses_by_skill":
            skill = sq.entities.skill_id
            # Multi-skill: prefer resolved_skill_ids list when available (also used for session follow-ups)
            skill_ids = sq.params.get("resolved_skill_ids")
            if not skill_ids and skill:
                skill_ids = [skill]
            if not skill_ids:
                attempted = sq.params.get("attempted_skill")
                if attempted:
                    return _clarif(sq_index, intent,
                        f"I couldn't find a skill matching '{attempted}' in the curriculum. "
                        f"Could you provide a more specific skill name?")
                return _clarif(sq_index, intent, "Which skill are you looking for courses about?")
            result = self._kg.call("search_courses_by_skill", {"skill_ids": skill_ids})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                attempted = sq.params.get("attempted_skill") or (skill_ids[0] if skill_ids else skill)
                return _info(sq_index, intent, {**result, "attempted_skill": attempted})
            # For topic_fallback: pass the flag through so Composer can explain the reroute
            if sq.params.get("topic_fallback"):
                result = {**result, "topic_fallback": True,
                          "original_mention": sq.params.get("attempted_candidate", "")}
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
                if ctx and _is_unsupported_track(ctx):
                    return _unsupported_track_result(sq_index, intent, ctx)
                return _clarif(sq_index, intent, "Which track would you like to see roles for?")
            if not e.track_id and ctx and _is_unsupported_track(ctx):
                return _unsupported_track_result(sq_index, intent, ctx)
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
                if ctx and _is_unsupported_track(ctx):
                    return _unsupported_track_result(sq_index, intent, ctx)
                target_id = ctx.track_id if ctx else None
                target_type = "track"
            if not target_id:
                return _clarif(sq_index, intent, "Which role or track are you focusing on?")
            # Only pass completed courses for personalised (student-referential) queries
            focus_completed = completed if sq.student_referential_fallback else []
            result = self._kg.call("get_focus_courses_for_target", {
                "target_id": target_id,
                "target_type": target_type,
                "completed_courses": focus_completed,
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
                if ctx and _is_unsupported_track(ctx):
                    return _unsupported_track_result(sq_index, intent, ctx)
                return _clarif(sq_index, intent, "Which track would you like an overview of?")
            if not e.track_id and ctx and _is_unsupported_track(ctx):
                return _unsupported_track_result(sq_index, intent, ctx)
            result = self._kg.call("get_track_overview", {"track_id": track})
            if _is_kg_adapter_error(result):
                return _err(sq_index, intent, "engine_error", "kg_adapter", "KG unavailable.")
            if "error" in result:
                return _info(sq_index, intent, result)
            return _ok(sq_index, intent, result)

        if intent == "compare_tracks":
            track1 = e.track_id or (ctx.track_id if ctx else None)
            track2 = (sq.secondary_entities.track_id if sq.secondary_entities else None)
            if not track1:
                if ctx and _is_unsupported_track(ctx):
                    return _unsupported_track_result(sq_index, intent, ctx)
            if not track1 or not track2:
                return _clarif(sq_index, intent, "Which two tracks would you like to compare?")
            if not e.track_id and ctx and _is_unsupported_track(ctx):
                return _unsupported_track_result(sq_index, intent, ctx)
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
        result_error = result.get("error")
        if result_error:
            if result_error == "empty_query":
                return _clarif(sq_index, intent, "Which policy are you asking about?")
            return _err(sq_index, intent, "engine_error", "rag_adapter",
                        f"RAG engine error: {result_error}")

        answer = result.get("answer", "")
        facts = result.get("extracted_facts", [])
        citations = result.get("citations", [])

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
        caches: _TurnCaches,
        had_clear: bool = False,
        execution_overrides: Optional[SessionOverrides] = None,
    ) -> PerSQResult:
        intent = "get_student_record"

        record_focus = sq.params.get("record_focus") or "full_record"
        response_style = sq.params.get("response_style") or "normal"

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

        # Focus-aware enrichment: skip KG calls for scalar-only or no-enrich focuses
        failed_history_codes: list = []
        failed_history_details: list = []

        if record_focus in _D6_SCALAR_FOCUSES or record_focus in _D6_NO_ENRICH_FOCUSES:
            completed_details: list = []
            in_progress_details: list = []
            failed_details: list = []
        elif record_focus in _D6_COMPLETED_ONLY_FOCUSES:
            completed_details = self._enrich_course_details(ctx.completed_courses or [], caches)
            in_progress_details = []
            failed_details = []
        elif record_focus in _D6_IN_PROGRESS_ONLY_FOCUSES:
            completed_details = []
            in_progress_details = self._enrich_course_details(ctx.in_progress_courses or [], caches)
            failed_details = []
        elif record_focus in _D6_FAILED_ONLY_FOCUSES:
            completed_details = []
            in_progress_details = []
            failed_details = self._enrich_course_details(ctx.failed_courses or [], caches)
        elif record_focus in _D6_COURSE_STATUS_CHECK_FOCUSES:
            target_code = sq.entities.course_code
            if target_code:
                # Enrich only the target course, not all courses
                completed_details = self._enrich_course_details(
                    [c for c in (ctx.completed_courses or []) if c == target_code], caches
                )
                in_progress_details = self._enrich_course_details(
                    [c for c in (ctx.in_progress_courses or []) if c == target_code], caches
                )
                failed_details = self._enrich_course_details(
                    [c for c in (ctx.failed_courses or []) if c == target_code], caches
                )
            else:
                # No specific course — enrich current courses only
                completed_details = []
                in_progress_details = self._enrich_course_details(ctx.in_progress_courses or [], caches)
                failed_details = []
        elif record_focus in _D6_FAILED_HISTORY_FOCUSES:
            # Extract all courses ever failed from course_history
            failed_history_codes = list(dict.fromkeys(
                r.course_code for r in (ctx.course_history or [])
                if r.status in ("failed", "repeated")
            ))
            failed_history_details = self._enrich_course_details(failed_history_codes, caches)
            completed_details = []
            in_progress_details = []
            failed_details = []
        else:
            # full_record, progress_summary, unknown focus → enrich all
            completed_details = self._enrich_course_details(ctx.completed_courses or [], caches)
            in_progress_details = self._enrich_course_details(ctx.in_progress_courses or [], caches)
            failed_details = self._enrich_course_details(ctx.failed_courses or [], caches)

        level_display: Optional[str] = None
        if ctx.level is not None:
            level_display = _LEVEL_DISPLAY.get(ctx.level, str(ctx.level))

        # Scenario credits: when assumptions added passed courses with known credits
        scenario_completed_credits: Optional[int] = None
        eff_overrides = execution_overrides if execution_overrides is not None else sq.session_overrides
        if assumptions_active and eff_overrides and eff_overrides.assumed_passed_courses and completed_details:
            known = [d["credits"] for d in completed_details if isinstance(d.get("credits"), int)]
            if len(known) == len(completed_details):
                scenario_completed_credits = sum(known)

        # Assumed course lists for Composer labelling
        assumed_failed = list(eff_overrides.assumed_failed_courses) if eff_overrides else []
        assumed_passed = list(eff_overrides.assumed_passed_courses) if eff_overrides else []

        snapshot: dict = {
            "record_focus": record_focus,
            "response_style": response_style,
            "track_id": ctx.track_id,
            "program": ctx.program,
            "level": ctx.level,
            "level_display": level_display,
            "cgpa": ctx.cgpa,
            "last_semester_gpa": ctx.last_semester_gpa,
            "last_semester_chs": ctx.last_semester_chs,
            "last_semester_cps": ctx.last_semester_cps,
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
            "completed_course_details": completed_details,
            "in_progress_course_details": in_progress_details,
            "failed_course_details": failed_details,
            "failed_history_codes": failed_history_codes,
            "failed_history_details": failed_history_details,
            "scenario_completed_credits": scenario_completed_credits,
            "assumed_failed_courses": assumed_failed,
            "assumed_passed_courses": assumed_passed,
        }

        if had_clear:
            snapshot["assumptions_cleared"] = True
            snapshot["message"] = (
                "I cleared your what-if assumptions. "
                "You are back to your official academic record."
            )

        return _ok(sq_index, intent, snapshot,
                   override_state_active=True if assumptions_active else None)

    # ── KG helper ─────────────────────────────────────────────────────────────

    def _enrich_course_details(self, codes: list, caches: _TurnCaches) -> list:
        """Return enriched course detail dicts for a list of course codes.

        Uses course_profile_cache. KG failure, non-dict result, or unknown course
        produces a fallback dict with course_name=None and credits=None.
        Never raises.
        """
        details = []
        for code in codes:
            if code not in caches.course_profile_cache:
                result = self._kg.call("get_course_profile", {"course_code": code})
                caches.course_profile_cache[code] = result
            profile = caches.course_profile_cache.get(code, {})
            if (
                not isinstance(profile, dict)
                or _is_kg_adapter_error(profile)
                or "error" in profile
            ):
                details.append({"course_code": code, "course_name": None, "credits": None})
            else:
                details.append({
                    "course_code": code,
                    "course_name": profile.get("name"),
                    "credits": profile.get("credits"),
                })
        return details

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
