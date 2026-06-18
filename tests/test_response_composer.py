"""
Unit tests for gateway/response_composer.py.

All LLM calls are mocked so tests run without real API keys.
Tests cover:
  - LLM success path
  - LLM failure → deterministic fallback
  - LLM not configured → deterministic fallback
  - COMPOSER_USE_LLM=false → deterministic fallback
  - Every PerSQResult status (success, error, clarification_needed,
    out_of_scope, informational, soft_no_evidence)
  - Multi-SQ response ordering and merging
  - Citation merging and deduplication
  - Assumption / override notices
  - Representative intents from all six domains
  - Status mapping (turn_status → QueryResponse.status)
  - No raw StudentContext leakage through to the LLM prompt
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from gateway.models.schemas import (
    Citation, PerSQResult, QueryResponse, TurnWrapper,
)
from gateway.response_composer import (
    ResponseComposer,
    _collect_citations,
    _deterministic_answer,
    _extract_packet,
    _map_turn_status,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_tw(
    results: list[PerSQResult],
    turn_status: str = "completed",
    session_id: str = "sess-001",
) -> TurnWrapper:
    statuses = {r.status for r in results}
    return TurnWrapper(
        turn_id="t-001",
        session_id=session_id,
        timestamp="2026-06-16T00:00:00+00:00",
        results=results,
        result_count=len(results),
        turn_status=turn_status,
        has_error="error" in statuses,
        has_clarification="clarification_needed" in statuses,
        has_informational="informational" in statuses,
        has_soft_no_evidence="soft_no_evidence" in statuses,
    )


def _make_composer(use_llm: bool = False) -> ResponseComposer:
    """Return a Composer with LLM disabled (deterministic) by default for testing."""
    with patch.dict(os.environ, {"COMPOSER_USE_LLM": "false" if not use_llm else "true"}):
        return ResponseComposer()


def _make_composer_with_mock_llm(mock_llm: MagicMock) -> ResponseComposer:
    """Return a Composer that uses a pre-built mock LLM client."""
    composer = _make_composer(use_llm=True)
    composer._llm = mock_llm
    return composer


# ── _extract_packet ────────────────────────────────────────────────────────────

class TestExtractPacket:

    def test_error_status(self):
        r = PerSQResult(sq_index=0, intent="get_course_info", status="error",
                        error_detail="KG unavailable.", error_code="kg_unavailable")
        p = _extract_packet(r)
        assert p["status"] == "error"
        assert "KG unavailable" in p["error"]
        assert "error_code" in p
        # data must NOT appear for error
        assert "data" not in p
        assert "notice_assumptions_active" not in p

    def test_clarification_needed_status(self):
        r = PerSQResult(sq_index=0, intent="check_course_eligibility",
                        status="clarification_needed",
                        clarification_prompt="Which course?")
        p = _extract_packet(r)
        assert p["status"] == "clarification_needed"
        assert p["clarification_prompt"] == "Which course?"

    def test_out_of_scope_status(self):
        r = PerSQResult(sq_index=0, intent="out_of_scope", status="out_of_scope",
                        scope_explanation="Outside scope.")
        p = _extract_packet(r)
        assert p["status"] == "out_of_scope"
        assert "Outside scope" in p["scope_explanation"]

    def test_soft_no_evidence_sets_flag(self):
        r = PerSQResult(sq_index=0, intent="policy_query", status="soft_no_evidence",
                        data={"answer": "Limited info.", "extracted_facts": []})
        p = _extract_packet(r)
        assert p.get("evidence_limited") is True
        assert "answer" in p

    def test_assumptions_active_notice(self):
        r = PerSQResult(sq_index=0, intent="plan_semester", status="success",
                        data={}, assumptions_active=True)
        p = _extract_packet(r)
        assert "notice_assumptions_active" in p
        assert "what-if" in p["notice_assumptions_active"].lower()

    def test_assumptions_excluded_notice(self):
        r = PerSQResult(sq_index=0, intent="run_graduation_audit", status="success",
                        data={}, assumptions_excluded=True)
        p = _extract_packet(r)
        assert "notice_assumptions_excluded" in p
        assert "official" in p["notice_assumptions_excluded"].lower()

    def test_override_state_active_notice(self):
        r = PerSQResult(sq_index=0, intent="get_student_record", status="success",
                        data={}, override_state_active=True)
        p = _extract_packet(r)
        assert "notice_override_active" in p

    def test_citations_forwarded(self):
        r = PerSQResult(sq_index=0, intent="policy_query", status="success",
                        data={"answer": "x", "extracted_facts": ["f"]},
                        citations=[{"source": "Handbook", "page": 12}])
        p = _extract_packet(r)
        assert p["citations"] == [{"source": "Handbook", "page": 12}]

    def test_no_raw_student_context_fields(self):
        """The packet must not contain course_history, student_id, name, etc."""
        r = PerSQResult(sq_index=0, intent="get_course_info", status="success",
                        data={"course_code": "C-CS301", "name": "Data Structures",
                              "credits": 3})
        p = _extract_packet(r)
        for forbidden in ("course_history", "student_id", "name_of_student"):
            assert forbidden not in p

    # Intent-specific field extraction
    def test_eligibility_fields(self):
        r = PerSQResult(sq_index=0, intent="check_course_eligibility", status="success",
                        data={"eligible": False, "reason": "prereq_not_met",
                              "missing_prerequisites": ["C-CS201"],
                              "target_course_code": "C-CS301"})
        p = _extract_packet(r)
        assert p["eligible"] is False
        assert p["reason"] == "prereq_not_met"
        assert "C-CS201" in p["missing_prerequisites"]
        assert p["target_course_code"] == "C-CS301"

    def test_graduation_audit_fields(self):
        r = PerSQResult(sq_index=0, intent="run_graduation_audit", status="success",
                        data={"can_graduate": True, "cgpa": 3.5, "honors": "Distinction"})
        p = _extract_packet(r)
        assert p["can_graduate"] is True
        assert p["cgpa"] == 3.5
        assert p["honors"] == "Distinction"

    def test_gpa_fields(self):
        r = PerSQResult(sq_index=0, intent="simulate_gpa_forward", status="success",
                        data={"current_cgpa": 2.8, "projected_cgpa": 3.1})
        p = _extract_packet(r)
        assert p["current_cgpa"] == 2.8
        assert p["projected_cgpa"] == 3.1

    def test_policy_fields(self):
        r = PerSQResult(sq_index=0, intent="policy_query", status="success",
                        data={"answer": "Max 21 credits per semester.",
                              "extracted_facts": ["Max load is 21 CH"]})
        p = _extract_packet(r)
        assert "Max 21 credits" in p["answer"]
        assert p["extracted_facts"][0] == "Max load is 21 CH"

    def test_large_list_capped(self):
        """Lists larger than the cap must be truncated."""
        courses = [f"C-CS{i:03d}" for i in range(50)]
        r = PerSQResult(sq_index=0, intent="search_courses_by_skill", status="success",
                        data={"skill_id": "python", "courses": courses})
        p = _extract_packet(r)
        assert len(p["courses"]) <= 20


# ── _deterministic_answer ─────────────────────────────────────────────────────

class TestDeterministicAnswer:

    def _packets(self, results: list[PerSQResult]) -> list[dict]:
        return [_extract_packet(r) for r in results]

    def test_error_result(self):
        r = PerSQResult(sq_index=0, intent="get_course_info", status="error",
                        error_detail="Course not found.")
        answer = _deterministic_answer(self._packets([r]))
        assert "not found" in answer.lower() or "Course not found" in answer

    def test_clarification_prompt_returned(self):
        r = PerSQResult(sq_index=0, intent="check_course_eligibility",
                        status="clarification_needed",
                        clarification_prompt="Which course would you like to check?")
        answer = _deterministic_answer(self._packets([r]))
        assert "Which course would you like to check?" in answer

    def test_out_of_scope(self):
        r = PerSQResult(sq_index=0, intent="out_of_scope", status="out_of_scope",
                        scope_explanation="PathFinder covers academic advising only.")
        answer = _deterministic_answer(self._packets([r]))
        assert "PathFinder covers academic advising only." in answer

    def test_soft_no_evidence_notice(self):
        r = PerSQResult(sq_index=0, intent="policy_query", status="soft_no_evidence",
                        data={"answer": "Some info.", "extracted_facts": []})
        answer = _deterministic_answer(self._packets([r]))
        assert "limited" in answer.lower()

    def test_eligible_true(self):
        r = PerSQResult(sq_index=0, intent="check_course_eligibility", status="success",
                        data={"eligible": True, "target_course_code": "C-CS301"})
        answer = _deterministic_answer(self._packets([r]))
        assert "eligible" in answer.lower()
        assert "C-CS301" in answer

    def test_eligible_false_with_missing(self):
        r = PerSQResult(sq_index=0, intent="check_course_eligibility", status="success",
                        data={"eligible": False, "target_course_code": "C-CS401",
                              "reason": "prereq_not_met",
                              "missing_prerequisites": ["C-CS301", "C-CS302"]})
        answer = _deterministic_answer(self._packets([r]))
        assert "not" in answer.lower()
        assert "C-CS301" in answer
        assert "C-CS302" in answer

    def test_graduation_audit_can_graduate_true(self):
        r = PerSQResult(sq_index=0, intent="run_graduation_audit", status="success",
                        data={"can_graduate": True, "cgpa": 3.6})
        answer = _deterministic_answer(self._packets([r]))
        assert "eligible to graduate" in answer.lower()
        assert "3.60" in answer

    def test_graduation_audit_cannot_graduate_with_gaps(self):
        r = PerSQResult(sq_index=0, intent="run_graduation_audit", status="success",
                        data={"can_graduate": False, "gaps": ["Missing 6 CH electives"],
                              "cgpa": 2.5})
        answer = _deterministic_answer(self._packets([r]))
        assert "not yet" in answer.lower()
        assert "Missing 6 CH electives" in answer

    def test_simulate_gpa_forward(self):
        r = PerSQResult(sq_index=0, intent="simulate_gpa_forward", status="success",
                        data={"current_cgpa": 2.9, "projected_cgpa": 3.1})
        answer = _deterministic_answer(self._packets([r]))
        assert "2.90" in answer
        assert "3.10" in answer

    def test_solve_target_gpa_already_met(self):
        r = PerSQResult(sq_index=0, intent="solve_target_gpa", status="success",
                        data={"already_met": True, "current_cgpa": 3.5, "target_cgpa": 3.0})
        answer = _deterministic_answer(self._packets([r]))
        assert "already met" in answer.lower()

    def test_policy_answer(self):
        r = PerSQResult(sq_index=0, intent="policy_query", status="success",
                        data={"answer": "Students may retake a failed course once.",
                              "extracted_facts": ["One retake allowed"]})
        answer = _deterministic_answer(self._packets([r]))
        assert "retake" in answer.lower()

    def test_course_info(self):
        r = PerSQResult(sq_index=0, intent="get_course_info", status="success",
                        data={"course_code": "C-CS301", "name": "Data Structures",
                              "credits": 3, "description": "Core data structures."})
        answer = _deterministic_answer(self._packets([r]))
        assert "C-CS301" in answer
        assert "Data Structures" in answer
        assert "3" in answer

    def test_get_role_profile(self):
        r = PerSQResult(sq_index=0, intent="get_role_profile", status="success",
                        data={"role_id": "ML-ENG", "name": "ML Engineer",
                              "required_skills": [{"name": "Python"}, {"name": "ML"}]})
        answer = _deterministic_answer(self._packets([r]))
        assert "ML Engineer" in answer
        assert "Python" in answer

    def test_find_best_matching_roles(self):
        r = PerSQResult(sq_index=0, intent="find_best_matching_roles", status="success",
                        data={"ranked_roles": [
                            {"role_id": "R1", "name": "Data Scientist",
                             "alignment_score": 0.85},
                            {"role_id": "R2", "name": "ML Engineer",
                             "alignment_score": 0.72},
                        ]})
        answer = _deterministic_answer(self._packets([r]))
        assert "Data Scientist" in answer
        assert "85%" in answer

    def test_assumptions_active_notice_in_answer(self):
        r = PerSQResult(sq_index=0, intent="plan_semester", status="success",
                        data={"recommended_courses": ["C-CS301"]},
                        assumptions_active=True)
        answer = _deterministic_answer(self._packets([r]))
        assert "what-if" in answer.lower() or "assumption" in answer.lower()

    def test_assumptions_excluded_notice_in_answer(self):
        r = PerSQResult(sq_index=0, intent="run_graduation_audit", status="success",
                        data={"can_graduate": False},
                        assumptions_excluded=True)
        answer = _deterministic_answer(self._packets([r]))
        assert "official" in answer.lower()

    def test_citations_appended(self):
        r = PerSQResult(sq_index=0, intent="policy_query", status="success",
                        data={"answer": "Policy says X.", "extracted_facts": ["X"]},
                        citations=[{"source": "Student Handbook 2024", "page": 45}])
        answer = _deterministic_answer(self._packets([r]))
        assert "Student Handbook 2024" in answer
        assert "p.45" in answer

    def test_multi_sq_all_results_appear(self):
        r1 = PerSQResult(sq_index=0, intent="run_graduation_audit", status="success",
                         data={"can_graduate": False, "cgpa": 3.0})
        r2 = PerSQResult(sq_index=1, intent="generate_graduation_roadmap", status="success",
                         data={"total_semesters": 3})
        answer = _deterministic_answer(self._packets([r1, r2]))
        assert "graduate" in answer.lower()
        assert "3" in answer  # total_semesters

    def test_multi_sq_order_preserved(self):
        """Results must appear in sq_index order."""
        r1 = PerSQResult(sq_index=0, intent="run_graduation_audit", status="success",
                         data={"can_graduate": True})
        r2 = PerSQResult(sq_index=1, intent="policy_query", status="success",
                         data={"answer": "Credit limit is 21.", "extracted_facts": ["21"]})
        packets = [_extract_packet(r1), _extract_packet(r2)]
        answer = _deterministic_answer(packets)
        idx_grad = answer.find("graduate")
        idx_policy = answer.find("Credit limit")
        assert idx_grad < idx_policy, "Graduation result must precede policy result"

    def test_empty_results_returns_fallback_message(self):
        answer = _deterministic_answer([])
        assert "unable" in answer.lower() or "try again" in answer.lower()


# ── _collect_citations ─────────────────────────────────────────────────────────

class TestCollectCitations:

    def test_basic_citation(self):
        r = PerSQResult(sq_index=0, intent="policy_query", status="success",
                        data={},
                        citations=[{"source": "Handbook", "page": 10}])
        citations = _collect_citations([r])
        assert len(citations) == 1
        assert citations[0].source == "Handbook"
        assert citations[0].page == 10

    def test_deduplication(self):
        r1 = PerSQResult(sq_index=0, intent="policy_query", status="success",
                         data={},
                         citations=[{"source": "Handbook", "page": 10}])
        r2 = PerSQResult(sq_index=1, intent="policy_query", status="success",
                         data={},
                         citations=[{"source": "Handbook", "page": 10}])
        citations = _collect_citations([r1, r2])
        assert len(citations) == 1

    def test_different_pages_not_deduped(self):
        r = PerSQResult(sq_index=0, intent="policy_query", status="success",
                        data={},
                        citations=[
                            {"source": "Handbook", "page": 10},
                            {"source": "Handbook", "page": 11},
                        ])
        citations = _collect_citations([r])
        assert len(citations) == 2

    def test_no_citations_returns_empty(self):
        r = PerSQResult(sq_index=0, intent="get_course_info", status="success", data={})
        citations = _collect_citations([r])
        assert citations == []

    def test_merge_across_results(self):
        r1 = PerSQResult(sq_index=0, intent="policy_query", status="success", data={},
                         citations=[{"source": "Handbook", "page": 5}])
        r2 = PerSQResult(sq_index=1, intent="policy_query", status="success", data={},
                         citations=[{"source": "Regulations", "page": 2}])
        citations = _collect_citations([r1, r2])
        sources = {c.source for c in citations}
        assert sources == {"Handbook", "Regulations"}

    def test_invalid_citation_entry_skipped(self):
        # PerSQResult.citations is list[dict] — Pydantic rejects strings at construction.
        # Use model_construct() to bypass validation and test the collector's guard directly.
        r = PerSQResult.model_construct(
            sq_index=0, intent="policy_query", status="success",
            data={}, citations=["not-a-dict"],
        )
        citations = _collect_citations([r])
        assert citations == []


# ── _map_turn_status ──────────────────────────────────────────────────────────

class TestMapTurnStatus:

    def _tw(self, turn_status: str) -> TurnWrapper:
        return _make_tw(
            [PerSQResult(sq_index=0, intent="get_course_info", status="success", data={})],
            turn_status=turn_status,
        )

    def test_completed_maps_to_ok(self):
        assert _map_turn_status(self._tw("completed")) == "ok"

    def test_partial_success_maps_to_ok(self):
        assert _map_turn_status(self._tw("partial_success")) == "ok"

    def test_out_of_scope_maps_to_ok(self):
        assert _map_turn_status(self._tw("out_of_scope")) == "ok"

    def test_needs_clarification_maps_to_clarification_needed(self):
        assert _map_turn_status(self._tw("needs_clarification")) == "clarification_needed"

    def test_failed_maps_to_error(self):
        assert _map_turn_status(self._tw("failed")) == "error"


# ── ResponseComposer — compose() ─────────────────────────────────────────────

class TestCompose:

    def _eligibility_tw(self, session_id: str = "s-001") -> TurnWrapper:
        return _make_tw(
            [PerSQResult(sq_index=0, intent="check_course_eligibility", status="success",
                         data={"eligible": False, "target_course_code": "C-CS401",
                               "reason": "prereq_not_met",
                               "missing_prerequisites": ["C-CS301"]})],
            session_id=session_id,
        )

    def test_compose_returns_query_response(self):
        composer = _make_composer(use_llm=False)
        tw = self._eligibility_tw()
        qr = composer.compose("Am I eligible for C-CS401?", tw, "s-001", "Chat 1")
        assert isinstance(qr, QueryResponse)

    def test_compose_session_metadata_propagated(self):
        composer = _make_composer(use_llm=False)
        tw = self._eligibility_tw("sid-xyz")
        qr = composer.compose("query", tw, "sid-xyz", "My Session")
        assert qr.session_id == "sid-xyz"
        assert qr.session_name == "My Session"

    def test_compose_answer_text_non_empty(self):
        composer = _make_composer(use_llm=False)
        tw = self._eligibility_tw()
        qr = composer.compose("Am I eligible?", tw, "s-001", "Chat")
        assert isinstance(qr.answer_text, str) and len(qr.answer_text) > 0

    def test_compose_status_ok_for_success(self):
        composer = _make_composer(use_llm=False)
        tw = self._eligibility_tw()
        qr = composer.compose("query", tw, "s", "n")
        assert qr.status == "ok"

    def test_compose_status_error_for_failed_turn(self):
        composer = _make_composer(use_llm=False)
        tw = _make_tw(
            [PerSQResult(sq_index=0, intent="get_course_info", status="error",
                         error_detail="KG unavailable.", error_code="kg_unavailable")],
            turn_status="failed",
        )
        qr = composer.compose("query", tw, "s", "n")
        assert qr.status == "error"

    def test_compose_status_clarification_needed(self):
        composer = _make_composer(use_llm=False)
        tw = _make_tw(
            [PerSQResult(sq_index=0, intent="check_course_eligibility",
                         status="clarification_needed",
                         clarification_prompt="Which course?")],
            turn_status="needs_clarification",
        )
        qr = composer.compose("query", tw, "s", "n")
        assert qr.status == "clarification_needed"
        assert "Which course?" in qr.answer_text

    def test_compose_with_llm_success(self):
        """LLM answer replaces deterministic when LLM succeeds."""
        mock_llm = MagicMock()
        mock_llm.is_configured.return_value = True
        mock_llm.chat.return_value = "You are not eligible for C-CS401 because you haven't completed C-CS301."

        composer = _make_composer(use_llm=True)
        composer._llm = mock_llm

        tw = self._eligibility_tw()
        qr = composer.compose("Am I eligible?", tw, "s-001", "Chat")
        assert "C-CS301" in qr.answer_text
        assert mock_llm.chat.called

    def test_compose_llm_failure_falls_back(self):
        """If all LLM models fail, the deterministic fallback is used."""
        from gateway.llm_client import LLMError
        mock_llm = MagicMock()
        mock_llm.is_configured.return_value = True
        mock_llm.chat.side_effect = LLMError("timeout")

        composer = _make_composer(use_llm=True)
        composer._llm = mock_llm
        composer._fallbacks = []  # no fallbacks so we fail fast

        tw = self._eligibility_tw()
        qr = composer.compose("Am I eligible?", tw, "s-001", "Chat")
        # Must still return a valid answer (deterministic)
        assert isinstance(qr.answer_text, str) and len(qr.answer_text) > 0

    def test_compose_llm_not_configured_falls_back(self):
        """LLMNotConfigured is treated as 'skip LLM entirely'."""
        from gateway.llm_client import LLMNotConfigured
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = LLMNotConfigured("no key")

        composer = _make_composer(use_llm=True)
        composer._llm = mock_llm

        tw = self._eligibility_tw()
        qr = composer.compose("Am I eligible?", tw, "s-001", "Chat")
        assert isinstance(qr.answer_text, str) and len(qr.answer_text) > 0

    def test_compose_use_llm_false_skips_llm(self):
        """COMPOSER_USE_LLM=false must skip LLM entirely."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "LLM answer"

        composer = _make_composer(use_llm=False)
        composer._llm = mock_llm

        tw = self._eligibility_tw()
        composer.compose("query", tw, "s", "n")
        mock_llm.chat.assert_not_called()

    def test_compose_citations_in_response(self):
        composer = _make_composer(use_llm=False)
        tw = _make_tw([PerSQResult(
            sq_index=0, intent="policy_query", status="success",
            data={"answer": "Max 21 CH per semester.",
                  "extracted_facts": ["Max 21 CH"]},
            citations=[{"source": "Student Handbook 2024", "page": 15}],
        )])
        qr = composer.compose("Credit limit?", tw, "s", "n")
        assert len(qr.citations) == 1
        assert qr.citations[0].source == "Student Handbook 2024"
        assert qr.citations[0].page == 15

    def test_compose_citation_dedup_across_results(self):
        composer = _make_composer(use_llm=False)
        tw = _make_tw([
            PerSQResult(sq_index=0, intent="policy_query", status="success",
                        data={"answer": "A", "extracted_facts": ["A"]},
                        citations=[{"source": "Handbook", "page": 5}]),
            PerSQResult(sq_index=1, intent="policy_query", status="success",
                        data={"answer": "B", "extracted_facts": ["B"]},
                        citations=[{"source": "Handbook", "page": 5}]),
        ])
        qr = composer.compose("Policy?", tw, "s", "n")
        assert len(qr.citations) == 1

    def test_compose_multi_sq_ordering(self):
        """Results must appear in sq_index order even if passed reversed."""
        composer = _make_composer(use_llm=False)
        # Pass results in reverse order — Composer must sort by sq_index
        r0 = PerSQResult(sq_index=0, intent="run_graduation_audit", status="success",
                         data={"can_graduate": True, "cgpa": 3.5})
        r1 = PerSQResult(sq_index=1, intent="policy_query", status="success",
                         data={"answer": "Credit limit is 21 CH.", "extracted_facts": ["21"]})
        tw = _make_tw([r1, r0])  # reversed in list
        qr = composer.compose("query", tw, "s", "n")
        idx_audit = qr.answer_text.find("graduate")
        idx_policy = qr.answer_text.find("21")
        assert idx_audit < idx_policy, (
            "run_graduation_audit (sq_index=0) must appear before policy_query (sq_index=1)"
        )

    def test_compose_out_of_scope_answer_explains(self):
        composer = _make_composer(use_llm=False)
        tw = _make_tw(
            [PerSQResult(sq_index=0, intent="out_of_scope", status="out_of_scope",
                         scope_explanation="PathFinder covers academic and career advising only.")],
            turn_status="out_of_scope",
        )
        qr = composer.compose("How do I apply for a loan?", tw, "s", "n")
        assert qr.status == "ok"  # out_of_scope → ok (with polite explanation)
        assert "PathFinder" in qr.answer_text or "advising" in qr.answer_text.lower()

    def test_compose_informational_status_ok(self):
        composer = _make_composer(use_llm=False)
        tw = _make_tw(
            [PerSQResult(sq_index=0, intent="plan_semester", status="informational",
                         data={"status": "cannot_compute",
                               "message": "Cannot compute: CGPA data missing."})],
            turn_status="completed",
        )
        qr = composer.compose("query", tw, "s", "n")
        assert qr.status == "ok"
        assert "cannot" in qr.answer_text.lower() or "CGPA" in qr.answer_text

    def test_compose_soft_no_evidence_mentions_limited(self):
        composer = _make_composer(use_llm=False)
        tw = _make_tw(
            [PerSQResult(sq_index=0, intent="policy_query", status="soft_no_evidence",
                         data={"answer": "Some general info.", "extracted_facts": []})],
            turn_status="completed",
        )
        qr = composer.compose("Tell me about retakes.", tw, "s", "n")
        assert "limited" in qr.answer_text.lower()

    def test_compose_llm_model_chain_tries_primary_then_fallback(self):
        """If primary model fails, the first fallback must be tried."""
        from gateway.llm_client import LLMError
        mock_llm = MagicMock()
        call_count = {"n": 0}

        def side_effect(system, user, *, temperature, model):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise LLMError("primary failed")
            return "Fallback answer from second model."

        mock_llm.chat.side_effect = side_effect

        composer = _make_composer(use_llm=True)
        composer._llm = mock_llm
        composer._fallbacks = ["llama-3.1-8b-instant"]

        tw = self._eligibility_tw()
        qr = composer.compose("Am I eligible?", tw, "s", "n")
        assert mock_llm.chat.call_count == 2
        assert "Fallback answer" in qr.answer_text


