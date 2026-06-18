"""
Tests for main.py / FastAPI /chat endpoint wiring.

All external services (KG, RAG, ALE, SCP, QU, session manager) are mocked
so tests run without any real infrastructure.

Verifies:
- /chat returns TurnResponse (not QueryResponse)
- execute_turn is called with list[StructuredQuery]
- answer_text stored in turn history is a structured summary, not empty string
- student-not-found → 404
- /health returns {"status": "ok"}
- /sessions and /session history endpoints still work
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gateway.models.schemas import (
    EntitySet, LastReferenced, PerSQResult,
    SessionOverrides, SessionState, StudentContext,
    StructuredQuery, TurnWrapper, QueryResponse,
    SessionSummary, StudentSessionsResponse, SessionHistoryResponse,
)


# ── Shared test data factories ─────────────────────────────────────────────────

def _make_student() -> StudentContext:
    return StudentContext(
        student_id="S001", name="Test Student",
        program="Artificial Intelligence", track_id="AI",
        level=3, first_semester="Fall 2022", study_status="Studying",
        total_credit_hours_earned=60, cgpa=3.0,
        cumulative_chs=60, cumulative_cps=180.0,
    )


def _make_session(session_id: str | None = None) -> SessionState:
    return SessionState(
        session_id=session_id or str(uuid.uuid4()),
        student_id="S001",
        session_name="Test Chat",
        student_context=_make_student(),
    )


def _make_sqs() -> list[StructuredQuery]:
    return [StructuredQuery(
        intent="get_course_info",
        original_text="Tell me about C-CS301",
        entities=EntitySet(course_code="C-CS301"),
    )]


def _make_tw(session_id: str = "sid-001") -> TurnWrapper:
    return TurnWrapper(
        turn_id="t-001",
        session_id=session_id,
        timestamp="2026-06-16T00:00:00+00:00",
        results=[PerSQResult(
            sq_index=0, intent="get_course_info", status="success",
            data={"course_code": "C-CS301", "name": "Data Structures"},
        )],
        result_count=1,
        turn_status="completed",
        has_error=False, has_clarification=False,
        has_informational=False, has_soft_no_evidence=False,
    )


# ── Fixture ────────────────────────────────────────────────────────────────────

@pytest.fixture
def api(monkeypatch):
    """
    Yield a configured TestClient with all external dependencies mocked.
    Also yields the MagicMock objects so tests can inspect calls.
    """
    session_id = "test-session-001"
    session = _make_session(session_id)
    sqs = _make_sqs()
    tw = _make_tw(session_id)

    mock_kg = MagicMock()
    mock_kg._client = None  # simulate KG unavailable → resolver=None
    mock_rag = MagicMock()
    mock_rag.get_rule_bundles.return_value = {"grading_scale_rules": MagicMock()}
    mock_ale = MagicMock()

    mock_orch = MagicMock()
    mock_orch.execute_turn.return_value = tw
    mock_orch.extract_last_referenced.return_value = LastReferenced(course_code="C-CS301")

    mock_composer = MagicMock()
    mock_composer.compose.return_value = QueryResponse(
        session_id=session_id,
        session_name="Test Chat",
        answer_text="Data Structures (C-CS301) is a 3-credit course covering core algorithms.",
        citations=[],
        status="ok",
    )

    with patch("main.KGAdapter", return_value=mock_kg), \
         patch("main.RAGAdapter", return_value=mock_rag), \
         patch("main.ALEAdapter", return_value=mock_ale), \
         patch("main.Orchestrator", return_value=mock_orch), \
         patch("main.ResponseComposer", return_value=mock_composer), \
         patch("main.load_excel"), \
         patch("main.get_context", return_value=_make_student()) as p_gc, \
         patch("main.get_or_create_session",
               return_value=(session, True)) as p_gos, \
         patch("main.understand_query", return_value=sqs) as p_uq, \
         patch("main.merge_turn_overrides",
               return_value=(SessionOverrides(), False)) as p_mto, \
         patch("main.update_session_after_turn") as p_usat, \
         patch("main.get_student_sessions",
               return_value=StudentSessionsResponse(student_id="S001", sessions=[])) as p_gss, \
         patch("main.get_session_history",
               return_value=SessionHistoryResponse(
                   session_id=session_id, session_name="Test Chat", turns=[]
               )) as p_gsh:

        import main as m
        with TestClient(m.app) as client:
            yield {
                "client": client,
                "session": session,
                "session_id": session_id,
                "sqs": sqs,
                "tw": tw,
                "mock_orch": mock_orch,
                "mock_composer": mock_composer,
                "p_uq": p_uq,
                "p_usat": p_usat,
                "p_mto": p_mto,
                "p_gos": p_gos,
                "p_gc": p_gc,
                "p_gss": p_gss,
                "p_gsh": p_gsh,
            }


# ── /health ────────────────────────────────────────────────────────────────────

def test_health(api):
    resp = api["client"].get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "PathFinder"}


# ── /chat — response shape ─────────────────────────────────────────────────────

def test_chat_returns_query_response(api):
    """Response is QueryResponse: session_id, session_name, answer_text, citations, status."""
    resp = api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "session_id" in data
    assert "session_name" in data
    assert "answer_text" in data
    assert "citations" in data
    assert "status" in data
    # TurnWrapper is internal; not exposed at this endpoint anymore
    assert "turn" not in data


def test_chat_query_response_shape(api):
    """QueryResponse fields must have the correct types and values."""
    resp = api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    data = resp.json()
    assert isinstance(data["answer_text"], str)
    assert len(data["answer_text"]) > 0
    assert isinstance(data["citations"], list)
    assert data["status"] in ("ok", "error", "clarification_needed")


def test_chat_session_metadata(api):
    """session_id and session_name must match the session."""
    resp = api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    data = resp.json()
    assert data["session_id"] == api["session_id"]
    assert data["session_name"] == api["session"].session_name


# ── /chat — Composer wiring ───────────────────────────────────────────────────

def test_chat_calls_composer_compose(api):
    """compose() must be called once with user_text, turn, session_id, session_name."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    mock_composer = api["mock_composer"]
    assert mock_composer.compose.call_count == 1
    kwargs = mock_composer.compose.call_args.kwargs
    assert kwargs["user_text"] == "Tell me about C-CS301"
    assert "turn" in kwargs
    assert kwargs["session_id"] == api["session_id"]
    assert kwargs["session_name"] == api["session"].session_name


