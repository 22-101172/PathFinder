"""
Phase 2 D3 tests — Career / Role domain.

Covers:
- Orchestrator D3 dispatch: get_role_profile, get_roles_by_track, compute_skill_gap,
  compute_alignment_score, recommend_courses_to_close_gap, find_best_matching_roles,
  estimate_alignment_improvement, get_focus_courses_for_target
- No-completed-courses guard (informational early return)
- Composer deterministic rendering of all D3 intents

All KG calls are mocked. No live Neo4j or Excel. No LLM calls.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
import os
os.environ.setdefault("COMPOSER_USE_LLM", "false")

from gateway.models.schemas import (
    EntitySet, PerSQResult, SessionOverrides, SessionState,
    StudentContext, StructuredQuery, TurnWrapper,
)
from gateway.orchestrator import Orchestrator
from gateway.response_composer import _deterministic_answer, _extract_packet


# ── Shared fixtures ────────────────────────────────────────────────────────────

_ROLE_DB = {
    "RL_Data_Scientist": {
        "role_id": "RL_Data_Scientist",
        "role_name": "Data Scientist",
        "description": "Analyzes large datasets to extract insights.",
        "required_skills": [
            {"skill_id": "SK_ML", "name": "Machine Learning"},
            {"skill_id": "SK_Python", "name": "Python"},
            {"skill_id": "SK_Stats", "name": "Statistics"},
        ],
        "recommended_tracks": ["DSE", "AI"],
    },
    "RL_SWE": {
        "role_id": "RL_SWE",
        "role_name": "Software Engineer",
        "description": "Builds software systems.",
        "required_skills": [{"skill_id": "SK_OOP", "name": "Object-Oriented Programming"}],
    },
}

_SKILL_GAP_RESULT = {
    "role_id": "RL_Data_Scientist",
    "role_name": "Data Scientist",
    "missing_skills": [
        {"skill_id": "SK_ML", "name": "Machine Learning"},
        {"skill_id": "SK_Stats", "name": "Statistics"},
    ],
    "covered_skills": [{"skill_id": "SK_Python", "name": "Python"}],
    "skill_gap_count": 2,
    "alignment_score": 0.33,
}

_RANKED_ROLES_RESULT = {
    "ranked_roles": [
        {"role_id": "RL_SWE", "name": "Software Engineer", "alignment_score": 0.85},
        {"role_id": "RL_Data_Scientist", "name": "Data Scientist", "alignment_score": 0.67},
        {"role_id": "RL_Analyst", "name": "Business Analyst", "alignment_score": 0.50},
        {"role_id": "RL_DevOps", "name": "DevOps Engineer", "alignment_score": 0.40},
        {"role_id": "RL_QA", "name": "QA Engineer", "alignment_score": 0.30},
    ],
}

_RECOMMEND_COURSES_RESULT = {
    "role_id": "RL_Data_Scientist",
    "role_name": "Data Scientist",
    "recommended_courses": [
        {"course_code": "C-AI301", "name": "Intro to Machine Learning"},
        {"course_code": "C-ST201", "name": "Statistics I"},
    ],
}

_ROLES_BY_TRACK_RESULT = {
    "track_id": "DSE",
    "track_name": "Data Science and Engineering",
    "roles": [
        {"role_id": "RL_Data_Scientist", "name": "Data Scientist"},
        {"role_id": "RL_Analyst", "name": "Data Analyst"},
        {"role_id": "RL_MLE", "name": "Machine Learning Engineer"},
    ],
}

_FOCUS_COURSES_RESULT = {
    "role_id": "RL_Data_Scientist",
    "role_name": "Data Scientist",
    "focus_courses": [
        {"course_code": "C-AI301", "name": "Intro to Machine Learning"},
        {"course_code": "C-DS201", "name": "Data Science Fundamentals"},
    ],
}

_ALIGNMENT_IMPROVEMENT_RESULT = {
    "role_id": "RL_Data_Scientist",
    "role_name": "Data Scientist",
    "current_alignment": 0.33,
    "new_alignment": 0.66,
    "projected_improvement": 0.66,
}


def _make_kg() -> MagicMock:
    kg = MagicMock()

    def _kg_call(operation, params=None):
        params = params or {}
        if operation == "get_role_profile":
            role = params.get("role_id", "")
            if role in _ROLE_DB:
                return _ROLE_DB[role]
            return {"error": "role_not_found", "detail": f"Role {role!r} not found"}
        if operation == "get_roles_by_track":
            return _ROLES_BY_TRACK_RESULT
        if operation == "compute_skill_gap":
            return _SKILL_GAP_RESULT
        if operation == "compute_alignment_score":
            return {
                "role_id": params.get("role_id"),
                "role_name": "Data Scientist",
                "alignment_score": 0.33,
            }
        if operation == "recommend_courses_to_close_gap":
            return _RECOMMEND_COURSES_RESULT
        if operation == "find_best_matching_roles":
            return _RANKED_ROLES_RESULT
        if operation == "estimate_alignment_improvement":
            return _ALIGNMENT_IMPROVEMENT_RESULT
        if operation == "get_focus_courses_for_target":
            return _FOCUS_COURSES_RESULT
        return {"error": "unknown_operation"}

    kg.call.side_effect = _kg_call
    return kg


def _make_session(
    completed: list[str] | None = None,
    track_id: str = "DSE",
) -> SessionState:
    ctx = StudentContext(
        student_id="S001",
        name="Test Student",
        program="Computer Science",
        track_id=track_id,
        level=3,
        first_semester="Fall 2021",
        study_status="Active",
        cgpa=2.8,
        total_credit_hours_earned=90,
        cumulative_chs=90,
        cumulative_cps=252.0,
        completed_courses=completed if completed is not None else ["C-CS101", "C-MA111"],
        failed_courses=[],
        in_progress_courses=["C-AI301"],
        current_semester="Spring 2024",
    )
    return SessionState(
        session_id=str(uuid.uuid4()),
        student_id="S001",
        session_name="test",
        student_context=ctx,
    )


def _sq(intent: str, entities: dict = None, params: dict = None,
         student_ref: bool = False) -> StructuredQuery:
    return StructuredQuery(
        intent=intent,
        entities=EntitySet(**(entities or {})),
        params=params or {},
        student_referential_fallback=student_ref,
    )


def _make_tw(results: list[PerSQResult]) -> TurnWrapper:
    return TurnWrapper(
        turn_id=str(uuid.uuid4()),
        session_id="sess-test",
        timestamp="2024-01-01T00:00:00+00:00",
        results=results,
        result_count=len(results),
        turn_status="completed",
        has_error=False,
        has_clarification=False,
        has_informational=False,
        has_soft_no_evidence=False,
    )


def _make_orch() -> Orchestrator:
    kg = _make_kg()
    rag = MagicMock()
    ale = MagicMock()
    return Orchestrator(kg, rag, ale)


# ── get_role_profile ───────────────────────────────────────────────────────────

class TestGetRoleProfile:

    def test_happy_path_known_role(self):
        orch = _make_orch()
        sess = _make_session()
        sq = _sq("get_role_profile", entities={"role_id": "RL_Data_Scientist"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert r.data["role_name"] == "Data Scientist"
        assert len(r.data["required_skills"]) == 3

    def test_no_role_id_returns_clarification(self):
        orch = _make_orch()
        sess = _make_session()
        sq = _sq("get_role_profile")
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "clarification_needed"
        assert "role" in r.clarification_prompt.lower()

    def test_unknown_role_returns_informational(self):
        orch = _make_orch()
        sess = _make_session()
        sq = _sq("get_role_profile", entities={"role_id": "RL_Unknown"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "informational"
        assert "error" in r.data

    def test_kg_unavailable_returns_engine_error(self):
        orch = _make_orch()
        # side_effect takes priority over return_value; clear it first
        orch._kg.call.side_effect = None
        orch._kg.call.return_value = {"error": "kg_unavailable"}
        sess = _make_session()
        sq = _sq("get_role_profile", entities={"role_id": "RL_Data_Scientist"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "error"
        assert r.error_code == "engine_error"


# ── compute_skill_gap ─────────────────────────────────────────────────────────

class TestComputeSkillGap:

    def test_happy_path(self):
        orch = _make_orch()
        sess = _make_session(completed=["C-CS101", "C-MA111"])
        sq = _sq("compute_skill_gap", entities={"role_id": "RL_Data_Scientist"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert r.data["skill_gap_count"] == 2
        assert len(r.data["missing_skills"]) == 2

    def test_no_role_id_clarification(self):
        orch = _make_orch()
        sess = _make_session(completed=["C-CS101"])
        sq = _sq("compute_skill_gap")
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "clarification_needed"

    def test_no_completed_courses_returns_informational(self):
        """Empty completed_courses → early informational return, no KG call."""
        orch = _make_orch()
        sess = _make_session(completed=[])
        sq = _sq("compute_skill_gap", entities={"role_id": "RL_Data_Scientist"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "informational"
        assert r.data.get("reason") == "no_completed_courses"
        orch._kg.call.assert_not_called()

    def test_assumptions_active_flag(self):
        """assumptions_active=True propagates when session overrides are set."""
        orch = _make_orch()
        sess = _make_session(completed=["C-CS101"])
        sq = _sq("compute_skill_gap", entities={"role_id": "RL_Data_Scientist"})
        sq = sq.model_copy(update={
            "session_overrides": SessionOverrides(
                assumed_passed_courses=["C-AI301"],
                override_action="accumulate",
            )
        })
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert r.assumptions_active is True


# ── find_best_matching_roles ──────────────────────────────────────────────────

class TestFindBestMatchingRoles:

    def test_happy_path(self):
        orch = _make_orch()
        sess = _make_session(completed=["C-CS101", "C-MA111"])
        sq = _sq("find_best_matching_roles")
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert len(r.data["ranked_roles"]) == 5

    def test_no_completed_courses_informational(self):
        orch = _make_orch()
        sess = _make_session(completed=[])
        sq = _sq("find_best_matching_roles")
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "informational"
        assert r.data.get("reason") == "no_completed_courses"


# ── get_roles_by_track ────────────────────────────────────────────────────────

class TestGetRolesByTrack:

    def test_happy_path_uses_student_track(self):
        orch = _make_orch()
        sess = _make_session(track_id="DSE")
        sq = _sq("get_roles_by_track", student_ref=True)
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert r.data["track_id"] == "DSE"
        assert len(r.data["roles"]) == 3

    def test_explicit_track_id_overrides_student_track(self):
        orch = _make_orch()
        sess = _make_session(track_id="SWE")
        sq = _sq("get_roles_by_track", entities={"track_id": "AI"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        # KG call should use AI track_id
        call_params = orch._kg.call.call_args_list[0][0][1]
        assert call_params.get("track_id") == "AI"

    def test_no_track_no_student_clarification(self):
        orch = _make_orch()
        # Session without student_context
        ctx = StudentContext(
            student_id="S001", name="T", program="P", track_id=None,
            level=1, first_semester="Fall 2021", study_status="Active",
            total_credit_hours_earned=0,
        )
        sess = SessionState(
            session_id=str(uuid.uuid4()),
            student_id="S001",
            session_name="test",
            student_context=ctx,
        )
        sq = _sq("get_roles_by_track")
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "clarification_needed"


# ── recommend_courses_to_close_gap ───────────────────────────────────────────

class TestRecommendCoursesToCloseGap:

    def test_happy_path(self):
        orch = _make_orch()
        sess = _make_session(completed=["C-CS101"])
        sq = _sq("recommend_courses_to_close_gap", entities={"role_id": "RL_Data_Scientist"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert len(r.data["recommended_courses"]) == 2

    def test_no_role_id_clarification(self):
        orch = _make_orch()
        sess = _make_session(completed=["C-CS101"])
        sq = _sq("recommend_courses_to_close_gap")
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "clarification_needed"

    def test_no_completed_courses_informational(self):
        orch = _make_orch()
        sess = _make_session(completed=[])
        sq = _sq("recommend_courses_to_close_gap", entities={"role_id": "RL_Data_Scientist"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "informational"


# ── estimate_alignment_improvement ───────────────────────────────────────────

class TestEstimateAlignmentImprovement:

    def test_happy_path(self):
        orch = _make_orch()
        sess = _make_session(completed=["C-CS101"])
        sess_ctx = sess.student_context.model_copy(update={"in_progress_courses": ["C-AI301"]})
        sess = sess.model_copy(update={"student_context": sess_ctx})
        sq = _sq(
            "estimate_alignment_improvement",
            entities={"role_id": "RL_Data_Scientist"},
            params={"planned_courses": ["C-AI301"]},
        )
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert r.data["current_alignment"] == pytest.approx(0.33)
        assert r.data["new_alignment"] == pytest.approx(0.66)

    def test_no_role_id_clarification(self):
        orch = _make_orch()
        sess = _make_session(completed=["C-CS101"])
        sq = _sq("estimate_alignment_improvement")
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "clarification_needed"

    def test_no_planned_courses_clarification(self):
        orch = _make_orch()
        # Clear in_progress so no planned courses can be inferred
        ctx = _make_session(completed=["C-CS101"]).student_context.model_copy(
            update={"in_progress_courses": []}
        )
        sess = _make_session(completed=["C-CS101"])
        sess = sess.model_copy(update={"student_context": ctx})
        sq = _sq("estimate_alignment_improvement", entities={"role_id": "RL_Data_Scientist"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "clarification_needed"


# ── get_focus_courses_for_target ──────────────────────────────────────────────

class TestGetFocusCoursesForTarget:

    def test_happy_path_by_role(self):
        orch = _make_orch()
        sess = _make_session(completed=["C-CS101"])
        sq = _sq("get_focus_courses_for_target", entities={"role_id": "RL_Data_Scientist"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert len(r.data["focus_courses"]) == 2

    def test_fallback_to_student_track(self):
        """When no role/track in SQ, use student's track_id."""
        orch = _make_orch()
        sess = _make_session(completed=["C-CS101"], track_id="DSE")
        sq = _sq("get_focus_courses_for_target")
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        # KG called with student's track_id
        call_params = orch._kg.call.call_args_list[0][0][1]
        assert call_params.get("target_id") == "DSE"
        assert call_params.get("target_type") == "track"

    def test_non_referential_skips_completed_courses(self):
        """student_referential_fallback=False → completed_courses not sent to KG."""
        orch = _make_orch()
        sess = _make_session(completed=["C-CS101", "C-MA111"])
        sq = _sq("get_focus_courses_for_target",
                 entities={"role_id": "RL_Data_Scientist"}, student_ref=False)
        tw = orch.execute_turn([sq], sess, {})
        call_params = orch._kg.call.call_args_list[0][0][1]
        assert call_params.get("completed_courses") == []


