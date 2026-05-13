"""
test_orchestrator.py
────────────────────
Unit tests for the Orchestrator. Uses `FakeKGAdapter` and `FakeRAGAdapter`
fixtures from conftest. No live KG or RAG.
"""

from __future__ import annotations

from gateway.models.schemas import (
    EntitySet,
    SessionOverrides,
    StructuredQuery,
)
from gateway.orchestrator import Orchestrator


def _make_query(
    *,
    intent: str,
    engine_pattern: str = "kg",
    query_type: str = "non_student_aware",
    course_code: str = None,
    role_id: str = None,
    track_id: str = None,
    skill_id: str = None,
    needs_clarification: bool = False,
    clarification_prompt: str = None,
) -> StructuredQuery:
    return StructuredQuery(
        intent=intent,
        engine_pattern=engine_pattern,
        query_type=query_type,
        entities=EntitySet(
            course_code=course_code,
            role_id=role_id,
            track_id=track_id,
            skill_id=skill_id,
        ),
        needs_clarification=needs_clarification,
        clarification_prompt=clarification_prompt,
        session_overrides=SessionOverrides(),
    )


# ── KG-only workflow ─────────────────────────────────────────────────────────

def test_kg_only_prerequisites(fake_kg, fake_rag):
    fake_kg.set_response("get_prerequisites", {
        "course_code": "C-AI311",
        "name": "Intro to AI",
        "direct_prerequisites": [{"course_code": "C-CS213", "name": "Data Structures"}],
        "non_course_prerequisites": [],
        "has_prerequisites": True,
        "full_prerequisite_tree": [],
    })
    orch = Orchestrator(fake_kg, fake_rag)
    query = _make_query(intent="get_prerequisites", course_code="C-AI311")
    result = orch.run(query, effective_context=None, original_query="prereqs?")

    assert fake_kg.calls == [("get_prerequisites", {"course_code": "C-AI311", "depth": "direct"})]
    assert fake_rag.calls == []
    assert result.status == "ok"
    assert result.engine_pattern == "kg"


def test_kg_error_returns_error_package(fake_kg, fake_rag):
    fake_kg.set_response("get_course_profile", {"error": "course_not_found", "submitted_code": "C-XX999"})
    orch = Orchestrator(fake_kg, fake_rag)
    query = _make_query(intent="get_course_profile", course_code="C-XX999")
    result = orch.run(query, effective_context=None, original_query="tell me about C-XX999")

    assert result.status == "error"
    assert result.error_detail == "course_not_found"


def test_unmapped_intent_returns_clarification(fake_kg, fake_rag):
    """`compare_tracks` is deferred — Orchestrator can't map it."""
    orch = Orchestrator(fake_kg, fake_rag)
    query = _make_query(intent="compare_tracks", course_code=None)
    result = orch.run(query, effective_context=None, original_query="compare tracks")
    assert result.status == "clarification_needed"
    assert fake_kg.calls == []  # no operation called


# ── RAG-only workflow ────────────────────────────────────────────────────────

def test_rag_only_passes_original_query_no_student_context(fake_kg, fake_rag):
    fake_rag.set_next(
        answer="Maximum credit hours per semester is 21.",
        citations=[{"source": "CIS Student Handbook", "page": 12}],
    )
    orch = Orchestrator(fake_kg, fake_rag)
    query = _make_query(intent="handbook_policy_query", engine_pattern="rag")
    result = orch.run(query, effective_context=None, original_query="What is the credit limit?")

    assert fake_kg.calls == []
    # student_context must be None — RAG should never see PII.
    assert fake_rag.calls == [("What is the credit limit?", None)]
    assert result.status == "ok"
    assert result.engine_pattern == "rag"
    assert len(result.rag_result.citations) == 1
    assert result.rag_result.citations[0].source == "CIS Student Handbook"
    assert result.rag_result.citations[0].page == 12


def test_rag_error_returns_error_package(fake_kg, fake_rag):
    fake_rag.set_next(
        answer="An error occurred while searching the handbook: boom",
        citations=[],
    )
    orch = Orchestrator(fake_kg, fake_rag)
    query = _make_query(intent="handbook_policy_query", engine_pattern="rag")
    result = orch.run(query, effective_context=None, original_query="probation rules?")
    assert result.status == "error"


# ── Student-aware workflow ───────────────────────────────────────────────────

