from __future__ import annotations
from abc import ABC, abstractmethod
from gateway.models.schemas import SessionState


class SessionStore(ABC):

    @abstractmethod
    def save(self, session: SessionState) -> None:
        """Insert or replace a session."""

    @abstractmethod
    def load(self, session_id: str) -> SessionState | None:
        """Return session by id, or None if not found."""

    @abstractmethod
    def delete(self, session_id: str) -> bool:
        """Delete session by id. Return True if deleted, False if not found."""

    @abstractmethod
    def get_all_for_student(self, student_id: str) -> list[SessionState]:
        """Return all sessions for a student, ordered by last_updated descending."""

    @abstractmethod
    def delete_all(self) -> int:
        """Delete ALL sessions. Return count deleted. Dev use only."""

    @abstractmethod
    def delete_all_for_student(self, student_id: str) -> int:
        """Delete all sessions for a specific student. Return count deleted."""

    @abstractmethod
    def get_summaries_for_student(self, student_id: str) -> list[tuple[str, str, str]]:
        """Return (session_id, session_name, last_updated) tuples for a student, ordered by last_updated DESC."""
