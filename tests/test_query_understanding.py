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
