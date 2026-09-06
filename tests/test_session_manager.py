"""
Phase 1 Step 5 — Session Manager + Session Store focused tests.

Groups:
  G1  — SQLiteSessionStore isolation (save/load/delete/corrupted blobs)
  G2  — Override accumulation logic (_apply_overrides / merge_turn_overrides)
  G3  — build_effective_context multi-list independence
  G4  — last_referenced merging
  G5  — get_or_create_session ownership / context refresh
  G6  — update_session_after_turn (merge, replace, last_referenced merge)
  G7  — Delete operations (single, per-student, global)
  G8  — Session lifecycle integration
  G9  — Schema mutable-default regression
  G10 — Legacy apply_query_result (kept for backward compat)
"""
from __future__ import annotations

import sqlite3 as _sqlite3
import time
import uuid

import pytest

from gateway.models.schemas import (
    EntitySet,
    LastReferenced,
    SessionOverrides,
    SessionState,
    StudentContext,
    StructuredQuery,
)
from gateway.session_store.sqlite_store import SQLiteSessionStore
import gateway.session_manager as sm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ctx(
    student_id: str = "S001",
    completed: list[str] | None = None,
    failed: list[str] | None = None,
    in_progress: list[str] | None = None,
    zero_credit: list[str] | None = None,
) -> StudentContext:
    return StudentContext(
        student_id=student_id,
        name="Test Student",
        program="CS",
        track_id="AI",
        level=3,
        first_semester="2022-Fall",
        study_status="regular",
        total_credit_hours_earned=60,
        completed_courses=completed or [],
        failed_courses=failed or [],
        in_progress_courses=in_progress or [],
        zero_credit_courses_passed=zero_credit or [],
    )


def make_session(student_id: str = "S001", session_id: str | None = None) -> SessionState:
    return SessionState(
        session_id=session_id or str(uuid.uuid4()),
        student_id=student_id,
        session_name="Test Session",
        student_context=make_ctx(student_id),
    )


def make_sq(
    added: list[str] | None = None,
    passed: list[str] | None = None,
    failed_courses: list[str] | None = None,
    override_type: str = "none",
    action: str = "accumulate",
    target_role: str | None = None,
    entities: EntitySet | None = None,
) -> StructuredQuery:
    return StructuredQuery(
        intent="test",
        entities=entities or EntitySet(),
        session_overrides=SessionOverrides(
            added_courses=added or [],
            assumed_passed_courses=passed or [],
            assumed_failed_courses=failed_courses or [],
            target_role=target_role,
            course_override_type=override_type,
            override_action=action,
        ),
    )


