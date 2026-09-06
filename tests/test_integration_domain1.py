"""
Integration tests — Domain 1 cross-cutting fixes (Phase 2, batch 3).

Tests cover behavioral issues identified during Phase 2 D1 integration testing:
  1. Subset references patch to course_status_check with course_codes (not full list)
  2. Ordinal references patch clarification_needed to check_course_eligibility
  3. Mixed ordered items (course + role) still ask clarification — no bad resolution
  4. Reset assumptions forces record_focus="reset_assumptions" (no full_record enrichment)
  5. Display enrichment finalizer adds course_display_labels to student-record results
  6. response_style="only" for cgpa restricts packet to just the CGPA field
  7. List-incompatible response_style is repaired in QU normalizer

All mocks; no live KG/ALE/RAG required.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from gateway.models.schemas import (
    AmbiguityMeta, AnswerMemory, DisplayItem,
    EntitySet, LastReferenced, PerSQResult, SessionOverrides, SessionState,
    StudentContext, StructuredQuery, TurnMemory,
)
from gateway.orchestrator import Orchestrator, _TurnCaches
from gateway.qu_preprocessing import (
    PreprocessResult,
    detect_subset_reference,
    is_list_incompatible_style,
)
from gateway.query_understanding import (
    _normalize_one_sq,
    _patch_for_turn_memory_impl,
    _maybe_fast_path_ordinal,
)
from gateway.response_composer import (
    _deterministic_answer,
    _extract_packet,
    _extract_student_record,
)
from gateway.turn_memory_builder import _extract_student_record as _tmb_extract_student_record


# ── Shared helpers ────────────────────────────────────────────────────────────

def _sq(intent: str, params: dict | None = None, entities: dict | None = None,
        override_action: str = "accumulate") -> StructuredQuery:
    return StructuredQuery(
        intent=intent,
        original_text=intent,
        entities=EntitySet(**(entities or {})),
        params=params or {},
        session_overrides=SessionOverrides(override_action=override_action),
        student_referential_fallback=True,
    )


def _course_item(code: str, rank: int = 0) -> DisplayItem:
    return DisplayItem(
        type="course", code=code, source_intent="get_student_record",
        source_field="completed_course_details", rank=rank,
    )


def _role_item(role_id: str, name: str, rank: int = 0) -> DisplayItem:
    return DisplayItem(
        type="role", code=role_id, name=name,
        source_intent="get_role_profile", rank=rank,
    )


def _tm(ordered_items: list[DisplayItem], courses: list[str] | None = None,
        ambiguous: bool = False) -> TurnMemory:
    am = AnswerMemory(
        courses=courses or [i.code for i in ordered_items if i.type == "course" and i.code],
        ordered_display_items=ordered_items,
    )
    meta = AmbiguityMeta(has_multiple_reference_groups=ambiguous)
    return TurnMemory(
        source_intents=["get_student_record"],
        primary_domain="student_record",
        answer_memory=am,
        ambiguity=meta,
    )


def _make_student(completed=None, in_progress=None, failed=None) -> StudentContext:
    return StudentContext(
        student_id="STU000411",
        name="Test",
        program="Computer Information Systems",
        track_id="SWE",
        level=3,
        first_semester="Spring 2024",
        study_status="Studying",
        cgpa=2.41,
        cumulative_chs=80,
        cumulative_cps=192.8,
        total_credit_hours_earned=80,
        completed_courses=completed or ["C-CS111", "C-CS112", "C-CS221"],
        failed_courses=failed or [],
        in_progress_courses=in_progress or ["C-CS315"],
        current_semester="Spring 2026",
    )


def _make_session(student=None) -> SessionState:
    return SessionState(
        session_id=str(uuid.uuid4()),
        student_id="STU000411",
        session_name="domain1-test",
        student_context=student or _make_student(),
        overrides=SessionOverrides(),
    )


def _make_bundles() -> dict:
    return {
        "grading_scale_rules": MagicMock(),
        "graduation_requirement_rules": MagicMock(),
        "academic_warning_rules": MagicMock(cgpa_warning_threshold=2.0),
        "honors_rules": MagicMock(),
        "credit_limit_rules": MagicMock(),
        "retake_rules": MagicMock(),
        "summer_semester_rules": MagicMock(),
        "student_level_rules": MagicMock(),
    }


def _make_kg(catalogue: dict) -> MagicMock:
    kg = MagicMock()

    def _call(op, params=None):
        params = params or {}
        if op == "get_course_profile":
            code = params.get("course_code", "")
            if code in catalogue:
                return {"course_code": code, **catalogue[code]}
            return {"error": "course_not_found"}
        return {}

    kg.call.side_effect = _call
    return kg


_CATALOGUE = {
    "C-CS111": {"name": "Introduction to CS",      "credits": 3},
    "C-CS112": {"name": "Programming Fundamentals", "credits": 3},
    "C-CS221": {"name": "Data Structures",          "credits": 3},
    "C-CS315": {"name": "Operating Systems",        "credits": 3},
    "C-CS319": {"name": "Software Engineering I",   "credits": 3},
    "C-CS320": {"name": "Software Engineering II",  "credits": 3},
}


# ── Test 1: Subset reference → course_status_check with course_codes ──────────

class TestSubsetReferencePatching:

    _items = [
        _course_item("C-CS319", rank=0),
        _course_item("C-CS320", rank=1),
        _course_item("C-CS221", rank=2),
    ]
    _memory = _tm(_items, courses=["C-CS319", "C-CS320", "C-CS221"])

    def _patch(self, text: str, starting_intent: str = "get_student_record",
               focus: str = "completed_courses") -> list[StructuredQuery]:
        sqs = [_sq(starting_intent, params={"record_focus": focus})]
        return _patch_for_turn_memory_impl(sqs, text, self._memory, "test")

    def test_subset_detected_in_query(self):
        assert detect_subset_reference("Which of these did I already complete?")

    def test_patches_to_course_status_check(self):
        result = self._patch("Which of these did I already complete before?")
        assert result[0].intent == "get_student_record"
        assert result[0].params["record_focus"] == "course_status_check"

    def test_injects_all_three_course_codes_from_memory(self):
        result = self._patch("Which of these did I already complete before?")
        codes = result[0].params.get("course_codes") or []
        assert set(codes) == {"C-CS319", "C-CS320", "C-CS221"}

    def test_injects_status_filter_completed(self):
        result = self._patch("Which of these did I already complete before?")
        assert result[0].params.get("status_filter") == "completed"

    def test_course_codes_limited_to_referenced_subset_not_full_history(self):
        result = self._patch("Which of these did I already complete before?")
        codes = result[0].params.get("course_codes") or []
        assert len(codes) == 3

    def test_patches_clarification_needed_to_course_status_check(self):
        sqs = [_sq("clarification_needed")]
        result = _patch_for_turn_memory_impl(
            sqs, "Did I complete these?", self._memory, "test"
        )
        assert result[0].intent == "get_student_record"
        assert result[0].params["record_focus"] == "course_status_check"

    def test_failed_subset_filter(self):
        sqs = [_sq("get_student_record", params={"record_focus": "failed_courses"})]
        result = _patch_for_turn_memory_impl(
            sqs, "Which of these did I fail?", self._memory, "test"
        )
        assert result[0].params.get("status_filter") == "failed"


# ── Test 2: Ordinal reference → check_course_eligibility ─────────────────────

class TestOrdinalReferencePatching:

    _items = [
        _course_item("C-CS319", rank=0),
        _course_item("C-CS320", rank=1),
        _course_item("C-CS221", rank=2),
    ]
    _memory = _tm(_items, courses=["C-CS319", "C-CS320", "C-CS221"])

    def _patch(self, text: str, starting_intent: str = "clarification_needed") -> list[StructuredQuery]:
        sqs = [_sq(starting_intent)]
        return _patch_for_turn_memory_impl(sqs, text, self._memory, "test")

    def test_first_one_resolves_to_first_course(self):
        result = self._patch("Can I take the first one again later?")
        assert result[0].intent == "check_course_eligibility"
        assert result[0].entities.course_code == "C-CS319"

    def test_second_one_resolves_to_second_course(self):
        result = self._patch("Can I take the second one?")
        assert result[0].intent == "check_course_eligibility"
        assert result[0].entities.course_code == "C-CS320"

    def test_third_one_resolves_to_third_course(self):
        result = self._patch("What about the third one?")
        assert result[0].intent == "check_course_eligibility"
        assert result[0].entities.course_code == "C-CS221"

    def test_source_reference_annotation_present(self):
        result = self._patch("Can I take the first one again later?")
        assert "source_reference" in result[0].params

    def test_eligibility_intent_with_missing_entity_gets_patched(self):
        sqs = [_sq("check_course_eligibility")]  # no course entity
        result = _patch_for_turn_memory_impl(
            sqs, "Am I eligible for the second one?", self._memory, "test"
        )
        assert result[0].entities.course_code == "C-CS320"


# ── Test 3: Mixed ordered items still ask clarification ──────────────────────

class TestMixedOrderedItemsNoResolution:

    _mixed_items = [
        _course_item("C-CS319", rank=0),
        _role_item("RL_Software_Engineer", "Software Engineer", rank=1),
        _course_item("C-CS221", rank=2),
    ]
    _memory_mixed = _tm(_mixed_items, ambiguous=True)

    def test_mixed_types_ordinal_not_resolved(self):
        sqs = [_sq("clarification_needed")]
        result = _patch_for_turn_memory_impl(
            sqs, "Can I take the first one?", self._memory_mixed, "test"
        )
        # Mixed type groups: clarification_needed should be preserved
        assert result[0].intent == "clarification_needed"

    def test_all_courses_same_type_still_resolves(self):
        items_homogeneous = [
            _course_item("C-CS319", rank=0),
            _course_item("C-CS320", rank=1),
        ]
        memory_homo = _tm(items_homogeneous)
        sqs = [_sq("clarification_needed")]
        result = _patch_for_turn_memory_impl(
            sqs, "Can I take the first one?", memory_homo, "test"
        )
        assert result[0].intent == "check_course_eligibility"
        assert result[0].entities.course_code == "C-CS319"

    def test_empty_memory_no_patch(self):
        memory_empty = _tm([])
        sqs = [_sq("clarification_needed")]
        result = _patch_for_turn_memory_impl(
            sqs, "Can I take the first one?", memory_empty, "test"
        )
        assert result[0].intent == "clarification_needed"


# ── Test 4: Reset assumptions forces record_focus (no full_record enrichment) ─

class TestResetAssumptionsGuard:

    def _run_with_clear(self, initial_focus: str = "full_record") -> PerSQResult:
        kg = _make_kg(_CATALOGUE)
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        session = _make_session()
        sq = StructuredQuery(
            intent="get_student_record",
            original_text="clear assumptions",
            entities=EntitySet(),
            params={"record_focus": initial_focus},
            session_overrides=SessionOverrides(override_action="clear"),
            student_referential_fallback=True,
        )
        wrapper = orch.execute_turn([sq], session, _make_bundles())
        return wrapper.results[0]

    def test_had_clear_forces_reset_assumptions_focus(self):
        r = self._run_with_clear()
        assert r.data["record_focus"] == "reset_assumptions"

    def test_had_clear_overrides_full_record_focus(self):
        r = self._run_with_clear(initial_focus="full_record")
        assert r.data["record_focus"] == "reset_assumptions"

    def test_had_clear_overrides_cgpa_focus(self):
        r = self._run_with_clear(initial_focus="cgpa")
        assert r.data["record_focus"] == "reset_assumptions"

    def test_assumptions_cleared_flag_set(self):
        r = self._run_with_clear()
        assert r.data.get("assumptions_cleared") is True

    def test_no_course_enrichment_on_reset(self):
        r = self._run_with_clear()
        # reset_assumptions is in _D6_NO_ENRICH_FOCUSES → empty detail lists
        assert r.data.get("completed_course_details") == []
        assert r.data.get("in_progress_course_details") == []
        assert r.data.get("failed_course_details") == []

    def test_result_is_success_status(self):
        r = self._run_with_clear()
        assert r.status == "success"


# ── Test 5: Display enrichment finalizer adds course_display_labels ───────────

class TestDisplayEnrichmentFinalizer:

    def test_apply_display_enrichment_adds_labels_for_unenriched_codes(self):
        kg = _make_kg(_CATALOGUE)
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        caches = _TurnCaches()
        result = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "progress_summary",
                "in_progress_courses": ["C-CS315"],
                "completed_courses": ["C-CS111"],
                "failed_courses": [],
                "completed_course_details": [],
                "in_progress_course_details": [],
                "failed_course_details": [],
            },
        )
        enriched = orch._apply_display_enrichment([result], caches)
        labels = enriched[0].data.get("course_display_labels") or {}
        assert len(labels) >= 1
        for code, label in labels.items():
            assert code in label  # label contains the code

    def test_display_label_includes_course_name(self):
        kg = _make_kg(_CATALOGUE)
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        caches = _TurnCaches()
        result = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "progress_summary",
                "in_progress_courses": ["C-CS315"],
                "completed_courses": [],
                "failed_courses": [],
                "completed_course_details": [],
                "in_progress_course_details": [],
                "failed_course_details": [],
            },
        )
        enriched = orch._apply_display_enrichment([result], caches)
        labels = enriched[0].data.get("course_display_labels") or {}
        assert "C-CS315" in labels
        assert "Operating Systems" in labels["C-CS315"]

    def test_display_enrichment_skips_scalar_focus(self):
        kg = _make_kg(_CATALOGUE)
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        caches = _TurnCaches()
        result = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "cgpa",
                "cgpa": 2.41,
                "completed_courses": ["C-CS111"],
                "in_progress_courses": ["C-CS315"],
                "completed_course_details": [],
                "in_progress_course_details": [],
                "failed_course_details": [],
            },
        )
        enriched = orch._apply_display_enrichment([result], caches)
        assert "course_display_labels" not in (enriched[0].data or {})

    def test_display_enrichment_skips_reset_focus(self):
        kg = _make_kg(_CATALOGUE)
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        caches = _TurnCaches()
        result = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "reset_assumptions",
                "completed_courses": ["C-CS111"],
                "completed_course_details": [],
                "in_progress_course_details": [],
                "failed_course_details": [],
            },
        )
        enriched = orch._apply_display_enrichment([result], caches)
        assert "course_display_labels" not in (enriched[0].data or {})

    def test_display_enrichment_skips_already_detailed_codes(self):
        kg = _make_kg(_CATALOGUE)
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        caches = _TurnCaches()
        # C-CS315 is already in completed_course_details → should not appear in labels
        result = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "in_progress_courses",
                "in_progress_courses": ["C-CS315"],
                "completed_courses": [],
                "failed_courses": [],
                "completed_course_details": [],
                "in_progress_course_details": [
                    {"course_code": "C-CS315", "course_name": "Operating Systems", "credits": 3}
                ],
                "failed_course_details": [],
            },
        )
        enriched = orch._apply_display_enrichment([result], caches)
        labels = enriched[0].data.get("course_display_labels") or {}
        # C-CS315 is already detailed — no need to add a label for it
        assert "C-CS315" not in labels

    def test_display_enrichment_does_not_raise_on_kg_error(self):
        kg = MagicMock()
        kg.call.return_value = {"error": "kg_unavailable"}
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        caches = _TurnCaches()
        result = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "progress_summary",
                "in_progress_courses": ["C-CS315"],
                "completed_courses": [],
                "failed_courses": [],
                "completed_course_details": [],
                "in_progress_course_details": [],
                "failed_course_details": [],
            },
        )
        enriched = orch._apply_display_enrichment([result], caches)
        # Should not crash; label may say "not available"
        assert enriched[0] is not None


# ── Test 6: response_style is a tone hint — packet always contains all fields ──

class TestResponseStyleOnly:
    """After Issue 4 fix: response_style no longer strips fields from the packet.
    The packet always contains all available fields; deterministic narration handles
    focus-specific rendering via the record_focus dispatcher.
    """

    _full_data: dict = {
        "record_focus": "cgpa",
        "response_style": "only",
        "cgpa": 2.41,
        "last_semester_gpa": 3.1,
        "academic_standing": "good",
        "consecutive_warnings": 0,
        "total_warnings": 0,
        "track_id": "SWE",
        "level": 3,
        "level_display": "Junior",
        "completed_courses": ["C-CS111"],
        "in_progress_courses": ["C-CS315"],
        "completed_course_details": [],
        "in_progress_course_details": [],
        "failed_course_details": [],
    }

    def _packet_for(self, data: dict) -> dict:
        packet: dict = {"intent": "get_student_record", "status": "success"}
        _extract_student_record(packet, data)
        return packet

    def test_only_style_includes_cgpa(self):
        p = self._packet_for(self._full_data)
        assert p["cgpa"] == 2.41

    def test_only_style_response_style_hint_present(self):
        # response_style is kept in packet as a hint, not used to strip fields
        p = self._packet_for(self._full_data)
        assert p.get("response_style") == "only"

    def test_only_style_includes_all_fields(self):
        # After fix: all fields are included regardless of response_style
        p = self._packet_for(self._full_data)
        assert "last_semester_gpa" in p
        assert "academic_standing" in p

    def test_only_style_includes_course_lists(self):
        # Packet includes the course lists; focus narration handles display
        p = self._packet_for(self._full_data)
        assert "completed_courses" in p

    def test_only_style_includes_level_and_track(self):
        p = self._packet_for(self._full_data)
        assert "level" in p
        assert "track_id" in p

    def test_normal_style_includes_all_fields(self):
        data = {**self._full_data, "response_style": "normal"}
        p = self._packet_for(data)
        assert "last_semester_gpa" in p
        assert "academic_standing" in p
        assert "completed_courses" in p

    def test_deterministic_answer_for_cgpa_only_is_short(self):
        # Deterministic fallback for record_focus=cgpa returns early after one sentence
        packet = {
            "intent": "get_student_record",
            "status": "success",
            "record_focus": "cgpa",
            "response_style": "only",
            "cgpa": 2.41,
        }
        answer = _deterministic_answer([packet])
        assert "2.41" in answer
        # The cgpa focus dispatcher returns early: at most 1-2 lines
        lines = [ln for ln in answer.split("\n") if ln.strip()]
        assert len(lines) <= 2


# ── Test 7: List-incompatible response_style repaired in QU normalizer ────────

class TestListIncompatibleStyleRepair:

    _pre = PreprocessResult()
    _last_ref = LastReferenced()

    def _make_sq(self, text: str, focus: str, style: str) -> StructuredQuery:
        return StructuredQuery(
            intent="get_student_record",
            original_text=text,
            entities=EntitySet(),
            params={"record_focus": focus, "response_style": style},
            session_overrides=SessionOverrides(),
            student_referential_fallback=True,
        )

    # is_list_incompatible_style helper

    def test_incompatible_true_for_failed_courses(self):
        assert is_list_incompatible_style("show me all courses I failed", "failed_courses")

    def test_incompatible_true_for_completed_courses(self):
        assert is_list_incompatible_style("list all my completed courses", "completed_courses")

    def test_incompatible_true_for_in_progress_courses(self):
        assert is_list_incompatible_style("show me all my in-progress courses", "in_progress_courses")

    def test_incompatible_false_for_scalar_cgpa(self):
        assert not is_list_incompatible_style("what is my cgpa", "cgpa")

    def test_incompatible_false_for_course_status_check(self):
        assert not is_list_incompatible_style("did I fail C-CS221?", "course_status_check")

    # Normalizer repair

    def test_normalizer_strips_one_sentence_from_failed_courses(self):
        sq = self._make_sq("show me all courses I failed", "failed_courses", "one_sentence")
        result = _normalize_one_sq(sq, "show me all courses I failed", self._pre, self._last_ref)
        assert result.params.get("response_style") != "one_sentence"

    def test_normalizer_strips_one_sentence_from_completed_courses(self):
        sq = self._make_sq("list all completed courses", "completed_courses", "one_sentence")
        result = _normalize_one_sq(sq, "list all completed courses", self._pre, self._last_ref)
        assert result.params.get("response_style") != "one_sentence"

    def test_normalizer_keeps_only_style_on_scalar_focus(self):
        sq = self._make_sq("what is my cgpa only", "cgpa", "only")
        result = _normalize_one_sq(sq, "what is my cgpa only", self._pre, self._last_ref)
        assert result.params.get("response_style") == "only"

    def test_normalizer_keeps_normal_style_unchanged(self):
        sq = self._make_sq("show me all courses I failed", "failed_courses", "normal")
        result = _normalize_one_sq(sq, "show me all courses I failed", self._pre, self._last_ref)
        assert result.params.get("response_style", "normal") in ("normal", "yes_no", "")


# ── Class A: TurnMemory focus-aware extraction ────────────────────────────────

class TestTurnMemoryFocusAware:
    """Issue 1: _extract_student_record in turn_memory_builder should be focus-aware."""

    def _make_result(self, data: dict) -> PerSQResult:
        return PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data=data,
        )

    def test_in_progress_focus_only_stores_in_progress(self):
        """Given focus=in_progress_courses with codes in all 3 lists, only in_progress items stored."""
        data = {
            "record_focus": "in_progress_courses",
            "in_progress_courses": ["C-CS315", "C-CS221", "C-CS319"],
            "in_progress_course_details": [
                {"course_code": "C-CS315", "course_name": "Operating Systems", "credits": 3},
                {"course_code": "C-CS221", "course_name": "Data Structures", "credits": 3},
                {"course_code": "C-CS319", "course_name": "Software Engineering I", "credits": 3},
            ],
            "completed_courses": ["C-CS111", "C-CS112", "C-CS113", "C-CS114", "C-CS115"],
            "completed_course_details": [
                {"course_code": f"C-CS11{i}", "course_name": f"Course {i}", "credits": 3}
                for i in range(5)
            ],
            "failed_courses": ["C-CS200", "C-CS201", "C-CS202"],
            "failed_course_details": [
                {"course_code": f"C-CS20{i}", "course_name": f"Failed {i}", "credits": 3}
                for i in range(3)
            ],
        }
        result = self._make_result(data)
        _, display_items = _tmb_extract_student_record(result)

        codes = {di.code for di in display_items}
        # Only in_progress codes should appear
        assert "C-CS315" in codes
        assert "C-CS221" in codes
        assert "C-CS319" in codes
        # No completed or failed codes
        for code in ["C-CS111", "C-CS112", "C-CS113", "C-CS114", "C-CS115"]:
            assert code not in codes
        for code in ["C-CS200", "C-CS201", "C-CS202"]:
            assert code not in codes

    def test_completed_focus_only_stores_completed(self):
        """Given focus=completed_courses, only completed items appear in display items."""
        data = {
            "record_focus": "completed_courses",
            "completed_courses": ["C-CS111", "C-CS112"],
            "completed_course_details": [
                {"course_code": "C-CS111", "course_name": "Intro CS", "credits": 3},
                {"course_code": "C-CS112", "course_name": "Programming", "credits": 3},
            ],
            "in_progress_courses": ["C-CS315"],
            "in_progress_course_details": [
                {"course_code": "C-CS315", "course_name": "OS", "credits": 3},
            ],
            "failed_courses": ["C-CS200"],
            "failed_course_details": [
                {"course_code": "C-CS200", "course_name": "Failed Course", "credits": 3},
            ],
        }
        result = self._make_result(data)
        _, display_items = _tmb_extract_student_record(result)

        codes = {di.code for di in display_items}
        assert "C-CS111" in codes
        assert "C-CS112" in codes
        assert "C-CS315" not in codes
        assert "C-CS200" not in codes

    def test_failed_focus_only_stores_failed(self):
        """Given focus=failed_courses, only failed items appear in display items."""
        data = {
            "record_focus": "failed_courses",
            "failed_courses": ["C-CS200", "C-CS201"],
            "failed_course_details": [
                {"course_code": "C-CS200", "course_name": "Failed 1", "credits": 3},
                {"course_code": "C-CS201", "course_name": "Failed 2", "credits": 3},
            ],
            "completed_courses": ["C-CS111"],
            "completed_course_details": [
                {"course_code": "C-CS111", "course_name": "Intro CS", "credits": 3},
            ],
            "in_progress_courses": ["C-CS315"],
            "in_progress_course_details": [],
        }
        result = self._make_result(data)
        _, display_items = _tmb_extract_student_record(result)

        codes = {di.code for di in display_items}
        assert "C-CS200" in codes
        assert "C-CS201" in codes
        assert "C-CS111" not in codes
        assert "C-CS315" not in codes

    def test_scalar_focus_stores_no_courses(self):
        """Given cgpa focus, display items is empty — no course pollution."""
        data = {
            "record_focus": "cgpa",
            "cgpa": 2.41,
            "completed_courses": ["C-CS111", "C-CS112"],
            "completed_course_details": [
                {"course_code": "C-CS111", "course_name": "Intro CS", "credits": 3},
            ],
            "in_progress_courses": ["C-CS315"],
            "in_progress_course_details": [],
            "failed_courses": [],
            "failed_course_details": [],
        }
        result = self._make_result(data)
        _, display_items = _tmb_extract_student_record(result)

        assert display_items == [], f"Expected empty display items for scalar focus, got: {display_items}"


# ── Class B: Display enrichment focus-aware ───────────────────────────────────

class TestDisplayEnrichmentFocusAware:
    """Issue 2+3: Orchestrator display enrichment should be focus-aware."""

    def test_in_progress_enrichment_skips_completed_raw(self):
        """For in_progress_courses focus, enrichment should NOT call KG for completed codes."""
        kg = _make_kg(_CATALOGUE)
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        caches = _TurnCaches()
        result = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "in_progress_courses",
                "in_progress_courses": ["C-CS315"],
                "completed_courses": ["C-CS111", "C-CS112"],
                "failed_courses": [],
                "completed_course_details": [],
                "in_progress_course_details": [],
                "failed_course_details": [],
            },
        )
        enriched = orch._apply_display_enrichment([result], caches)
        labels = enriched[0].data.get("course_display_labels") or {}
        # Only in_progress code should be enriched, not completed codes
        assert "C-CS315" in labels
        assert "C-CS111" not in labels
        assert "C-CS112" not in labels

    def test_failed_enrichment_skips_other_raw(self):
        """For failed_courses focus, enrichment should NOT label in_progress/completed raw codes."""
        kg = _make_kg(_CATALOGUE)
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        caches = _TurnCaches()
        result = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "failed_courses",
                "in_progress_courses": ["C-CS315"],
                "completed_courses": ["C-CS111"],
                "failed_courses": ["C-CS319"],
                "completed_course_details": [],
                "in_progress_course_details": [],
                "failed_course_details": [],
            },
        )
        enriched = orch._apply_display_enrichment([result], caches)
        labels = enriched[0].data.get("course_display_labels") or {}
        # Only failed code should be enriched
        assert "C-CS319" in labels
        assert "C-CS315" not in labels
        assert "C-CS111" not in labels

    def test_entity_resolved_status_check_checks_all_statuses(self):
        """When entities.course_code is set for course_status_check,
        all 3 status lists are checked (status_filter is ignored)."""
        student = _make_student(
            completed=["C-CS319"],
            in_progress=["C-CS315"],
            failed=[],
        )
        kg = _make_kg(_CATALOGUE)
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        session = _make_session(student=student)
        # Entity-resolved: entities.course_code set, status_filter="in_progress" (should be ignored)
        sq = StructuredQuery(
            intent="get_student_record",
            original_text="Am I currently taking C-CS319?",
            entities=EntitySet(course_code="C-CS319"),
            params={"record_focus": "course_status_check", "status_filter": "in_progress"},
            session_overrides=SessionOverrides(),
            student_referential_fallback=True,
        )
        wrapper = orch.execute_turn([sq], session, _make_bundles())
        r = wrapper.results[0]
        # C-CS319 is in completed; status_filter="in_progress" should be overridden
        # completed_course_details should contain C-CS319
        completed = r.data.get("completed_course_details") or []
        completed_codes = [d.get("course_code") for d in completed]
        assert "C-CS319" in completed_codes, (
            "Entity-resolved course_status_check should check all statuses, "
            f"got completed_details={completed}"
        )
        # Also verify snapshot_status_filter was cleared
        assert r.data.get("status_filter") is None

    def test_memory_subset_status_check_respects_filter(self):
        """When only params.course_codes (no entities.course_code), status_filter is respected."""
        student = _make_student(
            completed=["C-CS319"],
            in_progress=["C-CS315"],
            failed=[],
        )
        kg = _make_kg(_CATALOGUE)
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        session = _make_session(student=student)
        # Memory-subset: no entities.course_code, course_codes from params
        sq = StructuredQuery(
            intent="get_student_record",
            original_text="Which of these did I complete?",
            entities=EntitySet(),  # no entity-resolved code
            params={
                "record_focus": "course_status_check",
                "course_codes": ["C-CS319", "C-CS315"],
                "status_filter": "completed",
            },
            session_overrides=SessionOverrides(),
            student_referential_fallback=True,
        )
        wrapper = orch.execute_turn([sq], session, _make_bundles())
        r = wrapper.results[0]
        # Only completed courses should be enriched (status_filter respected)
        completed = r.data.get("completed_course_details") or []
        in_progress = r.data.get("in_progress_course_details") or []
        completed_codes = [d.get("course_code") for d in completed]
        in_progress_codes = [d.get("course_code") for d in in_progress]
        assert "C-CS319" in completed_codes
        assert "C-CS315" not in in_progress_codes
        # status_filter preserved in snapshot
        assert r.data.get("status_filter") == "completed"


# ── Class C: Composer flexibility ─────────────────────────────────────────────

class TestComposerFlexibility:
    """Issues 4+5+6: Composer packet and narration improvements."""

    def test_cgpa_narration_natural_sentence(self):
        """Deterministic fallback for cgpa focus produces a natural sentence, not a raw value."""
        packet = {
            "intent": "get_student_record",
            "status": "success",
            "record_focus": "cgpa",
            "response_style": "only",
            "cgpa": 1.91,
        }
        answer = _deterministic_answer([packet])
        # Should be a natural sentence containing the CGPA
        assert "1.91" in answer
        # Should NOT be just the raw number
        assert len(answer.strip()) > len("1.91")

    def test_failed_courses_narration_includes_list(self):
        """Deterministic fallback for failed_courses focus includes the course list."""
        packet = {
            "intent": "get_student_record",
            "status": "success",
            "record_focus": "failed_courses",
            "response_style": "normal",
            "failed_course_details": [
                {"course_code": "C-CS200", "course_name": "Failed Course A", "credits": 3},
                {"course_code": "C-CS201", "course_name": "Failed Course B", "credits": 3},
            ],
            "failed_courses": ["C-CS200", "C-CS201"],
            "in_progress_courses": [],
            "completed_courses": [],
        }
        answer = _deterministic_answer([packet])
        # Should include course codes or names
        assert "C-CS200" in answer or "Failed Course A" in answer
        assert "C-CS201" in answer or "Failed Course B" in answer

    def test_no_fabricated_names_in_narration(self):
        """When course details lack course_name, narration uses the code alone."""
        packet = {
            "intent": "get_student_record",
            "status": "success",
            "record_focus": "in_progress_courses",
            "response_style": "normal",
            "in_progress_course_details": [
                {"course_code": "C-AI321", "course_name": None, "credits": 3},
            ],
            "in_progress_courses": ["C-AI321"],
            "completed_courses": [],
            "failed_courses": [],
        }
        answer = _deterministic_answer([packet])
        # Code should appear in the answer
        assert "C-AI321" in answer
        # Should NOT contain fabricated names derived from the code
        assert "Artificial Intelligence 321" not in answer
        assert "AI 321" not in answer

    def test_response_style_hint_in_packet(self):
        """response_style is present in packet as hint, not used to strip other fields."""
        data = {
            "record_focus": "cgpa",
            "response_style": "only",
            "cgpa": 2.41,
            "last_semester_gpa": 3.1,
            "academic_standing": "good",
            "completed_courses": ["C-CS111"],
            "in_progress_courses": ["C-CS315"],
            "completed_course_details": [],
            "in_progress_course_details": [],
            "failed_course_details": [],
        }
        packet: dict = {"intent": "get_student_record", "status": "success"}
        _extract_student_record(packet, data)
        # response_style is in the packet (as a hint)
        assert packet.get("response_style") == "only"
        # Other fields are NOT stripped
        assert "cgpa" in packet
        assert "last_semester_gpa" in packet
        assert "academic_standing" in packet


# ── Class D: Pre-LLM ordinal fast path ───────────────────────────────────────

class TestPreLLMOrdinalFastPath:
    """Issue 7: _maybe_fast_path_ordinal skips LLM for obvious ordinal+intent cases."""

    _items = [
        _course_item("C-CS319", rank=0),
        _course_item("C-CS320", rank=1),
    ]

    def _make_tm(self, items=None, ambiguous=False) -> TurnMemory:
        items = items if items is not None else self._items
        am = AnswerMemory(
            courses=[i.code for i in items if i.type == "course" and i.code],
            ordered_display_items=items,
        )
        meta = AmbiguityMeta(has_multiple_reference_groups=ambiguous)
        return TurnMemory(
            source_intents=["get_student_record"],
            primary_domain="student_record",
            answer_memory=am,
            ambiguity=meta,
        )

    def test_fast_path_eligibility(self):
        """_maybe_fast_path_ordinal returns check_course_eligibility SQ for 'can I take the first one?'"""
        tm = self._make_tm()
        result = _maybe_fast_path_ordinal("can I take the first one again later?", tm, "test")
        assert result is not None, "Expected a fast-path result, got None"
        assert len(result) == 1
        sq = result[0]
        assert sq.intent == "check_course_eligibility"
        assert sq.entities.course_code == "C-CS319"
        assert "source_reference" in sq.params

    def test_fast_path_none_for_ambiguous(self):
        """Returns None when TurnMemory has multiple types (courses + roles) — ambiguous."""
        mixed_items = [
            _course_item("C-CS319", rank=0),
            _role_item("RL_Software_Engineer", "Software Engineer", rank=1),
        ]
        tm_ambiguous = self._make_tm(items=mixed_items, ambiguous=True)
        result = _maybe_fast_path_ordinal("can I take the first one?", tm_ambiguous, "test")
        assert result is None, "Expected None for ambiguous TurnMemory with mixed types"

    def test_fast_path_none_for_no_memory(self):
        """Returns None when TurnMemory is None."""
        result = _maybe_fast_path_ordinal("can I take the first one?", None, "test")
        assert result is None

    def test_fast_path_none_for_unknown_intent_pattern(self):
        """Returns None when the query doesn't match any known intent pattern."""
        tm = self._make_tm()
        result = _maybe_fast_path_ordinal("what time does the first one meet?", tm, "test")
        assert result is None

    def test_fast_path_second_item(self):
        """Returns check_course_eligibility for 'am I eligible for the second one?'"""
        tm = self._make_tm()
        result = _maybe_fast_path_ordinal("am I eligible for the second one?", tm, "test")
        assert result is not None
        sq = result[0]
        assert sq.intent == "check_course_eligibility"
        assert sq.entities.course_code == "C-CS320"


