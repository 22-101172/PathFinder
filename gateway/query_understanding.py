"""
query_understanding.py  [T07 — Person A]
─────────────────────────────────────────
Responsibility:
  Transform raw user text + student context into a StructuredQuery.
  This is the ONLY component that interprets natural language for routing.

Rules (from blueprint Section 5.5):
  ✓ Classify query intent
  ✓ Determine engine_pattern (kg / rag / mixed)
  ✓ Extract named entities (course_code, role_id, track_id, skill_id)
  ✓ Detect student-awareness flag
  ✓ Detect ambiguity → set needs_clarification: True
  ✓ Detect session overrides in user text → put in session_overrides field
  ✗ Must NOT call KG or RAG engines
  ✗ Must NOT apply overrides to session (Session Manager does that)
  ✗ Must NOT generate the final user response

Architecture:
  Layer 1: Rule-based (keyword matching for known intents)  ← build this first
  Layer 2: LLM fallback for unrecognized / ambiguous queries ← add second

Supported intents (6 workflows from Section 4):
  W1 "get_prerequisites"         → engine_pattern: "kg"
  W1 "get_course_profile"        → engine_pattern: "kg"
  W2 "handbook_policy_query"     → engine_pattern: "rag"
  W3 "course_and_policy_query"   → engine_pattern: "mixed"
  W4 "skill_gap_analysis"        → engine_pattern: "kg",  query_type: "student_aware"
  W4 "role_recommendation"       → engine_pattern: "kg",  query_type: "student_aware"
  W5 (resolved via session context, intent determined by last_referenced)
  W6 "ambiguous"                 → needs_clarification: True

Done when:
  - 6 test queries each return correct engine_pattern
  - Hypothetical override query → session_overrides.added_courses populated
  - Ambiguous query → needs_clarification: True
  - Entity IDs resolve to exact KG schema IDs (e.g. "ROLE_DATA_SCIENTIST")
"""

import os
import re
from typing import Optional

from models.schemas import (
    StructuredQuery, EntitySet, SessionOverrides, StudentContext
)


# ── Intent keyword patterns (Layer 1 — rule-based) ───────────────────────────
# Extend this dict as you discover more patterns during testing.

INTENT_PATTERNS: dict[str, list[str]] = {
    "get_prerequisites":      ["prerequisite", "prereq", "before i take", "required for"],
    "get_course_profile":     ["tell me about", "what is", "describe the course", "how many credits"],
    "skill_gap_analysis":     ["skills am i missing", "skill gap", "missing for", "what do i need for"],
    "role_recommendation":    ["what role", "career match", "best role for me", "which job"],
    "track_guidance":         ["which track", "compare track", "best track for"],
    "handbook_policy_query":  ["rule", "policy", "regulation", "probation", "gpa requirement",
                               "credit limit", "retake", "attendance", "grade appeal"],
    "ambiguous":              [],   # fallback
}

# ── Override trigger phrases ──────────────────────────────────────────────────
OVERRIDE_PATTERNS: list[str] = [
    "assume i", "hypothetically", "if i add", "if i complete", "pretend i",
    "what if i", "suppose i", "imagine i took",
]


