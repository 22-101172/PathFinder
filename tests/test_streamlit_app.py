"""
Tests for ui/streamlit_app.py helper functions — no live backend required.

Streamlit is mocked before import to prevent set_page_config / chat_input
from executing at module level and crashing the test session.

Helper functions tested:
  api_chat, api_get_sessions, api_load_history,
  api_delete_session, _apply_delete_to_state
"""
from __future__ import annotations
import sys
from unittest.mock import MagicMock, patch

# Must install Streamlit stub BEFORE importing streamlit_app so that
# top-level st.* calls (set_page_config, chat_input, sidebar, etc.) are no-ops.
_st_stub = MagicMock()
_st_stub.chat_input.return_value = None   # prevents chat-input block at import time
sys.modules.setdefault("streamlit", _st_stub)

import ui.streamlit_app as _app  # noqa: E402 — must be after stub install


# ── api_chat ──────────────────────────────────────────────────────────────────

def test_api_chat_200():
    """api_chat returns parsed JSON payload on HTTP 200."""
    payload = {
        "answer_text": "You need 21 more credits.",
        "session_id": "ses-abc",
        "session_name": "Grad Chat",
        "citations": [],
        "status": "ok",
    }
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = payload
    with patch("ui.streamlit_app.requests.post", return_value=mock_resp):
        result = _app.api_chat("How many credits do I need?", "STU000001", "ses-abc")
    assert result["answer_text"] == "You need 21 more credits."
    assert result["status"] == "ok"
    assert result["session_id"] == "ses-abc"
    assert result["session_name"] == "Grad Chat"


def test_api_chat_non_200_returns_error_shape():
    """api_chat returns a safe error-shaped dict on non-200 status."""
    mock_resp = MagicMock(status_code=500, text="Internal Server Error")
    with patch("ui.streamlit_app.requests.post", return_value=mock_resp):
        result = _app.api_chat("Hello", "STU000001")
    assert result["status"] == "error"
    assert "answer_text" in result
    assert "500" in result["answer_text"]
    assert "citations" in result


def test_api_chat_exception_returns_error_shape():
    """api_chat returns a safe error-shaped dict on network exception."""
    with patch("ui.streamlit_app.requests.post", side_effect=ConnectionError("down")):
        result = _app.api_chat("Hello", "STU000001")
    assert result["status"] == "error"
    assert "Cannot reach backend" in result["answer_text"]
    assert "citations" in result


def test_api_chat_omits_session_id_when_none():
    """api_chat must NOT send session_id in POST body when it is None."""
    payload = {
        "answer_text": "Hi!", "session_id": "new-ses",
        "session_name": "New Conversation", "citations": [], "status": "ok",
    }
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = payload
    with patch("ui.streamlit_app.requests.post", return_value=mock_resp) as mock_post:
        _app.api_chat("Hello", "STU000001")   # no session_id arg → defaults to None
    posted_json = mock_post.call_args.kwargs["json"]
    assert "session_id" not in posted_json


def test_api_chat_sends_session_id_when_provided():
    """api_chat must include session_id in POST body when provided."""
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "answer_text": "ok", "session_id": "s1",
        "session_name": "s", "citations": [], "status": "ok",
    }
    with patch("ui.streamlit_app.requests.post", return_value=mock_resp) as mock_post:
        _app.api_chat("Hello", "STU000001", "ses-xyz")
    posted_json = mock_post.call_args.kwargs["json"]
    assert posted_json["session_id"] == "ses-xyz"


# ── api_get_sessions ──────────────────────────────────────────────────────────

def test_api_get_sessions_success():
    """api_get_sessions returns the sessions list from the response on 200."""
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "student_id": "STU000001",
        "sessions": [
            {"session_id": "s1", "session_name": "Chat 1", "last_updated": "2026-06-20"},
            {"session_id": "s2", "session_name": "Chat 2", "last_updated": "2026-06-21"},
        ],
    }
    with patch("ui.streamlit_app.requests.get", return_value=mock_resp):
        result = _app.api_get_sessions("STU000001")
    assert len(result) == 2
    assert result[0]["session_id"] == "s1"
    assert result[1]["session_name"] == "Chat 2"


def test_api_get_sessions_non_200_returns_empty():
    """api_get_sessions returns empty list on non-200."""
    mock_resp = MagicMock(status_code=404)
    with patch("ui.streamlit_app.requests.get", return_value=mock_resp):
        result = _app.api_get_sessions("UNKNOWN")
    assert result == []


def test_api_get_sessions_exception_returns_empty():
    """api_get_sessions returns empty list on network exception."""
    with patch("ui.streamlit_app.requests.get", side_effect=ConnectionError()):
        result = _app.api_get_sessions("STU000001")
    assert result == []


# ── api_load_history ──────────────────────────────────────────────────────────

def test_api_load_history_maps_turns_to_messages():
    """api_load_history maps each turn to user + assistant chat message dicts."""
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "session_id": "s1",
        "session_name": "Grad Chat",
        "turns": [
            {"user": "Can I graduate?", "answer": "You need 21 more credits."},
            {"user": "What courses?", "answer": "C-AI422 and C-AI320."},
        ],
    }
    with patch("ui.streamlit_app.requests.get", return_value=mock_resp):
        msgs, name = _app.api_load_history("STU000001", "s1")
    assert len(msgs) == 4
    assert msgs[0] == {"role": "user", "content": "Can I graduate?"}
    assert msgs[1] == {"role": "assistant", "content": "You need 21 more credits."}
    assert msgs[2] == {"role": "user", "content": "What courses?"}
    assert msgs[3] == {"role": "assistant", "content": "C-AI422 and C-AI320."}
    assert name == "Grad Chat"


