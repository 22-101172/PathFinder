"""
Minimal KG engine facade over the Neo4j-backed query functions.

This module is not yet used by the gateway, but it gives the `engines/kg`
folder a concrete entry point and a natural home for future service or CLI
wrapping if the team decides to split the KG engine back out later.
"""

from __future__ import annotations

from typing import Any

from engines.kg.neo4j_client import Neo4jClient
import engines.kg.queries as Q


class KGEngine:
    """Small facade that dispatches operation names to KG query functions."""

    def __init__(self, client: Neo4jClient | None = None) -> None:
        self._client = client or Neo4jClient().connect()

    def close(self) -> None:
        self._client.close()

    def call(self, operation: str, params: dict[str, Any]) -> dict:
        dispatch: dict[str, Any] = {
            "get_course_profile": Q.q_get_course_profile,
            "get_prerequisites": Q.q_get_prerequisites,
            "get_skills_taught": Q.q_get_skills_taught,
            "search_courses_by_skill": Q.q_search_courses_by_skill,
            "get_role_profile": Q.q_get_role_profile,
            "get_roles_by_track": Q.q_get_roles_by_track,
            "compute_skill_gap": Q.q_compute_skill_gap,
            "compute_alignment_score": Q.q_compute_alignment_score,
            "recommend_courses_to_close_gap": Q.q_recommend_courses_to_close_gap,
            "estimate_alignment_improvement": Q.q_estimate_alignment_improvement,
            "find_best_matching_roles": Q.q_find_best_matching_roles,
            "get_track_overview": Q.q_get_track_overview,
            "compare_tracks": Q.q_compare_tracks,
            "recommend_track_for_role": Q.q_recommend_track_for_role,
            "recommend_track_for_skill": Q.q_recommend_track_for_skill,
        }

        fn = dispatch.get(operation)
        if fn is None:
            return {"status": "error", "message": f"Unknown KG operation: {operation!r}"}

        try:
            return fn(self._client, **params)
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
