"""
Orchestrator unit tests.
All external calls (KG, RAG, ALE adapters) are mocked with MagicMock.
Tests do NOT require a live Neo4j, RAG engine, or ALE engine.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from gateway.models.schemas import (
    CourseRecord, EntitySet, LastReferenced,
    PerSQResult, SessionOverrides, SessionState,
    StudentContext, StructuredQuery, TurnWrapper,
)
from gateway.orchestrator import Orchestrator


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_orchestrator(kg=None, rag=None, ale=None) -> Orchestrator:
    return Orchestrator(
        kg_adapter=kg or MagicMock(),
        rag_adapter=rag or MagicMock(),
        ale_adapter=ale or MagicMock(),
    )


def _make_student(
    cgpa: float = 3.0,
    cumulative_chs: int = 60,
    cumulative_cps: float = 180.0,
    completed: list | None = None,
    failed: list | None = None,
    in_progress: list | None = None,
    track_id: str = "AI",
    current_semester: str = "Spring 2026",
) -> StudentContext:
    return StudentContext(
        student_id="S001",
        name="Test Student",
        program="Artificial Intelligence",
        track_id=track_id,
        level=3,
        first_semester="Fall 2022",
        study_status="Studying",
        cgpa=cgpa,
        cumulative_chs=cumulative_chs,
        cumulative_cps=cumulative_cps,
        total_credit_hours_earned=cumulative_chs,
        completed_courses=completed or [],
        failed_courses=failed or [],
        in_progress_courses=in_progress or [],
        current_semester=current_semester,
    )


def _make_session(student: StudentContext | None = None, overrides: SessionOverrides | None = None) -> SessionState:
    return SessionState(
        session_id=str(uuid.uuid4()),
        student_id="S001",
        session_name="test",
        student_context=student or _make_student(),
        overrides=overrides or SessionOverrides(),
    )


def _sq(intent: str, **kwargs) -> StructuredQuery:
    return StructuredQuery(
        intent=intent,
        original_text=kwargs.pop("original_text", intent),
        entities=EntitySet(**kwargs.pop("entities", {})),
        secondary_entities=kwargs.pop("secondary_entities", None),
        params=kwargs.pop("params", {}),
        session_overrides=kwargs.pop("session_overrides", SessionOverrides()),
        student_referential_fallback=kwargs.pop("student_referential_fallback", False),
    )


def _make_bundles(partial: dict | None = None) -> dict:
    """Create a minimal rule_bundles dict with MagicMock bundles."""
    defaults = {
        "grading_scale_rules": MagicMock(letter_to_points={"A": 4.0, "B": 3.0, "C": 2.0, "F": 0.0}),
        "graduation_requirement_rules": MagicMock(),
        "academic_warning_rules": MagicMock(cgpa_warning_threshold=2.0),
        "honors_rules": MagicMock(),
        "credit_limit_rules": MagicMock(),
        "retake_rules": MagicMock(),
        "summer_semester_rules": MagicMock(),
        "student_level_rules": MagicMock(),
    }
    if partial:
        defaults.update(partial)
    return defaults


# ── T01: Forbidden / stale intents ───────────────────────────────────────────

class TestForbiddenIntents:

    def test_plan_next_semester_returns_validation_error(self):
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("plan_next_semester")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status == "error"
        assert r.error_code == "validation_failed"
        assert r.error_category == "intent"

    def test_check_eligibility_stale_rejected(self):
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("check_eligibility", entities={"course_code": "C-CS301"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "error"
        assert result.results[0].error_code == "validation_failed"

    def test_handbook_query_stale_rejected(self):
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("handbook_query", original_text="absence policy?")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "error"

    def test_simulate_gpa_stale_rejected(self):
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("simulate_gpa")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "error"
        assert result.results[0].error_code == "validation_failed"


# ── T02: Control intents ──────────────────────────────────────────────────────

class TestControlIntents:

    def test_clarification_needed_passthrough(self):
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("clarification_needed", original_text="Which course did you mean?")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert isinstance(result, TurnWrapper)
        assert result.results[0].status == "clarification_needed"
        assert result.results[0].clarification_prompt == "Which course did you mean?"

    def test_out_of_scope_passthrough(self):
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("out_of_scope")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "out_of_scope"
        assert result.turn_status == "out_of_scope"

    def test_clarification_turn_status(self):
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("clarification_needed")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.turn_status == "needs_clarification"
        assert result.has_clarification is True

    def test_clarification_prompt_in_params_preferred_over_original_text(self):
        """
        When QU returns clarification_needed with params['clarification_prompt'] set
        (e.g. LLM-generated disambiguation question), the Orchestrator must use
        that prompt — not original_text, which may be the user's raw query text.
        """
        orch = _make_orchestrator()
        session = _make_session()
        clarification_q = (
            "Are you asking about roles in the Cybersecurity (CYS) track, "
            "or roles with 'security' in their title across all tracks?"
        )
        sqs = [_sq(
            "clarification_needed",
            original_text="What roles are connected to security?",
            params={"clarification_prompt": clarification_q},
        )]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status == "clarification_needed"
        assert r.clarification_prompt == clarification_q
        # Must NOT be the user's original query
        assert r.clarification_prompt != "What roles are connected to security?"

    def test_clarification_prompt_field_preferred_when_no_params(self):
        """sq.clarification_prompt (schema field) is used if params key is absent."""
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [StructuredQuery(
            intent="clarification_needed",
            original_text="raw user text",
            clarification_prompt="Schema-level clarification question?",
        )]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status == "clarification_needed"
        assert r.clarification_prompt == "Schema-level clarification question?"


# ── T03: Domain 2 — KG course intents ────────────────────────────────────────

class TestDomain2KG:

    def test_get_course_info_success(self):
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-CS301", "name": "OS", "credits": 3}
        orch = _make_orchestrator(kg=kg)
        session = _make_session()
        sqs = [_sq("get_course_info", entities={"course_code": "C-CS301"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"
        assert result.results[0].data["course_code"] == "C-CS301"
        kg.call.assert_called_once_with("get_course_profile", {"course_code": "C-CS301"})

    def test_get_course_info_not_found_is_informational(self):
        kg = MagicMock()
        kg.call.return_value = {"error": "course_not_found"}
        orch = _make_orchestrator(kg=kg)
        session = _make_session()
        sqs = [_sq("get_course_info", entities={"course_code": "C-XX999"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "informational"

    def test_get_course_info_missing_code_returns_clarification(self):
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("get_course_info")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "clarification_needed"

    def test_kg_adapter_error_returns_engine_error(self):
        kg = MagicMock()
        kg.call.return_value = {"error": "kg_unavailable"}
        orch = _make_orchestrator(kg=kg)
        session = _make_session()
        sqs = [_sq("get_course_info", entities={"course_code": "C-CS301"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "error"
        assert result.results[0].error_code == "engine_error"
        assert result.results[0].error_category == "kg_adapter"

    def test_get_course_prerequisites(self):
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-CS401", "direct_prerequisites": []}
        orch = _make_orchestrator(kg=kg)
        session = _make_session()
        sqs = [_sq("get_course_prerequisites", entities={"course_code": "C-CS401"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"
        kg.call.assert_called_once_with("get_prerequisites", {"course_code": "C-CS401", "depth": "direct"})

    def test_get_skills_taught(self):
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-AI321", "skills_taught": []}
        orch = _make_orchestrator(kg=kg)
        session = _make_session()
        sqs = [_sq("get_skills_taught", entities={"course_code": "C-AI321"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"

    def test_search_courses_by_skill(self):
        kg = MagicMock()
        kg.call.return_value = {"results": [], "total_results": 0}
        orch = _make_orchestrator(kg=kg)
        session = _make_session()
        sqs = [_sq("search_courses_by_skill", entities={"skill_id": "python"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"
        kg.call.assert_called_once_with("search_courses_by_skill", {"skill_ids": ["python"]})


# ── T04: Domain 1 — ALE intents ──────────────────────────────────────────────

class TestDomain1ALE:

    def test_run_graduation_audit_success(self):
        kg = MagicMock()
        kg.call.return_value = {}  # no course_profile lookup needed for empty history
        ale = MagicMock()
        ale.call.return_value = {"status": "success", "can_graduate": False}
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session(_make_student())
        sqs = [_sq("run_graduation_audit", student_referential_fallback=True)]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"
        assert result.results[0].data["status"] == "success"

    def test_run_graduation_audit_uses_base_context_not_effective(self):
        """Audit must use base_context even when session has active overrides."""
        ale = MagicMock()
        ale.call.return_value = {"status": "success", "can_graduate": False}
        kg = MagicMock()
        kg.call.return_value = {}

        student = _make_student(completed=["C-CS301"])
        session = _make_session(
            student=student,
            overrides=SessionOverrides(
                added_courses=["C-AI321"], course_override_type="assumed_done"
            ),
        )
        orch = _make_orchestrator(kg=kg, ale=ale)
        sqs = [_sq("run_graduation_audit")]
        result = orch.execute_turn(sqs, session, _make_bundles())

        # ALE should receive base_context (completed=["C-CS301"]), NOT effective (includes C-AI321)
        call_args = ale.call.call_args
        ctx_passed = call_args[0][1]  # second positional arg is student_context
        assert "C-AI321" not in ctx_passed.completed_courses
        assert result.results[0].assumptions_excluded is True

    def test_run_graduation_audit_ale_error(self):
        ale = MagicMock()
        ale.call.return_value = {"status": "error", "message": "ALE crashed"}
        kg = MagicMock()
        kg.call.return_value = {}
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session()
        sqs = [_sq("run_graduation_audit")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "error"
        assert result.results[0].error_code == "engine_error"
        assert result.results[0].error_category == "ale_adapter"

    def test_check_eligibility_dispatches_to_ale(self):
        kg = MagicMock()
        kg.call.return_value = {
            "direct_prerequisites": [{"course_code": "C-CS201"}],
            "non_course_prerequisites": [],
        }
        ale = MagicMock()
        ale.call.return_value = {"status": "eligible"}
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session()
        sqs = [_sq("check_course_eligibility", entities={"course_code": "C-CS301"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"
        kg.call.assert_called_once_with("get_prerequisites", {"course_code": "C-CS301", "depth": "direct"})

    def test_student_not_found_for_student_aware_intent(self):
        orch = _make_orchestrator()
        session = _make_session()
        session.student_context = None
        sqs = [_sq("run_graduation_audit")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "error"
        assert result.results[0].error_code == "student_not_found"

    def test_missing_rule_bundle_returns_engine_error(self):
        orch = _make_orchestrator()
        session = _make_session()
        # Provide bundles with retake_rules=None
        bundles = _make_bundles({"retake_rules": None})
        sqs = [_sq("check_course_eligibility", entities={"course_code": "C-CS301"})]
        result = orch.execute_turn(sqs, session, bundles)
        assert result.results[0].status == "error"
        assert result.results[0].error_code == "engine_error"
        assert result.results[0].error_category == "ale_adapter"


# ── T05: Domain 5 — RAG / policy ─────────────────────────────────────────────

class TestDomain5RAG:

    def test_policy_query_success(self):
        rag = MagicMock()
        rag.execute.return_value = {
            "answer": "You are allowed 20% absences.",
            "extracted_facts": ["20% absence limit"],
            "citations": [{"source": "CIS Handbook", "page": 12, "text": "20%"}],
        }
        orch = _make_orchestrator(rag=rag)
        session = _make_session()
        sqs = [_sq("policy_query", original_text="How many absences are allowed?")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"
        assert result.results[0].citations is not None

    def test_policy_query_empty_facts_is_soft_no_evidence(self):
        rag = MagicMock()
        rag.execute.return_value = {
            "answer": "Not found in handbook.",
            "extracted_facts": [],
            "citations": [],
        }
        orch = _make_orchestrator(rag=rag)
        session = _make_session()
        sqs = [_sq("policy_query", original_text="What is the grading scale?")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "soft_no_evidence"

    def test_policy_query_blank_text_returns_clarification(self):
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("policy_query", original_text="")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "clarification_needed"

    def test_policy_never_receives_student_context(self):
        rag = MagicMock()
        rag.execute.return_value = {"answer": "rule", "extracted_facts": ["fact"], "citations": []}
        orch = _make_orchestrator(rag=rag)
        session = _make_session()
        sqs = [_sq("policy_query", original_text="Warning policy?")]
        orch.execute_turn(sqs, session, _make_bundles())
        # RAG execute must be called without student_context
        rag.execute.assert_called_once_with("Warning policy?")


# ── T06: Domain 6 — Student record ───────────────────────────────────────────

class TestDomain6StudentRecord:

    def test_get_student_record_assembly(self):
        orch = _make_orchestrator()
        session = _make_session(_make_student(cgpa=3.2, completed=["C-CS301", "C-AI321"]))
        sqs = [_sq("get_student_record")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"
        data = result.results[0].data
        assert data["cgpa"] == 3.2
        assert "C-CS301" in data["completed_courses"]
        assert "student_id" not in data
        assert "name" not in data

    def test_student_record_does_not_expose_pii(self):
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("get_student_record")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        data = result.results[0].data
        for pii_field in ("student_id", "name", "military_status", "cumulative_chs",
                          "cumulative_cps", "retake_count", "total_improve_retakes_used"):
            assert pii_field not in data, f"PII field {pii_field!r} exposed in student record"

    def test_student_record_academic_standing_good(self):
        bundles = _make_bundles()
        bundles["academic_warning_rules"] = MagicMock(cgpa_warning_threshold=2.0)
        orch = _make_orchestrator()
        session = _make_session(_make_student(cgpa=3.5))
        sqs = [_sq("get_student_record")]
        result = orch.execute_turn(sqs, session, bundles)
        assert result.results[0].data["academic_standing"] == "good"

    def test_student_record_academic_standing_warning(self):
        bundles = _make_bundles()
        bundles["academic_warning_rules"] = MagicMock(cgpa_warning_threshold=2.0)
        orch = _make_orchestrator()
        session = _make_session(_make_student(cgpa=1.8))
        sqs = [_sq("get_student_record")]
        result = orch.execute_turn(sqs, session, bundles)
        assert result.results[0].data["academic_standing"] == "warning"

    def test_student_record_no_rule_bundle_gives_unknown_standing(self):
        bundles = _make_bundles({"academic_warning_rules": None})
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("get_student_record")]
        result = orch.execute_turn(sqs, session, bundles)
        assert result.results[0].data["academic_standing"] == "unknown"

    # ── D6 enrichment tests (Phase 2) ────────────────────────────────────────

    def test_student_record_includes_completed_course_details(self):
        """completed_course_details must be present when completed courses exist."""
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-CS301", "name": "Operating Systems", "credits": 3}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(_make_student(completed=["C-CS301"]))
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        data = result.results[0].data
        assert "completed_course_details" in data
        assert isinstance(data["completed_course_details"], list)
        assert len(data["completed_course_details"]) == 1

    def test_student_record_includes_in_progress_course_details(self):
        """in_progress_course_details must be present when in-progress courses exist."""
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-CS112", "name": "Programming Fundamentals", "credits": 3}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(_make_student(in_progress=["C-CS112"]))
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        data = result.results[0].data
        assert "in_progress_course_details" in data
        assert len(data["in_progress_course_details"]) == 1

    def test_student_record_includes_failed_course_details(self):
        """failed_course_details must be present when failed courses exist."""
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-MA111", "name": "Calculus I", "credits": 3}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(_make_student(failed=["C-MA111"]))
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        data = result.results[0].data
        assert "failed_course_details" in data
        assert len(data["failed_course_details"]) == 1

    def test_student_record_kg_called_for_course_profiles(self):
        """KG get_course_profile must be called for each course in the student's lists."""
        kg = MagicMock()
        kg.call.return_value = {"name": "Some Course", "credits": 3}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(_make_student(
            completed=["C-CS301"],
            in_progress=["C-CS112"],
            failed=["C-MA111"],
        ))
        orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        called_ops = [call[0][0] for call in kg.call.call_args_list]
        assert "get_course_profile" in called_ops

    def test_student_record_detail_contains_name_and_credits_from_kg(self):
        """When KG returns a valid profile, course_name and credits must be populated."""
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-CS301", "name": "Operating Systems", "credits": 3}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(_make_student(completed=["C-CS301"]))
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        detail = result.results[0].data["completed_course_details"][0]
        assert detail["course_code"] == "C-CS301"
        assert detail["course_name"] == "Operating Systems"
        assert detail["credits"] == 3

    def test_student_record_kg_failure_does_not_fail_result(self):
        """A KG adapter error during enrichment must not fail the student record result."""
        kg = MagicMock()
        kg.call.return_value = {"error": "kg_unavailable"}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(_make_student(completed=["C-CS301"]))
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        assert result.results[0].status == "success"

    def test_student_record_kg_failure_uses_fallback_detail(self):
        """KG adapter error → fallback detail with course_name=None and credits=None."""
        kg = MagicMock()
        kg.call.return_value = {"error": "kg_unavailable"}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(_make_student(completed=["C-CS301"]))
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        detail = result.results[0].data["completed_course_details"][0]
        assert detail["course_code"] == "C-CS301"
        assert detail["course_name"] is None
        assert detail["credits"] is None

    def test_student_record_course_not_found_uses_fallback_detail(self):
        """KG 'error' key for unknown course → fallback detail with None name/credits."""
        kg = MagicMock()
        kg.call.return_value = {"error": "course_not_found"}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(_make_student(completed=["C-UNKNOWN999"]))
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        detail = result.results[0].data["completed_course_details"][0]
        assert detail["course_code"] == "C-UNKNOWN999"
        assert detail["course_name"] is None

    def test_student_record_raw_codes_still_present(self):
        """completed_courses (raw codes) must still be in snapshot for backward compat."""
        kg = MagicMock()
        kg.call.return_value = {"name": "Some Course", "credits": 3}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(_make_student(completed=["C-CS301", "HUM111"]))
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        data = result.results[0].data
        assert "C-CS301" in data["completed_courses"]
        assert "HUM111" in data["completed_courses"]

    def test_student_record_scalar_focus_skips_kg_enrichment(self):
        """Scalar focus (cgpa) must not call get_course_profile."""
        kg = MagicMock()
        kg.call.return_value = {"name": "Some Course", "credits": 3}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(_make_student(completed=["C-CS301"], in_progress=["C-CS112"]))
        result = orch.execute_turn(
            [_sq("get_student_record", params={"record_focus": "cgpa"})],
            session, _make_bundles()
        )
        assert result.results[0].status == "success"
        profile_calls = [c for c in kg.call.call_args_list if c[0][0] == "get_course_profile"]
        assert len(profile_calls) == 0, "Should not call get_course_profile for scalar cgpa focus"

    def test_student_record_completed_focus_only_enriches_completed(self):
        """completed_courses focus must only enrich completed list, not in_progress."""
        kg = MagicMock()
        kg.call.return_value = {"name": "Some Course", "credits": 3}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(_make_student(completed=["C-CS301"], in_progress=["C-CS112"]))
        result = orch.execute_turn(
            [_sq("get_student_record", params={"record_focus": "completed_courses"})],
            session, _make_bundles()
        )
        data = result.results[0].data
        assert len(data["completed_course_details"]) == 1
        assert data["in_progress_course_details"] == []

    def test_student_record_level_display_included(self):
        """Snapshot must include level_display derived from level."""
        orch = _make_orchestrator()
        # _make_student uses level=3 → Junior
        session = _make_session(_make_student())
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        data = result.results[0].data
        assert "level_display" in data
        assert data["level_display"] == "Junior"

    def test_student_record_level_display_sophomore(self):
        """Snapshot must show Sophomore for level=2."""
        orch = _make_orchestrator()
        student = StudentContext(
            student_id="S001", name="Test Student", program="AI",
            track_id="AI", level=2, first_semester="Fall 2023",
            study_status="Studying", cgpa=3.0,
            cumulative_chs=40, cumulative_cps=120.0,
            total_credit_hours_earned=40,
            current_semester="Spring 2026",
        )
        session = _make_session(student)
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        data = result.results[0].data
        assert data["level_display"] == "Sophomore"

    def test_student_record_last_semester_gpa_included(self):
        """Snapshot must include last_semester_gpa when set on StudentContext."""
        orch = _make_orchestrator()
        student = StudentContext(
            student_id="S001", name="Test Student", program="AI",
            track_id="AI", level=3, first_semester="Fall 2022",
            study_status="Studying", cgpa=3.0,
            cumulative_chs=60, cumulative_cps=180.0,
            total_credit_hours_earned=60,
            current_semester="Spring 2026",
            last_semester_gpa=3.1,
        )
        session = _make_session(student)
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        data = result.results[0].data
        assert "last_semester_gpa" in data
        assert data["last_semester_gpa"] == 3.1

    def test_student_record_program_field_included(self):
        """Snapshot must include program field."""
        orch = _make_orchestrator()
        session = _make_session(_make_student())
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        assert "program" in result.results[0].data

    def test_student_record_record_focus_in_snapshot(self):
        """record_focus from params must be passed through in the snapshot."""
        orch = _make_orchestrator()
        session = _make_session(_make_student())
        result = orch.execute_turn(
            [_sq("get_student_record", params={"record_focus": "academic_level"})],
            session, _make_bundles()
        )
        assert result.results[0].data.get("record_focus") == "academic_level"


