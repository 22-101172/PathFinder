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
)
from gateway.response_composer import (
    _deterministic_answer,
    _extract_student_record,
)


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


# ── Test 6: response_style=only restricts packet to just the CGPA field ──────

class TestResponseStyleOnly:

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

    def test_only_style_excludes_last_semester_gpa(self):
        p = self._packet_for(self._full_data)
        assert "last_semester_gpa" not in p

    def test_only_style_excludes_academic_standing(self):
        p = self._packet_for(self._full_data)
        assert "academic_standing" not in p

    def test_only_style_excludes_course_lists(self):
        p = self._packet_for(self._full_data)
        assert "completed_courses" not in p
        assert "in_progress_courses" not in p

    def test_only_style_excludes_level_and_track(self):
        p = self._packet_for(self._full_data)
        assert "level" not in p
        assert "track_id" not in p

    def test_normal_style_includes_all_fields(self):
        data = {**self._full_data, "response_style": "normal"}
        p = self._packet_for(data)
        assert "last_semester_gpa" in p
        assert "academic_standing" in p
        assert "completed_courses" in p

    def test_deterministic_answer_for_cgpa_only_is_short(self):
        packet = {
            "intent": "get_student_record",
            "status": "success",
            "record_focus": "cgpa",
            "response_style": "only",
            "cgpa": 2.41,
        }
        answer = _deterministic_answer([packet])
        assert "2.41" in answer
        # No extra lines about standing, last-semester GPA, etc.
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
