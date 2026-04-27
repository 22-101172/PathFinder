"""
wrappers/kg_wrapper.py  [T05 — Person B]
─────────────────────────────────────────
Responsibility:
  Translate structured operation names + params into KGEngine method calls.
  Return the raw structured dict from KGEngine.

Rules (from blueprint Section 5.7):
  ✓ Map operation name + params → KGEngine method call
  ✓ Return structured dict from KGEngine
  ✗ Must NOT perform reasoning or routing
  ✗ Must NOT modify KGEngine output

All 15 KGEngine operations (from KG component documentation):
  A2 — Course Catalogue Lookup:
    1.  get_course_profile(course_code)
    2.  get_prerequisites(course_code, depth)
    3.  get_skills_taught(course_code)
    4.  search_courses_by_skill(skills: list[str])

  B1 — Career Role Exploration:
    5.  get_role_profile(role_id)
    6.  get_roles_by_track(track_id)

  B2 — Skill Gap & Alignment:
    7.  compute_skill_gap(role_id, completed_courses)
    8.  compute_alignment_score(role_id, completed_courses)
    9.  recommend_courses_to_close_gap(role_id, completed_courses)
    10. estimate_alignment_improvement(role_id, completed_courses, planned_courses)
    11. find_best_matching_roles(completed_courses)

  B3 — Track Guidance:
    12. get_track_overview(track_id)
    13. compare_tracks(track_id_1, track_id_2)
    14. recommend_track_for_role(role_id)
    15. recommend_track_for_skill(skill_id)

Done when:
  - All 15 operations callable via call(operation, params)
  - Invalid entity returns {"status": "error", ...} — never raises exception
  - All method signatures match KGEngine exactly
"""

from typing import Any


class KGWrapper:
    """
    Thin adapter between the Orchestrator and the KGEngine Python class.

    Usage:
        wrapper = KGWrapper()
        result = wrapper.call("compute_skill_gap", {
            "role_id": "ROLE_DATA_SCIENTIST",
            "completed_courses": ["C-CS112", "C-CS201"]
        })
    """

    def __init__(self):
        # TODO: import and instantiate KGEngine
        # from kg_engine.src.engine import KGEngine  (adjust path to actual module)
        # self._engine = KGEngine()
        self._engine = None  # placeholder

    def call(self, operation: str, params: dict) -> dict:
        """
        Dispatch to the correct KGEngine method.
        Returns the KGEngine result dict, or an error dict on failure.

        Never raises — always returns a dict.
        """
        # TODO: implement
        # Map operation name to method call:
        #   operation_map = {
        #       "get_course_profile": self._engine.get_course_profile,
        #       "get_prerequisites":  self._engine.get_prerequisites,
        #       ... all 15 ...
        #   }
        # Call with params as kwargs.
        # Catch exceptions and return {"status": "error", "message": str(e)}
        raise NotImplementedError

    # ── Individual operation methods (wrappers for clarity + docstrings) ──────

    def get_course_profile(self, course_code: str) -> dict:
        """Returns course metadata: name, credits, level, track, semester, description."""
        # TODO: return self._engine.get_course_profile(course_code)
        raise NotImplementedError

    def get_prerequisites(self, course_code: str, depth: str = "direct") -> dict:
        """depth: 'direct' (immediate prereqs) or 'full' (recursive)."""
        # TODO: return self._engine.get_prerequisites(course_code, depth)
        raise NotImplementedError

    def get_skills_taught(self, course_code: str) -> dict:
        # TODO: return self._engine.get_skills_taught(course_code)
        raise NotImplementedError

    def search_courses_by_skill(self, skills: list[str]) -> dict:
        # TODO: return self._engine.search_courses_by_skill(skills)
        raise NotImplementedError

    def get_role_profile(self, role_id: str) -> dict:
        # TODO: return self._engine.get_role_profile(role_id)
        raise NotImplementedError

    def get_roles_by_track(self, track_id: str) -> dict:
        # TODO: return self._engine.get_roles_by_track(track_id)
        raise NotImplementedError

    def compute_skill_gap(self, role_id: str, completed_courses: list[str]) -> dict:
        """Returns missing_skills, covered_skills, gap_percentage."""
        # TODO: return self._engine.compute_skill_gap(role_id, completed_courses)
        raise NotImplementedError

    def compute_alignment_score(self, role_id: str, completed_courses: list[str]) -> dict:
        """Returns normalized score 0.0–1.0."""
        # TODO: return self._engine.compute_alignment_score(role_id, completed_courses)
        raise NotImplementedError

    def recommend_courses_to_close_gap(self, role_id: str, completed_courses: list[str]) -> dict:
        # TODO: return self._engine.recommend_courses_to_close_gap(role_id, completed_courses)
        raise NotImplementedError

    def estimate_alignment_improvement(
        self,
        role_id: str,
        completed_courses: list[str],
        planned_courses: list[str],
    ) -> dict:
        """What-if: projects alignment score if planned_courses are completed."""
        # TODO: return self._engine.estimate_alignment_improvement(role_id, completed_courses, planned_courses)
        raise NotImplementedError

    def find_best_matching_roles(self, completed_courses: list[str]) -> dict:
        """Returns role recommendations ranked by alignment score."""
        # TODO: return self._engine.find_best_matching_roles(completed_courses)
        raise NotImplementedError

    def get_track_overview(self, track_id: str) -> dict:
        # TODO: return self._engine.get_track_overview(track_id)
        raise NotImplementedError

    def compare_tracks(self, track_id_1: str, track_id_2: str) -> dict:
        # TODO: return self._engine.compare_tracks(track_id_1, track_id_2)
        raise NotImplementedError

    def recommend_track_for_role(self, role_id: str) -> dict:
        # TODO: return self._engine.recommend_track_for_role(role_id)
        raise NotImplementedError

    def recommend_track_for_skill(self, skill_id: str) -> dict:
        # TODO: return self._engine.recommend_track_for_skill(skill_id)
        raise NotImplementedError