def test_chat_composer_receives_turn_wrapper(api):
    """compose() must receive the TurnWrapper returned by execute_turn."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    kwargs = api["mock_composer"].compose.call_args.kwargs
    assert isinstance(kwargs["turn"], TurnWrapper)


# ── /chat — dispatch correctness ──────────────────────────────────────────────

def test_chat_calls_execute_turn_with_list(api):
    """execute_turn must receive list[StructuredQuery], not a single SQ."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    mock_orch = api["mock_orch"]
    assert mock_orch.execute_turn.called
    call_args = mock_orch.execute_turn.call_args
    sqs_arg = call_args[0][0]  # first positional arg
    assert isinstance(sqs_arg, list), f"expected list, got {type(sqs_arg)}"
    assert len(sqs_arg) == 1
    assert sqs_arg[0].intent == "get_course_info"


def test_chat_passes_rule_bundles_to_execute_turn(api):
    """execute_turn must receive a rule_bundles dict (can be empty for test)."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    call_args = api["mock_orch"].execute_turn.call_args
    rule_bundles_arg = call_args[0][2]  # third positional arg
    assert isinstance(rule_bundles_arg, dict)


def test_chat_calls_understand_query_with_resolver(api):
    """understand_query must be called with resolver kwarg (even if None for mocked KG)."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    p_uq = api["p_uq"]
    assert p_uq.called
    kwargs = p_uq.call_args.kwargs
    assert "resolver" in kwargs


