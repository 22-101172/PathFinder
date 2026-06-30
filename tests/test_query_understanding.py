"""
QU tests — organized by behavior category.
Tests run without a live LLM or KG; all external calls are mocked.

Categories:
  T01–T06  Preprocessing (pure functions)
  T07–T09  Schema / intent validation
  T10–T16  Deterministic fallback (no LLM)
  T17–T24  LLM mock parsing (mock client returns controlled JSON)
  T25–T30  Anti-forbidden intent
  T31–T33  Edge cases
"""
from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from gateway.models.schemas import EntitySet, LastReferenced, SessionOverrides, StructuredQuery
from gateway.qu_intents import FORBIDDEN_INTENTS, LOCKED_INTENTS
from gateway.qu_preprocessing import (
    PreprocessResult,
    detect_out_of_scope,
    detect_policy_signal,
    detect_student_referential,
    extract_course_codes,
    extract_expected_grades,
    parse_semester,
    parse_target_cgpa,
    preprocess,
)
from gateway.qu_llm_chain import (
    QUModelChain,
    AllModelsFailedError,
    IntentValidationError,
    _extract_sq_list,
    _validate_intents,
    load_model_chain,
    _load_qu_timeout,
)
from gateway.query_understanding import (
    _clarification,
    _deterministic_fallback,
    _parse_raw_sq,
    understand_query,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _last_ref(**kwargs) -> LastReferenced:
    return LastReferenced(**kwargs)


def _no_ref() -> LastReferenced:
    return LastReferenced()


def _sq_json(intent: str, original_text: str = "test", **overrides) -> str:
    """Build a minimal valid LLM response JSON string."""
    sq: dict[str, Any] = {
        "intent": intent,
        "original_text": original_text,
        "entities": {"course_code": None, "role": None, "track": None, "skill": None},
        "secondary_entities": None,
        "params": {},
        "session_overrides": {
            "added_courses": [],
            "assumed_passed_courses": [],
            "assumed_failed_courses": [],
            "target_role": None,
            "course_override_type": "none",
            "override_action": "accumulate",
        },
        "student_referential_fallback": False,
    }
    sq.update(overrides)
    return json.dumps({"queries": [sq]})


def _multi_sq_json(*sqs) -> str:
    """Build a multi-SQ LLM response JSON string."""
    return json.dumps({"queries": list(sqs)})


def _make_mock_client(responses: list[str]) -> MagicMock:
    """Mock LLMClient that returns responses in order."""
    client = MagicMock()
    client.is_configured.return_value = True
    client.chat.side_effect = responses
    return client


def _run_qu(user_text: str, mock_client=None, last_ref=None, recent_turns=None, monkeypatch=None, resolver=None) -> list[StructuredQuery]:
    """Run understand_query with an optional mock client."""
    if mock_client is not None and monkeypatch is not None:
        monkeypatch.setattr("gateway.query_understanding.get_llm_client", lambda: mock_client)
    return understand_query(
        user_text=user_text,
        last_referenced=last_ref or _no_ref(),
        recent_turns=recent_turns or [],
        resolver=resolver,
    )


# ── Category 1: Preprocessing ─────────────────────────────────────────────────

class TestPreprocessing:

    def test_T01_extract_exact_course_codes(self):
        codes = extract_course_codes("Can I take C-CS301 and C-AI421?")
        assert "C-CS301" in codes
        assert "C-AI421" in codes

    def test_T01b_extract_humanistic_course(self):
        codes = extract_course_codes("I passed HUM011 last semester")
        assert "HUM011" in codes

    def test_T01c_extract_case_insensitive(self):
        codes = extract_course_codes("tell me about c-ai311")
        assert "C-AI311" in codes

    def test_T01d_no_codes_in_plain_text(self):
        codes = extract_course_codes("What is the warning policy?")
        assert codes == []

    def test_T02_policy_keyword_detection(self):
        assert detect_policy_signal("What is the withdrawal policy?") is True
        assert detect_policy_signal("What happens if I get a warning?") is True
        assert detect_policy_signal("Tell me about C-CS301") is False

    def test_T02b_policy_keyword_probation(self):
        assert detect_policy_signal("What happens if I am on probation?") is True

    def test_T03_out_of_scope_detection(self):
        assert detect_out_of_scope("How do I apply for financial aid?") is True
        assert detect_out_of_scope("What is the tuition fee?") is True
        assert detect_out_of_scope("What courses are in the AI track?") is False

    def test_T04_student_referential_detection(self):
        assert detect_student_referential("Can I take C-CS301?") is True
        assert detect_student_referential("What is my GPA?") is True
        assert detect_student_referential("Tell me about Algorithms") is False

    def test_T05_semester_parsing(self):
        assert parse_semester("Plan my Fall 2025 semester") == "Fall 2025"
        assert parse_semester("What about Spring 2026?") == "Spring 2026"
        assert parse_semester("Tell me about OS") is None

    def test_T06_target_cgpa_parsing(self):
        val = parse_target_cgpa("I want to reach a GPA of 3.5")
        assert val == 3.5

    def test_T06b_cgpa_out_of_range_rejected(self):
        # 5.0 is out of range for a 4.0-scale
        val = parse_target_cgpa("reach a GPA of 5.0")
        assert val is None

    def test_T06c_expected_grades_extraction(self):
        grades = extract_expected_grades("If I get A in C-CS301 and B+ in C-AI321")
        assert grades.get("C-CS301") == "A"
        assert grades.get("C-AI321") == "B+"

    def test_T06d_reset_assumptions_signal(self):
        from gateway.qu_preprocessing import detect_reset_signal
        assert detect_reset_signal("reset assumptions") is True
        assert detect_reset_signal("cancel what-if") is True


# ── Category 2: Schema / Intent Validation ────────────────────────────────────

class TestIntentValidation:

    def test_T07_all_locked_intents_count(self):
        assert len(LOCKED_INTENTS) == 26

    def test_T07b_all_expected_intents_present(self):
        expected_sample = {
            "plan_semester", "run_graduation_audit", "check_course_eligibility",
            "get_course_info", "get_course_prerequisites", "compute_skill_gap",
            "policy_query", "clarification_needed", "out_of_scope",
        }
        assert expected_sample.issubset(LOCKED_INTENTS)

    def test_T08_forbidden_intents_not_in_locked(self):
        for intent in FORBIDDEN_INTENTS:
            assert intent not in LOCKED_INTENTS, f"Forbidden intent {intent!r} found in LOCKED_INTENTS"

    def test_T09_structured_query_has_params(self):
        sq = StructuredQuery(intent="get_course_info", original_text="test")
        assert hasattr(sq, "params")
        assert isinstance(sq.params, dict)

    def test_T09b_structured_query_has_student_referential_flag(self):
        sq = StructuredQuery(intent="run_graduation_audit", original_text="test")
        assert hasattr(sq, "student_referential_fallback")
        assert sq.student_referential_fallback is False

    def test_T09c_validate_intents_raises_on_invalid(self):
        with pytest.raises(IntentValidationError):
            _validate_intents([{"intent": "get_prerequisites"}], LOCKED_INTENTS)

    def test_T09d_validate_intents_passes_on_valid(self):
        _validate_intents([{"intent": "get_course_prerequisites"}], LOCKED_INTENTS)


# ── Category 3: Deterministic Fallback ───────────────────────────────────────

class TestDeterministicFallback:

    def _fallback(self, text: str) -> list[StructuredQuery]:
        pre = preprocess(text)
        return _deterministic_fallback(text, pre)

    def test_T10_policy_signal_returns_policy_query(self):
        result = self._fallback("What is the withdrawal policy?")
        assert result[0].intent == "policy_query"

    def test_T11_out_of_scope_signal_returns_out_of_scope(self):
        result = self._fallback("How do I apply for financial aid?")
        assert result[0].intent == "out_of_scope"

    def test_T12_course_code_plus_eligibility_keywords(self):
        result = self._fallback("Can I take C-CS301?")
        assert result[0].intent == "check_course_eligibility"
        assert result[0].entities.course_code == "C-CS301"
        assert result[0].student_referential_fallback is True

    def test_T13_course_code_plus_prerequisite_keywords(self):
        result = self._fallback("What are the prerequisites for C-AI311?")
        assert result[0].intent == "get_course_prerequisites"
        assert result[0].entities.course_code == "C-AI311"

    def test_T14_course_code_alone_returns_course_info(self):
        result = self._fallback("Tell me about C-DE312")
        assert result[0].intent == "get_course_info"
        assert result[0].entities.course_code == "C-DE312"

    def test_T15_graduation_keywords_returns_audit(self):
        result = self._fallback("Can I graduate this semester?")
        assert result[0].intent == "run_graduation_audit"
        assert result[0].student_referential_fallback is True

    def test_T16_no_signal_returns_clarification(self):
        result = self._fallback("xxxxxx")
        assert result[0].intent == "clarification_needed"

    def test_T16b_empty_text_returns_clarification(self):
        result = self._fallback("")
        assert result[0].intent == "clarification_needed"

    def test_T16c_reset_assumptions_fallback(self):
        result = self._fallback("Please reset assumptions")
        assert result[0].intent == "get_student_record"
        assert result[0].session_overrides.override_action == "clear"

    def test_T16d_override_before_policy_fallback(self):
        # Text has both override ("assume") and policy ("what happens if")
        result = self._fallback("Assume I fail algorithms, what happens if...?")
        # Override signal is present, so policy_query should be skipped, returning clarification or another intent
        # In deterministic fallback, without specific course code, it will hit clarification
        assert result[0].intent == "clarification_needed"

    def test_T16e_yes_alone_returns_clarification(self):
        result = self._fallback("yes")
        assert result[0].intent == "clarification_needed"
        assert result[0].session_overrides.override_action != "clear"

    def test_T16f_sure_returns_clarification(self):
        result = self._fallback("sure, go ahead")
        assert result[0].intent == "clarification_needed"
        assert result[0].session_overrides.override_action != "clear"


# ── Category 4: LLM Mock Parsing ─────────────────────────────────────────────

class TestLLMParsing:

    def test_T17_single_intent_parsed_correctly(self, monkeypatch):
        response = _sq_json(
            "get_course_info",
            original_text="Tell me about C-CS301",
            entities={"course_code": "C-CS301", "role": None, "track": None, "skill": None},
        )
        client = _make_mock_client([response])
        result = _run_qu("Tell me about C-CS301", client, monkeypatch=monkeypatch)

        assert len(result) == 1
        assert result[0].intent == "get_course_info"
        assert result[0].entities.course_code == "C-CS301"
        assert result[0].student_referential_fallback is False

    def test_T18_compound_query_produces_multiple_sqs(self, monkeypatch):
        sq1 = {
            "intent": "run_graduation_audit",
            "original_text": "Can I graduate?",
            "entities": {"course_code": None, "role": None, "track": None, "skill": None},
            "secondary_entities": None,
            "params": {},
            "session_overrides": {"added_courses": [], "assumed_passed_courses": [], "assumed_failed_courses": [], "target_role": None, "course_override_type": "none", "override_action": "accumulate"},
            "student_referential_fallback": True,
        }
        sq2 = {
            "intent": "generate_graduation_roadmap",
            "original_text": "Give me a roadmap",
            "entities": {"course_code": None, "role": None, "track": None, "skill": None},
            "secondary_entities": None,
            "params": {},
            "session_overrides": {"added_courses": [], "assumed_passed_courses": [], "assumed_failed_courses": [], "target_role": None, "course_override_type": "none", "override_action": "accumulate"},
            "student_referential_fallback": True,
        }
        response = json.dumps({"queries": [sq1, sq2]})
        client = _make_mock_client([response])
        result = _run_qu("Can I graduate, and if not give me a roadmap?", client, monkeypatch=monkeypatch)

        assert len(result) == 2
        assert result[0].intent == "run_graduation_audit"
        assert result[1].intent == "generate_graduation_roadmap"
        assert result[0].student_referential_fallback is True
        assert result[1].student_referential_fallback is True

    def test_T19_policy_query_with_rewritten_text(self, monkeypatch):
        response = _sq_json(
            "policy_query",
            original_text="What is the academic warning policy?",
        )
        client = _make_mock_client([response])
        result = _run_qu("What happens if I get a warning?", client, monkeypatch=monkeypatch)

        assert result[0].intent == "policy_query"
        assert "warning" in result[0].original_text.lower()

    def test_T20_student_referential_flag_set(self, monkeypatch):
        response = _sq_json(
            "check_course_eligibility",
            original_text="Can I take C-AI311?",
            entities={"course_code": "C-AI311", "role": None, "track": None, "skill": None},
            student_referential_fallback=True,
        )
        client = _make_mock_client([response])
        result = _run_qu("Can I take C-AI311?", client, monkeypatch=monkeypatch)

        assert result[0].student_referential_fallback is True

    def test_T21_assumed_done_override_detected(self, monkeypatch):
        response = _sq_json(
            "check_course_eligibility",
            original_text="Assume I took C-AI311, can I take C-AI412?",
            entities={"course_code": "C-AI412", "role": None, "track": None, "skill": None},
            session_overrides={
                "added_courses": ["C-AI311"],
                "assumed_passed_courses": [],
                "assumed_failed_courses": [],
                "target_role": None,
                "course_override_type": "assumed_done",
                "override_action": "accumulate",
            },
            student_referential_fallback=True,
        )
        client = _make_mock_client([response])
        result = _run_qu("Assume I took C-AI311, can I take C-AI412?", client, monkeypatch=monkeypatch)

        sq = result[0]
        assert sq.intent == "check_course_eligibility"
        assert "C-AI311" in sq.session_overrides.added_courses
        assert sq.session_overrides.course_override_type == "assumed_done"

    def test_T22_assumed_passed_and_failed_overrides(self, monkeypatch):
        response = json.dumps({"queries": [{
            "intent": "check_course_eligibility",
            "original_text": "Assume I passed C-AI311",
            "entities": {"course_code": None, "role": None, "track": None, "skill": None},
            "secondary_entities": None,
            "params": {},
            "session_overrides": {
                "added_courses": [],
                "assumed_passed_courses": ["C-AI311"],
                "assumed_failed_courses": [],
                "target_role": None,
                "course_override_type": "assumed_passed",
                "override_action": "accumulate",
            },
            "student_referential_fallback": True,
        }]})
        client = _make_mock_client([response])
        result = _run_qu("Assume I passed C-AI311", client, monkeypatch=monkeypatch)

        sq = result[0]
        assert "C-AI311" in sq.session_overrides.assumed_passed_courses
        assert sq.session_overrides.course_override_type == "assumed_passed"

    def test_T23_expected_grades_in_params_not_override(self, monkeypatch):
        response = _sq_json(
            "simulate_gpa_forward",
            original_text="If I get A in OS, what will my GPA be?",
            entities={"course_code": "OS", "role": None, "track": None, "skill": None},
            params={"expected_grades": {"OS": "A"}},
            student_referential_fallback=True,
        )
        client = _make_mock_client([response])
        result = _run_qu("If I get A in OS, what will my GPA be?", client, monkeypatch=monkeypatch)

        sq = result[0]
        assert sq.intent == "simulate_gpa_forward"
        # expected_grades must be in params, NOT in session_overrides
        assert "expected_grades" in sq.params
        assert sq.session_overrides.course_override_type == "none"
        assert sq.session_overrides.added_courses == []

    def test_T24_clarification_needed_intent(self, monkeypatch):
        response = _sq_json(
            "clarification_needed",
            original_text="Which course did you mean?",
        )
        client = _make_mock_client([response])
        result = _run_qu("tell me about it", client, monkeypatch=monkeypatch)

        assert result[0].intent == "clarification_needed"


# ── Category 5: Anti-Forbidden Intent ────────────────────────────────────────

class TestAntiForbiddenIntent:

    def test_T25_courses_in_track_maps_to_track_overview_not_invented(self, monkeypatch):
        response = _sq_json(
            "get_track_overview",
            original_text="What courses are in the AI track?",
            entities={"course_code": None, "role": None, "track": "AI", "skill": None},
        )
        client = _make_mock_client([response])
        result = _run_qu("What courses are in the AI track?", client, monkeypatch=monkeypatch)

        assert result[0].intent == "get_track_overview"
        assert result[0].intent != "get_courses_in_track"

    def test_T26_can_i_take_maps_to_check_course_eligibility_not_old_name(self, monkeypatch):
        response = _sq_json(
            "check_course_eligibility",
            original_text="Can I take OS?",
            entities={"course_code": "OS", "role": None, "track": None, "skill": None},
            student_referential_fallback=True,
        )
        client = _make_mock_client([response])
        result = _run_qu("Can I take OS?", client, monkeypatch=monkeypatch)

        assert result[0].intent == "check_course_eligibility"
        assert result[0].intent != "check_eligibility"

    def test_T27_prerequisites_maps_to_locked_intent_not_old_name(self, monkeypatch):
        response = _sq_json(
            "get_course_prerequisites",
            original_text="Prerequisites for OS",
            entities={"course_code": "OS", "role": None, "track": None, "skill": None},
        )
        client = _make_mock_client([response])
        result = _run_qu("Prerequisites for OS?", client, monkeypatch=monkeypatch)

        assert result[0].intent == "get_course_prerequisites"
        assert result[0].intent != "get_prerequisites"

    def test_T28_policy_maps_to_policy_query_not_handbook_query(self, monkeypatch):
        response = _sq_json(
            "policy_query",
            original_text="What is the academic warning policy?",
        )
        client = _make_mock_client([response])
        result = _run_qu("What is the warning policy?", client, monkeypatch=monkeypatch)

        assert result[0].intent == "policy_query"
        assert result[0].intent != "handbook_query"

    def test_T29_llm_returning_forbidden_intent_triggers_fallback(self, monkeypatch):
        # Model returns forbidden intent → validation fails → chain exhausts → deterministic fallback
        forbidden_response = json.dumps({"queries": [{"intent": "get_prerequisites", "original_text": "prereqs"}]})
        # Fallback for "prerequisites for C-CS301" → get_course_prerequisites (deterministic)
        client = _make_mock_client([forbidden_response, forbidden_response, forbidden_response, forbidden_response])
        result = _run_qu("What are the prerequisites for C-CS301?", client, monkeypatch=monkeypatch)

        # All models returned forbidden intent → deterministic fallback
        assert result[0].intent in LOCKED_INTENTS
        assert result[0].intent not in FORBIDDEN_INTENTS

    def test_T30_invented_intent_rejected_by_validator(self):
        with pytest.raises(IntentValidationError):
            _validate_intents([{"intent": "some_invented_intent_xyz"}], LOCKED_INTENTS)


# ── Category 6: Edge Cases ────────────────────────────────────────────────────

class TestEdgeCases:

    def test_T31_empty_text_returns_clarification(self, monkeypatch):
        # Even with LLM, empty text should return clarification
        response = _sq_json("clarification_needed", original_text="Please ask a question.")
        client = _make_mock_client([response])
        result = _run_qu("", client, monkeypatch=monkeypatch)

        assert len(result) >= 1
        assert result[0].intent in LOCKED_INTENTS

    def test_T32_all_llms_fail_deterministic_runs(self, monkeypatch):
        # Make LLM raise LLMError on every call — simulates all models failing
        from gateway.llm_client import LLMError
        client = MagicMock()
        client.is_configured.return_value = True
        client.chat.side_effect = LLMError("timeout")

        result = _run_qu("What is the warning policy?", client, monkeypatch=monkeypatch)

        assert len(result) >= 1
        assert result[0].intent in LOCKED_INTENTS
        # "warning policy" hits policy keyword → policy_query
        assert result[0].intent == "policy_query"

    def test_T33_llm_not_configured_deterministic_runs(self, monkeypatch):
        client = MagicMock()
        client.is_configured.return_value = False

        result = _run_qu("Can I take C-CS301?", client, monkeypatch=monkeypatch)

        assert len(result) >= 1
        assert result[0].intent in LOCKED_INTENTS
        assert result[0].intent == "check_course_eligibility"

    def test_T33b_result_always_non_empty(self, monkeypatch):
        from gateway.llm_client import LLMError
        client = MagicMock()
        client.is_configured.return_value = True
        client.chat.side_effect = LLMError("rate limit 429")

        result = _run_qu("fjdklsjfklsdfjklsd", client, monkeypatch=monkeypatch)

        assert isinstance(result, list)
        assert len(result) >= 1

    def test_parse_raw_sq_rejects_unknown_intent(self):
        raw = {"intent": "made_up_intent", "original_text": "test"}
        sq = _parse_raw_sq(raw, "test")
        assert sq.intent == "clarification_needed"

    def test_parse_raw_sq_valid_intent_preserved(self):
        raw = {
            "intent": "get_course_info",
            "original_text": "Tell me about Algorithms",
            "entities": {"course_code": "C-CS302", "role": None, "track": None, "skill": None},
            "params": {},
            "session_overrides": {
                "added_courses": [], "assumed_passed_courses": [], "assumed_failed_courses": [],
                "target_role": None, "course_override_type": "none", "override_action": "accumulate",
            },
            "student_referential_fallback": False,
        }
        sq = _parse_raw_sq(raw, "Tell me about Algorithms")
        assert sq.intent == "get_course_info"
        assert sq.entities.course_code == "C-CS302"

    def test_parse_raw_sq_params_validation(self):
        raw = {
            "intent": "solve_target_gpa",
            "original_text": "test",
            "params": {
                "target_gpa": "3.5",
                "depth": "ALL",
                "expected_grades": ["OS", "A"] # invalid list
            }
        }
        sq = _parse_raw_sq(raw, "test")
        assert sq.params.get("target_gpa") == 3.5
        assert sq.params.get("depth") == "full"
        assert "expected_grades" not in sq.params

    def test_parse_raw_sq_invalid_target_gpa(self):
        raw = {"intent": "solve_target_gpa", "params": {"target_gpa": "5.0"}}
        sq = _parse_raw_sq(raw, "test")
        assert "target_gpa" not in sq.params

    def test_parse_raw_sq_target_cgpa_alias(self):
        raw = {"intent": "solve_target_gpa", "params": {"target_cgpa": "3.5"}}
        sq = _parse_raw_sq(raw, "test")
        assert sq.params.get("target_gpa") == 3.5
        assert "target_cgpa" not in sq.params

    def test_parse_raw_sq_target_cgpa_out_of_range(self):
        raw = {"intent": "solve_target_gpa", "params": {"target_cgpa": "5.5"}}
        sq = _parse_raw_sq(raw, "test")
        assert "target_gpa" not in sq.params
        assert "target_cgpa" not in sq.params

    def test_parse_raw_sq_target_gpa_takes_precedence_over_cgpa(self):
        raw = {"intent": "solve_target_gpa", "params": {"target_gpa": "3.0", "target_cgpa": "2.0"}}
        sq = _parse_raw_sq(raw, "test")
        assert sq.params.get("target_gpa") == 3.0
        assert "target_cgpa" not in sq.params

    def test_prompt_mentions_last_skill(self):
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "last_skill" in prompt

    def test_no_pii_in_user_message(self):
        from gateway.qu_prompt import build_user_message
        last_ref = LastReferenced(course_code="C-CS301")
        # recent_turns intentionally have no student IDs, grades, or CGPA
        turns = [{"user": "Can I graduate?", "answer": "Let me check."}]
        msg = build_user_message("What about prerequisites?", last_ref, turns)
        assert "student_id" not in msg.lower()
        assert "cgpa" not in msg.lower() or "3." not in msg  # no raw GPA numbers

    def test_build_user_message_privacy(self):
        from gateway.qu_prompt import build_user_message
        last_ref = LastReferenced()
        turns = [{"user": "hello", "answer": "raw answer with CGPA 2.85"}]
        msg = build_user_message("test", last_ref, turns)
        assert "hello" in msg
        assert "raw answer" not in msg

    def test_build_user_message_last_skill(self):
        from gateway.qu_prompt import build_user_message
        last_ref = LastReferenced(skill_id="SK_Python")
        msg = build_user_message("test", last_ref, [])
        assert "last_skill=SK_Python" in msg

    def test_extract_sq_list_handles_bare_single_object(self):
        data = {"intent": "get_course_info", "original_text": "test"}
        result = _extract_sq_list(data)
        assert len(result) == 1
        assert result[0]["intent"] == "get_course_info"

    def test_extract_sq_list_handles_queries_array(self):
        data = {"queries": [{"intent": "get_course_info"}, {"intent": "run_graduation_audit"}]}
        result = _extract_sq_list(data)
        assert len(result) == 2


# ── Resolver Mock Tests ───────────────────────────────────────────────────────

class TestEntityResolution:

    def _ok_resolver(self, entity_type: str, entity_text: str) -> dict:
        return {"status": "ok", "id": f"{entity_type.upper()}_{entity_text.upper().replace(' ', '_')}"}

    def _ambiguous_resolver(self, entity_type: str, entity_text: str) -> dict:
        return {
            "status": "ambiguous",
            "matches": [{"id": "OPT_A", "name": "Option A"}, {"id": "OPT_B", "name": "Option B"}],
        }

    def _not_found_resolver(self, entity_type: str, entity_text: str) -> dict:
        return {"status": "not_found"}

    def test_canonical_course_code_bypasses_resolver(self, monkeypatch):
        response = _sq_json(
            "get_course_info",
            original_text="Tell me about C-CS301",
            entities={"course_code": "C-CS301", "role": None, "track": None, "skill": None},
        )
        client = _make_mock_client([response])

        # Resolver should NOT be called for canonical codes
        resolver_calls = []
        def tracking_resolver(entity_type, entity_text):
            resolver_calls.append((entity_type, entity_text))
            return {"status": "ok", "id": entity_text}

        result = _run_qu(
            "Tell me about C-CS301",
            client,
            monkeypatch=monkeypatch,
            resolver=tracking_resolver,
        )
        # Resolver was not called for the canonical course code
        course_calls = [c for c in resolver_calls if c[0] == "course"]
        assert course_calls == []
        assert result[0].entities.course_code == "C-CS301"

    def test_ambiguous_entity_returns_clarification(self, monkeypatch):
        response = _sq_json(
            "get_course_info",
            original_text="Tell me about Algorithms",
            entities={"course_code": "Algorithms", "role": None, "track": None, "skill": None},
        )
        client = _make_mock_client([response])
        result = _run_qu(
            "Tell me about Algorithms",
            client,
            monkeypatch=monkeypatch,
            resolver=self._ambiguous_resolver,
        )
        assert result[0].intent == "clarification_needed"

    def test_not_found_entity_returns_clarification(self, monkeypatch):
        response = _sq_json(
            "get_role_profile",
            original_text="Tell me about quantum_wizard role",
            entities={"course_code": None, "role": "quantum_wizard", "track": None, "skill": None},
        )
        client = _make_mock_client([response])
        result = _run_qu(
            "Tell me about quantum wizard role",
            client,
            monkeypatch=monkeypatch,
            resolver=self._not_found_resolver,
        )
        assert result[0].intent == "clarification_needed"

    def test_successful_role_resolution(self, monkeypatch):
        response = _sq_json(
            "get_role_profile",
            original_text="Tell me about Data Scientist",
            entities={"course_code": None, "role": "data scientist", "track": None, "skill": None},
        )
        client = _make_mock_client([response])
        result = _run_qu(
            "Tell me about Data Scientist",
            client,
            monkeypatch=monkeypatch,
            resolver=self._ok_resolver,
        )
        assert result[0].intent == "get_role_profile"
        assert result[0].entities.role_id is not None

    def test_resolved_id_key_correctly_updates_entity(self, monkeypatch):
        """Verify resolver response using 'resolved_id' key (actual KG format) is read correctly."""
        response = _sq_json(
            "get_role_profile",
            original_text="Tell me about Data Scientist",
            entities={"course_code": None, "role": "data scientist", "track": None, "skill": None},
        )
        client = _make_mock_client([response])

        # Actual KG resolver returns "resolved_id", not "id"
        def kg_format_resolver(entity_type: str, entity_text: str) -> dict:
            return {
                "status": "ok",
                "resolved_id": "data_scientist",
                "name": "Data Scientist",
                "match_type": "exact_name",
                "confidence": 1.0,
                "matches": [{"id": "data_scientist", "name": "Data Scientist"}],
            }

        result = _run_qu(
            "Tell me about Data Scientist",
            client,
            monkeypatch=monkeypatch,
            resolver=kg_format_resolver,
        )
        assert result[0].intent == "get_role_profile"
        assert result[0].entities.role_id == "data_scientist"

    def test_resolver_failure_safety_exception(self, monkeypatch):
        response = _sq_json("get_course_info", entities={"course_code": "Algorithms"})
        client = _make_mock_client([response])
        def raising_resolver(et, txt):
            raise ValueError("Mock exception")
        result = _run_qu("Tell me about Algorithms", client, monkeypatch=monkeypatch, resolver=raising_resolver)
        assert result[0].intent == "clarification_needed"

    def test_resolver_failure_safety_missing_id(self, monkeypatch):
        response = _sq_json("get_course_info", entities={"course_code": "Algorithms"})
        client = _make_mock_client([response])
        def bad_ok_resolver(et, txt):
            return {"status": "ok"} # missing id
        result = _run_qu("Tell me about Algorithms", client, monkeypatch=monkeypatch, resolver=bad_ok_resolver)
        assert result[0].intent == "clarification_needed"

    def test_override_course_list_resolution(self, monkeypatch):
        response = _sq_json(
            "plan_semester",
            session_overrides={"assumed_passed_courses": ["oop", "C-CS112"], "assumed_failed_courses": ["os"], "added_courses": ["intro ai"], "course_override_type": "none", "override_action": "accumulate"}
        )
        client = _make_mock_client([response])
        def tracking_resolver(et, txt):
            if txt.upper() == "C-CS112":
                return {"status": "ok", "id": "C-CS112"}
            return {"status": "ok", "id": f"RESOLVED_{txt.upper()}"}
            
        result = _run_qu("Assume I passed oop...", client, monkeypatch=monkeypatch, resolver=tracking_resolver)
        sq = result[0]
        assert "RESOLVED_OOP" in sq.session_overrides.assumed_passed_courses
        assert "C-CS112" in sq.session_overrides.assumed_passed_courses
        assert "RESOLVED_OS" in sq.session_overrides.assumed_failed_courses
        assert "RESOLVED_INTRO AI" in sq.session_overrides.added_courses

    def test_expected_grades_key_resolution(self, monkeypatch):
        response = _sq_json(
            "simulate_gpa_forward",
            params={"expected_grades": {"os": "A", "oop": "90"}}
        )
        client = _make_mock_client([response])
        def tracking_resolver(et, txt):
            return {"status": "ok", "id": f"C_{txt.upper()}"}
            
        result = _run_qu("If I get A in os...", client, monkeypatch=monkeypatch, resolver=tracking_resolver)
        sq = result[0]
        assert sq.params["expected_grades"]["C_OS"] == "A"
        assert sq.params["expected_grades"]["C_OOP"] == "90"
        assert "os" not in sq.params["expected_grades"]

    def test_non_strict_course_code_uses_resolver(self, monkeypatch):
        response = _sq_json(
            "get_course_info",
            entities={"course_code": "CS219"} # No C- prefix
        )
        client = _make_mock_client([response])
        def tracking_resolver(et, txt):
            return {"status": "ok", "id": "C-CS219"}

        result = _run_qu("Tell me about CS219", client, monkeypatch=monkeypatch, resolver=tracking_resolver)
        assert result[0].entities.course_code == "C-CS219"

    def test_resolver_unknown_status_returns_clarification(self, monkeypatch):
        response = _sq_json("get_course_info", entities={"course_code": "Algorithms"})
        client = _make_mock_client([response])
        def unknown_status_resolver(et, txt):
            return {"status": "some_brand_new_unknown_status"}
        result = _run_qu("Tell me about Algorithms", client, monkeypatch=monkeypatch, resolver=unknown_status_resolver)
        assert result[0].intent == "clarification_needed"

    def test_expected_grades_values_preserved_as_strings(self, monkeypatch):
        """Grade values (letter, percentage, decimal) must be preserved as strings; ALE owns final validation."""
        response = _sq_json(
            "simulate_gpa_forward",
            params={"expected_grades": {"C-CS301": "B+", "C-AI311": "90", "C-CS218": 3.7}},
        )
        client = _make_mock_client([response])
        def identity_resolver(et, txt):
            return {"status": "ok", "id": txt}

        result = _run_qu("If I get B+ in C-CS301...", client, monkeypatch=monkeypatch, resolver=identity_resolver)
        grades = result[0].params.get("expected_grades", {})
        assert grades.get("C-CS301") == "B+"
        assert grades.get("C-AI311") == "90"
        assert grades.get("C-CS218") == "3.7"

    def test_resolver_none_nulls_noncanonical_course(self, monkeypatch):
        """Without resolver, non-canonical course names must be nulled out (not passed raw to KG)."""
        response = _sq_json(
            "get_course_info",
            entities={"course_code": "Operating Systems", "role": None, "track": None, "skill": None},
        )
        client = _make_mock_client([response])
        result = _run_qu("Tell me about Operating Systems", client, monkeypatch=monkeypatch, resolver=None)
        assert result[0].entities.course_code is None

    def test_resolver_none_keeps_canonical_course(self, monkeypatch):
        """Without resolver, C-prefixed canonical codes must be passed through."""
        response = _sq_json(
            "get_course_info",
            entities={"course_code": "C-CS316", "role": None, "track": None, "skill": None},
        )
        client = _make_mock_client([response])
        result = _run_qu("Tell me about C-CS316", client, monkeypatch=monkeypatch, resolver=None)
        assert result[0].entities.course_code == "C-CS316"

    def test_resolver_none_nulls_noncanonical_role(self, monkeypatch):
        """Without resolver, natural role names must be nulled out."""
        response = _sq_json(
            "get_role_profile",
            entities={"course_code": None, "role": "Data Scientist", "track": None, "skill": None},
        )
        client = _make_mock_client([response])
        result = _run_qu("Tell me about Data Scientist", client, monkeypatch=monkeypatch, resolver=None)
        assert result[0].entities.role_id is None

    def test_resolver_none_keeps_canonical_track(self, monkeypatch):
        """Without resolver, canonical track IDs (AI/CYS/DSE/SWE/GEN) must pass through."""
        response = _sq_json(
            "get_track_overview",
            entities={"course_code": None, "role": None, "track": "AI", "skill": None},
        )
        client = _make_mock_client([response])
        result = _run_qu("Tell me about AI track", client, monkeypatch=monkeypatch, resolver=None)
        assert result[0].entities.track_id == "AI"


# ── Boundary Audit Tests ──────────────────────────────────────────────────────

class TestBoundaryAudit:
    """Step 6D-0: focused boundary regression tests for intent taxonomy audit."""

    # ── Depth in deterministic fallback ──────────────────────────────────────

    def test_depth_full_prerequisites_deterministic(self):
        """Deterministic fallback: 'full prerequisites' → depth='full'."""
        pre = preprocess("What are the full prerequisites for C-CS301?")
        result = _deterministic_fallback("What are the full prerequisites for C-CS301?", pre)
        assert result[0].intent == "get_course_prerequisites"
        assert result[0].params.get("depth") == "full"

    def test_depth_direct_prerequisites_deterministic(self):
        """Deterministic fallback: 'prerequisites' (no full/complete/etc.) → depth='direct'."""
        pre = preprocess("What are the prerequisites for C-CS301?")
        result = _deterministic_fallback("What are the prerequisites for C-CS301?", pre)
        assert result[0].intent == "get_course_prerequisites"
        assert result[0].params.get("depth") == "direct"

    def test_depth_complete_prerequisites_deterministic(self):
        """Deterministic fallback: 'complete prerequisites' → depth='full'."""
        pre = preprocess("Give me the complete prerequisites for C-AI311")
        result = _deterministic_fallback("Give me the complete prerequisites for C-AI311", pre)
        assert result[0].intent == "get_course_prerequisites"
        assert result[0].params.get("depth") == "full"

    # ── Semester params normalization ─────────────────────────────────────────

    def test_explicit_semester_params_normalized(self):
        """target_semester 'FALL 2026' normalizes to 'Fall 2026'; type normalizes; source preserved."""
        raw = {
            "intent": "plan_semester",
            "params": {
                "target_semester": "FALL 2026",
                "target_semester_type": "fall",
                "semester_resolution_source": "explicit",
            },
        }
        sq = _parse_raw_sq(raw, "help me plan Fall 2026")
        assert sq.params.get("target_semester") == "Fall 2026"
        assert sq.params.get("target_semester_type") == "Fall"
        assert sq.params.get("semester_resolution_source") == "explicit"

    def test_relative_semester_params_preserved(self):
        """target_semester_text for relative phrase is preserved; no target_semester key."""
        raw = {
            "intent": "plan_semester",
            "params": {
                "target_semester_text": "two falls from now",
                "semester_resolution_source": "relative",
            },
        }
        sq = _parse_raw_sq(raw, "plan two falls from now")
        assert sq.params.get("target_semester_text") == "two falls from now"
        assert sq.params.get("semester_resolution_source") == "relative"
        assert "target_semester" not in sq.params

    def test_next_semester_relative_preserved(self):
        """'next semester' relative phrase is preserved as target_semester_text."""
        raw = {
            "intent": "plan_semester",
            "params": {
                "target_semester_text": "next semester",
                "semester_resolution_source": "relative",
            },
        }
        sq = _parse_raw_sq(raw, "what should I register next semester")
        assert sq.params.get("target_semester_text") == "next semester"
        assert sq.params.get("semester_resolution_source") == "relative"

    def test_invalid_semester_type_rejected(self):
        """Invalid target_semester_type ('winter') is deleted."""
        raw = {"intent": "plan_semester", "params": {"target_semester_type": "winter"}}
        sq = _parse_raw_sq(raw, "plan my winter semester")
        assert "target_semester_type" not in sq.params

    def test_invalid_semester_format_rejected(self):
        """target_semester without year ('next Fall') is deleted."""
        raw = {"intent": "plan_semester", "params": {"target_semester": "next Fall"}}
        sq = _parse_raw_sq(raw, "plan next fall")
        assert "target_semester" not in sq.params

    def test_invalid_resolution_source_rejected(self):
        """Unknown semester_resolution_source is deleted."""
        raw = {"intent": "plan_semester", "params": {"semester_resolution_source": "auto"}}
        sq = _parse_raw_sq(raw, "plan semester")
        assert "semester_resolution_source" not in sq.params

    # ── Prompt boundary string tests ──────────────────────────────────────────

    def test_prompt_plan_semester_registration_only_boundary(self):
        """Prompt must explicitly state plan_semester is for REGISTRATION SCHEDULING only."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "REGISTRATION SCHEDULING ONLY" in prompt or "COURSE REGISTRATION" in prompt

    def test_prompt_career_learning_not_plan_semester(self):
        """Prompt must explicitly state career learning queries do NOT use plan_semester."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "NEVER use plan_semester" in prompt

    def test_prompt_focus_courses_personal_signal_words(self):
        """Prompt must list personal signal words (still/remaining/left) for get_focus_courses_for_target."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "still" in prompt and "remaining" in prompt and "left" in prompt

    def test_prompt_estimate_alignment_planned_courses_rule(self):
        """Prompt must instruct LLM to extract planned_courses for estimate_alignment_improvement."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "estimate_alignment_improvement" in prompt
        assert "planned_courses" in prompt

    def test_prompt_semester_extraction_section_present(self):
        """Prompt must contain SEMESTER EXTRACTION section with explicit/relative guidance."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "SEMESTER EXTRACTION" in prompt
        assert "semester_resolution_source" in prompt
        assert "target_semester_text" in prompt

    # ── LLM mock: get_focus_courses_for_target student-referential boundary ───

    def test_focus_courses_general_not_student_referential(self, monkeypatch):
        """'Important courses for data scientist' (general) → student_referential_fallback=false."""
        response = _sq_json(
            "get_focus_courses_for_target",
            original_text="Important courses for data scientist",
            entities={"course_code": None, "role": "data_scientist", "track": None, "skill": None},
            student_referential_fallback=False,
        )
        client = _make_mock_client([response])
        result = _run_qu("What are the important courses for data scientist?", client, monkeypatch=monkeypatch)
        assert result[0].intent == "get_focus_courses_for_target"
        assert result[0].student_referential_fallback is False

    def test_focus_courses_remaining_is_student_referential(self, monkeypatch):
        """'What focus courses should I still take for data scientist' → student_referential_fallback=true."""
        response = _sq_json(
            "get_focus_courses_for_target",
            original_text="What focus courses should I still take for data scientist?",
            entities={"course_code": None, "role": "data_scientist", "track": None, "skill": None},
            student_referential_fallback=True,
        )
        client = _make_mock_client([response])
        result = _run_qu("What focus courses should I still take for data scientist?", client, monkeypatch=monkeypatch)
        assert result[0].intent == "get_focus_courses_for_target"
        assert result[0].student_referential_fallback is True

    def test_estimate_alignment_improvement_planned_courses_extracted(self, monkeypatch):
        """estimate_alignment_improvement with explicit planned_courses preserves them in params."""
        response = _sq_json(
            "estimate_alignment_improvement",
            entities={"course_code": None, "role": "data_scientist", "track": None, "skill": None},
            params={"planned_courses": ["C-AI311", "C-CS301"]},
            student_referential_fallback=True,
        )
        client = _make_mock_client([response])
        result = _run_qu(
            "If I take C-AI311 and C-CS301, how much better is my data scientist alignment?",
            client, monkeypatch=monkeypatch,
        )
        sq = result[0]
        assert sq.intent == "estimate_alignment_improvement"
        assert "planned_courses" in sq.params
        assert "C-AI311" in sq.params["planned_courses"]

    def test_estimate_alignment_improvement_no_courses_clarifies(self, monkeypatch):
        """estimate_alignment_improvement with no planned courses → LLM returns clarification."""
        response = _sq_json(
            "clarification_needed",
            original_text="Which courses are you planning to take for the alignment check?",
        )
        client = _make_mock_client([response])
        result = _run_qu(
            "How much better would my data scientist alignment be?",
            client, monkeypatch=monkeypatch,
        )
        assert result[0].intent == "clarification_needed"

    def test_planned_courses_non_list_removed(self):
        """planned_courses that is not a list is removed by _parse_raw_sq."""
        raw = {"intent": "estimate_alignment_improvement", "params": {"planned_courses": "C-AI311"}}
        sq = _parse_raw_sq(raw, "test")
        assert "planned_courses" not in sq.params

    def test_planned_courses_list_preserved_as_strings(self):
        """planned_courses list is preserved and coerced to strings."""
        raw = {"intent": "estimate_alignment_improvement", "params": {"planned_courses": ["C-AI311", "C-CS301"]}}
        sq = _parse_raw_sq(raw, "test")
        assert sq.params["planned_courses"] == ["C-AI311", "C-CS301"]


# ── Model Chain Config Tests ──────────────────────────────────────────────────

class TestModelChainConfig:
    """Step 6D-0.5: focused model chain, timeout, and context-turns config tests."""

    def test_load_model_chain_dedup(self, monkeypatch):
        """Primary model duplicated in fallbacks is deduplicated, keeping position."""
        monkeypatch.setenv("QU_PRIMARY_MODEL", "model-a")
        monkeypatch.setenv("QU_FALLBACK_MODELS", "model-a,model-b,model-c")
        chain = load_model_chain()
        assert chain.count("model-a") == 1
        assert chain == ["model-a", "model-b", "model-c"]

    def test_load_model_chain_primary_first(self, monkeypatch):
        """Primary model must always be the first element in the chain."""
        monkeypatch.setenv("QU_PRIMARY_MODEL", "primary-model")
        monkeypatch.setenv("QU_FALLBACK_MODELS", "fallback-a,fallback-b")
        chain = load_model_chain()
        assert chain[0] == "primary-model"
        assert chain == ["primary-model", "fallback-a", "fallback-b"]

    def test_load_model_chain_empty_fallback(self, monkeypatch):
        """Empty fallback string produces a single-model chain."""
        monkeypatch.setenv("QU_PRIMARY_MODEL", "only-model")
        monkeypatch.setenv("QU_FALLBACK_MODELS", "")
        chain = load_model_chain()
        assert chain == ["only-model"]

    def test_load_model_chain_strips_whitespace(self, monkeypatch):
        """Whitespace around model names in QU_FALLBACK_MODELS is stripped."""
        monkeypatch.setenv("QU_PRIMARY_MODEL", "model-a")
        monkeypatch.setenv("QU_FALLBACK_MODELS", " model-b , model-c ")
        chain = load_model_chain()
        assert chain == ["model-a", "model-b", "model-c"]

    def test_timeout_default_when_env_missing(self, monkeypatch):
        """QU_TIMEOUT_SECONDS defaults to 30.0 when env var is not set."""
        monkeypatch.delenv("QU_TIMEOUT_SECONDS", raising=False)
        timeout = _load_qu_timeout()
        assert timeout == 30.0

    def test_timeout_loads_from_env(self, monkeypatch):
        """QU_TIMEOUT_SECONDS value is correctly parsed from env."""
        monkeypatch.setenv("QU_TIMEOUT_SECONDS", "15")
        timeout = _load_qu_timeout()
        assert timeout == 15.0

    def test_timeout_invalid_falls_back_to_default(self, monkeypatch):
        """Non-numeric QU_TIMEOUT_SECONDS falls back to 30.0."""
        monkeypatch.setenv("QU_TIMEOUT_SECONDS", "not_a_number")
        timeout = _load_qu_timeout()
        assert timeout == 30.0

    def test_build_user_message_all_turns_included(self):
        """QU_CONTEXT_TURNS is now authoritative: all passed turns appear in message."""
        from gateway.qu_prompt import build_user_message
        last_ref = LastReferenced()
        turns = [
            {"user": "turn one query", "answer": "answer A"},
            {"user": "turn two query", "answer": "answer B"},
            {"user": "turn three query", "answer": "answer C"},
            {"user": "turn four query", "answer": "answer D"},
        ]
        msg = build_user_message("current query", last_ref, turns)
        assert "turn one query" in msg
        assert "turn two query" in msg
        assert "turn three query" in msg
        assert "turn four query" in msg

    def test_build_user_message_answer_text_stripped(self):
        """Previous answer_text must NOT appear in the user message sent to LLM (privacy guard)."""
        from gateway.qu_prompt import build_user_message
        last_ref = LastReferenced()
        turns = [{"user": "can I take OS?", "answer": "CGPA_SENSITIVE_DATA_XYZ"}]
        msg = build_user_message("test query", last_ref, turns)
        assert "CGPA_SENSITIVE_DATA_XYZ" not in msg
        assert "can I take OS?" in msg

    def test_build_user_message_user_text_truncated_at_100_chars(self):
        """Student turn user text is capped at 100 chars before sending to LLM."""
        from gateway.qu_prompt import build_user_message
        last_ref = LastReferenced()
        long_text = "A" * 200
        turns = [{"user": long_text, "answer": "some answer"}]
        msg = build_user_message("current query", last_ref, turns)
        assert "A" * 101 not in msg
        assert "A" * 100 in msg

    def test_production_fallback_chain_no_deprecated_models(self, monkeypatch):
        """Updated .env fallback chain must not contain models deprecated before Aug 16, 2026."""
        import os
        # Simulate the updated .env values
        monkeypatch.setenv("QU_PRIMARY_MODEL", "llama-3.3-70b-versatile")
        monkeypatch.setenv("QU_FALLBACK_MODELS", "openai/gpt-oss-120b,openai/gpt-oss-20b")
        chain = load_model_chain()
        # These two are shut down July 17, 2026 — must not appear in any chain
        assert "meta-llama/llama-4-scout-17b-16e-instruct" not in chain
        assert "qwen/qwen3-32b" not in chain
        # Production replacements must be present
        assert "openai/gpt-oss-120b" in chain
        assert "openai/gpt-oss-20b" in chain


# ── Alias Table Tests ─────────────────────────────────────────────────────────

# ── Logging Tests ─────────────────────────────────────────────────────────────

class TestQULogging:
    """Step 6-logging: verify QU emits safe diagnostic logs without PII."""

    def test_start_log_emitted_no_raw_text(self, monkeypatch, caplog):
        """QU.start must be emitted with query_len; raw user text must not appear."""
        response = _sq_json("get_course_info",
                            entities={"course_code": "C-CS301", "role": None, "track": None, "skill": None})
        client = _make_mock_client([response])
        secret = "UNIQUESECRETQUERY_XYZABC123"
        with caplog.at_level(logging.INFO, logger="gateway.query_understanding"):
            _run_qu(secret, client, monkeypatch=monkeypatch)
        start_records = [r for r in caplog.records if "QU.start" in r.message]
        assert start_records, "QU.start log not emitted"
        assert "query_len=" in start_records[0].message
        assert secret not in caplog.text

    def test_preprocess_log_emitted_with_signals(self, monkeypatch, caplog):
        """QU.preprocess must be emitted with correct boolean signal values."""
        response = _sq_json("policy_query")
        client = _make_mock_client([response])
        with caplog.at_level(logging.INFO, logger="gateway.query_understanding"):
            _run_qu("What is the withdrawal policy?", client, monkeypatch=monkeypatch)
        pre_records = [r for r in caplog.records if "QU.preprocess" in r.message]
        assert pre_records, "QU.preprocess log not emitted"
        assert "policy=True" in pre_records[0].message

    def test_result_log_contains_source_and_duration(self, monkeypatch, caplog):
        """QU.result must include source= and duration_ms= fields."""
        response = _sq_json("get_course_info")
        client = _make_mock_client([response])
        with caplog.at_level(logging.INFO, logger="gateway.query_understanding"):
            _run_qu("test query", client, monkeypatch=monkeypatch)
        result_records = [r for r in caplog.records if "QU.result" in r.message]
        assert result_records, "QU.result log not emitted"
        assert "source=" in result_records[0].message
        assert "duration_ms=" in result_records[0].message

    def test_all_models_failed_source_in_result(self, monkeypatch, caplog):
        """When all LLMs fail, QU.result source must be deterministic_fallback_all_models_failed."""
        from gateway.llm_client import LLMError
        client = MagicMock()
        client.is_configured.return_value = True
        client.chat.side_effect = LLMError("timeout")
        with caplog.at_level(logging.INFO, logger="gateway.query_understanding"):
            _run_qu("What is the withdrawal policy?", client, monkeypatch=monkeypatch)
        result_records = [r for r in caplog.records if "QU.result" in r.message]
        assert result_records, "QU.result log not emitted"
        assert "deterministic_fallback_all_models_failed" in result_records[0].message

    def test_llm_not_configured_source_in_result(self, monkeypatch, caplog):
        """When LLM not configured, QU.result source must be deterministic_fallback_llm_not_configured."""
        client = MagicMock()
        client.is_configured.return_value = False
        with caplog.at_level(logging.INFO, logger="gateway.query_understanding"):
            _run_qu("Can I take C-CS301?", client, monkeypatch=monkeypatch)
        result_records = [r for r in caplog.records if "QU.result" in r.message]
        assert result_records, "QU.result log not emitted"
        assert "deterministic_fallback_llm_not_configured" in result_records[0].message

    def test_resolver_failure_logs_safe_warning_no_mention(self, monkeypatch, caplog):
        """Entity resolution failure must log QU.resolve_failed without the raw entity mention."""
        response = _sq_json(
            "get_course_info",
            entities={"course_code": "SomeCourseName", "role": None, "track": None, "skill": None},
        )
        client = _make_mock_client([response])

        def not_found_resolver(et, txt):
            return {"status": "not_found"}

        with caplog.at_level(logging.WARNING, logger="gateway.query_understanding"):
            result = _run_qu("Tell me about some course", client, monkeypatch=monkeypatch,
                             resolver=not_found_resolver)
        assert result[0].intent == "clarification_needed"
        warn_records = [r for r in caplog.records if "QU.resolve_failed" in r.message]
        assert warn_records, "QU.resolve_failed warning not emitted"
        # entity mention must not appear in log
        assert "SomeCourseName" not in caplog.text

    def test_resolve_summary_log_emitted(self, monkeypatch, caplog):
        """QU.resolve summary must be emitted after entity resolution."""
        response = _sq_json("get_course_info",
                            entities={"course_code": "C-CS301", "role": None, "track": None, "skill": None})
        client = _make_mock_client([response])

        def ok_resolver(et, txt):
            return {"status": "ok", "resolved_id": txt}

        with caplog.at_level(logging.INFO, logger="gateway.query_understanding"):
            _run_qu("Tell me about C-CS301", client, monkeypatch=monkeypatch, resolver=ok_resolver)
        resolve_records = [r for r in caplog.records if "QU.resolve" in r.message]
        assert resolve_records, "QU.resolve log not emitted"

    def test_no_raw_query_in_any_log(self, monkeypatch, caplog):
        """Raw user query text must never appear in any QU log line."""
        response = _sq_json("get_course_info")
        client = _make_mock_client([response])
        unique_marker = "UNIQUEPIIMARKER_NOSHOULDAPPEAR_999"
        with caplog.at_level(logging.DEBUG, logger="gateway.query_understanding"):
            _run_qu(unique_marker, client, monkeypatch=monkeypatch)
        assert unique_marker not in caplog.text


def test_sw_track_alias_exists_in_entity_aliases():
    """'sw' must be a valid alias for the SWE track in entity_aliases.json."""
    import json
    import os
    alias_path = os.path.join(
        os.path.dirname(__file__), "..", "engines", "kg", "data", "entity_aliases.json"
    )
    with open(alias_path, encoding="utf-8") as f:
        data = json.load(f)
    assert "sw" in data["track"]["aliases"]["SWE"], (
        "'sw' alias missing from SWE track in entity_aliases.json"
    )


# ── D6 Record Focus / Response Style Tests ────────────────────────────────────

class TestD6FocusDetection:
    """Tests for deterministic D6 record_focus and response_style detection.
    All tests use the deterministic fallback path (LLM not configured).
    """

    def _fallback(self, text: str) -> list:
        from gateway.qu_preprocessing import preprocess
        from gateway.query_understanding import _deterministic_fallback
        pre = preprocess(text)
        return _deterministic_fallback(text, pre)

    def test_level_query_routes_to_student_record_not_clarification(self):
        sqs = self._fallback("what is my level?")
        assert len(sqs) == 1
        assert sqs[0].intent == "get_student_record"
        assert sqs[0].params.get("record_focus") == "academic_level"

    def test_my_level_query_routes_to_academic_level_focus(self):
        sqs = self._fallback("what level am I?")
        assert sqs[0].intent == "get_student_record"
        assert sqs[0].params.get("record_focus") == "academic_level"

    def test_cgpa_only_query_sets_only_style(self):
        sqs = self._fallback("only cgpa pls")
        assert sqs[0].intent == "get_student_record"
        assert sqs[0].params.get("record_focus") == "cgpa"
        assert sqs[0].params.get("response_style") == "only"

    def test_my_cgpa_query_sets_cgpa_focus(self):
        sqs = self._fallback("what is my cgpa?")
        assert sqs[0].intent == "get_student_record"
        assert sqs[0].params.get("record_focus") == "cgpa"

    def test_in_progress_courses_query(self):
        sqs = self._fallback("what courses am I taking now?")
        assert sqs[0].intent == "get_student_record"
        assert sqs[0].params.get("record_focus") == "in_progress_courses"

    def test_completed_courses_query(self):
        sqs = self._fallback("what courses have I completed so far?")
        assert sqs[0].intent == "get_student_record"
        assert sqs[0].params.get("record_focus") == "completed_courses"

    def test_standing_query(self):
        sqs = self._fallback("am I in good academic standing?")
        assert sqs[0].intent == "get_student_record"
        assert sqs[0].params.get("record_focus") == "academic_standing"

    def test_cooked_query_routes_to_standing(self):
        sqs = self._fallback("am I cooked academically?")
        assert sqs[0].intent == "get_student_record"
        assert sqs[0].params.get("record_focus") == "academic_standing"

    def test_assumption_only_routes_to_acknowledgement_not_plan(self):
        sqs = self._fallback("Assume I failed Programming Fundamentals.")
        assert sqs[0].intent == "get_student_record"
        assert sqs[0].params.get("record_focus") == "assumption_acknowledgement"

    def test_assumption_with_followup_does_not_set_acknowledgement(self):
        """'Assume I failed X, what should I take next?' → NOT assumption_acknowledgement."""
        sqs = self._fallback("Assume I failed Programming Fundamentals, what should I take next?")
        # Should NOT produce assumption_acknowledgement; may produce plan_semester or other
        if sqs[0].params.get("record_focus") is not None:
            assert sqs[0].params.get("record_focus") != "assumption_acknowledgement"

    def test_yes_no_style_detected(self):
        sqs = self._fallback("just answer yes or no: am I taking Advanced Physics now?")
        assert sqs[0].intent == "get_student_record"
        assert sqs[0].params.get("response_style") == "yes_no"

    def test_reset_signal_sets_reset_focus(self):
        sqs = self._fallback("reset assumptions")
        assert sqs[0].intent == "get_student_record"
        assert sqs[0].params.get("record_focus") == "reset_assumptions"

    def test_parse_raw_sq_normalizes_valid_record_focus(self):
        from gateway.query_understanding import _parse_raw_sq
        raw = {"intent": "get_student_record", "params": {"record_focus": "cgpa"}}
        sq = _parse_raw_sq(raw, "test")
        assert sq.params.get("record_focus") == "cgpa"

    def test_parse_raw_sq_drops_invalid_record_focus(self):
        from gateway.query_understanding import _parse_raw_sq
        raw = {"intent": "get_student_record", "params": {"record_focus": "invalid_focus"}}
        sq = _parse_raw_sq(raw, "test")
        assert "record_focus" not in sq.params

    def test_parse_raw_sq_normalizes_valid_response_style(self):
        from gateway.query_understanding import _parse_raw_sq
        raw = {"intent": "get_student_record", "params": {"response_style": "yes_no"}}
        sq = _parse_raw_sq(raw, "test")
        assert sq.params.get("response_style") == "yes_no"

    def test_parse_raw_sq_drops_invalid_response_style(self):
        from gateway.query_understanding import _parse_raw_sq
        raw = {"intent": "get_student_record", "params": {"response_style": "bullet_list"}}
        sq = _parse_raw_sq(raw, "test")
        assert "response_style" not in sq.params


# ── Phase 2 Behavioral Stabilization Tests ────────────────────────────────────


class TestPhase2EntityCandidatesNormalization:
    """QU normalization: entity_candidates from LLM params guide entity extraction."""

    def _normalize(self, intent: str, entities_dict: dict, params_dict: dict) -> StructuredQuery:
        from gateway.query_understanding import _normalize_one_sq
        sq = StructuredQuery(
            intent=intent,
            original_text="test",
            entities=EntitySet(**{k: entities_dict.get(k) for k in ("course_code", "role_id", "track_id", "skill_id")}),
            params=params_dict,
        )
        return _normalize_one_sq(sq, "test", PreprocessResult(), LastReferenced())

    def test_entity_candidates_first_used_as_course_when_entity_missing(self):
        """First entity_candidate becomes course_code when entity missing."""
        sq = self._normalize(
            "get_course_info",
            {"course_code": None, "role_id": None, "track_id": None, "skill_id": None},
            {"entity_candidates": ["probability and statistics", "intro to probability"]},
        )
        assert sq.entities.course_code == "probability and statistics"

    def test_entity_candidates_skipped_when_entity_already_set(self):
        """entity_candidates not applied when course entity already present."""
        sq = self._normalize(
            "get_course_info",
            {"course_code": "C-CS219", "role_id": None, "track_id": None, "skill_id": None},
            {"entity_candidates": ["something else"]},
        )
        assert sq.entities.course_code == "C-CS219"

    def test_skill_candidates_first_used_for_skill_search(self):
        """First skill_candidate becomes skill_id for search_courses_by_skill."""
        sq = self._normalize(
            "search_courses_by_skill",
            {"course_code": None, "role_id": None, "track_id": None, "skill_id": None},
            {"skill_candidates": ["machine learning", "deep learning"]},
        )
        assert sq.entities.skill_id == "machine learning"

    def test_entity_candidates_fallback_for_skill_when_no_skill_candidates(self):
        """entity_candidates used for skill when skill_candidates absent."""
        sq = self._normalize(
            "search_courses_by_skill",
            {"course_code": None, "role_id": None, "track_id": None, "skill_id": None},
            {"entity_candidates": ["object-oriented programming"]},
        )
        assert sq.entities.skill_id == "object-oriented programming"

    def test_get_skills_taught_uses_entity_candidates(self):
        """get_skills_taught: entity_candidates promotes course candidate."""
        sq = self._normalize(
            "get_skills_taught",
            {"course_code": None, "role_id": None, "track_id": None, "skill_id": None},
            {"entity_type_hint": "course", "entity_candidates": ["probability and statistics"]},
        )
        assert sq.entities.course_code == "probability and statistics"


class TestPhase2ResolverCandidateList:
    """QU resolver: tries entity_candidates in order when primary entity resolution fails."""

    def _make_resolver(self, course_map: dict[str, str] = None,
                       skill_map: dict[str, str] = None) -> any:
        """Type-aware resolver: course_map for courses, skill_map for skills."""
        course_map = {k.lower(): v for k, v in (course_map or {}).items()}
        skill_map = {k.lower(): v for k, v in (skill_map or {}).items()}

        def resolver(entity_type: str, mention: str) -> dict:
            key = mention.strip().lower()
            if entity_type == "course" and key in course_map:
                return {"status": "ok", "resolved_id": course_map[key]}
            if entity_type == "skill" and key in skill_map:
                return {"status": "ok", "resolved_id": skill_map[key]}
            return {"status": "not_found"}
        return resolver

    def _make_sq(self, intent: str, course_code=None, skill_id=None, params=None) -> StructuredQuery:
        return StructuredQuery(
            intent=intent,
            original_text="test",
            entities=EntitySet(course_code=course_code, skill_id=skill_id),
            params=params or {},
        )

    def test_candidate_resolves_when_primary_fails(self):
        """Resolver tries entity_candidates when primary entity fails."""
        from gateway.query_understanding import _resolve_sq
        resolver = self._make_resolver(
            course_map={"intro to machine learning": "C-AI301"}
        )
        sq = self._make_sq(
            "get_course_info",
            course_code="machine learning",  # won't resolve as course
            params={"entity_candidates": ["machine learning", "intro to machine learning"]},
        )
        result = _resolve_sq(sq, resolver)
        assert result.intent == "get_course_info"
        assert result.entities.course_code == "C-AI301"

    def test_clarification_when_all_candidates_fail(self):
        """clarification_needed when primary and all candidates fail and no skill fallback."""
        from gateway.query_understanding import _resolve_sq
        resolver = self._make_resolver()  # resolves nothing
        sq = self._make_sq(
            "get_course_info",
            course_code="gibberish course name",
            params={"entity_candidates": ["another gibberish", "yet another"]},
        )
        result = _resolve_sq(sq, resolver)
        assert result.intent == "clarification_needed"

    def test_topic_fallback_course_to_skill(self):
        """Course-intent fails as course but skill resolves → reroute to search_courses_by_skill."""
        from gateway.query_understanding import _resolve_sq
        # object-oriented programming resolves as skill but NOT as course
        resolver = self._make_resolver(skill_map={"object-oriented programming": "SK_OOP"})
        sq = self._make_sq(
            "get_course_info",
            course_code="object-oriented programming",
            params={"entity_candidates": ["object-oriented programming"]},
        )
        result = _resolve_sq(sq, resolver)
        assert result.intent == "search_courses_by_skill"
        assert result.entities.skill_id == "SK_OOP"
        assert result.params.get("topic_fallback") is True
        assert result.params.get("original_intent") == "get_course_info"

    def test_skill_candidates_tried_in_order(self):
        """skill_candidates list tried in priority order when primary fails."""
        from gateway.query_understanding import _resolve_sq
        resolver = self._make_resolver(skill_map={"machine learning": "SK_ML"})
        sq = self._make_sq(
            "search_courses_by_skill",
            skill_id="ml",  # won't resolve
            params={"skill_candidates": ["ml", "machine learning"]},
        )
        result = _resolve_sq(sq, resolver)
        assert result.intent == "search_courses_by_skill"
        assert result.entities.skill_id == "SK_ML"

    def test_multi_skill_resolved_ids_stored_in_params(self):
        """When skill_candidates resolve, resolved_skill_ids stored in params."""
        from gateway.query_understanding import _resolve_sq
        resolver = self._make_resolver(skill_map={
            "machine learning": "SK_ML",
            "deep learning": "SK_DL",
        })
        sq = self._make_sq(
            "search_courses_by_skill",
            skill_id="machine learning",
            params={"skill_candidates": ["machine learning", "deep learning"]},
        )
        result = _resolve_sq(sq, resolver)
        assert result.intent == "search_courses_by_skill"
        assert result.entities.skill_id == "SK_ML"
        resolved_ids = result.params.get("resolved_skill_ids", [])
        assert "SK_ML" in resolved_ids
        assert "SK_DL" in resolved_ids


# ── Prerequisite Depth Signal & Normalization Tests ───────────────────────────

class TestPrereqDepthNormalization:
    """
    Tests for detect_prereq_full_depth signal and post-LLM normalization rules.

    Positive: full/all/complete/entire/whole/chain/tree qualifiers → depth=full
    Negative: bare "prerequisites of X" → depth=direct; policy/general context → no override
    """

    # ── Signal function ──────────────────────────────────────────────────────

    def test_signal_full_prefix(self):
        from gateway.qu_preprocessing import detect_prereq_full_depth
        assert detect_prereq_full_depth("full prerequisites of C-CS219") is True

    def test_signal_all_prefix(self):
        from gateway.qu_preprocessing import detect_prereq_full_depth
        assert detect_prereq_full_depth("all prerequisites of C-CS219") is True

    def test_signal_complete_prefix(self):
        from gateway.qu_preprocessing import detect_prereq_full_depth
        assert detect_prereq_full_depth("complete prerequisites of C-CS219") is True

    def test_signal_entire_prefix(self):
        from gateway.qu_preprocessing import detect_prereq_full_depth
        assert detect_prereq_full_depth("entire prerequisite tree for C-CS219") is True

    def test_signal_complete_chain(self):
        from gateway.qu_preprocessing import detect_prereq_full_depth
        assert detect_prereq_full_depth("complete prerequisite chain of C-CS219") is True

    def test_signal_whole_prereq_chain(self):
        from gateway.qu_preprocessing import detect_prereq_full_depth
        assert detect_prereq_full_depth("show me the whole prereq chain for C-CS219") is True

    def test_signal_false_for_bare_prerequisites(self):
        from gateway.qu_preprocessing import detect_prereq_full_depth
        assert detect_prereq_full_depth("prerequisites of C-CS219") is False

    def test_signal_false_for_prereq_policy_sentence(self):
        from gateway.qu_preprocessing import detect_prereq_full_depth
        assert detect_prereq_full_depth("I heard prerequisites are confusing, what is the registration policy?") is False

    def test_signal_false_for_general_prereq_mention(self):
        from gateway.qu_preprocessing import detect_prereq_full_depth
        assert detect_prereq_full_depth("Can you explain what prerequisites mean?") is False

    # ── Post-LLM normalization: depth upgrade ────────────────────────────────

    def _normalize(self, intent: str, params: dict, user_text: str) -> StructuredQuery:
        from gateway.query_understanding import _normalize_one_sq
        sq = StructuredQuery(
            intent=intent,
            original_text=user_text,
            entities=EntitySet(course_code="C-CS219"),
            params=params,
        )
        return _normalize_one_sq(sq, user_text, PreprocessResult(), LastReferenced())

    def test_normalization_upgrades_direct_to_full_when_signal_fires(self):
        """LLM says depth=direct but 'full prerequisites' → normalization upgrades to full."""
        sq = self._normalize(
            "get_course_prerequisites",
            {"depth": "direct"},
            "What are the full prerequisites of C-CS219?",
        )
        assert sq.params.get("depth") == "full"

    def test_normalization_keeps_full_when_llm_correct(self):
        """LLM says depth=full for 'all prerequisites' → normalization keeps full."""
        sq = self._normalize(
            "get_course_prerequisites",
            {"depth": "full"},
            "all prerequisites of C-CS219",
        )
        assert sq.params.get("depth") == "full"

    def test_normalization_defaults_to_direct_when_no_signal(self):
        """LLM omits depth for bare 'prerequisites of X' → defaults to direct."""
        sq = self._normalize(
            "get_course_prerequisites",
            {},
            "prerequisites of C-CS219",
        )
        assert sq.params.get("depth") == "direct"

    def test_normalization_sets_full_from_missing_depth_with_signal(self):
        """LLM omits depth but 'complete prereq chain' → normalization sets full."""
        sq = self._normalize(
            "get_course_prerequisites",
            {},
            "show the complete prereq chain of C-CS219",
        )
        assert sq.params.get("depth") == "full"

    def test_normalization_does_not_downgrade_llm_full(self):
        """If LLM says full but signal is absent, trust LLM — do not downgrade to direct."""
        sq = self._normalize(
            "get_course_prerequisites",
            {"depth": "full"},
            "prerequisites of C-CS219",  # no full signal
        )
        assert sq.params.get("depth") == "full"

    def test_normalization_does_not_affect_other_intents(self):
        """Depth normalization only applies to get_course_prerequisites, not other intents."""
        from gateway.query_understanding import _normalize_one_sq
        sq = StructuredQuery(
            intent="get_course_info",
            original_text="full details of C-CS219",
            entities=EntitySet(course_code="C-CS219"),
            params={},
        )
        result = _normalize_one_sq(sq, "full details of C-CS219", PreprocessResult(), LastReferenced())
        assert result.intent == "get_course_info"
        assert "depth" not in result.params

    # ── LLM mock: depth patching end-to-end ──────────────────────────────────

    def test_llm_mock_direct_upgraded_to_full_on_full_text(self, monkeypatch):
        """End-to-end: LLM returns depth=direct for 'full prerequisites' → patched to full."""
        response = _sq_json(
            "get_course_prerequisites",
            original_text="What are the full prerequisites of C-CS219?",
            entities={"course_code": "C-CS219", "role": None, "track": None, "skill": None},
            params={"depth": "direct"},
        )
        client = _make_mock_client([response])
        result = _run_qu(
            "What are the full prerequisites of C-CS219?",
            client, monkeypatch=monkeypatch,
        )
        assert result[0].intent == "get_course_prerequisites"
        assert result[0].params.get("depth") == "full"

    def test_llm_mock_depth_direct_kept_for_bare_prereq(self, monkeypatch):
        """End-to-end: LLM returns depth=direct for bare 'prerequisites of X' → kept as direct."""
        response = _sq_json(
            "get_course_prerequisites",
            original_text="prerequisites of C-CS219",
            entities={"course_code": "C-CS219", "role": None, "track": None, "skill": None},
            params={"depth": "direct"},
        )
        client = _make_mock_client([response])
        result = _run_qu("prerequisites of C-CS219", client, monkeypatch=monkeypatch)
        assert result[0].params.get("depth") == "direct"

    # ── Negative / anti-rigid tests ──────────────────────────────────────────

    def test_policy_sentence_with_prereq_word_stays_policy(self, monkeypatch):
        """'I heard prerequisites are confusing, what is the registration policy?' → policy_query."""
        response = _sq_json(
            "policy_query",
            original_text="What is the course registration policy?",
        )
        client = _make_mock_client([response])
        result = _run_qu(
            "I heard prerequisites are confusing, what is the registration policy?",
            client, monkeypatch=monkeypatch,
        )
        assert result[0].intent == "policy_query"

    def test_policy_sentence_not_overridden_by_normalization(self, monkeypatch):
        """Normalization must NOT convert policy_query to get_course_prerequisites."""
        response = _sq_json(
            "policy_query",
            original_text="What is the policy for prerequisites?",
        )
        client = _make_mock_client([response])
        result = _run_qu(
            "I heard full prerequisites are confusing, what is the policy?",
            client, monkeypatch=monkeypatch,
        )
        assert result[0].intent == "policy_query"

    def test_before_track_choice_not_prereq_intent(self, monkeypatch):
        """'Before I choose a track, explain the policy' → NOT get_course_prerequisites."""
        response = _sq_json(
            "policy_query",
            original_text="What is the policy for choosing a track?",
        )
        client = _make_mock_client([response])
        result = _run_qu(
            "Before I choose a track, can you explain the policy?",
            client, monkeypatch=monkeypatch,
        )
        assert result[0].intent != "get_course_prerequisites"

    def test_deterministic_fallback_prereq_policy_sentence(self):
        """Deterministic fallback: 'registration policy' sentence containing 'prerequisite' → policy_query."""
        from gateway.qu_preprocessing import preprocess
        text = "I heard prerequisites are confusing, what is the registration policy?"
        pre = preprocess(text)
        result = _deterministic_fallback(text, pre)
        assert result[0].intent == "policy_query"


# ── Compare Tracks, Course-Info vs Skill-Search, CGPA+Style ──────────────────

class TestCriticalBehaviorGuards:
    """
    Guards for critical behaviors that must be preserved after prompt compression.
    All tests use mocked LLM so we test the normalization pipeline, not LLM output.
    """

    # ── Compare tracks produces ONE SQ ──────────────────────────────────────

    def test_compare_tracks_one_sq_with_secondary(self, monkeypatch):
        """'Compare AI and DSE tracks' → one SQ, compare_tracks, secondary_entities.track=DSE.

        Uses canonical IDs (AI, DSE) so _filter_unresolved keeps them without a resolver.
        """
        sq_data = {
            "intent": "compare_tracks",
            "original_text": "Compare AI and Data Science tracks",
            "entities": {"course_code": None, "role": None, "track": "AI", "skill": None},
            "secondary_entities": {"course_code": None, "role": None, "track": "DSE", "skill": None},
            "params": {},
            "session_overrides": {
                "added_courses": [], "assumed_passed_courses": [], "assumed_failed_courses": [],
                "target_role": None, "course_override_type": "none", "override_action": "accumulate",
            },
            "student_referential_fallback": False,
        }
        response = json.dumps({"queries": [sq_data]})
        client = _make_mock_client([response])
        result = _run_qu("Compare AI and Data Science tracks", client, monkeypatch=monkeypatch)

        assert len(result) == 1
        assert result[0].intent == "compare_tracks"
        assert result[0].entities.track_id == "AI"
        assert result[0].secondary_entities is not None
        assert result[0].secondary_entities.track_id == "DSE"

    # ── Course info vs skill search distinction ──────────────────────────────

    def test_tell_me_about_database_systems_is_course_info(self, monkeypatch):
        """'Tell me about Database Systems' → get_course_info (not search_courses_by_skill)."""
        response = _sq_json(
            "get_course_info",
            original_text="Tell me about Database Systems",
            entities={"course_code": "Database Systems", "role": None, "track": None, "skill": None},
        )
        client = _make_mock_client([response])
        result = _run_qu("Tell me about Database Systems", client, monkeypatch=monkeypatch)
        assert result[0].intent == "get_course_info"

    def test_courses_for_learning_databases_is_skill_search(self, monkeypatch):
        """'I want courses for learning databases' → search_courses_by_skill (not get_course_info)."""
        response = _sq_json(
            "search_courses_by_skill",
            original_text="I want courses for learning databases",
            entities={"course_code": None, "role": None, "track": None, "skill": "databases"},
        )
        client = _make_mock_client([response])
        result = _run_qu("I want courses for learning databases", client, monkeypatch=monkeypatch)
        assert result[0].intent == "search_courses_by_skill"

    def test_which_courses_teach_machine_learning_is_skill_search(self, monkeypatch):
        """'Which courses teach machine learning?' → search_courses_by_skill.

        skill_id is nulled by _filter_unresolved without a resolver (non-SK_ prefix);
        the important guard here is intent correctness, not entity resolution.
        """
        response = _sq_json(
            "search_courses_by_skill",
            original_text="Which courses teach machine learning?",
            entities={"course_code": None, "role": None, "track": None, "skill": "machine learning"},
        )
        client = _make_mock_client([response])
        result = _run_qu("Which courses teach machine learning?", client, monkeypatch=monkeypatch)
        assert result[0].intent == "search_courses_by_skill"

    # ── Record focus: cgpa + response_style=only ─────────────────────────────

    def test_only_my_cgpa_sets_focus_and_style(self, monkeypatch):
        """'only my CGPA' → record_focus=cgpa, response_style=only."""
        response = _sq_json(
            "get_student_record",
            original_text="Only tell me my CGPA.",
            params={"record_focus": "cgpa", "response_style": "only"},
            student_referential_fallback=True,
        )
        client = _make_mock_client([response])
        result = _run_qu("only my CGPA", client, monkeypatch=monkeypatch)
        assert result[0].intent == "get_student_record"
        assert result[0].params.get("record_focus") == "cgpa"
        assert result[0].params.get("response_style") == "only"

    # ── Reset assumptions clears overrides ───────────────────────────────────

    def test_reset_assumptions_llm_mock_clears(self, monkeypatch):
        """'reset assumptions' → get_student_record with override_action=clear."""
        response = _sq_json(
            "get_student_record",
            original_text="Reset what-if assumptions",
            session_overrides={
                "added_courses": [], "assumed_passed_courses": [], "assumed_failed_courses": [],
                "target_role": None, "course_override_type": "none", "override_action": "clear",
            },
            student_referential_fallback=True,
        )
        client = _make_mock_client([response])
        result = _run_qu("reset assumptions", client, monkeypatch=monkeypatch)
        assert result[0].intent == "get_student_record"
        assert result[0].session_overrides.override_action == "clear"

    # ── Prompt content boundary tests ────────────────────────────────────────

    def test_prompt_contains_prereq_depth_guidance(self):
        """Compressed prompt must still contain prerequisite depth routing rule."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "depth" in prompt
        assert "full" in prompt
        assert "direct" in prompt

    def test_prompt_contains_forbidden_intents_block(self):
        """Compressed prompt must still warn against forbidden intents."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "FORBIDDEN INTENTS" in prompt
        assert "get_prerequisites" in prompt
        assert "handbook_query" in prompt

    def test_prompt_contains_compare_tracks_rule(self):
        """Compressed prompt must contain compare_tracks routing."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "compare_tracks" in prompt
        assert "secondary_entities" in prompt

    def test_prompt_size_reduced_from_baseline(self):
        """Compressed prompt must be substantially smaller than the 41,484 char baseline."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        chars = len(prompt)
        estimated_tokens = (chars + 3) // 4
        # Must be less than 75% of baseline (41,484 chars = 10,371 tokens)
        assert chars < 31_000, f"Prompt too large: {chars} chars (baseline was 41,484)"
        assert estimated_tokens < 7_800, f"Estimated tokens too high: {estimated_tokens} (baseline was 10,371)"


# ── Course-Info Domain 2 Live Bug Regression Tests ────────────────────────────

class TestCourseInfoD2LiveBugs:
    """
    Regression tests for live failures found in D2 / get_course_info integration.
    Tests A-F as specified in the bug report.
    All use deterministic normalization or mock LLM — no live LLM or KG required.
    """

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _normalize_sq(self, intent: str, entities_dict: dict, params_dict: dict,
                      original_text: str = "test", user_text: str = "test") -> StructuredQuery:
        """Run _normalize_one_sq for a single SQ without touching LLM or resolver."""
        from gateway.query_understanding import _normalize_one_sq
        sq = StructuredQuery(
            intent=intent,
            original_text=original_text,
            entities=EntitySet(**{k: entities_dict.get(k) for k in ("course_code", "role_id", "track_id", "skill_id")}),
            params=params_dict,
        )
        return _normalize_one_sq(sq, user_text, PreprocessResult(), LastReferenced())

    def _resolve_sq(self, intent: str, entities_dict: dict, params_dict: dict,
                    resolver, original_text: str = "test") -> StructuredQuery:
        """Run _resolve_sq directly for targeted resolution testing."""
        from gateway.query_understanding import _resolve_sq
        sq = StructuredQuery(
            intent=intent,
            original_text=original_text,
            entities=EntitySet(**{k: entities_dict.get(k) for k in ("course_code", "role_id", "track_id", "skill_id")}),
            params=params_dict,
        )
        return _resolve_sq(sq, resolver)

    # ── A: Track-membership question → get_course_info, not get_track_overview ─

    def test_A1_track_membership_data_analysis_rerouted(self):
        """'to which track the data analysis course belongs to?' → get_course_info."""
        from gateway.qu_preprocessing import detect_course_track_membership, extract_course_from_track_membership
        text = "to which track the data analysis course belongs to?"
        assert detect_course_track_membership(text) is True
        mention = extract_course_from_track_membership(text)
        assert mention is not None
        assert "data analysis" in mention.lower()

    def test_A2_track_membership_reroutes_from_get_track_overview(self):
        """Normalization: get_track_overview on track-membership query → get_course_info."""
        text = "to which track the data analysis course belongs to?"
        sq = self._normalize_sq(
            "get_track_overview",
            {"course_code": None, "role_id": None, "track_id": "DSE", "skill_id": None},
            {},
            original_text=text,
            user_text=text,
        )
        assert sq.intent == "get_course_info"
        assert sq.entities.course_code is not None
        assert "data analysis" in sq.entities.course_code.lower()
        assert sq.entities.track_id is None

    def test_A3_track_membership_which_track_does_X_belong(self):
        """'Which track does Natural Language Processing belong to?' → get_course_info."""
        from gateway.qu_preprocessing import detect_course_track_membership, extract_course_from_track_membership
        text = "Which track does Natural Language Processing belong to?"
        assert detect_course_track_membership(text) is True
        mention = extract_course_from_track_membership(text)
        assert mention is not None
        assert "natural language processing" in mention.lower()

    def test_A4_track_membership_reroutes_from_recommend_track_for_skill(self):
        """Normalization: recommend_track_for_skill on track-membership → get_course_info."""
        text = "Which track does Natural Language Processing belong to?"
        sq = self._normalize_sq(
            "recommend_track_for_skill",
            {"course_code": None, "role_id": None, "track_id": None, "skill_id": "SK_NLP"},
            {},
            original_text=text,
            user_text=text,
        )
        assert sq.intent == "get_course_info"
        assert sq.entities.course_code is not None
        assert "natural language processing" in sq.entities.course_code.lower()
        assert sq.entities.skill_id is None

    def test_A5_real_track_overview_not_affected(self):
        """'What courses are in the AI track?' stays get_track_overview (not membership)."""
        text = "What courses are in the AI track?"
        sq = self._normalize_sq(
            "get_track_overview",
            {"course_code": None, "role_id": None, "track_id": "AI", "skill_id": None},
            {},
            original_text=text,
            user_text=text,
        )
        assert sq.intent == "get_track_overview"
        assert sq.entities.track_id == "AI"

    # ── B: Explicit entity beats last_referenced / stale canonical code ────────

    def test_B1_stale_code_overridden_by_raw_entity_mention(self):
        """If LLM outputs stale C-CS435 but raw_entity_mention='data analysis', override."""
        text = "well, which level is the data analysis course available in?"
        sq = self._normalize_sq(
            "get_course_info",
            {"course_code": "C-CS435", "role_id": None, "track_id": None, "skill_id": None},
            {"raw_entity_mention": "data analysis"},
            original_text=text,
            user_text=text,
        )
        assert sq.intent == "get_course_info"
        # Code was overridden: should NOT be C-CS435 any more
        assert sq.entities.course_code != "C-CS435"
        assert sq.entities.course_code is not None
        assert "data analysis" in sq.entities.course_code.lower()

    def test_B2_explicit_code_in_user_text_is_kept(self):
        """If user explicitly types C-CS301, it must not be replaced."""
        text = "Tell me about C-CS301"
        sq = self._normalize_sq(
            "get_course_info",
            {"course_code": "C-CS301", "role_id": None, "track_id": None, "skill_id": None},
            {"raw_entity_mention": "C-CS301"},
            original_text=text,
            user_text=text,
        )
        assert sq.entities.course_code == "C-CS301"

    def test_B3_pronoun_raw_mention_does_not_override_resolved_code(self):
        """Pronoun 'it' in raw_entity_mention must NOT override a resolved canonical code."""
        text = "tell me more about it"
        sq = self._normalize_sq(
            "get_course_info",
            {"course_code": "C-CS316", "role_id": None, "track_id": None, "skill_id": None},
            {"raw_entity_mention": "it"},
            original_text=text,
            user_text=text,
        )
        # 'it' is a pronoun; C-CS316 should not be replaced
        assert sq.entities.course_code == "C-CS316"

    # ── C: NLP should resolve as course, not skill, for track-membership wording ─

    def test_C1_nlp_track_membership_gives_course_info(self):
        """'Which track does NLP belong to?' → get_course_info, not recommend_track_for_skill."""
        from gateway.qu_preprocessing import detect_course_track_membership
        assert detect_course_track_membership("Which track does NLP belong to?") is True

    def test_C2_nlp_what_track_is_in_pattern(self):
        """'What track is Natural Language Processing available in?' detected as membership."""
        from gateway.qu_preprocessing import detect_course_track_membership, extract_course_from_track_membership
        text = "What track is Natural Language Processing available in?"
        assert detect_course_track_membership(text) is True
        mention = extract_course_from_track_membership(text)
        assert mention is not None
        assert "natural language processing" in mention.lower()

    # ── D: Hallucinated course code validated through resolver ─────────────────

    def test_D1_hallucinated_code_not_in_user_text_goes_to_resolver(self):
        """C-SO305 not in user text → sent to resolver → not_found → clarification."""
        user_text = "Give me a quick overview of Development of Secure Software Systems"
        calls = []

        def not_found_resolver(et, txt):
            calls.append((et, txt))
            return {"status": "not_found"}

        result = self._resolve_sq(
            "get_course_info",
            {"course_code": "C-SO305", "role_id": None, "track_id": None, "skill_id": None},
            {},
            not_found_resolver,
            original_text=user_text,
        )
        # C-SO305 is not in user_text → resolver was called → not_found → clarification
        assert result.intent == "clarification_needed"
        course_calls = [c for c in calls if c[0] == "course"]
        assert len(course_calls) >= 1

    def test_D2_explicit_user_code_bypasses_resolver(self):
        """C-CS301 typed explicitly by user → resolver NOT called."""
        user_text = "Tell me about C-CS301"
        calls = []

        def tracking_resolver(et, txt):
            calls.append((et, txt))
            return {"status": "ok", "resolved_id": txt}

        result = self._resolve_sq(
            "get_course_info",
            {"course_code": "C-CS301", "role_id": None, "track_id": None, "skill_id": None},
            {},
            tracking_resolver,
            original_text=user_text,
        )
        assert result.intent == "get_course_info"
        assert result.entities.course_code == "C-CS301"
        course_calls = [c for c in calls if c[0] == "course"]
        assert course_calls == []

    def test_D3_field_training_resolves_correctly(self):
        """'Is Field Training a real course in the catalogue?' resolves to C-TR304."""
        user_text = "Is Field Training a real course in the catalogue?"
        calls = []

        def alias_resolver(et, txt):
            calls.append((et, txt))
            # Simulate alias resolution: "field training" → C-TR304
            if txt.lower().strip() in ("field training",):
                return {"status": "ok", "resolved_id": "C-TR304"}
            return {"status": "not_found"}

        result = self._resolve_sq(
            "get_course_info",
            {"course_code": "Field Training", "role_id": None, "track_id": None, "skill_id": None},
            {},
            alias_resolver,
            original_text=user_text,
        )
        assert result.intent == "get_course_info"
        assert result.entities.course_code == "C-TR304"

    # ── E: Explicit entity in current turn beats TurnMemory last entity ────────

    def test_E1_graduation_project_b_not_overridden_by_raw_mention(self):
        """'What can you tell me about Graduation Project B?' → override stale code."""
        text = "What can you tell me about Graduation Project B?"
        # Simulate LLM using stale C-TR304 but providing correct raw_entity_mention
        sq = self._normalize_sq(
            "get_course_info",
            {"course_code": "C-TR304", "role_id": None, "track_id": None, "skill_id": None},
            {"raw_entity_mention": "Graduation Project B"},
            original_text=text,
            user_text=text,
        )
        assert sq.entities.course_code != "C-TR304"
        assert sq.entities.course_code is not None
        assert "graduation project b" in sq.entities.course_code.lower()

    def test_E2_graduation_project_b_alias_in_entity_aliases(self):
        """'graduation project b' must be an alias for C-GP411B in entity_aliases.json."""
        import json
        import os
        alias_path = os.path.join(
            os.path.dirname(__file__), "..", "engines", "kg", "data", "entity_aliases.json"
        )
        with open(alias_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "graduation project b" in data["course"]["aliases"]["C-GP411B"]

    def test_E3_field_training_alias_in_entity_aliases(self):
        """'field training' must be an alias for C-TR304 in entity_aliases.json."""
        import json
        import os
        alias_path = os.path.join(
            os.path.dirname(__file__), "..", "engines", "kg", "data", "entity_aliases.json"
        )
        with open(alias_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "field training" in data["course"]["aliases"]["C-TR304"]

    # ── Alias existence for D: Development of Secure Software Systems ──────────

    def test_D4_secure_software_alias_in_entity_aliases(self):
        """'development of secure software systems' must be alias for C-SW423."""
        import json
        import os
        alias_path = os.path.join(
            os.path.dirname(__file__), "..", "engines", "kg", "data", "entity_aliases.json"
        )
        with open(alias_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "development of secure software systems" in data["course"]["aliases"]["C-SW423"]

    # ── F: Ordinal reference behavior from Domain 1 preserved ────────────────

    def test_F1_detect_course_track_membership_not_triggered_by_ordinal(self):
        """'can I take the first one again?' must NOT be detected as track membership."""
        from gateway.qu_preprocessing import detect_course_track_membership
        assert detect_course_track_membership("can I take the first one again?") is False

    def test_F2_detect_course_track_membership_not_triggered_by_courses_in_track(self):
        """'What courses are in the AI track?' must NOT be detected as track membership."""
        from gateway.qu_preprocessing import detect_course_track_membership
        assert detect_course_track_membership("What courses are in the AI track?") is False

    def test_F3_detect_course_track_membership_not_triggered_by_skill_best_track(self):
        """'Which track is best for learning ML?' is NOT a course-membership question."""
        from gateway.qu_preprocessing import detect_course_track_membership
        assert detect_course_track_membership("Which track is best for learning ML?") is False

    def test_F4_normalization_does_not_alter_get_course_info_with_explicit_code(self):
        """Normalization must not alter a valid get_course_info SQ with explicit code in text."""
        text = "Tell me about C-AI424"
        sq = self._normalize_sq(
            "get_course_info",
            {"course_code": "C-AI424", "role_id": None, "track_id": None, "skill_id": None},
            {},
            original_text=text,
            user_text=text,
        )
        assert sq.intent == "get_course_info"
        assert sq.entities.course_code == "C-AI424"

    def test_F5_stale_code_guard_only_fires_for_d2_course_intents(self):
        """Stale code guard must not affect plan_semester or other non-D2 intents."""
        text = "plan my semester"
        sq = self._normalize_sq(
            "plan_semester",
            {"course_code": "C-CS435", "role_id": None, "track_id": None, "skill_id": None},
            {"raw_entity_mention": "some other course"},
            original_text=text,
            user_text=text,
        )
        # plan_semester is not in _D2_COURSE_INTENTS → no override
        assert sq.intent == "plan_semester"
        assert sq.entities.course_code == "C-CS435"

    # ── Prompt contains new disambiguation rules ───────────────────────────────

    def test_prompt_contains_track_membership_disambiguation(self):
        """Prompt must contain track-membership disambiguation rule."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "TRACK MEMBERSHIP DISAMBIGUATION" in prompt

    def test_prompt_contains_entity_extraction_priority_rule(self):
        """Prompt must contain entity extraction priority rule."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "ENTITY EXTRACTION PRIORITY" in prompt

    def test_prompt_contains_llm_course_code_safety(self):
        """Prompt must contain LLM course code safety rule."""
        from gateway.qu_prompt import build_system_prompt
        prompt = build_system_prompt()
        assert "LLM COURSE CODE SAFETY" in prompt


# ── ML Ambiguity, Clarification Quality, and Topic-Fallback Refinement ──────────

class TestMLAmbiguityAndClarification:
    """
    Regression tests for live Course Info failures involving ML ambiguity,
    topic-fallback over-firing, and clarification message quality.
    Tests A–F as requested.
    """

    # ── shared helpers ─────────────────────────────────────────────────────────

    def _resolve_sq(self, intent: str, entities_dict: dict, params_dict: dict,
                    resolver, original_text: str = "test") -> StructuredQuery:
        from gateway.query_understanding import _resolve_sq as _rq
        sq = StructuredQuery(
            intent=intent,
            original_text=original_text,
            entities=EntitySet(**{k: entities_dict.get(k) for k in
                                  ("course_code", "role_id", "track_id", "skill_id")}),
            params=params_dict,
        )
        return _rq(sq, resolver)

    @staticmethod
    def _ml_ambiguous_resolver(et: str, txt: str) -> dict:
        """Mock: 'ML' as course → ambiguous (C-AI321 / C-AI422); 'ML' as skill → SK_ML."""
        norm = txt.strip().lower()
        if et == "course" and norm in ("ml", "ml course", "machine learning"):
            return {
                "status": "ambiguous",
                "matches": [
                    {"id": "C-AI321", "name": "Introduction to Machine Learning",
                     "match_type": "explicit_ambiguous", "score": 0.85},
                    {"id": "C-AI422", "name": "Machine Learning Fundamentals",
                     "match_type": "explicit_ambiguous", "score": 0.85},
                ],
            }
        if et == "skill" and norm in ("ml", "machine learning"):
            return {"status": "ok", "resolved_id": "SK_ML"}
        return {"status": "not_found"}

    # ── A: "Tell me about ML" → clarification, NOT skill search ───────────────

    def test_A_tell_me_about_ml_produces_clarification(self):
        """'Tell me about ML' → clarification_needed, not search_courses_by_skill."""
        result = self._resolve_sq(
            "get_course_info",
            {"course_code": "ML", "role_id": None, "track_id": None, "skill_id": None},
            {"raw_entity_mention": "ML"},
            self._ml_ambiguous_resolver,
            original_text="Tell me about ML",
        )
        assert result.intent == "clarification_needed", (
            f"Expected clarification_needed, got {result.intent}"
        )

    def test_A_tell_me_about_ml_clarification_includes_course_options(self):
        """Clarification prompt for 'Tell me about ML' includes both ML course candidates."""
        result = self._resolve_sq(
            "get_course_info",
            {"course_code": "ML", "role_id": None, "track_id": None, "skill_id": None},
            {"raw_entity_mention": "ML"},
            self._ml_ambiguous_resolver,
            original_text="Tell me about ML",
        )
        prompt = result.params.get("clarification_prompt", "")
        # Both course candidates must appear in the prompt (by name or code)
        assert "C-AI321" in prompt or "Introduction to Machine Learning" in prompt
        assert "C-AI422" in prompt or "Machine Learning Fundamentals" in prompt

    def test_A_tell_me_about_ml_not_rerouted_to_skill_search(self):
        """topic_fallback must NOT fire for 'Tell me about ML' even though SK_ML resolves."""
        result = self._resolve_sq(
            "get_course_info",
            {"course_code": "ML", "role_id": None, "track_id": None, "skill_id": None},
            {},
            self._ml_ambiguous_resolver,
            original_text="Tell me about ML",
        )
        assert result.intent != "search_courses_by_skill"
        assert not result.params.get("topic_fallback")

    # ── B: "talk to me about the ML course" → clarification ───────────────────

    def test_B_talk_about_ml_course_produces_clarification(self):
        """'talk to me about the ML course' → clarification_needed, not direct C-AI321."""
        result = self._resolve_sq(
            "get_course_info",
            {"course_code": "ML", "role_id": None, "track_id": None, "skill_id": None},
            {"raw_entity_mention": "the ML course"},
            self._ml_ambiguous_resolver,
            original_text="talk to me about the ML course",
        )
        assert result.intent == "clarification_needed"

    def test_B_clarification_shows_both_ml_courses_with_names(self):
        """Clarification for ML course query shows C-AI321 and C-AI422 with names."""
        result = self._resolve_sq(
            "get_course_info",
            {"course_code": "ML", "role_id": None, "track_id": None, "skill_id": None},
            {"raw_entity_mention": "the ML course"},
            self._ml_ambiguous_resolver,
            original_text="talk to me about the ML course",
        )
        prompt = result.params.get("clarification_prompt", "")
        assert "Introduction to Machine Learning" in prompt or "C-AI321" in prompt
        assert "Machine Learning Fundamentals" in prompt or "C-AI422" in prompt
        # Friendly format: names should accompany codes in parentheses
        assert "(" in prompt

    # ── C: "Which courses teach ML?" → skill search preserved ─────────────────

    def test_C_which_courses_teach_ml_stays_skill_search(self, monkeypatch):
        """'Which courses teach ML?' → search_courses_by_skill (topic_fallback not involved)."""
        response = _sq_json(
            "search_courses_by_skill",
            original_text="Which courses teach ML?",
            entities={"course_code": None, "role": None, "track": None, "skill": "machine learning"},
        )
        client = _make_mock_client([response])

        def resolver(et: str, txt: str) -> dict:
            if et == "skill" and txt.strip().lower() in ("machine learning", "ml"):
                return {"status": "ok", "resolved_id": "SK_ML"}
            return {"status": "not_found"}

        result = _run_qu("Which courses teach ML?", client, monkeypatch=monkeypatch, resolver=resolver)
        assert result[0].intent == "search_courses_by_skill"
        assert result[0].entities.skill_id == "SK_ML"

    def test_C_courses_cover_ml_stays_skill_search(self, monkeypatch):
        """'Which courses cover ML?' → search_courses_by_skill."""
        response = _sq_json(
            "search_courses_by_skill",
            original_text="Which courses cover ML?",
            entities={"course_code": None, "role": None, "track": None, "skill": "ml"},
        )
        client = _make_mock_client([response])

        def resolver(et: str, txt: str) -> dict:
            if et == "skill" and txt.strip().lower() in ("ml", "machine learning"):
                return {"status": "ok", "resolved_id": "SK_ML"}
            return {"status": "not_found"}

        result = _run_qu("Which courses cover ML?", client, monkeypatch=monkeypatch, resolver=resolver)
        assert result[0].intent == "search_courses_by_skill"

    # ── D: Unambiguous full name resolves directly ─────────────────────────────

    def test_D_tell_me_about_intro_to_machine_learning_resolves_c_ai321(self, monkeypatch):
        """'Tell me about Introduction to Machine Learning' → get_course_info C-AI321."""
        response = _sq_json(
            "get_course_info",
            original_text="Tell me about Introduction to Machine Learning",
            entities={"course_code": "Introduction to Machine Learning",
                      "role": None, "track": None, "skill": None},
            params={"raw_entity_mention": "Introduction to Machine Learning"},
        )
        client = _make_mock_client([response])

        def resolver(et: str, txt: str) -> dict:
            if et == "course" and "introduction to machine learning" in txt.lower():
                return {"status": "ok", "resolved_id": "C-AI321"}
            return {"status": "not_found"}

        result = _run_qu(
            "Tell me about Introduction to Machine Learning",
            client, monkeypatch=monkeypatch, resolver=resolver,
        )
        assert result[0].intent == "get_course_info"
        assert result[0].entities.course_code == "C-AI321"

    def test_E_tell_me_about_ml_fundamentals_resolves_c_ai422(self, monkeypatch):
        """'Tell me about Machine Learning Fundamentals' → get_course_info C-AI422."""
        response = _sq_json(
            "get_course_info",
            original_text="Tell me about Machine Learning Fundamentals",
            entities={"course_code": "Machine Learning Fundamentals",
                      "role": None, "track": None, "skill": None},
            params={"raw_entity_mention": "Machine Learning Fundamentals"},
        )
        client = _make_mock_client([response])

        def resolver(et: str, txt: str) -> dict:
            if et == "course" and "machine learning fundamentals" in txt.lower():
                return {"status": "ok", "resolved_id": "C-AI422"}
            return {"status": "not_found"}

        result = _run_qu(
            "Tell me about Machine Learning Fundamentals",
            client, monkeypatch=monkeypatch, resolver=resolver,
        )
        assert result[0].intent == "get_course_info"
        assert result[0].entities.course_code == "C-AI422"

    # ── F: Ambiguous clarification uses friendly "Name (CODE)" labels ──────────

    def test_F_selected_topics_clarification_with_friendly_labels(self):
        """'Tell me about Selected Topics' → clarification with Name (CODE) format."""
        def resolver(et: str, txt: str) -> dict:
            if et == "course" and "selected topics" in txt.lower():
                return {
                    "status": "ambiguous",
                    "matches": [
                        {"id": "C-CS351", "name": "Selected Topics in Basic Computer Science-1",
                         "match_type": "partial_name", "score": 0.7},
                        {"id": "C-CS352", "name": "Selected Topics in Basic Computer Science-2",
                         "match_type": "partial_name", "score": 0.7},
                        {"id": "C-CS495", "name": "Selected Topics in Computer Science-1",
                         "match_type": "partial_name", "score": 0.7},
                        {"id": "C-CS496", "name": "Selected Topics in Computer Science-2",
                         "match_type": "partial_name", "score": 0.7},
                    ],
                }
            return {"status": "not_found"}

        result = self._resolve_sq(
            "get_course_info",
            {"course_code": "Selected Topics", "role_id": None, "track_id": None, "skill_id": None},
            {},
            resolver,
            original_text="Tell me about Selected Topics",
        )
        assert result.intent == "clarification_needed"
        prompt = result.params.get("clarification_prompt", "")
        # Course names must be present (not just raw codes)
        assert "Selected Topics in Basic Computer Science-1" in prompt
        assert "C-CS351" in prompt
        # Parenthesis format: "Name (CODE)"
        assert "(" in prompt

    def test_F_selected_topics_clarification_raw_code_only_format_absent(self):
        """Clarification must not be 'Options: C-CS351, C-CS352, ...' (code-only)."""
        def resolver(et: str, txt: str) -> dict:
            if et == "course" and "selected topics" in txt.lower():
                return {
                    "status": "ambiguous",
                    "matches": [
                        {"id": "C-CS351", "name": "Selected Topics in Basic Computer Science-1",
                         "match_type": "partial_name", "score": 0.7},
                        {"id": "C-CS352", "name": "Selected Topics in Basic Computer Science-2",
                         "match_type": "partial_name", "score": 0.7},
                    ],
                }
            return {"status": "not_found"}

        result = self._resolve_sq(
            "get_course_info",
            {"course_code": "Selected Topics", "role_id": None, "track_id": None, "skill_id": None},
            {},
            resolver,
            original_text="Tell me about Selected Topics",
        )
        prompt = result.params.get("clarification_prompt", "")
        # The old format was e.g. "Options: C-CS351, C-CS352" — this must not be the full picture
        # Names must appear alongside codes
        assert "Selected Topics in Basic Computer Science" in prompt

    # ── G: existing Domain 1 behavior preserved (regression guard) ────────────

    def test_G_topic_fallback_still_fires_for_ambiguous_original_text(self):
        """topic_fallback must still fire when original_text has no course-info verb."""
        # Uses generic text 'test' — no "tell me about" → fallback should fire
        def resolver(et: str, txt: str) -> dict:
            if et == "skill" and "oop" in txt.lower():
                return {"status": "ok", "resolved_id": "SK_OOP"}
            return {"status": "not_found"}

        result = self._resolve_sq(
            "get_course_info",
            {"course_code": "oop", "role_id": None, "track_id": None, "skill_id": None},
            {},
            resolver,
            original_text="test",  # no course-info verb → fallback fires
        )
        assert result.intent == "search_courses_by_skill"
        assert result.entities.skill_id == "SK_OOP"
        assert result.params.get("topic_fallback") is True

    def test_G_ambiguous_entity_aliases_json_has_ml(self):
        """entity_aliases.json must have 'ml' in course ambiguous_terms → C-AI321 and C-AI422."""
        import json
        import os
        alias_path = os.path.join(
            os.path.dirname(__file__), "..", "engines", "kg", "data", "entity_aliases.json"
        )
        with open(alias_path, encoding="utf-8") as f:
            data = json.load(f)
        ambiguous = data["course"]["ambiguous_terms"]
        assert "ml" in ambiguous
        assert "C-AI321" in ambiguous["ml"]
        assert "C-AI422" in ambiguous["ml"]


# ── compare_tracks 3+ mention guard ──────────────────────────────────────────


class TestCompareTracksThreePlusGuard:
    """
    Tests for the compare_tracks normalization guard:
    when 3+ tracks are mentioned, return clarification_needed instead of
    silently executing a two-track comparison.
    """

    def _normalize_sq(self, intent: str, track_id: str | None,
                      secondary_track_id: str | None,
                      original_text: str) -> StructuredQuery:
        from gateway.query_understanding import _normalize_one_sq
        from gateway.models.schemas import EntitySet, StructuredQuery
        secondary = None
        if secondary_track_id is not None:
            secondary = EntitySet(track_id=secondary_track_id)
        sq = StructuredQuery(
            intent=intent,
            original_text=original_text,
            entities=EntitySet(track_id=track_id),
            secondary_entities=secondary,
            params={},
        )
        return _normalize_one_sq(sq, original_text, PreprocessResult(), LastReferenced())

    # ── A: 3+ tracks in text → clarification_needed ──────────────────────────

    def test_A_three_track_ids_returns_clarification(self):
        """'Compare AI, DSE, and SWE' → clarification_needed, not compare_tracks."""
        result = self._normalize_sq(
            "compare_tracks", "AI", "DSE",
            "Compare AI, DSE, and SWE",
        )
        assert result.intent == "clarification_needed"

    def test_A_three_tracks_long_names_returns_clarification(self):
        """'Compare Artificial Intelligence, Data Science and Engineering, and SWE' → clarification_needed."""
        result = self._normalize_sq(
            "compare_tracks", "AI", "DSE",
            "Compare Artificial Intelligence, Data Science and Engineering, and SWE",
        )
        assert result.intent == "clarification_needed"

    def test_A_four_tracks_also_returns_clarification(self):
        """Four tracks mentioned → still clarification_needed."""
        result = self._normalize_sq(
            "compare_tracks", "AI", "DSE",
            "Compare AI, DSE, SWE, and CYS tracks",
        )
        assert result.intent == "clarification_needed"

    # ── B: Clarification prompt lists all options with friendly names ─────────

    def test_B_clarification_includes_all_three_options(self):
        """Clarification prompt must include all three track options with friendly names."""
        result = self._normalize_sq(
            "compare_tracks", "AI", "DSE",
            "Compare AI, DSE, and SWE",
        )
        prompt = result.params.get("clarification_prompt", "")
        assert "Artificial Intelligence (AI)" in prompt
        assert "Data Science and Engineering (DSE)" in prompt
        assert "Software Engineering (SWE)" in prompt

    def test_B_clarification_prompt_is_friendly(self):
        """Clarification prompt must explain the two-track limit."""
        result = self._normalize_sq(
            "compare_tracks", "AI", "DSE",
            "Compare AI, DSE, and SWE",
        )
        prompt = result.params.get("clarification_prompt", "")
        assert "two tracks at a time" in prompt.lower() or "two" in prompt.lower()

    def test_B_clarification_prompt_stored_in_params(self):
        """Clarification prompt must be in params['clarification_prompt']."""
        result = self._normalize_sq(
            "compare_tracks", "AI", "DSE",
            "Compare AI, DSE, and SWE",
        )
        assert "clarification_prompt" in result.params
        assert len(result.params["clarification_prompt"]) > 20

    # ── C: Two-track comparisons must NOT trigger the guard ──────────────────

    def test_C1_two_tracks_ai_vs_dse_passes(self):
        """'Compare AI and Data Science tracks' → compare_tracks (2 tracks, no guard)."""
        result = self._normalize_sq(
            "compare_tracks", "AI", "DSE",
            "Compare AI and Data Science tracks",
        )
        assert result.intent == "compare_tracks"

    def test_C2_two_tracks_ai_vs_cybersecurity_passes(self):
        """'Compare AI track and Cybersecurity' → compare_tracks (2 tracks, no guard)."""
        result = self._normalize_sq(
            "compare_tracks", "AI", "CYS",
            "Compare AI track and Cybersecurity",
        )
        assert result.intent == "compare_tracks"

    def test_C3_two_tracks_vs_notation_passes(self):
        """'SWE vs CYS' → compare_tracks (2 tracks, no guard)."""
        result = self._normalize_sq(
            "compare_tracks", "SWE", "CYS",
            "SWE vs CYS",
        )
        assert result.intent == "compare_tracks"

    def test_C4_two_tracks_with_context_words_passes(self):
        """'Compare the courses and skills in SWE and DSE' → compare_tracks."""
        result = self._normalize_sq(
            "compare_tracks", "SWE", "DSE",
            "Compare the courses and skills in SWE and DSE",
        )
        assert result.intent == "compare_tracks"

    def test_C5_two_tracks_full_names_passes(self):
        """'Compare Software Engineering and Cybersecurity' → compare_tracks."""
        result = self._normalize_sq(
            "compare_tracks", "SWE", "CYS",
            "Compare Software Engineering and Cybersecurity",
        )
        assert result.intent == "compare_tracks"

    # ── D: Unsupported/unknown track in text does not trigger guard ───────────

    def test_D_unknown_track_name_not_counted(self):
        """'Compare business and AI' → 1 canonical track (AI) → no guard, intent unchanged."""
        result = self._normalize_sq(
            "compare_tracks", "AI", "business",
            "Compare business and AI",
        )
        # Guard must NOT fire (only 1 canonical track in text) → intent stays compare_tracks
        assert result.intent == "compare_tracks"

    def test_D_only_one_canonical_track_no_guard(self):
        """Single canonical track in text → guard does not fire."""
        result = self._normalize_sq(
            "compare_tracks", "DSE", None,
            "Compare DSE and something else entirely",
        )
        assert result.intent == "compare_tracks"

    # ── E: End-to-end via LLM mock ────────────────────────────────────────────

    def test_E_end_to_end_three_tracks_clarification(self, monkeypatch):
        """End-to-end: LLM returns compare_tracks for 3-track query → clarification_needed."""
        sq_data = {
            "intent": "compare_tracks",
            "original_text": "Compare AI, DSE, and SWE",
            "entities": {"course_code": None, "role": None, "track": "AI", "skill": None},
            "secondary_entities": {"course_code": None, "role": None, "track": "DSE", "skill": None},
            "params": {},
            "session_overrides": {
                "added_courses": [], "assumed_passed_courses": [], "assumed_failed_courses": [],
                "target_role": None, "course_override_type": "none", "override_action": "accumulate",
            },
            "student_referential_fallback": False,
        }
        response = json.dumps({"queries": [sq_data]})
        client = _make_mock_client([response])
        result = _run_qu("Compare AI, DSE, and SWE", client, monkeypatch=monkeypatch)

        assert len(result) == 1
        assert result[0].intent == "clarification_needed"
        prompt = result[0].params.get("clarification_prompt", "")
        assert "Artificial Intelligence (AI)" in prompt
        assert "Data Science and Engineering (DSE)" in prompt
        assert "Software Engineering (SWE)" in prompt

    def test_E_end_to_end_two_track_regression(self, monkeypatch):
        """End-to-end: LLM returns compare_tracks for 2-track query → compare_tracks preserved."""
        sq_data = {
            "intent": "compare_tracks",
            "original_text": "Compare AI and DSE tracks",
            "entities": {"course_code": None, "role": None, "track": "AI", "skill": None},
            "secondary_entities": {"course_code": None, "role": None, "track": "DSE", "skill": None},
            "params": {},
            "session_overrides": {
                "added_courses": [], "assumed_passed_courses": [], "assumed_failed_courses": [],
                "target_role": None, "course_override_type": "none", "override_action": "accumulate",
            },
            "student_referential_fallback": False,
        }
        response = json.dumps({"queries": [sq_data]})
        client = _make_mock_client([response])
        result = _run_qu("Compare AI and DSE tracks", client, monkeypatch=monkeypatch)

        assert len(result) == 1
        assert result[0].intent == "compare_tracks"
        assert result[0].entities.track_id == "AI"
        assert result[0].secondary_entities is not None
        assert result[0].secondary_entities.track_id == "DSE"


# ── T38: Focus vs plan_semester routing guard ─────────────────────────────────

class TestFocusVsPlanSemesterRouting:
    """
    QU must not confuse 'what should I focus on' (get_focus_courses_for_target)
    with 'what should I register' (plan_semester).

    Tests H and I from the D3 chunk-3 audit.
    """

    def _focus_sq_json(self, text: str) -> str:
        sq: dict = {
            "intent": "get_focus_courses_for_target",
            "original_text": text,
            "entities": {"course_code": None, "role": "RL_ML_Engineer", "track": None, "skill": None},
            "secondary_entities": None,
            "params": {},
            "session_overrides": {
                "added_courses": [], "assumed_passed_courses": [], "assumed_failed_courses": [],
                "target_role": None, "course_override_type": "none", "override_action": "accumulate",
            },
            "student_referential_fallback": True,
        }
        return json.dumps({"queries": [sq]})

    def _plan_sq_json(self, text: str) -> str:
        sq: dict = {
            "intent": "plan_semester",
            "original_text": text,
            "entities": {"course_code": None, "role": None, "track": None, "skill": None},
            "secondary_entities": None,
            "params": {},
            "session_overrides": {
                "added_courses": [], "assumed_passed_courses": [], "assumed_failed_courses": [],
                "target_role": None, "course_override_type": "none", "override_action": "accumulate",
            },
            "student_referential_fallback": True,
        }
        return json.dumps({"queries": [sq]})

    def test_H_future_focus_query_routes_to_focus_not_plan(self, monkeypatch):
        """
        H: 'What future courses should I focus on for ML Engineer?' →
        get_focus_courses_for_target, NOT plan_semester.
        """
        user_text = "What future courses should I focus on for ML Engineer?"
        client = _make_mock_client([self._focus_sq_json(user_text)])
        result = _run_qu(user_text, client, monkeypatch=monkeypatch)
        assert len(result) == 1
        assert result[0].intent == "get_focus_courses_for_target"
        assert result[0].intent != "plan_semester"

    def test_I_register_next_semester_routes_to_plan_not_focus(self, monkeypatch):
        """
        I: 'What should I register next semester for ML Engineer?' →
        plan_semester (or clarification), NOT get_focus_courses_for_target.
        """
        user_text = "What should I register next semester for ML Engineer?"
        client = _make_mock_client([self._plan_sq_json(user_text)])
        result = _run_qu(user_text, client, monkeypatch=monkeypatch)
        assert len(result) == 1
        assert result[0].intent in ("plan_semester", "clarification_needed")
        assert result[0].intent != "get_focus_courses_for_target"