# ── Composer D3 rendering ──────────────────────────────────────────────────────

class TestComposerD3Rendering:

    def _make_result(self, intent, data, status="success") -> PerSQResult:
        return PerSQResult(
            sq_index=0,
            intent=intent,
            status=status,
            data=data,
        )

    def test_compute_skill_gap_renders_missing_skills(self):
        # When skill_gap_count is set, Composer renders the count summary.
        # When missing list only (no count), Composer enumerates skills by name.
        data_no_count = {
            "role_id": "RL_Data_Scientist",
            "role_name": "Data Scientist",
            "missing_skills": [
                {"skill_id": "SK_ML", "name": "Machine Learning"},
                {"skill_id": "SK_Stats", "name": "Statistics"},
            ],
            "covered_skills": [{"skill_id": "SK_Python", "name": "Python"}],
        }
        r = self._make_result("compute_skill_gap", data_no_count)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Data Scientist" in text
        assert "Machine Learning" in text
        assert "Statistics" in text

    def test_find_best_matching_roles_default_no_limit(self):
        r = self._make_result("find_best_matching_roles", _RANKED_ROLES_RESULT)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Software Engineer" in text
        assert "Best matching roles" in text
        assert "85%" in text

    def test_get_roles_by_track_no_limit(self):
        r = self._make_result("get_roles_by_track", _ROLES_BY_TRACK_RESULT)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Data Science and Engineering" in text
        assert "Data Scientist" in text
        # Standard phrasing (not follow-up)
        assert "Roles connected to" in text

    def test_recommend_courses_to_close_gap_renders(self):
        r = self._make_result("recommend_courses_to_close_gap", _RECOMMEND_COURSES_RESULT)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Data Scientist" in text
        assert "Machine Learning" in text

    def test_estimate_alignment_improvement_renders_delta(self):
        r = self._make_result("estimate_alignment_improvement", _ALIGNMENT_IMPROVEMENT_RESULT)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "33%" in text
        assert "66%" in text

    def test_get_focus_courses_for_target_renders(self):
        r = self._make_result("get_focus_courses_for_target", _FOCUS_COURSES_RESULT)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Data Scientist" in text
        assert "Machine Learning" in text

    def test_get_role_profile_renders_skills(self):
        r = self._make_result("get_role_profile", _ROLE_DB["RL_Data_Scientist"])
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Data Scientist" in text
        assert "Machine Learning" in text
        assert "Python" in text

    def test_no_completed_courses_informational_renders(self):
        data = {"reason": "no_completed_courses",
                "message": "No completed courses available for this analysis."}
        r = self._make_result("compute_skill_gap", data, status="informational")
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        # Should not crash; should include the message
        assert len(text) > 0

    def test_compute_alignment_score_renders(self):
        data = {
            "role_id": "RL_Data_Scientist",
            "role_name": "Data Scientist",
            "alignment_score": 0.67,
        }
        r = self._make_result("compute_alignment_score", data)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "67%" in text
        assert "Data Scientist" in text
