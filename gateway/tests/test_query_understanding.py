"""
test_query_understanding.py
───────────────────────────
Unit tests for the rule-based and LLM-fallback layers of
`QueryUnderstandingLayer`.

All tests run offline: the LLM fixture is a fake, and the KG reference data
is a hand-built stub from conftest.
"""

from __future__ import annotations

import json

import pytest

from gateway.query_understanding import QueryUnderstandingLayer
from gateway.session_manager import SessionState as RuntimeSessionState
from datetime import datetime, timezone


def _session(course_code: str = None, role_id: str = None, target_role: str = None) -> RuntimeSessionState:
    """Build a runtime SessionState with the given last_referenced/overrides."""
    now = datetime.now(timezone.utc)
    state = RuntimeSessionState(
        session_id="sess_test",
        active_student_id="S_TEST",
        created_at=now,
        last_updated=now,
    )
    if course_code:
        state.last_referenced["course_code"] = course_code
    if role_id:
        state.last_referenced["role_id"] = role_id
    if target_role:
        state.overrides["target_role"] = target_role
    return state


# ── Rule layer ───────────────────────────────────────────────────────────────

def test_prerequisites_kg_route(kg_data_stub, fake_llm, fake_student_context):
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify(
        "What are the prerequisites for C-AI311?",
        student_context=fake_student_context,
    )
    assert result.intent == "get_prerequisites"
    assert result.engine_pattern == "kg"
    assert result.query_type == "non_student_aware"
    assert result.entities.course_code == "C-AI311"
    assert result.needs_clarification is False


def test_handbook_policy_route(kg_data_stub, fake_llm):
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify("What is the credit limit per semester?")
    assert result.intent == "handbook_policy_query"
    assert result.engine_pattern == "rag"


def test_skill_gap_student_aware(kg_data_stub, fake_llm, fake_student_context):
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify(
        "What skills am I missing for Data Scientist?",
        student_context=fake_student_context,
    )
    assert result.intent == "skill_gap_analysis"
    assert result.engine_pattern == "kg"
    assert result.query_type == "student_aware"
    assert result.entities.role_id == "RL_Data_Scientist"


def test_course_skills_kg(kg_data_stub, fake_llm):
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify("What skills does C-AI311 teach?")
    assert result.intent == "get_skills_taught"
    assert result.entities.course_code == "C-AI311"


def test_role_recommendation_student_aware(kg_data_stub, fake_llm, fake_student_context):
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify(
        "What roles match my background?",
        student_context=fake_student_context,
    )
    assert result.intent == "role_recommendation"
    assert result.query_type == "student_aware"


def test_followup_pronoun_resolves_from_session(kg_data_stub, fake_llm):
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    state = _session(course_code="C-AI311")
    result = qu.classify("What about its prerequisites?", session_state=state)
    assert result.intent == "get_prerequisites"
    assert result.entities.course_code == "C-AI311"
    assert result.needs_clarification is False


def test_override_added_courses(kg_data_stub, fake_llm):
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify("Assume I completed C-AI311 and look up its prerequisites.")
    assert "C-AI311" in result.session_overrides.added_courses


def test_override_target_role(kg_data_stub, fake_llm):
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify("I want to become a Data Scientist.")
    assert result.session_overrides.target_role == "RL_Data_Scientist"


def test_compare_tracks_returns_clarification(kg_data_stub, fake_llm):
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify("Compare tracks AI and DSE.")
    assert result.needs_clarification is True
    assert "one track at a time" in (result.clarification_prompt or "").lower()


def test_empty_text_returns_clarification(kg_data_stub, fake_llm):
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify("")
    assert result.needs_clarification is True


def test_course_intent_without_course_returns_clarification(kg_data_stub, fake_llm):
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify("What are the prerequisites?")
    assert result.needs_clarification is True
    assert "course" in (result.clarification_prompt or "").lower()


# ── LLM fallback ─────────────────────────────────────────────────────────────

def test_llm_fallback_invoked_when_rules_miss(kg_data_stub, fake_llm):
    fake_llm.next_response = json.dumps({
        "intent": "get_course_profile",
        "engine_pattern": "kg",
        "query_type": "non_student_aware",
        "entities": {"course_code": "C-AI311"},
        "needs_clarification": False,
        "clarification_prompt": None,
        "session_overrides": {"added_courses": [], "target_role": None},
    })
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    # A query that the rule layer cannot classify (no keyword match) but for
    # which we provide the LLM answer.
    result = qu.classify("Walk me through C-AI311 briefly.")
    assert len(fake_llm.calls) == 1
    assert result.intent == "get_course_profile"
    assert result.entities.course_code == "C-AI311"


def test_llm_fallback_skipped_when_not_configured(kg_data_stub, fake_llm):
    fake_llm.configured = False
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify("Walk me through C-AI311 briefly.")
    assert fake_llm.calls == []
    # Result should be ambiguous with a clarification prompt.
    assert result.needs_clarification is True


def test_llm_fallback_bad_json_returns_ambiguous(kg_data_stub, fake_llm):
    fake_llm.next_response = "this is not json"
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify("Walk me through C-AI311 briefly.")
    assert result.needs_clarification is True


def test_llm_fallback_invalid_intent_returns_ambiguous(kg_data_stub, fake_llm):
    fake_llm.next_response = json.dumps({
        "intent": "nuke_curriculum",
        "engine_pattern": "kg",
        "query_type": "non_student_aware",
        "entities": {},
        "needs_clarification": False,
        "session_overrides": {"added_courses": [], "target_role": None},
    })
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    result = qu.classify("Walk me through something obscure.")
    assert result.needs_clarification is True


# ── Privacy guard ────────────────────────────────────────────────────────────

def test_llm_prompt_does_not_leak_pii(kg_data_stub, fake_llm, fake_student_context):
    """The LLM-fallback prompt must not contain student PII."""
    fake_llm.next_response = json.dumps({
        "intent": "get_course_profile",
        "engine_pattern": "kg",
        "query_type": "non_student_aware",
        "entities": {"course_code": "C-AI311"},
        "needs_clarification": False,
        "session_overrides": {"added_courses": [], "target_role": None},
    })
    qu = QueryUnderstandingLayer(kg_data=kg_data_stub, llm_client=fake_llm)
    qu.classify("Walk me through C-AI311 briefly.", student_context=fake_student_context)

    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    blob = call["system"] + "\n" + call["user"]
    # Spot-check for sensitive substrings from the student context.
    assert fake_student_context.name not in blob
    assert fake_student_context.student_id not in blob
    assert str(fake_student_context.cgpa) not in blob
    for record in fake_student_context.course_history:
        if record.grade:
            # The literal letter grade should not appear.
            assert f" {record.grade} " not in blob