# ── Class E: KG Metadata Cache ────────────────────────────────────────────────

class TestKGMetadataCache:
    """Tests for _KGMetadataCache: startup display cache loaded by KGAdapter."""

    def test_returns_course_metadata_when_loaded(self):
        from adapters.kg_adapter import _KGMetadataCache
        cache = _KGMetadataCache()
        cache.courses["C-AI321"] = {
            "type": "course", "id": "C-AI321",
            "name": "Introduction to Machine Learning", "credits": 4, "level": 3,
        }
        result = cache.get_course("C-AI321")
        assert result["name"] == "Introduction to Machine Learning"
        assert result["credits"] == 4
        assert result["id"] == "C-AI321"

    def test_graceful_fallback_for_missing_code(self):
        from adapters.kg_adapter import _KGMetadataCache
        cache = _KGMetadataCache()
        result = cache.get_course("C-GP411")
        assert result["id"] == "C-GP411"
        assert result["name"] is None

    def test_get_entity_dispatches_to_skill(self):
        from adapters.kg_adapter import _KGMetadataCache
        cache = _KGMetadataCache()
        cache.skills["SK_ML"] = {"type": "skill", "id": "SK_ML", "name": "Machine Learning", "category": "AI"}
        result = cache.get_entity("skill", "SK_ML")
        assert result["name"] == "Machine Learning"

    def test_get_entity_unknown_type_returns_graceful(self):
        from adapters.kg_adapter import _KGMetadataCache
        cache = _KGMetadataCache()
        result = cache.get_entity("unknown_type", "some_id")
        assert result["id"] == "some_id"
        assert result["name"] is None


