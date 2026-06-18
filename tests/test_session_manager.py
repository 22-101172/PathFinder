from __future__ import annotations
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

def make_student_context(student_id: str = "S001", completed_courses: list[str] | None = None) -> StudentContext:
    return StudentContext(
        student_id=student_id,
        name="Test Student",
        program="CS",
        track_id="ai",
        level=3,
        first_semester="2022-Fall",
        study_status="regular",
        total_credit_hours_earned=60,
        completed_courses=completed_courses or [],
    )


def make_session_state(student_id: str = "S001", session_id: str | None = None) -> SessionState:
    return SessionState(
        session_id=session_id or str(uuid.uuid4()),
        student_id=student_id,
        session_name="Test Session",
        student_context=make_student_context(student_id),
    )


def make_sq(
    added_courses: list[str] | None = None,
    course_override_type: str = "none",
    override_action: str = "accumulate",
    target_role: str | None = None,
    entities: EntitySet | None = None,
) -> StructuredQuery:
    return StructuredQuery(
        intent="test",
        engine_pattern="test",
        query_type="test",
        entities=entities or EntitySet(),
        session_overrides=SessionOverrides(
            added_courses=added_courses or [],
            target_role=target_role,
            course_override_type=course_override_type,
            override_action=override_action,
        ),
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


# ---------------------------------------------------------------------------
# Group 1 — SQLiteSessionStore isolation
# ---------------------------------------------------------------------------

def test_save_and_load(store):
    session = make_session_state()
    store.save(session)
    loaded = store.load(session.session_id)
    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert loaded.student_id == session.student_id
    assert loaded.session_name == session.session_name


def test_load_nonexistent(store):
    assert store.load("does-not-exist") is None


def test_delete_existing(store):
    session = make_session_state()
    store.save(session)
    assert store.delete(session.session_id) is True
    assert store.load(session.session_id) is None


def test_delete_nonexistent(store):
    assert store.delete("does-not-exist") is False


def test_get_all_for_student(store):
    ctx = make_student_context("S999")
    for i in range(3):
        s = SessionState(
            session_id=f"ses-{i}",
            student_id="S999",
            session_name=f"Session {i}",
            student_context=ctx,
        )
        store.save(s)
        time.sleep(0.02)

    result = store.get_all_for_student("S999")
    assert len(result) == 3
    assert result[0].session_id == "ses-2"
    assert result[2].session_id == "ses-0"


def test_delete_all(store):
    ctx = make_student_context()
    for i in range(3):
        store.save(SessionState(
            session_id=f"del-{i}",
            student_id="S001",
            session_name=f"Session {i}",
            student_context=ctx,
        ))
    count = store.delete_all()
    assert count == 3
    assert store.get_all_for_student("S001") == []


# ---------------------------------------------------------------------------
# Group 2 — Override logic
# ---------------------------------------------------------------------------

def test_accumulate_courses(patched_sm):
    ctx = make_student_context()
    state, _ = sm.get_or_create_session(None, "S001", ctx, "first message")
    sid = state.session_id

    sm.apply_query_result(sid, make_sq(["C-AI311"], course_override_type="planned"))
    sm.apply_query_result(sid, make_sq(["C-CS301"], course_override_type="planned"))

    session = sm._store.load(sid)
    assert "C-AI311" in session.overrides.added_courses
    assert "C-CS301" in session.overrides.added_courses


def test_replace_courses(patched_sm):
    ctx = make_student_context()
    state, _ = sm.get_or_create_session(None, "S001", ctx, "first message")
    sid = state.session_id

    sm.apply_query_result(sid, make_sq(["C-AI311"], course_override_type="planned"))
    sm.apply_query_result(sid, make_sq(["C-CS301"], course_override_type="planned", override_action="replace"))

    session = sm._store.load(sid)
    assert session.overrides.added_courses == ["C-CS301"]
    assert "C-AI311" not in session.overrides.added_courses


def test_clear_overrides(patched_sm):
    ctx = make_student_context()
    state, _ = sm.get_or_create_session(None, "S001", ctx, "first message")
    sid = state.session_id

    sm.apply_query_result(sid, make_sq(["C-AI311"], course_override_type="planned", target_role="SWE"))
    sm.apply_query_result(sid, make_sq(override_action="clear"))

    session = sm._store.load(sid)
    assert session.overrides.added_courses == []
    assert session.overrides.target_role is None
    assert session.overrides.course_override_type == "none"


def test_assumed_done_merges_into_completed(patched_sm):
    ctx = make_student_context(completed_courses=["C-CS101"])
    state, _ = sm.get_or_create_session(None, "S001", ctx, "first message")
    sid = state.session_id

    sm.apply_query_result(sid, make_sq(["C-AI311"], course_override_type="assumed_done"))

    session = sm._store.load(sid)
    effective = sm.build_effective_context(session.student_context, session.overrides)

    assert "C-AI311" in effective.completed_courses
    assert "C-CS101" in effective.completed_courses


def test_planned_merges_into_in_progress(patched_sm):
    ctx = make_student_context(completed_courses=["C-CS101"])
    state, _ = sm.get_or_create_session(None, "S001", ctx, "first message")
    sid = state.session_id

    sm.apply_query_result(sid, make_sq(["C-AI311"], course_override_type="planned"))

    session = sm._store.load(sid)
    effective = sm.build_effective_context(session.student_context, session.overrides)

    assert "C-AI311" in effective.in_progress_courses
    assert "C-AI311" not in effective.completed_courses
    assert "C-CS101" in effective.completed_courses


def test_last_referenced_accumulates(patched_sm):
    ctx = make_student_context()
    state, _ = sm.get_or_create_session(None, "S001", ctx, "first message")
    sid = state.session_id

    sm.apply_query_result(sid, make_sq(entities=EntitySet(course_code="C-AI311")))
    sm.apply_query_result(sid, make_sq(entities=EntitySet(role_id="SWE")))

    session = sm._store.load(sid)
    assert session.last_referenced.course_code == "C-AI311"
    assert session.last_referenced.role_id == "SWE"


def test_assumed_failed_moves_course_to_failed(patched_sm):
    ctx = make_student_context(completed_courses=["C-CS101", "C-MA111"])
    state, _ = sm.get_or_create_session(None, "S001", ctx, "first message")
    sid = state.session_id

    sq = StructuredQuery(
        intent="test",
        engine_pattern="test",
        query_type="test",
        entities=EntitySet(),
        session_overrides=SessionOverrides(
            assumed_failed_courses=["C-MA111"],
            course_override_type="assumed_failed",
            override_action="accumulate",
        ),
    )
    sm.apply_query_result(sid, sq)

    session = sm._store.load(sid)
    effective = sm.build_effective_context(session.student_context, session.overrides)

    assert "C-MA111" in effective.failed_courses
    assert "C-MA111" not in effective.completed_courses
    assert "C-CS101" in effective.completed_courses


def test_assumed_passed_moves_course_to_completed(patched_sm):
    ctx = make_student_context(completed_courses=["C-CS101"])
    # Manually inject a failed course into context
    ctx = ctx.model_copy(update={"failed_courses": ["C-MA111"]})
    state, _ = sm.get_or_create_session(None, "S001", ctx, "first message")
    sid = state.session_id

    sq = StructuredQuery(
        intent="test",
        engine_pattern="test",
        query_type="test",
        entities=EntitySet(),
        session_overrides=SessionOverrides(
            assumed_passed_courses=["C-MA111"],
            course_override_type="assumed_passed",
            override_action="accumulate",
        ),
    )
    sm.apply_query_result(sid, sq)

    session = sm._store.load(sid)
    effective = sm.build_effective_context(session.student_context, session.overrides)

    assert "C-MA111" in effective.completed_courses
    assert "C-MA111" not in effective.failed_courses
    assert "C-CS101" in effective.completed_courses


# ---------------------------------------------------------------------------
# Group 3 — Session lifecycle integration
# ---------------------------------------------------------------------------

def test_create_new_session(patched_sm):
    ctx = make_student_context()
    state, is_new = sm.get_or_create_session(None, "S001", ctx, "Hello world")
    assert is_new is True
    assert state.session_id is not None
    assert patched_sm.load(state.session_id) is not None


def test_load_existing_session(patched_sm):
    ctx = make_student_context()
    state, _ = sm.get_or_create_session(None, "S001", ctx, "Hello world")
    sid = state.session_id

    state2, is_new = sm.get_or_create_session(sid, "S001", ctx, "Hello again")
    assert is_new is False
    assert state2.session_id == sid


def test_stale_session_id(patched_sm):
    ctx = make_student_context()
    state, is_new = sm.get_or_create_session("nonexistent-id-xyz", "S001", ctx, "Hello")
    assert is_new is True
    assert state.session_id != "nonexistent-id-xyz"


def test_full_turn_lifecycle(patched_sm):
    ctx = make_student_context()
    state, _ = sm.get_or_create_session(None, "S001", ctx, "What courses should I take?")
    sid = state.session_id

    qu = sm.get_qu_context(sid, "What courses should I take?")
    assert qu is not None
    assert qu.user_text == "What courses should I take?"

    sm.apply_query_result(sid, make_sq())

    sm.update_session_after_turn(sid, "What courses should I take?", "You should take C-AI311.")

    history = sm.get_session_history(sid)
    assert history is not None
    assert len(history.turns) == 1
    assert history.turns[0]["user"] == "What courses should I take?"
    assert history.turns[0]["answer"] == "You should take C-AI311."


def test_qu_context_window(patched_sm):
    ctx = make_student_context()
    state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
    sid = state.session_id

    session = patched_sm.load(sid)
    for i in range(8):
        session.turn_history.append({"user": f"q{i}", "answer": f"a{i}"})
    patched_sm.save(session)

    qu = sm.get_qu_context(sid, "new question")
    assert qu is not None
    assert len(qu.recent_turns) == sm.QU_CONTEXT_TURNS
    assert qu.recent_turns[0]["user"] == f"q{8 - sm.QU_CONTEXT_TURNS}"
    assert qu.recent_turns[-1]["user"] == "q7"


def test_delete_session(patched_sm):
    ctx = make_student_context()
    state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
    sid = state.session_id

    result = sm.delete_session(sid)
    assert result is True
    assert sm.get_session_history(sid) is None


# ---------------------------------------------------------------------------
# Group 4 — merge_turn_overrides
# ---------------------------------------------------------------------------

def _ovr(added=None, passed=None, failed=None, override_type="none", action="accumulate"):
    return SessionOverrides(
        added_courses=added or [],
        assumed_passed_courses=passed or [],
        assumed_failed_courses=failed or [],
        course_override_type=override_type,
        override_action=action,
    )


def _msq(intent="test", overrides=None):
    return StructuredQuery(
        intent=intent,
        original_text=intent,
        entities=EntitySet(),
        session_overrides=overrides or _ovr(),
    )


def test_merge_turn_overrides_empty_list():
    merged, had_clear = sm.merge_turn_overrides([])
    assert merged.added_courses == []
    assert merged.assumed_passed_courses == []
    assert merged.assumed_failed_courses == []
    assert had_clear is False


def test_merge_turn_overrides_single_sq_accumulate():
    sqs = [_msq(overrides=_ovr(added=["C-AI311"], override_type="planned"))]
    merged, had_clear = sm.merge_turn_overrides(sqs)
    assert "C-AI311" in merged.added_courses
    assert merged.course_override_type == "planned"
    assert had_clear is False


def test_merge_turn_overrides_multiple_sqs_union():
    sqs = [
        _msq(overrides=_ovr(added=["C-AI311"], override_type="planned")),
        _msq(overrides=_ovr(added=["C-CS301"], override_type="planned")),
    ]
    merged, had_clear = sm.merge_turn_overrides(sqs)
    assert "C-AI311" in merged.added_courses
    assert "C-CS301" in merged.added_courses
    assert had_clear is False


def test_merge_turn_overrides_clear_action_detected():
    sqs = [
        _msq(overrides=_ovr(added=["C-AI311"], override_type="planned")),
        _msq(overrides=_ovr(action="clear")),
    ]
    merged, had_clear = sm.merge_turn_overrides(sqs)
    assert had_clear is True
    assert merged.added_courses == []
    assert merged.course_override_type == "none"


def test_merge_turn_overrides_mixed_override_types():
    sqs = [
        _msq(overrides=_ovr(passed=["C-AI101"], override_type="assumed_passed")),
        _msq(overrides=_ovr(failed=["C-CS301"], override_type="assumed_failed")),
    ]
    merged, had_clear = sm.merge_turn_overrides(sqs)
    assert "C-AI101" in merged.assumed_passed_courses
    assert "C-CS301" in merged.assumed_failed_courses
    assert had_clear is False


# ---------------------------------------------------------------------------
# Group 5 — update_session_after_turn with replace_overrides
# ---------------------------------------------------------------------------

def test_update_session_after_turn_default_merges(patched_sm):
    ctx = make_student_context()
    state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
    sid = state.session_id

    # Seed existing overrides
    session = patched_sm.load(sid)
    session.overrides = _ovr(added=["C-AI101"], override_type="planned")
    patched_sm.save(session)

    # update with additional courses — should merge
    sm.update_session_after_turn(
        sid, "q", "a",
        new_overrides=_ovr(added=["C-AI311"], override_type="planned"),
        replace_overrides=False,
    )

    loaded = patched_sm.load(sid)
    assert "C-AI101" in loaded.overrides.added_courses
    assert "C-AI311" in loaded.overrides.added_courses


def test_update_session_after_turn_replace_overrides_true(patched_sm):
    ctx = make_student_context()
    state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
    sid = state.session_id

    # Seed existing overrides with planned courses
    session = patched_sm.load(sid)
    session.overrides = _ovr(added=["C-AI101", "C-AI201"], override_type="planned")
    patched_sm.save(session)

    # update with replace_overrides=True (clear action was issued)
    sm.update_session_after_turn(
        sid, "clear q", "a",
        new_overrides=SessionOverrides(),
        replace_overrides=True,
    )

    loaded = patched_sm.load(sid)
    assert loaded.overrides.added_courses == []
    assert loaded.overrides.course_override_type == "none"


def test_update_session_after_turn_turn_history_appended(patched_sm):
    ctx = make_student_context()
    state, _ = sm.get_or_create_session(None, "S001", ctx, "start")
    sid = state.session_id

    sm.update_session_after_turn(sid, "my question", "my answer")

    loaded = patched_sm.load(sid)
    assert len(loaded.turn_history) == 1
    assert loaded.turn_history[0]["user"] == "my question"
    assert loaded.turn_history[0]["answer"] == "my answer"


# ---------------------------------------------------------------------------
# Group 4 — Schema compatibility
# ---------------------------------------------------------------------------

def test_session_overrides_schema_has_assumed_failed():
    ovr = SessionOverrides(assumed_failed_courses=["C-AI321"])
    assert ovr.assumed_failed_courses == ["C-AI321"]
    dumped = ovr.model_dump_json()
    reloaded = SessionOverrides.model_validate_json(dumped)
    assert reloaded.assumed_failed_courses == ["C-AI321"]


def test_session_overrides_schema_has_assumed_passed():
    ovr = SessionOverrides(assumed_passed_courses=["C-AI321"])
    assert ovr.assumed_passed_courses == ["C-AI321"]
    dumped = ovr.model_dump_json()
    reloaded = SessionOverrides.model_validate_json(dumped)
    assert reloaded.assumed_passed_courses == ["C-AI321"]


def test_session_overrides_accepts_assumed_failed_type():
    ovr = SessionOverrides(course_override_type="assumed_failed")
    assert ovr.course_override_type == "assumed_failed"


def test_session_overrides_accepts_assumed_passed_type():
    ovr = SessionOverrides(course_override_type="assumed_passed")
    assert ovr.course_override_type == "assumed_passed"


# ---------------------------------------------------------------------------
# Group 5 — Override cleanup regression tests (bugs #1, #2, #3)
# ---------------------------------------------------------------------------

def test_assumed_done_cleans_failed_courses():
    ctx = make_student_context()
    ctx = ctx.model_copy(update={"failed_courses": ["C-AI321"]})
    overrides = SessionOverrides(added_courses=["C-AI321"], course_override_type="assumed_done")

    effective = sm.build_effective_context(ctx, overrides)

    assert "C-AI321" in effective.completed_courses, "assumed_done must add to completed_courses"
    assert "C-AI321" not in effective.failed_courses, "assumed_done must remove from failed_courses"


def test_assumed_done_cleans_in_progress_courses():
    ctx = make_student_context()
    ctx = ctx.model_copy(update={"in_progress_courses": ["C-AI321"]})
    overrides = SessionOverrides(added_courses=["C-AI321"], course_override_type="assumed_done")

    effective = sm.build_effective_context(ctx, overrides)

    assert "C-AI321" in effective.completed_courses, "assumed_done must add to completed_courses"
    assert "C-AI321" not in effective.in_progress_courses, "assumed_done must remove from in_progress_courses"


def test_assumed_passed_cleans_in_progress_courses():
    ctx = make_student_context()
    ctx = ctx.model_copy(update={"in_progress_courses": ["C-AI321"]})
    overrides = SessionOverrides(assumed_passed_courses=["C-AI321"], course_override_type="assumed_passed")

    effective = sm.build_effective_context(ctx, overrides)

    assert "C-AI321" in effective.completed_courses, "assumed_passed must add to completed_courses"
    assert "C-AI321" not in effective.in_progress_courses, "assumed_passed must remove from in_progress_courses"


# ---------------------------------------------------------------------------
# Group 6 — Corrupted blob handling (bugs #4, #5)
# ---------------------------------------------------------------------------

def test_load_corrupted_blob_returns_none(store):
    import sqlite3 as _sqlite3
    sid = "corrupted-session-001"
    with _sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, student_id, session_name, last_updated, session_blob) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, "S001", "Test", "2026-01-01T00:00:00+00:00", "{not valid json at all!!!"),
        )

    result = store.load(sid)
    assert result is None, "load() must return None for a corrupted blob, not raise"


def test_get_all_for_student_skips_corrupted_blobs(store):
    import sqlite3 as _sqlite3
    import time

    ctx = make_student_context("S002")
    valid_session = SessionState(
        session_id="valid-session-001",
        student_id="S002",
        session_name="Valid",
        student_context=ctx,
    )
    store.save(valid_session)
    time.sleep(0.02)

    with _sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, student_id, session_name, last_updated, session_blob) "
            "VALUES (?, ?, ?, ?, ?)",
            ("corrupted-session-002", "S002", "Corrupted", "2026-06-14T00:00:00+00:00", "{bad blob}"),
        )

    results = store.get_all_for_student("S002")
    assert len(results) == 1, "Corrupted blob must be skipped, valid blob must be returned"
    assert results[0].session_id == "valid-session-001"
