from __future__ import annotations
import os
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from gateway.models.schemas import (
    LastReferenced, QUContext, SessionOverrides,
    SessionState, SessionSummary, SessionHistoryResponse,
    StudentContext, StudentSessionsResponse, StructuredQuery,
)
from gateway.session_store import SQLiteSessionStore

logger = logging.getLogger(__name__)

QU_CONTEXT_TURNS: int = int(os.getenv("QU_CONTEXT_TURNS", "5"))
SESSION_DB_PATH: str = os.getenv("SESSION_DB_PATH", "pathfinder_sessions.db")

_store = SQLiteSessionStore(SESSION_DB_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_session_name(text: str) -> str:
    t = text.strip()
    return (t[:40] + "...") if len(t) > 40 else t


def _apply_overrides(
    existing: SessionOverrides,
    incoming: SessionOverrides,
) -> SessionOverrides:
    """
    Merge incoming override with existing session overrides.

    Behavior:
    - "clear": return fresh empty SessionOverrides()
    - "replace": return incoming overrides only; override_action reset to "accumulate"
      This is a one-time operation. Next call will accumulate, not replace.
    - "accumulate" (default): union all course lists; incoming role/type win if non-None/non-none

    Design note: course_override_type tracks only the most recent non-"none" type.
    If a student "assumes passed C-AI321" then "plans C-SW222", the session's
    course_override_type becomes "planned" but both assumed_passed_courses and
    added_courses are accumulated. Orchestrator must call build_effective_context
    with care to apply the correct override type to the correct list.
    """
    action = incoming.override_action

    if action == "clear":
        return SessionOverrides()

    if action == "replace":
        return SessionOverrides(
            added_courses=list(incoming.added_courses),
            assumed_failed_courses=list(incoming.assumed_failed_courses),
            assumed_passed_courses=list(incoming.assumed_passed_courses),
            target_role=incoming.target_role,
            course_override_type=incoming.course_override_type,
            override_action="accumulate",
        )

    # accumulate (default)
    merged_courses = list(set(existing.added_courses) | set(incoming.added_courses))
    merged_failed = list(set(existing.assumed_failed_courses) | set(incoming.assumed_failed_courses))
    merged_passed = list(set(existing.assumed_passed_courses) | set(incoming.assumed_passed_courses))
    new_role = incoming.target_role if incoming.target_role is not None else existing.target_role
    new_type = (
        incoming.course_override_type
        if incoming.course_override_type != "none"
        else existing.course_override_type
    )
    return SessionOverrides(
        added_courses=merged_courses,
        assumed_failed_courses=merged_failed,
        assumed_passed_courses=merged_passed,
        target_role=new_role,
        course_override_type=new_type,
        override_action="accumulate",
    )


def _apply_last_referenced(
    existing: LastReferenced,
    entities: dict,
) -> LastReferenced:
    return LastReferenced(
        course_code=entities.get("course_code") or existing.course_code,
        role_id=entities.get("role_id") or existing.role_id,
        track_id=entities.get("track_id") or existing.track_id,
    )


def build_effective_context(
    base_context: StudentContext,
    overrides: SessionOverrides,
) -> StudentContext:
    """
    Apply session overrides to StudentContext. Never mutates base_context.

    Override types:
    - "assumed_done": union added_courses into completed_courses; remove from failed/in_progress
    - "planned": union added_courses into in_progress_courses
    - "assumed_failed": union assumed_failed_courses into failed_courses; remove from completed/zero_credit
    - "assumed_passed": union assumed_passed_courses into completed_courses; remove from failed/in_progress
    - "gpa_scenario": NOT HANDLED — returns base_context unchanged (deferred to future)
    - "none": returns base_context unchanged

    Returns: new StudentContext via model_copy(). Always safe for ALE to consume.
    """
    override_type = overrides.course_override_type
    courses = overrides.added_courses

    if override_type == "assumed_done" and courses:
        courses_set = set(courses)
        merged = list(set(base_context.completed_courses) | courses_set)
        failed_cleaned = [c for c in base_context.failed_courses if c not in courses_set]
        in_progress_cleaned = [c for c in base_context.in_progress_courses if c not in courses_set]
        return base_context.model_copy(update={
            "completed_courses": merged,
            "failed_courses": failed_cleaned,
            "in_progress_courses": in_progress_cleaned,
        })

    if override_type == "planned" and courses:
        merged = list(set(base_context.in_progress_courses) | set(courses))
        return base_context.model_copy(update={"in_progress_courses": merged})

    if override_type == "assumed_failed" and overrides.assumed_failed_courses:
        assumed_set = set(overrides.assumed_failed_courses)
        merged = list(set(base_context.failed_courses) | assumed_set)
        completed_cleaned = [c for c in base_context.completed_courses if c not in assumed_set]
        zero_credit_cleaned = [c for c in base_context.zero_credit_courses_passed if c not in assumed_set]
        return base_context.model_copy(update={
            "failed_courses": merged,
            "completed_courses": completed_cleaned,
            "zero_credit_courses_passed": zero_credit_cleaned,
        })

    if override_type == "assumed_passed" and overrides.assumed_passed_courses:
        assumed_set = set(overrides.assumed_passed_courses)
        merged = list(set(base_context.completed_courses) | assumed_set)
        failed_cleaned = [c for c in base_context.failed_courses if c not in assumed_set]
        in_progress_cleaned = [c for c in base_context.in_progress_courses if c not in assumed_set]
        return base_context.model_copy(update={
            "completed_courses": merged,
            "failed_courses": failed_cleaned,
            "in_progress_courses": in_progress_cleaned,
        })

    return base_context


def _create_new_session(
    student_id: str,
    context: StudentContext,
    first_message: str,
) -> SessionState:
    sid = str(uuid.uuid4())
    state = SessionState(
        session_id=sid,
        student_id=student_id,
        session_name=_make_session_name(first_message),
        student_context=context,
    )
    _store.save(state)
    logger.info("SessionManager: created session %s for student %s", sid, student_id)
    return state


def get_or_create_session(
    session_id: Optional[str],
    student_id: str,
    context: StudentContext,
    first_message: str,
) -> tuple[SessionState, bool]:
    if session_id is not None:
        state = _store.load(session_id)
        if state is not None:
            return state, False
        logger.warning(
            "SessionManager: stale session_id %s — creating new session", session_id
        )
        state = _create_new_session(student_id, context, first_message)
        return state, True

    state = _create_new_session(student_id, context, first_message)
    return state, True


def get_qu_context(session_id: str, user_text: str) -> QUContext | None:
    session = _store.load(session_id)
    if session is None:
        return None
    recent_turns = session.turn_history[-QU_CONTEXT_TURNS:]
    return QUContext(
        user_text=user_text,
        recent_turns=recent_turns,
        last_referenced=session.last_referenced,
        current_overrides=session.overrides,
    )


def apply_query_result(
    session_id: str,
    structured_query: StructuredQuery,
) -> None:
    session = _store.load(session_id)
    if session is None:
        logger.warning("SessionManager: apply_query_result called for unknown session %s", session_id)
        return

    session.overrides = _apply_overrides(session.overrides, structured_query.session_overrides)
    session.last_referenced = _apply_last_referenced(
        session.last_referenced,
        structured_query.entities.model_dump(),
    )

    _store.save(session)
    logger.debug("SessionManager: session %s overrides and last_referenced updated", session_id)


def update_session_after_turn(
    session_id: str,
    user_text: str,
    answer_text: str,
    new_overrides: Optional[SessionOverrides] = None,
    new_last_referenced: Optional[LastReferenced] = None,
) -> None:
    session = _store.load(session_id)
    if session is None:
        logger.warning("SessionManager: update called for unknown session %s", session_id)
        return

    session.turn_history.append({"user": user_text, "answer": answer_text})

    if new_overrides is not None:
        session.overrides = _apply_overrides(session.overrides, new_overrides)

    if new_last_referenced is not None:
        session.last_referenced = new_last_referenced

    _store.save(session)
    logger.debug(
        "SessionManager: saved turn %d for session %s",
        len(session.turn_history),
        session_id,
    )


def get_student_sessions(student_id: str) -> StudentSessionsResponse:
    rows = _store.get_summaries_for_student(student_id)
    summaries = [
        SessionSummary(session_id=sid, session_name=name, last_updated=lu)
        for sid, name, lu in rows
    ]
    return StudentSessionsResponse(student_id=student_id, sessions=summaries)


def get_session_history(session_id: str) -> SessionHistoryResponse | None:
    session = _store.load(session_id)
    if session is None:
        return None
    return SessionHistoryResponse(
        session_id=session_id,
        session_name=session.session_name,
        turns=session.turn_history,
    )


def delete_session(session_id: str) -> bool:
    result = _store.delete(session_id)
    if result:
        logger.info("SessionManager: deleted session %s", session_id)
    else:
        logger.warning("SessionManager: delete called for unknown session %s", session_id)
    return result


# DEV ONLY — never expose via any API endpoint
# Call once before real system launch to give users a clean start
def clear_all_sessions() -> int:
    count = _store.delete_all()
    logger.warning("SessionManager: DEV ONLY — cleared %d sessions", count)
    return count
