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
import os
import time
from typing import Any, Optional

from pydantic import BaseModel

from engines.ale.schemas import (
    RetakeRules, CreditLimitRules, SummerSemesterRules,
    GraduationRequirementRules, AcademicWarningRules,
    HonorsRules, GradingScaleRules, StudentLevelRules,
    PercentageRange,
)

logger = logging.getLogger(__name__)

_ANSWER_NOT_FOUND   = "Not found in handbook."
_ANSWER_UNAVAILABLE = "The handbook search service is currently unavailable."
_ANSWER_FAILURE     = "I could not search the handbook safely right now."


def _safe_preview(text: Any, limit: int = 180) -> str:
    try:
        if text is None:
            text = ""
        if not isinstance(text, str):
            text = str(text)
        text = " ".join(text.split())
        return text[:limit]
    except Exception:
        return ""


def _duration_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _load_rule_bundle_delay() -> float:
    """Read RAG_RULE_BUNDLE_DELAY_SECONDS from env; default to 2.0 on missing/invalid."""
    try:
        return float(os.getenv("RAG_RULE_BUNDLE_DELAY_SECONDS", "2.0"))
    except (TypeError, ValueError):
        return 2.0


class RAGAdapter:

    def __init__(self) -> None:
        try:
            # Package import works when project root is on sys.path (pytest.ini pythonpath=.)
            from engines.rag.rag_core import extract_facts, extract_structured
            self.extract_facts         = extract_facts
            self.extract_structured_fn = extract_structured
            logger.info("RAGAdapter: rag_core loaded via package import.")
        except ImportError:
            # Fallback for environments where the project root is not on sys.path
            try:
                import sys
                import os
                _rag_dir = os.path.normpath(
                    os.path.join(os.path.dirname(__file__), '..', 'engines', 'rag')
                )
                if _rag_dir not in sys.path:
                    sys.path.insert(0, _rag_dir)
                import rag_core  # type: ignore[import]
                self.extract_facts         = rag_core.extract_facts
                self.extract_structured_fn = rag_core.extract_structured
                logger.info("RAGAdapter: rag_core loaded via fallback sys.path.")
            except Exception as exc:
                logger.error("RAGAdapter: failed to initialise RAG engine: %s", exc)
                self.extract_facts         = None
                self.extract_structured_fn = None
        except Exception as exc:
            logger.error("RAGAdapter: failed to initialise RAG engine: %s", exc)
            self.extract_facts         = None
            self.extract_structured_fn = None

    # ── citation builder ─────────────────────────────────────────────────────

    @staticmethod
    def _build_citations(source_documents) -> list[dict]:
        """Build citation list from source_documents; skips malformed entries."""
        if not source_documents:
            return []
        citations = []
        for d in source_documents:
            if isinstance(d, dict):
                page = d.get("page")
                text = d.get("text", "")
            elif hasattr(d, "metadata") and hasattr(d, "page_content"):
                # LangChain Document-like object
                page = d.metadata.get("page") if isinstance(d.metadata, dict) else None
                text = d.page_content or ""
            elif hasattr(d, "page"):
                page = d.page
                text = getattr(d, "text", "")
            else:
                logger.debug(
                    "RAGAdapter._build_citations: skipping malformed source_document type %s",
                    type(d).__name__,
                )
                continue
            citations.append({"source": "CIS Handbook", "page": page, "text": text})
        return citations

    # ── execute (free-text) ──────────────────────────────────────────────────

    def execute(
        self,
        sub_query: str,
        student_context: Optional[Any] = None,  # intentionally ignored — never forwarded to RAG
        trace_id: str = "",
    ) -> dict[str, Any]:
        """
        Run the RAG pipeline for one free-text handbook policy question.

        student_context is accepted to match the Orchestrator call signature but is
        NEVER forwarded to the RAG engine (hard privacy boundary).

        Returns dict with keys:
            "found"          : bool
            "answer"         : str
            "extracted_facts": list[str]
            "citations"      : list[dict]
            "error"          : str  (only present on infrastructure/input failure)
        """
        start = time.perf_counter()
        logger.info(
            "RAGAdapter.execute start trace_id=%s query_len=%d query_preview=%r",
            trace_id, len(sub_query or ""), _safe_preview(sub_query, 100),
        )

        if not sub_query or not sub_query.strip():
            logger.info(
                "RAGAdapter.execute result trace_id=%s found=False facts=0 citations=0 "
                "error=empty_query duration_ms=%d",
                trace_id, _duration_ms(start),
            )
            return {
                "found":           False,
                "answer":          _ANSWER_NOT_FOUND,
                "extracted_facts": [],
                "citations":       [],
                "error":           "empty_query",
            }

        if self.extract_facts is None:
            logger.warning(
                "RAGAdapter.execute result trace_id=%s found=False facts=0 citations=0 "
                "error=rag_unavailable duration_ms=%d",
                trace_id, _duration_ms(start),
            )
            return {
                "found":           False,
                "answer":          _ANSWER_UNAVAILABLE,
                "extracted_facts": [],
                "citations":       [],
                "error":           "rag_unavailable",
            }

        try:
            result = self.extract_facts(sub_query)
        except Exception:
            logger.exception("RAGAdapter.execute: unexpected error for query %r", sub_query)
            logger.warning(
                "RAGAdapter.execute result trace_id=%s found=False facts=0 citations=0 "
                "error=rag_adapter_error duration_ms=%d",
                trace_id, _duration_ms(start),
            )
            return {
                "found":           False,
                "answer":          _ANSWER_FAILURE,
                "extracted_facts": [],
                "citations":       [],
                "error":           "rag_adapter_error",
            }

        # Preserve safe error codes emitted by rag_core (rag_llm_error, rag_retrieval_error, …)
        if "error" in result:
            error_code = result["error"]
            logger.warning(
                "RAGAdapter.execute result trace_id=%s found=False facts=0 citations=0 "
                "error=%s duration_ms=%d",
                trace_id, error_code, _duration_ms(start),
            )
            return {
                "found":           False,
                "answer":          _ANSWER_FAILURE,
                "extracted_facts": [],
                "citations":       [],
                "error":           error_code,
            }

        facts = result.get("extracted_facts") or []
        found = bool(facts)  # facts present ↔ evidence found; empty list ↔ not found

        citations = self._build_citations(result.get("source_documents")) if found else []
        answer    = " ".join(facts) if found else _ANSWER_NOT_FOUND

        logger.info(
            "RAGAdapter.execute result trace_id=%s found=%s facts=%d citations=%d "
            "answer_preview=%r duration_ms=%d",
            trace_id, found, len(facts), len(citations),
            _safe_preview(answer, 220), _duration_ms(start),
        )
        return {
            "found":           found,
            "answer":          answer,
            "extracted_facts": facts,
            "citations":       citations,
        }

    # ── execute_structured ───────────────────────────────────────────────────

    def execute_structured(
        self,
        sub_query: str,
        expected_schema: dict,
    ) -> dict[str, Any]:
        """
        Run the RAG pipeline with schema-forced extraction.

        Returns dict with keys:
            "data"     : dict   (schema-matched result on success, {} on failure)
            "citations": list[dict]
            "error"    : str    (only present on failure)
        """
        start = time.perf_counter()
        logger.info(
            "RAGAdapter.execute_structured start query_len=%d query_preview=%r schema_keys=%s",
            len(sub_query or ""),
            _safe_preview(sub_query, 100),
            list(expected_schema.keys()) if isinstance(expected_schema, dict) else [],
        )

        if not sub_query or not sub_query.strip():
            logger.info(
                "RAGAdapter.execute_structured result data_keys=[] citations=0 error=empty_query duration_ms=%d",
                _duration_ms(start),
            )
            return {"data": {}, "citations": [], "error": "empty_query"}

        if not expected_schema or not isinstance(expected_schema, dict):
            logger.info(
                "RAGAdapter.execute_structured result data_keys=[] citations=0 error=invalid_schema duration_ms=%d",
                _duration_ms(start),
            )
            return {"data": {}, "citations": [], "error": "invalid_schema"}

        if self.extract_structured_fn is None:
            logger.warning(
                "RAGAdapter.execute_structured result data_keys=[] citations=0 error=rag_unavailable duration_ms=%d",
                _duration_ms(start),
            )
            return {"data": {}, "citations": [], "error": "rag_unavailable"}

        try:
            result = self.extract_structured_fn(sub_query, expected_schema)
        except Exception:
            logger.exception(
                "RAGAdapter.execute_structured: unexpected error for query %r", sub_query
            )
            logger.warning(
                "RAGAdapter.execute_structured result data_keys=[] citations=0 error=rag_adapter_error duration_ms=%d",
                _duration_ms(start),
            )
            return {"data": {}, "citations": [], "error": "rag_adapter_error"}

        citations = self._build_citations(result.get("source_documents"))

        if "error" in result:
            error_code = result["error"]
            logger.warning(
                "RAGAdapter.execute_structured result data_keys=[] citations=%d error=%s duration_ms=%d",
                len(citations),
                error_code,
                _duration_ms(start),
            )
            return {"data": {}, "citations": citations, "error": error_code}

        data = result.get("data") or {}
        logger.info(
            "RAGAdapter.execute_structured result data_keys=%s citations=%d duration_ms=%d",
            list(data.keys()),
            len(citations),
            _duration_ms(start),
        )
        return {"data": data, "citations": citations}

    # ── get_rule_bundles ─────────────────────────────────────────────────────

    def get_rule_bundles(self, inter_call_delay: float | None = None) -> dict[str, BaseModel | None]:
        """
        Retrieves all 8 rule bundles required by ALE by querying the RAG engine
        with expected schemas.

        inter_call_delay: seconds to sleep between LLM calls to avoid Groq 30 RPM rate limit.
            If None (default), reads RAG_RULE_BUNDLE_DELAY_SECONDS from env (default 2.0).
        """
        start = time.perf_counter()

        if inter_call_delay is None:
            inter_call_delay = _load_rule_bundle_delay()

        logger.info(
            "RAGAdapter.get_rule_bundles start inter_call_delay=%.2f",
            inter_call_delay,
        )

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

        def _sanitize_nulls(data: dict) -> dict:
            """Convert LLM 'null'/'none' strings → Python None so Pydantic's Optional fields work."""
            return {
                k: (None if isinstance(v, str) and v.strip().lower() in ("null", "none", "n/a") else v)
                for k, v in data.items()
            }

        def _merge(base: dict, supplement: dict) -> dict:
            """Fill None values in *base* from *supplement*."""
            result = dict(base)
            for k, v in supplement.items():
                if result.get(k) is None and v is not None:
                    result[k] = v
            return result

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
                {"min_pct": "float", "max_pct": "float", "letter": "string"}
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
        time.sleep(inter_call_delay)

        # 2. graduation_requirement_rules
        # Two-query approach: credit/CGPA requirements are in section 2 of the handbook;
        # maximum_regular_semesters (16) is in the Academic Warnings and Program Dismissal section.
        graduation_schema = {
            "total_credits_required": "int",
            "minimum_cgpa": "float",
            "minimum_regular_semesters": "int",
            "maximum_regular_semesters": "int",
            "must_pass_zero_credit_courses": "boolean",
            "military_training_required_for_males": "boolean"
        }
        res_gr1 = self.execute_structured(
            "What are the graduation requirements: total credit hours required, minimum CGPA at graduation, "
            "and minimum number of regular Fall/Spring semesters needed to graduate?",
            graduation_schema
        )
        if "error" in res_gr1:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'graduation_requirement_rules' query-1 error: %s",
                res_gr1["error"],
            )
        time.sleep(inter_call_delay)
        res_gr2 = self.execute_structured(
            "What is the maximum number of regular fall and spring semesters a student is allowed "
            "before being dismissed from the program for failing to graduate?",
            graduation_schema
        )
        if "error" in res_gr2:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'graduation_requirement_rules' query-2 error: %s",
                res_gr2["error"],
            )
        gr_data = _merge(res_gr1.get("data", {}), res_gr2.get("data", {}))
        # HANDBOOK-BACKED NORMALIZATION:
        # CIS Handbook Program Graduation Requirements explicitly require:
        #   - passing all 0-credit courses
        #   - male students passing military training
        # Structured extraction may emit False when the evidence is missed because booleans
        # have safe defaults. These two requirements are deterministic for the CIS program.
        gr_data["must_pass_zero_credit_courses"] = True
        gr_data["military_training_required_for_males"] = True
        bundles["graduation_requirement_rules"] = _warn_if_empty("graduation_requirement_rules", gr_data)
        time.sleep(inter_call_delay)

        # 3. academic_warning_rules
        # Two-query approach: warning/dismissal facts are in one section;
        # dismissal appeal extension terms (extra semesters) are in another.
        warning_schema = {
            "cgpa_warning_threshold": "float",
            "max_consecutive_warnings": "int",
            "max_total_warnings": "int",
            "warning_exempt_first_semester": "boolean",
            "dismissal_extension_credits_percentage": "float",
            "dismissal_extension_extra_semesters": "int",
            "dismissal_extension_extra_summer_semesters": "int"
        }
        res_wr1 = self.execute_structured(
            "What CGPA level triggers an academic warning? Is the first semester of study exempt from "
            "receiving an academic warning? How many consecutive academic warnings and how many total "
            "academic warnings result in program dismissal?",
            warning_schema
        )
        if "error" in res_wr1:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'academic_warning_rules' query-1 error: %s",
                res_wr1["error"],
            )
        time.sleep(inter_call_delay)
        res_wr2 = self.execute_structured(
            "What are the conditions and extension terms for students appealing program dismissal, "
            "including what percentage of total program credit hours must be passed, and how many "
            "extra regular semesters and extra summer semesters are granted on a successful appeal?",
            warning_schema
        )
        if "error" in res_wr2:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'academic_warning_rules' query-2 error: %s",
                res_wr2["error"],
            )
        wr_data = _merge(res_wr1.get("data", {}), res_wr2.get("data", {}))
        # Post-process: handbook states credit percentage as whole number (e.g. 80);
        # ALE schema expects decimal fraction (0.80). Normalize if LLM returns raw percentage.
        ext_pct = wr_data.get("dismissal_extension_credits_percentage")
        if ext_pct is not None and ext_pct > 1.0:
            wr_data["dismissal_extension_credits_percentage"] = round(ext_pct / 100.0, 4)
        bundles["academic_warning_rules"] = _warn_if_empty("academic_warning_rules", wr_data)
        time.sleep(inter_call_delay)

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
        time.sleep(inter_call_delay)

        # 5. credit_limit_rules
        # Two-query approach: CGPA bracket limits live in one section;
        # Incomplete-grade exception is stated separately and often missed by a general CGPA query.
        credit_limit_schema = {
            "cgpa_above_3_limit": "int",
            "cgpa_between_2_and_3_limit": "int",
            "cgpa_between_1_and_2_limit": "int",
            "cgpa_below_1_limit": "int",
            "minimum_per_semester": "int",
            "final_semester_override": "int",
            "incomplete_extra_course_allowed": "boolean"
        }
        res_cl1 = self.execute_structured(
            "What are the credit limit rules per semester based on CGPA?",
            credit_limit_schema
        )
        if "error" in res_cl1:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'credit_limit_rules' query-1 error: %s",
                res_cl1["error"],
            )
        time.sleep(inter_call_delay)
        res_cl2 = self.execute_structured(
            "Can a student with a previous Incomplete grade in a course register for that course "
            "as an extra course on top of their normal semester credit hour limit?",
            credit_limit_schema
        )
        if "error" in res_cl2:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'credit_limit_rules' query-2 error: %s",
                res_cl2["error"],
            )
        cl_data = _merge(res_cl1.get("data", {}), res_cl2.get("data", {}))
        # Q2 is the authoritative source for incomplete_extra_course_allowed.
        # _merge() only fills None; Q1 returns False (not None), so the targeted Q2 value must
        # override explicitly.
        cl2_incomplete = res_cl2.get("data", {}).get("incomplete_extra_course_allowed")
        if cl2_incomplete is not None:
            cl_data["incomplete_extra_course_allowed"] = cl2_incomplete
        bundles["credit_limit_rules"] = _warn_if_empty("credit_limit_rules", cl_data)
        time.sleep(inter_call_delay)

        # 6. retake_rules
        retake_schema = {
            "failed_first_retake_grade_cap": "string",
            "improve_retake_first_attempt_cap": "null or string",
            "improve_retake_subsequent_cap": "string",
            "improve_retake_max_courses_cgpa_above_2": "int",
            "improve_retake_unlimited_below_cgpa": "float"
        }
        res_rr = self.execute_structured(
            "What are the rules for retaking failed courses and improving passed course grades, "
            "including grade caps, maximum number of improve-retake courses for students with CGPA "
            "at or above 2.0, and the rule for students below 2.0 CGPA?",
            retake_schema
        )
        if "error" in res_rr:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'retake_rules' extraction error: %s",
                res_rr["error"],
            )
        bundles["retake_rules"] = _warn_if_empty("retake_rules", res_rr.get("data", {}))
        time.sleep(inter_call_delay)

        # 7. summer_semester_rules
        summer_schema = {
            "default_max_courses": "int",
            "cgpa_above_3_max_courses": "int",
            "cgpa_threshold_for_extra_course": "float"
        }
        res_sr = self.execute_structured(
            "In the summer semester, how many courses (not credit hours) can a student register for "
            "by default, how many courses can a student register for if their CGPA is high, and what "
            "is the CGPA threshold for the higher course limit?",
            summer_schema
        )
        if "error" in res_sr:
            logger.warning(
                "RAGAdapter.get_rule_bundles: bundle 'summer_semester_rules' extraction error: %s",
                res_sr["error"],
            )
        sr_data = res_sr.get("data", {})
        # HANDBOOK-BACKED STARTUP FALLBACK (CIS Handbook p.7):
        #   "A student can register for up to 2 courses" (default)
        #   "If your CGPA ≥3, you can register for up to 3 courses"
        # All three values are explicit handbook constants. RAG occasionally misses
        # the CGPA threshold on rate-limited or cold-start runs, so we fill only
        # fields that are still None after extraction — never overwrite a real value.
        # This fallback must be kept until Step 2D tests confirm live extraction
        # is reliable enough to remove it.
        if sr_data.get("default_max_courses") is None:
            sr_data["default_max_courses"] = 2
            logger.info("get_rule_bundles: summer default_max_courses filled from fallback (2)")
        if sr_data.get("cgpa_above_3_max_courses") is None:
            sr_data["cgpa_above_3_max_courses"] = 3
            logger.info("get_rule_bundles: summer cgpa_above_3_max_courses filled from fallback (3)")
        if sr_data.get("cgpa_threshold_for_extra_course") is None:
            sr_data["cgpa_threshold_for_extra_course"] = 3.0
            logger.info("get_rule_bundles: summer cgpa_threshold_for_extra_course filled from fallback (3.0)")
        bundles["summer_semester_rules"] = _warn_if_empty("summer_semester_rules", sr_data)
        time.sleep(inter_call_delay)

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

        # Normalize grading scale: Abs is a failing grade with 0.0 points.
        # RAG may misclassify it as null because it is non-numeric text, so normalize deterministically.
        gs_data = bundles.get("grading_scale_rules", {})
        ltp = gs_data.get("letter_to_points", {})
        if isinstance(ltp, dict) and ltp.get("Abs") is None:
            ltp["Abs"] = 0.0

        # Convert percentage_to_letter tuples/dicts to PercentageRange objects
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
            bundle_data = _sanitize_nulls(bundles.get(bundle_name, {}))
            try:
                converted_bundles[bundle_name] = schema_class(**bundle_data)
                logger.info(
                    "RAGAdapter.get_rule_bundles bundle=%s status=ok model=%s",
                    bundle_name,
                    type(converted_bundles[bundle_name]).__name__,
                )
            except Exception as exc:
                logger.error(
                    "RAGAdapter.get_rule_bundles: Pydantic instantiation failed for bundle '%s': %s",
                    bundle_name,
                    exc,
                )
                converted_bundles[bundle_name] = None
                logger.warning(
                    "RAGAdapter.get_rule_bundles bundle=%s status=missing_or_invalid",
                    bundle_name,
                )

        loaded_count = sum(1 for v in converted_bundles.values() if v is not None)
        missing_names = [k for k, v in converted_bundles.items() if v is None]

        if any(value is not None for value in converted_bundles.values()):
            if missing_names:
                logger.warning(
                    "RAGAdapter.get_rule_bundles: partial rule bundle load. Failed bundles: %s",
                    missing_names,
                )
            logger.info(
                "RAGAdapter.get_rule_bundles complete loaded=%d missing=%s duration_ms=%d",
                loaded_count,
                missing_names,
                _duration_ms(start),
            )
            return converted_bundles

        logger.critical(
            "RAGAdapter.get_rule_bundles: all rule bundle conversions failed — returning {}"
        )
        logger.info(
            "RAGAdapter.get_rule_bundles complete loaded=%d missing=%s duration_ms=%d",
            loaded_count,
            missing_names,
            _duration_ms(start),
        )
        return {}
