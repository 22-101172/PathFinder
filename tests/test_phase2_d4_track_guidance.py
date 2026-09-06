"""
Phase 2 D4 tests — Track Guidance domain.

Covers:
- Orchestrator D4 dispatch: get_track_overview, compare_tracks,
  recommend_track_for_role, recommend_track_for_skill
- Conditional student-referential fallback (student track used when no explicit track given)
- compare_tracks: two-track requirement, identical-track guard, secondary_entities handling
- Unsupported track guard
- Composer deterministic rendering for all D4 intents

All KG calls are mocked. No live Neo4j or Excel. No LLM calls.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
import os
os.environ.setdefault("COMPOSER_USE_LLM", "false")

from gateway.models.schemas import (
    EntitySet, PerSQResult, SessionOverrides, SessionState,
    StudentContext, StructuredQuery, TurnWrapper,
)
from gateway.orchestrator import Orchestrator
from gateway.response_composer import _deterministic_answer, _extract_packet


# ── Shared KG data ─────────────────────────────────────────────────────────────

_TRACK_OVERVIEW_DSE = {
    "track_id": "DSE",
    "name": "Data Science and Engineering",
    "description": "Focuses on data analysis, machine learning, and engineering.",
    "courses": [
        {"course_code": "C-DS101", "name": "Intro to Data Science"},
        {"course_code": "C-AI301", "name": "Intro to Machine Learning"},
    ],
}

_TRACK_OVERVIEW_AI = {
    "track_id": "AI",
    "name": "Artificial Intelligence",
    "description": "Focuses on AI systems, deep learning, and reasoning.",
    "courses": [
        {"course_code": "C-AI301", "name": "Intro to Machine Learning"},
        {"course_code": "C-AI401", "name": "Deep Learning"},
    ],
}

_COMPARE_TRACKS_RESULT = {
    "track_id_1": "DSE",
    "track_id_2": "AI",
    "track_1_name": "Data Science and Engineering",
    "track_2_name": "Artificial Intelligence",
    "shared_courses": [{"course_code": "C-AI301", "name": "Intro to Machine Learning"}],
    "different_courses": {
        "DSE": [{"course_code": "C-DS101", "name": "Intro to Data Science"}],
        "AI": [{"course_code": "C-AI401", "name": "Deep Learning"}],
    },
}

_RECOMMEND_FOR_ROLE_RESULT = {
    "recommended_track": [
        {"track_id": "DSE", "name": "Data Science and Engineering"},
        {"track_id": "AI", "name": "Artificial Intelligence"},
    ],
}

_RECOMMEND_FOR_SKILL_RESULT = {
    "recommended_track": [
        {"track_id": "AI", "name": "Artificial Intelligence"},
    ],
}


def _make_kg() -> MagicMock:
    kg = MagicMock()

    _track_db = {"DSE": _TRACK_OVERVIEW_DSE, "AI": _TRACK_OVERVIEW_AI}

    def _kg_call(operation, params=None):
        params = params or {}
        if operation == "get_track_overview":
            track = params.get("track_id", "")
            if track in _track_db:
                return _track_db[track]
            return {"error": "track_not_found"}
        if operation == "compare_tracks":
            t1 = params.get("track_id_1", "")
            t2 = params.get("track_id_2", "")
            if t1 not in _track_db or t2 not in _track_db:
                return {"error": "track_not_found"}
            return _COMPARE_TRACKS_RESULT
        if operation == "recommend_track_for_role":
            return _RECOMMEND_FOR_ROLE_RESULT
        if operation == "recommend_track_for_skill":
            return _RECOMMEND_FOR_SKILL_RESULT
        return {"error": "unknown_operation"}

    kg.call.side_effect = _kg_call
    return kg


def _make_session(
    track_id: str = "DSE",
    track_status: str = "supported",
) -> SessionState:
    ctx = StudentContext(
        student_id="S001",
        name="Test Student",
        program="Computer Science",
        track_id=track_id,
        track_status=track_status,
        track_error_code="unsupported_track" if track_status == "unsupported" else None,
        level=3,
        first_semester="Fall 2021",
        study_status="Active",
        cgpa=2.8,
        total_credit_hours_earned=90,
        completed_courses=["C-CS101", "C-MA111"],
        in_progress_courses=[],
        current_semester="Spring 2024",
    )
    return SessionState(
        session_id=str(uuid.uuid4()),
        student_id="S001",
        session_name="test",
        student_context=ctx,
    )


def _sq(intent: str, entities: dict = None, secondary_entities: dict = None,
        params: dict = None, student_ref: bool = False) -> StructuredQuery:
    sq = StructuredQuery(
        intent=intent,
        entities=EntitySet(**(entities or {})),
        params=params or {},
        student_referential_fallback=student_ref,
    )
    if secondary_entities is not None:
        sq = sq.model_copy(update={"secondary_entities": EntitySet(**secondary_entities)})
    return sq


def _make_orch() -> Orchestrator:
    kg = _make_kg()
    rag = MagicMock()
    ale = MagicMock()
    return Orchestrator(kg, rag, ale)


# ── get_track_overview ────────────────────────────────────────────────────────

class TestGetTrackOverview:

    def test_explicit_track_id(self):
        orch = _make_orch()
        sess = _make_session(track_id="SWE")
        sq = _sq("get_track_overview", entities={"track_id": "DSE"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert r.data["track_id"] == "DSE"
        # KG called with DSE, not SWE
        call_params = orch._kg.call.call_args_list[0][0][1]
        assert call_params["track_id"] == "DSE"

    def test_student_referential_fallback_uses_student_track(self):
        orch = _make_orch()
        sess = _make_session(track_id="DSE")
        sq = _sq("get_track_overview", student_ref=True)
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        call_params = orch._kg.call.call_args_list[0][0][1]
        assert call_params["track_id"] == "DSE"

    def test_no_track_no_referential_fallback_returns_clarification(self):
        """Without student_referential_fallback and no entity, must ask for track."""
        orch = _make_orch()
        sess = _make_session(track_id="DSE")
        sq = _sq("get_track_overview", student_ref=False)  # explicit: NOT referential
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "clarification_needed"
        assert "track" in r.clarification_prompt.lower()

    def test_unsupported_track_returns_not_applicable(self):
        orch = _make_orch()
        sess = _make_session(track_id="UNKNOWN", track_status="unsupported")
        sq = _sq("get_track_overview", student_ref=True)
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "informational"
        assert r.data.get("reason_code") == "unsupported_track"

    def test_explicit_track_ignores_unsupported_student_track(self):
        """Explicit track_id in entities bypasses unsupported-track guard."""
        orch = _make_orch()
        sess = _make_session(track_id="UNKNOWN", track_status="unsupported")
        sq = _sq("get_track_overview", entities={"track_id": "AI"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert r.data["track_id"] == "AI"

    def test_unknown_track_returns_informational(self):
        orch = _make_orch()
        sess = _make_session()
        sq = _sq("get_track_overview", entities={"track_id": "NOPE"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "informational"
        assert "error" in r.data


# ── compare_tracks ────────────────────────────────────────────────────────────

class TestCompareTracks:

    def test_happy_path_two_explicit_tracks(self):
        """Primary track in entities, second in secondary_entities."""
        orch = _make_orch()
        sess = _make_session()
        sq = _sq(
            "compare_tracks",
            entities={"track_id": "DSE"},
            secondary_entities={"track_id": "AI"},
        )
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert r.data["track_id_1"] == "DSE"
        assert r.data["track_id_2"] == "AI"
        assert len(r.data["shared_courses"]) == 1

    def test_student_fallback_for_first_track(self):
        """No explicit track → use student's track as track1."""
        orch = _make_orch()
        sess = _make_session(track_id="DSE")
        sq = _sq(
            "compare_tracks",
            secondary_entities={"track_id": "AI"},
            student_ref=True,
        )
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        call_params = orch._kg.call.call_args_list[0][0][1]
        assert call_params["track_id_1"] == "DSE"
        assert call_params["track_id_2"] == "AI"

    def test_missing_second_track_returns_clarification(self):
        """Only one track provided → must ask for the second."""
        orch = _make_orch()
        sess = _make_session(track_id="DSE")
        sq = _sq("compare_tracks", entities={"track_id": "DSE"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "clarification_needed"
        assert "two tracks" in r.clarification_prompt.lower()

    def test_missing_both_tracks_returns_clarification(self):
        orch = _make_orch()
        # No explicit track, no student referential — can't infer first track
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
        sq = _sq("compare_tracks")
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "clarification_needed"

    def test_identical_tracks_returns_informational(self):
        orch = _make_orch()
        sess = _make_session(track_id="DSE")
        sq = _sq(
            "compare_tracks",
            entities={"track_id": "DSE"},
            secondary_entities={"track_id": "DSE"},
        )
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "informational"
        assert r.data.get("error") == "identical_tracks_provided"

    def test_unsupported_student_track_blocked(self):
        """Unsupported student track without explicit track1 → not_applicable."""
        orch = _make_orch()
        sess = _make_session(track_id="UNKNOWN", track_status="unsupported")
        sq = _sq(
            "compare_tracks",
            secondary_entities={"track_id": "AI"},
            student_ref=True,
        )
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "informational"
        assert r.data.get("reason_code") == "unsupported_track"


# ── recommend_track_for_role ──────────────────────────────────────────────────

class TestRecommendTrackForRole:

    def test_happy_path(self):
        orch = _make_orch()
        sess = _make_session()
        sq = _sq("recommend_track_for_role", entities={"role_id": "RL_Data_Scientist"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert isinstance(r.data["recommended_track"], list)
        assert len(r.data["recommended_track"]) == 2

    def test_no_role_id_returns_clarification(self):
        orch = _make_orch()
        sess = _make_session()
        sq = _sq("recommend_track_for_role")
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "clarification_needed"
        assert "role" in r.clarification_prompt.lower()

    def test_no_student_context_still_works(self):
        """recommend_track_for_role is not student-aware — works without student."""
        orch = _make_orch()
        sess = _make_session()
        sq = _sq("recommend_track_for_role", entities={"role_id": "RL_SWE"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        # Only KG is called; student context is not passed
        assert r.status == "success"


# ── recommend_track_for_skill ─────────────────────────────────────────────────

class TestRecommendTrackForSkill:

    def test_happy_path(self):
        orch = _make_orch()
        sess = _make_session()
        sq = _sq("recommend_track_for_skill", entities={"skill_id": "SK_ML"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "success"
        assert isinstance(r.data["recommended_track"], list)
        assert r.data["recommended_track"][0]["track_id"] == "AI"

    def test_no_skill_id_returns_clarification(self):
        orch = _make_orch()
        sess = _make_session()
        sq = _sq("recommend_track_for_skill")
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "clarification_needed"
        assert "skill" in r.clarification_prompt.lower()

    def test_kg_error_returns_engine_error(self):
        orch = _make_orch()
        # side_effect takes priority over return_value; clear it first
        orch._kg.call.side_effect = None
        orch._kg.call.return_value = {"error": "kg_unavailable"}
        sess = _make_session()
        sq = _sq("recommend_track_for_skill", entities={"skill_id": "SK_ML"})
        tw = orch.execute_turn([sq], sess, {})
        r = tw.results[0]
        assert r.status == "error"
        assert r.error_code == "engine_error"


# ── Composer D4 rendering ─────────────────────────────────────────────────────

class TestComposerD4Rendering:

    def _make_result(self, intent, data, status="success") -> PerSQResult:
        return PerSQResult(
            sq_index=0,
            intent=intent,
            status=status,
            data=data,
        )

    def test_get_track_overview_renders(self):
        r = self._make_result("get_track_overview", _TRACK_OVERVIEW_DSE)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Data Science and Engineering" in text
        assert "DSE" in text

    def test_get_track_overview_renders_description(self):
        r = self._make_result("get_track_overview", _TRACK_OVERVIEW_DSE)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "data analysis" in text.lower()

    def test_compare_tracks_renders_both_tracks(self):
        r = self._make_result("compare_tracks", _COMPARE_TRACKS_RESULT)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Data Science and Engineering" in text
        assert "Artificial Intelligence" in text
        assert "Comparing" in text

    def test_compare_tracks_shows_shared_count(self):
        r = self._make_result("compare_tracks", _COMPARE_TRACKS_RESULT)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Shared" in text or "shared" in text
        assert "1" in text

    def test_compare_tracks_shows_unique_counts(self):
        r = self._make_result("compare_tracks", _COMPARE_TRACKS_RESULT)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Unique" in text or "unique" in text

    def test_recommend_for_role_renders_ranked_list(self):
        r = self._make_result("recommend_track_for_role", _RECOMMEND_FOR_ROLE_RESULT)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Recommended tracks" in text
        assert "Data Science and Engineering" in text

    def test_recommend_for_skill_renders_single(self):
        r = self._make_result("recommend_track_for_skill", _RECOMMEND_FOR_SKILL_RESULT)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Artificial Intelligence" in text

    def test_identical_tracks_informational_renders(self):
        data = {
            "error": "identical_tracks_provided",
            "message": "Please specify two different tracks to compare.",
        }
        r = self._make_result("compare_tracks", data, status="informational")
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert len(text) > 0

    def test_clarification_needed_renders_prompt(self):
        r = PerSQResult(
            sq_index=0,
            intent="compare_tracks",
            status="clarification_needed",
            clarification_prompt="Which two tracks would you like to compare?",
        )
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Which two tracks" in text

    def test_recommend_for_role_empty_list_renders_fallback(self):
        r = self._make_result("recommend_track_for_role", {"recommended_track": []})
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "No track recommendation" in text

    def test_get_track_overview_with_courses(self):
        r = self._make_result("get_track_overview", _TRACK_OVERVIEW_AI)
        packet = _extract_packet(r)
        text = _deterministic_answer([packet])
        assert "Artificial Intelligence" in text
        # Should show course count
        assert "Courses" in text or "courses" in text


# ── Multi-SQ D4 turn ──────────────────────────────────────────────────────────

class TestD4MultiSQTurn:

    def test_two_track_overviews_in_one_turn(self):
        """Two get_track_overview SQs in one turn → both results returned."""
        orch = _make_orch()
        sess = _make_session()
        sq1 = _sq("get_track_overview", entities={"track_id": "DSE"})
        sq2 = _sq("get_track_overview", entities={"track_id": "AI"})
        tw = orch.execute_turn([sq1, sq2], sess, {})
        assert len(tw.results) == 2
        assert tw.results[0].status == "success"
        assert tw.results[1].status == "success"
        assert tw.results[0].data["track_id"] == "DSE"
        assert tw.results[1].data["track_id"] == "AI"

    def test_compare_plus_recommend_in_one_turn(self):
        """compare_tracks + recommend_track_for_role in same turn."""
        orch = _make_orch()
        sess = _make_session()
        sq1 = _sq("compare_tracks",
                   entities={"track_id": "DSE"},
                   secondary_entities={"track_id": "AI"})
        sq2 = _sq("recommend_track_for_role", entities={"role_id": "RL_Data_Scientist"})
        tw = orch.execute_turn([sq1, sq2], sess, {})
        assert len(tw.results) == 2
        assert all(r.status == "success" for r in tw.results)
        assert tw.turn_status == "completed"
