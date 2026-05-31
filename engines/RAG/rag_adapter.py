"""
gateway/adapters/rag_adapter.py
────────────────────────────────
Adapter between the Orchestrator and the RAG engine.

Implements:
  - execute()           → free-text handbook query
  - execute_structured() → schema-forced extraction
  - get_rule_bundles()  → returns ALL 6 rule bundles required by ALE (Section 4 of ALE contract)

ALE contract status: FULLY IMPLEMENTED
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RAGAdapter:

    def __init__(self) -> None:
        try:
            import rag_core
            self.extract_facts          = rag_core.extract_facts
            self.extract_structured_fn  = rag_core.extract_structured
            logger.info("RAGAdapter: rag_core loaded successfully.")
        except Exception as exc:
            logger.error("RAGAdapter: failed to initialise RAG engine: %s", exc)
            self.extract_facts          = None
            self.extract_structured_fn  = None

    # ── execute (free-text) ──────────────────────────────────────────────────

    def execute(
        self,
        sub_query: str,
        student_context: Optional[Any] = None,
    ) -> dict[str, Any]:
        """
        Run the RAG pipeline for one free-text question.

        Returns dict with keys expected by the Orchestrator:
            "answer"          : str
            "extracted_facts" : list[str]
            "citations"       : list[dict]
        """
        if not sub_query or not sub_query.strip():
            return {"answer": "Not found in handbook.", "extracted_facts": [], "citations": []}

        if self.extract_facts is None:
            return {"answer": "RAG Engine is currently unavailable.", "extracted_facts": [], "citations": []}

        try:
            result = self.extract_facts(sub_query)
            facts  = result.get("extracted_facts", [])
            answer = " ".join(facts) if facts else "Not found in handbook."
            docs   = result.get("source_documents", [])
            citations = [
                {"source": "CIS Handbook", "page": d.get("page"), "text": d.get("text", "")}
                for d in docs
            ]
            return {"answer": answer, "extracted_facts": facts, "citations": citations}

        except Exception as exc:
            logger.error("RAGAdapter.execute failed: %s", exc)
            return {
                "answer": f"An error occurred while searching the handbook: {exc}",
                "extracted_facts": [],
                "citations": [],
            }

    # ── execute_structured ───────────────────────────────────────────────────

    def execute_structured(
        self,
        sub_query: str,
        expected_schema: dict,
    ) -> dict[str, Any]:
        """
        Executes a query forcing the output to match the expected_schema.
        """
        if self.extract_structured_fn is None:
            return {"data": {}, "citations": [], "error": "RAG Engine is unavailable"}

        try:
            result = self.extract_structured_fn(sub_query, expected_schema)
            data   = result.get("data", {})
            docs   = result.get("source_documents", [])
            citations = [
                {"source": "CIS Handbook", "page": d.get("page"), "text": d.get("text", "")}
                for d in docs
            ]
            return {"data": data, "citations": citations}

        except Exception as exc:
            logger.error("RAGAdapter.execute_structured failed: %s", exc)
            return {"data": {}, "citations": [], "error": str(exc)}

    # ── get_rule_bundles ─────────────────────────────────────────────────────

    def get_rule_bundles(self) -> dict[str, Any]:
        """
        Returns all 6 rule bundles required by the ALE (Section 4 of ALE contract).

        These values are sourced directly from the CIS Student Handbook.
        The Orchestrator should cache this per session (TTL: per session).

        ⚠️  GRADING SCALE CONFLICT — READ BEFORE USING:
        The ALE Integration Contract (Section 4) specifies a grading scale that
        differs from the CIS Handbook. The RAG returns the HANDBOOK values.
        The ALE team must confirm which source is authoritative before GPA
        computations are trusted.

        Specific conflicts (Handbook vs Contract):
          - A:   HB=3.7 pts, >=92%   |  Contract=4.0 pts, 93-96%
          - A-:  HB=3.4 pts, >=88%   |  Contract=3.7 pts, 90-92%
          - B+:  HB=3.2 pts, >=84%   |  Contract=3.3 pts, 87-89%
          - B-:  HB=2.8 pts, >=76%   |  Contract=2.7 pts, 80-82%
          - C+:  HB=2.6 pts, >=72%   |  Contract=2.3 pts, 77-79%
          - C:   HB=2.4 pts, >=68%   |  Contract=2.0 pts, 73-76%
          - C-:  HB=2.2 pts, >=64%   |  Contract=1.7 pts, 70-72%
          - D+:  HB=2.0 pts, >=60%   |  Contract=1.3 pts, 67-69%
          - D:   HB=1.5 pts, >=55%   |  Contract=1.0 pts, 60-66%
          - D-:  HB=1.0 pts, >=50%   |  IN HANDBOOK, MISSING FROM CONTRACT
          - A+:  HB threshold >=96%  |  Contract threshold >=97%
        """

        # ── grading_scale ────────────────────────────────────────────────────
        # SOURCE: CIS Handbook Table 1 (Grading Scale)
        # ⚠️  These values differ from ALE Integration Contract Section 4.
        # Handbook is the authoritative source for the RAG.
        grading_scale = {
            "letter_to_points": {
                "A+": 4.0,
                "A":  3.7,   # ⚠️  Contract says 4.0
                "A-": 3.4,   # ⚠️  Contract says 3.7
                "B+": 3.2,   # ⚠️  Contract says 3.3
                "B":  3.0,
                "B-": 2.8,   # ⚠️  Contract says 2.7
                "C+": 2.6,   # ⚠️  Contract says 2.3
                "C":  2.4,   # ⚠️  Contract says 2.0
                "C-": 2.2,   # ⚠️  Contract says 1.7
                "D+": 2.0,   # ⚠️  Contract says 1.3
                "D":  1.5,   # ⚠️  Contract says 1.0
                "D-": 1.0,   # ⚠️  Contract does not include D-
                "F":  0.0,
                "Abs": 0.0,  # Absent from final exam without excuse = F
                "P":  None,  # Pass — not counted in GPA
            },
            "percentage_to_letter": [
                (96, 100, "A+"),   # ⚠️  Contract says 97
                (92, 95.9, "A"),   # ⚠️  Contract says 93-96
                (88, 91.9, "A-"),  # ⚠️  Contract says 90-92
                (84, 87.9, "B+"),  # ⚠️  Contract says 87-89
                (80, 83.9, "B"),   # ⚠️  Contract says 83-86
                (76, 79.9, "B-"),  # ⚠️  Contract says 80-82
                (72, 75.9, "C+"),  # ⚠️  Contract says 77-79
                (68, 71.9, "C"),   # ⚠️  Contract says 73-76
                (64, 67.9, "C-"),  # ⚠️  Contract says 70-72
                (60, 63.9, "D+"),  # ⚠️  Contract says 67-69
                (55, 59.9, "D"),   # ⚠️  Contract says 60-66
                (50, 54.9, "D-"),  # ⚠️  Not in contract
                (0,  49.9, "F"),   # ⚠️  Contract says <60
            ]
        }

        # ── graduation_rules ─────────────────────────────────────────────────
        # SOURCE: CIS Handbook Section 2 (Program Graduation Requirements)
        graduation_rules = {
            "total_credits_required":              133,
            "minimum_cgpa":                        2.0,
            "minimum_regular_semesters":           6,
            "maximum_regular_semesters":           16,
            "must_pass_zero_credit_courses":       True,
            "military_training_required_for_males": True,
        }

        # ── warning_rules ─────────────────────────────────────────────────────
        # SOURCE: CIS Handbook Academic Warning sections
        warning_rules = {
            "cgpa_warning_threshold":                   2.0,
            "max_consecutive_warnings":                 4,
            "max_total_warnings":                       6,
            "warning_exempt_first_semester":            True,
            "dismissal_extension_credits_percentage":   0.80,
            "dismissal_extension_extra_semesters":      2,
            "dismissal_extension_extra_summer_semesters": 1,
        }

        # ── honors_rules ──────────────────────────────────────────────────────
        # SOURCE: CIS Handbook Honors section
        honors_rules = {
            "minimum_cgpa_throughout":   3.0,
            "minimum_semesters":         6,
            "maximum_semesters":         8,
            "no_f_grade_allowed":        True,
            "no_disciplinary_penalties": True,
        }

        # ── credit_limit_rules ────────────────────────────────────────────────
        # SOURCE: CIS Handbook Section 5 (Course Registration Limits)
        credit_limit_rules = {
            "cgpa_above_3_limit":             21,
            "cgpa_between_2_and_3_limit":     18,
            "cgpa_between_1_and_2_limit":     15,
            "cgpa_below_1_limit":             12,
            "minimum_per_semester":            9,
            "final_semester_override":         21,
            "incomplete_extra_course_allowed": True,
        }

        # ── retake_rules ──────────────────────────────────────────────────────
        # SOURCE: CIS Handbook retake/improvement policies
        retake_rules = {
            "failed_first_retake_grade_cap":           "B",
            "improve_retake_first_attempt_cap":        None,
            "improve_retake_subsequent_cap":           "B",
            "improve_retake_max_courses_cgpa_above_2": 3,
            "improve_retake_unlimited_below_cgpa":     2.0,
        }

        # ── summer_rules ──────────────────────────────────────────────────────
        # SOURCE: CIS Handbook summer session rules
        summer_rules = {
            "default_max_courses":              2,
            "cgpa_above_3_max_courses":         3,
            "cgpa_threshold_for_extra_course":  3.0,
        }

        # ── student_level_rules ───────────────────────────────────────────────
        # SOURCE: CIS Handbook Section 2a (Student Level Classification)
        # Used by SCP to map total_credit_hours_earned → level (1/2/3/4)
        # NOTE: The ALE contract maps level numbers:
        #   1=Freshman, 2=Sophomore, 3=Junior, 4=Senior
        student_level_rules = {
            "freshman_max_hours":  26,   # 0  – 26  → level 1
            "sophomore_min_hours": 27,   # 27 – 59  → level 2
            "sophomore_max_hours": 59,
            "junior_min_hours":    60,   # 60 – 93  → level 3
            "junior_max_hours":    93,
            "senior_min_hours":    94,   # 94 – 133 → level 4
            "senior_max_hours":    133,
        }

        return {
            "grading_scale":       grading_scale,
            "graduation_rules":    graduation_rules,
            "warning_rules":       warning_rules,
            "honors_rules":        honors_rules,
            "credit_limit_rules":  credit_limit_rules,
            "retake_rules":        retake_rules,
            "summer_rules":        summer_rules,
            "student_level_rules": student_level_rules,
        }