# ── Class F: Course Status Check Metadata in Orchestrator Packet ──────────────

_META_LOOKUP = {
    "C-AI321": {"type": "course", "id": "C-AI321", "name": "Introduction to Machine Learning", "credits": 4, "level": 3},
    "C-CS496": {"type": "course", "id": "C-CS496", "name": "Selected Topics in Computer Science-2", "credits": 3, "level": 4},
    "C-DE324": {"type": "course", "id": "C-DE324", "name": "Big Data Engineering", "credits": 3, "level": 3},
    "C-CS435": {"type": "course", "id": "C-CS435", "name": "Computer Vision", "credits": 3, "level": 4},
}


def _make_kg_with_meta(catalogue: dict | None = None) -> MagicMock:
    """KG mock that returns real metadata from get_course_metadata and real profiles from call."""
    kg = _make_kg(catalogue or _CATALOGUE)
    kg.get_course_metadata.side_effect = lambda code: _META_LOOKUP.get(
        code, {"type": "course", "id": code, "name": None}
    )
    return kg


class TestCourseStatusCheckMetadata:
    """Orchestrator attaches checked_course_metadata and in_progress_course_metadata
    for course_status_check focus, enabling Composer to display real names."""

    def test_checked_course_metadata_in_multi_course_status_check(self):
        """Multi-course subset: checked_course_metadata has names for all checked codes."""
        student = _make_student(
            in_progress=["C-AI321", "C-CS496", "C-DE324"],
            completed=["C-CS111"],
            failed=[],
        )
        kg = _make_kg_with_meta()
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        session = _make_session(student=student)
        sq = StructuredQuery(
            intent="get_student_record",
            original_text="Which of these did I complete before?",
            entities=EntitySet(),
            params={
                "record_focus": "course_status_check",
                "course_codes": ["C-AI321", "C-CS496", "C-DE324"],
                "status_filter": "completed",
            },
            session_overrides=SessionOverrides(),
            student_referential_fallback=True,
        )
        wrapper = orch.execute_turn([sq], session, _make_bundles())
        data = wrapper.results[0].data or {}
        checked_meta = data.get("checked_course_metadata") or {}
        assert "C-AI321" in checked_meta, "Expected metadata for C-AI321"
        assert checked_meta["C-AI321"]["name"] == "Introduction to Machine Learning"
        assert "C-CS496" in checked_meta
        assert checked_meta["C-CS496"]["name"] == "Selected Topics in Computer Science-2"
        assert "C-DE324" in checked_meta
        assert checked_meta["C-DE324"]["name"] == "Big Data Engineering"

    def test_computer_vision_status_check_has_target_metadata(self):
        """Entity-resolved C-CS435: checked_course_metadata contains Computer Vision entry."""
        student = _make_student(
            in_progress=["C-AI321", "C-CS496"],
            completed=["C-CS435"],
            failed=[],
        )
        kg = _make_kg_with_meta()
        kg.call.side_effect = lambda op, params=None: (
            {"course_code": "C-CS435", "name": "Computer Vision", "credits": 3, "level": 4}
            if op == "get_course_profile" and (params or {}).get("course_code") == "C-CS435"
            else {}
        )
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        session = _make_session(student=student)
        sq = StructuredQuery(
            intent="get_student_record",
            original_text="Am I currently taking Computer Vision?",
            entities=EntitySet(course_code="C-CS435"),
            params={"record_focus": "course_status_check"},
            session_overrides=SessionOverrides(),
            student_referential_fallback=True,
        )
        wrapper = orch.execute_turn([sq], session, _make_bundles())
        data = wrapper.results[0].data or {}
        checked_meta = data.get("checked_course_metadata") or {}
        assert "C-CS435" in checked_meta
        assert checked_meta["C-CS435"]["name"] == "Computer Vision"

    def test_in_progress_course_metadata_excludes_completed(self):
        """in_progress_course_metadata only has in-progress codes — not completed codes."""
        student = _make_student(
            in_progress=["C-AI321"],
            completed=["C-CS111", "C-CS112"],
            failed=[],
        )
        kg = _make_kg_with_meta()
        kg.call.side_effect = lambda op, params=None: {}
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        session = _make_session(student=student)
        sq = StructuredQuery(
            intent="get_student_record",
            original_text="Am I taking any courses?",
            entities=EntitySet(),
            params={
                "record_focus": "course_status_check",
                "course_codes": ["C-AI321"],
                "status_filter": "in_progress",
            },
            session_overrides=SessionOverrides(),
            student_referential_fallback=True,
        )
        wrapper = orch.execute_turn([sq], session, _make_bundles())
        data = wrapper.results[0].data or {}
        in_p_meta = data.get("in_progress_course_metadata") or {}
        assert "C-AI321" in in_p_meta, "Expected in-progress code in metadata"
        assert "C-CS111" not in in_p_meta, "Completed code should not be in in_progress_course_metadata"
        assert "C-CS112" not in in_p_meta

    def test_no_metadata_for_unknown_code(self):
        """When KG returns name=None, code is omitted from checked_course_metadata."""
        student = _make_student(in_progress=[], completed=[], failed=[])
        kg = _make_kg({})
        kg.get_course_metadata.return_value = {"type": "course", "id": "C-UNKNOWN", "name": None}
        orch = Orchestrator(kg_adapter=kg, rag_adapter=MagicMock(), ale_adapter=MagicMock())
        session = _make_session(student=student)
        sq = StructuredQuery(
            intent="get_student_record",
            original_text="Did I take C-UNKNOWN?",
            entities=EntitySet(),
            params={
                "record_focus": "course_status_check",
                "course_codes": ["C-UNKNOWN"],
                "status_filter": "completed",
            },
            session_overrides=SessionOverrides(),
            student_referential_fallback=True,
        )
        wrapper = orch.execute_turn([sq], session, _make_bundles())
        data = wrapper.results[0].data or {}
        checked_meta = data.get("checked_course_metadata") or {}
        assert "C-UNKNOWN" not in checked_meta


