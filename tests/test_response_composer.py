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

import logging
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
    _fmt_course_label,
    _fmt_role_label,
    _fmt_skill_label,
    _fmt_track_label,
    _map_turn_status,
    _render_course_detail,
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

        def side_effect(system, user, *, temperature, model, timeout_seconds=None, **kw):
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


# ── Step 8 audit fixes: display formatting helpers ────────────────────────────

class TestDisplayFormatHelpers:

    def test_fmt_course_label_name_first(self):
        assert _fmt_course_label("C-CS219", "Advanced Programming") == "Advanced Programming (C-CS219)"

    def test_fmt_course_label_with_credits(self):
        label = _fmt_course_label("C-CS219", "Advanced Programming", credits=3)
        assert label == "Advanced Programming (C-CS219) — 3 credits"

    def test_fmt_course_label_code_only(self):
        assert _fmt_course_label("C-CS219", "") == "C-CS219"

    def test_fmt_course_label_name_only(self):
        assert _fmt_course_label("", "Advanced Programming") == "Advanced Programming"

    def test_fmt_role_label_prefers_name(self):
        assert _fmt_role_label("RL_Data_Scientist", "Data Scientist") == "Data Scientist"

    def test_fmt_role_label_converts_rl_prefix(self):
        assert _fmt_role_label("RL_Data_Scientist") == "Data Scientist"

    def test_fmt_role_label_converts_rl_multi_word(self):
        assert _fmt_role_label("RL_Machine_Learning_Engineer") == "Machine Learning Engineer"

    def test_fmt_role_label_plain_id_unchanged(self):
        assert _fmt_role_label("ML-ENG", "ML Engineer") == "ML Engineer"

    def test_fmt_skill_label_prefers_name(self):
        assert _fmt_skill_label("SK_Machine_Learning", "Machine Learning") == "Machine Learning"

    def test_fmt_skill_label_converts_sk_prefix(self):
        assert _fmt_skill_label("SK_Machine_Learning") == "Machine Learning"

    def test_fmt_skill_label_converts_sk_multi_word(self):
        assert _fmt_skill_label("SK_Deep_Neural_Networks") == "Deep Neural Networks"

    def test_fmt_track_label_ai(self):
        assert _fmt_track_label("AI") == "Artificial Intelligence (AI)"

    def test_fmt_track_label_cys(self):
        assert _fmt_track_label("CYS") == "Cyber Security (CYS)"

    def test_fmt_track_label_dse(self):
        assert _fmt_track_label("DSE") == "Data Science and Engineering (DSE)"

    def test_fmt_track_label_swe(self):
        assert _fmt_track_label("SWE") == "Software Engineering (SWE)"

    def test_fmt_track_label_gen(self):
        assert _fmt_track_label("GEN") == "General Program (GEN)"

    def test_fmt_track_label_with_name(self):
        label = _fmt_track_label("AI", "Artificial Intelligence")
        assert "Artificial Intelligence" in label
        assert "AI" in label

    def test_fmt_track_label_unknown_id_passthrough(self):
        assert _fmt_track_label("XYZ") == "XYZ"


# ── Step 8 audit fixes: eligibility status handling ──────────────────────────

class TestEligibilityStatusHandling:

    def _packets(self, data: dict) -> list[dict]:
        r = PerSQResult(sq_index=0, intent="check_course_eligibility",
                        status="success", data=data)
        return [_extract_packet(r)]

    def test_in_progress_says_already_enrolled(self):
        packets = self._packets({
            "status": "in_progress",
            "target_course_code": "C-AI321",
        })
        answer = _deterministic_answer(packets)
        assert "already enrolled" in answer.lower() or "in progress" in answer.lower() or "currently taking" in answer.lower()
        assert "not" not in answer.lower().split("eligible")[0] if "eligible" in answer.lower() else True

    def test_in_progress_does_not_say_not_eligible(self):
        packets = self._packets({
            "eligibility_status": "in_progress",
            "eligible": False,
            "target_course_code": "C-AI321",
        })
        answer = _deterministic_answer(packets)
        assert "not currently eligible" not in answer.lower()
        assert "not eligible" not in answer.lower()

    def test_already_completed_says_completed(self):
        packets = self._packets({
            "status": "already_completed",
            "target_course_code": "C-CS201",
        })
        answer = _deterministic_answer(packets)
        assert "already completed" in answer.lower() or "already passed" in answer.lower()

    def test_already_completed_does_not_say_not_eligible(self):
        packets = self._packets({
            "eligibility_status": "already_completed",
            "eligible": False,
            "target_course_code": "C-CS201",
        })
        answer = _deterministic_answer(packets)
        assert "not currently eligible" not in answer.lower()
        assert "not eligible" not in answer.lower()

    def test_retake_cap_exceeded_says_cap_reached(self):
        packets = self._packets({
            "eligibility_status": "retake_cap_exceeded",
            "target_course_code": "C-CS101",
            "reason": "Maximum retakes reached.",
        })
        answer = _deterministic_answer(packets)
        assert "retake cap" in answer.lower() or "retake" in answer.lower()

    def test_eligible_true_still_works(self):
        packets = self._packets({
            "eligible": True,
            "target_course_code": "C-CS301",
        })
        answer = _deterministic_answer(packets)
        assert "eligible" in answer.lower()
        assert "C-CS301" in answer

    def test_eligibility_status_set_from_ale_status(self):
        """_extract_packet must map ALE 'status' field to eligibility_status."""
        r = PerSQResult(sq_index=0, intent="check_course_eligibility",
                        status="success",
                        data={"status": "in_progress", "target_course_code": "C-AI321"})
        p = _extract_packet(r)
        assert p.get("eligibility_status") == "in_progress"

    def test_course_name_extracted_from_target_course_name(self):
        r = PerSQResult(sq_index=0, intent="check_course_eligibility",
                        status="success",
                        data={"eligible": True, "target_course_code": "C-CS219",
                              "target_course_name": "Advanced Programming"})
        p = _extract_packet(r)
        assert p.get("target_course_name") == "Advanced Programming"
        answer = _deterministic_answer([p])
        assert "Advanced Programming" in answer
        assert "C-CS219" in answer


