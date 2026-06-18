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

    def test_no_pii_in_user_message(self):
        from gateway.qu_prompt import build_user_message
        last_ref = LastReferenced(course_code="C-CS301")
        # recent_turns intentionally have no student IDs, grades, or CGPA
        turns = [{"user": "Can I graduate?", "answer": "Let me check."}]
        msg = build_user_message("What about prerequisites?", last_ref, turns)
        assert "student_id" not in msg.lower()
        assert "cgpa" not in msg.lower() or "3." not in msg  # no raw GPA numbers

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