class QueryUnderstandingLayer:
    """
    Two-layer query classifier.
    Layer 1 (rule-based) handles deterministic patterns fast.
    Layer 2 (LLM fallback) handles anything Layer 1 can't classify.
    """

    def __init__(self):
        self._llm_api_key = os.environ.get("LLM_API_KEY", "")
        self._llm_base_url = os.environ.get("LLM_BASE_URL", "")
        self._llm_model = os.environ.get("LLM_MODEL", "")

    # ── Main entry point ─────────────────────────────────────────────────────

    def classify(
        self,
        user_text: str,
        student_context: Optional[StudentContext] = None,
    ) -> StructuredQuery:
        """
        Classify user_text and return a StructuredQuery.

        Try rule-based layer first. Fall back to LLM only if unresolved.
        """
        # TODO: implement
        # Steps:
        #   1. Normalize user_text (lowercase, strip)
        #   2. Try _rule_layer(text, context)
        #   3. If result is None → try _llm_layer(text, context)
        #   4. If still None → return ambiguous StructuredQuery
        raise NotImplementedError

    # ── Layer 1: Rule-based ──────────────────────────────────────────────────

    def _rule_layer(
        self,
        text: str,
        context: Optional[StudentContext],
    ) -> Optional[StructuredQuery]:
        """
        Keyword matching for known intents.
        Returns StructuredQuery if matched, None if unrecognized.

        Build this first and test all 6 workflows before touching Layer 2.
        """
        # TODO: implement
        # Steps:
        #   1. Check each intent in INTENT_PATTERNS for keyword match
        #   2. Extract entities with _extract_entities()
        #   3. Detect student-awareness (does text use "I", "my", "me"?)
        #   4. Detect overrides with _detect_overrides()
        #   5. Build and return StructuredQuery
        raise NotImplementedError

    def _detect_intent(self, text: str) -> Optional[str]:
        """Return the first matching intent string, or None."""
        # TODO: implement
        raise NotImplementedError

    def _determine_engine_pattern(self, intent: str) -> str:
        """
        Map intent string to "kg" | "rag" | "mixed".
        Extend this mapping as new intents are added.
        """
        kg_intents = {
            "get_prerequisites", "get_course_profile",
            "skill_gap_analysis", "role_recommendation", "track_guidance",
        }
        rag_intents = {"handbook_policy_query"}
        # TODO: mixed intents (queries that need both) go here

        if intent in kg_intents:
            return "kg"
        if intent in rag_intents:
            return "rag"
        return "mixed"

    def _extract_entities(self, text: str) -> EntitySet:
        """
        Extract course_code, role_id, track_id, skill_id from text.

        IMPORTANT: IDs must be resolved to exact KG schema IDs.
        e.g. "data scientist" → "ROLE_DATA_SCIENTIST"
             "AI track" → "AI"

        Start with exact string matching. Add fuzzy/embedding lookup later.
        """
        # TODO: implement
        raise NotImplementedError

    def _detect_student_awareness(self, text: str) -> bool:
        """
        Return True if the query is about the student themselves.
        e.g. "what am I missing", "my skill gap", "what can I do"
        """
        # TODO: implement
        # Hint: check for first-person pronouns: "i", "me", "my", "mine"
        raise NotImplementedError

    # ── Override detection ────────────────────────────────────────────────────

    def _detect_overrides(self, text: str) -> SessionOverrides:
        """
        Detect hypothetical course additions or role changes in user text.

        e.g. "assume I've completed C-CS401" → added_courses: ["C-CS401"]
             "hypothetically, target role Data Engineer" → target_role: "ROLE_DATA_ENGINEER"

        Returns a SessionOverrides object (empty if none found).
        The Session Manager will apply whatever is returned here.
        """
        # TODO: implement
        raise NotImplementedError

    # ── Layer 2: LLM fallback ─────────────────────────────────────────────────

    def _llm_layer(
        self,
        text: str,
        context: Optional[StudentContext],
    ) -> Optional[StructuredQuery]:
        """
        Call hosted LLM API with a structured prompt.
        LLM must return JSON matching StructuredQuery schema — not free text.

        Only called when Layer 1 fails to classify.
        """
        # TODO: implement
        # Steps:
        #   1. Build a system prompt explaining the StructuredQuery schema
        #   2. Include context summary if student_aware
        #   3. Call LLM API (self._llm_api_key, self._llm_base_url, self._llm_model)
        #   4. Parse JSON response → StructuredQuery
        #   5. Validate the returned intent + engine_pattern are valid values
        raise NotImplementedError

    def _build_llm_prompt(self, text: str, context: Optional[StudentContext]) -> str:
        """
        Build the system prompt for the LLM classification call.
        LLM must respond ONLY with a JSON object — no preamble, no markdown.
        """
        # TODO: implement
        raise NotImplementedError
