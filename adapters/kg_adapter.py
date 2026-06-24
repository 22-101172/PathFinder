"""
Thin adapter between the orchestrator and the KG engine.
All 18 KG operations callable via call(operation, params).
"""
from __future__ import annotations
import logging
import time
from typing import Any

from engines.kg.neo4j_client import Neo4jClient
import engines.kg.queries as Q

logger = logging.getLogger(__name__)

_ADAPTER_ERRORS = {"kg_unavailable", "unknown_operation", "bad_params", "kg_error"}

# Scalar param keys that are safe to log verbatim
_SAFE_SCALAR_KEYS = {
    "course_code", "role_id", "track_id", "track_id_1", "track_id_2",
    "skill_id", "entity_type", "entity_text", "depth",
    "target_id", "target_type",
}

# List param keys that may be long — summarise as count + short preview
_LIST_KEYS = {"completed_courses", "skill_ids", "planned_courses"}

# Result list fields worth counting
_COUNTED_LIST_FIELDS = {
    "results", "courses", "skills_taught", "required_skills", "covered_skills",
    "missing_skills", "recommended_courses", "direct_prerequisites",
    "full_prerequisite_tree", "non_course_prerequisites", "tracks", "roles",
    "focus_courses", "ranked_roles", "ranked_tracks",
}

# Compact scalar result fields worth surfacing
_COMPACT_SCALAR_FIELDS = {"total_results", "total_skills", "alignment_percentage"}


def _duration_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _summarize_params(params: dict | None) -> dict:
    try:
        if not isinstance(params, dict):
            return {"_invalid": type(params).__name__ if params is not None else "NoneType"}
        summary: dict[str, Any] = {}
        for k, v in params.items():
            if k in _SAFE_SCALAR_KEYS:
                summary[k] = v
            elif k in _LIST_KEYS:
                if isinstance(v, list):
                    preview = v[:3] + (["..."] if len(v) > 3 else [])
                    summary[k] = {"count": len(v), "preview": preview}
                else:
                    summary[k] = v
            elif isinstance(v, list):
                preview = v[:5] + (["..."] if len(v) > 5 else [])
                summary[k] = {"count": len(v), "preview": preview}
            elif isinstance(v, dict):
                summary[k] = {"_type": "dict"}
            elif isinstance(v, (str, int, float, bool)) or v is None:
                summary[k] = v
            else:
                summary[k] = {"_type": type(v).__name__}
        return summary
    except Exception:
        return {}


def _summarize_result(result: dict) -> tuple[str, dict]:
    try:
        keys = list(result.keys())
        error = result.get("error")

        if error is not None:
            status = "adapter_error" if error in _ADAPTER_ERRORS else "business_error"
        else:
            status = "success"

        summary: dict[str, Any] = {"keys": keys}
        if error is not None:
            summary["error"] = error

        counts: dict[str, int] = {}
        for field in _COUNTED_LIST_FIELDS:
            val = result.get(field)
            if isinstance(val, list):
                counts[field] = len(val)
        if counts:
            summary["counts"] = counts

        for field in _COMPACT_SCALAR_FIELDS:
            if field in result:
                summary[field] = result[field]

        return status, summary
    except Exception:
        return "success", {"keys": []}


