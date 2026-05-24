from __future__ import annotations
import logging

from gateway.models.schemas import (
    Citation, ComposerContext, LastReferenced,
    RAGResult, ResultPackage, SessionState, StructuredQuery,
)

logger = logging.getLogger(__name__)


class Orchestrator:

    def __init__(self, kg_adapter, rag_adapter, ale_adapter):
        self._kg = kg_adapter
        self._rag = rag_adapter
        self._ale = ale_adapter
        logger.info("Orchestrator: initialized")

    def run(self, sq: StructuredQuery, session: SessionState) -> ResultPackage:
        logger.info("Orchestrator: intent=%s engine=%s student=%s",
                    sq.intent, sq.engine_pattern, session.student_id)

        ctx = session.student_context
        effective_completed = list(set(ctx.completed_courses + ctx.planned_courses))

        try:
            if sq.needs_clarification:
                return ResultPackage(
                    original_query=sq.intent, intent="clarification",
                    engine_pattern="clarification", status="clarification_needed",
                    error_detail=sq.clarification_prompt or "Could you clarify your question?",
                )

            if sq.engine_pattern == "kg":
                return self._kg_route(sq, ctx, effective_completed)
            elif sq.engine_pattern == "rag":
                return self._rag_route(sq)
            elif sq.engine_pattern == "mixed":
                return self._mixed_route(sq, ctx, effective_completed)
            elif sq.engine_pattern == "ale":
                return self._ale_route(sq, ctx, effective_completed)
            else:
                logger.warning("Orchestrator: unknown pattern %s, fallback rag", sq.engine_pattern)
                return self._rag_route(sq)

        except Exception as exc:
            logger.exception("Orchestrator: unhandled error intent=%s", sq.intent)
            return ResultPackage(
                original_query=sq.intent, intent=sq.intent,
                engine_pattern=sq.engine_pattern, status="error", error_detail=str(exc),
            )

    def _kg_route(self, sq, ctx, effective_completed) -> ResultPackage:
        intent = sq.intent
        e = sq.entities
        logger.info("Orchestrator[KG]: %s", intent)

        dispatch = {
            "get_course_profile":             lambda: self._kg.call("get_course_profile", {"course_code": e.course_code or ""}),
            "get_prerequisites":              lambda: self._kg.call("get_prerequisites", {"course_code": e.course_code or "", "depth": "direct"}),
            "get_skills_taught":              lambda: self._kg.call("get_skills_taught", {"course_code": e.course_code or ""}),
            "search_courses_by_skill":        lambda: self._kg.call("search_courses_by_skill", {"skills": [e.skill_id] if e.skill_id else []}),
            "get_role_profile":               lambda: self._kg.call("get_role_profile", {"role_id": e.role_id or ""}),
            "get_roles_by_track":             lambda: self._kg.call("get_roles_by_track", {"track_id": e.track_id or ctx.track_id}),
            "compute_skill_gap":              lambda: self._kg.call("compute_skill_gap", {"role_id": e.role_id or "", "completed_courses": effective_completed}),
            "compute_alignment_score":        lambda: self._kg.call("compute_alignment_score", {"role_id": e.role_id or "", "completed_courses": effective_completed}),
            "recommend_courses_to_close_gap": lambda: self._kg.call("recommend_courses_to_close_gap", {"role_id": e.role_id or "", "completed_courses": effective_completed}),
            "estimate_alignment_improvement": lambda: self._kg.call("estimate_alignment_improvement", {"role_id": e.role_id or "", "completed_courses": effective_completed, "planned_courses": ctx.planned_courses}),
            "find_best_matching_roles":       lambda: self._kg.call("find_best_matching_roles", {"completed_courses": effective_completed}),
            "get_track_overview":             lambda: self._kg.call("get_track_overview", {"track_id": e.track_id or ctx.track_id}),
            "compare_tracks":                 lambda: self._kg.call("compare_tracks", {"track_id_1": e.track_id or ctx.track_id, "track_id_2": (sq.secondary_entities.track_id if sq.secondary_entities else "") or ""}),
            "recommend_track_for_role":       lambda: self._kg.call("recommend_track_for_role", {"role_id": e.role_id or ""}),
            "recommend_track_for_skill":      lambda: self._kg.call("recommend_track_for_skill", {"skill_id": e.skill_id or ""}),
        }

        fn = dispatch.get(intent)
        result = fn() if fn else {"error": "unknown_kg_intent", "detail": intent}

        logger.info("Orchestrator[KG]: result keys=%s", list(result.keys()))
        has_error = "error" in result

        return ResultPackage(
            original_query=intent, intent=intent, engine_pattern="kg",
            kg_result=result,
            composer_context=self._composer_ctx(ctx),
            status="error" if has_error else "ok",
            error_detail=result.get("error") if has_error else None,
        )

    def _rag_route(self, sq) -> ResultPackage:
        query_text = sq.original_text or sq.intent
        logger.info("Orchestrator[RAG]: handbook query: %s", query_text[:80])
        raw = self._rag.execute(query_text)
        return ResultPackage(
            original_query=query_text, intent=sq.intent, engine_pattern="rag",
            rag_result=RAGResult(
                answer=raw.get("answer"),
                citations=[Citation(source=c.get("source", ""), page=c.get("page")) for c in raw.get("citations", [])],
            ),
            status="ok",
        )

    def _mixed_route(self, sq, ctx, effective_completed) -> ResultPackage:
        logger.info("Orchestrator[MIXED]: kg + rag")
        kg_pkg = self._kg_route(sq, ctx, effective_completed)
        raw = self._rag.execute(sq.original_text or sq.intent)
        return ResultPackage(
            original_query=sq.intent, intent=sq.intent, engine_pattern="mixed",
            kg_result=kg_pkg.kg_result,
            rag_result=RAGResult(
                answer=raw.get("answer"),
                citations=[Citation(source=c.get("source", ""), page=c.get("page")) for c in raw.get("citations", [])],
            ),
            composer_context=self._composer_ctx(ctx),
            status="ok",
        )

    def _ale_route(self, sq, ctx, effective_completed) -> ResultPackage:
        intent = sq.intent
        e = sq.entities
        logger.info("Orchestrator[ALE]: %s", intent)

        ctx_dict = ctx.model_dump()
        ctx_dict["completed_courses"] = effective_completed

        if intent == "check_eligibility":
            prereq_result = {}
            if e.course_code:
                prereq_result = self._kg.call("get_prerequisites", {"course_code": e.course_code, "depth": "direct"})
                logger.info("Orchestrator[ALE]: prereqs from KG: %s", prereq_result)
            prereqs = {
                "direct": [p["course_code"] for p in prereq_result.get("direct_prerequisites", [])],
                "non_course": prereq_result.get("non_course_prerequisites", []),
            }
            ale_result = self._ale.call("check_eligibility", {
                "student_context": ctx_dict,
                "course_code": e.course_code or "",
                "target_course": {"course_code": e.course_code or "", "credits": 3, "prerequisites": prereqs},
            })

        elif intent == "run_graduation_audit":
            ale_result = self._ale.call("run_graduation_audit", {"student_context": ctx_dict})

        elif intent == "generate_semester_plan":
            ale_result = self._ale.call("generate_semester_plan", {
                "student_context": ctx_dict,
                "available_offerings": [],
                "constraints": {"max_credits": 0, "is_final_semester": False},
            })

        elif intent == "simulate_gpa":
            ale_result = self._ale.call("simulate_gpa", {
                "student_context": ctx_dict,
                "simulation_scenario": {},
            })

        else:
            ale_result = {"status": "error", "message": f"Unknown ALE intent: {intent}"}

        logger.info("Orchestrator[ALE]: result keys=%s", list(ale_result.keys()) if ale_result else [])
        is_error = ale_result.get("status") == "error"

        return ResultPackage(
            original_query=intent, intent=intent, engine_pattern="ale",
            ale_result=ale_result,
            composer_context=self._composer_ctx(ctx),
            status="error" if is_error else "ok",
            error_detail=ale_result.get("message") if is_error else None,
        )

    def _composer_ctx(self, ctx) -> ComposerContext:
        return ComposerContext(
            track_id=ctx.track_id, level=ctx.level, cgpa=ctx.cgpa,
            academic_standing=ctx.academic_standing, study_status=ctx.study_status,
            total_credit_hours_earned=ctx.total_credit_hours_earned,
            current_semester=ctx.current_semester,
            consecutive_warnings=ctx.consecutive_warnings,
        )

    def extract_last_referenced(self, sq: StructuredQuery) -> LastReferenced:
        return LastReferenced(
            course_code=sq.entities.course_code,
            role_id=sq.entities.role_id,
            track_id=sq.entities.track_id,
        )
