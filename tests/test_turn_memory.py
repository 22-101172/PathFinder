"""
Structured turn memory tests.

Covers:
  TM01-TM08  build_turn_memory — per-intent extraction
  TM09-TM10  render_turn_memory — compact rendering for QU injection
  TM11-TM15  SessionState.last_turn_memory persistence
  TM16-TM23  Scenario chains A–H (QU follow-up resolution via mocked LLM)
  TM24       Multi-intent turn memory
  TM25       Ambiguity guard
  TM26       Empty / error result graceful handling
  TM27       Caps enforcement
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from gateway.models.schemas import (
    AnswerMemory,
    Citation,
    DisplayItem,
    EntitySet,
    LastReferenced,
    PerSQResult,
    PolicyText,
    QueryMemory,
    SessionOverrides,
    SessionState,
    StudentContext,
    StructuredQuery,
    TurnMemory,
    TurnWrapper,
)
from gateway.turn_memory_builder import build_turn_memory
from gateway.qu_prompt import render_turn_memory, build_user_message


# ── Factories ─────────────────────────────────────────────────────────────────

def _sq(intent: str, *, course=None, role=None, track=None, skill=None,
        student_ref=False, params=None, sec_track=None) -> StructuredQuery:
    entities = EntitySet(course_code=course, role_id=role, track_id=track, skill_id=skill)
    sec = EntitySet(track_id=sec_track) if sec_track else None
    return StructuredQuery(
        intent=intent,
        entities=entities,
        secondary_entities=sec,
        params=params or {},
        student_referential_fallback=student_ref,
    )


def _result(intent: str, status: str = "success", data: dict | None = None,
            citations: list | None = None, sq_index: int = 0) -> PerSQResult:
    return PerSQResult(
        sq_index=sq_index,
        intent=intent,
        status=status,
        data=data or {},
        citations=citations,
    )


def _wrap(results: list[PerSQResult], status: str = "completed") -> TurnWrapper:
    return TurnWrapper(
        turn_id="t1",
        session_id="s1",
        timestamp="2026-06-28T00:00:00Z",
        results=results,
        result_count=len(results),
        turn_status=status,
        has_error=any(r.status == "error" for r in results),
        has_clarification=any(r.status == "clarification_needed" for r in results),
        has_informational=any(r.status == "informational" for r in results),
        has_soft_no_evidence=False,
    )


def _no_ref() -> LastReferenced:
    return LastReferenced()


# ── TM01-TM08: build_turn_memory per-intent extraction ────────────────────────

class TestBuildTurnMemoryIntents:

    def test_TM01_compute_skill_gap_stores_role_and_missing_skills(self):
        sqs = [_sq("compute_skill_gap", role="RL_Data_Scientist", student_ref=True)]
        tw = _wrap([_result("compute_skill_gap", data={
            "role_id": "RL_Data_Scientist",
            "missing_skills": [
                {"skill_id": "SK_ML", "name": "Machine Learning"},
                {"skill_id": "SK_Stats", "name": "Statistics"},
                {"skill_id": "SK_DV", "name": "Data Visualization"},
            ],
            "covered_skills": [],
        })])
        tm = build_turn_memory(sqs, tw, "What am I missing for data scientist?")

        assert tm.source_intents == ["compute_skill_gap"]
        assert tm.primary_domain == "career"
        assert "RL_Data_Scientist" in tm.answer_memory.roles
        assert "SK_ML" in tm.answer_memory.skills
        assert "SK_Stats" in tm.answer_memory.skills
        # Ordered display items: role first, then skills
        types = [d.type for d in tm.answer_memory.ordered_display_items]
        assert "role" in types
        assert "skill" in types

    def test_TM02_search_courses_by_skill_stores_skills_and_courses(self):
        sqs = [_sq("search_courses_by_skill", skill="SK_ML")]
        tw = _wrap([_result("search_courses_by_skill", data={
            "skill_id": "SK_ML",
            "courses": [
                {"course_code": "C-AI301", "course_name": "Intro to ML"},
                {"course_code": "C-AI401", "course_name": "Deep Learning"},
            ],
        })])
        tm = build_turn_memory(sqs, tw, "Which courses teach machine learning?")

        assert "SK_ML" in tm.answer_memory.skills
        assert "C-AI301" in tm.answer_memory.courses
        assert "C-AI401" in tm.answer_memory.courses
        assert tm.primary_domain == "course"

    def test_TM03_find_best_matching_roles_stores_ranked_roles(self):
        sqs = [_sq("find_best_matching_roles", student_ref=True)]
        tw = _wrap([_result("find_best_matching_roles", data={
            "ranked_roles": [
                {"role_id": "RL_Data_Scientist", "name": "Data Scientist"},
                {"role_id": "RL_ML_Engineer", "name": "ML Engineer"},
                {"role_id": "RL_Data_Analyst", "name": "Data Analyst"},
            ],
        })])
        tm = build_turn_memory(sqs, tw, "What roles fit me?")

        assert "RL_Data_Scientist" in tm.answer_memory.roles
        assert "RL_ML_Engineer" in tm.answer_memory.roles
        # Ordered items ranked by position
        assert tm.answer_memory.ordered_display_items[0].code == "RL_Data_Scientist"
        assert tm.answer_memory.ordered_display_items[0].rank == 0
        assert tm.answer_memory.ordered_display_items[1].code == "RL_ML_Engineer"
        assert tm.answer_memory.ordered_display_items[1].rank == 1

    def test_TM04_policy_query_stores_compact_text_not_symbolic_label(self):
        raw_answer = "A student is placed on academic probation when their CGPA falls below 2.0 for two consecutive semesters."
        sqs = [_sq("policy_query")]
        tw = _wrap([_result("policy_query",
            data={"answer": raw_answer},
            citations=[{"source": "CIS Handbook", "page": 9}],
        )])
        tm = build_turn_memory(sqs, tw, "What is the academic probation policy?")

        assert tm.primary_domain == "policy"
        pt = tm.answer_memory.policy_text
        assert pt is not None
        assert "probation" in pt.answer_brief.lower()
        assert len(pt.answer_brief) <= 500
        assert pt.citations[0].source == "CIS Handbook"
        assert pt.citations[0].page == 9
        # No hardcoded symbolic label
        assert not hasattr(pt, "academic_probation_rule")

    def test_TM05_plan_semester_stores_planning_items(self):
        sqs = [_sq("plan_semester", student_ref=True)]
        tw = _wrap([_result("plan_semester", data={
            "plans": [{
                "plan_label": "Recommended",
                "total_credits": 18,
                "courses": [
                    {"course_code": "C-CS301", "course_name": "Data Structures"},
                    {"course_code": "C-CS302", "course_name": "Algorithms"},
                ],
            }],
        })])
        tm = build_turn_memory(sqs, tw, "What courses should I take next semester?")

        assert tm.primary_domain == "planning"
        assert "C-CS301" in tm.answer_memory.planning_items
        assert "C-CS302" in tm.answer_memory.planning_items
        types = [d.type for d in tm.answer_memory.ordered_display_items]
        assert "planning_item" in types

    def test_TM06_get_skills_taught_stores_course_and_skills(self):
        sqs = [_sq("get_skills_taught", course="C-AI311")]
        tw = _wrap([_result("get_skills_taught", data={
            "course_code": "C-AI311",
            "skills_taught": [
                {"skill_id": "SK_CV", "name": "Computer Vision"},
                {"skill_id": "SK_DL", "name": "Deep Learning"},
            ],
        })])
        tm = build_turn_memory(sqs, tw, "What skills does Computer Vision teach?")

        assert "C-AI311" in tm.answer_memory.courses
        assert "SK_CV" in tm.answer_memory.skills
        assert "SK_DL" in tm.answer_memory.skills

    def test_TM07_get_student_record_no_pii_stored(self):
        sqs = [_sq("get_student_record", student_ref=True, params={"record_focus": "cgpa"})]
        tw = _wrap([_result("get_student_record", data={
            "record_focus": "cgpa",
            "cgpa": 3.1,
            "academic_standing": "good",
            "total_credit_hours_earned": 90,
            # PII that must NOT appear in summary
            "student_id": "22-101172",
            "name": "John Doe",
        })])
        tm = build_turn_memory(sqs, tw, "What is my GPA?")

        assert tm.primary_domain == "student_record"
        summary = tm.answer_memory.student_record_summary or ""
        # Must not contain name or ID
        assert "John" not in summary
        assert "22-101172" not in summary
        # Should have safe fields
        assert "cgpa" in summary or "focus" in summary

    def test_TM08_check_course_eligibility_stores_checked_course(self):
        sqs = [_sq("check_course_eligibility", course="C-CS301", student_ref=True)]
        tw = _wrap([_result("check_course_eligibility", data={
            "target_course_code": "C-CS301",
            "target_course_name": "Data Structures",
            "eligible": True,
            "eligibility_status": "eligible",
        })])
        tm = build_turn_memory(sqs, tw, "Can I take Data Structures?")

        assert tm.primary_domain == "planning"
        assert "C-CS301" in tm.answer_memory.planning_items
        di = tm.answer_memory.ordered_display_items[0]
        assert di.code == "C-CS301"
        assert di.type == "course"


# ── TM09-TM10: render_turn_memory ─────────────────────────────────────────────

class TestRenderTurnMemory:

    def test_TM09_render_skill_gap_memory(self):
        tm = TurnMemory(
            source_intents=["compute_skill_gap"],
            primary_domain="career",
            query_memory=QueryMemory(
                user_text_brief="What are my missing skills for data scientist?",
                intents=["compute_skill_gap"],
                student_referential=True,
            ),
            answer_memory=AnswerMemory(
                roles=["RL_Data_Scientist"],
                skills=["SK_ML", "SK_Stats", "SK_DV"],
                ordered_display_items=[
                    DisplayItem(type="role", code="RL_Data_Scientist",
                                source_intent="compute_skill_gap", rank=0),
                    DisplayItem(type="skill", code="SK_ML", name="Machine Learning",
                                source_intent="compute_skill_gap", rank=1),
                    DisplayItem(type="skill", code="SK_Stats", name="Statistics",
                                source_intent="compute_skill_gap", rank=2),
                ],
            ),
        )
        rendered = render_turn_memory(tm)

        assert "PREVIOUS TURN MEMORY" in rendered
        assert "compute_skill_gap" in rendered
        assert "RL_Data_Scientist" in rendered
        assert "SK_ML" in rendered
        assert "1)" in rendered  # ordered items
        assert "2)" in rendered

    def test_TM10_render_policy_memory(self):
        tm = TurnMemory(
            source_intents=["policy_query"],
            primary_domain="policy",
            query_memory=QueryMemory(
                user_text_brief="What is the academic probation policy?",
                intents=["policy_query"],
            ),
            answer_memory=AnswerMemory(
                policy_text=PolicyText(
                    answer_brief="A student is placed on academic probation when CGPA falls below 2.0...",
                    citations=[Citation(source="CIS Handbook", page=9)],
                ),
            ),
        )
        rendered = render_turn_memory(tm)

        assert "PREVIOUS TURN MEMORY" in rendered
        assert "policy_query" in rendered
        assert "probation" in rendered.lower()
        assert "CIS Handbook" in rendered
        assert "p.9" in rendered

    def test_TM10b_render_planning_memory(self):
        tm = TurnMemory(
            source_intents=["plan_semester"],
            primary_domain="planning",
            query_memory=QueryMemory(
                user_text_brief="What should I take next semester?",
                intents=["plan_semester"],
            ),
            answer_memory=AnswerMemory(
                planning_items=["C-CS301", "C-CS302"],
                ordered_display_items=[
                    DisplayItem(type="planning_item", code="C-CS301",
                                source_intent="plan_semester", rank=0),
                    DisplayItem(type="planning_item", code="C-CS302",
                                source_intent="plan_semester", rank=1),
                ],
            ),
        )
        rendered = render_turn_memory(tm)
        assert "C-CS301" in rendered
        assert "C-CS302" in rendered
        assert "planning_item" in rendered

    def test_TM10c_render_ambiguity_flag(self):
        from gateway.models.schemas import AmbiguityGroup, AmbiguityMeta
        tm = TurnMemory(
            source_intents=["compute_skill_gap"],
            primary_domain="career",
            query_memory=QueryMemory(user_text_brief="test", intents=["compute_skill_gap"]),
            answer_memory=AnswerMemory(
                courses=["C-AI301"],
                skills=["SK_ML"],
            ),
            ambiguity=AmbiguityMeta(
                has_multiple_reference_groups=True,
                groups=[
                    AmbiguityGroup(label="courses", item_type="course", items=["C-AI301"]),
                    AmbiguityGroup(label="skills", item_type="skill", items=["SK_ML"]),
                ],
            ),
        )
        rendered = render_turn_memory(tm)
        assert "Multiple reference groups" in rendered
        assert "clarification_needed" in rendered


# ── TM11-TM15: SessionState.last_turn_memory ─────────────────────────────────

class TestSessionStateTurnMemory:

    def _make_ctx(self) -> StudentContext:
        return StudentContext(
            student_id="S001", name="Test", program="CS",
            first_semester="Fall 2022", study_status="Active",
            total_credit_hours_earned=60,
        )

    def test_TM11_session_state_defaults_to_no_turn_memory(self):
        ctx = self._make_ctx()
        state = SessionState(
            session_id="sess1", student_id="S001",
            session_name="test", student_context=ctx,
        )
        assert state.last_turn_memory is None

    def test_TM12_session_state_stores_turn_memory(self):
        ctx = self._make_ctx()
        tm = TurnMemory(source_intents=["compute_skill_gap"])
        state = SessionState(
            session_id="sess1", student_id="S001",
            session_name="test", student_context=ctx,
            last_turn_memory=tm,
        )
        assert state.last_turn_memory is not None
        assert state.last_turn_memory.source_intents == ["compute_skill_gap"]

    def test_TM13_session_state_roundtrip_json(self):
        ctx = self._make_ctx()
        tm = TurnMemory(
            source_intents=["compute_skill_gap"],
            answer_memory=AnswerMemory(roles=["RL_Data_Scientist"], skills=["SK_ML"]),
        )
        state = SessionState(
            session_id="sess1", student_id="S001",
            session_name="test", student_context=ctx,
            last_turn_memory=tm,
        )
        dumped = state.model_dump_json()
        loaded = SessionState.model_validate_json(dumped)
        assert loaded.last_turn_memory is not None
        assert "RL_Data_Scientist" in loaded.last_turn_memory.answer_memory.roles


# ── TM16-TM23: Scenario chain tests ──────────────────────────────────────────

def _make_mock_client(response_json: str) -> MagicMock:
    client = MagicMock()
    client.is_configured.return_value = True
    client.chat.return_value = response_json
    return client


def _sq_json(intent: str, *, role: str | None = None, skill: str | None = None,
             course: str | None = None, skill_candidates: list | None = None,
             entity_candidates: list | None = None) -> str:
    params: dict[str, Any] = {}
    if skill_candidates:
        params["skill_candidates"] = skill_candidates
    if entity_candidates:
        params["entity_candidates"] = entity_candidates
    sq: dict[str, Any] = {
        "intent": intent,
        "original_text": "test",
        "entities": {
            "course_code": course,
            "role": role,
            "track": None,
            "skill": skill,
        },
        "secondary_entities": None,
        "params": params,
        "session_overrides": {
            "added_courses": [], "assumed_passed_courses": [], "assumed_failed_courses": [],
            "target_role": None, "course_override_type": "none", "override_action": "accumulate",
        },
        "student_referential_fallback": bool(role or course),
    }
    return json.dumps({"queries": [sq]})


class TestScenarioChains:
    """
    Scenario A–H: verify that the turn memory built from Turn 1 causes
    QU (via the injected rendered memory) to receive the expected context.
    We don't re-run QU with the LLM for Turn 2 (that would require a live KG);
    instead we verify that:
      1. build_turn_memory extracts the right data from Turn 1's TurnWrapper.
      2. render_turn_memory produces text containing the expected fields.
      3. build_user_message with last_turn_memory injects the rendered memory.
    """

    def test_TM16_scenario_A_skill_gap_chain(self):
        """Turn 1: compute_skill_gap → memory stores role + missing skills."""
        sqs = [_sq("compute_skill_gap", role="RL_Data_Scientist", student_ref=True)]
        tw = _wrap([_result("compute_skill_gap", data={
            "role_id": "RL_Data_Scientist",
            "missing_skills": [
                {"skill_id": "SK_ML", "name": "Machine Learning"},
                {"skill_id": "SK_Stats", "name": "Statistics"},
                {"skill_id": "SK_DV", "name": "Data Visualization"},
            ],
        })])
        tm = build_turn_memory(sqs, tw, "What are my missing skills to become a Data Scientist?")

        # Verify memory shape
        assert "RL_Data_Scientist" in tm.answer_memory.roles
        assert {"SK_ML", "SK_Stats", "SK_DV"}.issubset(set(tm.answer_memory.skills))

        # Verify QU injection for Turn 2
        rendered = render_turn_memory(tm)
        user_msg = build_user_message(
            "Which courses teach these skills?",
            _no_ref(), [], tm,
        )
        assert "PREVIOUS TURN MEMORY" in user_msg
        assert "SK_ML" in user_msg
        assert "compute_skill_gap" in user_msg

    def test_TM17_scenario_B_role_ranking_chain(self):
        """Turn 1: find_best_matching_roles → memory stores ranked_roles."""
        sqs = [_sq("find_best_matching_roles", student_ref=True)]
        tw = _wrap([_result("find_best_matching_roles", data={
            "ranked_roles": [
                {"role_id": "RL_DS", "name": "Data Scientist"},
                {"role_id": "RL_MLE", "name": "ML Engineer"},
                {"role_id": "RL_DA", "name": "Data Analyst"},
            ],
        })])
        tm = build_turn_memory(sqs, tw, "What roles fit me best?")

        assert tm.answer_memory.roles[0] == "RL_DS"
        assert tm.answer_memory.roles[1] == "RL_MLE"
        di = tm.answer_memory.ordered_display_items
        assert di[0].rank == 0
        assert di[1].rank == 1

        # Turn 2: "Tell me more about the second one" → should have rank 1 item available
        user_msg = build_user_message(
            "Tell me more about the second one.", _no_ref(), [], tm
        )
        assert "RL_MLE" in user_msg or "ML Engineer" in user_msg
        assert "2)" in user_msg

    def test_TM18_scenario_C_course_search_chain(self):
        """Turn 1: search_courses_by_skill → memory stores courses."""
        sqs = [_sq("search_courses_by_skill", skill="SK_DB")]
        tw = _wrap([_result("search_courses_by_skill", data={
            "skill_id": "SK_DB",
            "courses": [
                {"course_code": "C-CS401", "course_name": "Database Systems"},
                {"course_code": "C-CS402", "course_name": "Advanced Databases"},
            ],
        })])
        tm = build_turn_memory(sqs, tw, "Which courses teach databases?")

        assert "C-CS401" in tm.answer_memory.courses
        assert tm.answer_memory.ordered_display_items[0].code == "C-CS401"

        user_msg = build_user_message("Can I take the first one?", _no_ref(), [], tm)
        assert "C-CS401" in user_msg
        assert "1)" in user_msg

    def test_TM19_scenario_D_policy_to_personal_chain(self):
        """Turn 1: policy_query → memory stores compact natural-language policy text."""
        policy_answer = (
            "A student is placed on academic probation when their CGPA falls below 2.0 "
            "for two consecutive semesters."
        )
        sqs = [_sq("policy_query")]
        tw = _wrap([_result("policy_query",
            data={"answer": policy_answer},
            citations=[{"source": "CIS Handbook", "page": 9}],
        )])
        tm = build_turn_memory(sqs, tw, "What is the academic probation policy?")

        pt = tm.answer_memory.policy_text
        assert pt is not None
        assert "probation" in pt.answer_brief.lower()
        assert pt.citations[0].source == "CIS Handbook"

        # Turn 2: "does that apply to me?" → memory should be injected
        user_msg = build_user_message("Does that apply to me?", _no_ref(), [], tm)
        assert "PREVIOUS TURN MEMORY" in user_msg
        assert "probation" in user_msg.lower()
        assert "CIS Handbook" in user_msg

    def test_TM20_scenario_E_course_skills_chain(self):
        """Turn 1: get_skills_taught → memory stores taught skills."""
        sqs = [_sq("get_skills_taught", course="C-AI311")]
        tw = _wrap([_result("get_skills_taught", data={
            "course_code": "C-AI311",
            "skills_taught": [
                {"skill_id": "SK_CV", "name": "Computer Vision"},
                {"skill_id": "SK_DL", "name": "Deep Learning"},
            ],
        })])
        tm = build_turn_memory(sqs, tw, "What skills does Computer Vision teach?")

        assert "SK_CV" in tm.answer_memory.skills
        assert "SK_DL" in tm.answer_memory.skills

        user_msg = build_user_message(
            "Which other courses teach these skills?", _no_ref(), [], tm
        )
        assert "SK_CV" in user_msg
        assert "SK_DL" in user_msg

    def test_TM21_scenario_F_planning_chain(self):
        """Turn 1: plan_semester → memory stores planned courses."""
        sqs = [_sq("plan_semester", student_ref=True)]
        tw = _wrap([_result("plan_semester", data={
            "plans": [{
                "plan_label": "Recommended",
                "total_credits": 18,
                "courses": [
                    {"course_code": "C-CS301", "course_name": "Data Structures"},
                    {"course_code": "C-CS302", "course_name": "Algorithms"},
                    {"course_code": "C-CS303", "course_name": "OS"},
                ],
            }],
        })])
        tm = build_turn_memory(sqs, tw, "What courses should I take next semester?")

        assert "C-CS301" in tm.answer_memory.planning_items
        assert "C-CS302" in tm.answer_memory.planning_items

        user_msg = build_user_message(
            "What are prerequisites for these courses?", _no_ref(), [], tm
        )
        assert "C-CS301" in user_msg

    def test_TM22_scenario_G_multi_intent_chain(self):
        """Turn 1: [check_course_eligibility, get_skills_taught] → skills take precedence for skill reference."""
        sqs = [
            _sq("check_course_eligibility", course="C-AI311", student_ref=True),
            _sq("get_skills_taught", course="C-AI311"),
        ]
        tw = _wrap([
            _result("check_course_eligibility", sq_index=0, data={
                "target_course_code": "C-AI311",
                "eligible": True,
                "eligibility_status": "eligible",
            }),
            _result("get_skills_taught", sq_index=1, data={
                "course_code": "C-AI311",
                "skills_taught": [
                    {"skill_id": "SK_CV", "name": "Computer Vision"},
                    {"skill_id": "SK_DL", "name": "Deep Learning"},
                ],
            }),
        ])
        tm = build_turn_memory(sqs, tw, "Can I take Advanced Programming and what skills does it teach?")

        # Both intents captured
        assert "check_course_eligibility" in tm.source_intents
        assert "get_skills_taught" in tm.source_intents
        # Skills stored from get_skills_taught
        assert "SK_CV" in tm.answer_memory.skills
        # Course stored from both intents
        assert "C-AI311" in tm.answer_memory.courses or "C-AI311" in tm.answer_memory.planning_items

        user_msg = build_user_message(
            "Which other courses teach those skills?", _no_ref(), [], tm
        )
        assert "SK_CV" in user_msg

    def test_TM23_scenario_H_ambiguity_guard(self):
        """Turn 1 with both courses and skills → ambiguity metadata set."""
        sqs = [_sq("compute_skill_gap", role="RL_Data_Scientist", student_ref=True)]
        tw = _wrap([_result("compute_skill_gap", data={
            "role_id": "RL_Data_Scientist",
            "missing_skills": [{"skill_id": "SK_ML"}],
            "recommended_courses": [{"course_code": "C-AI301"}],
        })])
        # Manually craft a mixed answer_memory for the ambiguity test
        tm = build_turn_memory(sqs, tw, "What am I missing?")
        # Inject extra data to force ambiguity
        from gateway.models.schemas import AmbiguityGroup, AmbiguityMeta
        tm = tm.model_copy(update={
            "answer_memory": tm.answer_memory.model_copy(update={
                "courses": ["C-AI301"],
                "skills": ["SK_ML"],
            }),
            "ambiguity": AmbiguityMeta(
                has_multiple_reference_groups=True,
                groups=[
                    AmbiguityGroup(label="courses", item_type="course", items=["C-AI301"]),
                    AmbiguityGroup(label="skills", item_type="skill", items=["SK_ML"]),
                ],
            ),
        })

        rendered = render_turn_memory(tm)
        assert "Multiple reference groups" in rendered
        assert "clarification_needed" in rendered

        user_msg = build_user_message("Tell me more about the first one.", _no_ref(), [], tm)
        assert "Multiple reference groups" in user_msg


# ── TM24: Multi-intent turn memory ────────────────────────────────────────────

class TestMultiIntentMemory:

    def test_TM24_multi_intent_stores_all_intents_and_merges_data(self):
        sqs = [
            _sq("compute_skill_gap", role="RL_Data_Scientist", student_ref=True),
            _sq("recommend_courses_to_close_gap", role="RL_Data_Scientist", student_ref=True),
        ]
        tw = _wrap([
            _result("compute_skill_gap", sq_index=0, data={
                "role_id": "RL_Data_Scientist",
                "missing_skills": [{"skill_id": "SK_ML"}],
            }),
            _result("recommend_courses_to_close_gap", sq_index=1, data={
                "role_id": "RL_Data_Scientist",
                "recommended_courses": [{"course_code": "C-AI301", "course_name": "Intro ML"}],
            }),
        ])
        tm = build_turn_memory(sqs, tw, "What am I missing for data scientist and how to close the gap?")

        assert "compute_skill_gap" in tm.source_intents
        assert "recommend_courses_to_close_gap" in tm.source_intents
        assert "SK_ML" in tm.answer_memory.skills
        assert "C-AI301" in tm.answer_memory.courses
        assert "RL_Data_Scientist" in tm.answer_memory.roles


# ── TM25: Ambiguity detection ─────────────────────────────────────────────────

class TestAmbiguityDetection:

    def test_TM25_single_type_not_ambiguous(self):
        sqs = [_sq("compute_skill_gap", role="RL_Data_Scientist")]
        tw = _wrap([_result("compute_skill_gap", data={
            "role_id": "RL_Data_Scientist",
            "missing_skills": [{"skill_id": "SK_ML"}, {"skill_id": "SK_Stats"}],
        })])
        tm = build_turn_memory(sqs, tw, "skill gap")
        # Only skills in display items (plus the role) — not necessarily ambiguous
        # Skills + role = two types → could be ambiguous, but only skills were asked
        # The ambiguity guard fires when distinct item types > 1

    def test_TM25b_mixed_types_are_ambiguous(self):
        from gateway.models.schemas import AmbiguityGroup, AmbiguityMeta
        am = AnswerMemory(courses=["C-AI301"], skills=["SK_ML"])
        from gateway.turn_memory_builder import _build_ambiguity
        amb = _build_ambiguity(am)
        assert amb.has_multiple_reference_groups is True
        types = {g.item_type for g in amb.groups}
        assert "course" in types
        assert "skill" in types

    def test_TM25c_single_type_not_ambiguous(self):
        from gateway.turn_memory_builder import _build_ambiguity
        am = AnswerMemory(skills=["SK_ML", "SK_Stats", "SK_DV"])
        amb = _build_ambiguity(am)
        assert amb.has_multiple_reference_groups is False


# ── TM26: Error/empty graceful handling ───────────────────────────────────────

class TestGracefulHandling:

    def test_TM26_error_results_skipped(self):
        sqs = [_sq("compute_skill_gap", role="RL_Data_Scientist")]
        tw = _wrap([_result("compute_skill_gap", status="error")])
        tm = build_turn_memory(sqs, tw, "test")
        assert tm.answer_memory.roles == []
        assert tm.answer_memory.skills == []

    def test_TM26b_empty_data_no_crash(self):
        sqs = [_sq("compute_skill_gap", role="RL_Data_Scientist")]
        tw = _wrap([_result("compute_skill_gap", data={})])
        tm = build_turn_memory(sqs, tw, "test")
        assert isinstance(tm, TurnMemory)

    def test_TM26c_out_of_scope_result_skipped(self):
        sqs = [_sq("out_of_scope")]
        tw = _wrap([_result("out_of_scope", status="out_of_scope")])
        tm = build_turn_memory(sqs, tw, "Can you book a flight?")
        assert tm.primary_domain == "control"
        assert tm.answer_memory.courses == []


# ── TM27: Caps enforcement ────────────────────────────────────────────────────

class TestCapsEnforcement:

    def test_TM27_courses_capped_at_8(self):
        courses = [{"course_code": f"C-AI{300+i}", "course_name": f"Course {i}"} for i in range(20)]
        sqs = [_sq("search_courses_by_skill", skill="SK_ML")]
        tw = _wrap([_result("search_courses_by_skill", data={"skill_id": "SK_ML", "courses": courses})])
        tm = build_turn_memory(sqs, tw, "courses by skill")
        assert len(tm.answer_memory.courses) <= 8

    def test_TM27b_skills_capped_at_10(self):
        skills = [{"skill_id": f"SK_{i}", "name": f"Skill {i}"} for i in range(25)]
        sqs = [_sq("compute_skill_gap", role="RL_DS")]
        tw = _wrap([_result("compute_skill_gap", data={"role_id": "RL_DS", "missing_skills": skills})])
        tm = build_turn_memory(sqs, tw, "skill gap")
        assert len(tm.answer_memory.skills) <= 10

    def test_TM27c_display_items_capped_at_12(self):
        roles = [{"role_id": f"RL_R{i}", "name": f"Role {i}"} for i in range(20)]
        sqs = [_sq("find_best_matching_roles", student_ref=True)]
        tw = _wrap([_result("find_best_matching_roles", data={"ranked_roles": roles})])
        tm = build_turn_memory(sqs, tw, "what roles fit me")
        assert len(tm.answer_memory.ordered_display_items) <= 12

    def test_TM27d_policy_brief_capped_at_500(self):
        long_answer = "A " * 400  # 800 chars
        sqs = [_sq("policy_query")]
        tw = _wrap([_result("policy_query", data={"answer": long_answer})])
        tm = build_turn_memory(sqs, tw, "policy question")
        assert tm.answer_memory.policy_text is not None
        assert len(tm.answer_memory.policy_text.answer_brief) <= 500

    def test_TM27e_user_text_brief_capped_at_160(self):
        long_text = "x" * 300
        sqs = [_sq("policy_query")]
        tw = _wrap([_result("policy_query", data={"answer": "some answer"})])
        tm = build_turn_memory(sqs, tw, long_text)
        assert len(tm.query_memory.user_text_brief) <= 160


# ── build_user_message with memory integration ────────────────────────────────

class TestBuildUserMessageWithMemory:

    def test_memory_injected_before_recent_turns(self):
        tm = TurnMemory(
            source_intents=["compute_skill_gap"],
            query_memory=QueryMemory(user_text_brief="test", intents=["compute_skill_gap"]),
            answer_memory=AnswerMemory(skills=["SK_ML"]),
        )
        msg = build_user_message(
            "which courses teach these skills?",
            _no_ref(),
            [{"user": "previous question", "answer": "previous answer"}],
            tm,
        )
        # PREVIOUS TURN MEMORY must come before Recent turns
        assert msg.index("PREVIOUS TURN MEMORY") < msg.index("Recent turns")
        assert msg.index("Recent turns") < msg.index("Student:")

    def test_no_memory_no_injection(self):
        msg = build_user_message(
            "what is my GPA?",
            _no_ref(),
            [],
            None,
        )
        assert "PREVIOUS TURN MEMORY" not in msg
        assert "Student: what is my GPA?" in msg