# ── T07: Multi-SQ ordered execution ──────────────────────────────────────────

class TestMultiSQ:

    def test_multi_sq_audit_plus_roadmap_ordered(self):
        """[run_graduation_audit, generate_graduation_roadmap] → 2 results in order."""
        ale = MagicMock()
        ale.call.side_effect = [
            {"status": "success", "can_graduate": False},
            {"status": "success", "semesters": []},
        ]
        kg = MagicMock()
        kg.call.return_value = {"courses": []}
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session()
        sqs = [
            _sq("run_graduation_audit"),
            _sq("generate_graduation_roadmap"),
        ]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert len(result.results) == 2
        assert result.results[0].intent == "run_graduation_audit"
        assert result.results[1].intent == "generate_graduation_roadmap"
        assert result.results[0].sq_index == 0
        assert result.results[1].sq_index == 1

    def test_first_sq_failure_does_not_block_second(self):
        """Adapter error on SQ[0] does not cascade to SQ[1]."""
        kg = MagicMock()
        # SQ[0] get_course_info → KG adapter error
        # SQ[1] policy_query → RAG succeeds
        kg.call.return_value = {"error": "kg_unavailable"}
        rag = MagicMock()
        rag.execute.return_value = {
            "answer": "rule text", "extracted_facts": ["fact"], "citations": []
        }
        orch = _make_orchestrator(kg=kg, rag=rag)
        session = _make_session()
        sqs = [
            _sq("get_course_info", entities={"course_code": "C-CS301"}),
            _sq("policy_query", original_text="Warning policy?"),
        ]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "error"
        assert result.results[1].status == "success"
        assert result.turn_status == "partial_success"

    def test_sq_indices_are_correct(self):
        rag = MagicMock()
        rag.execute.return_value = {"answer": "x", "extracted_facts": ["f"], "citations": []}
        orch = _make_orchestrator(rag=rag)
        session = _make_session()
        sqs = [
            _sq("out_of_scope"),
            _sq("policy_query", original_text="Absence policy?"),
            _sq("clarification_needed"),
        ]
        result = orch.execute_turn(sqs, session, _make_bundles())
        for i, r in enumerate(result.results):
            assert r.sq_index == i