# ── Sample outputs (integration-style, deterministic path) ────────────────────

class TestSampleOutputs:
    """Three sample outputs required by the spec."""

    def test_sample_eligibility(self):
        """Sample 1: course eligibility answer."""
        composer = _make_composer(use_llm=False)
        tw = _make_tw([PerSQResult(
            sq_index=0, intent="check_course_eligibility", status="success",
            data={
                "eligible": False,
                "target_course_code": "C-CS401",
                "reason": "prereq_not_met",
                "missing_prerequisites": ["C-CS301", "C-CS302"],
                "attempt_type": "first_attempt",
            }
        )])
        qr = composer.compose(
            "Can I take C-CS401 this semester?", tw, "s-001", "Academic Session"
        )
        print("\n[SAMPLE 1 - eligibility]\n", qr.answer_text.encode("ascii", "replace").decode())
        assert qr.status == "ok"
        assert "C-CS401" in qr.answer_text
        assert "C-CS301" in qr.answer_text
        assert "C-CS302" in qr.answer_text
        assert "eligible" in qr.answer_text.lower()

    def test_sample_graduation_gpa(self):
        """Sample 2: graduation audit + GPA solve answer."""
        composer = _make_composer(use_llm=False)
        tw = _make_tw([
            PerSQResult(sq_index=0, intent="run_graduation_audit", status="success",
                        data={"can_graduate": False,
                              "gaps": ["6 credit hours of free electives"],
                              "cgpa": 2.85, "cgpa_required": 2.0},
                        assumptions_excluded=True),
            PerSQResult(sq_index=1, intent="solve_target_gpa", status="success",
                        data={"current_cgpa": 2.85, "target_cgpa": 3.0,
                              "semesters_needed": 2,
                              "required_gpa_each_semester": 3.8}),
        ])
        qr = composer.compose(
            "Can I graduate? What GPA do I need to reach 3.0?",
            tw, "s-002", "Session 2"
        )
        print("\n[SAMPLE 2 - graduation/GPA]\n", qr.answer_text.encode("ascii", "replace").decode())
        assert qr.status == "ok"
        assert "2.85" in qr.answer_text
        assert "official" in qr.answer_text.lower()  # assumptions_excluded notice

    def test_sample_policy_with_citations(self):
        """Sample 3: policy answer with citations."""
        composer = _make_composer(use_llm=False)
        tw = _make_tw([PerSQResult(
            sq_index=0, intent="policy_query", status="success",
            data={
                "answer": (
                    "Students may retake a failed course once for grade improvement. "
                    "The higher grade replaces the original in CGPA calculation."
                ),
                "extracted_facts": [
                    "One improve-retake allowed per course.",
                    "Higher grade replaces original in CGPA.",
                ],
            },
            citations=[
                {"source": "EUI Student Handbook 2024", "page": 34},
                {"source": "EUI Academic Regulations 2024", "page": 8},
            ],
        )])
        qr = composer.compose(
            "Can I retake a course I failed to improve my grade?",
            tw, "s-003", "Policy Session"
        )
        print("\n[SAMPLE 3 - policy + citations]\n", qr.answer_text.encode("ascii", "replace").decode())
        assert qr.status == "ok"
        assert "retake" in qr.answer_text.lower()
        assert len(qr.citations) == 2
        sources = {c.source for c in qr.citations}
        assert "EUI Student Handbook 2024" in sources
        assert "Student Handbook 2024" in qr.answer_text or "p.34" in qr.answer_text
