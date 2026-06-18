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


# ── T01: Resolver fix — resolved_id key ──────────────────────────────────────

class TestResolverFix:

    def test_resolver_resolved_id_key_used(self):
        """Proves the QU resolver reads 'resolved_id' correctly (not just 'id')."""
        from gateway.query_understanding import _resolve_entity

        def resolver_with_resolved_id(entity_type, entity_text):
            return {
                "status": "ok",
                "resolved_id": "data_scientist",
                "name": "Data Scientist",
            }

        resolved, failure = _resolve_entity("role", "data scientist", resolver_with_resolved_id)
        assert failure is None
        assert resolved == "data_scientist"

    def test_resolver_id_key_still_works(self):
        """Old 'id' key fallback still works for backwards compatibility."""
        from gateway.query_understanding import _resolve_entity

        def old_resolver(entity_type, entity_text):
            return {"status": "ok", "id": "data_scientist"}

        resolved, failure = _resolve_entity("role", "data scientist", old_resolver)
        assert failure is None
        assert resolved == "data_scientist"


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
