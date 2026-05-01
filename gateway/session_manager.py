"""
session_manager.py  [T03]
─────────────────────────
Responsibility:
  Maintain runtime conversational state across turns.
  Build effective_context = base_student_record + session_overrides.

Rules (Blueprint §5.3):
  ✓ Store and retrieve session state by session_id
  ✓ Apply overrides that arrive from StructuredQuery.session_overrides
  ✓ Track last_referenced entities for multi-turn follow-up resolution
  ✓ Build effective_context by merging base context with overrides
  ✗ Must NOT read or interpret user text
  ✗ Must NOT detect overrides in text  (Query Understanding Layer does that)
  ✗ Must NOT call engines
  ✗ Must NOT permanently modify the base StudentContext

Critical override rule:
  QU Layer DETECTS overrides → places them in StructuredQuery.session_overrides
  Session Manager APPLIES them → merges into effective_context
  These responsibilities must never be swapped.

REDIS MIGRATION NOTE:
  When SESSION_STORE=redis (set in .env):
    - Replace self._sessions dict with a Redis client instance
    - Serialize SessionState to/from JSON for storage
    - Replace the dict reads/writes inside _get_session() and _set_session()
    - All public method signatures stay identical — no caller changes needed
  Current: in-memory dict. Correct for a single-process demo.
  Switch trigger: multi-instance deployment or session persistence across restarts.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from models.schemas import StudentContext
from student_context_provider import StudentContextProvider

logger = logging.getLogger(__name__)

# Session ID prefix — makes sessions recognisable in logs and debugging output.
_SESSION_ID_PREFIX = "sess_"

# Length of the random hex suffix appended to session IDs.
# 8 hex chars = 32-bit entropy — sufficient for a single-instance demo.
_SESSION_ID_SUFFIX_LENGTH = 8


@dataclass
class SessionState:
    """
    Internal runtime session state. Not a serialised data contract.

    See models.schemas.SessionState for the external API contract (Blueprint §6.4).
    This dataclass holds richer runtime fields (timestamps, turn count) that are
    not exposed in API responses.

    REDIS MIGRATION: serialise/deserialise this dataclass to JSON inside
    _get_session() and _set_session(). All other code stays the same.
    """

    session_id: str
    active_student_id: str
    created_at: datetime
    last_updated: datetime
    turn_count: int = 0

    # last_referenced tracks the most recent entity mentioned in a turn so that
    # follow-up queries ("what about its prerequisites?") can resolve "its".
    last_referenced: dict = field(
        default_factory=lambda: {
            "course_code": None,
            "role_id": None,
            "workflow": None,
        }
    )

    # overrides holds hypothetical additions from the current conversation.
    # added_courses accumulates across turns; target_role is replaced each turn.
    overrides: dict = field(
        default_factory=lambda: {
            "added_courses": [],
            "target_role": None,
        }
    )


class SessionManager:
    """
    In-memory session store keyed by session_id.

    All storage reads go through _get_session(); all writes through _set_session().
    These two private methods are the sole swap points for a Redis migration.

    Concept — two context objects per turn:
      base_context      = what is actually true (StudentContextProvider output).
                          Never modified. Same object for every call on this student.
      effective_context = base_context + session overrides for the current turn.
                          A new object, created by build_effective_context().
                          Discarded after the turn; not persisted to the base record.
    """

    def __init__(self, student_provider: StudentContextProvider) -> None:
        # student_provider kept for forward compatibility.
        # Current design passes base_context into build_effective_context() directly,
        # so the provider is not called here. Future designs may use it.
        self._student_provider = student_provider
        self._sessions: dict[str, SessionState] = {}

    # ── Public interface ──────────────────────────────────────────────────────

    def get_or_create_session(
        self,
        student_id: str,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Return an existing session_id or create and store a new session.

        If session_id is given and found: update last_updated, return same id.
        If session_id is given but not found: log a warning (stale ID from a
          server restart), create a new session, return the new id.
        If no session_id is given: create a new session.
        """
        if session_id is not None:
            session = self._get_session(session_id)
            if session is not None:
                session.last_updated = datetime.now(timezone.utc)
                self._set_session(session)
                return session_id
            logger.warning(
                "Session %s not found — creating a new session for student %s.",
                session_id,
                student_id,
            )

        new_session = self._new_session(student_id)
        self._set_session(new_session)
        return new_session.session_id

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """Return the SessionState for this session_id, or None if not found."""
        return self._get_session(session_id)

    def apply_overrides(
        self,
        session_id: str,
        overrides_dict: dict,
    ) -> None:
        """
        Apply overrides detected by the Query Understanding Layer.

        added_courses → EXTEND the existing list (never replace).
          Reason: a student may propose hypothetical courses across multiple turns.
          Turn 2 adds C-AI401; turn 3 adds C-AI501 — both must survive in the context.
          Duplicates are skipped.

        target_role → REPLACE the previous value.
          Reason: a new target role makes the old one irrelevant.

        If the session is not found: log a warning and return silently.
        Never call this with overrides you detected yourself — that belongs to QU Layer.
        """
        session = self._get_session(session_id)
        if session is None:
            logger.warning("apply_overrides: session %s not found.", session_id)
            return

        for course in overrides_dict.get("added_courses") or []:
            if course not in session.overrides["added_courses"]:
                session.overrides["added_courses"].append(course)

        new_role = overrides_dict.get("target_role")
        if new_role is not None:
            session.overrides["target_role"] = new_role

        session.last_updated = datetime.now(timezone.utc)
        self._set_session(session)

    def update_last_referenced(
        self,
        session_id: str,
        course_code: Optional[str] = None,
        role_id: Optional[str] = None,
        workflow: Optional[str] = None,
    ) -> None:
        """
        Update last_referenced for multi-turn context resolution.

        Only overwrites keys that are explicitly supplied (not None).
        Called by the Orchestrator after each turn so follow-up queries
        ("what about its prerequisites?") can resolve the referent.
        If the session is not found: log a warning and return silently.
        """
        session = self._get_session(session_id)
        if session is None:
            logger.warning(
                "update_last_referenced: session %s not found.", session_id
            )
            return

        if course_code is not None:
            session.last_referenced["course_code"] = course_code
        if role_id is not None:
            session.last_referenced["role_id"] = role_id
        if workflow is not None:
            session.last_referenced["workflow"] = workflow

        session.last_updated = datetime.now(timezone.utc)
        self._set_session(session)

    def build_effective_context(
        self,
        base_context: StudentContext,
        session_id: str,
    ) -> StudentContext:
        """
        Return a StudentContext with session overrides applied for this turn.

        If the session is not found or overrides are empty, base_context is
        returned unchanged — no copy is created.

        'Empty overrides' means added_courses == [] AND target_role is None.

        When overrides exist, a new StudentContext is created via model_copy().
        list() is used to copy added_courses so mutations to the effective context
        cannot bleed back into the session's internal override list.

        base_context is NEVER mutated. This invariant is critical: the provider
        caches base_context and returns the same object on every call.
        """
        session = self._get_session(session_id)
        if session is None:
            return base_context

        overrides = session.overrides
        has_courses = bool(overrides.get("added_courses"))
        has_role = overrides.get("target_role") is not None

        if not has_courses and not has_role:
            return base_context

        # list() creates a defensive copy — never hand the session's own list
        # to another object or mutations would bleed across turns.
        new_planned = list(overrides.get("added_courses", []))
        return base_context.model_copy(update={"planned_courses": new_planned})

    def record_turn(self, session_id: str) -> None:
        """Increment turn_count and update last_updated. Safe on missing sessions."""
        session = self._get_session(session_id)
        if session is None:
            logger.warning("record_turn: session %s not found.", session_id)
            return

        session.turn_count += 1
        session.last_updated = datetime.now(timezone.utc)
        self._set_session(session)

    # ── Private storage methods ───────────────────────────────────────────────
    # REDIS MIGRATION: replace the dict reads/writes below with Redis get/set.
    # Serialise SessionState to JSON for storage; deserialise on retrieval.
    # All public methods above remain unchanged.

    def _get_session(self, session_id: str) -> Optional[SessionState]:
        """Read a session from the store. Returns None if not found."""
        return self._sessions.get(session_id)

    def _set_session(self, session: SessionState) -> None:
        """Write a session to the store."""
        self._sessions[session.session_id] = session

    def _new_session(self, student_id: str) -> SessionState:
        """Create a fresh SessionState with a unique ID and current timestamps."""
        now = datetime.now(timezone.utc)
        return SessionState(
            session_id=_SESSION_ID_PREFIX + uuid.uuid4().hex[:_SESSION_ID_SUFFIX_LENGTH],
            active_student_id=student_id,
            created_at=now,
            last_updated=now,
        )
