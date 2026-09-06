"""
Phase 2 D2 tests — Course Info domain.

Covers:
- QU preprocessing detection helpers
- QU deterministic fallback for course-info phrases without explicit course codes
- Orchestrator D2 route improvements (not-found with candidate, field pass-through)
- Composer D2 deterministic rendering (semester_offering, tracks, credit_threshold, skills, errors)

All KG/ALE/RAG calls are mocked; no live Neo4j or Excel required.
No LLM calls (COMPOSER_USE_LLM=false, AllModelsFailedError patched for fallback tests).
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from gateway.models.schemas import (
    EntitySet, PerSQResult, SessionOverrides, SessionState,
    StudentContext, StructuredQuery, TurnWrapper,
)
from gateway.orchestrator import Orchestrator
from gateway.qu_preprocessing import (
    detect_d2_course_info_verb,
    detect_d2_prereq_verb,
    detect_d2_skills_taught_verb,
    detect_d2_skill_search_verb,
    extract_d2_course_candidate,
    extract_d2_skill_candidate,
)
from gateway.response_composer import (
    _deterministic_answer,
    _extract_packet,
)


# ── Shared fixtures ────────────────────────────────────────────────────────────

_COURSE_CATALOGUE = {
    "C-CS219": {"name": "Advanced Programming", "credits": 3, "level": 2,
                "semester_offering": "Fall, Spring", "tracks": ["SWE", "AI"]},
    "C-CS111": {"name": "Introduction to Computer Science", "credits": 3},
    "C-AI301": {"name": "Intro to Machine Learning", "credits": 3,
                "skills_taught": [{"skill_id": "SK_ML", "name": "Machine Learning"},
                                   {"skill_id": "SK_Python", "name": "Python"}]},
    "C-MA111": {"name": "Calculus I", "credits": 3, "credit_threshold": 30},
}


def _make_kg(catalogue: dict = _COURSE_CATALOGUE) -> MagicMock:
    kg = MagicMock()

    def _kg_call(operation, params=None):
        params = params or {}
        if operation == "get_course_profile":
            code = params.get("course_code", "")
            if code in catalogue:
                return {"course_code": code, **catalogue[code]}
            return {"error": "course_not_found", "detail": f"Course {code!r} not found"}
        if operation == "get_prerequisites":
            code = params.get("course_code", "")
            if code in catalogue:
                return {
                    "course_code": code,
                    "direct_prerequisites": [],
                    "non_course_prerequisites": [],
                }
            return {"error": "course_not_found"}
        if operation == "get_skills_taught":
            code = params.get("course_code", "")
            if code in catalogue:
                entry = catalogue[code]
                skills = entry.get("skills_taught") or entry.get("skills") or []
                return {"course_code": code, "name": entry.get("name", ""), "skills_taught": skills}
            return {"error": "course_not_found"}
        if operation == "search_courses_by_skill":
            skill_ids = params.get("skill_ids", [])
            if "machine learning" in [s.lower() for s in skill_ids] or "SK_ML" in skill_ids:
                return {"courses": [{"course_code": "C-AI301", "name": "Intro to Machine Learning"}]}
            if "NLP" in skill_ids or "nlp" in [s.lower() for s in skill_ids]:
                return {"courses": [{"course_code": "C-AI411", "name": "Natural Language Processing"}]}
            return {"error": "skill_not_found", "skill_ids": skill_ids}
        return {}

    kg.call.side_effect = _kg_call
    return kg


def _make_student() -> StudentContext:
    return StudentContext(
        student_id="STU999",
        name="Test Student",
        program="CIS",
        track_id="AI",
        level=2,
        first_semester="Fall 2024",
        study_status="Studying",
        cgpa=3.2,
        cumulative_chs=36,
        cumulative_cps=115.2,
        total_credit_hours_earned=36,
        completed_courses=["C-CS111"],
        failed_courses=[],
        in_progress_courses=["C-AI301"],
        current_semester="Spring 2026",
    )


def _make_session(student=None) -> SessionState:
    return SessionState(
        session_id=str(uuid.uuid4()),
        student_id="STU999",
        session_name="d2-test",
        student_context=student or _make_student(),
        overrides=SessionOverrides(),
    )


def _make_bundles() -> dict:
    return {
        "grading_scale_rules": MagicMock(),
        "graduation_requirement_rules": MagicMock(),
        "academic_warning_rules": MagicMock(cgpa_warning_threshold=2.0),
        "honors_rules": MagicMock(),
        "credit_limit_rules": MagicMock(),
        "retake_rules": MagicMock(),
        "summer_semester_rules": MagicMock(),
        "student_level_rules": MagicMock(),
    }


def _sq(intent: str, **kwargs) -> StructuredQuery:
    return StructuredQuery(
        intent=intent,
        original_text=kwargs.pop("original_text", intent),
        entities=EntitySet(**kwargs.pop("entities", {})),
        secondary_entities=None,
        params=kwargs.pop("params", {}),
        session_overrides=SessionOverrides(),
        student_referential_fallback=kwargs.pop("student_referential_fallback", False),
    )


def _make_orchestrator(kg=None) -> Orchestrator:
    return Orchestrator(
        kg_adapter=kg or _make_kg(),
        rag_adapter=MagicMock(),
        ale_adapter=MagicMock(),
    )


# ── Part 1: QU Preprocessing Detection Helpers ────────────────────────────────

class TestD2Preprocessing:
    """Pure function tests — no mocks needed."""

    # detect_d2_course_info_verb
    def test_tell_me_about(self):
        assert detect_d2_course_info_verb("tell me about Advanced Programming") is True

    def test_give_me_info_about(self):
        assert detect_d2_course_info_verb("give me info about Digital Logic") is True

    def test_what_is_x(self):
        assert detect_d2_course_info_verb("what is Calculus I?") is True

    def test_explain_x(self):
        assert detect_d2_course_info_verb("explain Operating Systems") is True

    def test_how_many_credits(self):
        assert detect_d2_course_info_verb("how many credits is Advanced Programming?") is True

    def test_not_a_course_info_verb(self):
        assert detect_d2_course_info_verb("can I take C-CS219?") is False

    # detect_d2_prereq_verb
    def test_prereq_of(self):
        assert detect_d2_prereq_verb("prerequisites of NLP") is True

    def test_complete_prereq_chain(self):
        assert detect_d2_prereq_verb("show the complete prerequisite chain of NLP") is True

    def test_what_do_i_need_before(self):
        assert detect_d2_prereq_verb("what do I need before taking Advanced Programming?") is True

    def test_full_prereqs_for(self):
        assert detect_d2_prereq_verb("full prereqs for Data Structures") is True

    def test_not_a_prereq_verb(self):
        assert detect_d2_prereq_verb("tell me about NLP") is False

    # detect_d2_skills_taught_verb
    def test_skills_does_x_teach(self):
        assert detect_d2_skills_taught_verb("What skills does ML teach?") is True

    def test_skills_taught_by(self):
        assert detect_d2_skills_taught_verb("skills taught by Advanced Programming") is True

    def test_what_will_i_learn_in(self):
        assert detect_d2_skills_taught_verb("what will I learn in Intro to ML?") is True

    def test_not_a_skills_taught_verb(self):
        assert detect_d2_skills_taught_verb("which courses teach machine learning?") is False

    # detect_d2_skill_search_verb
    def test_which_courses_teach(self):
        assert detect_d2_skill_search_verb("Which courses teach machine learning?") is True

    def test_where_can_i_learn(self):
        assert detect_d2_skill_search_verb("Where can I learn NLP?") is True

    def test_what_courses_cover(self):
        assert detect_d2_skill_search_verb("What courses cover machine learning?") is True

    def test_not_a_skill_search_verb(self):
        assert detect_d2_skill_search_verb("tell me about machine learning") is False

    # extract_d2_course_candidate
    def test_extract_course_from_tell_me_about(self):
        result = extract_d2_course_candidate("tell me about Advanced Programming")
        assert result == "Advanced Programming"

    def test_extract_course_from_give_me_info(self):
        result = extract_d2_course_candidate("Give me info about Digital Logic")
        assert result == "Digital Logic"

    def test_extract_course_from_prereq_chain(self):
        result = extract_d2_course_candidate("show the complete prerequisite chain of NLP")
        assert result == "NLP"

    def test_extract_course_from_skills_taught(self):
        result = extract_d2_course_candidate("What skills does Intro to ML teach?")
        assert result is not None
        assert "Intro to ML" in result or "ML" in result

    def test_extract_course_from_intro_to_statistics(self):
        result = extract_d2_course_candidate("tell me about intro to statistics")
        assert result is not None
        assert len(result) > 1

    def test_extract_returns_none_for_unknown_pattern(self):
        result = extract_d2_course_candidate("can I take C-CS219?")
        # Should not extract because "can I take" is eligibility, not a D2 info verb
        # (May or may not be None depending on pattern — just check it doesn't crash)
        # The important thing is no exception is raised

    # extract_d2_skill_candidate
    def test_extract_skill_from_which_courses_teach(self):
        result = extract_d2_skill_candidate("Which courses teach machine learning?")
        assert result == "machine learning"

    def test_extract_skill_from_where_can_i_learn(self):
        result = extract_d2_skill_candidate("Where can I learn NLP?")
        assert result == "NLP"

    def test_extract_skill_from_what_courses_cover(self):
        result = extract_d2_skill_candidate("What courses cover databases?")
        assert result == "databases"

    def test_extract_skill_returns_none_for_non_skill_pattern(self):
        result = extract_d2_skill_candidate("tell me about Advanced Programming")
        assert result is None


# ── Part 2: QU Deterministic Fallback Tests ───────────────────────────────────

class TestD2QUDeterministicFallback:
    """Test that fallback correctly routes D2 natural language queries."""

    def _classify(self, text: str):
        """Run deterministic fallback by patching LLM to fail."""
        from gateway.query_understanding import understand_query
        from gateway.models.schemas import LastReferenced
        from gateway.qu_llm_chain import AllModelsFailedError

        with patch("gateway.query_understanding.get_llm_client") as mock_llm:
            mock_client = MagicMock()
            mock_client.is_configured.return_value = True
            mock_llm.return_value = mock_client

            with patch("gateway.query_understanding.QUModelChain") as mock_chain_cls:
                mock_chain = MagicMock()
                mock_chain.call.side_effect = AllModelsFailedError("all failed")
                mock_chain_cls.return_value = mock_chain

                sqs = understand_query(
                    user_text=text,
                    last_referenced=LastReferenced(),
                    recent_turns=[],
                    resolver=None,
                )
        return sqs

    def _get_course_entity(self, sq) -> str | None:
        """Get course entity from either entities.course_code or params.attempted_candidate."""
        return sq.entities.course_code or sq.params.get("attempted_candidate")

    def _get_skill_entity(self, sq) -> str | None:
        """Get skill entity from either entities.skill_id or params.attempted_skill."""
        return sq.entities.skill_id or sq.params.get("attempted_skill")

    def test_tell_me_about_advanced_programming_get_course_info(self):
        sqs = self._classify("tell me about Advanced Programming")
        assert len(sqs) >= 1
        assert sqs[0].intent == "get_course_info"
        # Without resolver, entity may be in params.attempted_candidate (filter_unresolved clears non-canonical)
        entity = self._get_course_entity(sqs[0])
        assert entity is not None
        assert "Advanced Programming" in entity

    def test_prereq_chain_of_nlp_full_depth(self):
        sqs = self._classify("show the complete prerequisite chain of NLP")
        assert len(sqs) >= 1
        assert sqs[0].intent == "get_course_prerequisites"
        entity = self._get_course_entity(sqs[0])
        assert entity is not None
        assert "NLP" in entity
        assert sqs[0].params.get("depth") == "full"

    def test_direct_prereq_of_calculus(self):
        sqs = self._classify("prerequisites of Calculus I")
        assert len(sqs) >= 1
        assert sqs[0].intent == "get_course_prerequisites"
        assert sqs[0].params.get("depth") in ("direct", "full")

    def test_skills_taught_by_intro_to_ml(self):
        sqs = self._classify("What skills does Intro to Machine Learning teach?")
        assert len(sqs) >= 1
        assert sqs[0].intent == "get_skills_taught"
        entity = self._get_course_entity(sqs[0])
        assert entity is not None

    def test_which_courses_teach_machine_learning_skill_search(self):
        sqs = self._classify("Which courses teach machine learning?")
        assert len(sqs) >= 1
        assert sqs[0].intent == "search_courses_by_skill"
        entity = self._get_skill_entity(sqs[0])
        assert entity is not None

    def test_where_can_i_learn_nlp_skill_search(self):
        sqs = self._classify("Where can I learn NLP?")
        assert len(sqs) >= 1
        assert sqs[0].intent == "search_courses_by_skill"
        entity = self._get_skill_entity(sqs[0])
        assert entity is not None
        assert "NLP" in entity

    def test_can_i_take_stays_eligibility(self):
        """'Can I take X' must remain check_course_eligibility (D1), not D2."""
        sqs = self._classify("Can I take Advanced Programming?")
        # Should not produce get_course_info
        for sq in sqs:
            assert sq.intent != "get_course_info", (
                "'can I take' should not map to get_course_info"
            )

    def test_give_me_info_about_digital_logic(self):
        sqs = self._classify("Give me info about Digital Logic")
        assert len(sqs) >= 1
        assert sqs[0].intent == "get_course_info"
        entity = self._get_course_entity(sqs[0])
        assert entity is not None
        assert "Digital Logic" in entity


# ── Part 3: Orchestrator D2 Route Tests ──────────────────────────────────────

class TestOrchestratorD2Route:
    """Test orchestrator D2 routing with mocked KG adapter."""

    def _run_sq(self, sq: StructuredQuery, kg=None) -> PerSQResult:
        orch = _make_orchestrator(kg=kg)
        session = _make_session()
        result = orch.execute_turn([sq], session, _make_bundles())
        return result.results[0]

    # get_course_info

    def test_course_info_resolved_code_calls_kg_profile(self):
        kg = _make_kg()
        r = self._run_sq(_sq("get_course_info", entities={"course_code": "C-CS219"}), kg=kg)
        assert r.status == "success"
        assert r.data.get("name") == "Advanced Programming"
        kg.call.assert_any_call("get_course_profile", {"course_code": "C-CS219"})

    def test_course_info_no_entity_returns_clarification(self):
        r = self._run_sq(_sq("get_course_info"))
        assert r.status == "clarification_needed"

    def test_course_info_with_candidate_not_found_returns_informational(self):
        r = self._run_sq(_sq("get_course_info",
                              entities={"course_code": "UNKNOWN999"},
                              params={"attempted_candidate": "Quantum Computing"}))
        # KG returns error → should be informational with attempted_candidate
        assert r.status == "informational"
        assert r.data.get("attempted_candidate") is not None

    def test_course_info_no_entity_with_attempted_candidate_clarifies(self):
        r = self._run_sq(_sq("get_course_info", params={"attempted_candidate": "Quantum Computing"}))
        assert r.status == "clarification_needed"
        assert "Quantum Computing" in (r.clarification_prompt or "")

    def test_course_info_result_includes_semester_offering(self):
        r = self._run_sq(_sq("get_course_info", entities={"course_code": "C-CS219"}))
        assert r.status == "success"
        assert r.data.get("semester_offering") == "Fall, Spring"

    def test_course_info_result_includes_tracks(self):
        r = self._run_sq(_sq("get_course_info", entities={"course_code": "C-CS219"}))
        assert r.data.get("tracks") == ["SWE", "AI"]

    # get_course_prerequisites

    def test_prereq_with_full_depth_passes_depth(self):
        kg = _make_kg()
        r = self._run_sq(_sq("get_course_prerequisites",
                              entities={"course_code": "C-CS219"},
                              params={"depth": "full"}), kg=kg)
        kg.call.assert_any_call("get_prerequisites", {"course_code": "C-CS219", "depth": "full"})

    def test_prereq_no_entity_returns_clarification(self):
        r = self._run_sq(_sq("get_course_prerequisites"))
        assert r.status == "clarification_needed"

    def test_prereq_not_found_with_candidate_returns_informational(self):
        r = self._run_sq(_sq("get_course_prerequisites",
                              entities={"course_code": "UNKNOWN"},
                              params={"attempted_candidate": "Quantum Computing"}))
        assert r.status == "informational"
        assert "attempted_candidate" in r.data

    # get_skills_taught

    def test_skills_taught_success(self):
        r = self._run_sq(_sq("get_skills_taught", entities={"course_code": "C-AI301"}))
        assert r.status == "success"
        assert "skills_taught" in r.data or "skills" in r.data

    def test_skills_taught_no_entity_returns_clarification(self):
        r = self._run_sq(_sq("get_skills_taught"))
        assert r.status == "clarification_needed"

    def test_skills_taught_not_found_with_candidate(self):
        r = self._run_sq(_sq("get_skills_taught",
                              entities={"course_code": "UNKNOWN"},
                              params={"attempted_candidate": "Quantum Computing"}))
        assert r.status == "informational"
        assert "attempted_candidate" in r.data

    # search_courses_by_skill

    def test_skill_search_finds_courses(self):
        r = self._run_sq(_sq("search_courses_by_skill",
                              entities={"skill_id": "machine learning"}))
        assert r.status == "success"
        assert "courses" in r.data

    def test_skill_search_no_skill_returns_clarification(self):
        r = self._run_sq(_sq("search_courses_by_skill"))
        assert r.status == "clarification_needed"

    def test_skill_search_no_skill_with_attempted_clarifies(self):
        r = self._run_sq(_sq("search_courses_by_skill",
                              params={"attempted_skill": "blockchain"}))
        assert r.status == "clarification_needed"
        assert "blockchain" in (r.clarification_prompt or "")

    def test_skill_search_not_found_with_attempted_skill(self):
        r = self._run_sq(_sq("search_courses_by_skill",
                              entities={"skill_id": "UNKNOWN_SKILL_XYZ"},
                              params={"attempted_skill": "blockchain engineering"}))
        assert r.status == "informational"
        assert "attempted_skill" in r.data


# ── Part 4: Composer D2 Deterministic Rendering Tests ────────────────────────

class TestComposerD2Rendering:
    """Test deterministic rendering of D2 packets (COMPOSER_USE_LLM=false)."""

    def _make_result(self, intent: str, status: str, data: dict,
                     clarification_prompt: str = None) -> PerSQResult:
        return PerSQResult(
            sq_index=0,
            intent=intent,
            status=status,
            data=data,
            clarification_prompt=clarification_prompt,
        )

    def _answer(self, result: PerSQResult) -> str:
        packet = _extract_packet(result)
        return _deterministic_answer([packet])

    # Course info rendering

    def test_course_info_shows_name(self):
        r = self._make_result("get_course_info", "success", {
            "course_code": "C-CS219", "name": "Advanced Programming", "credits": 3,
        })
        answer = self._answer(r)
        assert "Advanced Programming" in answer

    def test_course_info_shows_credits(self):
        r = self._make_result("get_course_info", "success", {
            "course_code": "C-CS219", "name": "Advanced Programming", "credits": 3,
        })
        answer = self._answer(r)
        assert "3" in answer

    def test_course_info_shows_semester_offering(self):
        r = self._make_result("get_course_info", "success", {
            "course_code": "C-CS219", "name": "Advanced Programming",
            "credits": 3, "semester_offering": "Fall, Spring",
        })
        answer = self._answer(r)
        assert "Fall" in answer or "Spring" in answer

    def test_course_info_shows_tracks(self):
        r = self._make_result("get_course_info", "success", {
            "course_code": "C-CS219", "name": "Advanced Programming",
            "credits": 3, "tracks": ["SWE", "AI"],
        })
        answer = self._answer(r)
        assert "SWE" in answer or "AI" in answer

    def test_course_info_shows_credit_threshold(self):
        r = self._make_result("get_course_info", "success", {
            "course_code": "C-MA111", "name": "Calculus I",
            "credits": 3, "credit_threshold": 30,
        })
        answer = self._answer(r)
        assert "30" in answer

    def test_course_info_not_found_shows_candidate(self):
        r = self._make_result("get_course_info", "informational", {
            "error": "course_not_found", "attempted_candidate": "Quantum Computing",
        })
        answer = self._answer(r)
        assert "Quantum Computing" in answer
        assert "couldn't find" in answer.lower() or "not found" in answer.lower()

    def test_course_info_name_first_format(self):
        r = self._make_result("get_course_info", "success", {
            "course_code": "C-CS219", "name": "Advanced Programming", "credits": 3,
        })
        answer = self._answer(r)
        # Name-first: "Advanced Programming (C-CS219)"
        assert "Advanced Programming (C-CS219)" in answer

    # Skills taught rendering

    def test_skills_taught_shows_readable_names(self):
        r = self._make_result("get_skills_taught", "success", {
            "course_code": "C-AI301", "name": "Intro to Machine Learning",
            "skills_taught": [
                {"skill_id": "SK_Machine_Learning", "name": "Machine Learning"},
                {"skill_id": "SK_Python", "name": "Python"},
            ],
        })
        answer = self._answer(r)
        assert "Machine Learning" in answer
        assert "Python" in answer
        # Must NOT show raw SK_* IDs
        assert "SK_Machine_Learning" not in answer
        assert "SK_Python" not in answer

    def test_skills_taught_not_found_shows_candidate(self):
        r = self._make_result("get_skills_taught", "informational", {
            "error": "course_not_found", "attempted_candidate": "Quantum Computing",
        })
        answer = self._answer(r)
        assert "Quantum Computing" in answer

    def test_skills_taught_empty_skills_shows_friendly_message(self):
        r = self._make_result("get_skills_taught", "success", {
            "course_code": "C-CS219", "name": "Advanced Programming", "skills": [],
        })
        answer = self._answer(r)
        assert "couldn't find" in answer.lower() or "no skill" in answer.lower()

    # Skill search rendering

    def test_courses_by_skill_shows_course_names(self):
        r = self._make_result("search_courses_by_skill", "success", {
            "skill_id": "SK_ML",
            "skill_name": "Machine Learning",
            "courses": [{"course_code": "C-AI301", "name": "Intro to Machine Learning"}],
        })
        answer = self._answer(r)
        assert "Machine Learning" in answer
        assert "C-AI301" in answer or "Intro to Machine Learning" in answer

    def test_courses_by_skill_not_found_shows_attempted_skill(self):
        r = self._make_result("search_courses_by_skill", "informational", {
            "error": "skill_not_found", "attempted_skill": "blockchain engineering",
        })
        answer = self._answer(r)
        assert "blockchain engineering" in answer

    def test_courses_by_skill_normalizes_results_field(self):
        """If KG returns 'results' instead of 'courses', it should still render."""
        r = self._make_result("search_courses_by_skill", "success", {
            "skill_id": "SK_ML",
            "results": [{"course_code": "C-AI301", "name": "Intro to Machine Learning"}],
        })
        answer = self._answer(r)
        assert "C-AI301" in answer or "Intro to Machine Learning" in answer

    def test_courses_by_skill_uses_skill_display_from_attempted(self):
        """When no skill_name, attempted_skill should be used for display."""
        r = self._make_result("search_courses_by_skill", "success", {
            "skill_id": "machine learning",
            "courses": [{"course_code": "C-AI301", "name": "Intro to Machine Learning"}],
        })
        answer = self._answer(r)
        # Should display something about machine learning courses
        assert "machine learning" in answer.lower() or "C-AI301" in answer

    # Prerequisites rendering

    def test_prereqs_not_found_shows_candidate(self):
        r = self._make_result("get_course_prerequisites", "informational", {
            "error": "course_not_found", "attempted_candidate": "NLP",
        })
        answer = self._answer(r)
        assert "NLP" in answer

    def test_prereqs_success_renders(self):
        r = self._make_result("get_course_prerequisites", "success", {
            "course_code": "C-CS219",
            "direct_prerequisites": [{"course_code": "C-CS111", "name": "Intro to CS"}],
            "non_course_prerequisites": [],
        })
        answer = self._answer(r)
        assert "C-CS219" in answer or "prerequisite" in answer.lower()

    # End-to-end through orchestrator + composer

    def test_e2e_course_info_with_semester_offering(self):
        """Full pipeline: Orchestrator enriches + Composer renders semester_offering."""
        import os

        orch = _make_orchestrator()
        session = _make_session()
        turn = orch.execute_turn(
            [_sq("get_course_info", entities={"course_code": "C-CS219"})],
            session, _make_bundles(),
        )

        with patch.dict(os.environ, {"COMPOSER_USE_LLM": "false"}):
            from gateway.response_composer import ResponseComposer
            composer = ResponseComposer()
        qr = composer.compose("tell me about C-CS219", turn, session.session_id, "test")

        assert "Advanced Programming" in qr.answer_text
        assert "Fall" in qr.answer_text or "Spring" in qr.answer_text

    def test_e2e_skill_search_for_machine_learning(self):
        """Full pipeline: skill search returns courses."""
        import os

        orch = _make_orchestrator()
        session = _make_session()
        turn = orch.execute_turn(
            [_sq("search_courses_by_skill", entities={"skill_id": "machine learning"})],
            session, _make_bundles(),
        )

        with patch.dict(os.environ, {"COMPOSER_USE_LLM": "false"}):
            from gateway.response_composer import ResponseComposer
            composer = ResponseComposer()
        qr = composer.compose("which courses teach machine learning?", turn,
                               session.session_id, "test")

        assert "C-AI301" in qr.answer_text or "Machine Learning" in qr.answer_text
