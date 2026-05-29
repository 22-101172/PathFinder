"""
Thin adapter between the orchestrator and the KG engine.
All 15 KG operations callable via call(operation, params).
"""
from __future__ import annotations
import logging
from typing import Any

from engines.kg.neo4j_client import Neo4jClient
import engines.kg.queries as Q

logger = logging.getLogger(__name__)


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
        if self._client is None:
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
            "get_course_focus":                self.get_course_focus,
            "get_focus_courses_for_target":    self.get_focus_courses_for_target,
            "resolve_entity":                  self.resolve_entity,
        }
        fn = dispatch.get(operation)
        if fn is None:
            return {"error": f"unknown_operation", "detail": operation}
        try:
            return fn(**params)
        except TypeError as exc:
            logger.error("KGAdapter.call(%r) bad params: %s", operation, exc)
            return {"error": "bad_params", "detail": str(exc)}
        except Exception as exc:
            logger.exception("KGAdapter.call(%r) failed", operation)
            return {"error": "kg_error", "detail": str(exc)}

    def get_course_profile(self, course_code: str) -> dict:
        return Q.q_get_course_profile(self._client, course_code)

    def get_prerequisites(self, course_code: str, depth: str = "direct") -> dict:
        return Q.q_get_prerequisites(self._client, course_code, depth)

    def get_skills_taught(self, course_code: str) -> dict:
        return Q.q_get_skills_taught(self._client, course_code)

    def search_courses_by_skill(self, skills: list[str]) -> dict:
        return Q.q_search_courses_by_skill(self._client, skills)

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

    def get_course_focus(self, course_code: str) -> dict:
        return Q.q_get_course_focus(self._client, course_code)

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