def _ovr(
    added=None, passed=None, failed=None,
    override_type="none", action="accumulate",
) -> SessionOverrides:
    return SessionOverrides(
        added_courses=added or [],
        assumed_passed_courses=passed or [],
        assumed_failed_courses=failed or [],
        course_override_type=override_type,
        override_action=action,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return SQLiteSessionStore(str(tmp_path / "test.db"))


@pytest.fixture
def patched_sm(tmp_path, monkeypatch):
    test_store = SQLiteSessionStore(str(tmp_path / "sm_test.db"))
    monkeypatch.setattr(sm, "_store", test_store)
    return test_store


# ===========================================================================
# G1 — SQLiteSessionStore isolation
# ===========================================================================

class TestSQLiteStore:

    def test_save_and_load(self, store):
        s = make_session()
        store.save(s)
        loaded = store.load(s.session_id)
        assert loaded is not None
        assert loaded.session_id == s.session_id
        assert loaded.student_id == s.student_id

    def test_load_nonexistent_returns_none(self, store):
        assert store.load("does-not-exist") is None

    def test_delete_existing(self, store):
        s = make_session()
        store.save(s)
        assert store.delete(s.session_id) is True
        assert store.load(s.session_id) is None

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete("does-not-exist") is False

    def test_get_all_for_student_ordered(self, store):
        ctx = make_ctx("S999")
        for i in range(3):
            store.save(SessionState(
                session_id=f"ses-{i}", student_id="S999",
                session_name=f"Session {i}", student_context=ctx,
            ))
            time.sleep(0.02)
        result = store.get_all_for_student("S999")
        assert len(result) == 3
        assert result[0].session_id == "ses-2"
        assert result[2].session_id == "ses-0"

    def test_delete_all(self, store):
        ctx = make_ctx()
        for i in range(3):
            store.save(SessionState(
                session_id=f"del-{i}", student_id="S001",
                session_name=f"Session {i}", student_context=ctx,
            ))
        count = store.delete_all()
        assert count == 3
        assert store.get_all_for_student("S001") == []

    def test_delete_all_for_student(self, store):
        """delete_all_for_student removes only the given student's sessions."""
        ctx_a = make_ctx("SA")
        ctx_b = make_ctx("SB")
        for i in range(2):
            store.save(SessionState(
                session_id=f"a-{i}", student_id="SA",
                session_name=f"A{i}", student_context=ctx_a,
            ))
        store.save(SessionState(
            session_id="b-0", student_id="SB",
            session_name="B0", student_context=ctx_b,
        ))
        count = store.delete_all_for_student("SA")
        assert count == 2
        assert store.get_all_for_student("SA") == []
        assert len(store.get_all_for_student("SB")) == 1

    def test_get_summaries_for_student(self, store):
        ctx = make_ctx("S777")
        store.save(SessionState(
            session_id="sum-1", student_id="S777",
            session_name="Summary Test", student_context=ctx,
        ))
        rows = store.get_summaries_for_student("S777")
        assert len(rows) == 1
        sid, name, lu = rows[0]
        assert sid == "sum-1"
        assert name == "Summary Test"
        assert lu  # non-empty timestamp

    def test_load_corrupted_blob_returns_none(self, store):
        sid = "corrupted-001"
        with _sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, student_id, session_name, last_updated, session_blob) "
                "VALUES (?, ?, ?, ?, ?)",
                (sid, "S001", "Test", "2026-01-01T00:00:00+00:00", "{not valid json!!!"),
            )
        assert store.load(sid) is None

    def test_get_all_for_student_skips_corrupted_blobs(self, store):
        ctx = make_ctx("S002")
        store.save(SessionState(
            session_id="valid-001", student_id="S002",
            session_name="Valid", student_context=ctx,
        ))
        time.sleep(0.02)
        with _sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, student_id, session_name, last_updated, session_blob) "
                "VALUES (?, ?, ?, ?, ?)",
                ("corrupted-002", "S002", "Corrupted", "2026-06-14T00:00:00+00:00", "{bad}"),
            )
        results = store.get_all_for_student("S002")
        assert len(results) == 1
        assert results[0].session_id == "valid-001"

    def test_sqlite_roundtrip_full_session(self, store):
        """Full SessionState with overrides and history roundtrips correctly."""
        ctx = make_ctx("S100", completed=["C-CS101"], failed=["C-MA200"])
        s = SessionState(
            session_id="rt-001",
            student_id="S100",
            session_name="Roundtrip",
            student_context=ctx,
            last_referenced=LastReferenced(course_code="C-CS101", role_id="RL_ML_Engineer"),
            overrides=SessionOverrides(
                added_courses=["C-AI311"],
                assumed_passed_courses=["C-MA200"],
                course_override_type="assumed_passed",
            ),
        )
        s.turn_history.append({"user": "hi", "answer": "hello"})
        store.save(s)
        loaded = store.load("rt-001")
        assert loaded.overrides.assumed_passed_courses == ["C-MA200"]
        assert loaded.last_referenced.course_code == "C-CS101"
        assert loaded.last_referenced.role_id == "RL_ML_Engineer"
        assert loaded.turn_history[0]["user"] == "hi"


# ===========================================================================
# G2 — Override accumulation logic
# ===========================================================================