# ── Class G: Composer Metadata Display ───────────────────────────────────────

class TestComposerMetadataDisplay:
    """Composer uses checked_course_metadata / in_progress_course_metadata for real names."""

    def test_extract_packet_passes_through_checked_course_metadata(self):
        """_extract_packet forwards checked_course_metadata to the narration layer."""
        result = PerSQResult(
            sq_index=0, intent="get_student_record", status="success",
            data={
                "record_focus": "course_status_check",
                "checked_course_codes": ["C-AI321"],
                "checked_course_metadata": {
                    "C-AI321": {"type": "course", "id": "C-AI321", "name": "Introduction to Machine Learning"},
                },
                "completed_course_details": [],
                "in_progress_course_details": [],
                "failed_course_details": [],
                "completed_courses": [],
                "in_progress_courses": [],
                "failed_courses": [],
            },
        )
        packet = _extract_packet(result)
        assert "checked_course_metadata" in packet
        assert packet["checked_course_metadata"]["C-AI321"]["name"] == "Introduction to Machine Learning"

    def test_cl_uses_checked_course_metadata_for_not_found_codes(self):
        """When checked codes not in completed, _cl shows metadata names not bare codes."""
        packet = {
            "intent": "get_student_record",
            "status": "success",
            "record_focus": "course_status_check",
            "response_style": "normal",
            "checked_course_codes": ["C-AI321", "C-CS496"],
            "status_filter": "completed",
            "checked_course_metadata": {
                "C-AI321": {"type": "course", "id": "C-AI321", "name": "Introduction to Machine Learning", "credits": 4},
                "C-CS496": {"type": "course", "id": "C-CS496", "name": "Selected Topics in Computer Science-2", "credits": 3},
            },
            "completed_course_details": [],
            "in_progress_course_details": [],
            "failed_course_details": [],
            "completed_courses": [],
            "in_progress_courses": ["C-AI321", "C-CS496"],
            "failed_courses": [],
        }
        answer = _deterministic_answer([packet])
        assert "Introduction to Machine Learning" in answer
        assert "Selected Topics in Computer Science-2" in answer
        # No fabricated names
        assert "Artificial Intelligence 321" not in answer
        assert "Advanced AI" not in answer

    def test_in_progress_fallback_uses_metadata_names(self):
        """When no checked_codes, in-progress fallback list uses in_progress_course_metadata names."""
        packet = {
            "intent": "get_student_record",
            "status": "success",
            "record_focus": "course_status_check",
            "response_style": "normal",
            # No checked_course_codes → fallback branch
            "in_progress_courses": ["C-AI321", "C-DE324"],
            "in_progress_course_metadata": {
                "C-AI321": {"type": "course", "id": "C-AI321", "name": "Introduction to Machine Learning", "credits": 4},
                "C-DE324": {"type": "course", "id": "C-DE324", "name": "Big Data Engineering", "credits": 3},
            },
            "completed_courses": [],
            "failed_courses": [],
            "completed_course_details": [],
            "in_progress_course_details": [],
            "failed_course_details": [],
        }
        answer = _deterministic_answer([packet])
        assert "Introduction to Machine Learning" in answer
        assert "Big Data Engineering" in answer
        # Codes still present in parenthetical form
        assert "C-AI321" in answer
        assert "C-DE324" in answer

    def test_composer_shows_code_when_metadata_absent(self):
        """When no metadata is available, Composer shows bare code without inventing a name."""
        packet = {
            "intent": "get_student_record",
            "status": "success",
            "record_focus": "course_status_check",
            "checked_course_codes": ["C-GP411"],
            "status_filter": "completed",
            # No checked_course_metadata → bare code fallback
            "completed_course_details": [],
            "in_progress_course_details": [],
            "failed_course_details": [],
            "completed_courses": [],
            "in_progress_courses": [],
            "failed_courses": [],
        }
        answer = _deterministic_answer([packet])
        assert "C-GP411" in answer
        # No invented names from code format
        assert "Graduation Project" not in answer
        assert "General Programming" not in answer