# ── T08: Current-turn override affects engine call ───────────────────────────

class TestCurrentTurnOverrides:

    def test_current_turn_assumed_passed_affects_eligibility(self):
        """
        SQ has assumed_passed_courses=["C-CS201"].
        The eligibility check must see C-CS201 in effective_context.completed_courses.
        """
        ale = MagicMock()
        ale.call.return_value = {"status": "eligible"}
        kg = MagicMock()
        kg.call.return_value = {
            "direct_prerequisites": [{"course_code": "C-CS201"}],
            "non_course_prerequisites": [],
        }
        orch = _make_orchestrator(kg=kg, ale=ale)

        student = _make_student(completed=[])  # C-CS201 NOT completed officially
        session = _make_session(student=student)  # no previous overrides

        sq_overrides = SessionOverrides(
            assumed_passed_courses=["C-CS201"],
            course_override_type="assumed_passed",
        )
        sqs = [_sq(
            "check_course_eligibility",
            entities={"course_code": "C-CS301"},
            session_overrides=sq_overrides,
        )]

        result = orch.execute_turn(sqs, session, _make_bundles())

        # ALE should receive effective_context where C-CS201 is in completed_courses
        call_args = ale.call.call_args
        ctx_passed = call_args[0][1]
        assert "C-CS201" in ctx_passed.completed_courses
        assert result.results[0].assumptions_active is True

    def test_previous_session_overrides_are_included(self):
        """Session.overrides from previous turns must also be applied."""
        ale = MagicMock()
        ale.call.return_value = {"status": "eligible"}
        kg = MagicMock()
        kg.call.return_value = {"direct_prerequisites": [], "non_course_prerequisites": []}
        orch = _make_orchestrator(kg=kg, ale=ale)

        student = _make_student(completed=[])
        previous_overrides = SessionOverrides(
            added_courses=["C-AI100"], course_override_type="assumed_done"
        )
        session = _make_session(student=student, overrides=previous_overrides)

        sqs = [_sq("check_course_eligibility", entities={"course_code": "C-AI200"})]
        result = orch.execute_turn(sqs, session, _make_bundles())

        call_args = ale.call.call_args
        ctx_passed = call_args[0][1]
        assert "C-AI100" in ctx_passed.completed_courses

    def test_audit_always_uses_base_context_with_current_turn_overrides(self):
        """Even if current-turn SQ has an assumption, audit uses base context."""
        ale = MagicMock()
        ale.call.return_value = {"status": "success", "can_graduate": False}
        kg = MagicMock()
        kg.call.return_value = {}

        student = _make_student(completed=["C-CS301"])
        session = _make_session(student=student)

        sq_overrides = SessionOverrides(
            assumed_passed_courses=["C-AI999"],
            course_override_type="assumed_passed",
        )
        sqs = [_sq("run_graduation_audit", session_overrides=sq_overrides)]
        orch = _make_orchestrator(kg=kg, ale=ale)
        result = orch.execute_turn(sqs, session, _make_bundles())

        call_args = ale.call.call_args
        ctx_passed = call_args[0][1]
        # Audit must NOT include the assumed-passed course
        assert "C-AI999" not in ctx_passed.completed_courses
        assert result.results[0].assumptions_excluded is True

    def test_clear_override_resets_previous_session_assumptions(self):
        """override_action=clear in current turn SQ clears session.overrides for execution."""
        ale = MagicMock()
        ale.call.return_value = {"status": "eligible"}
        kg = MagicMock()
        kg.call.return_value = {"direct_prerequisites": [], "non_course_prerequisites": []}
        orch = _make_orchestrator(kg=kg, ale=ale)

        student = _make_student(completed=[])
        previous_overrides = SessionOverrides(
            added_courses=["C-AI100"], course_override_type="assumed_done"
        )
        session = _make_session(student=student, overrides=previous_overrides)

        clear_override = SessionOverrides(override_action="clear")
        sqs = [_sq(
            "check_course_eligibility",
            entities={"course_code": "C-AI200"},
            session_overrides=clear_override,
        )]
        orch.execute_turn(sqs, session, _make_bundles())

        call_args = ale.call.call_args
        ctx_passed = call_args[0][1]
        # After clear, C-AI100 should NOT be in completed_courses
        assert "C-AI100" not in ctx_passed.completed_courses


