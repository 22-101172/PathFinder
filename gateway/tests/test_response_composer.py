"""
test_response_composer.py
─────────────────────────
Unit tests for the ResponseComposer with a FakeLLMClient.
"""

from __future__ import annotations

from gateway.models.schemas import (
    Citation,
    RAGResult,
    ResultPackage,
)
from gateway.response_composer import ResponseComposer


# ── Clarification / error paths (no LLM) ─────────────────────────────────────

def test_clarification_does_not_call_llm(fake_llm):
    composer = ResponseComposer(llm_client=fake_llm)
    pkg = ResultPackage(
        original_query="???",
        engine_pattern="kg",
        status="clarification_needed",
        error_detail="Which course?",
    )
    response = composer.compose(pkg)
    assert response.status == "clarification_needed"
    assert response.answer_text == "Which course?"
    assert fake_llm.calls == []


def test_error_does_not_call_llm_and_maps_known_codes(fake_llm):
    composer = ResponseComposer(llm_client=fake_llm)
    pkg = ResultPackage(
        original_query="tell me about C-XX999",
        engine_pattern="kg",
        status="error",
        error_detail="course_not_found",
    )
    response = composer.compose(pkg)
    assert response.status == "error"
    assert response.answer_text == "I couldn't find that course in the curriculum."
    assert fake_llm.calls == []


# ── OK path with LLM ─────────────────────────────────────────────────────────

def test_ok_calls_llm_with_sanitized_prompt(fake_llm, fake_student_context):
    fake_llm.next_response = "Here is what the curriculum says about C-AI311."
    composer = ResponseComposer(llm_client=fake_llm)
    pkg = ResultPackage(
        original_query="tell me about C-AI311",
        engine_pattern="kg",
        kg_result={
            "course_code": "C-AI311",
            "name": "Introduction to Artificial Intelligence",
            "credits": 4,
            "level": 3,
            "semester_offering": ["Fall"],
            "tracks": [],
            "description": "",
        },
        student_context=fake_student_context,
        status="ok",
    )
    response = composer.compose(pkg)
    assert response.status == "ok"
    assert response.answer_text.startswith("Here is what the curriculum")

    # Privacy: the user prompt must not contain student PII.
    assert len(fake_llm.calls) == 1
    user_prompt = fake_llm.calls[0]["user"]
    assert fake_student_context.name not in user_prompt
    assert fake_student_context.student_id not in user_prompt
    assert str(fake_student_context.cgpa) not in user_prompt
    # CourseRecord and detailed transcript MUST NOT leak through.
    assert "course_history" not in user_prompt
    assert "completed_courses" not in user_prompt
    # But non-personal fields are fine.
    assert "AI" in user_prompt  # track_id
    assert "Spring" in user_prompt  # current_semester


def test_rag_only_includes_citations(fake_llm):
    fake_llm.next_response = "The credit limit is 21 per semester."
    composer = ResponseComposer(llm_client=fake_llm)
    pkg = ResultPackage(
        original_query="credit limit?",
        engine_pattern="rag",
        rag_result=RAGResult(
            answer="The credit limit is 21 per semester.",
            citations=[
                Citation(source="CIS Student Handbook", page=12),
                Citation(source="CIS Student Handbook", page=13),
            ],
        ),
        status="ok",
    )
    response = composer.compose(pkg)
    assert response.status == "ok"
    assert len(response.citations) == 2
    assert response.citations[0].page == 12


def test_mixed_includes_both_blocks_in_prompt(fake_llm):
    fake_llm.next_response = "Mixed answer."
    composer = ResponseComposer(llm_client=fake_llm)
    pkg = ResultPackage(
        original_query="C-AI311 and the drop policy?",
        engine_pattern="mixed",
        kg_result={"course_code": "C-AI311", "name": "AI", "credits": 4, "level": 3, "semester_offering": ["Fall"], "tracks": []},
        rag_result=RAGResult(
            answer="Drop policy text.",
            citations=[Citation(source="CIS Student Handbook", page=4)],
        ),
        status="ok",
    )
    response = composer.compose(pkg)
    user_prompt = fake_llm.calls[0]["user"]
    assert "CURRICULUM DATA" in user_prompt
    assert "HANDBOOK DATA" in user_prompt
    assert "C-AI311" in user_prompt
    assert "Drop policy text." in user_prompt
    assert response.status == "ok"
    # Citations come from the RAG side regardless of LLM.
    assert response.citations[0].page == 4


# ── Fallback paths ───────────────────────────────────────────────────────────

def test_llm_failure_falls_back_deterministically(fake_llm):
    fake_llm.fail = True
    composer = ResponseComposer(llm_client=fake_llm)
    pkg = ResultPackage(
        original_query="tell me about C-AI311",
        engine_pattern="kg",
        kg_result={
            "course_code": "C-AI311",
            "name": "Introduction to Artificial Intelligence",
            "credits": 4,
            "level": 3,
            "semester_offering": ["Fall"],
            "tracks": [],
            "description": "",
        },
        status="ok",
    )
    response = composer.compose(pkg)
    assert response.status == "ok"
    assert "C-AI311" in response.answer_text
    assert "Introduction to Artificial Intelligence" in response.answer_text


def test_llm_not_configured_falls_back(fake_llm):
    fake_llm.configured = False
    composer = ResponseComposer(llm_client=fake_llm)
    pkg = ResultPackage(
        original_query="prereqs?",
        engine_pattern="kg",
        kg_result={
            "course_code": "C-AI311",
            "name": "Introduction to Artificial Intelligence",
            "direct_prerequisites": [
                {"course_code": "C-CS213", "name": "Data Structures"},
            ],
            "non_course_prerequisites": [],
            "has_prerequisites": True,
            "full_prerequisite_tree": [],
        },
        status="ok",
    )
    response = composer.compose(pkg)
    assert fake_llm.calls == []
    assert response.status == "ok"
    assert "Data Structures" in response.answer_text


def test_fallback_passes_rag_answer_through(fake_llm):
    fake_llm.fail = True
    composer = ResponseComposer(llm_client=fake_llm)
    pkg = ResultPackage(
        original_query="probation rules?",
        engine_pattern="rag",
        rag_result=RAGResult(
            answer="If your CGPA stays below 2.0 for two semesters, you may be placed on academic probation.",
            citations=[Citation(source="CIS Student Handbook", page=27)],
        ),
        status="ok",
    )
    response = composer.compose(pkg)
    assert response.status == "ok"
    assert "academic probation" in response.answer_text
    assert response.citations[0].page == 27