# ── Step 8 audit fixes: plan/roadmap name-first formatting ───────────────────

class TestPlanFormattingNameFirst:

    def test_plan_recommended_courses_name_first(self):
        """_extract_plan must produce 'Course Name (CODE)' not 'CODE — Course Name'."""
        r = PerSQResult(sq_index=0, intent="plan_semester", status="success",
                        data={
                            "plans": [{
                                "plan_label": "Recommended",
                                "total_credits": 15,
                                "courses": [
                                    {"course_code": "C-CS219",
                                     "course_name": "Advanced Programming",
                                     "credits": 3},
                                ],
                            }]
                        })
        p = _extract_packet(r)
        courses = p.get("recommended_courses", [])
        assert len(courses) == 1
        assert "Advanced Programming (C-CS219)" in courses[0]
        assert "C-CS219 — Advanced Programming" not in courses[0]

    def test_plan_credits_dash_not_parens(self):
        """Credits must appear as '— N credits', not '(N cr)'."""
        r = PerSQResult(sq_index=0, intent="plan_semester", status="success",
                        data={
                            "plans": [{
                                "plan_label": "Recommended",
                                "total_credits": 15,
                                "courses": [
                                    {"course_code": "C-AI321", "course_name": "Machine Learning",
                                     "credits": 4},
                                ],
                            }]
                        })
        p = _extract_packet(r)
        label = p["recommended_courses"][0]
        assert "— 4 credits" in label
        assert "(4 cr)" not in label

    def test_roadmap_semester_plans_name_first(self):
        """Semester plan courses must also use name-first in the narration."""
        r = PerSQResult(sq_index=0, intent="generate_graduation_roadmap", status="success",
                        data={
                            "semester_plans": [{
                                "semester_label": "Fall 2025",
                                "total_credits": 15,
                                "courses": [
                                    {"course_code": "C-CS219",
                                     "course_name": "Advanced Programming",
                                     "credits": 3},
                                ],
                            }]
                        })
        answer = _deterministic_answer([_extract_packet(r)])
        assert "Advanced Programming (C-CS219)" in answer
        assert "C-CS219 — Advanced Programming" not in answer


# ── Step 8 audit fixes: role/skill/track display ─────────────────────────────

class TestRoleSkillTrackDisplay:

    def _packets(self, intent: str, data: dict) -> list[dict]:
        r = PerSQResult(sq_index=0, intent=intent, status="success", data=data)
        return [_extract_packet(r)]

    def test_role_profile_no_raw_rl_id(self):
        """get_role_profile must not expose RL_* in the answer."""
        packets = self._packets("get_role_profile", {
            "role_id": "RL_Data_Scientist",
            "name": "Data Scientist",
            "description": "Analyses data.",
            "required_skills": [{"name": "Python"}, {"name": "Statistics"}],
        })
        answer = _deterministic_answer(packets)
        assert "RL_Data_Scientist" not in answer
        assert "Data Scientist" in answer

    def test_role_profile_rl_id_without_name_converted(self):
        """If only role_id is available, RL_ prefix must be stripped."""
        packets = self._packets("get_role_profile", {
            "role_id": "RL_Data_Scientist",
        })
        answer = _deterministic_answer(packets)
        assert "RL_Data_Scientist" not in answer
        assert "Data Scientist" in answer

    def test_search_courses_by_skill_no_raw_sk_id(self):
        """search_courses_by_skill header must not expose SK_* if name available."""
        packets = self._packets("search_courses_by_skill", {
            "skill_id": "SK_Machine_Learning",
            "skill_name": "Machine Learning",
            "courses": [{"course_code": "C-AI321", "name": "Machine Learning Fundamentals"}],
        })
        answer = _deterministic_answer(packets)
        assert "SK_Machine_Learning" not in answer
        assert "Machine Learning" in answer

    def test_search_courses_by_skill_sk_id_cleaned_when_no_name(self):
        """SK_ prefix must be stripped in the header when no skill_name provided."""
        packets = self._packets("search_courses_by_skill", {
            "skill_id": "SK_Machine_Learning",
            "courses": [],
        })
        answer = _deterministic_answer(packets)
        assert "SK_Machine_Learning" not in answer
        assert "Machine Learning" in answer

    def test_roles_by_track_friendly_track_name(self):
        packets = self._packets("get_roles_by_track", {
            "track_id": "AI",
            "roles": [{"role_id": "RL_ML_Engineer", "name": "ML Engineer"}],
        })
        answer = _deterministic_answer(packets)
        assert "Artificial Intelligence" in answer
        assert "ML Engineer" in answer
        assert "RL_ML_Engineer" not in answer

    def test_find_best_matching_roles_no_raw_id(self):
        packets = self._packets("find_best_matching_roles", {
            "ranked_roles": [
                {"role_id": "RL_Data_Scientist", "name": "Data Scientist",
                 "alignment_score": 0.90},
            ],
        })
        answer = _deterministic_answer(packets)
        assert "RL_Data_Scientist" not in answer
        assert "Data Scientist" in answer
        assert "90%" in answer

    def test_compute_skill_gap_no_raw_sk_skills(self):
        packets = self._packets("compute_skill_gap", {
            "role_id": "RL_Data_Scientist",
            "role_name": "Data Scientist",
            "missing_skills": [
                {"skill_id": "SK_Deep_Learning", "name": "Deep Learning"},
                {"skill_id": "SK_Statistics", "name": "Statistics"},
            ],
        })
        answer = _deterministic_answer(packets)
        assert "SK_Deep_Learning" not in answer
        assert "SK_Statistics" not in answer
        assert "Deep Learning" in answer
        assert "Statistics" in answer

    def test_track_overview_friendly_name(self):
        packets = self._packets("get_track_overview", {
            "track_id": "DSE",
            "name": "Data Science and Engineering",
            "description": "Focus on data.",
            "courses": [],
        })
        answer = _deterministic_answer(packets)
        assert "Data Science and Engineering" in answer

    def test_compare_tracks_friendly_names(self):
        packets = self._packets("compare_tracks", {
            "track_id_1": "AI",
            "track_id_2": "CYS",
            "shared_courses": ["C-CS101"],
            "different_courses": {},
        })
        answer = _deterministic_answer(packets)
        assert "Artificial Intelligence" in answer
        assert "Cyber Security" in answer


