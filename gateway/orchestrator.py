"""
orchestrator.py  [T08 — Person B]
──────────────────────────────────
Responsibility:
  Select workflow, invoke the correct engine wrapper(s), aggregate results.

Rules (from blueprint Section 5.6):
  ✓ Map intent to predefined workflow (rule-governed, NOT LLM-driven)
  ✓ Invoke KG wrapper, RAG wrapper, or both
  ✓ Aggregate results into ResultPackage
  ✓ Handle missing entities and engine errors gracefully
  ✗ Must NOT interpret raw user text
  ✗ Must NOT generate the final user response
  ✗ Must NOT access databases directly
  ✗ Must NOT use an LLM to decide routing

Supported workflows (map from StructuredQuery.engine_pattern + query_type):
  W1  KG-only,          non_student_aware  → _kg_only_workflow()
  W2  RAG-only,         non_student_aware  → _rag_only_workflow()
  W3  mixed,            non_student_aware  → _mixed_workflow()
  W4  KG (or mixed),    student_aware      → _student_aware_workflow()
  W5  Resolved via session context         → same as appropriate workflow above
  W6  needs_clarification=True             → _clarification_workflow()

Done when:
  - All 6 workflows route correctly and return a valid ResultPackage
  - Missing entity → returns ResultPackage with status="clarification_needed"
  - Engine error → returns ResultPackage with status="error"
"""

from gateway.models.schemas import (
    StructuredQuery, StudentContext, ResultPackage, RAGResult
)
from gateway.adapters.kg_adapter import KGAdapter
from gateway.adapters.rag_adapter import RAGAdapter


class Orchestrator:

    def __init__(self, kg_wrapper: KGAdapter, rag_wrapper: RAGAdapter):
        self._kg = kg_wrapper
        self._rag = rag_wrapper

    # ── Main dispatch ─────────────────────────────────────────────────────────

    def run(
        self,
        structured_query: StructuredQuery,
        effective_context: StudentContext | None,
        original_query: str,
    ) -> ResultPackage:
        """
        Select and run the appropriate workflow.
        Returns a ResultPackage for the Response Composer.
        """
        # TODO: implement
        # Dispatch logic:
        #   if needs_clarification → _clarification_workflow()
        #   elif query_type == "student_aware" → _student_aware_workflow()
        #   elif engine_pattern == "kg" → _kg_only_workflow()
        #   elif engine_pattern == "rag" → _rag_only_workflow()
        #   elif engine_pattern == "mixed" → _mixed_workflow()
        #   else → error package
        raise NotImplementedError

    # ── Workflow implementations ──────────────────────────────────────────────

    def _kg_only_workflow(
        self,
        query: StructuredQuery,
        original_query: str,
    ) -> ResultPackage:
        """W1: KG-only, non-student-aware query."""
        # TODO: implement
        raise NotImplementedError

    def _rag_only_workflow(
        self,
        query: StructuredQuery,
        original_query: str,
    ) -> ResultPackage:
        """W2: RAG-only, handbook/policy query."""
        # TODO: implement
        raise NotImplementedError

    def _mixed_workflow(
        self,
        query: StructuredQuery,
        original_query: str,
    ) -> ResultPackage:
        """W3: Both KG and RAG called, results merged."""
        # TODO: implement
        # Tip: call both wrappers, combine into one ResultPackage
        raise NotImplementedError

    def _student_aware_workflow(
        self,
        query: StructuredQuery,
        context: StudentContext,
        original_query: str,
    ) -> ResultPackage:
        """W4: Student context is injected into the KG call."""
        # TODO: implement
        # Pass context.completed_courses (+ any override additions) to kg_wrapper
        raise NotImplementedError

    def _clarification_workflow(
        self,
        query: StructuredQuery,
        original_query: str,
    ) -> ResultPackage:
        """W6: Query was ambiguous. Return the clarification prompt without calling engines."""
        # TODO: implement
        # Return ResultPackage with status="clarification_needed"
        # Include query.clarification_prompt in error_detail
        raise NotImplementedError

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _map_intent_to_kg_operation(self, intent: str) -> str | None:
        """
        Map intent string to KGEngine operation name.
        Returns None if no mapping exists.

        KGEngine operations (all 15 — from KG component docs):
          get_course_profile, get_prerequisites, get_skills_taught,
          search_courses_by_skill, get_role_profile, get_roles_by_track,
          compute_skill_gap, compute_alignment_score,
          recommend_courses_to_close_gap, estimate_alignment_improvement,
          find_best_matching_roles, get_track_overview, compare_tracks,
          recommend_track_for_role, recommend_track_for_skill
        """
        # TODO: implement
        # Example: "get_prerequisites" → "get_prerequisites"
        #          "skill_gap_analysis" → "compute_skill_gap"
        #          "role_recommendation" → "find_best_matching_roles"
        raise NotImplementedError

    def _error_package(self, original_query: str, detail: str) -> ResultPackage:
        return ResultPackage(
            original_query=original_query,
            engine_pattern="unknown",
            status="error",
            error_detail=detail,
        )