def test_chat_passes_last_referenced_to_qu(api):
    """understand_query must receive last_referenced from the session."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    p_uq = api["p_uq"]
    kwargs = p_uq.call_args.kwargs
    assert "last_referenced" in kwargs
    assert isinstance(kwargs["last_referenced"], LastReferenced)


# ── /chat — session persistence ───────────────────────────────────────────────

def test_chat_calls_merge_turn_overrides(api):
    """merge_turn_overrides must be called with the sqs list."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    p_mto = api["p_mto"]
    assert p_mto.called
    sqs_arg = p_mto.call_args[0][0]
    assert isinstance(sqs_arg, list)


def test_chat_calls_update_session_after_turn(api):
    """update_session_after_turn must be called exactly once per request."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    assert api["p_usat"].call_count == 1


def test_chat_stores_composed_answer_in_history(api):
    """answer_text stored in turn history must be the Composer's non-empty answer text."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    call_kwargs = api["p_usat"].call_args.kwargs
    answer_text = call_kwargs.get("answer_text", "")
    assert isinstance(answer_text, str) and answer_text != "", (
        "answer_text passed to update_session_after_turn must be non-empty"
    )


def test_chat_passes_replace_overrides_flag(api):
    """replace_overrides kwarg must be passed to update_session_after_turn."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    call_kwargs = api["p_usat"].call_args.kwargs
    assert "replace_overrides" in call_kwargs, (
        "replace_overrides must be explicitly passed (not relying on default)"
    )


def test_chat_passes_new_last_referenced(api):
    """update_session_after_turn must receive the new_last_referenced from the orchestrator."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "user_text": "Tell me about C-CS301",
    })
    call_kwargs = api["p_usat"].call_args.kwargs
    assert "new_last_referenced" in call_kwargs
    assert isinstance(call_kwargs["new_last_referenced"], LastReferenced)


# ── /chat — error paths ────────────────────────────────────────────────────────

def test_chat_student_not_found_returns_404(api):
    """If get_context returns None, endpoint must respond 404."""
    with patch("main.get_context", return_value=None):
        resp = api["client"].post("/chat", json={
            "student_id": "UNKNOWN",
            "user_text": "Hello",
        })
    assert resp.status_code == 404


def test_chat_with_existing_session_id(api):
    """Passing a session_id sends it through to get_or_create_session."""
    api["client"].post("/chat", json={
        "student_id": "S001",
        "session_id": "existing-session-abc",
        "user_text": "Tell me about OS",
    })
    p_gos = api["p_gos"]
    assert p_gos.called
    call_args = p_gos.call_args
    assert call_args[0][0] == "existing-session-abc"  # session_id forwarded


# ── Other endpoints ────────────────────────────────────────────────────────────

def test_list_sessions(api):
    resp = api["client"].get("/sessions/S001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["student_id"] == "S001"
    assert "sessions" in data


def test_session_history(api):
    resp = api["client"].get(f"/session/{api['session_id']}/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == api["session_id"]
    assert "turns" in data


def test_session_history_not_found(api):
    with patch("main.get_session_history", return_value=None):
        resp = api["client"].get("/session/no-such-session/history")
    assert resp.status_code == 404


# ── _make_turn_history_summary helper ─────────────────────────────────────────

def test_make_turn_history_summary_contains_intents():
    from main import _make_turn_history_summary
    tw = _make_tw()
    summary = _make_turn_history_summary(tw)
    assert "get_course_info" in summary
    assert "success" in summary
    assert "completed" in summary


def test_make_turn_history_summary_multi_sq():
    from main import _make_turn_history_summary
    tw = TurnWrapper(
        turn_id="x", session_id="s", timestamp="2026-06-16T00:00:00Z",
        results=[
            PerSQResult(sq_index=0, intent="run_graduation_audit", status="success",
                        data={"can_graduate": False}),
            PerSQResult(sq_index=1, intent="generate_graduation_roadmap", status="success",
                        data={"roadmap": []}),
        ],
        result_count=2, turn_status="completed",
        has_error=False, has_clarification=False,
        has_informational=False, has_soft_no_evidence=False,
    )
    summary = _make_turn_history_summary(tw)
    assert "run_graduation_audit" in summary
    assert "generate_graduation_roadmap" in summary
    assert "completed" in summary
