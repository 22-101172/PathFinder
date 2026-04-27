"""
session_manager.py  [T03 — Person A]
──────────────────────────────────────
Responsibility:
  Maintain runtime conversational state.
  Build effective_context = base_student_record + session_overrides.

Rules (from blueprint Section 5.3):
  ✓ Load session by session_id
  ✓ Apply overrides that come IN from StructuredQuery.session_overrides
  ✓ Track last_referenced entities across turns
  ✓ Expose effective_context to downstream components
  ✗ Must NOT detect overrides in user text  ← this belongs to QU Layer
  ✗ Must NOT permanently modify the student record
  ✗ Must NOT call engines

Critical override rule:
  QU Layer DETECTS overrides → places them in StructuredQuery.session_overrides
  Session Manager APPLIES them → merges into effective_context
  Never the other way around.

Done when:
  - Multi-turn session survives 3 consecutive turns
  - apply_overrides() changes effective_context without touching base record
  - last_referenced is updated correctly after each turn
"""

import uuid
from copy import deepcopy
from typing import Optional

from models.schemas import SessionState, StudentContext, SessionOverrides, LastReferenced
from student_context_provider import StudentContextProvider


class SessionManager:
    """
    In-memory session store (dict keyed by session_id).

    When SESSION_STORE=redis is set, only this class changes.
    All consumers always call the same public interface.
    """

    def __init__(self, student_provider: StudentContextProvider):
        self._sessions: dict[str, SessionState] = {}
        self._student_provider = student_provider

    # ── Public interface ─────────────────────────────────────────────────────

    def get_or_create_session(self, session_id: Optional[str], student_id: str) -> SessionState:
        """
        Load existing session or create a new one.
        Returns the current SessionState.
        """
        # TODO: implement
        # If session_id is None or not found → create new SessionState
        # Otherwise → return existing session from self._sessions
        raise NotImplementedError

    def build_effective_context(self, session: SessionState) -> StudentContext:
        """
        Return the student context with session overrides applied.

        effective_context = base_student_record + session.overrides

        Does NOT modify the base record. Returns a new object.
        """
        # TODO: implement
        # Steps:
        #   1. Load base student record via self._student_provider.get_student(session.active_student_id)
        #   2. Deep-copy the record
        #   3. Apply session.overrides.added_courses → merge into completed_courses
        #   4. Apply session.overrides.target_role if set
        #   5. Return the modified copy
        raise NotImplementedError

    def apply_overrides_from_structured_query(
        self,
        session: SessionState,
        overrides: SessionOverrides
    ) -> SessionState:
        """
        Apply detected overrides from the StructuredQuery into session state.

        Called AFTER Query Understanding Layer returns — never before.
        Merges new overrides with existing session overrides.
        """
        # TODO: implement
        raise NotImplementedError

    def update_last_referenced(
        self,
        session: SessionState,
        course_code: Optional[str] = None,
        role_id: Optional[str] = None,
        workflow: Optional[str] = None,
    ) -> None:
        """
        Update the last_referenced entity after each turn.
        Enables multi-turn follow-up resolution (e.g. "what about its prerequisites?").
        """
        # TODO: implement
        raise NotImplementedError

    def save_session(self, session: SessionState) -> None:
        """Persist session state. Currently writes to in-memory dict."""
        # TODO: implement
        raise NotImplementedError

    # ── Private helpers ──────────────────────────────────────────────────────

    def _new_session(self, student_id: str) -> SessionState:
        """Create and return a fresh SessionState with a new UUID."""
        # TODO: implement
        raise NotImplementedError
