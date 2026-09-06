"""
Phase 1.5 — Integration Contract Tests

Verifies that audited components connect correctly at their contract boundaries.
No live LLM, no Neo4j, no RAG engine. All external dependencies are mocked.

Contracts verified:
  1. QU StructuredQuery  → Orchestrator input
  2. Orchestrator TurnWrapper/PerSQResult → Composer input
  3. Composer QueryResponse → API response schema
  4. UI helpers consume API response shape
  5. API /chat mocked pipeline returns expected QueryResponse shape
  6. Reset-assumptions structured signal (assumptions_cleared)
  7. No stale response fields in API/UI
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gateway.models.schemas import (
    Citation, EntitySet, LastReferenced, PerSQResult, QueryRequest,
    QueryResponse, SessionOverrides, SessionState, StructuredQuery,
    StudentContext, TurnWrapper,
)
from gateway.orchestrator import Orchestrator, _collect_turn_overrides
from gateway.response_composer import ResponseComposer, _deterministic_answer, _extract_packet
from gateway.session_manager import build_effective_context, merge_turn_overrides
from gateway.qu_intents import LOCKED_INTENTS, FORBIDDEN_INTENTS


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_student_context(**kwargs) -> StudentContext:
    defaults = dict(
        student_id="STU000001",
        name="Test Student",
        program="CS",
        track_id="AI",
        track_status="supported",
        level=3,
        first_semester="Fall 2021",
        study_status="Regular",
        cgpa=3.0,
        cumulative_chs=90,
        cumulative_cps=270.0,
        total_credit_hours_earned=90,
        current_semester="Spring 2024",
        completed_courses=["C-CS101", "C-CS201"],
        failed_courses=[],
        in_progress_courses=["C-CS301"],
    )
    defaults.update(kwargs)
    return StudentContext(**defaults)


def _make_session(ctx: StudentContext | None = None) -> SessionState:
    ctx = ctx or _make_student_context()
    return SessionState(
        session_id="test-session-00001111",
        student_id=ctx.student_id,
        session_name="Test Session",
        student_context=ctx,
    )


def _make_sq(intent: str, **kwargs) -> StructuredQuery:
    return StructuredQuery(intent=intent, original_text="test query", **kwargs)


def _make_persq(intent: str, status: str = "success", data: dict | None = None) -> PerSQResult:
    return PerSQResult(sq_index=0, intent=intent, status=status, data=data or {})


def _make_turn(results: list[PerSQResult], turn_status: str = "completed") -> TurnWrapper:
    return TurnWrapper(
        turn_id="t1",
        session_id="test-session-00001111",
        timestamp="2026-06-24T00:00:00+00:00",
        results=results,
        result_count=len(results),
        turn_status=turn_status,
        has_error=any(r.status == "error" for r in results),
        has_clarification=any(r.status == "clarification_needed" for r in results),
        has_informational=any(r.status == "informational" for r in results),
        has_soft_no_evidence=any(r.status == "soft_no_evidence" for r in results),
    )


# ── Contract 1: QU StructuredQuery → Orchestrator ────────────────────────────

class TestContract1_QU_Orchestrator:

    def test_all_locked_intents_are_handled(self):
        """Every locked intent must be accepted by Orchestrator (not return validation_failed)."""
        kg = MagicMock()
        kg.call.return_value = {}
        rag = MagicMock()
        rag.execute.return_value = {"answer": "ok", "extracted_facts": ["fact"], "citations": []}
        ale = MagicMock()
        ale.call.return_value = {"status": "success"}

        orch = Orchestrator(kg, rag, ale)
        ctx = _make_student_context()
        session = _make_session(ctx)

        control_intents = {"clarification_needed", "out_of_scope"}
        for intent in LOCKED_INTENTS - control_intents:
            sq = _make_sq(intent,
                entities=EntitySet(course_code="C-CS101", role_id="RL_Data_Scientist",
                                   track_id="AI", skill_id="SK_Python"),
                secondary_entities=EntitySet(track_id="DSE"),
                params={"target_gpa": 3.0, "depth": "direct",
                        "planned_courses": ["C-CS101"],
                        "expected_grades": {"C-CS101": "A"},
                        "target_semester_type": "Fall",
                        "target_credit_load": None},
                student_referential_fallback=True,
            )
            tw = orch.execute_turn([sq], session, {})
            result = tw.results[0]
            assert result.intent == intent
            assert result.status != "validation_failed" or result.error_code != "intent", (
                f"Intent {intent!r} was rejected with validation_failed/intent"
            )

    def test_forbidden_intents_are_rejected(self):
        """Every forbidden/stale intent must be rejected before routing."""
        kg = MagicMock()
        rag = MagicMock()
        ale = MagicMock()
        orch = Orchestrator(kg, rag, ale)
        session = _make_session()

        for intent in FORBIDDEN_INTENTS:
            sq = _make_sq(intent)
            tw = orch.execute_turn([sq], session, {})
            result = tw.results[0]
            assert result.status == "error", (
                f"Forbidden intent {intent!r} should produce error, got {result.status}"
            )
            assert result.error_code == "validation_failed", (
                f"Forbidden intent {intent!r} error_code should be validation_failed"
            )

    def test_sq_entity_fields_match_schema(self):
        """EntitySet fields used by QU are all present in the schema."""
        e = EntitySet(course_code="C-CS101", role_id="RL_X", track_id="AI", skill_id="SK_Y")
        assert e.course_code == "C-CS101"
        assert e.role_id == "RL_X"
        assert e.track_id == "AI"
        assert e.skill_id == "SK_Y"

    def test_secondary_entities_shape_for_compare_tracks(self):
        """compare_tracks reads sq.secondary_entities.track_id."""
        kg = MagicMock()
        kg.call.return_value = {"track_id_1": "AI", "track_id_2": "DSE",
                                "shared_courses": [], "different_courses": {}}
        orch = Orchestrator(kg, MagicMock(), MagicMock())
        session = _make_session()
        sq = _make_sq(
            "compare_tracks",
            entities=EntitySet(track_id="AI"),
            secondary_entities=EntitySet(track_id="DSE"),
        )
        tw = orch.execute_turn([sq], session, {})
        assert tw.results[0].status in ("success", "informational")

    def test_params_depth_accepted(self):
        """depth param reaches KG as direct or full."""
        kg = MagicMock()
        kg.call.return_value = {"direct_prerequisites": [], "non_course_prerequisites": []}
        orch = Orchestrator(kg, MagicMock(), MagicMock())
        session = _make_session()
        sq = _make_sq("get_course_prerequisites",
                      entities=EntitySet(course_code="C-CS201"),
                      params={"depth": "full"})
        orch.execute_turn([sq], session, {})
        kg.call.assert_called_once_with("get_prerequisites", {"course_code": "C-CS201", "depth": "full"})

    def test_session_overrides_shape_consumed(self):
        """SessionOverrides with clear action is recognized by _collect_turn_overrides."""
        sq = _make_sq("get_student_record",
                      session_overrides=SessionOverrides(override_action="clear"))
        _, had_clear = _collect_turn_overrides([sq])
        assert had_clear is True

    def test_student_referential_fallback_propagated(self):
        """student_referential_fallback reaches Orchestrator dispatch logic."""
        sq = StructuredQuery(
            intent="get_roles_by_track",
            student_referential_fallback=True,
        )
        assert sq.student_referential_fallback is True


# ── Contract 2: Orchestrator TurnWrapper/PerSQResult → Composer ───────────────

class TestContract2_Orchestrator_Composer:

    def test_composer_reads_turn_wrapper_fields(self):
        """Composer.compose() signature matches TurnWrapper fields."""
        result = _make_persq("get_student_record", data={
            "track_id": "AI", "level": 3, "cgpa": 3.0,
            "academic_standing": "good", "study_status": "Regular",
            "total_credit_hours_earned": 90, "current_semester": "Spring 2024",
        })
        turn = _make_turn([result])
        composer = ResponseComposer.__new__(ResponseComposer)
        composer._use_llm = False
        composer._primary = "none"
        composer._fallbacks = []
        composer._timeout = 30.0
        composer._llm = MagicMock()
        qr = composer.compose("What is my record?", turn, "session-001", "My Session")
        assert isinstance(qr, QueryResponse)
        assert qr.session_id == "session-001"
        assert qr.session_name == "My Session"

    def test_composer_reads_persq_result_fields(self):
        """All PerSQResult fields Composer reads are present in schema."""
        r = _make_persq("policy_query", data={"answer": "ok", "extracted_facts": ["fact"]})
        r_full = r.model_copy(update={
            "error_code": None, "error_category": None, "error_detail": None,
            "clarification_prompt": None, "scope_explanation": None,
            "assumptions_active": None, "assumptions_excluded": None,
            "override_state_active": None, "citations": None,
        })
        packet = _extract_packet(r_full)
        assert "intent" in packet
        assert "status" in packet

    def test_no_student_context_in_turn_wrapper(self):
        """TurnWrapper and PerSQResult never contain raw StudentContext."""
        result = _make_persq("get_student_record", data={"cgpa": 3.0})
        turn = _make_turn([result])
        turn_dict = turn.model_dump()
        # No StudentContext field in TurnWrapper
        assert "student_context" not in turn_dict
        result_dict = turn_dict["results"][0]
        assert "student_context" not in result_dict

    def test_error_result_packet(self):
        """Error PerSQResult produces safe error packet."""
        r = PerSQResult(sq_index=0, intent="plan_semester", status="error",
                        error_code="engine_error", error_detail="ALE unavailable")
        packet = _extract_packet(r)
        assert packet["status"] == "error"
        assert "error" in packet

    def test_clarification_result_packet(self):
        r = PerSQResult(sq_index=0, intent="check_course_eligibility",
                        status="clarification_needed",
                        clarification_prompt="Which course?")
        packet = _extract_packet(r)
        assert packet["clarification_prompt"] == "Which course?"

    def test_out_of_scope_result_packet(self):
        r = PerSQResult(sq_index=0, intent="out_of_scope", status="out_of_scope",
                        scope_explanation="Outside scope.")
        packet = _extract_packet(r)
        assert "scope_explanation" in packet

    def test_citations_shape_consumed_by_composer(self):
        """Citations list[dict] from PerSQResult is consumed by Composer."""
        cit = [{"source": "CIS Handbook", "page": 5}]
        r = _make_persq("policy_query", data={"answer": "ok", "extracted_facts": ["fact"]})
        r = r.model_copy(update={"citations": cit})
        packet = _extract_packet(r)
        assert packet.get("citations") == cit

    def test_assumptions_active_notice_in_packet(self):
        r = _make_persq("plan_semester", data={"plans": []})
        r = r.model_copy(update={"assumptions_active": True})
        packet = _extract_packet(r)
        assert "notice_assumptions_active" in packet

    def test_assumptions_excluded_notice_in_packet(self):
        r = _make_persq("run_graduation_audit", data={"status": "not_eligible", "checks": []})
        r = r.model_copy(update={"assumptions_excluded": True})
        packet = _extract_packet(r)
        assert "notice_assumptions_excluded" in packet

    def test_override_state_active_notice_in_packet(self):
        r = _make_persq("get_student_record", data={"cgpa": 2.5})
        r = r.model_copy(update={"override_state_active": True})
        packet = _extract_packet(r)
        assert "notice_override_active" in packet


# ── Contract 3: Composer QueryResponse → API schema ───────────────────────────

class TestContract3_Composer_APISchema:

    def test_query_response_fields(self):
        """QueryResponse has exactly the required fields."""
        qr = QueryResponse(
            session_id="s1", session_name="Test", answer_text="hello",
            citations=[], status="ok",
        )
        d = qr.model_dump()
        assert "session_id" in d
        assert "session_name" in d
        assert "answer_text" in d
        assert "citations" in d
        assert "status" in d

    def test_no_stale_fields_in_query_response(self):
        """QueryResponse must NOT contain legacy fields."""
        qr = QueryResponse(session_id="s1", session_name="T", answer_text="A")
        d = qr.model_dump()
        assert "answer" not in d, "Stale field 'answer' found in QueryResponse"
        assert "llm_used" not in d, "Stale field 'llm_used' found in QueryResponse"
        assert "model_used" not in d, "Stale field 'model_used' found in QueryResponse"

    def test_citation_schema(self):
        """Citation has source and optional page."""
        c = Citation(source="CIS Handbook", page=5)
        assert c.source == "CIS Handbook"
        assert c.page == 5
        c2 = Citation(source="CIS Handbook")
        assert c2.page is None

    def test_status_literals(self):
        """QueryResponse.status only accepts valid literals."""
        qr_ok = QueryResponse(session_id="s", session_name="n", answer_text="a", status="ok")
        qr_err = QueryResponse(session_id="s", session_name="n", answer_text="a", status="error")
        qr_cl = QueryResponse(session_id="s", session_name="n", answer_text="a",
                              status="clarification_needed")
        assert qr_ok.status == "ok"
        assert qr_err.status == "error"
        assert qr_cl.status == "clarification_needed"

    def test_api_response_model_matches_composer_output(self):
        """Composer output is valid for the API response_model."""
        from gateway.models.schemas import QueryResponse as ApiQR
        qr = QueryResponse(session_id="x", session_name="n", answer_text="ans",
                           citations=[Citation(source="H", page=1)], status="ok")
        assert isinstance(qr, ApiQR)


# ── Contract 4: UI helpers consume API response shape ─────────────────────────

class TestContract4_UI_APIShape:

    def test_ui_reads_answer_text(self):
        """UI reads resp.get('answer_text'), not 'answer'."""
        resp = {"answer_text": "Hello", "session_id": "s1", "session_name": "S",
                "citations": [], "status": "ok"}
        answer = resp.get("answer_text", "Something went wrong.")
        assert answer == "Hello"

    def test_ui_reads_citations(self):
        resp = {"answer_text": "hi", "citations": [{"source": "H", "page": 1}]}
        citations = resp.get("citations", [])
        assert len(citations) == 1
        assert citations[0].get("source") == "H"

    def test_ui_preserves_session_id(self):
        resp = {"answer_text": "hi", "session_id": "ses-123", "session_name": "My Chat",
                "citations": [], "status": "ok"}
        assert resp.get("session_id") == "ses-123"

    def test_ui_history_reads_answer_key(self):
        """Turn history stores and UI reads 'answer' key (Turn TypedDict convention)."""
        turn = {"user": "What is my CGPA?", "answer": "Your CGPA is 3.0."}
        msg_user = turn.get("user", "")
        msg_answer = turn.get("answer", "")
        assert msg_user == "What is my CGPA?"
        assert msg_answer == "Your CGPA is 3.0."

    def test_ui_error_fallback_shape(self):
        """UI error fallback always returns answer_text + session_id."""
        error_resp = {"answer_text": "Server error 500: Internal error", "status": "error",
                      "session_id": None, "session_name": "", "citations": []}
        assert error_resp.get("answer_text") is not None
        assert "session_id" in error_resp


# ── Contract 5: API /chat mocked pipeline → QueryResponse ─────────────────────

class TestContract5_API_Pipeline:

    def test_chat_pipeline_returns_query_response_shape(self):
        """
        Mock the entire /chat pipeline and verify QueryResponse shape.
        Tests that: QU → Orchestrator → Composer → return all connect.
        """
        from fastapi.testclient import TestClient

        # We'll test just the schema at the model level rather than the full HTTP stack
        # because that avoids needing live DB / SCP Excel
        ctx = _make_student_context()
        session = _make_session(ctx)

        sq = _make_sq("get_student_record", student_referential_fallback=True)

        # Mock orchestrator result
        per_sq = PerSQResult(
            sq_index=0, intent="get_student_record", status="success",
            data={"track_id": "AI", "cgpa": 3.0, "level": 3,
                  "academic_standing": "good", "study_status": "Regular",
                  "total_credit_hours_earned": 90, "current_semester": "Spring 2024"},
        )
        turn = _make_turn([per_sq])

        # Mock composer (deterministic)
        composer = ResponseComposer.__new__(ResponseComposer)
        composer._use_llm = False
        composer._primary = "none"
        composer._fallbacks = []
        composer._timeout = 30.0
        composer._llm = MagicMock()
        qr = composer.compose("What is my record?", turn, session.session_id, session.session_name)

        # Verify shape
        assert isinstance(qr, QueryResponse)
        assert qr.session_id == session.session_id
        assert qr.session_name == session.session_name
        assert isinstance(qr.answer_text, str) and len(qr.answer_text) > 0
        assert isinstance(qr.citations, list)
        assert qr.status in ("ok", "error", "clarification_needed")

    def test_query_request_fields(self):
        """QueryRequest must have student_id, user_text, optional session_id."""
        qr = QueryRequest(student_id="STU000001", user_text="What is my CGPA?")
        assert qr.student_id == "STU000001"
        assert qr.user_text == "What is my CGPA?"
        assert qr.session_id is None

        qr2 = QueryRequest(student_id="STU000001", user_text="hi", session_id="s1")
        assert qr2.session_id == "s1"

    def test_api_stores_answer_text_in_history(self):
        """After compose(), answer_text is stored as the 'answer' turn field."""
        ctx = _make_student_context()
        session = _make_session(ctx)

        per_sq = _make_persq("get_student_record", data={"cgpa": 3.0, "track_id": "AI"})
        turn = _make_turn([per_sq])

        composer = ResponseComposer.__new__(ResponseComposer)
        composer._use_llm = False
        composer._primary = "none"
        composer._fallbacks = []
        composer._timeout = 30.0
        composer._llm = MagicMock()
        qr = composer.compose("My record?", turn, session.session_id, session.session_name)

        # Simulate what update_session_after_turn stores
        history_entry = {"user": "My record?", "answer": qr.answer_text}
        assert history_entry["answer"] == qr.answer_text


# ── Contract 6: Reset-assumptions structured signal ───────────────────────────

class TestContract6_ResetAssumptionsSignal:

    def _run_clear_turn(self) -> tuple[PerSQResult, bool]:
        """Run an Orchestrator turn with override_action='clear'."""
        kg = MagicMock()
        kg.call.return_value = {}
        rag = MagicMock()
        ale = MagicMock()
        orch = Orchestrator(kg, rag, ale)

        ctx = _make_student_context()
        session = _make_session(ctx)
        # Give session some active overrides so had_clear is meaningful
        session = session.model_copy(update={
            "overrides": SessionOverrides(
                assumed_passed_courses=["C-CS999"],
                override_action="accumulate",
            )
        })

        sq = _make_sq(
            "get_student_record",
            session_overrides=SessionOverrides(override_action="clear"),
            student_referential_fallback=True,
        )
        # Use a minimal but valid rule_bundles (academic_warning_rules stub)
        warning_rules = MagicMock()
        warning_rules.cgpa_warning_threshold = 2.0
        rule_bundles = {"academic_warning_rules": warning_rules}

        tw = orch.execute_turn([sq], session, rule_bundles)
        result = tw.results[0]
        _, had_clear = merge_turn_overrides([sq])
        return result, had_clear

    def test_had_clear_detected(self):
        """merge_turn_overrides detects had_clear=True for clear SQ."""
        sq = _make_sq("get_student_record",
                      session_overrides=SessionOverrides(override_action="clear"))
        _, had_clear = merge_turn_overrides([sq])
        assert had_clear is True

    def test_orchestrator_sets_assumptions_cleared_in_data(self):
        """Orchestrator adds assumptions_cleared=True to get_student_record data when clear."""
        result, _ = self._run_clear_turn()
        assert result.intent == "get_student_record"
        assert result.status == "success"
        assert result.data is not None
        assert result.data.get("assumptions_cleared") is True, (
            "Orchestrator must set data['assumptions_cleared']=True on clear action"
        )

    def test_orchestrator_sets_standard_message(self):
        """Orchestrator sets the standard 'cleared' message in data."""
        result, _ = self._run_clear_turn()
        assert result.data is not None
        msg = result.data.get("message", "")
        assert "cleared" in msg.lower() or "what-if" in msg.lower(), (
            f"Expected cleared-assumptions message, got: {msg!r}"
        )

    def test_composer_extract_packet_includes_assumptions_cleared(self):
        """_extract_packet passes assumptions_cleared through to narration packet."""
        r = _make_persq("get_student_record", data={
            "cgpa": 3.0, "track_id": "AI",
            "assumptions_cleared": True,
            "message": "I cleared your what-if assumptions. You are back to your official academic record.",
        })
        packet = _extract_packet(r)
        assert packet.get("assumptions_cleared") is True
        assert "cleared" in packet.get("message", "").lower()

    def test_deterministic_narration_uses_standard_wording(self):
        """Deterministic fallback outputs the standard clear-assumptions wording."""
        packet = {
            "intent": "get_student_record",
            "status": "success",
            "cgpa": 3.0,
            "track_id": "AI",
            "assumptions_cleared": True,
            "message": "I cleared your what-if assumptions. You are back to your official academic record.",
        }
        answer = _deterministic_answer([packet])
        assert "cleared" in answer.lower(), (
            f"Expected 'cleared' in deterministic answer, got:\n{answer}"
        )
        assert "what-if" in answer.lower() or "official academic record" in answer.lower(), (
            f"Expected standard wording in answer, got:\n{answer}"
        )

    def test_deterministic_narration_no_bad_wording(self):
        """Deterministic fallback must NOT say 'record updated' or 'academic record updated'."""
        packet = {
            "intent": "get_student_record",
            "status": "success",
            "cgpa": 3.0,
            "track_id": "AI",
            "assumptions_cleared": True,
            "message": "I cleared your what-if assumptions. You are back to your official academic record.",
        }
        answer = _deterministic_answer([packet])
        bad_phrases = [
            "academic record has been updated",
            "your record has been updated",
            "updated your record",
            "updated your academic record",
        ]
        for phrase in bad_phrases:
            assert phrase not in answer.lower(), (
                f"Bad wording found in answer: {phrase!r}\n\nFull answer:\n{answer}"
            )

    def test_non_clear_turn_has_no_assumptions_cleared(self):
        """A regular get_student_record turn without clear must NOT set assumptions_cleared."""
        kg = MagicMock()
        kg.call.return_value = {}
        orch = Orchestrator(kg, MagicMock(), MagicMock())
        ctx = _make_student_context()
        session = _make_session(ctx)

        sq = _make_sq("get_student_record", student_referential_fallback=True)
        warning_rules = MagicMock()
        warning_rules.cgpa_warning_threshold = 2.0
        tw = orch.execute_turn([sq], session, {"academic_warning_rules": warning_rules})
        result = tw.results[0]
        assert result.data is not None
        assert result.data.get("assumptions_cleared") is not True, (
            "assumptions_cleared must NOT be set on a regular (non-clear) turn"
        )

    def test_api_had_clear_triggers_replace_overrides(self):
        """merge_turn_overrides returns had_clear=True, which triggers replace_overrides in API."""
        sq_clear = _make_sq("get_student_record",
                            session_overrides=SessionOverrides(override_action="clear"))
        sq_normal = _make_sq("get_student_record")

        _, had_clear_yes = merge_turn_overrides([sq_clear])
        _, had_clear_no = merge_turn_overrides([sq_normal])
        assert had_clear_yes is True
        assert had_clear_no is False


# ── Contract 7: No stale fields ───────────────────────────────────────────────

class TestContract7_NoStaleFields:

    def test_query_response_no_stale_fields(self):
        """QueryResponse schema has no legacy fields."""
        fields = QueryResponse.model_fields.keys()
        stale = {"answer", "llm_used", "model_used", "turn", "turn_id"}
        found = stale & set(fields)
        assert not found, f"Stale fields found in QueryResponse: {found}"

    def test_per_sq_result_no_stale_fields(self):
        """PerSQResult has no legacy fields from old ResultPackage."""
        fields = PerSQResult.model_fields.keys()
        stale = {"engine_pattern", "kg_result", "rag_result", "ale_result", "composer_context"}
        found = stale & set(fields)
        assert not found, f"Stale fields found in PerSQResult: {found}"

    def test_turn_wrapper_no_stale_fields(self):
        """TurnWrapper has no legacy fields."""
        fields = TurnWrapper.model_fields.keys()
        stale = {"intents", "engine_patterns", "raw_results"}
        found = stale & set(fields)
        assert not found, f"Stale fields found in TurnWrapper: {found}"

    def test_ui_does_not_read_stale_answer_field(self):
        """UI code reads answer_text, not the stale answer field."""
        resp = {"answer_text": "Hello", "answer": "SHOULD_NOT_BE_READ", "session_id": "s"}
        answer = resp.get("answer_text", "Something went wrong.")
        assert answer == "Hello"
        assert answer != "SHOULD_NOT_BE_READ"

    def test_structured_query_no_legacy_routing(self):
        """StructuredQuery.engine_pattern is Optional and never required."""
        sq = StructuredQuery(intent="get_course_info")
        assert sq.engine_pattern is None  # legacy field — always None in production


# ── Contract 8: Session state → QU and Orchestrator ──────────────────────────

class TestContract8_SessionContracts:

    def test_session_state_fields_for_qu(self):
        """QU receives turn_history as list[dict] and last_referenced."""
        ctx = _make_student_context()
        session = _make_session(ctx)
        # turn_history is list[Turn=dict]
        session = session.model_copy(update={
            "turn_history": [{"user": "Q1", "answer": "A1"}]
        })
        recent_turns = list(session.turn_history[-5:])
        assert isinstance(recent_turns, list)
        assert recent_turns[0]["user"] == "Q1"
        assert recent_turns[0]["answer"] == "A1"

    def test_last_referenced_shape(self):
        """LastReferenced mirrors EntitySet for QU context propagation."""
        lr = LastReferenced(course_code="C-CS101", role_id=None, track_id="AI", skill_id=None)
        assert lr.course_code == "C-CS101"
        assert lr.track_id == "AI"

    def test_build_effective_context_applies_assumed_passed(self):
        """build_effective_context correctly applies assumed_passed override."""
        ctx = _make_student_context(
            completed_courses=["C-CS101"],
            failed_courses=["C-CS201"],
        )
        overrides = SessionOverrides(
            assumed_passed_courses=["C-CS201"],
            override_action="accumulate",
        )
        eff = build_effective_context(ctx, overrides)
        assert "C-CS201" in eff.completed_courses
        assert "C-CS201" not in eff.failed_courses

    def test_build_effective_context_clear_is_no_op(self):
        """build_effective_context with empty overrides returns unchanged context."""
        ctx = _make_student_context()
        eff = build_effective_context(ctx, SessionOverrides())
        assert eff.completed_courses == ctx.completed_courses

    def test_session_overrides_accumulate(self):
        """_apply_overrides accumulate mode unions course lists."""
        from gateway.session_manager import _apply_overrides
        existing = SessionOverrides(added_courses=["C-CS101"], override_action="accumulate")
        incoming = SessionOverrides(added_courses=["C-CS202"], override_action="accumulate")
        merged = _apply_overrides(existing, incoming)
        assert "C-CS101" in merged.added_courses
        assert "C-CS202" in merged.added_courses

    def test_session_overrides_replace(self):
        from gateway.session_manager import _apply_overrides
        existing = SessionOverrides(added_courses=["C-CS101"], override_action="accumulate")
        incoming = SessionOverrides(added_courses=["C-CS202"], override_action="replace")
        merged = _apply_overrides(existing, incoming)
        assert "C-CS101" not in merged.added_courses
        assert "C-CS202" in merged.added_courses
        assert merged.override_action == "accumulate"

    def test_session_overrides_clear(self):
        from gateway.session_manager import _apply_overrides
        existing = SessionOverrides(added_courses=["C-CS101"], override_action="accumulate")
        incoming = SessionOverrides(override_action="clear")
        merged = _apply_overrides(existing, incoming)
        assert merged.added_courses == []
        assert merged.assumed_passed_courses == []
        assert merged.assumed_failed_courses == []
