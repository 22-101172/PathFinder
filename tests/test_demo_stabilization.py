"""
Demo-stabilization regression tests (2026-07-05).
Covers: Phase 4 (requested_courses), Phase 5 (D6 multi-focus),
        Phase 6 (referential 'its'), Phase 7 (parse guards).
All tests are offline — no LLM or KG calls.
"""
from __future__ import annotations

import json
import pytest

from gateway.models.schemas import EntitySet, LastReferenced, SessionOverrides, StructuredQuery
from gateway.query_understanding import (
    _normalize_one_sq,
    _expand_d6_multi_focus,
    _parse_raw_sq,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(text: str, intent: str, course_code=None, params=None, lr=None):
    from gateway.qu_preprocessing import preprocess
    pre = preprocess(text)
    if lr is None:
        lr = LastReferenced()
    sq = StructuredQuery(
        intent=intent,
        original_text=text,
        entities=EntitySet(course_code=course_code),
        params=params or {},
        session_overrides=SessionOverrides(),
    )
    return _normalize_one_sq(sq, text, pre, lr)


def _d6_sq(focus: str, text: str = "test") -> StructuredQuery:
    return StructuredQuery(
        intent="get_student_record",
        original_text=text,
        entities=EntitySet(),
        params={"record_focus": focus},
        session_overrides=SessionOverrides(),
    )


# ── Phase 6: Referential "its" / "it" follow-ups ─────────────────────────────

class TestReferentialCourseFollowUp:

    def test_its_in_pronoun_set(self):
        """'its' must be recognized as a referential candidate."""
        from gateway.query_understanding import _is_referential_course_candidate
        assert _is_referential_course_candidate("its") is True

    def test_its_credits_resolves_to_last_course(self):
        """'what is its credits?' with entity=None → last_referenced.course_code."""
        lr = LastReferenced(course_code="C-CS443")
        result = _norm("what is its credits?", "get_course_info", lr=lr)
        assert result.entities.course_code == "C-CS443"

    def test_llm_entity_its_cleared_and_substituted(self):
        """If LLM sets entities.course_code='its', cleared then substituted via last_referenced."""
        lr = LastReferenced(course_code="C-CS443")
        result = _norm("what is its credits?", "get_course_info", course_code="its",
                       params={"entity_candidates": ["its"]}, lr=lr)
        assert result.entities.course_code == "C-CS443"

    def test_check_eligibility_referential_its_entity(self):
        """check_course_eligibility with entities.course_code='its' → last_referenced."""
        lr = LastReferenced(course_code="C-CS443")
        result = _norm("can I take it?", "check_course_eligibility", course_code="its", lr=lr)
        assert result.entities.course_code == "C-CS443"

    def test_check_eligibility_referential_no_entity(self):
        """check_course_eligibility with no entity and 'it' in text → last_referenced."""
        lr = LastReferenced(course_code="C-CS443")
        result = _norm("can I take it?", "check_course_eligibility", lr=lr)
        assert result.entities.course_code == "C-CS443"

    def test_no_referential_if_no_last_referenced(self):
        """Without last_referenced, referential stays unresolved; no crash."""
        result = _norm("what is its credits?", "get_course_info", course_code="its")
        # Should not crash; course_code should be cleared, not remain "its"
        assert result.entities.course_code != "its"

    def test_explicit_course_not_overridden(self):
        """Explicit course name is NOT treated as referential even with last_referenced set."""
        lr = LastReferenced(course_code="C-CS111")
        result = _norm(
            "what are the prerequisites of Data Security?",
            "get_course_prerequisites",
            course_code="Data Security",
            lr=lr,
        )
        # "Data Security" is a real course name, not a referential — must not be replaced
        assert result.entities.course_code is not None
        assert result.entities.course_code != "C-CS111"

    def test_its_prerequisites_resolves(self):
        """'what are its prerequisites?' → last_referenced via possessive prefix."""
        lr = LastReferenced(course_code="C-CS443")
        result = _norm("what are its prerequisites?", "get_course_prerequisites", lr=lr)
        assert result.entities.course_code == "C-CS443"


# ── Phase 4: Requested-courses extraction for plan_semester ──────────────────

class TestRequestedCoursesExtraction:

    def _plan_norm(self, text: str, pre_existing_rc=None):
        from gateway.qu_preprocessing import preprocess
        pre = preprocess(text)
        lr = LastReferenced()
        params = {}
        if pre_existing_rc is not None:
            params["requested_courses"] = pre_existing_rc
        sq = StructuredQuery(
            intent="plan_semester",
            original_text=text,
            entities=EntitySet(),
            params=params,
            session_overrides=SessionOverrides(),
        )
        return _normalize_one_sq(sq, text, pre, lr)

    def test_put_course_in_plan(self):
        """'put Introduction to Programming course in the plan' → requested_courses."""
        text = (
            "generate my next semester plan but i want in that plan to put "
            "Introduction to Programming course as i want to retake it next semester"
        )
        result = self._plan_norm(text)
        rc = result.params.get("requested_courses")
        assert rc and len(rc) >= 1
        combined = " ".join(c.lower() for c in rc)
        assert "programming" in combined or "introduction" in combined

    def test_include_course_in_plan(self):
        """'include Data Security in the plan' → requested_courses."""
        result = self._plan_norm("give me a semester plan and include Data Security in the plan")
        rc = result.params.get("requested_courses")
        assert rc and len(rc) >= 1
        combined = " ".join(c.lower() for c in rc)
        assert "data security" in combined or "security" in combined

    def test_retake_course_next_semester(self):
        """'i want to retake Algorithms next semester' → requested_courses."""
        result = self._plan_norm("plan my semester and i want to retake Algorithms next semester")
        rc = result.params.get("requested_courses")
        assert rc and len(rc) >= 1
        combined = " ".join(c.lower() for c in rc)
        assert "algorithm" in combined

    def test_along_with_course(self):
        """'along with Data Security next semester' → requested_courses."""
        result = self._plan_norm("plan my next semester along with Data Security next semester")
        rc = result.params.get("requested_courses")
        assert rc and len(rc) >= 1

    def test_llm_set_not_overwritten(self):
        """If LLM already set requested_courses, normalization does not overwrite."""
        result = self._plan_norm(
            "plan with Data Security",
            pre_existing_rc=["C-CS443"],
        )
        assert result.params["requested_courses"] == ["C-CS443"]


# ── Phase 5: D6 multi-focus expansion ─────────────────────────────────────────

class TestD6MultiFocusExpansion:

    def test_academic_standing_expands_to_completed_courses(self):
        """'academic standing and completed courses' → second SQ added."""
        text = "what is my academic standing and what are the courses that i completed?"
        sqs = [_d6_sq("academic_standing", text)]
        expanded = _expand_d6_multi_focus(sqs, text)
        focuses = [sq.params.get("record_focus") for sq in expanded]
        assert "academic_standing" in focuses
        assert "completed_courses" in focuses

    def test_both_focuses_already_present_no_duplication(self):
        """If both focuses are already in SQ list, no duplication occurs."""
        text = "what is my gpa and what courses am i currently taking?"
        sqs = [_d6_sq("cgpa", text), _d6_sq("in_progress_courses", text)]
        expanded = _expand_d6_multi_focus(sqs, text)
        focuses = [sq.params.get("record_focus") for sq in expanded]
        assert focuses.count("cgpa") == 1
        assert focuses.count("in_progress_courses") == 1

    def test_non_d6_sqs_untouched(self):
        """Non-D6 SQs are not affected."""
        text = "can I take C-CS301?"
        sqs = [StructuredQuery(
            intent="check_course_eligibility",
            original_text=text,
            entities=EntitySet(course_code="C-CS301"),
            params={},
            session_overrides=SessionOverrides(),
        )]
        expanded = _expand_d6_multi_focus(sqs, text)
        assert len(expanded) == 1
        assert expanded[0].intent == "check_course_eligibility"

    def test_caps_at_two_additional(self):
        """Expansion adds at most 2 additional SQs."""
        text = "my gpa, my level, my academic standing, my completed courses, my in progress courses"
        sqs = [_d6_sq("cgpa", text)]
        expanded = _expand_d6_multi_focus(sqs, text)
        assert len(expanded) <= 3  # original 1 + at most 2

    def test_empty_sq_list_unchanged(self):
        """Empty SQ list is returned unchanged."""
        expanded = _expand_d6_multi_focus([], "what is my gpa?")
        assert expanded == []


# ── Phase 7: _parse_raw_sq isinstance guards ──────────────────────────────────

class TestParseRawSqGuards:

    def test_entities_non_dict_does_not_crash(self):
        """LLM returns entities=True (non-dict) → treated as empty, no crash."""
        raw = {
            "intent": "get_course_info",
            "original_text": "test",
            "entities": True,
            "params": {},
            "session_overrides": {},
        }
        sq = _parse_raw_sq(raw, "test")
        assert sq.entities.course_code is None

    def test_session_overrides_non_dict_does_not_crash(self):
        """LLM returns session_overrides=True (non-dict) → empty overrides, no crash."""
        raw = {
            "intent": "get_course_info",
            "original_text": "test",
            "entities": {},
            "params": {},
            "session_overrides": True,
        }
        sq = _parse_raw_sq(raw, "test")
        assert sq.session_overrides.added_courses == []

    def test_params_non_dict_does_not_crash(self):
        """LLM returns params=True (non-dict) — was causing TypeError — now safe."""
        raw = {
            "intent": "get_student_record",
            "original_text": "my gpa",
            "entities": {},
            "params": True,
            "session_overrides": {},
        }
        sq = _parse_raw_sq(raw, "my gpa")
        assert sq.intent == "get_student_record"
        assert isinstance(sq.params, dict)

    def test_entities_list_does_not_crash(self):
        """LLM returns entities=[] (list not dict) → treated as empty."""
        raw = {
            "intent": "get_course_info",
            "original_text": "test",
            "entities": [],
            "params": {},
            "session_overrides": {},
        }
        sq = _parse_raw_sq(raw, "test")
        assert sq.entities.course_code is None

    def test_session_overrides_list_does_not_crash(self):
        """LLM returns session_overrides=[] (list not dict) → empty, no crash."""
        raw = {
            "intent": "get_course_info",
            "original_text": "test",
            "entities": {},
            "params": {},
            "session_overrides": [],
        }
        sq = _parse_raw_sq(raw, "test")
        assert sq.session_overrides.added_courses == []