class TestOverrideAccumulation:

    def test_accumulate_courses(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "first")
        sid = state.session_id
        sm.apply_query_result(sid, make_sq(added=["C-AI311"], override_type="planned"))
        sm.apply_query_result(sid, make_sq(added=["C-CS301"], override_type="planned"))
        session = patched_sm.load(sid)
        assert "C-AI311" in session.overrides.added_courses
        assert "C-CS301" in session.overrides.added_courses

    def test_replace_discards_old_courses(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "first")
        sid = state.session_id
        sm.apply_query_result(sid, make_sq(added=["C-AI311"], override_type="planned"))
        sm.apply_query_result(sid, make_sq(added=["C-CS301"], override_type="planned", action="replace"))
        session = patched_sm.load(sid)
        assert session.overrides.added_courses == ["C-CS301"]
        assert "C-AI311" not in session.overrides.added_courses

    def test_clear_resets_all_overrides(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "first")
        sid = state.session_id
        sm.apply_query_result(sid, make_sq(added=["C-AI311"], override_type="planned", target_role="SWE"))
        sm.apply_query_result(sid, make_sq(action="clear"))
        session = patched_sm.load(sid)
        assert session.overrides.added_courses == []
        assert session.overrides.target_role is None
        assert session.overrides.course_override_type == "none"
        assert session.overrides.assumed_passed_courses == []
        assert session.overrides.assumed_failed_courses == []

    def test_accumulate_preserves_all_lists(self):
        """accumulate merges all three lists independently."""
        base = _ovr(passed=["C-AI101"], failed=["C-CS201"], added=["C-AI311"])
        incoming = _ovr(passed=["C-AI201"], failed=["C-CS301"], added=["C-AI411"])
        merged = sm._apply_overrides(base, incoming)
        assert set(merged.assumed_passed_courses) == {"C-AI101", "C-AI201"}
        assert set(merged.assumed_failed_courses) == {"C-CS201", "C-CS301"}
        assert set(merged.added_courses) == {"C-AI311", "C-AI411"}

    def test_accumulate_deduplicates_deterministically(self):
        """Duplicate course codes produce exactly one entry, first-occurrence order."""
        base = _ovr(added=["C-A", "C-B"])
        incoming = _ovr(added=["C-B", "C-C"])
        merged = sm._apply_overrides(base, incoming)
        assert merged.added_courses == ["C-A", "C-B", "C-C"]

    def test_merge_turn_overrides_empty(self):
        merged, had_clear = sm.merge_turn_overrides([])
        assert merged.added_courses == []
        assert had_clear is False

    def test_merge_turn_overrides_single_accumulate(self):
        sqs = [make_sq(added=["C-AI311"], override_type="planned")]
        merged, had_clear = sm.merge_turn_overrides(sqs)
        assert "C-AI311" in merged.added_courses
        assert merged.course_override_type == "planned"
        assert had_clear is False

    def test_merge_turn_overrides_multiple_sqs_union(self):
        sqs = [
            make_sq(added=["C-AI311"], override_type="planned"),
            make_sq(added=["C-CS301"], override_type="planned"),
        ]
        merged, _ = sm.merge_turn_overrides(sqs)
        assert "C-AI311" in merged.added_courses
        assert "C-CS301" in merged.added_courses

    def test_merge_turn_overrides_clear_detected(self):
        sqs = [
            make_sq(added=["C-AI311"], override_type="planned"),
            make_sq(action="clear"),
        ]
        merged, had_clear = sm.merge_turn_overrides(sqs)
        assert had_clear is True
        assert merged.added_courses == []
        assert merged.course_override_type == "none"

    def test_merge_turn_overrides_mixed_types(self):
        """Passed and failed from different SQs are both preserved."""
        sqs = [
            make_sq(passed=["C-AI101"], override_type="assumed_passed"),
            make_sq(failed_courses=["C-CS301"], override_type="assumed_failed"),
        ]
        merged, _ = sm.merge_turn_overrides(sqs)
        assert "C-AI101" in merged.assumed_passed_courses
        assert "C-CS301" in merged.assumed_failed_courses


# ===========================================================================
# G3 — build_effective_context multi-list independence
# ===========================================================================