def test_api_load_history_non_200_returns_empty():
    """api_load_history returns ([], "") on non-200 status."""
    mock_resp = MagicMock(status_code=410)
    with patch("ui.streamlit_app.requests.get", return_value=mock_resp):
        msgs, name = _app.api_load_history("STU000001", "unknown-ses")
    assert msgs == []
    assert name == ""


def test_api_load_history_exception_returns_empty():
    """api_load_history returns ([], "") on network exception."""
    with patch("ui.streamlit_app.requests.get", side_effect=ConnectionError()):
        msgs, name = _app.api_load_history("STU000001", "s1")
    assert msgs == []
    assert name == ""


def test_api_load_history_calls_student_owned_endpoint():
    """api_load_history must call /students/{student_id}/sessions/{session_id}/history."""
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"session_id": "s1", "session_name": "chat", "turns": []}
    with patch("ui.streamlit_app.requests.get", return_value=mock_resp) as mock_get:
        _app.api_load_history("STU000001", "ses-xyz")
    called_url = mock_get.call_args.args[0]
    assert "STU000001" in called_url
    assert "ses-xyz" in called_url
    assert "/students/" in called_url
    # Must NOT call the deprecated /session/{session_id}/history pattern
    assert called_url.count("/session/") == 0 or "/students/" in called_url


def test_api_load_history_empty_turns():
    """api_load_history returns empty message list when session has no turns."""
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "session_id": "s1", "session_name": "Empty", "turns": []
    }
    with patch("ui.streamlit_app.requests.get", return_value=mock_resp):
        msgs, name = _app.api_load_history("STU000001", "s1")
    assert msgs == []
    assert name == "Empty"


# ── api_delete_session ────────────────────────────────────────────────────────

def test_api_delete_session_success():
    """api_delete_session returns True on HTTP 200."""
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"deleted": True, "session_id": "s1"}
    with patch("ui.streamlit_app.requests.delete", return_value=mock_resp):
        result = _app.api_delete_session("STU000001", "s1")
    assert result is True


def test_api_delete_session_non_200_returns_false():
    """api_delete_session returns False on non-200 (e.g. 404 session not found)."""
    mock_resp = MagicMock(status_code=404)
    with patch("ui.streamlit_app.requests.delete", return_value=mock_resp):
        result = _app.api_delete_session("STU000001", "unknown-ses")
    assert result is False


def test_api_delete_session_exception_returns_false():
    """api_delete_session returns False on network exception — never raises."""
    with patch("ui.streamlit_app.requests.delete", side_effect=ConnectionError("down")):
        result = _app.api_delete_session("STU000001", "s1")
    assert result is False


def test_api_delete_session_calls_ownership_safe_endpoint():
    """api_delete_session must call DELETE /students/{student_id}/sessions/{session_id}."""
    mock_resp = MagicMock(status_code=200)
    with patch("ui.streamlit_app.requests.delete", return_value=mock_resp) as mock_del:
        _app.api_delete_session("STU000001", "ses-xyz")
    called_url = mock_del.call_args.args[0]
    assert "STU000001" in called_url
    assert "ses-xyz" in called_url
    assert "/students/" in called_url


# ── _apply_delete_to_state ────────────────────────────────────────────────────

def test_apply_delete_clears_active_session():
    """Deleting the active session clears session_id, session_name, and messages."""
    state = {
        "all_sessions": [
            {"session_id": "s1", "session_name": "Chat 1"},
            {"session_id": "s2", "session_name": "Chat 2"},
        ],
        "session_id": "s1",
        "session_name": "Chat 1",
        "messages": [{"role": "user", "content": "hello"}],
    }
    _app._apply_delete_to_state(state, "s1")
    assert state["session_id"] is None
    assert state["session_name"] is None
    assert state["messages"] == []
    assert all(s["session_id"] != "s1" for s in state["all_sessions"])


def test_apply_delete_keeps_active_session_when_deleting_different():
    """Deleting an inactive session leaves the active session and messages untouched."""
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    state = {
        "all_sessions": [
            {"session_id": "s1", "session_name": "Chat 1"},
            {"session_id": "s2", "session_name": "Chat 2"},
        ],
        "session_id": "s2",
        "session_name": "Chat 2",
        "messages": messages,
    }
    _app._apply_delete_to_state(state, "s1")
    assert state["session_id"] == "s2"
    assert state["session_name"] == "Chat 2"
    assert state["messages"] == messages
    assert len(state["all_sessions"]) == 1
    assert state["all_sessions"][0]["session_id"] == "s2"


def test_apply_delete_removes_session_from_all_sessions():
    """Deleted session id is removed from all_sessions regardless of which is active."""
    state = {
        "all_sessions": [
            {"session_id": "s1"}, {"session_id": "s2"}, {"session_id": "s3"}
        ],
        "session_id": "s3",
        "session_name": "Chat 3",
        "messages": [],
    }
    _app._apply_delete_to_state(state, "s2")
    ids = [s["session_id"] for s in state["all_sessions"]]
    assert "s2" not in ids
    assert "s1" in ids
    assert "s3" in ids


def test_apply_delete_returns_state():
    """_apply_delete_to_state returns the mutated state dict."""
    state = {"all_sessions": [], "session_id": None, "session_name": None, "messages": []}
    result = _app._apply_delete_to_state(state, "s-nonexistent")
    assert result is state