def test_student_aware_skill_gap_passes_completed_courses(fake_kg, fake_rag, fake_student_context):
    fake_kg.set_response("compute_skill_gap", {
        "role_id": "RL_Data_Scientist",
        "role_name": "Data Scientist",
        "covered_skills": [],
        "missing_skills": [{"skill_id": "SK_ML", "name": "Machine Learning", "tier": "core", "weight": 0.9}],
        "total_covered": 0,
        "total_missing": 1,
        "total_required": 1,
    })
    orch = Orchestrator(fake_kg, fake_rag)
    query = _make_query(
        intent="skill_gap_analysis",
        query_type="student_aware",
        role_id="RL_Data_Scientist",
    )
    result = orch.run(query, effective_context=fake_student_context, original_query="gap?")

    assert len(fake_kg.calls) == 1
    op, params = fake_kg.calls[0]
    assert op == "compute_skill_gap"
    assert params["role_id"] == "RL_Data_Scientist"
    assert params["completed_courses"] == ["C-CS111", "C-AI311"]
    assert result.status == "ok"
    assert result.student_context is fake_student_context


def test_student_aware_estimate_uses_planned_courses(fake_kg, fake_rag, fake_student_context):
    fake_student_context_with_planned = fake_student_context.model_copy(
        update={"planned_courses": ["C-AI321"]}
    )
    fake_kg.set_response("estimate_alignment_improvement", {
        "role_id": "RL_Data_Scientist",
        "role_name": "Data Scientist",
        "current_alignment_score": 0.4,
        "current_alignment_percentage": 40.0,
        "projected_alignment_score": 0.6,
        "projected_alignment_percentage": 60.0,
        "alignment_improvement": 0.2,
        "newly_covered_skills": [],
        "still_missing_skills": [],
        "total_newly_covered": 0,
        "total_still_missing": 0,
    })
    orch = Orchestrator(fake_kg, fake_rag)
    query = _make_query(
        intent="estimate_alignment_improvement",
        query_type="student_aware",
        role_id="RL_Data_Scientist",
    )
    result = orch.run(query, effective_context=fake_student_context_with_planned, original_query="what if?")

    op, params = fake_kg.calls[0]
    assert op == "estimate_alignment_improvement"
    assert params["planned_courses"] == ["C-AI321"]
    assert result.status == "ok"


def test_student_aware_missing_role_returns_clarification(fake_kg, fake_rag, fake_student_context):
    orch = Orchestrator(fake_kg, fake_rag)
    query = _make_query(
        intent="skill_gap_analysis",
        query_type="student_aware",
        role_id=None,  # missing!
    )
    result = orch.run(query, effective_context=fake_student_context, original_query="gap?")
    assert result.status == "clarification_needed"
    assert fake_kg.calls == []


def test_student_aware_without_context_returns_error(fake_kg, fake_rag):
    orch = Orchestrator(fake_kg, fake_rag)
    query = _make_query(
        intent="skill_gap_analysis",
        query_type="student_aware",
        role_id="RL_Data_Scientist",
    )
    result = orch.run(query, effective_context=None, original_query="gap?")
    assert result.status == "error"
    assert result.error_detail == "student_context_required"


# ── Mixed workflow ───────────────────────────────────────────────────────────

def test_mixed_workflow_calls_both_engines(fake_kg, fake_rag):
    fake_kg.set_response("get_course_profile", {
        "course_code": "C-AI311",
        "name": "Intro to AI",
        "credits": 4,
        "level": 3,
        "semester_offering": ["Fall"],
        "tracks": [],
        "description": "",
    })
    fake_rag.set_next(
        answer="The drop policy is documented in the handbook.",
        citations=[{"source": "CIS Student Handbook", "page": 4}],
    )
    orch = Orchestrator(fake_kg, fake_rag)
    query = _make_query(
        intent="course_and_policy_query",
        engine_pattern="mixed",
        course_code="C-AI311",
    )
    result = orch.run(
        query, effective_context=None,
        original_query="Tell me about C-AI311 and the drop policy.",
    )
    assert any(c[0] == "get_course_profile" for c in fake_kg.calls)
    assert len(fake_rag.calls) == 1
    assert result.status == "ok"
    assert result.engine_pattern == "mixed"
    assert result.kg_result is not None
    assert result.rag_result is not None
    # Citations carried through.
    assert result.rag_result.citations[0].source == "CIS Student Handbook"


# ── Clarification passthrough ────────────────────────────────────────────────

def test_clarification_passthrough(fake_kg, fake_rag):
    orch = Orchestrator(fake_kg, fake_rag)
    query = _make_query(
        intent="ambiguous",
        needs_clarification=True,
        clarification_prompt="Could you be more specific?",
    )
    result = orch.run(query, effective_context=None, original_query="???")
    assert result.status == "clarification_needed"
    assert result.error_detail == "Could you be more specific?"
    assert fake_kg.calls == []
    assert fake_rag.calls == []
