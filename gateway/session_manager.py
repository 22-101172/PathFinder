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


def _dedupe_ordered(items: list[str]) -> list[str]:
    """Remove duplicates while preserving first-occurrence order."""
    return list(dict.fromkeys(items))


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
    It is used as a narration/source flag only. build_effective_context applies all
    three override lists independently regardless of this flag.
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

    # accumulate (default) — deterministic order preserved
    merged_courses = _dedupe_ordered(existing.added_courses + incoming.added_courses)
    merged_failed = _dedupe_ordered(existing.assumed_failed_courses + incoming.assumed_failed_courses)
    merged_passed = _dedupe_ordered(existing.assumed_passed_courses + incoming.assumed_passed_courses)
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


def merge_turn_overrides(
    sqs: list[StructuredQuery],
) -> tuple[SessionOverrides, bool]:
    """
    Accumulate per-SQ session overrides across a single turn.

    Returns (merged_overrides, had_clear).
    had_clear=True means at least one SQ issued a clear action; the caller
    must replace session.overrides directly rather than re-merging, so the
    clear actually takes effect.
    """
    result = SessionOverrides()
    had_clear = False
    for sq in sqs:
        if sq.session_overrides.override_action == "clear":
            had_clear = True
        result = _apply_overrides(result, sq.session_overrides)
    return result, had_clear


def _apply_last_referenced(
    existing: LastReferenced,
    entities: dict,
) -> LastReferenced:
    """
    Merge new entity references into existing LastReferenced.

    Only updates a field when the new value is non-None/non-empty.
    Old values for fields not present in this turn are preserved.
    """
    return LastReferenced(
        course_code=entities.get("course_code") or existing.course_code,
        role_id=entities.get("role_id") or existing.role_id,
        track_id=entities.get("track_id") or existing.track_id,
        skill_id=entities.get("skill_id") or existing.skill_id,
    )


