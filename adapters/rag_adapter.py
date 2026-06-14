"""
gateway/adapters/rag_adapter.py
────────────────────────────────
Adapter between the Orchestrator and the RAG engine.

Implements:
  - execute()           → free-text handbook query
  - execute_structured() → schema-forced extraction
  - get_rule_bundles()  → returns ALL 8 rule bundles required by ALE (Section 4 of ALE contract)

ALE contract status: FULLY IMPLEMENTED
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel

from engines.ale.schemas import (
    RetakeRules, CreditLimitRules, SummerSemesterRules,
    GraduationRequirementRules, AcademicWarningRules,
    HonorsRules, GradingScaleRules, StudentLevelRules,
    PercentageRange,
)

logger = logging.getLogger(__name__)


class RAGAdapter:

    def __init__(self) -> None:
        import sys
        import os
        
        # Add both the current flat directory and the intended real structure path
        current_dir = os.path.dirname(__file__)
        sys.path.insert(0, current_dir)
        sys.path.insert(0, os.path.join(current_dir, '..', 'engines', 'RAG'))
        sys.path.insert(0, os.path.join(current_dir, '..', '..', 'engines', 'RAG'))
        
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
            docs   = result.get("source_documents", [])
            citations = [
                {"source": "CIS Handbook", "page": d.get("page"), "text": d.get("text", "")}
                for d in docs
            ]
            if "error" in result:
                return {"data": {}, "citations": citations, "error": result["error"]}
            data = result.get("data", {})
            return {"data": data, "citations": citations}

        except Exception as exc:
            logger.error("RAGAdapter.execute_structured failed: %s", exc)
            return {"data": {}, "citations": [], "error": str(exc)}

    # ── get_rule_bundles ─────────────────────────────────────────────────────

    def get_rule_bundles(self) -> dict[str, BaseModel | None]:
        """
        Retrieves all 8 rule bundles required by ALE by querying the RAG engine
        with expected schemas.
        """
        if self.extract_structured_fn is None:
            logger.warning("RAGAdapter.get_rule_bundles: RAG engine not available — returning empty bundles")
            return {}

        def _warn_if_empty(name: str, data: dict) -> dict:
            if not data:
                logger.warning(
                    "RAGAdapter.get_rule_bundles: bundle '%s' returned empty from RAG — "
                    "Pydantic instantiation will likely fail", name
                )
            return data

        bundles: dict[str, Any] = {}

        # 1. grading_scale_rules
        grading_scale_schema = {
            "letter_to_points": {
                "A+": "float", "A": "float", "A-": "float",
                "B+": "float", "B": "float", "B-": "float",
                "C+": "float", "C": "float", "C-": "float",
                "D+": "float", "D": "float", "D-": "float",
                "Abs": "float", "F": "float",
                "P": "null"
            },
            "percentage_to_letter": [
                {"min_pct": "int", "max_pct": "int", "letter": "string"}
            ]
        }
        res_gs = self.execute_structured(
            "What is the grading scale, letter grades to GPA points mapping, and percentage ranges?",
            grading_scale_schema
        )
        if "error" in res_gs:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'grading_scale_rules' extraction error: %s",
                res_gs["error"],
            )
        bundles["grading_scale_rules"] = _warn_if_empty("grading_scale_rules", res_gs.get("data", {}))

        # 2. graduation_requirement_rules
        graduation_schema = {
            "total_credits_required": "int",
            "minimum_cgpa": "float",
            "minimum_regular_semesters": "int",
            "maximum_regular_semesters": "int",
            "must_pass_zero_credit_courses": "boolean",
            "military_training_required_for_males": "boolean"
        }
        res_gr = self.execute_structured(
            "What are the graduation requirements including total credits, minimum CGPA, min and max semesters?",
            graduation_schema
        )
        if "error" in res_gr:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'graduation_requirement_rules' extraction error: %s",
                res_gr["error"],
            )
        bundles["graduation_requirement_rules"] = _warn_if_empty("graduation_requirement_rules", res_gr.get("data", {}))

        # 3. academic_warning_rules
        warning_schema = {
            "cgpa_warning_threshold": "float",
            "max_consecutive_warnings": "int",
            "max_total_warnings": "int",
            "warning_exempt_first_semester": "boolean",
            "dismissal_extension_credits_percentage": "float",
            "dismissal_extension_extra_semesters": "int",
            "dismissal_extension_extra_summer_semesters": "int"
        }
        res_wr = self.execute_structured(
            "What are the academic warning and dismissal rules?",
            warning_schema
        )
        if "error" in res_wr:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'academic_warning_rules' extraction error: %s",
                res_wr["error"],
            )
        bundles["academic_warning_rules"] = _warn_if_empty("academic_warning_rules", res_wr.get("data", {}))

        # 4. honors_rules
        honors_schema = {
            "minimum_cgpa_throughout": "float",
            "minimum_semesters": "int",
            "maximum_semesters": "int",
            "no_f_grade_allowed": "boolean",
            "no_disciplinary_penalties": "boolean"
        }
        res_hr = self.execute_structured(
            "What are the requirements for honors?",
            honors_schema
        )
        if "error" in res_hr:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'honors_rules' extraction error: %s",
                res_hr["error"],
            )
        bundles["honors_rules"] = _warn_if_empty("honors_rules", res_hr.get("data", {}))

        # 5. credit_limit_rules
        credit_limit_schema = {
            "cgpa_above_3_limit": "int",
            "cgpa_between_2_and_3_limit": "int",
            "cgpa_between_1_and_2_limit": "int",
            "cgpa_below_1_limit": "int",
            "minimum_per_semester": "int",
            "final_semester_override": "int",
            "incomplete_extra_course_allowed": "boolean"
        }
        res_cl = self.execute_structured(
            "What are the credit limit rules per semester based on CGPA?",
            credit_limit_schema
        )
        if "error" in res_cl:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'credit_limit_rules' extraction error: %s",
                res_cl["error"],
            )
        bundles["credit_limit_rules"] = _warn_if_empty("credit_limit_rules", res_cl.get("data", {}))

        # 6. retake_rules
        retake_schema = {
            "failed_first_retake_grade_cap": "string",
            "improve_retake_first_attempt_cap": "null or string",
            "improve_retake_subsequent_cap": "string",
            "improve_retake_max_courses_cgpa_above_2": "int",
            "improve_retake_unlimited_below_cgpa": "float"
        }
        res_rr = self.execute_structured(
            "What are the course retake rules and grade caps?",
            retake_schema
        )
        if "error" in res_rr:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'retake_rules' extraction error: %s",
                res_rr["error"],
            )
        bundles["retake_rules"] = _warn_if_empty("retake_rules", res_rr.get("data", {}))

        # 7. summer_semester_rules
        summer_schema = {
            "default_max_courses": "int",
            "cgpa_above_3_max_courses": "int",
            "cgpa_threshold_for_extra_course": "float"
        }
        res_sr = self.execute_structured(
            "What are the rules and maximum courses for summer semesters?",
            summer_schema
        )
        if "error" in res_sr:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'summer_semester_rules' extraction error: %s",
                res_sr["error"],
            )
        bundles["summer_semester_rules"] = _warn_if_empty("summer_semester_rules", res_sr.get("data", {}))

        # 8. student_level_rules
        student_level_schema = {
            "freshman_max_hours": "int",
            "sophomore_min_hours": "int",
            "sophomore_max_hours": "int",
            "junior_min_hours": "int",
            "junior_max_hours": "int",
            "senior_min_hours": "int",
            "senior_max_hours": "int"
        }
        res_sl = self.execute_structured(
            "What are the credit hour thresholds for student levels (Freshman, Sophomore, Junior, Senior)?",
            student_level_schema
        )
        if "error" in res_sl:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'student_level_rules' extraction error: %s",
                res_sl["error"],
            )
        bundles["student_level_rules"] = _warn_if_empty("student_level_rules", res_sl.get("data", {}))

        # Convert percentage_to_letter tuples/dicts to PercentageRange objects
        gs_data = bundles.get("grading_scale_rules", {})
        raw_pct = gs_data.get("percentage_to_letter", [])
        if raw_pct and isinstance(raw_pct[0], (list, tuple)):
            gs_data["percentage_to_letter"] = [
                PercentageRange(min_pct=float(r[0]), max_pct=float(r[1]), letter=r[2])
                for r in raw_pct
            ]
        elif raw_pct and isinstance(raw_pct[0], dict):
            gs_data["percentage_to_letter"] = [
                PercentageRange(**r) for r in raw_pct
            ]

        pydantic_schemas = {
            "retake_rules":                 RetakeRules,
            "credit_limit_rules":           CreditLimitRules,
            "summer_semester_rules":        SummerSemesterRules,
            "graduation_requirement_rules": GraduationRequirementRules,
            "academic_warning_rules":       AcademicWarningRules,
            "honors_rules":                 HonorsRules,
            "grading_scale_rules":          GradingScaleRules,
            "student_level_rules":          StudentLevelRules,
        }

        converted_bundles: dict[str, BaseModel | None] = {}

        for bundle_name, schema_class in pydantic_schemas.items():
            bundle_data = bundles.get(bundle_name, {})
            try:
                converted_bundles[bundle_name] = schema_class(**bundle_data)
            except Exception as exc:
                logger.error(
                    "RAGAdapter.get_rule_bundles: Pydantic instantiation failed for bundle '%s': %s",
                    bundle_name,
                    exc,
                )
                converted_bundles[bundle_name] = None

        if any(value is not None for value in converted_bundles.values()):
            failed = [k for k, v in converted_bundles.items() if v is None]
            if failed:
                logger.warning(
                    "RAGAdapter.get_rule_bundles: partial rule bundle load. Failed bundles: %s",
                    failed,
                )
            return converted_bundles

        logger.critical(
            "RAGAdapter.get_rule_bundles: all rule bundle conversions failed — returning {}"
        )
        return {}