# ── T09: No forbidden intent names ───────────────────────────────────────────

class TestNoForbiddenIntents:

    def test_orchestrator_never_calls_ale_with_old_check_eligibility(self):
        """ALE must be called with 'check_course_eligibility', never 'check_eligibility'."""
        ale = MagicMock()
        ale.call.return_value = {"status": "eligible"}
        kg = MagicMock()
        kg.call.return_value = {"direct_prerequisites": [], "non_course_prerequisites": []}
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session()
        sqs = [_sq("check_course_eligibility", entities={"course_code": "C-CS301"})]
        orch.execute_turn(sqs, session, _make_bundles())
        call_args = ale.call.call_args
        ale_operation = call_args[0][0]
        assert ale_operation == "check_course_eligibility"
        assert ale_operation != "check_eligibility"

    def test_orchestrator_maps_plan_semester_to_ale_generate_semester_plan(self):
        """Intent 'plan_semester' must call ALE with 'generate_semester_plan'."""
        ale = MagicMock()
        ale.call.return_value = {"status": "success", "selected_courses": []}
        kg = MagicMock()
        kg.call.return_value = {"courses": [], "track_id": "AI", "track_name": "AI"}
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session()
        sqs = [_sq("plan_semester")]
        orch.execute_turn(sqs, session, _make_bundles())
        call_args = ale.call.call_args
        ale_operation = call_args[0][0]
        assert ale_operation == "generate_semester_plan"
        assert ale_operation != "plan_semester"

    def test_orchestrator_never_calls_ale_with_simulate_gpa(self):
        """Intent 'simulate_gpa_forward' must call ALE with 'simulate_gpa_forward'."""
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-AI321", "name": "AI", "credits": 3}
        ale = MagicMock()
        ale.call.return_value = {"status": "success", "projected_cgpa": 3.2}
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session(_make_student(in_progress=["C-AI321"]))
        sqs = [_sq("simulate_gpa_forward",
                   params={"expected_grades": {"C-AI321": "A"}})]
        orch.execute_turn(sqs, session, _make_bundles())
        call_args = ale.call.call_args
        ale_operation = call_args[0][0]
        assert ale_operation == "simulate_gpa_forward"
        assert ale_operation != "simulate_gpa"


# ── T10: TurnWrapper status derivation ───────────────────────────────────────

class TestTurnWrapperStatus:

    def test_all_success_gives_completed(self):
        rag = MagicMock()
        rag.execute.return_value = {"answer": "x", "extracted_facts": ["f"], "citations": []}
        orch = _make_orchestrator(rag=rag)
        session = _make_session()
        sqs = [
            _sq("policy_query", original_text="Warning?"),
            _sq("policy_query", original_text="Attendance?"),
        ]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.turn_status == "completed"
        assert result.has_error is False

    def test_all_errors_gives_failed(self):
        kg = MagicMock()
        kg.call.return_value = {"error": "kg_unavailable"}
        orch = _make_orchestrator(kg=kg)
        session = _make_session()
        sqs = [
            _sq("get_course_info", entities={"course_code": "C-CS301"}),
            _sq("get_skills_taught", entities={"course_code": "C-AI321"}),
        ]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.turn_status == "failed"
        assert result.has_error is True

    def test_mixed_gives_partial_success(self):
        kg = MagicMock()
        kg.call.return_value = {"error": "kg_unavailable"}
        rag = MagicMock()
        rag.execute.return_value = {"answer": "x", "extracted_facts": ["f"], "citations": []}
        orch = _make_orchestrator(kg=kg, rag=rag)
        session = _make_session()
        sqs = [
            _sq("get_course_info", entities={"course_code": "C-CS301"}),  # error
            _sq("policy_query", original_text="Policy?"),  # success
        ]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.turn_status == "partial_success"


# ── T11: extract_last_referenced ─────────────────────────────────────────────

class TestExtractLastReferenced:

    def test_returns_first_sq_with_entities(self):
        orch = _make_orchestrator()
        sqs = [
            _sq("clarification_needed"),
            _sq("get_course_info", entities={"course_code": "C-CS301"}),
            _sq("get_role_profile", entities={"role_id": "data_scientist"}),
        ]
        ref = orch.extract_last_referenced(sqs)
        assert ref.course_code == "C-CS301"

    def test_returns_none_when_no_entities(self):
        orch = _make_orchestrator()
        sqs = [_sq("clarification_needed"), _sq("out_of_scope")]
        ref = orch.extract_last_referenced(sqs)
        # None means "don't update stored last_referenced" — preserves existing context
        assert ref is None

    def test_extract_includes_all_entity_types(self):
        """extract_last_referenced must surface course_code, role_id, track_id, and skill_id."""
        orch = _make_orchestrator()
        sqs = [_sq("get_course_info", entities={
            "course_code": "C-CS301", "role_id": "data_scientist",
            "track_id": "AI", "skill_id": "python",
        })]
        ref = orch.extract_last_referenced(sqs)
        assert ref is not None
        assert ref.course_code == "C-CS301"
        assert ref.role_id == "data_scientist"
        assert ref.track_id == "AI"
        assert ref.skill_id == "python"


# ── T12: Prerequisite depth param ────────────────────────────────────────────