def build_effective_context(
    base_context: StudentContext,
    overrides: SessionOverrides,
) -> StudentContext:
    """
    Apply session overrides to StudentContext. Never mutates base_context.

    All three override lists are applied independently in order:

    1. assumed_failed_courses:
       - Add to failed_courses
       - Remove from completed_courses, in_progress_courses, zero_credit_courses_passed

    2. assumed_passed_courses:
       - Add to completed_courses
       - Remove from failed_courses, in_progress_courses

    3. added_courses  (planned / assumed_done):
       - added to in_progress_courses (planned) or completed_courses (assumed_done)
       - based on course_override_type flag for narration only

    course_override_type is used as a narration/source flag for added_courses only.
    It does NOT gate the assumed_failed or assumed_passed logic.

    Returns: new StudentContext via model_copy(). Always safe for ALE to consume.
    """
    # Start from base values (copied, not mutated)
    completed = list(base_context.completed_courses)
    failed = list(base_context.failed_courses)
    in_progress = list(base_context.in_progress_courses)
    zero_credit = list(base_context.zero_credit_courses_passed)

    # Step 1 — assumed_failed_courses
    if overrides.assumed_failed_courses:
        assumed_failed_set = set(overrides.assumed_failed_courses)
        # Add to failed (preserving order, de-duped)
        failed = _dedupe_ordered(failed + list(overrides.assumed_failed_courses))
        # Remove from completed, in_progress, zero_credit
        completed = [c for c in completed if c not in assumed_failed_set]
        in_progress = [c for c in in_progress if c not in assumed_failed_set]
        zero_credit = [c for c in zero_credit if c not in assumed_failed_set]

    # Step 2 — assumed_passed_courses
    if overrides.assumed_passed_courses:
        assumed_passed_set = set(overrides.assumed_passed_courses)
        # Add to completed (preserving order, de-duped)
        completed = _dedupe_ordered(completed + list(overrides.assumed_passed_courses))
        # Remove from failed, in_progress
        failed = [c for c in failed if c not in assumed_passed_set]
        in_progress = [c for c in in_progress if c not in assumed_passed_set]

    # Step 3 — added_courses (planned or assumed_done)
    override_type = overrides.course_override_type
    if overrides.added_courses:
        if override_type == "assumed_done":
            # Treat as completed; remove from failed/in_progress
            added_set = set(overrides.added_courses)
            completed = _dedupe_ordered(completed + list(overrides.added_courses))
            failed = [c for c in failed if c not in added_set]
            in_progress = [c for c in in_progress if c not in added_set]
        else:
            # Treat as in_progress (planned) by default, even if type is assumed_passed/assumed_failed
            in_progress = _dedupe_ordered(in_progress + list(overrides.added_courses))
        # gpa_scenario: not handled — no course list mutation; reserved for future

    # Only create a new object if anything changed
    if (
        completed != list(base_context.completed_courses)
        or failed != list(base_context.failed_courses)
        or in_progress != list(base_context.in_progress_courses)
        or zero_credit != list(base_context.zero_credit_courses_passed)
    ):
        return base_context.model_copy(update={
            "completed_courses": completed,
            "failed_courses": failed,
            "in_progress_courses": in_progress,
            "zero_credit_courses_passed": zero_credit,
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
    """
    Load an existing session or create a new one.

    Ownership safety:
    - If session_id is provided and the stored session belongs to a DIFFERENT student,
      do NOT reuse that session. Create a new session for the requested student and
      log a safe warning (no PII in the log).
    - If session_id is provided and the stored session belongs to THIS student,
      refresh student_context from the freshly loaded SCP context.

    Returns (session_state, is_new).
    """
    if session_id is not None:
        state = _store.load(session_id)
        if state is not None:
            if state.student_id != student_id:
                # Ownership mismatch — do not leak another student's session
                logger.warning(
                    "SessionManager: session_id %s belongs to a different student "
                    "(expected=%s owner=<redacted>) — creating new session for requested student",
                    session_id, student_id,
                )
                new_state = _create_new_session(student_id, context, first_message)
                return new_state, True

            # Same student — refresh student_context from fresh SCP data
            refreshed = state.model_copy(update={"student_context": context})
            _store.save(refreshed)
            return refreshed, False

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


# LEGACY — prefer update_session_after_turn() for post-turn persistence.
# This function is kept for backward compatibility with existing tests that
# call it directly. It updates overrides and last_referenced per-SQ but does
# NOT append turn history. New code should call update_session_after_turn()
# once per turn, after merge_turn_overrides() has been called.
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
    replace_overrides: bool = False,
) -> None:
    """
    Persist turn result to the session store.

    replace_overrides=True: assign session.overrides = new_overrides directly.
    Use this when a clear action was issued; re-merging would be a no-op.
    replace_overrides=False (default): merge via _apply_overrides (existing behaviour).

    last_referenced is MERGED (not replaced) so that entities from prior turns
    are preserved when the new turn only references one entity type.
    """
    session = _store.load(session_id)
    if session is None:
        logger.warning("SessionManager: update called for unknown session %s", session_id)
        return

    session.turn_history.append({"user": user_text, "answer": answer_text})

    if new_overrides is not None:
        if replace_overrides:
            session.overrides = new_overrides
        else:
            session.overrides = _apply_overrides(session.overrides, new_overrides)

    if new_last_referenced is not None:
        # Merge new entities into existing last_referenced — do not wipe previous references
        session.last_referenced = _apply_last_referenced(
            session.last_referenced,
            new_last_referenced.model_dump(),
        )

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


def get_session_history_for_student(
    student_id: str,
    session_id: str,
) -> SessionHistoryResponse | None:
    """
    Ownership-safe session history retrieval.

    Returns None if the session does not exist OR belongs to a different student.
    """
    session = _store.load(session_id)
    if session is None:
        return None
    if session.student_id != student_id:
        logger.warning(
            "SessionManager: get_session_history_for_student — session %s does not belong "
            "to requested student (owner=<redacted>)",
            session_id,
        )
        return None
    return SessionHistoryResponse(
        session_id=session_id,
        session_name=session.session_name,
        turns=session.turn_history,
    )


def delete_session(session_id: str) -> bool:
    """Delete a session by id. No ownership check. Internal use only."""
    result = _store.delete(session_id)
    if result:
        logger.info("SessionManager: deleted session %s", session_id)
    else:
        logger.warning("SessionManager: delete called for unknown session %s", session_id)
    return result


def delete_session_for_student(student_id: str, session_id: str) -> bool:
    """
    Delete a session only if it belongs to the given student.

    Returns True if deleted, False if not found or ownership mismatch.
    Ownership mismatch is logged as a warning.
    """
    session = _store.load(session_id)
    if session is None:
        logger.warning("SessionManager: delete_session_for_student — session %s not found", session_id)
        return False
    if session.student_id != student_id:
        logger.warning(
            "SessionManager: delete_session_for_student — session %s does not belong to "
            "requested student (owner=<redacted>)",
            session_id,
        )
        return False
    result = _store.delete(session_id)
    if result:
        logger.info("SessionManager: deleted session %s for student %s", session_id, student_id)
    return result


def delete_all_sessions_for_student(student_id: str) -> int:
    """Delete all sessions for a specific student. Return count deleted."""
    count = _store.delete_all_for_student(student_id)
    logger.info("SessionManager: deleted %d sessions for student %s", count, student_id)
    return count


# DEV ONLY — never expose via any non-dev-gated API endpoint.
# Call once before real system launch to give users a clean start, or
# during automated test teardown.
def delete_all_sessions_global() -> int:
    """Delete ALL sessions globally. Dev/test use only."""
    count = _store.delete_all()
    logger.warning("SessionManager: DEV ONLY — cleared %d sessions globally", count)
    return count


# Alias kept for backward compatibility with existing callers.
# New code should call delete_all_sessions_global().
def clear_all_sessions() -> int:
    return delete_all_sessions_global()
