from __future__ import annotations
import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

OPERATION_MAP = {
    "check_eligibility":      "check_eligibility",
    "run_graduation_audit":   "run_graduation_audit",
    "generate_semester_plan": "generate_semester_plan",
    "simulate_gpa":           "simulate_gpa",
}


class ALEAdapter:

    def __init__(self):
        from engines.ale.ale_wrapper import AcademicLogicWrapper
        self._ale = AcademicLogicWrapper()
        self._curriculum = self._load_curriculum()
        logger.info("ALEAdapter: initialized")

    def call(self, operation: str, params: Dict[str, Any]) -> Dict[str, Any]:
        method_name = OPERATION_MAP.get(operation)
        if method_name is None:
            return {"status": "error", "message": f"Unknown operation: {operation}"}
        try:
            payload = self._build_payload(operation, params)
            method = getattr(self._ale, method_name)
            result = method(payload)
            return result
        except Exception as exc:
            logger.exception("ALEAdapter error | operation=%s", operation)
            return {"status": "error", "message": str(exc), "operation": operation}

    def _build_payload(self, operation: str, params: Dict[str, Any]) -> Dict[str, Any]:
        student_ctx = params.get("student_context", {})
        entities = params.get("entities", {})

        if operation == "check_eligibility":
            grade_points = {}
            for record in student_ctx.get("course_history", []):
                if isinstance(record, dict) and record.get("grade_points") is not None:
                    grade_points[record["course_code"]] = record["grade_points"]
            in_progress_credits = sum(
                r.get("credit_hours", 0) if isinstance(r, dict) else getattr(r, "credit_hours", 0)
                for r in student_ctx.get("course_history", [])
                if (r.get("status") if isinstance(r, dict) else getattr(r, "status", "")) == "in_progress"
            )
            max_cr = self._credit_limit_from_cgpa(student_ctx.get("cgpa", 0.0))
            course_code = params.get("course_code") or (entities.get("course_codes") or [None])[0]
            return {
                "student_snapshot": {
                    "completed_courses": student_ctx.get("completed_courses", []),
                    "in_progress_courses": student_ctx.get("in_progress_courses", []),
                    "failed_courses": student_ctx.get("failed_courses", []),
                    "cgpa": student_ctx.get("cgpa", 0.0),
                    "total_credit_hours_earned": student_ctx.get("total_credit_hours_earned", 0),
                    "max_credit_hours_allowed": max_cr,
                    "total_credit_hours_in_progress": in_progress_credits,
                    "course_grade_points": grade_points,
                },
                "target_course": params.get("target_course", {
                    "course_code": course_code,
                    "credits": 3,
                    "prerequisites": {"direct": [], "non_course": []},
                }),
                "term_context": {"term": student_ctx.get("current_semester", ""), "is_final_semester": False},
            }

        if operation == "run_graduation_audit":
            enriched = dict(student_ctx)
            enriched["cgpa"] = float(enriched.get("cgpa") or 0.0)
            enriched.setdefault("military_status", None)
            enriched.setdefault("study_status", "Studying")
            enriched.setdefault("consecutive_warnings", 0)
            enriched.setdefault("total_warnings", 0)
            track_id = student_ctx.get("track_id", "")
            curriculum_rules = None
            if self._curriculum and track_id:
                tracks = self._curriculum.get("tracks", {})
                track_data = tracks.get(track_id) or tracks.get("CS", {})
                if track_data:
                    curriculum_rules = {
                        "track_id": track_id,
                        "total_credits_required": track_data.get("total_credits_required", 133),
                        "minimum_semesters": track_data.get("minimum_semesters", 6),
                        "required_courses": track_data.get("required_courses", []),
                    }
            return {"student_context": enriched, "curriculum_rules": curriculum_rules}

        if operation == "generate_semester_plan":
            enriched = dict(student_ctx)
            enriched["cgpa"] = float(enriched.get("cgpa") or 0.0)
            enriched.setdefault("military_status", None)
            enriched.setdefault("study_status", "Studying")
            enriched.setdefault("consecutive_warnings", 0)
            enriched.setdefault("total_warnings", 0)
            constraints = params.get("constraints", {})
            return {
                "student_context": enriched,
                "available_offerings": params.get("available_offerings", []),
                "curriculum_rules": None,
                "constraints": {
                    "max_credits": constraints.get("max_credits", 0),
                    "term": student_ctx.get("current_semester", ""),
                    "is_final_semester": constraints.get("is_final_semester", False),
                },
            }

        if operation == "simulate_gpa":
            raw_scenario = params.get("simulation_scenario", {})
            grade_assump = raw_scenario.get("grade_assumptions") or {}
            courses = [
                {"course_code": code, "credits": 3, "expected_grade_points": float(gp)}
                for code, gp in grade_assump.items()
            ]
            if not courses:
                for code in student_ctx.get("planned_courses", []):
                    courses.append({"course_code": code, "credits": 3, "expected_grade_points": 3.0})
            return {
                "student_context": {
                    "cgpa": float(student_ctx.get("cgpa") or 0.0),
                    "total_credit_hours_earned": student_ctx.get("total_credit_hours_earned", 0),
                    "track_id": student_ctx.get("track_id", ""),
                    "academic_standing": student_ctx.get("academic_standing", "good"),
                    "course_history": student_ctx.get("course_history", []),
                    "completed_courses": student_ctx.get("completed_courses", []),
                    "failed_courses": student_ctx.get("failed_courses", []),
                    "in_progress_courses": student_ctx.get("in_progress_courses", []),
                },
                "simulation_scenario": {"courses": courses},
            }

        raise ValueError(f"No payload builder for {operation}")

    def _credit_limit_from_cgpa(self, cgpa: float) -> int:
        if self._curriculum:
            limits = self._curriculum.get("global_rules", {}).get("credit_limits", {})
            if cgpa >= 3.0: return limits.get("above_3_0", 21)
            if cgpa >= 2.0: return limits.get("between_2_0_and_3_0", 18)
            if cgpa >= 1.0: return limits.get("below_2_0", 15)
            return limits.get("below_1_0", 12)
        if cgpa >= 3.0: return 21
        if cgpa >= 2.0: return 18
        if cgpa >= 1.0: return 15
        return 12

    def _load_curriculum(self) -> dict:
        try:
            path = os.path.join(os.path.dirname(__file__), "..", "engines", "ale", "rules", "curriculum_2026.json")
            with open(os.path.normpath(path), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("ALEAdapter: curriculum not loaded: %s", exc)
            return {}
