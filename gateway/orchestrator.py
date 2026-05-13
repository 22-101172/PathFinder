"""
orchestrator.py  [T08 — Integration sprint implementation]
──────────────────────────────────────────────────────────
Deterministic workflow dispatcher between the gateway and the KG/RAG adapters.

Responsibility (Blueprint §5.6):
  Given a StructuredQuery and the effective student context, choose ONE of
  six workflows, invoke the appropriate adapter(s), and aggregate the results
  into a ResultPackage for the ResponseComposer.

Hard boundaries:
  ✗ Never interprets raw user text (QU has already done so — original_query
    is only passed through to RAG and the composer).
  ✗ Never calls Neo4j directly or imports engines.kg.queries.
  ✗ Never uses an LLM to make routing decisions.
  ✗ Never generates the final natural-language answer.

Workflow map:
  W1  KG-only           → _kg_only_workflow
  W2  RAG-only          → _rag_only_workflow
  W3  mixed             → _mixed_workflow
  W4  student-aware KG  → _student_aware_workflow
  W5  follow-up         → resolved upstream by QU; routes to one of W1–W4
  W6  clarification     → _clarification_workflow
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from gateway.adapters.kg_adapter import KGAdapter
from gateway.adapters.rag_adapter import RAGAdapter
from gateway.models.schemas import (
    Citation,
    RAGResult,
    ResultPackage,
    StructuredQuery,
    StudentContext,
)

logger = logging.getLogger(__name__)


# ── Intent → KG operation mapping ────────────────────────────────────────────
#
# Single source of truth: maps the high-level intents emitted by QU onto the
# actual KGAdapter operation names. See gateway/adapters/kg_adapter.py for
# the full list of operations.
INTENT_TO_KG_OP: dict[str, str] = {
    "get_course_profile": "get_course_profile",
    "get_prerequisites": "get_prerequisites",
    "get_skills_taught": "get_skills_taught",
    "search_courses_by_skill": "search_courses_by_skill",
    "get_role_profile": "get_role_profile",
    "get_roles_by_track": "get_roles_by_track",
    "skill_gap_analysis": "compute_skill_gap",
    "compute_alignment_score": "compute_alignment_score",
    "recommend_courses_to_close_gap": "recommend_courses_to_close_gap",
    "estimate_alignment_improvement": "estimate_alignment_improvement",
    "role_recommendation": "find_best_matching_roles",
    "track_guidance": "get_track_overview",
    "recommend_track_for_role": "recommend_track_for_role",
    "recommend_track_for_skill": "recommend_track_for_skill",
}


# ── KG error detection ───────────────────────────────────────────────────────

def _kg_result_is_error(result: dict) -> bool:
    """The KG layer uses two error shapes:

      - queries.py: `{"error": "course_not_found", ...}`
      - kg_adapter wrappers: `{"status": "error", "message": "..."}`

    We treat either as an error so the composer can present a friendly
    message without needing to know which layer produced it.
    """
    if not isinstance(result, dict):
        return True
    if "error" in result and result["error"]:
        return True
    if result.get("status") == "error":
        return True
    return False


def _kg_error_detail(result: dict) -> str:
    """Extract a short, log-safe error string from any KG error shape."""
    if not isinstance(result, dict):
        return "kg_unexpected_response_shape"
    if result.get("error"):
        return str(result["error"])
    if result.get("message"):
        return str(result["message"])
    return "kg_error"


def _rag_dict_is_error(raw: dict) -> bool:
    """The current RAGAdapter returns errors via the `answer` text prefix."""
    if not isinstance(raw, dict):
        return True
    answer = raw.get("answer", "") or ""
    if answer.startswith("An error occurred while searching the handbook"):
        return True
    if answer == "RAG Engine is currently unavailable.":
        return True
    if answer == "Backend configuration error: LLM endpoint is not set.":
        return True
    return False


def _to_rag_result(raw: dict) -> RAGResult:
    """Normalize the raw RAG response dict into the `RAGResult` schema."""
    answer = (raw or {}).get("answer") or ""
    raw_citations = (raw or {}).get("citations") or []
    citations: list[Citation] = []
    for c in raw_citations:
        if not isinstance(c, dict):
            continue
        source = (c.get("source") or "").strip()
        if not source:
            continue
        page = c.get("page")
        if page is not None and not isinstance(page, int):
            try:
                page = int(page)
            except (TypeError, ValueError):
                page = None
        citations.append(Citation(source=source, page=page))
    return RAGResult(answer=answer, citations=citations)


# ── Orchestrator ─────────────────────────────────────────────────────────────

class Orchestrator:

    def __init__(self, kg_wrapper: KGAdapter, rag_wrapper: RAGAdapter) -> None:
        self._kg = kg_wrapper
        self._rag = rag_wrapper

    # ── Main dispatch ─────────────────────────────────────────────────────────

    def run(
        self,
        structured_query: StructuredQuery,
        effective_context: Optional[StudentContext],
        original_query: str,
    ) -> ResultPackage:
        """Pick a workflow and execute it. Never raises."""
        try:
            if structured_query.needs_clarification:
                workflow = "clarification"
                package = self._clarification_workflow(structured_query, original_query)
            elif structured_query.engine_pattern == "mixed":
                workflow = "mixed"
                package = self._mixed_workflow(
                    structured_query, effective_context, original_query
                )
            elif structured_query.query_type == "student_aware":
                if effective_context is None:
                    workflow = "error_no_context"
                    package = self._error_package(
                        original_query, "student_context_required"
                    )
                else:
                    workflow = "student_aware"
                    package = self._student_aware_workflow(
                        structured_query, effective_context, original_query
                    )
            elif structured_query.engine_pattern == "kg":
                workflow = "kg_only"
                package = self._kg_only_workflow(structured_query, original_query)
            elif structured_query.engine_pattern == "rag":
                workflow = "rag_only"
                package = self._rag_only_workflow(structured_query, original_query)
            else:
                workflow = "unknown"
                package = self._error_package(
                    original_query,
                    f"unknown engine_pattern: {structured_query.engine_pattern}",
                )
        except Exception:
            # Defensive: an orchestrator bug must still produce a valid response.
            logger.exception("orchestrator.unexpected_error")
            return self._error_package(original_query, "internal_orchestrator_error")

        logger.info(
            "orchestrator.workflow workflow=%s intent=%s status=%s",
            workflow, structured_query.intent, package.status,
        )
        return package

    # ── Workflow implementations ──────────────────────────────────────────────

    def _kg_only_workflow(
        self,
        query: StructuredQuery,
        original_query: str,
    ) -> ResultPackage:
        op = self._map_intent_to_kg_operation(query.intent)
        if op is None:
            return self._clarification_package(
                query, original_query,
                "I couldn't map your question to a known curriculum operation.",
            )

        params, missing = self._build_kg_params(op, query, context=None)
        if missing:
            return self._clarification_package(
                query, original_query,
                f"I need more information to answer that — missing: {missing}.",
            )

        kg_result = self._safe_kg_call(op, params)
        if _kg_result_is_error(kg_result):
            detail = _kg_error_detail(kg_result)
            logger.warning("kg.call op=%s status=error message=%s", op, detail)
            return ResultPackage(
                original_query=original_query,
                engine_pattern="kg",
                kg_result=kg_result,
                status="error",
                error_detail=detail,
            )

        logger.debug(
            "kg.call op=%s status=ok keys=%s",
            op, list(kg_result.keys())[:5],
        )
        return ResultPackage(
            original_query=original_query,
            engine_pattern="kg",
            kg_result=kg_result,
            status="ok",
        )

    def _rag_only_workflow(
        self,
        query: StructuredQuery,
        original_query: str,
    ) -> ResultPackage:
        """Hand the *original* user text to the RAG adapter.

        Privacy note: we deliberately do NOT pass `student_context` — handbook
        answers should not be conditioned on the student's transcript."""
        raw = self._safe_rag_call(original_query)
        rag_result = _to_rag_result(raw)
        if _rag_dict_is_error(raw):
            detail = raw.get("answer") if isinstance(raw, dict) else "rag_error"
            logger.warning("rag.call status=error message=%s", detail)
            return ResultPackage(
                original_query=original_query,
                engine_pattern="rag",
                rag_result=rag_result,
                status="error",
                error_detail=detail or "rag_error",
            )

        logger.debug("rag.call status=ok citations=%d", len(rag_result.citations))
        return ResultPackage(
            original_query=original_query,
            engine_pattern="rag",
            rag_result=rag_result,
            status="ok",
        )

    def _mixed_workflow(
        self,
        query: StructuredQuery,
        context: Optional[StudentContext],
        original_query: str,
    ) -> ResultPackage:
        """Always call RAG; call KG only when we have a course code to look up.

        The current MVP supports one mixed shape: a curriculum question about
        a specific course that also touches a handbook policy. Anything
        broader should be added behind a new explicit intent rather than
        smuggled through here."""
        # KG side — only when we have a course_code in entities.
        kg_result: Optional[dict] = None
        kg_error_detail: Optional[str] = None
        if query.entities.course_code:
            kg_op, kg_params = self._pick_mixed_kg_op(query, original_query)
            if kg_op is not None:
                kg_result = self._safe_kg_call(kg_op, kg_params)
                if _kg_result_is_error(kg_result):
                    kg_error_detail = _kg_error_detail(kg_result)
                    logger.warning(
                        "kg.call op=%s status=error message=%s (mixed-side)",
                        kg_op, kg_error_detail,
                    )

        # RAG side — always
        raw = self._safe_rag_call(original_query)
        rag_result = _to_rag_result(raw)
        rag_error = _rag_dict_is_error(raw)

        # Status: ok if at least one side succeeded.
        kg_ok = kg_result is not None and not _kg_result_is_error(kg_result)
        rag_ok = not rag_error
        if not kg_ok and not rag_ok:
            return ResultPackage(
                original_query=original_query,
                engine_pattern="mixed",
                kg_result=kg_result,
                rag_result=rag_result,
                status="error",
                error_detail=kg_error_detail or (raw.get("answer") if isinstance(raw, dict) else "mixed_both_failed"),
            )

        return ResultPackage(
            original_query=original_query,
            engine_pattern="mixed",
            kg_result=kg_result,
            rag_result=rag_result,
            status="ok",
            error_detail=kg_error_detail if not kg_ok else (
                raw.get("answer") if (isinstance(raw, dict) and not rag_ok) else None
            ),
        )

    def _student_aware_workflow(
        self,
        query: StructuredQuery,
        context: StudentContext,
        original_query: str,
    ) -> ResultPackage:
        op = self._map_intent_to_kg_operation(query.intent)
        if op is None:
            return self._clarification_package(
                query, original_query,
                "I couldn't map your question to a known curriculum operation.",
            )

        params, missing = self._build_kg_params(op, query, context=context)
        if missing:
            return self._clarification_package(
                query, original_query,
                f"I need more information to answer that — missing: {missing}.",
            )

        kg_result = self._safe_kg_call(op, params)
        if _kg_result_is_error(kg_result):
            detail = _kg_error_detail(kg_result)
            logger.warning("kg.call op=%s status=error message=%s", op, detail)
            return ResultPackage(
                original_query=original_query,
                engine_pattern="kg",
                kg_result=kg_result,
                student_context=context,
                status="error",
                error_detail=detail,
            )

        logger.debug(
            "kg.call op=%s status=ok keys=%s",
            op, list(kg_result.keys())[:5],
        )
        return ResultPackage(
            original_query=original_query,
            engine_pattern="kg",
            kg_result=kg_result,
            student_context=context,
            status="ok",
        )

    def _clarification_workflow(
        self,
        query: StructuredQuery,
        original_query: str,
    ) -> ResultPackage:
        return ResultPackage(
            original_query=original_query,
            engine_pattern=query.engine_pattern or "kg",
            status="clarification_needed",
            error_detail=query.clarification_prompt or "Could you clarify your question?",
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _map_intent_to_kg_operation(self, intent: str) -> Optional[str]:
        """Map a QU intent to a `KGAdapter.call` operation name."""
        return INTENT_TO_KG_OP.get(intent)

    def _build_kg_params(
        self,
        op: str,
        query: StructuredQuery,
        context: Optional[StudentContext],
    ) -> tuple[dict[str, Any], Optional[str]]:
        """Return (params, missing) where `missing` is a human-readable string
        describing which required field is absent, or None if params are
        complete. Keeping this as a small explicit switch keeps the contract
        easy to audit against the KG layer."""
        e = query.entities
        completed = context.completed_courses if context is not None else []
        planned = context.planned_courses if context is not None else []

        if op == "get_course_profile":
            if not e.course_code:
                return {}, "course_code"
            return {"course_code": e.course_code}, None

        if op == "get_prerequisites":
            if not e.course_code:
                return {}, "course_code"
            return {"course_code": e.course_code, "depth": "direct"}, None

        if op == "get_skills_taught":
            if not e.course_code:
                return {}, "course_code"
            return {"course_code": e.course_code}, None

        if op == "search_courses_by_skill":
            # Prefer the canonical skill display name when we resolved an id;
            # otherwise fall back to the raw query text (queries.py compares
            # case-insensitive against Skill.name).
            if e.skill_id:
                # We don't have direct access to KG data here; the orchestrator
                # only holds adapters. Pass the id — KG's resolver matches
                # against Skill.name, so we instead pass `e.skill_id` only as
                # a last resort. Better behaviour is for QU to also surface
                # a skill_name in `entities`; for now, pass the id verbatim
                # which the KG layer will treat as an unrecognized skill but
                # still respond with a structured error.
                return {"skills": [e.skill_id]}, None
            return {}, "skill_id"

        if op == "get_role_profile":
            if not e.role_id:
                return {}, "role_id"
            return {"role_id": e.role_id}, None

        if op == "get_roles_by_track":
            if not e.track_id:
                return {}, "track_id"
            return {"track_id": e.track_id}, None

        if op == "compute_skill_gap":
            if not e.role_id:
                return {}, "role_id"
            if not completed:
                return {}, "completed_courses"
            return {"role_id": e.role_id, "completed_courses": completed}, None

        if op == "compute_alignment_score":
            if not e.role_id:
                return {}, "role_id"
            if not completed:
                return {}, "completed_courses"
            return {"role_id": e.role_id, "completed_courses": completed}, None

        if op == "recommend_courses_to_close_gap":
            if not e.role_id:
                return {}, "role_id"
            if not completed:
                return {}, "completed_courses"
            return {"role_id": e.role_id, "completed_courses": completed}, None

        if op == "estimate_alignment_improvement":
            if not e.role_id:
                return {}, "role_id"
            if not completed:
                return {}, "completed_courses"
            if not planned:
                return {}, "planned_courses"
            return {
                "role_id": e.role_id,
                "completed_courses": completed,
                "planned_courses": planned,
            }, None

        if op == "find_best_matching_roles":
            if not completed:
                return {}, "completed_courses"
            return {"completed_courses": completed}, None

        if op == "get_track_overview":
            if not e.track_id:
                return {}, "track_id"
            return {"track_id": e.track_id}, None

        if op == "recommend_track_for_role":
            if not e.role_id:
                return {}, "role_id"
            return {"role_id": e.role_id}, None

        if op == "recommend_track_for_skill":
            if not e.skill_id:
                return {}, "skill_id"
            return {"skill_id": e.skill_id}, None

        return {}, f"unsupported_op:{op}"

    def _pick_mixed_kg_op(
        self,
        query: StructuredQuery,
        original_query: str,
    ) -> tuple[Optional[str], dict[str, Any]]:
        """For a mixed query, pick the KG operation that fits best.

        Heuristic: prerequisites if the user mentioned them explicitly,
        otherwise the course profile."""
        text = (original_query or "").lower()
        if any(k in text for k in ("prereq", "prerequisite", "before i take")):
            if query.entities.course_code:
                return "get_prerequisites", {
                    "course_code": query.entities.course_code,
                    "depth": "direct",
                }
        if query.entities.course_code:
            return "get_course_profile", {"course_code": query.entities.course_code}
        return None, {}

    def _safe_kg_call(self, op: str, params: dict[str, Any]) -> dict:
        """Call the KG adapter, normalizing any unexpected error shape.

        The adapter is documented as never raising — but we keep a defensive
        try/except so a buggy adapter cannot break the orchestrator."""
        try:
            return self._kg.call(op, params) or {}
        except Exception as exc:
            logger.exception("kg.call op=%s unexpected_exception", op)
            return {"status": "error", "message": f"unexpected_kg_exception: {exc}"}

    def _safe_rag_call(self, sub_query: str) -> dict:
        """Call the RAG adapter; convert unexpected exceptions into the same
        dict shape the adapter normally returns."""
        try:
            return self._rag.execute(sub_query) or {}
        except Exception as exc:
            logger.exception("rag.call unexpected_exception")
            return {
                "answer": f"An error occurred while searching the handbook: {exc}",
                "citations": [],
            }

    def _clarification_package(
        self,
        query: StructuredQuery,
        original_query: str,
        prompt: str,
    ) -> ResultPackage:
        """Build a clarification-status ResultPackage from inside Orchestrator
        (distinct from QU-detected clarifications, which arrive via the
        StructuredQuery itself)."""
        return ResultPackage(
            original_query=original_query,
            engine_pattern=query.engine_pattern or "kg",
            status="clarification_needed",
            error_detail=prompt,
        )

    def _error_package(self, original_query: str, detail: str) -> ResultPackage:
        return ResultPackage(
            original_query=original_query,
            engine_pattern="unknown",
            status="error",
            error_detail=detail,
        )