class TestBuildEffectiveContext:

    def test_assumed_done_adds_to_completed_removes_from_failed_and_in_progress(self):
        ctx = make_ctx(failed=["C-AI321"], in_progress=["C-AI421"])
        ovr = SessionOverrides(added_courses=["C-AI321", "C-AI421"], course_override_type="assumed_done")
        eff = sm.build_effective_context(ctx, ovr)
        assert "C-AI321" in eff.completed_courses
        assert "C-AI421" in eff.completed_courses
        assert "C-AI321" not in eff.failed_courses
        assert "C-AI421" not in eff.in_progress_courses

    def test_planned_adds_to_in_progress(self):
        ctx = make_ctx(completed=["C-CS101"])
        ovr = SessionOverrides(added_courses=["C-AI311"], course_override_type="planned")
        eff = sm.build_effective_context(ctx, ovr)
        assert "C-AI311" in eff.in_progress_courses
        assert "C-CS101" in eff.completed_courses
        assert "C-AI311" not in eff.completed_courses

    def test_assumed_failed_moves_course_out_of_completed_zero_credit_in_progress(self):
        ctx = make_ctx(
            completed=["C-CS101", "C-MA111"],
            in_progress=["C-AI201"],
            zero_credit=["C-MA111"],
        )
        ovr = SessionOverrides(assumed_failed_courses=["C-MA111", "C-AI201"])
        eff = sm.build_effective_context(ctx, ovr)
        assert "C-MA111" in eff.failed_courses
        assert "C-AI201" in eff.failed_courses
        assert "C-MA111" not in eff.completed_courses
        assert "C-MA111" not in eff.zero_credit_courses_passed
        assert "C-AI201" not in eff.in_progress_courses
        assert "C-CS101" in eff.completed_courses  # unaffected

    def test_assumed_passed_moves_course_out_of_failed_and_in_progress(self):
        ctx = make_ctx(failed=["C-MA111"], in_progress=["C-CS301"], completed=["C-CS101"])
        ovr = SessionOverrides(assumed_passed_courses=["C-MA111", "C-CS301"])
        eff = sm.build_effective_context(ctx, ovr)
        assert "C-MA111" in eff.completed_courses
        assert "C-CS301" in eff.completed_courses
        assert "C-MA111" not in eff.failed_courses
        assert "C-CS301" not in eff.in_progress_courses
        assert "C-CS101" in eff.completed_courses  # unaffected

    def test_all_three_lists_applied_independently(self):
        """
        assumed_passed + assumed_failed + planned are all applied in one call.
        course_override_type does NOT suppress the other lists.
        """
        ctx = make_ctx(failed=["C-MA111"], completed=["C-CS101"])
        ovr = SessionOverrides(
            assumed_passed_courses=["C-MA111"],
            assumed_failed_courses=["C-CS101"],  # now failed
            added_courses=["C-AI311"],
            course_override_type="planned",
        )
        eff = sm.build_effective_context(ctx, ovr)
        # assumed_failed applied first
        assert "C-CS101" not in eff.completed_courses
        assert "C-CS101" in eff.failed_courses
        # assumed_passed applied second: C-MA111 is now completed, removed from failed
        assert "C-MA111" in eff.completed_courses
        assert "C-MA111" not in eff.failed_courses
        # planned applied third
        assert "C-AI311" in eff.in_progress_courses

    def test_assumed_passed_then_assumed_failed_same_course_deterministic(self):
        """
        If the same course appears in both assumed_failed AND assumed_passed,
        the documented order is: failed applied first, then passed.
        Result: course ends up in completed (passed wins as it is applied second).
        """
        ctx = make_ctx(completed=["C-CS101"])
        ovr = SessionOverrides(
            assumed_failed_courses=["C-CS101"],
            assumed_passed_courses=["C-CS101"],
        )
        eff = sm.build_effective_context(ctx, ovr)
        # Step 1: assumed_failed removes from completed, adds to failed → C-CS101 in failed
        # Step 2: assumed_passed removes from failed, adds to completed → C-CS101 in completed
        assert "C-CS101" in eff.completed_courses
        assert "C-CS101" not in eff.failed_courses

    def test_does_not_mutate_base_context(self):
        ctx = make_ctx(completed=["C-CS101"], failed=["C-MA111"])
        original_completed = list(ctx.completed_courses)
        original_failed = list(ctx.failed_courses)
        ovr = SessionOverrides(
            assumed_passed_courses=["C-MA111"],
            assumed_failed_courses=["C-CS101"],
            added_courses=["C-AI311"],
            course_override_type="planned",
        )
        sm.build_effective_context(ctx, ovr)
        assert ctx.completed_courses == original_completed
        assert ctx.failed_courses == original_failed

    def test_no_op_returns_base_context_unchanged(self):
        """Empty overrides must return the base context unchanged (no copy)."""
        ctx = make_ctx(completed=["C-CS101"])
        ovr = SessionOverrides()
        eff = sm.build_effective_context(ctx, ovr)
        assert eff is ctx  # same object — no copy needed

    def test_assumed_passed_plus_planned_mixed(self):
        """assumed_passed and planned can coexist in one override set."""
        ctx = make_ctx(failed=["C-MA111"])
        ovr = SessionOverrides(
            assumed_passed_courses=["C-MA111"],
            added_courses=["C-AI311"],
            course_override_type="planned",
        )
        eff = sm.build_effective_context(ctx, ovr)
        assert "C-MA111" in eff.completed_courses
        assert "C-AI311" in eff.in_progress_courses

    def test_assumed_failed_plus_planned_mixed(self):
        """assumed_failed and planned can coexist in one override set."""
        ctx = make_ctx(completed=["C-CS101"])
        ovr = SessionOverrides(
            assumed_failed_courses=["C-CS101"],
            added_courses=["C-AI311"],
            course_override_type="planned",
        )
        eff = sm.build_effective_context(ctx, ovr)
        assert "C-CS101" in eff.failed_courses
        assert "C-CS101" not in eff.completed_courses
        assert "C-AI311" in eff.in_progress_courses

    def test_planned_persists_when_override_type_is_assumed_passed(self):
        """added_courses must remain in_progress even if the latest override type is assumed_passed."""
        ctx = make_ctx()
        ovr = SessionOverrides(
            assumed_passed_courses=["C-MA111"],
            added_courses=["C-AI311"],
            course_override_type="assumed_passed",  # latest action was assumed_passed
        )
        eff = sm.build_effective_context(ctx, ovr)
        assert "C-MA111" in eff.completed_courses
        assert "C-AI311" in eff.in_progress_courses

    def test_planned_persists_when_override_type_is_assumed_failed(self):
        """added_courses must remain in_progress even if the latest override type is assumed_failed."""
        ctx = make_ctx()
        ovr = SessionOverrides(
            assumed_failed_courses=["C-MA111"],
            added_courses=["C-AI311"],
            course_override_type="assumed_failed",  # latest action was assumed_failed
        )
        eff = sm.build_effective_context(ctx, ovr)
        assert "C-MA111" in eff.failed_courses
        assert "C-AI311" in eff.in_progress_courses