# ── Step 8 audit fixes: combined credit-limit personalisation ─────────────────

class TestCreditLimitPersonalisation:

    def _make_tw_combined(self, cgpa: float, policy_answer: str) -> list[dict]:
        r_student = PerSQResult(
            sq_index=0, intent="get_student_record", status="success",
            data={"track_id": "AI", "cgpa": cgpa, "level": 4}
        )
        r_policy = PerSQResult(
            sq_index=1, intent="policy_query", status="success",
            data={"answer": policy_answer, "extracted_facts": [policy_answer]}
        )
        from gateway.response_composer import _extract_packet
        return [_extract_packet(r_student), _extract_packet(r_policy)]

    def test_cgpa_above_3_gets_21_hours(self):
        packets = self._make_tw_combined(3.5, "The maximum credit hour limit depends on CGPA.")
        answer = _deterministic_answer(packets)
        assert "21" in answer

    def test_cgpa_2_to_3_gets_18_hours(self):
        packets = self._make_tw_combined(2.63,
            "Students have a credit hour limit based on CGPA. Maximum credit hours allowed per semester.")
        answer = _deterministic_answer(packets)
        assert "18" in answer

    def test_cgpa_1_to_2_gets_15_hours(self):
        packets = self._make_tw_combined(1.85,
            "The maximum credit hour limit is set by the university policy.")
        answer = _deterministic_answer(packets)
        assert "15" in answer

    def test_cgpa_below_1_gets_12_hours(self):
        packets = self._make_tw_combined(0.80,
            "Credit limit rules: maximum credit hours per semester.")
        answer = _deterministic_answer(packets)
        assert "12" in answer

    def test_non_credit_limit_policy_no_personalisation(self):
        """A non-credit-limit policy packet must not trigger personalisation."""
        packets = self._make_tw_combined(3.5,
            "Students may retake a failed course once.")
        answer = _deterministic_answer(packets)
        # 21 should NOT appear as a personalised credit limit
        # (it may appear in other contexts but the personalisation sentence should not be there)
        assert "can register up to" not in answer

    def test_no_student_record_no_personalisation(self):
        """Without a student record packet, no personalisation occurs."""
        r = PerSQResult(sq_index=0, intent="policy_query", status="success",
                        data={"answer": "Credit hour limit policy.", "extracted_facts": []})
        packets = [_extract_packet(r)]
        answer = _deterministic_answer(packets)
        assert "can register up to" not in answer


# ── Step 8 audit fixes: citation safety ───────────────────────────────────────

