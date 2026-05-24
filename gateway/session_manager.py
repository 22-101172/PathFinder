from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from gateway.models.schemas import (
    LastReferenced, SessionOverrides, SessionState,
    SessionSummary, StudentContext, StudentSessionsResponse, SessionHistoryResponse,
)

logger = logging.getLogger(__name__)

_sessions: dict[str, SessionState] = {}
_student_sessions: dict[str, list[str]] = {}
_session_timestamps: dict[str, str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_name(text: str) -> str:
    t = text.strip()
    return (t[:40] + "...") if len(t) > 40 else t


def create_session(student_id: str, context: StudentContext, first_message: str) -> SessionState:
    sid = str(uuid.uuid4())
    state = SessionState(
        session_id=sid,
        student_id=student_id,
        session_name=_make_name(first_message),
        student_context=context,
    )
    _sessions[sid] = state
    _session_timestamps[sid] = _now()
    _student_sessions.setdefault(student_id, []).append(sid)
    logger.info("SessionManager: created session %s for %s", sid, student_id)
    return state


def get_session(session_id: str) -> Optional[SessionState]:
    s = _sessions.get(session_id)
    if not s:
        logger.warning("SessionManager: session %s not found", session_id)
    return s


def get_student_sessions(student_id: str) -> StudentSessionsResponse:
    ids = _student_sessions.get(student_id, [])
    summaries = []
    for sid in reversed(ids):
        state = _sessions.get(sid)
        if state:
            summaries.append(SessionSummary(
                session_id=sid,
                session_name=state.session_name,
                last_updated=_session_timestamps.get(sid, _now()),
            ))
    return StudentSessionsResponse(student_id=student_id, sessions=summaries)


def get_session_history(session_id: str) -> Optional[SessionHistoryResponse]:
    state = _sessions.get(session_id)
    if not state:
        return None
    return SessionHistoryResponse(
        session_id=session_id,
        session_name=state.session_name,
        turns=state.turn_history,
    )


def update_session_after_turn(
    session_id: str,
    user_text: str,
    answer_text: str,
    new_overrides: Optional[SessionOverrides] = None,
    new_last_referenced: Optional[LastReferenced] = None,
) -> None:
    state = _sessions.get(session_id)
    if not state:
        return

    state.turn_history.append({"user": user_text, "answer": answer_text})

    if new_overrides:
        if new_overrides.added_courses:
            merged = list(set(state.overrides.added_courses) | set(new_overrides.added_courses))
            state.overrides.added_courses = merged
            state.student_context.planned_courses = merged
        if new_overrides.target_role:
            state.overrides.target_role = new_overrides.target_role

    if new_last_referenced:
        state.last_referenced = new_last_referenced

    _session_timestamps[session_id] = _now()
    logger.debug("SessionManager: updated session %s", session_id)