# ===========================================================================
# G4 — last_referenced merging
# ===========================================================================

class TestLastReferencedMerge:

    def test_apply_last_referenced_merges_fields(self):
        existing = LastReferenced(course_code="C-CS101", role_id="RL_ML_Engineer")
        new_entities = {"track_id": "AI"}
        result = sm._apply_last_referenced(existing, new_entities)
        assert result.course_code == "C-CS101"   # preserved
        assert result.role_id == "RL_ML_Engineer"  # preserved
        assert result.track_id == "AI"             # new

    def test_apply_last_referenced_does_not_wipe_existing_on_partial_update(self):
        existing = LastReferenced(course_code="C-CS101", role_id="RL_ML_Engineer", track_id="DSE")
        new_entities = {"course_code": "C-AI311"}
        result = sm._apply_last_referenced(existing, new_entities)
        assert result.course_code == "C-AI311"     # updated
        assert result.role_id == "RL_ML_Engineer"  # preserved
        assert result.track_id == "DSE"            # preserved

    def test_apply_last_referenced_merges_skill_id(self):
        existing = LastReferenced(course_code="C-CS101")
        new_entities = {"skill_id": "SK_ML"}
        result = sm._apply_last_referenced(existing, new_entities)
        assert result.skill_id == "SK_ML"
        assert result.course_code == "C-CS101"  # preserved

    def test_update_session_after_turn_merges_last_referenced(self, patched_sm):
        """update_session_after_turn merges rather than replaces last_referenced."""
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
        sid = state.session_id

        # Set an initial last_referenced with course + role
        session = patched_sm.load(sid)
        session.last_referenced = LastReferenced(course_code="C-CS101", role_id="RL_ML_Engineer")
        patched_sm.save(session)

        # Update with only a track_id (should NOT wipe course and role)
        sm.update_session_after_turn(
            sid, "question", "answer",
            new_last_referenced=LastReferenced(track_id="AI"),
        )

        loaded = patched_sm.load(sid)
        assert loaded.last_referenced.course_code == "C-CS101"    # preserved
        assert loaded.last_referenced.role_id == "RL_ML_Engineer"  # preserved
        assert loaded.last_referenced.track_id == "AI"             # new

    def test_last_referenced_accumulates_via_apply_query_result(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
        sid = state.session_id

        sm.apply_query_result(sid, make_sq(entities=EntitySet(course_code="C-AI311")))
        sm.apply_query_result(sid, make_sq(entities=EntitySet(role_id="RL_ML_Engineer")))

        session = patched_sm.load(sid)
        assert session.last_referenced.course_code == "C-AI311"
        assert session.last_referenced.role_id == "RL_ML_Engineer"


# ===========================================================================
# G5 — get_or_create_session ownership / context refresh
# ===========================================================================

class TestGetOrCreateSession:

    def test_new_session_created_when_no_session_id(self, patched_sm):
        ctx = make_ctx()
        state, is_new = sm.get_or_create_session(None, "S001", ctx, "Hello")
        assert is_new is True
        assert state.session_id is not None
        assert patched_sm.load(state.session_id) is not None

    def test_existing_same_student_session_reused(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "Hello")
        sid = state.session_id

        state2, is_new = sm.get_or_create_session(sid, "S001", ctx, "Hello again")
        assert is_new is False
        assert state2.session_id == sid

    def test_existing_session_refreshes_student_context(self, patched_sm):
        """Existing session gets student_context refreshed from fresh SCP context."""
        old_ctx = make_ctx("S001", completed=["C-CS101"])
        state, _ = sm.get_or_create_session(None, "S001", old_ctx, "Hello")
        sid = state.session_id

        # Fresh context has additional completed course
        new_ctx = make_ctx("S001", completed=["C-CS101", "C-AI311"])
        state2, is_new = sm.get_or_create_session(sid, "S001", new_ctx, "Hello again")
        assert is_new is False
        assert "C-AI311" in state2.student_context.completed_courses

    def test_existing_session_refreshes_but_preserves_overrides_and_history(self, patched_sm):
        """Context refresh must not wipe overrides, last_referenced, or turn_history."""
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "Hello")
        sid = state.session_id

        # Manually inject overrides and history
        session = patched_sm.load(sid)
        session.overrides = SessionOverrides(added_courses=["C-AI311"], course_override_type="planned")
        session.last_referenced = LastReferenced(course_code="C-AI311")
        session.turn_history.append({"user": "q1", "answer": "a1"})
        patched_sm.save(session)

        new_ctx = make_ctx("S001", completed=["C-CS101"])
        state2, is_new = sm.get_or_create_session(sid, "S001", new_ctx, "New question")
        assert is_new is False
        assert "C-AI311" in state2.overrides.added_courses
        assert state2.last_referenced.course_code == "C-AI311"
        assert len(state2.turn_history) == 1

    def test_different_student_session_id_does_not_reuse(self, patched_sm):
        """Session belonging to student A must NOT be given to student B."""
        ctx_a = make_ctx("SA")
        state_a, _ = sm.get_or_create_session(None, "SA", ctx_a, "Hello A")
        sid_a = state_a.session_id

        ctx_b = make_ctx("SB")
        state_b, is_new = sm.get_or_create_session(sid_a, "SB", ctx_b, "Hello B")
        assert is_new is True
        assert state_b.session_id != sid_a
        assert state_b.student_id == "SB"

    def test_different_student_session_does_not_leak_data(self, patched_sm):
        """session_b must not contain any data from session_a."""
        ctx_a = make_ctx("SA", completed=["C-CS101"])
        state_a, _ = sm.get_or_create_session(None, "SA", ctx_a, "Hello A")
        sid_a = state_a.session_id

        # Inject some state into SA's session
        session = patched_sm.load(sid_a)
        session.overrides = SessionOverrides(added_courses=["C-AI999"], course_override_type="planned")
        session.turn_history.append({"user": "secret", "answer": "classified"})
        patched_sm.save(session)

        ctx_b = make_ctx("SB")
        state_b, _ = sm.get_or_create_session(sid_a, "SB", ctx_b, "Hello B")
        assert state_b.student_id == "SB"
        assert state_b.overrides.added_courses == []
        assert state_b.turn_history == []
        assert "C-CS101" not in state_b.student_context.completed_courses

    def test_stale_session_id_creates_new_session(self, patched_sm):
        ctx = make_ctx()
        state, is_new = sm.get_or_create_session("nonexistent-id-xyz", "S001", ctx, "Hello")
        assert is_new is True
        assert state.session_id != "nonexistent-id-xyz"