class KGAdapter:

    def __init__(self):
        self._client = None
        try:
            client = Neo4jClient()
            client.connect()
            self._client = client
            logger.info("KGAdapter: connected to Neo4j")
        except Exception as exc:
            logger.warning("KGAdapter: Neo4j unavailable at startup: %s", exc)

    def close(self):
        if self._client is not None:
            self._client.close()

    def call(self, operation: str, params: dict) -> dict:
        start = time.perf_counter()
        safe_params = _summarize_params(params)
        logger.info(
            "KGAdapter.call start operation=%s params=%s",
            operation,
            safe_params,
        )

        if self._client is None:
            logger.warning(
                "KGAdapter.call result operation=%s status=adapter_error error=kg_unavailable duration_ms=%d",
                operation,
                _duration_ms(start),
            )
            return {
                "error": "kg_unavailable",
                "detail": "Knowledge Graph is not connected. Check Neo4j configuration and startup.",
            }

        dispatch: dict[str, Any] = {
            "get_course_profile":              self.get_course_profile,
            "get_prerequisites":               self.get_prerequisites,
            "get_skills_taught":               self.get_skills_taught,
            "search_courses_by_skill":         self.search_courses_by_skill,
            "get_role_profile":                self.get_role_profile,
            "get_roles_by_track":              self.get_roles_by_track,
            "compute_skill_gap":               self.compute_skill_gap,
            "compute_alignment_score":         self.compute_alignment_score,
            "recommend_courses_to_close_gap":  self.recommend_courses_to_close_gap,
            "estimate_alignment_improvement":  self.estimate_alignment_improvement,
            "find_best_matching_roles":        self.find_best_matching_roles,
            "get_track_overview":              self.get_track_overview,
            "compare_tracks":                  self.compare_tracks,
            "recommend_track_for_role":        self.recommend_track_for_role,
            "recommend_track_for_skill":       self.recommend_track_for_skill,
            "get_courses_by_track":            self.get_courses_by_track,
            "get_focus_courses_for_target":    self.get_focus_courses_for_target,
            "resolve_entity":                  self.resolve_entity,
        }
        fn = dispatch.get(operation)
        if fn is None:
            logger.warning(
                "KGAdapter.call result operation=%s status=adapter_error error=unknown_operation duration_ms=%d",
                operation,
                _duration_ms(start),
            )
            return {"error": f"unknown_operation", "detail": operation}

        try:
            result = fn(**params)
            status, summary = _summarize_result(result)
            log = logger.warning if status != "success" else logger.info
            log(
                "KGAdapter.call result operation=%s status=%s summary=%s duration_ms=%d",
                operation,
                status,
                summary,
                _duration_ms(start),
            )
            return result
        except TypeError as exc:
            logger.error("KGAdapter.call(%r) bad params: %s", operation, exc)
            logger.warning(
                "KGAdapter.call result operation=%s status=adapter_error error=bad_params duration_ms=%d",
                operation,
                _duration_ms(start),
            )
            return {"error": "bad_params", "detail": str(exc)}
        except Exception as exc:
            logger.exception("KGAdapter.call(%r) failed", operation)
            logger.warning(
                "KGAdapter.call result operation=%s status=adapter_error error=kg_error duration_ms=%d",
                operation,
                _duration_ms(start),
            )
            return {"error": "kg_error", "detail": str(exc)}

    def get_course_profile(self, course_code: str) -> dict:
        return Q.q_get_course_profile(self._client, course_code)

    def get_prerequisites(self, course_code: str, depth: str = "direct") -> dict:
        return Q.q_get_prerequisites(self._client, course_code, depth)

    def get_skills_taught(self, course_code: str) -> dict:
        return Q.q_get_skills_taught(self._client, course_code)

    def search_courses_by_skill(self, skill_ids: list[str]) -> dict:
        return Q.q_search_courses_by_skill(self._client, skill_ids)

    def get_role_profile(self, role_id: str) -> dict:
        return Q.q_get_role_profile(self._client, role_id)

    def get_roles_by_track(self, track_id: str) -> dict:
        return Q.q_get_roles_by_track(self._client, track_id)

    def compute_skill_gap(self, role_id: str, completed_courses: list[str]) -> dict:
        return Q.q_compute_skill_gap(self._client, role_id, completed_courses)

    def compute_alignment_score(self, role_id: str, completed_courses: list[str]) -> dict:
        return Q.q_compute_alignment_score(self._client, role_id, completed_courses)

    def recommend_courses_to_close_gap(self, role_id: str, completed_courses: list[str]) -> dict:
        return Q.q_recommend_courses_to_close_gap(self._client, role_id, completed_courses)

    def estimate_alignment_improvement(self, role_id: str, completed_courses: list[str], planned_courses: list[str]) -> dict:
        return Q.q_estimate_alignment_improvement(self._client, role_id, completed_courses, planned_courses)

    def find_best_matching_roles(self, completed_courses: list[str]) -> dict:
        return Q.q_find_best_matching_roles(self._client, completed_courses)

    def get_track_overview(self, track_id: str) -> dict:
        return Q.q_get_track_overview(self._client, track_id)

    def compare_tracks(self, track_id_1: str, track_id_2: str) -> dict:
        return Q.q_compare_tracks(self._client, track_id_1, track_id_2)

    def recommend_track_for_role(self, role_id: str) -> dict:
        return Q.q_recommend_track_for_role(self._client, role_id)

    def recommend_track_for_skill(self, skill_id: str) -> dict:
        return Q.q_recommend_track_for_skill(self._client, skill_id)

    def get_courses_by_track(self, track_id: str) -> dict:
        return Q.q_get_courses_by_track(self._client, track_id)

    def get_focus_courses_for_target(
        self,
        target_id: str,
        target_type: str,
        completed_courses: list,
    ) -> dict:
        return Q.q_get_focus_courses_for_target(
            self._client, target_id, target_type, completed_courses
        )

    def resolve_entity(self, entity_type: str, entity_text: str) -> dict:
        return Q.q_resolve_entity(self._client, entity_type, entity_text)
