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

    effective = sm.apply_query_result(
        sid, make_sq(["C-AI311"], course_override_type="assumed_done")
    )

    assert effective is not None
    assert "C-AI311" in effective.completed_courses
    assert "C-CS101" in effective.completed_courses


def test_planned_does_not_merge(patched_sm):
    ctx = make_student_context(completed_courses=["C-CS101"])
    state, _ = sm.get_or_create_session(None, "S001", ctx, "first message")
    sid = state.session_id

    effective = sm.apply_query_result(
        sid, make_sq(["C-AI311"], course_override_type="planned")
    )

    assert effective is not None
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

    effective = sm.apply_query_result(sid, make_sq())
    assert effective is not None

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