class TestPrerequisiteDepth:

    def test_get_course_prerequisites_full_depth_passed_to_kg(self):
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-CS401", "direct_prerequisites": [], "all_prerequisites": []}
        orch = _make_orchestrator(kg=kg)
        session = _make_session()
        sqs = [_sq("get_course_prerequisites",
                   entities={"course_code": "C-CS401"},
                   params={"depth": "full"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"
        kg.call.assert_called_once_with("get_prerequisites", {"course_code": "C-CS401", "depth": "full"})


# ── T13: GPA credits bug ──────────────────────────────────────────────────────

class TestGPACreditsBug:

    def test_simulate_gpa_zero_credit_course_preserved(self):
        """credits=0 must NOT default to 3 — zero-credit is a valid value."""
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-HUM001", "name": "Military", "credits": 0}
        ale = MagicMock()
        ale.call.return_value = {"status": "projected", "projected_cgpa": 3.0}
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session(_make_student(in_progress=["C-HUM001"]))
        sqs = [_sq("simulate_gpa_forward", params={"expected_grades": {"C-HUM001": "P"}})]
        orch.execute_turn(sqs, session, _make_bundles())
        ale.call.assert_called_once()
        # ALE is called with positional args: (operation, ctx, bundles, kg_data, params)
        positional = ale.call.call_args[0]
        params_passed = positional[4]
        planned_courses = params_passed.get("planned_courses", [])
        assert len(planned_courses) == 1
        assert planned_courses[0].credits == 0, "credits=0 must be preserved, not defaulted to 3"

    def test_simulate_gpa_missing_credits_returns_cannot_compute(self):
        """credits=None in KG profile must yield cannot_compute, not guess."""
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-XX001", "name": "Unknown"}  # no "credits" key
        ale = MagicMock()
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session(_make_student(in_progress=["C-XX001"]))
        sqs = [_sq("simulate_gpa_forward", params={"expected_grades": {"C-XX001": "A"}})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "informational"
        ale.call.assert_not_called()

    def test_simulate_gpa_course_not_found_returns_informational(self):
        """KG business not-found in GPA path must not fabricate credits."""
        kg = MagicMock()
        kg.call.return_value = {"error": "course_not_found"}
        ale = MagicMock()
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session(_make_student(in_progress=["C-XX999"]))
        sqs = [_sq("simulate_gpa_forward", params={"expected_grades": {"C-XX999": "A"}})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "informational"
        ale.call.assert_not_called()

    def test_solve_target_gpa_missing_credits_returns_cannot_compute(self):
        """credits=None in solve_target_gpa must yield informational, not guess."""
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-XX001", "name": "Unknown"}
        ale = MagicMock()
        orch = _make_orchestrator(kg=kg, ale=ale)
        student = _make_student(in_progress=["C-XX001"])
        session = _make_session(student)
        sqs = [_sq("solve_target_gpa", params={"target_gpa": 3.5})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "informational"
        ale.call.assert_not_called()

    def test_solve_target_gpa_zero_credit_course_preserved(self):
        """credits=0 for solve_target_gpa must not default to 3."""
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-HUM001", "name": "Military", "credits": 0}
        ale = MagicMock()
        ale.call.return_value = {"status": "solvable", "required_average_grade_points": 3.5}
        orch = _make_orchestrator(kg=kg, ale=ale)
        student = _make_student(in_progress=["C-HUM001"])
        session = _make_session(student)
        sqs = [_sq("solve_target_gpa", params={"target_gpa": 3.5})]
        orch.execute_turn(sqs, session, _make_bundles())
        ale.call.assert_called_once()
        positional = ale.call.call_args[0]
        params_passed = positional[4]
        planned = params_passed.get("planned_courses", [])
        assert len(planned) == 1
        assert planned[0].credits == 0


# ── T14: Graduation audit / roadmap — zero-credit courses ────────────────────

class TestGraduationZeroCredit:

    def test_graduation_audit_passes_required_zero_credit_courses(self):
        """Orchestrator must pass required_zero_credit_courses in kg_data to ALE."""
        kg = MagicMock()
        ale = MagicMock()

        def kg_side_effect(operation, params):
            if operation == "get_courses_by_track":
                return {"courses": [
                    {"course_code": "C-HUM001", "credits": 0, "name": "Military Training",
                     "level": 1, "semester_offering": [], "prerequisites": []},
                    {"course_code": "C-CS101", "credits": 3, "name": "Intro Programming",
                     "level": 1, "semester_offering": [], "prerequisites": []},
                ]}
            return {}

        kg.call.side_effect = kg_side_effect
        ale.call.return_value = {"status": "not_eligible", "checks": []}
        student = _make_student(track_id="AI")
        session = _make_session(student=student)
        orch = _make_orchestrator(kg=kg, ale=ale)
        sqs = [_sq("run_graduation_audit")]
        orch.execute_turn(sqs, session, _make_bundles())

        call_args = ale.call.call_args
        kg_data_passed = call_args[0][3]
        assert "required_zero_credit_courses" in kg_data_passed
        assert "C-HUM001" in kg_data_passed["required_zero_credit_courses"]
        assert "C-CS101" not in kg_data_passed["required_zero_credit_courses"]

    def test_graduation_roadmap_passes_required_zero_credit_courses(self):
        """Orchestrator must pass required_zero_credit_courses to roadmap ALE call."""
        kg = MagicMock()
        ale = MagicMock()

        def kg_side_effect(operation, params):
            if operation == "get_courses_by_track":
                return {"courses": [
                    {"course_code": "C-HUM001", "credits": 0, "name": "Military Training",
                     "level": 1, "semester_offering": ["Fall"], "prerequisites": []},
                    {"course_code": "C-CS201", "credits": 3, "name": "Data Structures",
                     "level": 2, "semester_offering": ["Fall", "Spring"], "prerequisites": []},
                ]}
            return {}

        kg.call.side_effect = kg_side_effect
        ale.call.return_value = {"status": "complete", "semester_plans": []}
        student = _make_student(track_id="AI")
        session = _make_session(student=student)
        orch = _make_orchestrator(kg=kg, ale=ale)
        sqs = [_sq("generate_graduation_roadmap")]
        orch.execute_turn(sqs, session, _make_bundles())

        call_args = ale.call.call_args
        kg_data_passed = call_args[0][3]
        assert "required_zero_credit_courses" in kg_data_passed
        assert "C-HUM001" in kg_data_passed["required_zero_credit_courses"]

    def test_graduation_audit_blocked_for_unsupported_track(self):
        """Audit must return informational not_applicable when student track is unsupported."""
        from gateway.models.schemas import StudentContext
        orch = _make_orchestrator()
        student = StudentContext(
            student_id="S001", name="Test", program="Unknown",
            track_id=None, track_status="unsupported", track_error_code="unsupported_track",
            level=3, first_semester="Fall 2022", study_status="Studying",
            total_credit_hours_earned=60,
        )
        session = _make_session(student=student)
        sqs = [_sq("run_graduation_audit")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "informational"
        assert result.results[0].data["reason_code"] == "unsupported_track"


# ── T15: Unsupported-track gate ───────────────────────────────────────────────

class TestUnsupportedTrack:

    def _make_unsupported_student(self):
        from gateway.models.schemas import StudentContext
        return StudentContext(
            student_id="S001", name="Test", program="General",
            track_id=None, track_status="unsupported", track_error_code="unsupported_track",
            level=2, first_semester="Fall 2023", study_status="Studying",
            total_credit_hours_earned=30,
        )

    def test_plan_semester_blocked_for_unsupported_track(self):
        orch = _make_orchestrator()
        session = _make_session(student=self._make_unsupported_student())
        sqs = [_sq("plan_semester")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "informational"
        assert result.results[0].data["reason_code"] == "unsupported_track"

    def test_roadmap_blocked_for_unsupported_track(self):
        orch = _make_orchestrator()
        session = _make_session(student=self._make_unsupported_student())
        sqs = [_sq("generate_graduation_roadmap")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "informational"
        assert result.results[0].data["reason_code"] == "unsupported_track"

    def test_explicit_track_query_works_for_unsupported_student(self):
        """get_track_overview with explicit entity must work even if student track is unsupported."""
        kg = MagicMock()
        kg.call.return_value = {"track_id": "AI", "name": "AI Track"}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=self._make_unsupported_student())
        # Explicit entity provided → should NOT be blocked by unsupported track
        sqs = [_sq("get_track_overview", entities={"track_id": "AI"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"

    def test_get_track_overview_blocked_when_falling_back_to_unsupported_track(self):
        """get_track_overview without explicit entity on unsupported-track student → informational."""
        orch = _make_orchestrator()
        session = _make_session(student=self._make_unsupported_student())
        sqs = [_sq("get_track_overview", student_referential_fallback=True)]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "informational"
        assert result.results[0].data["reason_code"] == "unsupported_track"

    def test_compare_tracks_explicit_both_works_despite_unsupported_student(self):
        """compare_tracks with both explicit tracks must succeed even if student track is unsupported."""
        kg = MagicMock()
        kg.call.return_value = {"comparison": {}}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=self._make_unsupported_student())
        sqs = [_sq("compare_tracks",
                   entities={"track_id": "AI"},
                   secondary_entities=EntitySet(track_id="DSE"))]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"


# ── T16: Focus courses referential flag ──────────────────────────────────────

class TestFocusCourses:

    def test_general_query_passes_empty_completed_courses(self):
        """Non-referential focus query must not leak student completed courses."""
        kg = MagicMock()
        kg.call.return_value = {"focus_courses": []}
        orch = _make_orchestrator(kg=kg)
        student = _make_student(completed=["C-CS301", "C-CS401"])
        session = _make_session(student=student)
        sqs = [_sq("get_focus_courses_for_target",
                   entities={"role_id": "data_scientist"},
                   student_referential_fallback=False)]
        orch.execute_turn(sqs, session, _make_bundles())
        call_args = kg.call.call_args
        assert call_args[0][1]["completed_courses"] == []

    def test_referential_query_passes_completed_courses(self):
        """Referential focus query must pass completed courses for personalisation."""
        kg = MagicMock()
        kg.call.return_value = {"focus_courses": []}
        orch = _make_orchestrator(kg=kg)
        student = _make_student(completed=["C-CS301", "C-CS401"])
        session = _make_session(student=student)
        sqs = [_sq("get_focus_courses_for_target",
                   entities={"role_id": "data_scientist"},
                   student_referential_fallback=True)]
        orch.execute_turn(sqs, session, _make_bundles())
        call_args = kg.call.call_args
        assert "C-CS301" in call_args[0][1]["completed_courses"]


# ── T17: Student record assumptions flag ─────────────────────────────────────

class TestStudentRecordAssumptions:

    def test_student_record_flags_override_state_when_assumptions_active(self):
        """get_student_record must set override_state_active=True when assumptions are present."""
        orch = _make_orchestrator()
        student = _make_student(completed=["C-CS301"])
        session = _make_session(student=student)
        sq_overrides = SessionOverrides(
            added_courses=["C-AI321"], course_override_type="assumed_done"
        )
        sqs = [_sq("get_student_record", session_overrides=sq_overrides)]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"
        assert result.results[0].override_state_active is True

    def test_student_record_no_flag_when_no_assumptions(self):
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("get_student_record")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"
        assert result.results[0].override_state_active is None


# ── T18: RAG error handling ───────────────────────────────────────────────────

class TestRAGErrorHandling:

    def test_rag_adapter_error_returns_engine_error(self):
        rag = MagicMock()
        rag.execute.return_value = {"error": "rag_unavailable"}
        orch = _make_orchestrator(rag=rag)
        session = _make_session()
        sqs = [_sq("policy_query", original_text="Absence policy?")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "error"
        assert result.results[0].error_code == "engine_error"
        assert result.results[0].error_category == "rag_adapter"

    def test_rag_empty_query_error_returns_clarification(self):
        rag = MagicMock()
        rag.execute.return_value = {"error": "empty_query"}
        orch = _make_orchestrator(rag=rag)
        session = _make_session()
        sqs = [_sq("policy_query", original_text="Something")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "clarification_needed"

    def test_rag_no_evidence_without_error_is_soft(self):
        rag = MagicMock()
        rag.execute.return_value = {"answer": "Not found.", "extracted_facts": [], "citations": []}
        orch = _make_orchestrator(rag=rag)
        session = _make_session()
        sqs = [_sq("policy_query", original_text="Obscure policy?")]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "soft_no_evidence"


# ── T19: All 26 active intents smoke coverage ─────────────────────────────────

class TestAllIntentsCoverage:
    """Smoke: every active intent routes to a known (non-unhandled) status."""

    def _smoke(self, intent, kg_return=None, ale_return=None, rag_return=None,
               entities=None, params=None, sq_extra=None, original_text=None):
        kg = MagicMock()
        ale = MagicMock()
        rag = MagicMock()
        kg.call.return_value = kg_return or {}
        ale.call.return_value = ale_return or {"status": "success"}
        rag.execute.return_value = rag_return or {"answer": "x", "extracted_facts": ["f"], "citations": []}
        orch = _make_orchestrator(kg=kg, ale=ale, rag=rag)
        student = _make_student(completed=["C-CS101"], in_progress=["C-CS201"])
        session = _make_session(student=student)
        sq = _sq(intent, original_text=original_text or intent,
                 entities=entities or {}, params=params or {}, **(sq_extra or {}))
        result = orch.execute_turn([sq], session, _make_bundles())
        r = result.results[0]
        assert not (r.status == "error" and "Unrecognised intent" in (r.error_detail or "")), \
            f"Intent {intent!r} hit unhandled fallback: {r}"
        return r

    def test_plan_semester(self):
        r = self._smoke("plan_semester",
                        kg_return={"courses": []}, ale_return={"status": "success", "plans": []})
        assert r.status in ("success", "informational", "clarification_needed", "error")

    def test_generate_graduation_roadmap(self):
        r = self._smoke("generate_graduation_roadmap",
                        kg_return={"courses": []}, ale_return={"status": "complete", "semester_plans": []})
        assert r.status in ("success", "informational", "error")

    def test_run_graduation_audit(self):
        r = self._smoke("run_graduation_audit",
                        kg_return={"courses": []}, ale_return={"status": "not_eligible", "checks": []})
        assert r.status in ("success", "informational", "error")

    def test_check_course_eligibility(self):
        r = self._smoke("check_course_eligibility",
                        entities={"course_code": "C-CS301"},
                        kg_return={"direct_prerequisites": [], "non_course_prerequisites": []},
                        ale_return={"status": "eligible"})
        assert r.status in ("success", "error")

    def test_simulate_gpa_forward(self):
        r = self._smoke("simulate_gpa_forward",
                        kg_return={"course_code": "C-CS201", "name": "DS", "credits": 3},
                        ale_return={"status": "projected", "projected_cgpa": 3.1},
                        params={"expected_grades": {"C-CS201": "A"}})
        assert r.status in ("success", "informational")

    def test_solve_target_gpa(self):
        r = self._smoke("solve_target_gpa",
                        kg_return={"course_code": "C-CS201", "name": "DS", "credits": 3},
                        ale_return={"status": "solvable"},
                        params={"target_gpa": 3.5})
        assert r.status in ("success", "informational")

    def test_get_course_info(self):
        r = self._smoke("get_course_info",
                        entities={"course_code": "C-CS301"},
                        kg_return={"course_code": "C-CS301", "name": "OS", "credits": 3})
        assert r.status == "success"

    def test_get_course_prerequisites(self):
        r = self._smoke("get_course_prerequisites",
                        entities={"course_code": "C-CS301"},
                        kg_return={"course_code": "C-CS301", "direct_prerequisites": []})
        assert r.status == "success"

    def test_get_skills_taught(self):
        r = self._smoke("get_skills_taught",
                        entities={"course_code": "C-CS301"},
                        kg_return={"skills_taught": []})
        assert r.status == "success"

    def test_search_courses_by_skill(self):
        r = self._smoke("search_courses_by_skill",
                        entities={"skill_id": "python"},
                        kg_return={"results": []})
        assert r.status == "success"

    def test_get_role_profile(self):
        r = self._smoke("get_role_profile",
                        entities={"role_id": "data_scientist"},
                        kg_return={"role_id": "data_scientist"})
        assert r.status == "success"

    def test_get_roles_by_track(self):
        r = self._smoke("get_roles_by_track",
                        entities={"track_id": "AI"},
                        kg_return={"roles": []})
        assert r.status in ("success", "informational")

    def test_compute_skill_gap(self):
        r = self._smoke("compute_skill_gap",
                        entities={"role_id": "data_scientist"},
                        kg_return={"skill_gap": []})
        assert r.status in ("success", "informational")

    def test_compute_alignment_score(self):
        r = self._smoke("compute_alignment_score",
                        entities={"role_id": "data_scientist"},
                        kg_return={"score": 0.5})
        assert r.status in ("success", "informational")

    def test_recommend_courses_to_close_gap(self):
        r = self._smoke("recommend_courses_to_close_gap",
                        entities={"role_id": "data_scientist"},
                        kg_return={"recommendations": []})
        assert r.status in ("success", "informational")

    def test_find_best_matching_roles(self):
        r = self._smoke("find_best_matching_roles",
                        kg_return={"roles": []})
        assert r.status in ("success", "informational")

    def test_estimate_alignment_improvement(self):
        """Smoke: estimate_alignment_improvement resolves planned codes then calls KG."""
        kg = MagicMock()

        def _kg_side(op, params, **kwargs):
            if op == "resolve_entity":
                # Course codes resolve to themselves
                return {"status": "ok", "resolved_id": params.get("entity_text"), "name": "Course"}
            return {"improvement": 0.1}

        kg.call.side_effect = _kg_side
        orch = _make_orchestrator(kg=kg)
        student = _make_student(completed=["C-CS101"])
        session = _make_session(student=student)
        sq = _sq("estimate_alignment_improvement",
                 entities={"role_id": "data_scientist"},
                 params={"planned_courses": ["C-CS301"]})
        result = orch.execute_turn([sq], session, _make_bundles())
        r = result.results[0]
        assert r.status in ("success", "informational")

    def test_get_focus_courses_for_target(self):
        r = self._smoke("get_focus_courses_for_target",
                        entities={"role_id": "data_scientist"},
                        kg_return={"focus_courses": []})
        assert r.status in ("success", "informational")

    def test_get_track_overview(self):
        r = self._smoke("get_track_overview",
                        entities={"track_id": "AI"},
                        kg_return={"track_id": "AI"})
        assert r.status == "success"

    def test_compare_tracks(self):
        r = self._smoke("compare_tracks",
                        entities={"track_id": "AI"},
                        sq_extra={"secondary_entities": EntitySet(track_id="DSE")},
                        kg_return={"comparison": {}})
        assert r.status in ("success", "informational")

    def test_recommend_track_for_role(self):
        r = self._smoke("recommend_track_for_role",
                        entities={"role_id": "data_scientist"},
                        kg_return={"recommended_track": "AI"})
        assert r.status == "success"

    def test_recommend_track_for_skill(self):
        r = self._smoke("recommend_track_for_skill",
                        entities={"skill_id": "python"},
                        kg_return={"recommended_track": "AI"})
        assert r.status == "success"

    def test_policy_query(self):
        r = self._smoke("policy_query",
                        original_text="Absence policy?",
                        rag_return={"answer": "20% limit", "extracted_facts": ["20%"], "citations": []})
        assert r.status == "success"

    def test_get_student_record(self):
        r = self._smoke("get_student_record")
        assert r.status == "success"

    def test_clarification_needed(self):
        r = self._smoke("clarification_needed", original_text="Which course?")
        assert r.status == "clarification_needed"

    def test_out_of_scope(self):
        r = self._smoke("out_of_scope")
        assert r.status == "out_of_scope"


# ── T20: improve_retake_number must be 1, never from retake_count ─────────────

class TestImproveRetakeNumber:

    def test_simulate_gpa_improve_retake_number_is_1_regardless_of_retake_count(self):
        """improve_retake_number must be 1 even when retake_count[code] = 3."""
        from gateway.models.schemas import CourseRecord
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-CS301", "name": "OS", "credits": 3}
        ale = MagicMock()
        ale.call.return_value = {"status": "projected", "projected_cgpa": 3.1}
        orch = _make_orchestrator(kg=kg, ale=ale)
        student = _make_student(
            completed=["C-CS301"],
            current_semester="Spring 2026",
        )
        # Inject retake_count with a high value to prove it is NOT used
        student = student.model_copy(update={"retake_count": {"C-CS301": 3}})
        session = _make_session(student=student)
        sqs = [_sq("simulate_gpa_forward", params={"expected_grades": {"C-CS301": "A"}})]
        orch.execute_turn(sqs, session, _make_bundles())
        ale.call.assert_called_once()
        params_passed = ale.call.call_args[0][4]
        planned = params_passed["planned_courses"]
        assert len(planned) == 1
        assert planned[0].attempt_type == "improve_retake"
        assert planned[0].improve_retake_number == 1, (
            "improve_retake_number must be 1 (MVP-safe), not retake_count value 3"
        )

    def test_solve_target_gpa_improve_retake_number_is_1_regardless_of_retake_count(self):
        """improve_retake_number must be 1 even when retake_count[code] = 3."""
        kg = MagicMock()
        kg.call.return_value = {"course_code": "C-CS301", "name": "OS", "credits": 3}
        ale = MagicMock()
        ale.call.return_value = {"status": "solvable", "required_average_grade_points": 3.0}
        orch = _make_orchestrator(kg=kg, ale=ale)
        student = _make_student(completed=["C-CS301"])
        student = student.model_copy(update={"retake_count": {"C-CS301": 3}})
        session = _make_session(student=student)
        sqs = [_sq("solve_target_gpa",
                   params={"target_gpa": 3.5, "planned_courses": ["C-CS301"]})]
        orch.execute_turn(sqs, session, _make_bundles())
        ale.call.assert_called_once()
        params_passed = ale.call.call_args[0][4]
        planned = params_passed["planned_courses"]
        assert len(planned) == 1
        assert planned[0].attempt_type == "improve_retake"
        assert planned[0].improve_retake_number == 1, (
            "improve_retake_number must be 1 (MVP-safe), not retake_count value 3"
        )


# ── T21: plan_semester consumes target_semester / target_semester_text ─────────

class TestPlanSemesterTargetSemester:

    def test_plan_semester_target_semester_param_parsed(self):
        """params.target_semester='Fall 2027' must resolve target_semester_type='Fall'."""
        kg = MagicMock()
        kg.call.return_value = {"courses": []}
        ale = MagicMock()
        ale.call.return_value = {"status": "success", "plans": []}
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session()
        sqs = [_sq("plan_semester", params={"target_semester": "Fall 2027"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status == "success"
        ale.call.assert_called_once()
        params_passed = ale.call.call_args[0][4]
        assert params_passed["target_semester_type"] == "Fall"

    def test_plan_semester_target_semester_text_parsed(self):
        """params.target_semester_text='Spring 2027' must resolve target_semester_type='Spring'."""
        kg = MagicMock()
        kg.call.return_value = {"courses": []}
        ale = MagicMock()
        ale.call.return_value = {"status": "success", "plans": []}
        orch = _make_orchestrator(kg=kg, ale=ale)
        session = _make_session()
        sqs = [_sq("plan_semester", params={"target_semester_text": "Spring 2027"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        ale.call.assert_called_once()
        params_passed = ale.call.call_args[0][4]
        assert params_passed["target_semester_type"] == "Spring"

    def test_plan_semester_malformed_target_semester_returns_validation_error(self):
        """A malformed target_semester string must return validation_failed, not a crash."""
        orch = _make_orchestrator()
        session = _make_session()
        sqs = [_sq("plan_semester", params={"target_semester": "not-a-semester"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status == "error"
        assert r.error_code == "validation_failed"


# ── T22: resolved track_id passed as target_track to ALE ──────────────────────

class TestResolvedTrackToALE:

    def test_plan_semester_entity_track_id_passes_to_ale_target_track(self):
        """entities.track_id must reach ALE as target_track even when not in params."""
        kg = MagicMock()
        kg.call.return_value = {"courses": []}
        ale = MagicMock()
        ale.call.return_value = {"status": "success", "plans": []}
        orch = _make_orchestrator(kg=kg, ale=ale)
        # Student has no track (track_id=None) so entities.track_id="AI" is the only source
        from gateway.models.schemas import StudentContext
        student = StudentContext(
            student_id="S001", name="Test", program="General",
            track_id=None, track_status="supported",
            level=2, first_semester="Fall 2023", study_status="Studying",
            cgpa=2.5, cumulative_chs=40, cumulative_cps=100.0,
            total_credit_hours_earned=40,
            current_semester="Spring 2026",
        )
        session = _make_session(student=student)
        sqs = [_sq("plan_semester", entities={"track_id": "AI"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        ale.call.assert_called_once()
        params_passed = ale.call.call_args[0][4]
        assert params_passed["target_track"] == "AI"

    def test_graduation_roadmap_entity_track_id_passes_to_ale_target_track(self):
        """entities.track_id must reach ALE roadmap as target_track."""
        kg = MagicMock()
        kg.call.return_value = {"courses": []}
        ale = MagicMock()
        ale.call.return_value = {"status": "complete", "semester_plans": []}
        orch = _make_orchestrator(kg=kg, ale=ale)
        from gateway.models.schemas import StudentContext
        student = StudentContext(
            student_id="S001", name="Test", program="General",
            track_id=None, track_status="supported",
            level=2, first_semester="Fall 2023", study_status="Studying",
            cgpa=2.5, cumulative_chs=40, cumulative_cps=100.0,
            total_credit_hours_earned=40,
            current_semester="Spring 2026",
        )
        session = _make_session(student=student)
        sqs = [_sq("generate_graduation_roadmap", entities={"track_id": "DSE"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        ale.call.assert_called_once()
        params_passed = ale.call.call_args[0][4]
        assert params_passed["target_track"] == "DSE"


# ── T23: relative semester phrase resolution ──────────────────────────────────

class TestRelativeSemesterPhrases:

    def test_roadmap_relative_target_semester_resolves_to_ale(self):
        """'3 Falls from now' with Spring 2026 → ALE target_end_semester_type='Fall', target_end_year=2029."""
        kg = MagicMock()
        kg.call.return_value = {"courses": []}
        ale = MagicMock()
        ale.call.return_value = {"status": "complete", "semester_plans": []}
        orch = _make_orchestrator(kg=kg, ale=ale)
        student = _make_student(current_semester="Spring 2026")
        session = _make_session(student=student)
        sqs = [_sq("generate_graduation_roadmap",
                   params={"target_semester_text": "3 Falls from now",
                           "semester_resolution_source": "relative"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status in ("success", "informational")
        ale.call.assert_called_once()
        params_passed = ale.call.call_args[0][4]
        assert params_passed["target_end_semester_type"] == "Fall"
        assert params_passed["target_end_year"] == 2029

    def test_plan_semester_relative_target_semester_resolves_to_type(self):
        """'3 Falls from now' with Spring 2026 → ALE target_semester_type='Fall'."""
        kg = MagicMock()
        kg.call.return_value = {"courses": []}
        ale = MagicMock()
        ale.call.return_value = {"status": "success", "plans": []}
        orch = _make_orchestrator(kg=kg, ale=ale)
        student = _make_student(current_semester="Spring 2026")
        session = _make_session(student=student)
        sqs = [_sq("plan_semester",
                   params={"target_semester_text": "3 Falls from now",
                           "semester_resolution_source": "relative"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        assert result.results[0].status in ("success", "informational")
        ale.call.assert_called_once()
        params_passed = ale.call.call_args[0][4]
        assert params_passed["target_semester_type"] == "Fall"

    def test_roadmap_unresolvable_phrase_returns_validation_failed(self):
        """An unrecognised relative phrase must return validation_failed, not crash."""
        orch = _make_orchestrator()
        student = _make_student(current_semester="Spring 2026")
        session = _make_session(student=student)
        sqs = [_sq("generate_graduation_roadmap",
                   params={"target_semester_text": "sometime next year"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status == "error"
        assert r.error_code == "validation_failed"

    def test_plan_semester_unresolvable_phrase_returns_validation_failed(self):
        """An unrecognised phrase in plan_semester must return validation_failed."""
        orch = _make_orchestrator()
        student = _make_student(current_semester="Spring 2026")
        session = _make_session(student=student)
        sqs = [_sq("plan_semester",
                   params={"target_semester": "sometime next year"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status == "error"
        assert r.error_code == "validation_failed"


# ── Skill-gap covered_by enrichment ──────────────────────────────────────────

class TestSkillGapCoveredByEnrichment:
    """
    Orchestrator._enrich_skill_gap_covered_by converts raw course-code strings
    in covered_skills[*].covered_by into {course_code, name} dicts.
    """

    _SKILL_GAP_WITH_CODES = {
        "role_id": "RL_ML_Engineer",
        "role_name": "Machine Learning Engineer",
        "missing_skills": [{"skill_id": "SK_DL", "name": "Deep Learning"}],
        "covered_skills": [
            {"skill_id": "SK_ML", "name": "Machine Learning", "covered_by": ["C-AI311"]},
            {"skill_id": "SK_Stats", "name": "Statistics",
             "covered_by": ["C-ST211", "C-DE211"]},
        ],
        "skill_gap_count": 1,
    }

    def _make_kg_with_metadata(self) -> MagicMock:
        kg = MagicMock()
        _COURSE_NAMES = {
            "C-AI311": "Introduction to Artificial Intelligence",
            "C-ST211": "Introduction to Probability and Statistics",
            "C-DE211": "Data Analysis",
        }
        kg.call.return_value = self._SKILL_GAP_WITH_CODES
        kg.get_course_metadata.side_effect = (
            lambda code: {"name": _COURSE_NAMES.get(code)}
        )
        return kg

    def test_covered_by_enriched_with_course_names(self):
        """covered_by string codes → {course_code, name} with resolved names."""
        kg = self._make_kg_with_metadata()
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=_make_student(completed=["C-AI311", "C-ST211", "C-DE211"]))
        sqs = [_sq("compute_skill_gap", entities={"role_id": "RL_ML_Engineer"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status == "success"
        covered = r.data["covered_skills"]
        ml_skill = next(s for s in covered if s["skill_id"] == "SK_ML")
        assert ml_skill["covered_by"] == [
            {"course_code": "C-AI311", "name": "Introduction to Artificial Intelligence"}
        ]
        stats_skill = next(s for s in covered if s["skill_id"] == "SK_Stats")
        cb_codes = [cb["course_code"] for cb in stats_skill["covered_by"]]
        assert "C-ST211" in cb_codes
        assert "C-DE211" in cb_codes
        assert stats_skill["covered_by"][0]["name"] == "Introduction to Probability and Statistics"

    def test_covered_by_name_is_none_when_metadata_unavailable(self):
        """If KG metadata returns name=None, covered_by item has name=None (show code only)."""
        kg = MagicMock()
        kg.call.return_value = {
            "role_id": "RL_ML_Engineer", "role_name": "ML Engineer",
            "missing_skills": [],
            "covered_skills": [
                {"skill_id": "SK_ML", "name": "Machine Learning", "covered_by": ["C-UNKNOWN"]},
            ],
            "skill_gap_count": 0,
        }
        kg.get_course_metadata.return_value = {"name": None}
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=_make_student(completed=["C-UNKNOWN"]))
        sqs = [_sq("compute_skill_gap", entities={"role_id": "RL_ML_Engineer"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        covered = r.data["covered_skills"]
        assert covered[0]["covered_by"] == [{"course_code": "C-UNKNOWN", "name": None}]

    def test_already_enriched_dicts_passed_through_unchanged(self):
        """covered_by items that are already dicts are not re-processed."""
        kg = MagicMock()
        kg.call.return_value = {
            "role_id": "RL_ML_Engineer", "role_name": "ML Engineer",
            "missing_skills": [],
            "covered_skills": [
                {"skill_id": "SK_ML", "name": "Machine Learning",
                 "covered_by": [{"course_code": "C-AI311", "name": "Intro AI"}]},
            ],
            "skill_gap_count": 0,
        }
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=_make_student(completed=["C-AI311"]))
        sqs = [_sq("compute_skill_gap", entities={"role_id": "RL_ML_Engineer"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        covered = r.data["covered_skills"]
        # Already-enriched dict unchanged
        assert covered[0]["covered_by"] == [{"course_code": "C-AI311", "name": "Intro AI"}]
        # get_course_metadata should NOT be called (no string codes to resolve)
        kg.get_course_metadata.assert_not_called()

    def test_empty_covered_skills_returns_result_unchanged(self):
        """No covered_skills → no enrichment, result passes through."""
        kg = MagicMock()
        kg.call.return_value = {
            "role_id": "RL_ML_Engineer", "role_name": "ML Engineer",
            "missing_skills": [{"skill_id": "SK_ML", "name": "Machine Learning"}],
            "covered_skills": [],
            "skill_gap_count": 1,
        }
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=_make_student(completed=["C-AI311"]))
        sqs = [_sq("compute_skill_gap", entities={"role_id": "RL_ML_Engineer"})]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status == "success"
        assert r.data["covered_skills"] == []
        kg.get_course_metadata.assert_not_called()


# ── T22: estimate_alignment_improvement course resolution ─────────────────────

class TestEstimateAlignmentImprovementCourseResolution:
    """estimate_alignment_improvement resolves raw course names to codes before KG call."""

    _KG_RESULT = {
        "role_id": "RL_Data_Scientist", "role_name": "Data Scientist",
        "current_alignment_score": 0.40,
        "current_alignment_percentage": 40.0,
        "projected_alignment_score": 0.65,
        "projected_alignment_percentage": 65.0,
        "alignment_improvement": 0.25,
        "newly_covered_skills": [],
        "still_missing_skills": [],
        "total_newly_covered": 0,
        "total_still_missing": 0,
    }

    def test_A_raw_names_resolved_to_codes(self):
        """Raw course names are resolved to codes before estimate_alignment_improvement KG call."""
        kg = MagicMock()

        def _side_effect(op, params, **kwargs):
            if op == "resolve_entity":
                text = params.get("entity_text", "")
                if "machine learning" in text.lower():
                    return {"status": "ok", "resolved_id": "C-AI321", "name": "Intro to ML"}
                if "natural language" in text.lower():
                    return {"status": "ok", "resolved_id": "C-AI424", "name": "NLP"}
                return {"status": "not_found"}
            if op == "estimate_alignment_improvement":
                return self._KG_RESULT
            return {}

        kg.call.side_effect = _side_effect
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=_make_student(completed=["C-CS301"]))
        sqs = [_sq(
            "estimate_alignment_improvement",
            entities={"role_id": "RL_Data_Scientist"},
            params={"planned_courses": [
                "Introduction to Machine Learning",
                "Natural Language Processing",
            ]},
        )]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status == "success"
        # KG must have received codes, not raw names
        kg_call_args = [
            c for c in kg.call.call_args_list
            if c[0][0] == "estimate_alignment_improvement"
        ]
        assert len(kg_call_args) == 1
        planned_sent = kg_call_args[0][0][1]["planned_courses"]
        assert "C-AI321" in planned_sent
        assert "C-AI424" in planned_sent
        assert "Introduction to Machine Learning" not in planned_sent

    def test_B_entity_course_code_included_as_planned(self):
        """sq.entities.course_code is included as a planned course code."""
        kg = MagicMock()

        def _side_effect(op, params, **kwargs):
            if op == "resolve_entity":
                text = params.get("entity_text", "")
                return {"status": "ok", "resolved_id": text, "name": "Course"}
            if op == "estimate_alignment_improvement":
                return self._KG_RESULT
            return {}

        kg.call.side_effect = _side_effect
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=_make_student(completed=["C-CS301"]))
        # No planned_courses in params — only entity course_code
        sqs = [_sq(
            "estimate_alignment_improvement",
            entities={"role_id": "RL_Data_Scientist", "course_code": "C-AI321"},
        )]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status in ("success", "informational")
        kg_call_args = [
            c for c in kg.call.call_args_list
            if c[0][0] == "estimate_alignment_improvement"
        ]
        assert len(kg_call_args) == 1
        assert "C-AI321" in kg_call_args[0][0][1]["planned_courses"]

    def test_C_mixed_valid_invalid_preserves_unresolved_names(self):
        """Mixed valid/invalid → valid codes sent to KG, unresolved names in result data."""
        kg = MagicMock()

        def _side_effect(op, params, **kwargs):
            if op == "resolve_entity":
                text = params.get("entity_text", "")
                if "machine learning" in text.lower():
                    return {"status": "ok", "resolved_id": "C-AI321", "name": "ML"}
                return {"status": "not_found"}
            if op == "estimate_alignment_improvement":
                return self._KG_RESULT
            return {}

        kg.call.side_effect = _side_effect
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=_make_student(completed=["C-CS301"]))
        sqs = [_sq(
            "estimate_alignment_improvement",
            entities={"role_id": "RL_Data_Scientist"},
            params={"planned_courses": [
                "Introduction to Machine Learning",
                "FakeCourse999",
            ]},
        )]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status in ("success", "informational")
        # KG call must have received only the valid code
        kg_call_args = [
            c for c in kg.call.call_args_list
            if c[0][0] == "estimate_alignment_improvement"
        ]
        assert len(kg_call_args) == 1
        planned_sent = kg_call_args[0][0][1]["planned_courses"]
        assert "C-AI321" in planned_sent
        assert "FakeCourse999" not in planned_sent
        # Unresolved name preserved in result
        assert "FakeCourse999" in (r.data.get("unresolved_planned_names") or [])

    def test_D_no_valid_planned_courses_returns_clarification(self):
        """All planned courses unresolvable → clarification (no KG call for estimate)."""
        kg = MagicMock()

        def _side_effect(op, params, **kwargs):
            if op == "resolve_entity":
                return {"status": "not_found"}
            return {}

        kg.call.side_effect = _side_effect
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=_make_student(completed=["C-CS301"]))
        sqs = [_sq(
            "estimate_alignment_improvement",
            entities={"role_id": "RL_Data_Scientist"},
            params={"planned_courses": ["FakeCourse999", "AnotherFake"]},
        )]
        result = orch.execute_turn(sqs, session, _make_bundles())
        r = result.results[0]
        assert r.status == "clarification_needed"
        # estimate_alignment_improvement KG call must NOT have been made
        assert not any(
            c[0][0] == "estimate_alignment_improvement"
            for c in kg.call.call_args_list
        )


# ── T23: get_focus_courses_for_target personalized wording ───────────────────

class TestFocusCoursesPersonalizedWording:
    """get_focus_courses_for_target passes completed_courses when query is personalized."""

    def test_D_future_wording_passes_completed_courses(self):
        """'future' keyword in original_text triggers personalized → completed passed to KG."""
        kg = MagicMock()
        kg.call.return_value = {
            "focus_courses": [],
            "personalized_focus": True,
        }
        orch = _make_orchestrator(kg=kg)
        student = _make_student(completed=["C-CS301", "C-AI311"])
        session = _make_session(student=student)
        sqs = [_sq(
            "get_focus_courses_for_target",
            original_text="What future courses should I focus on for ML Engineer?",
            entities={"role_id": "RL_ML_Engineer"},
            student_referential_fallback=False,  # QU didn't set this
        )]
        orch.execute_turn(sqs, session, _make_bundles())
        call_args = kg.call.call_args
        assert "C-CS301" in call_args[0][1]["completed_courses"]
        assert "C-AI311" in call_args[0][1]["completed_courses"]

    def test_generic_wording_passes_empty_completed(self):
        """Generic query passes no completed courses so KG returns all focus courses."""
        kg = MagicMock()
        kg.call.return_value = {"focus_courses": []}
        orch = _make_orchestrator(kg=kg)
        student = _make_student(completed=["C-CS301", "C-AI311"])
        session = _make_session(student=student)
        sqs = [_sq(
            "get_focus_courses_for_target",
            original_text="What are the important courses for Data Scientist?",
            entities={"role_id": "RL_Data_Scientist"},
            student_referential_fallback=False,
        )]
        orch.execute_turn(sqs, session, _make_bundles())
        call_args = kg.call.call_args
        assert call_args[0][1]["completed_courses"] == []

    def test_referential_flag_passes_completed_courses(self):
        """student_referential_fallback=True still passes completed courses (regression)."""
        kg = MagicMock()
        kg.call.return_value = {"focus_courses": []}
        orch = _make_orchestrator(kg=kg)
        student = _make_student(completed=["C-CS301"])
        session = _make_session(student=student)
        sqs = [_sq(
            "get_focus_courses_for_target",
            entities={"role_id": "RL_Data_Scientist"},
            student_referential_fallback=True,
        )]
        orch.execute_turn(sqs, session, _make_bundles())
        call_args = kg.call.call_args
        assert "C-CS301" in call_args[0][1]["completed_courses"]