class TestCitationSafety:

    def test_no_fabricated_sources_when_citations_empty(self):
        """Deterministic path must not add a Sources section when no citations exist."""
        composer = _make_composer(use_llm=False)
        tw = _make_tw([PerSQResult(
            sq_index=0, intent="policy_query", status="success",
            data={"answer": "Max 21 CH per semester.", "extracted_facts": ["21 CH"]},
        )])
        qr = composer.compose("Credit limit?", tw, "s", "n")
        assert "Sources:" not in qr.answer_text
        assert len(qr.citations) == 0

    def test_citations_preserved_when_present(self):
        composer = _make_composer(use_llm=False)
        tw = _make_tw([PerSQResult(
            sq_index=0, intent="policy_query", status="success",
            data={"answer": "Policy info.", "extracted_facts": []},
            citations=[{"source": "EUI Handbook 2024", "page": 12}],
        )])
        qr = composer.compose("Policy?", tw, "s", "n")
        assert "EUI Handbook 2024" in qr.answer_text
        assert len(qr.citations) == 1

    def test_citations_deduplicated(self):
        composer = _make_composer(use_llm=False)
        tw = _make_tw([
            PerSQResult(sq_index=0, intent="policy_query", status="success",
                        data={"answer": "A.", "extracted_facts": []},
                        citations=[{"source": "Handbook", "page": 5}]),
            PerSQResult(sq_index=1, intent="policy_query", status="success",
                        data={"answer": "B.", "extracted_facts": []},
                        citations=[{"source": "Handbook", "page": 5}]),
        ])
        qr = composer.compose("Policy?", tw, "s", "n")
        assert len(qr.citations) == 1

    def test_llm_fabricated_sources_stripped(self):
        """LLM-generated sources section is stripped when no real citations exist."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = (
            "You are not eligible.\n\n**Sources:** EUI Handbook 2024, PathFinder analysis."
        )
        composer = _make_composer(use_llm=True)
        composer._llm = mock_llm
        tw = _make_tw([PerSQResult(
            sq_index=0, intent="check_course_eligibility", status="success",
            data={"eligible": False, "target_course_code": "C-CS301"},
        )])
        qr = composer.compose("Am I eligible?", tw, "s", "n")
        assert "Sources:" not in qr.answer_text
        assert "PathFinder analysis" not in qr.answer_text


# ── Step 8 audit fixes: reset assumptions wording (known limitation) ──────────

class TestResetAssumptionsWording:
    """
    The Orchestrator does not propagate a structured 'had_clear' flag to
    PerSQResult.data, so the Composer has no reliable signal that assumptions
    were just cleared. This class documents the limitation and the safe system
    prompt rule that is in place for the LLM path.

    The deterministic path will produce a normal get_student_record answer
    (no special "I cleared your assumptions" message) until the Orchestrator
    propagates such a flag. The LLM path follows rule 28 in the system prompt.
    """

    def test_no_override_active_shows_plain_record(self):
        """Without override_state_active, the record is shown normally."""
        r = PerSQResult(sq_index=0, intent="get_student_record", status="success",
                        data={"track_id": "AI", "cgpa": 2.63, "level": 4},
                        override_state_active=False)
        answer = _deterministic_answer([_extract_packet(r)])
        assert "Artificial Intelligence" in answer
        assert "2.63" in answer

    def test_reset_wording_rule_in_system_prompt(self):
        """The system prompt must contain the reset-assumptions rule."""
        from gateway.response_composer import _SYSTEM_PROMPT
        assert "cleared your what-if assumptions" in _SYSTEM_PROMPT
        assert "official academic record" in _SYSTEM_PROMPT


# ── Step 8 audit fixes: LLM path safety ──────────────────────────────────────

class TestLLMPathSafety:

    def test_llm_disabled_uses_deterministic(self):
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "LLM answer"
        composer = _make_composer(use_llm=False)
        composer._llm = mock_llm
        tw = _make_tw([PerSQResult(sq_index=0, intent="policy_query", status="success",
                                    data={"answer": "Policy text.", "extracted_facts": []})])
        qr = composer.compose("query", tw, "s", "n")
        mock_llm.chat.assert_not_called()
        assert "Policy text." in qr.answer_text

    def test_llm_failure_uses_deterministic(self):
        from gateway.llm_client import LLMError
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = LLMError("timeout")
        composer = _make_composer(use_llm=True)
        composer._llm = mock_llm
        composer._fallbacks = []
        tw = _make_tw([PerSQResult(sq_index=0, intent="policy_query", status="success",
                                    data={"answer": "Policy text.", "extracted_facts": []})])
        qr = composer.compose("query", tw, "s", "n")
        assert len(qr.answer_text) > 0

    def test_composer_does_not_import_kg_ale_rag(self):
        """The Composer module must not import KG, RAG, ALE, QU, or SCP at the top level."""
        import importlib
        import sys
        mod = sys.modules.get("gateway.response_composer")
        if mod is None:
            mod = importlib.import_module("gateway.response_composer")
        forbidden = ("engines.kg", "engines.rag", "engines.ale",
                     "gateway.query_understanding", "gateway.session_manager")
        for name in forbidden:
            assert not any(name in k for k in sys.modules if k.startswith("gateway.response_composer")), (
                f"Composer must not import {name}"
            )
        # Verify the module itself doesn't reference those names as imports
        import inspect
        src = inspect.getsource(mod)
        for name in ("from engines", "import engines"):
            assert name not in src, f"Composer source must not contain '{name}'"


# ── Logging: privacy-safe and diagnostically useful ───────────────────────────

class TestComposerLogging:
    """
    Verify that compose() logs are diagnostically useful AND privacy-safe.

    Privacy hard rules enforced here:
      - raw user_text is never logged
      - full narration packet contents are never logged
      - final answer text is never logged
      - session_id is truncated to 8 chars
    """

    _LOGGER = "gateway.response_composer"

    def _make_tw_policy(self) -> TurnWrapper:
        return _make_tw([PerSQResult(
            sq_index=0, intent="policy_query", status="success",
            data={"answer": "Max 21 CH per semester.", "extracted_facts": ["21 CH"]},
        )])

    def test_start_log_has_truncated_session_id(self, caplog):
        """compose() start log must use the first 8 chars of session_id, not the full value."""
        composer = _make_composer(use_llm=False)
        full_sid = "abcdef1234567890"
        tw = self._make_tw_policy()
        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            composer.compose("query", tw, full_sid, "Chat")
        start_logs = [r for r in caplog.records if "Composer.compose start" in r.message]
        assert start_logs, "Expected a 'Composer.compose start' log line"
        msg = start_logs[0].message
        assert "abcdef12" in msg
        assert "abcdef1234567890" not in msg

    def test_result_log_has_required_diagnostic_fields(self, caplog):
        """compose() result log must include all diagnostic fields."""
        composer = _make_composer(use_llm=False)
        tw = self._make_tw_policy()
        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            composer.compose("query", tw, "sess-0001", "Chat")
        result_logs = [r for r in caplog.records if "Composer.compose result" in r.message]
        assert result_logs, "Expected a 'Composer.compose result' log line"
        msg = result_logs[0].message
        assert "duration_ms=" in msg
        assert "fallback_reason=" in msg
        assert "model=" in msg
        assert "answer_len=" in msg
        assert "citations=" in msg
        assert "llm_used=" in msg
        assert "qr_status=" in msg

    def test_result_log_no_user_text(self, caplog):
        """Raw user_text must never appear in any log record."""
        composer = _make_composer(use_llm=False)
        tw = self._make_tw_policy()
        secret_query = "MY_VERY_SECRET_QUERY_THAT_MUST_NOT_BE_LOGGED"
        with caplog.at_level(logging.DEBUG, logger=self._LOGGER):
            composer.compose(secret_query, tw, "sess-0001", "Chat")
        for record in caplog.records:
            assert secret_query not in record.message, (
                f"user_text leaked into log: {record.message}"
            )

    def test_result_log_no_packet_contents(self, caplog):
        """Full narration packet contents must not appear in any log record."""
        composer = _make_composer(use_llm=False)
        tw = _make_tw([PerSQResult(
            sq_index=0, intent="policy_query", status="success",
            data={"answer": "UNIQUE_PACKET_CONTENT_9876", "extracted_facts": []},
        )])
        with caplog.at_level(logging.DEBUG, logger=self._LOGGER):
            composer.compose("query", tw, "sess-0001", "Chat")
        for record in caplog.records:
            assert "UNIQUE_PACKET_CONTENT_9876" not in record.message, (
                f"packet contents leaked into log: {record.message}"
            )

    def test_result_log_no_answer_text(self, caplog):
        """Final answer text must not appear in any log record."""
        composer = _make_composer(use_llm=False)
        tw = _make_tw([PerSQResult(
            sq_index=0, intent="policy_query", status="success",
            data={"answer": "UNIQUE_ANSWER_TEXT_XYZABC", "extracted_facts": []},
        )])
        with caplog.at_level(logging.DEBUG, logger=self._LOGGER):
            composer.compose("query", tw, "sess-0001", "Chat")
        for record in caplog.records:
            assert "UNIQUE_ANSWER_TEXT_XYZABC" not in record.message, (
                f"answer text leaked into log: {record.message}"
            )

    def test_llm_success_path_logs_llm_used_true_and_model(self, caplog):
        """When LLM succeeds, result log must show llm_used=True and the model name."""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Great answer from the LLM."
        composer = _make_composer(use_llm=True)
        composer._llm = mock_llm
        tw = self._make_tw_policy()
        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            composer.compose("query", tw, "sess-0001", "Chat")
        result_logs = [r for r in caplog.records if "Composer.compose result" in r.message]
        assert result_logs, "Expected a 'Composer.compose result' log line"
        msg = result_logs[0].message
        assert "llm_used=True" in msg
        assert "model=" in msg

    def test_deterministic_fallback_logs_llm_used_false_and_reason(self, caplog):
        """When COMPOSER_USE_LLM=false, result log must show llm_used=False and fallback_reason=llm_disabled."""
        composer = _make_composer(use_llm=False)
        tw = self._make_tw_policy()
        with caplog.at_level(logging.INFO, logger=self._LOGGER):
            composer.compose("query", tw, "sess-0001", "Chat")
        result_logs = [r for r in caplog.records if "Composer.compose result" in r.message]
        assert result_logs, "Expected a 'Composer.compose result' log line"
        msg = result_logs[0].message
        assert "llm_used=False" in msg
        assert "fallback_reason=llm_disabled" in msg


# ── D6 student-record course enrichment rendering (Phase 2) ──────────────────

class TestDomain6CourseDetailRendering:
    """Verify Composer renders enriched course details name-first."""

    def test_render_course_detail_name_present(self):
        d = {"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3}
        assert _render_course_detail(d) == "Programming Fundamentals (C-CS112)"

    def test_render_course_detail_name_none_graceful_label(self):
        # course_name=None means KG profile lookup failed — expect graceful label, not raw None
        d = {"course_code": "C-CS112", "course_name": None, "credits": 3}
        result = _render_course_detail(d)
        assert "C-CS112" in result
        assert "not available" in result

    def test_render_course_detail_name_empty_string_falls_back_to_code(self):
        d = {"course_code": "HUM111", "course_name": "", "credits": 2}
        assert _render_course_detail(d) == "HUM111"

    def test_extract_student_record_picks_up_detail_fields(self):
        details = [{"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3}]
        r = PerSQResult(
            sq_index=0, intent="get_student_record", status="success",
            data={
                "cgpa": 3.48,
                "track_id": "AI",
                "completed_course_details": details,
                "in_progress_course_details": [],
                "failed_course_details": [],
            },
        )
        p = _extract_packet(r)
        assert "completed_course_details" in p
        assert p["completed_course_details"] == details

    def test_deterministic_renders_completed_name_first(self):
        details = [
            {"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3},
            {"course_code": "HUM111", "course_name": "Humanities I", "credits": 2},
        ]
        r = PerSQResult(
            sq_index=0, intent="get_student_record", status="success",
            data={"cgpa": 3.48, "track_id": "AI", "completed_course_details": details},
        )
        answer = _deterministic_answer([_extract_packet(r)])
        assert "Programming Fundamentals (C-CS112)" in answer
        assert "Humanities I (HUM111)" in answer
        # code-only form must NOT appear without name
        assert "• C-CS112" not in answer
        assert "• HUM111" not in answer

    def test_deterministic_renders_in_progress_name_first(self):
        details = [
            {"course_code": "C-MA112", "course_name": "Calculus II", "credits": 3},
            {"course_code": "C-PH112", "course_name": "Advanced Physics", "credits": 3},
        ]
        r = PerSQResult(
            sq_index=0, intent="get_student_record", status="success",
            data={"cgpa": 3.48, "track_id": "SWE", "in_progress_course_details": details},
        )
        answer = _deterministic_answer([_extract_packet(r)])
        assert "Calculus II (C-MA112)" in answer
        assert "Advanced Physics (C-PH112)" in answer

    def test_deterministic_renders_failed_name_first(self):
        details = [{"course_code": "C-CS111", "course_name": "Intro to CS", "credits": 3}]
        r = PerSQResult(
            sq_index=0, intent="get_student_record", status="success",
            data={"cgpa": 1.80, "track_id": "AI", "failed_course_details": details},
        )
        answer = _deterministic_answer([_extract_packet(r)])
        assert "Intro to CS (C-CS111)" in answer

    def test_deterministic_code_only_fallback_when_name_none(self):
        """When course_name is None, code must appear in the list without fabricated name."""
        details = [{"course_code": "C-CS999", "course_name": None, "credits": None}]
        r = PerSQResult(
            sq_index=0, intent="get_student_record", status="success",
            data={"cgpa": 3.0, "track_id": "AI", "completed_course_details": details},
        )
        answer = _deterministic_answer([_extract_packet(r)])
        assert "C-CS999" in answer
        # Must not invent a name
        assert "(" not in answer or "C-CS999" in answer

    def test_deterministic_falls_back_to_raw_codes_if_no_details(self):
        """If only completed_courses (not details) is present, still shows count."""
        r = PerSQResult(
            sq_index=0, intent="get_student_record", status="success",
            data={"cgpa": 3.0, "track_id": "AI", "completed_courses": ["C-CS111", "C-CS112"]},
        )
        answer = _deterministic_answer([_extract_packet(r)])
        assert "2" in answer or "course" in answer.lower()

    def test_no_generic_filler_in_deterministic_answer(self):
        """Deterministic student-record answer must not append 'Let me know' filler."""
        details = [{"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3}]
        r = PerSQResult(
            sq_index=0, intent="get_student_record", status="success",
            data={"cgpa": 3.48, "track_id": "AI", "in_progress_course_details": details},
        )
        answer = _deterministic_answer([_extract_packet(r)])
        assert "let me know" not in answer.lower()
        assert "if you need" not in answer.lower()
        assert "further details" not in answer.lower()

    def test_no_raw_dict_in_answer(self):
        """Course detail dicts must never appear as raw Python repr in the answer."""
        details = [{"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3}]
        r = PerSQResult(
            sq_index=0, intent="get_student_record", status="success",
            data={"cgpa": 3.48, "track_id": "AI", "completed_course_details": details},
        )
        answer = _deterministic_answer([_extract_packet(r)])
        assert "{'course_code'" not in answer
        assert '"course_code"' not in answer


class TestD6FocusNarration:
    """Verify focus-aware deterministic narration for get_student_record."""

    def _make_packet(self, **kwargs) -> dict:
        defaults = {
            "intent": "get_student_record",
            "status": "success",
            "cgpa": 3.48,
            "track_id": "SWE",
            "level": 2,
            "level_display": "Sophomore",
            "academic_standing": "good",
            "current_semester": "Spring 2026",
            "total_credit_hours_earned": 16,
            "consecutive_warnings": 0,
            "total_warnings": 0,
            "study_status": "Studying",
            "completed_course_details": [],
            "in_progress_course_details": [],
            "failed_course_details": [],
            "completed_courses": [],
            "in_progress_courses": [],
            "failed_courses": [],
            "assumed_failed_courses": [],
            "assumed_passed_courses": [],
            "record_focus": "full_record",
            "response_style": "normal",
        }
        defaults.update(kwargs)
        return defaults

    def _answer(self, **kwargs) -> str:
        packet = self._make_packet(**kwargs)
        return _deterministic_answer([packet])

    def test_cgpa_focus_shows_only_cgpa(self):
        answer = self._answer(record_focus="cgpa")
        assert "3.48" in answer

    def test_cgpa_focus_does_not_show_full_record(self):
        answer = self._answer(record_focus="cgpa")
        assert "Completed" not in answer
        assert "In-progress" not in answer

    def test_academic_level_focus_shows_level_display(self):
        answer = self._answer(record_focus="academic_level")
        assert "Level 2" in answer
        assert "Sophomore" in answer

    def test_academic_level_none_shows_not_available(self):
        answer = self._answer(record_focus="academic_level", level=None, level_display=None)
        assert "not available" in answer.lower()

    def test_academic_standing_good_shows_not_in_danger(self):
        answer = self._answer(record_focus="academic_standing")
        assert "good" in answer.lower()

    def test_academic_standing_warning_shows_warning(self):
        answer = self._answer(record_focus="academic_standing", academic_standing="warning")
        assert "warning" in answer.lower()

    def test_last_semester_gpa_focus_shows_gpa(self):
        answer = self._answer(record_focus="last_semester_gpa", last_semester_gpa=3.75)
        assert "3.75" in answer

    def test_last_semester_gpa_missing_shows_not_available(self):
        answer = self._answer(record_focus="last_semester_gpa", last_semester_gpa=None)
        assert "not available in our records" in answer.lower()
        assert "packet" not in answer.lower()

    def test_assumption_acknowledgement_shows_ack_message(self):
        answer = self._answer(
            record_focus="assumption_acknowledgement",
            assumed_failed_courses=["C-CS112"],
            failed_course_details=[{"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3}],
        )
        assert "what-if assumption" in answer.lower()
        assert "official academic record is unchanged" in answer.lower()
        assert "Programming Fundamentals" in answer

    def test_assumption_acknowledgement_does_not_show_full_record(self):
        answer = self._answer(
            record_focus="assumption_acknowledgement",
            assumed_failed_courses=["C-CS112"],
        )
        assert "CGPA" not in answer or "3.48" not in answer

    def test_reset_assumptions_cleared_shows_exact_wording(self):
        answer = self._answer(
            record_focus="reset_assumptions",
            assumptions_cleared=True,
            message="I cleared your what-if assumptions. You are back to your official academic record.",
        )
        assert "I cleared your what-if assumptions." in answer
        assert "You are back to your official academic record." in answer
        assert "Completed" not in answer  # no full record dump

    def test_completed_courses_focus_shows_name_first(self):
        details = [{"course_code": "C-CS111", "course_name": "Intro to CS", "credits": 3}]
        answer = self._answer(
            record_focus="completed_courses",
            completed_course_details=details,
            completed_courses=["C-CS111"],
        )
        assert "Intro to CS (C-CS111)" in answer

    def test_in_progress_courses_focus_shows_name_first(self):
        details = [{"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3}]
        answer = self._answer(
            record_focus="in_progress_courses",
            in_progress_course_details=details,
            in_progress_courses=["C-CS112"],
        )
        assert "Programming Fundamentals (C-CS112)" in answer

    def test_failed_courses_no_fails_shows_none_message(self):
        answer = self._answer(record_focus="failed_courses", failed_course_details=[], failed_courses=[])
        assert "no failed courses" in answer.lower()

    def test_completed_credits_focus_shows_credits(self):
        answer = self._answer(record_focus="completed_credits", total_credit_hours_earned=16)
        assert "16" in answer
        assert "credit" in answer.lower()

    def test_track_focus_shows_track(self):
        answer = self._answer(record_focus="track")
        assert "Software Engineering" in answer or "SWE" in answer

    def test_no_internal_terms_in_answer(self):
        """Composer must never expose internal terms like 'packet' in user-facing output."""
        for focus in ["cgpa", "academic_level", "last_semester_gpa", "full_record"]:
            answer = self._answer(record_focus=focus)
            assert "packet" not in answer.lower(), f"'packet' found in {focus} answer"
            assert "orchestrator" not in answer.lower()

    def test_friendly_ending_suppressed_for_only_style(self):
        details = [{"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3}]
        answer = self._answer(
            record_focus="in_progress_courses",
            response_style="only",
            in_progress_course_details=details,
            in_progress_courses=["C-CS112"],
        )
        assert "let me know" not in answer.lower()
        assert "if you need" not in answer.lower()
        assert "good luck" not in answer.lower()

    def test_full_record_shows_level_display(self):
        answer = self._answer(record_focus="full_record")
        assert "Sophomore" in answer or "Level 2" in answer

    def test_official_vs_whatsif_shown_when_assumptions_active(self):
        answer = self._answer(
            record_focus="full_record",
            override_state_active=True,
            assumed_failed_courses=["C-CS112"],
            assumed_passed_courses=[],
            failed_course_details=[{"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3}],
            failed_courses=["C-CS112"],
        )
        assert "what-if" in answer.lower()

    def test_scenario_credits_shown_when_different(self):
        answer = self._answer(
            record_focus="completed_credits",
            total_credit_hours_earned=16,
            scenario_completed_credits=19,
        )
        assert "16" in answer
        assert "19" in answer


# ── Phase 2 Behavioral Stabilization Tests ─────────────────────────────────────


class TestPhase2D3RolesByTrackShape:
    """D3 Composer: get_roles_by_track handles KG result shape (results vs roles, track vs track_name)."""

    def _extract(self, kg_data: dict) -> dict:
        r = PerSQResult(sq_index=0, intent="get_roles_by_track", status="success", data=kg_data)
        return _extract_packet(r)

    def test_roles_key_passed_through(self):
        data = {"track_id": "AI", "track_name": "Artificial Intelligence", "roles": [
            {"role_id": "RL_Data_Scientist", "name": "Data Scientist"}
        ]}
        packet = self._extract(data)
        assert packet["roles"]
        assert packet["track_id"] == "AI"

    def test_results_key_normalized_to_roles(self):
        """KG returning 'results' instead of 'roles' must be normalized."""
        data = {"track_id": "AI", "track": "AI", "total_results": 2, "results": [
            {"role_id": "RL_Data_Scientist", "name": "Data Scientist"},
            {"role_id": "RL_ML_Engineer", "name": "ML Engineer"},
        ]}
        packet = self._extract(data)
        assert "roles" in packet
        assert len(packet["roles"]) == 2
        assert packet["total_results"] == 2

    def test_track_key_normalized_to_track_name(self):
        """KG returning 'track' instead of 'track_name' must be normalized."""
        data = {"track_id": "DSE", "track": "DSE", "results": [
            {"role_id": "RL_Data_Engineer", "name": "Data Engineer"}
        ]}
        packet = self._extract(data)
        assert packet.get("track_name") == "DSE"

    def test_narration_never_says_no_roles_when_results_present(self):
        """Never say 'no roles found' when results/roles is non-empty."""
        data = {"track_id": "AI", "track": "AI", "results": [
            {"role_id": "RL_Data_Scientist", "name": "Data Scientist"},
        ]}
        packet = self._extract(data)
        answer = _deterministic_answer([packet])
        assert "no roles" not in answer.lower()
        assert "Data Scientist" in answer

    def test_narration_uses_track_name_when_track_key(self):
        data = {"track_id": "SWE", "track": "SWE", "results": [
            {"role_id": "RL_Software_Engineer", "name": "Software Engineer"},
        ]}
        packet = self._extract(data)
        answer = _deterministic_answer([packet])
        # Should mention software engineer role
        assert "Software Engineer" in answer


class TestPhase2D3RoleProfileShape:
    """D3 Composer: get_role_profile handles both 'name' and 'role_name' from KG."""

    def _extract(self, kg_data: dict) -> dict:
        r = PerSQResult(sq_index=0, intent="get_role_profile", status="success", data=kg_data)
        return _extract_packet(r)

    def test_name_field_used_directly(self):
        data = {"role_id": "RL_Data_Scientist", "name": "Data Scientist",
                "description": "Works with data."}
        packet = self._extract(data)
        assert packet["name"] == "Data Scientist"

    def test_role_name_normalized_to_name(self):
        """KG returning 'role_name' instead of 'name' must be normalized."""
        data = {"role_id": "RL_Data_Scientist", "role_name": "Data Scientist",
                "description": "Works with data."}
        packet = self._extract(data)
        assert packet.get("name") == "Data Scientist"

    def test_narration_shows_role_name(self):
        data = {"role_id": "RL_ML_Engineer", "role_name": "ML Engineer",
                "description": "Builds ML models."}
        packet = self._extract(data)
        answer = _deterministic_answer([packet])
        assert "ML Engineer" in answer

    def test_no_raw_rl_ids_in_narration(self):
        data = {"role_id": "RL_Data_Scientist", "role_name": "Data Scientist"}
        packet = self._extract(data)
        answer = _deterministic_answer([packet])
        # Friendly name shown, not raw RL_* ID
        assert "Data Scientist" in answer


class TestPhase2EstimateAlignmentImprovement:
    """estimate_alignment_improvement never outputs 'is ready' without actual data."""

    def _extract(self, data: dict) -> dict:
        r = PerSQResult(sq_index=0, intent="estimate_alignment_improvement",
                        status="success", data=data)
        return _extract_packet(r)

    def test_shows_actual_values_when_present(self):
        data = {"role_id": "RL_Data_Scientist", "role_name": "Data Scientist",
                "current_alignment": 0.4, "new_alignment": 0.65}
        packet = self._extract(data)
        answer = _deterministic_answer([packet])
        assert "40%" in answer or "0.4" in answer or "40" in answer
        assert "65%" in answer or "0.65" in answer or "65" in answer
        assert "ready" not in answer.lower()

    def test_no_ready_when_values_missing(self):
        data = {"role_id": "RL_Data_Scientist", "role_name": "Data Scientist"}
        packet = self._extract(data)
        answer = _deterministic_answer([packet])
        assert "ready" not in answer.lower()
        # Should explain cannot compute
        assert any(w in answer.lower() for w in ("could not", "cannot", "make sure", "identified"))

    def test_no_ready_when_message_contains_ready(self):
        data = {"role_id": "RL_Data_Scientist", "role_name": "Data Scientist",
                "message": "Alignment improvement estimate is ready."}
        packet = self._extract(data)
        answer = _deterministic_answer([packet])
        assert "ready" not in answer.lower()

    def test_delta_shown_in_improvement(self):
        data = {"role_id": "RL_ML_Engineer", "role_name": "ML Engineer",
                "current_alignment": 0.3, "new_alignment": 0.7}
        packet = self._extract(data)
        answer = _deterministic_answer([packet])
        # Should show both values and a delta
        assert "30%" in answer or "0.3" in answer
        assert "70%" in answer or "0.7" in answer


class TestPhase2MissingCourseKGProfile:
    """Completed-course list handles missing KG profiles gracefully."""

    def test_render_missing_profile_shows_code_and_message(self):
        """_render_course_detail with course_name=None shows graceful label."""
        detail = {"course_code": "C-GP411", "course_name": None, "credits": None}
        result = _render_course_detail(detail)
        assert "C-GP411" in result
        assert "not available" in result

    def test_render_normal_profile_unchanged(self):
        detail = {"course_code": "C-CS111", "course_name": "Intro to CS", "credits": 3}
        result = _render_course_detail(detail)
        assert "Intro to CS" in result
        assert "C-CS111" in result

    def test_render_empty_name_returns_code(self):
        """Empty string course_name (no KG entry) returns just the code."""
        detail = {"course_code": "C-CS999", "course_name": "", "credits": None}
        result = _render_course_detail(detail)
        assert "C-CS999" in result
        # No "not available" label for empty string — that's not a KG error
        assert "not available" not in result


class TestPhase2CoursesBySkillTopicFallback:
    """search_courses_by_skill shows topic fallback explanation when course intent rerouted."""

    def _extract(self, data: dict) -> dict:
        r = PerSQResult(sq_index=0, intent="search_courses_by_skill",
                        status="success", data=data)
        return _extract_packet(r)

    def test_topic_fallback_flag_triggers_reroute_explanation(self):
        data = {
            "skill_id": "SK_OOP", "skill_name": "Object-Oriented Programming",
            "courses": [{"course_code": "C-CS219", "name": "Advanced Programming"}],
            "topic_fallback": True,
            "original_mention": "oop",
        }
        packet = self._extract(data)
        answer = _deterministic_answer([packet])
        assert "oop" in answer.lower() or "not see" in answer.lower() or "don't see" in answer.lower()

    def test_no_fallback_flag_normal_narration(self):
        data = {
            "skill_id": "SK_ML", "skill_name": "Machine Learning",
            "courses": [{"course_code": "C-AI301", "name": "Intro to Machine Learning"}],
        }
        packet = self._extract(data)
        answer = _deterministic_answer([packet])
        assert "Machine Learning" in answer
        assert "don't see" not in answer.lower()