# ===========================================================================
# G6 — update_session_after_turn
# ===========================================================================

class TestUpdateSessionAfterTurn:

    def test_default_merges_overrides(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
        sid = state.session_id

        session = patched_sm.load(sid)
        session.overrides = _ovr(added=["C-AI101"], override_type="planned")
        patched_sm.save(session)

        sm.update_session_after_turn(
            sid, "q", "a",
            new_overrides=_ovr(added=["C-AI311"], override_type="planned"),
            replace_overrides=False,
        )
        loaded = patched_sm.load(sid)
        assert "C-AI101" in loaded.overrides.added_courses
        assert "C-AI311" in loaded.overrides.added_courses

    def test_replace_overrides_true_clears_existing(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
        sid = state.session_id

        session = patched_sm.load(sid)
        session.overrides = _ovr(added=["C-AI101", "C-AI201"], override_type="planned")
        patched_sm.save(session)

        sm.update_session_after_turn(
            sid, "clear q", "a",
            new_overrides=SessionOverrides(),
            replace_overrides=True,
        )
        loaded = patched_sm.load(sid)
        assert loaded.overrides.added_courses == []
        assert loaded.overrides.course_override_type == "none"

    def test_turn_history_appended(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
        sid = state.session_id

        sm.update_session_after_turn(sid, "my question", "my answer")
        loaded = patched_sm.load(sid)
        assert len(loaded.turn_history) == 1
        assert loaded.turn_history[0]["user"] == "my question"
        assert loaded.turn_history[0]["answer"] == "my answer"

    def test_last_referenced_merged_not_replaced(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
        sid = state.session_id

        # Seed existing last_referenced
        session = patched_sm.load(sid)
        session.last_referenced = LastReferenced(course_code="C-CS101", role_id="RL_ML_Engineer")
        patched_sm.save(session)

        # New turn only has track_id — must NOT erase course_code or role_id
        sm.update_session_after_turn(
            sid, "q", "a",
            new_last_referenced=LastReferenced(track_id="DSE"),
        )
        loaded = patched_sm.load(sid)
        assert loaded.last_referenced.course_code == "C-CS101"
        assert loaded.last_referenced.role_id == "RL_ML_Engineer"
        assert loaded.last_referenced.track_id == "DSE"


# ===========================================================================
# G7 — Delete operations
# ===========================================================================

class TestDeleteOperations:

    def test_delete_session_for_student_own_session(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
        sid = state.session_id

        result = sm.delete_session_for_student("S001", sid)
        assert result is True
        assert sm.get_session_history(sid) is None

    def test_delete_session_for_student_rejects_other_student(self, patched_sm):
        """Student B cannot delete student A's session."""
        ctx_a = make_ctx("SA")
        state_a, _ = sm.get_or_create_session(None, "SA", ctx_a, "Hello")
        sid_a = state_a.session_id

        result = sm.delete_session_for_student("SB", sid_a)
        assert result is False
        # Session A should still exist
        assert sm.get_session_history(sid_a) is not None

    def test_delete_session_for_student_nonexistent_session(self, patched_sm):
        result = sm.delete_session_for_student("S001", "nonexistent-session")
        assert result is False

    def test_delete_all_sessions_for_student(self, patched_sm):
        ctx = make_ctx("SA")
        for i in range(3):
            sm.get_or_create_session(None, "SA", ctx, f"message {i}")

        # Add one session for another student
        ctx_b = make_ctx("SB")
        sm.get_or_create_session(None, "SB", ctx_b, "message b")

        count = sm.delete_all_sessions_for_student("SA")
        assert count == 3
        assert sm.get_student_sessions("SA").sessions == []
        assert len(sm.get_student_sessions("SB").sessions) == 1

    def test_delete_all_sessions_global(self, patched_sm):
        ctx_a = make_ctx("SA")
        ctx_b = make_ctx("SB")
        sm.get_or_create_session(None, "SA", ctx_a, "msg a")
        sm.get_or_create_session(None, "SB", ctx_b, "msg b")

        count = sm.delete_all_sessions_global()
        assert count == 2
        assert sm.get_student_sessions("SA").sessions == []
        assert sm.get_student_sessions("SB").sessions == []

    def test_clear_all_sessions_alias(self, patched_sm):
        """clear_all_sessions is an alias for delete_all_sessions_global."""
        ctx = make_ctx()
        sm.get_or_create_session(None, "S001", ctx, "msg")
        count = sm.clear_all_sessions()
        assert count == 1

    def test_get_session_history_for_student_own_session(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
        sid = state.session_id

        hist = sm.get_session_history_for_student("S001", sid)
        assert hist is not None
        assert hist.session_id == sid

    def test_get_session_history_for_student_rejects_other_student(self, patched_sm):
        ctx_a = make_ctx("SA")
        state_a, _ = sm.get_or_create_session(None, "SA", ctx_a, "Hello A")
        sid_a = state_a.session_id

        hist = sm.get_session_history_for_student("SB", sid_a)
        assert hist is None


# ===========================================================================
# G8 — Session lifecycle integration
# ===========================================================================

class TestSessionLifecycle:

    def test_full_turn_lifecycle(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "What courses?")
        sid = state.session_id

        qu = sm.get_qu_context(sid, "What courses?")
        assert qu is not None
        assert qu.user_text == "What courses?"

        sm.update_session_after_turn(sid, "What courses?", "You should take C-AI311.")

        history = sm.get_session_history(sid)
        assert history is not None
        assert len(history.turns) == 1
        assert history.turns[0]["user"] == "What courses?"
        assert history.turns[0]["answer"] == "You should take C-AI311."

    def test_qu_context_window(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
        sid = state.session_id

        session = patched_sm.load(sid)
        for i in range(8):
            session.turn_history.append({"user": f"q{i}", "answer": f"a{i}"})
        patched_sm.save(session)

        qu = sm.get_qu_context(sid, "new question")
        assert qu is not None
        assert len(qu.recent_turns) == sm.QU_CONTEXT_TURNS
        assert qu.recent_turns[-1]["user"] == "q7"

    def test_get_student_sessions_shape(self, patched_sm):
        ctx = make_ctx()
        sm.get_or_create_session(None, "S001", ctx, "session one")
        sm.get_or_create_session(None, "S001", ctx, "session two")
        resp = sm.get_student_sessions("S001")
        assert resp.student_id == "S001"
        assert len(resp.sessions) == 2
        for s in resp.sessions:
            assert s.session_id
            assert s.session_name
            assert s.last_updated


# ===========================================================================
# G9 — Schema mutable-default regression
# ===========================================================================

class TestSchemaMutableDefaults:

    def test_session_overrides_list_defaults_are_independent(self):
        a = SessionOverrides()
        b = SessionOverrides()
        a.added_courses.append("C-X")
        assert b.added_courses == [], "Mutable default shared between instances"

    def test_session_state_overrides_are_independent(self):
        ctx = make_ctx()
        a = SessionState(session_id="a", student_id="S1", session_name="A", student_context=ctx)
        b = SessionState(session_id="b", student_id="S2", session_name="B", student_context=ctx)
        a.overrides.added_courses.append("C-X")
        assert b.overrides.added_courses == [], "Shared overrides mutable default"

    def test_session_state_last_referenced_are_independent(self):
        ctx = make_ctx()
        a = SessionState(session_id="a", student_id="S1", session_name="A", student_context=ctx)
        b = SessionState(session_id="b", student_id="S2", session_name="B", student_context=ctx)
        a.last_referenced.course_code = "C-X"
        assert b.last_referenced.course_code is None, "Shared last_referenced mutable default"

    def test_session_overrides_schema_roundtrip_assumed_failed(self):
        ovr = SessionOverrides(assumed_failed_courses=["C-AI321"])
        reloaded = SessionOverrides.model_validate_json(ovr.model_dump_json())
        assert reloaded.assumed_failed_courses == ["C-AI321"]

    def test_session_overrides_schema_roundtrip_assumed_passed(self):
        ovr = SessionOverrides(assumed_passed_courses=["C-AI321"])
        reloaded = SessionOverrides.model_validate_json(ovr.model_dump_json())
        assert reloaded.assumed_passed_courses == ["C-AI321"]

    def test_last_referenced_has_skill_id(self):
        lr = LastReferenced(skill_id="SK_ML")
        assert lr.skill_id == "SK_ML"
        dumped = lr.model_dump_json()
        reloaded = LastReferenced.model_validate_json(dumped)
        assert reloaded.skill_id == "SK_ML"


# ===========================================================================
# G10 — Legacy apply_query_result (kept for backward compat)
# ===========================================================================

class TestLegacyApplyQueryResult:

    def test_apply_query_result_updates_overrides(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "first")
        sid = state.session_id

        sm.apply_query_result(sid, make_sq(added=["C-AI311"], override_type="planned"))
        sm.apply_query_result(sid, make_sq(added=["C-CS301"], override_type="planned"))

        session = patched_sm.load(sid)
        assert "C-AI311" in session.overrides.added_courses
        assert "C-CS301" in session.overrides.added_courses

    def test_apply_query_result_merges_last_referenced(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "first")
        sid = state.session_id

        sm.apply_query_result(sid, make_sq(entities=EntitySet(course_code="C-AI311")))
        sm.apply_query_result(sid, make_sq(entities=EntitySet(role_id="RL_ML_Engineer")))

        session = patched_sm.load(sid)
        assert session.last_referenced.course_code == "C-AI311"
        assert session.last_referenced.role_id == "RL_ML_Engineer"

    def test_apply_query_result_unknown_session_is_no_op(self, patched_sm):
        """apply_query_result on a nonexistent session must not raise."""
        sm.apply_query_result("nonexistent", make_sq())  # should not raise

    def test_apply_query_result_clear_action(self, patched_sm):
        ctx = make_ctx()
        state, _ = sm.get_or_create_session(None, "S001", ctx, "first")
        sid = state.session_id

        sm.apply_query_result(sid, make_sq(added=["C-AI311"], override_type="planned", target_role="SWE"))
        sm.apply_query_result(sid, make_sq(action="clear"))

        session = patched_sm.load(sid)
        assert session.overrides.added_courses == []
        assert session.overrides.target_role is None
