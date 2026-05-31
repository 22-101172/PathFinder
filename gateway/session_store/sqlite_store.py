from __future__ import annotations
import logging
import sqlite3
from datetime import datetime, timezone

from gateway.models.schemas import SessionState
from gateway.session_store.base import SessionStore

logger = logging.getLogger(__name__)


class SQLiteSessionStore(SessionStore):

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id   TEXT PRIMARY KEY,
                    student_id   TEXT NOT NULL,
                    session_name TEXT NOT NULL,
                    last_updated TEXT NOT NULL,
                    session_blob TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_student_id ON sessions(student_id)"
            )

    def save(self, session: SessionState) -> None:
        now = datetime.now(timezone.utc).isoformat()
        blob = session.model_dump_json()
        logger.debug("SQLiteSessionStore: saving session %s", session.session_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions "
                "(session_id, student_id, session_name, last_updated, session_blob) "
                "VALUES (?, ?, ?, ?, ?)",
                (session.session_id, session.student_id, session.session_name, now, blob),
            )

    def load(self, session_id: str) -> SessionState | None:
        logger.debug("SQLiteSessionStore: loading session %s", session_id)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT session_blob FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionState.model_validate_json(row[0])

    def delete(self, session_id: str) -> bool:
        logger.debug("SQLiteSessionStore: deleting session %s", session_id)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
        return cursor.rowcount > 0

    def get_all_for_student(self, student_id: str) -> list[SessionState]:
        logger.debug("SQLiteSessionStore: getting all sessions for student %s", student_id)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT session_blob FROM sessions "
                "WHERE student_id = ? ORDER BY last_updated DESC",
                (student_id,),
            ).fetchall()
        return [SessionState.model_validate_json(row[0]) for row in rows]

    def get_summaries_for_student(self, student_id: str) -> list[tuple[str, str, str]]:
        """Return (session_id, session_name, last_updated) tuples ordered by last_updated DESC."""
        logger.debug("SQLiteSessionStore: getting summaries for student %s", student_id)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT session_id, session_name, last_updated FROM sessions "
                "WHERE student_id = ? ORDER BY last_updated DESC",
                (student_id,),
            ).fetchall()
        return rows

    def delete_all(self) -> int:
        logger.debug("SQLiteSessionStore: deleting all sessions")
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM sessions")
        return cursor.rowcount
