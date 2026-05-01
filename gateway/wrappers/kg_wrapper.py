"""
wrappers/kg_wrapper.py  [T05]
──────────────────────────────
Thin adapter between the Orchestrator and the KG query layer.

All 15 KGEngine operations are callable via call(operation_name, params_dict).
The underlying Cypher functions live in wrappers/kg_queries.py (copied from
PathFinder KG-Engine/src/queries.py). The Neo4j driver lives in
wrappers/neo4j_client.py.

Rules (Blueprint §5.7):
  ✓ Map operation name + params → query function call
  ✓ Return structured dict from the query layer
  ✗ Must NOT perform reasoning or routing
  ✗ Must NOT modify query output
  ✗ Must NOT raise — always returns a dict (error dict on failure)

Connection lifecycle:
  connect() is called at __init__ time and held open for the process lifetime.
  close() is called by the FastAPI lifespan handler on shutdown.
"""

import logging
from typing import Any

from wrappers.neo4j_client import Neo4jClient
import wrappers.kg_queries as Q

logger = logging.getLogger(__name__)


class KGWrapper:
    """
    Thin adapter between the Orchestrator and the KGEngine query layer.

    Usage:
        wrapper = KGWrapper()
        result = wrapper.call("compute_skill_gap", {
            "role_id": "RL_Data_Scientist",
            "completed_courses": ["C-CS112", "C-AI321"]
        })
        wrapper.close()   # called by lifespan handler on shutdown
    """

    def __init__(self):
        self._client = Neo4jClient()
        try:
            self._client.connect()
        except Exception as exc:
            # Non-fatal at startup — the connection will be retried on the
            # first actual query. All call() paths catch exceptions anyway.
            logger.warning("KGWrapper: Neo4j connection failed at startup: %s", exc)

    def close(self) -> None:
        """Release the Neo4j driver. Called by the FastAPI lifespan handler."""
        self._client.close()

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def call(self, operation: str, params: dict) -> dict:
        """
        Dispatch to the correct KG operation method.

        Returns the result dict, or {"status": "error", "message": ...} on
        any failure. Never raises.
        """
        _dispatch: dict[str, Any] = {
            "get_course_profile":             self.get_course_profile,
            "get_prerequisites":              self.get_prerequisites,
            "get_skills_taught":              self.get_skills_taught,
            "search_courses_by_skill":        self.search_courses_by_skill,
            "get_role_profile":               self.get_role_profile,
            "get_roles_by_track":             self.get_roles_by_track,
            "compute_skill_gap":              self.compute_skill_gap,
            "compute_alignment_score":        self.compute_alignment_score,
            "recommend_courses_to_close_gap": self.recommend_courses_to_close_gap,
            "estimate_alignment_improvement": self.estimate_alignment_improvement,
            "find_best_matching_roles":       self.find_best_matching_roles,
            "get_track_overview":             self.get_track_overview,
            "compare_tracks":                 self.compare_tracks,
            "recommend_track_for_role":       self.recommend_track_for_role,
            "recommend_track_for_skill":      self.recommend_track_for_skill,
        }

        fn = _dispatch.get(operation)
        if fn is None:
            return {"status": "error", "message": f"Unknown KG operation: {operation!r}"}

        try:
            return fn(**params)
        except TypeError as exc:
            # Wrong params dict for the operation signature
            logger.error("KGWrapper.call(%r) bad params %r: %s", operation, params, exc)
            return {"status": "error", "message": f"Bad params for {operation!r}: {exc}"}
        except Exception as exc:
            logger.exception("KGWrapper.call(%r) failed", operation)
            return {"status": "error", "message": str(exc)}

    # ── A2 — Course Catalogue Lookup ─────────────────────────────────────────

    def get_course_profile(self, course_code: str) -> dict:
        """Returns full course profile: name, credits, level, tracks, semester, description."""
        try:
            return Q.q_get_course_profile(self._client, course_code)
        except Exception as exc:
            logger.exception("get_course_profile(%r) failed", course_code)
            return {"status": "error", "message": str(exc)}

    def get_prerequisites(self, course_code: str, depth: str = "direct") -> dict:
        """depth: 'direct' (immediate prereqs) or 'full' (recursive tree)."""
        try:
            return Q.q_get_prerequisites(self._client, course_code, depth)
        except Exception as exc:
            logger.exception("get_prerequisites(%r) failed", course_code)
            return {"status": "error", "message": str(exc)}

    def get_skills_taught(self, course_code: str) -> dict:
        """Returns skills taught by the course."""
        try:
            return Q.q_get_skills_taught(self._client, course_code)
        except Exception as exc:
            logger.exception("get_skills_taught(%r) failed", course_code)
            return {"status": "error", "message": str(exc)}

    def search_courses_by_skill(self, skills: list[str]) -> dict:
        """Finds courses that teach any of the given skill names."""
        try:
            return Q.q_search_courses_by_skill(self._client, skills)
        except Exception as exc:
            logger.exception("search_courses_by_skill(%r) failed", skills)
            return {"status": "error", "message": str(exc)}

    # ── B1 — Career Role Exploration ─────────────────────────────────────────

    def get_role_profile(self, role_id: str) -> dict:
        """Returns role profile including required skills. role_id e.g. 'RL_Data_Scientist'."""
        try:
            return Q.q_get_role_profile(self._client, role_id)
        except Exception as exc:
            logger.exception("get_role_profile(%r) failed", role_id)
            return {"status": "error", "message": str(exc)}

    def get_roles_by_track(self, track_id: str) -> dict:
        """Returns roles reachable from a track via Track→Course→Skill→Role."""
        try:
            return Q.q_get_roles_by_track(self._client, track_id)
        except Exception as exc:
            logger.exception("get_roles_by_track(%r) failed", track_id)
            return {"status": "error", "message": str(exc)}

    # ── B2 — Skill Gap & Alignment ────────────────────────────────────────────

    def compute_skill_gap(self, role_id: str, completed_courses: list[str]) -> dict:
        """Returns covered_skills, missing_skills, gap counts for a target role."""
        try:
            return Q.q_compute_skill_gap(self._client, role_id, completed_courses)
        except Exception as exc:
            logger.exception("compute_skill_gap(%r) failed", role_id)
            return {"status": "error", "message": str(exc)}

    def compute_alignment_score(self, role_id: str, completed_courses: list[str]) -> dict:
        """Returns weighted alignment score [0.0, 1.0] for the target role."""
        try:
            return Q.q_compute_alignment_score(self._client, role_id, completed_courses)
        except Exception as exc:
            logger.exception("compute_alignment_score(%r) failed", role_id)
            return {"status": "error", "message": str(exc)}

    def recommend_courses_to_close_gap(
        self, role_id: str, completed_courses: list[str]
    ) -> dict:
        """Recommends courses that teach missing skills (excludes already completed)."""
        try:
            return Q.q_recommend_courses_to_close_gap(
                self._client, role_id, completed_courses
            )
        except Exception as exc:
            logger.exception("recommend_courses_to_close_gap(%r) failed", role_id)
            return {"status": "error", "message": str(exc)}

    def estimate_alignment_improvement(
        self,
        role_id: str,
        completed_courses: list[str],
        planned_courses: list[str],
    ) -> dict:
        """Projects alignment score if planned_courses are completed (what-if)."""
        try:
            return Q.q_estimate_alignment_improvement(
                self._client, role_id, completed_courses, planned_courses
            )
        except Exception as exc:
            logger.exception("estimate_alignment_improvement(%r) failed", role_id)
            return {"status": "error", "message": str(exc)}

    def find_best_matching_roles(self, completed_courses: list[str]) -> dict:
        """Ranks all roles by alignment score for the given courses."""
        try:
            return Q.q_find_best_matching_roles(self._client, completed_courses)
        except Exception as exc:
            logger.exception("find_best_matching_roles failed")
            return {"status": "error", "message": str(exc)}

    # ── B3 — Track Guidance ───────────────────────────────────────────────────

    def get_track_overview(self, track_id: str) -> dict:
        """Returns track profile: courses, skills taught, supported roles."""
        try:
            return Q.q_get_track_overview(self._client, track_id)
        except Exception as exc:
            logger.exception("get_track_overview(%r) failed", track_id)
            return {"status": "error", "message": str(exc)}

    def compare_tracks(self, track_id_1: str, track_id_2: str) -> dict:
        """Side-by-side comparison of two tracks by courses, skills, and role alignment."""
        try:
            return Q.q_compare_tracks(self._client, track_id_1, track_id_2)
        except Exception as exc:
            logger.exception("compare_tracks(%r, %r) failed", track_id_1, track_id_2)
            return {"status": "error", "message": str(exc)}

    def recommend_track_for_role(self, role_id: str) -> dict:
        """Ranks tracks by curriculum alignment to a target role."""
        try:
            return Q.q_recommend_track_for_role(self._client, role_id)
        except Exception as exc:
            logger.exception("recommend_track_for_role(%r) failed", role_id)
            return {"status": "error", "message": str(exc)}

    def recommend_track_for_skill(self, skill_id: str) -> dict:
        """Ranks tracks by how many of their courses teach the target skill."""
        try:
            return Q.q_recommend_track_for_skill(self._client, skill_id)
        except Exception as exc:
            logger.exception("recommend_track_for_skill(%r) failed", skill_id)
            return {"status": "error", "message": str(exc)}
