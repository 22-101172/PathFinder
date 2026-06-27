"""
Phase 2 D6 regression tests — student record course enrichment.

Covers the behavioral defect where get_student_record returned raw course codes
only. After the fix, Orchestrator enriches course lists with KG names/credits
and Composer renders them as 'Course Name (COURSE_CODE)'.

D6-BUG-1: student-record course lists returned raw codes only.

All KG/ALE/RAG calls are mocked; no live Neo4j or Excel required.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, call

import pytest

from gateway.models.schemas import (
    CourseRecord, EntitySet, PerSQResult, SessionOverrides, SessionState,
    StudentContext, StructuredQuery, TurnWrapper,
)
from gateway.orchestrator import Orchestrator
from gateway.response_composer import (
    _deterministic_answer,
    _extract_packet,
    _render_course_detail,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

_STU000031_COMPLETED = ["HUM111", "C-PH111", "C-CS111", "C-MA111", "HUM126"]
_STU000031_IN_PROGRESS = ["C-CS112", "C-MA112", "C-PH112", "C-PH113", "HUM224"]
_STU000031_CGPA = 3.48

_COURSE_CATALOGUE = {
    "HUM111":  {"name": "Humanities I",                  "credits": 2},
    "C-PH111": {"name": "Physics I",                     "credits": 3},
    "C-CS111": {"name": "Introduction to Computer Science", "credits": 3},
    "C-MA111": {"name": "Calculus I",                    "credits": 3},
    "HUM126":  {"name": "Technical Writing",             "credits": 2},
    "C-CS112": {"name": "Programming Fundamentals",      "credits": 3},
    "C-MA112": {"name": "Calculus II",                   "credits": 3},
    "C-PH112": {"name": "Advanced Physics",              "credits": 3},
    "C-PH113": {"name": "Digital Logic Design",          "credits": 3},
    "HUM224":  {"name": "Introduction to Management",    "credits": 2},
}


def _make_kg(catalogue: dict = _COURSE_CATALOGUE) -> MagicMock:
    """Mock KG adapter that answers get_course_profile from a catalogue dict."""
    kg = MagicMock()

    def _kg_call(operation, params=None):
        params = params or {}
        if operation == "get_course_profile":
            code = params.get("course_code", "")
            if code in catalogue:
                return {"course_code": code, **catalogue[code]}
            return {"error": "course_not_found"}
        return {}

    kg.call.side_effect = _kg_call
    return kg


def _make_student(
    completed=None, in_progress=None, failed=None,
    cgpa=_STU000031_CGPA, track_id="SWE",
) -> StudentContext:
    return StudentContext(
        student_id="STU000031",
        name="Test Student",
        program="Software Engineering",
        track_id=track_id,
        level=2,
        first_semester="Spring 2025",
        study_status="Studying",
        cgpa=cgpa,
        cumulative_chs=13,
        cumulative_cps=45.24,
        total_credit_hours_earned=13,
        completed_courses=completed or _STU000031_COMPLETED,
        failed_courses=failed or [],
        in_progress_courses=in_progress or _STU000031_IN_PROGRESS,
        current_semester="Spring 2026",
    )


def _make_session(student=None) -> SessionState:
    return SessionState(
        session_id=str(uuid.uuid4()),
        student_id="STU000031",
        session_name="d6-test",
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


def _sq(intent: str, **kwargs) -> StructuredQuery:
    return StructuredQuery(
        intent=intent,
        original_text=kwargs.pop("original_text", intent),
        entities=EntitySet(**kwargs.pop("entities", {})),
        secondary_entities=None,
        params=kwargs.pop("params", {}),
        session_overrides=SessionOverrides(),
        student_referential_fallback=False,
    )


def _make_orchestrator(kg=None) -> Orchestrator:
    return Orchestrator(
        kg_adapter=kg or _make_kg(),
        rag_adapter=MagicMock(),
        ale_adapter=MagicMock(),
    )


# ── Orchestrator enrichment ───────────────────────────────────────────────────

class TestOrchestratorD6Enrichment:

    def _run(self, student=None, kg=None):
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=student)
        result = orch.execute_turn([_sq("get_student_record")], session, _make_bundles())
        return result.results[0]

    def test_status_is_success(self):
        r = self._run()
        assert r.status == "success"

    def test_completed_course_details_present(self):
        r = self._run()
        assert "completed_course_details" in r.data
        assert len(r.data["completed_course_details"]) == len(_STU000031_COMPLETED)

    def test_in_progress_course_details_present(self):
        r = self._run()
        assert "in_progress_course_details" in r.data
        assert len(r.data["in_progress_course_details"]) == len(_STU000031_IN_PROGRESS)

    def test_failed_course_details_present_when_empty(self):
        r = self._run()
        assert "failed_course_details" in r.data
        assert r.data["failed_course_details"] == []

    def test_completed_detail_has_name_and_credits(self):
        r = self._run()
        details = r.data["completed_course_details"]
        by_code = {d["course_code"]: d for d in details}
        assert by_code["HUM111"]["course_name"] == "Humanities I"
        assert by_code["HUM111"]["credits"] == 2
        assert by_code["C-CS111"]["course_name"] == "Introduction to Computer Science"

    def test_in_progress_detail_has_name_and_credits(self):
        r = self._run()
        details = r.data["in_progress_course_details"]
        by_code = {d["course_code"]: d for d in details}
        assert by_code["C-CS112"]["course_name"] == "Programming Fundamentals"
        assert by_code["C-CS112"]["credits"] == 3
        assert by_code["HUM224"]["course_name"] == "Introduction to Management"

    def test_raw_codes_still_present_for_backward_compat(self):
        r = self._run()
        assert set(_STU000031_COMPLETED) == set(r.data["completed_courses"])
        assert set(_STU000031_IN_PROGRESS) == set(r.data["in_progress_courses"])

    def test_no_pii_in_data(self):
        r = self._run()
        for pii in ("student_id", "name", "military_status", "cumulative_chs",
                    "cumulative_cps", "retake_count", "total_improve_retakes_used"):
            assert pii not in r.data, f"PII field {pii!r} exposed"

    def test_cgpa_present(self):
        r = self._run()
        assert r.data["cgpa"] == _STU000031_CGPA

    def test_kg_failure_does_not_break_result(self):
        """Even if KG is completely down, student record must succeed with fallback details."""
        kg = MagicMock()
        kg.call.return_value = {"error": "kg_unavailable"}
        r = self._run(kg=kg)
        assert r.status == "success"
        for d in r.data["completed_course_details"]:
            assert d["course_name"] is None
            assert d["credits"] is None

    def test_unknown_course_uses_fallback_detail(self):
        """A course not in KG catalogue → fallback detail, overall result still success."""
        kg = _make_kg(catalogue={"C-CS112": {"name": "Programming Fundamentals", "credits": 3}})
        student = _make_student(completed=["C-UNKNOWN999"])
        r = self._run(student=student, kg=kg)
        assert r.status == "success"
        detail = r.data["completed_course_details"][0]
        assert detail["course_code"] == "C-UNKNOWN999"
        assert detail["course_name"] is None

    def test_course_profile_cache_used_across_lists(self):
        """A code appearing in multiple lists is fetched from KG only once."""
        kg = _make_kg()
        student = _make_student(
            completed=["C-CS111"],
            in_progress=["C-CS111"],   # same code in two lists (edge case)
            failed=[],
        )
        _make_orchestrator(kg=kg)
        orch = _make_orchestrator(kg=kg)
        orch.execute_turn([_sq("get_student_record")], _make_session(student=student), _make_bundles())
        # get_course_profile for C-CS111 should be called exactly once (cache hit on second list)
        profile_calls = [
            c for c in kg.call.call_args_list
            if c[0][0] == "get_course_profile"
            and c[0][1].get("course_code") == "C-CS111"
        ]
        assert len(profile_calls) == 1


# ── Composer rendering ────────────────────────────────────────────────────────

class TestComposerD6Rendering:
    """Verify deterministic Composer renders enriched details name-first."""

    def _make_d6_result(self, completed_details=None, in_progress_details=None,
                        failed_details=None, cgpa=3.48, track_id="SWE") -> PerSQResult:
        return PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "cgpa": cgpa,
                "track_id": track_id,
                "academic_standing": "good",
                "current_semester": "Spring 2026",
                "completed_course_details": completed_details or [],
                "in_progress_course_details": in_progress_details or [],
                "failed_course_details": failed_details or [],
            },
        )

    def _answer(self, **kwargs) -> str:
        r = self._make_d6_result(**kwargs)
        return _deterministic_answer([_extract_packet(r)])

    def test_completed_courses_rendered_name_first(self):
        details = [{"course_code": "C-CS111", "course_name": "Introduction to Computer Science", "credits": 3}]
        answer = self._answer(completed_details=details)
        assert "Introduction to Computer Science (C-CS111)" in answer

    def test_in_progress_courses_rendered_name_first(self):
        details = [
            {"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3},
            {"course_code": "HUM224", "course_name": "Introduction to Management", "credits": 2},
        ]
        answer = self._answer(in_progress_details=details)
        assert "Programming Fundamentals (C-CS112)" in answer
        assert "Introduction to Management (HUM224)" in answer

    def test_failed_courses_rendered_name_first(self):
        details = [{"course_code": "C-MA111", "course_name": "Calculus I", "credits": 3}]
        answer = self._answer(failed_details=details)
        assert "Calculus I (C-MA111)" in answer

    def test_all_stu000031_completed_rendered(self):
        details = [
            {"course_code": c, "course_name": _COURSE_CATALOGUE[c]["name"],
             "credits": _COURSE_CATALOGUE[c]["credits"]}
            for c in _STU000031_COMPLETED
        ]
        answer = self._answer(completed_details=details)
        for code in _STU000031_COMPLETED:
            name = _COURSE_CATALOGUE[code]["name"]
            assert f"{name} ({code})" in answer, f"Missing name-first label for {code}"

    def test_all_stu000031_in_progress_rendered(self):
        details = [
            {"course_code": c, "course_name": _COURSE_CATALOGUE[c]["name"],
             "credits": _COURSE_CATALOGUE[c]["credits"]}
            for c in _STU000031_IN_PROGRESS
        ]
        answer = self._answer(in_progress_details=details)
        for code in _STU000031_IN_PROGRESS:
            name = _COURSE_CATALOGUE[code]["name"]
            assert f"{name} ({code})" in answer, f"Missing name-first label for {code}"

    def test_code_only_fallback_when_name_none(self):
        """If KG returned no name, code still appears without fabricated name."""
        details = [{"course_code": "C-CS999", "course_name": None, "credits": None}]
        answer = self._answer(completed_details=details)
        assert "C-CS999" in answer
        # Must not invent a name before the code
        assert "(C-CS999)" not in answer or "C-CS999" in answer

    def test_no_raw_dict_in_answer(self):
        details = [{"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3}]
        answer = self._answer(in_progress_details=details)
        assert "{'course_code'" not in answer
        assert '"course_code"' not in answer

    def test_no_generic_filler(self):
        details = [{"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3}]
        answer = self._answer(in_progress_details=details)
        assert "let me know" not in answer.lower()
        assert "if you need" not in answer.lower()
        assert "further details" not in answer.lower()
        assert "if you'd like" not in answer.lower()

    def test_cgpa_in_answer(self):
        answer = self._answer()
        assert "3.48" in answer

    def test_academic_standing_in_answer(self):
        answer = self._answer()
        assert "good" in answer.lower()


# ── End-to-end pipeline simulation ───────────────────────────────────────────

class TestD6PipelineE2E:
    """
    Simulate the full pipeline for STU000031-style queries without a live system.
    Orchestrator enriches → Composer renders.
    """

    def _pipeline(self, query_text: str, student: StudentContext = None) -> str:
        from gateway.response_composer import ResponseComposer
        import os
        from unittest.mock import patch

        orch = _make_orchestrator()
        session = _make_session(student=student or _make_student())
        turn = orch.execute_turn([_sq("get_student_record", original_text=query_text)],
                                 session, _make_bundles())

        with patch.dict(os.environ, {"COMPOSER_USE_LLM": "false"}):
            composer = ResponseComposer()
        qr = composer.compose(query_text, turn, session.session_id, "test")
        return qr.answer_text

    def test_what_courses_did_i_complete_shows_names(self):
        answer = self._pipeline("what courses did i complete so far?")
        for code in _STU000031_COMPLETED:
            name = _COURSE_CATALOGUE[code]["name"]
            assert f"{name} ({code})" in answer, (
                f"Expected '{name} ({code})' in answer, got:\n{answer}"
            )

    def test_what_courses_am_i_taking_now_shows_names(self):
        answer = self._pipeline("what courses am i taking now?")
        for code in _STU000031_IN_PROGRESS:
            name = _COURSE_CATALOGUE[code]["name"]
            assert f"{name} ({code})" in answer, (
                f"Expected '{name} ({code})' in answer, got:\n{answer}"
            )

    def test_answer_includes_cgpa(self):
        answer = self._pipeline("what is my record?")
        assert "3.48" in answer

    def test_no_raw_codes_alone_in_answer(self):
        """Raw codes like 'C-CS112' must not appear without their course name in the answer."""
        answer = self._pipeline("show me my courses")
        for code in _STU000031_COMPLETED + _STU000031_IN_PROGRESS:
            # Code should appear inside "Name (CODE)" pattern, not bare
            assert code in answer, f"{code} completely missing from answer"
            name = _COURSE_CATALOGUE[code]["name"]
            assert f"{name} ({code})" in answer, (
                f"{code} appears as bare code without name"
            )

    def test_failed_course_appears_name_first_when_present(self):
        student = _make_student(failed=["C-MA111"])
        answer = self._pipeline("what courses have i failed?", student=student)
        assert "Calculus I (C-MA111)" in answer


# ── D6 Failed Course History ──────────────────────────────────────────────────

class TestD6FailedCourseHistory:
    """Tests for record_focus='failed_course_history' — distinct from failed_courses."""

    _HISTORY = [
        CourseRecord(course_code="C-MA111", semester_taken="Fall 2024", status="failed"),
        CourseRecord(course_code="C-MA111", semester_taken="Spring 2025", status="passed"),
        CourseRecord(course_code="C-CS111", semester_taken="Fall 2024", status="failed"),
        CourseRecord(course_code="C-PH111", semester_taken="Spring 2025", status="repeated"),
    ]

    def _make_student_with_history(self, course_history=None, failed=None) -> StudentContext:
        # Use sentinel None to distinguish "default" from explicit empty list
        resolved_history = self._HISTORY if course_history is None else course_history
        return StudentContext(
            student_id="STU000031",
            name="Test Student",
            program="CIS",
            track_id="SWE",
            level=2,
            first_semester="Fall 2024",
            study_status="Studying",
            cgpa=3.1,
            cumulative_chs=20,
            cumulative_cps=62.0,
            total_credit_hours_earned=20,
            completed_courses=["C-MA111", "C-CS111"],
            failed_courses=failed or [],
            in_progress_courses=["C-PH111"],
            current_semester="Spring 2026",
            course_history=resolved_history,
        )

    def _run_sq(self, sq, kg=None, student=None) -> PerSQResult:
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=student or self._make_student_with_history())
        result = orch.execute_turn([sq], session, _make_bundles())
        return result.results[0]

    def test_failed_history_focus_returns_success(self):
        r = self._run_sq(_sq("get_student_record", params={"record_focus": "failed_course_history"}))
        assert r.status == "success"

    def test_failed_history_codes_extracted_from_course_history(self):
        r = self._run_sq(_sq("get_student_record", params={"record_focus": "failed_course_history"}))
        codes = r.data.get("failed_history_codes") or []
        # Should include C-MA111 (failed then passed) and C-CS111 (failed) and C-PH111 (repeated)
        assert "C-MA111" in codes
        assert "C-CS111" in codes
        assert "C-PH111" in codes

    def test_failed_history_deduplicated(self):
        """Each course appears only once even if failed multiple times."""
        history_with_dup = [
            CourseRecord(course_code="C-MA111", semester_taken="Fall 2024", status="failed"),
            CourseRecord(course_code="C-MA111", semester_taken="Spring 2025", status="failed"),
        ]
        student = self._make_student_with_history(course_history=history_with_dup)
        r = self._run_sq(
            _sq("get_student_record", params={"record_focus": "failed_course_history"}),
            student=student,
        )
        codes = r.data.get("failed_history_codes") or []
        assert codes.count("C-MA111") == 1

    def test_failed_history_details_enriched(self):
        kg = _make_kg()
        r = self._run_sq(
            _sq("get_student_record", params={"record_focus": "failed_course_history"}),
            kg=kg,
        )
        details = r.data.get("failed_history_details") or []
        assert len(details) > 0
        for d in details:
            assert "course_code" in d

    def test_failed_history_distinct_from_failed_courses(self):
        """failed_history should be different from failed_courses (current unresolved fails)."""
        student = self._make_student_with_history(
            failed=["C-CS111"],  # current unresolved fail
        )
        r = self._run_sq(
            _sq("get_student_record", params={"record_focus": "failed_course_history"}),
            student=student,
        )
        # History codes include all that were ever failed/repeated
        history_codes = r.data.get("failed_history_codes") or []
        # C-MA111 was failed (now passed) — should appear in history but not in failed_courses
        assert "C-MA111" in history_codes

    def test_empty_course_history_returns_empty_failed_history(self):
        student = self._make_student_with_history(course_history=[])
        r = self._run_sq(
            _sq("get_student_record", params={"record_focus": "failed_course_history"}),
            student=student,
        )
        assert r.data.get("failed_history_codes") == []
        assert r.data.get("failed_history_details") == []

    def test_composer_renders_failed_history(self):
        r = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "failed_course_history",
                "failed_history_details": [
                    {"course_code": "C-MA111", "course_name": "Calculus I", "credits": 3},
                    {"course_code": "C-CS111", "course_name": "Intro to CS", "credits": 3},
                ],
                "failed_history_codes": ["C-MA111", "C-CS111"],
            },
        )
        packet = _extract_packet(r)
        answer = _deterministic_answer([packet])
        assert "Calculus I (C-MA111)" in answer
        assert "Intro to CS (C-CS111)" in answer
        # Should mention that some may have been retaken
        assert "retaken" in answer.lower() or "passed" in answer.lower()

    def test_composer_renders_empty_failed_history_gracefully(self):
        r = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "failed_course_history",
                "failed_history_details": [],
                "failed_history_codes": [],
            },
        )
        packet = _extract_packet(r)
        answer = _deterministic_answer([packet])
        assert "no historically failed" in answer.lower()


# ── D6 Course Status Check (Focused Enrichment) ───────────────────────────────

class TestD6CourseStatusCheckFocused:
    """Tests that course_status_check only enriches the target course."""

    def _run_sq(self, sq, kg=None, student=None) -> PerSQResult:
        orch = _make_orchestrator(kg=kg)
        session = _make_session(student=student or _make_student())
        result = orch.execute_turn([sq], session, _make_bundles())
        return result.results[0]

    def test_course_in_completed_enriches_only_that_course(self):
        student = _make_student(
            completed=_STU000031_COMPLETED + ["C-CS112"],
            in_progress=["C-MA112", "C-PH112"],
        )
        kg = _make_kg()
        sq = _sq("get_student_record",
                 params={"record_focus": "course_status_check"},
                 entities={"course_code": "HUM111"})
        r = self._run_sq(sq, kg=kg, student=student)
        assert r.status == "success"
        completed_d = r.data.get("completed_course_details") or []
        in_prog_d = r.data.get("in_progress_course_details") or []
        # Should only have HUM111 in completed_details (target course)
        assert all(d["course_code"] == "HUM111" for d in completed_d)
        # In-progress for target should be empty
        assert len(in_prog_d) == 0

    def test_course_in_in_progress_enriches_only_that_course(self):
        student = _make_student(
            in_progress=_STU000031_IN_PROGRESS,
        )
        kg = _make_kg()
        sq = _sq("get_student_record",
                 params={"record_focus": "course_status_check"},
                 entities={"course_code": "C-CS112"})
        r = self._run_sq(sq, kg=kg, student=student)
        assert r.status == "success"
        in_prog_d = r.data.get("in_progress_course_details") or []
        # Should only have C-CS112 in in_progress_details
        assert all(d["course_code"] == "C-CS112" for d in in_prog_d)

    def test_course_not_in_any_list_returns_empty_details(self):
        sq = _sq("get_student_record",
                 params={"record_focus": "course_status_check"},
                 entities={"course_code": "C-NONEXISTENT"})
        r = self._run_sq(sq)
        assert r.status == "success"
        assert r.data.get("completed_course_details") == []
        assert r.data.get("failed_course_details") == []

    def test_kg_called_at_most_once_for_target_course(self):
        """KG should not be called for all courses, only the target."""
        student = _make_student(
            completed=_STU000031_COMPLETED,
            in_progress=_STU000031_IN_PROGRESS,
        )
        kg = _make_kg()
        sq = _sq("get_student_record",
                 params={"record_focus": "course_status_check"},
                 entities={"course_code": "C-CS111"})
        _make_orchestrator(kg=kg).execute_turn([sq], _make_session(student=student), _make_bundles())

        profile_calls = [
            c for c in kg.call.call_args_list
            if c[0][0] == "get_course_profile"
        ]
        # Should not call KG for all 10 courses, only 1 (target)
        assert len(profile_calls) <= 1

    def test_composer_course_status_in_progress_says_yes(self):
        r = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "course_status_check",
                "in_progress_course_details": [
                    {"course_code": "C-CS112", "course_name": "Programming Fundamentals", "credits": 3},
                ],
                "completed_course_details": [],
                "failed_course_details": [],
                "in_progress_courses": ["C-CS112"],
                "completed_courses": [],
                "failed_courses": [],
            },
        )
        packet = _extract_packet(r)
        answer = _deterministic_answer([packet])
        assert "Programming Fundamentals" in answer
        assert "yes" in answer.lower() or "current" in answer.lower()

    def test_composer_course_status_completed_says_completed(self):
        r = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "course_status_check",
                "completed_course_details": [
                    {"course_code": "C-CS111", "course_name": "Introduction to Computer Science", "credits": 3},
                ],
                "in_progress_course_details": [],
                "failed_course_details": [],
                "completed_courses": ["C-CS111"],
                "in_progress_courses": [],
                "failed_courses": [],
            },
        )
        packet = _extract_packet(r)
        answer = _deterministic_answer([packet])
        assert "Introduction to Computer Science" in answer
        assert "completed" in answer.lower() or "yes" in answer.lower()


# ── D6 Assumption Wording ─────────────────────────────────────────────────────

class TestD6AssumptionWording:
    """Tests assumption_acknowledgement wording — must not say 'according to our records'."""

    def _make_ack_result(self, assumed_failed=None, assumed_passed=None) -> PerSQResult:
        return PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            override_state_active=True,
            data={
                "record_focus": "assumption_acknowledgement",
                "assumed_failed_courses": assumed_failed or [],
                "assumed_passed_courses": assumed_passed or [],
                "failed_course_details": [
                    {"course_code": c, "course_name": _COURSE_CATALOGUE.get(c, {}).get("name"), "credits": 3}
                    for c in (assumed_failed or [])
                ],
                "completed_course_details": [
                    {"course_code": c, "course_name": _COURSE_CATALOGUE.get(c, {}).get("name"), "credits": 3}
                    for c in (assumed_passed or [])
                ],
            },
        )

    def _answer(self, result: PerSQResult) -> str:
        packet = _extract_packet(result)
        return _deterministic_answer([packet])

    def test_ack_does_not_say_according_to_our_records(self):
        r = self._make_ack_result(assumed_failed=["C-MA111"])
        answer = self._answer(r)
        assert "according to our records" not in answer.lower()

    def test_ack_says_official_record_unchanged(self):
        r = self._make_ack_result(assumed_failed=["C-MA111"])
        answer = self._answer(r)
        assert "official academic record is unchanged" in answer.lower()

    def test_ack_says_what_if(self):
        r = self._make_ack_result(assumed_failed=["C-MA111"])
        answer = self._answer(r)
        assert "what-if" in answer.lower() or "assumed" in answer.lower()

    def test_ack_failed_course_shows_name_when_enriched(self):
        r = self._make_ack_result(assumed_failed=["C-MA111"])
        answer = self._answer(r)
        # Should show course name "Calculus I" not bare code
        assert "Calculus I (C-MA111)" in answer or "C-MA111" in answer

    def test_ack_passed_course_shows_name_when_enriched(self):
        r = self._make_ack_result(assumed_passed=["C-PH111"])
        answer = self._answer(r)
        assert "Physics I (C-PH111)" in answer or "C-PH111" in answer

    def test_override_preamble_does_not_say_according_to_our_records(self):
        """What-if preamble when a non-acknowledgement focus is active."""
        r = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            override_state_active=True,
            data={
                "record_focus": "completed_courses",
                "assumed_failed_courses": ["C-MA111"],
                "assumed_passed_courses": [],
                "failed_course_details": [
                    {"course_code": "C-MA111", "course_name": "Calculus I", "credits": 3}
                ],
                "completed_course_details": [],
                "completed_courses": [],
                "in_progress_courses": [],
                "failed_courses": ["C-MA111"],
            },
        )
        packet = _extract_packet(r)
        answer = _deterministic_answer([packet])
        assert "according to our records" not in answer.lower()

    def test_reset_says_cleared_correctly(self):
        """Reset assumptions message must match exact wording."""
        r = PerSQResult(
            sq_index=0,
            intent="get_student_record",
            status="success",
            data={
                "record_focus": "reset_assumptions",
                "assumptions_cleared": True,
                "message": "I cleared your what-if assumptions. You are back to your official academic record.",
                "assumed_failed_courses": [],
                "assumed_passed_courses": [],
            },
        )
        packet = _extract_packet(r)
        answer = _deterministic_answer([packet])
        assert "I cleared your what-if assumptions." in answer
        assert "official academic record" in answer
