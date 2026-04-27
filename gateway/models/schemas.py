"""
models/schemas.py
─────────────────
All Pydantic data contracts for PathFinder.
These are the canonical schemas shared between every component.
Source of truth: Integration Phase Blueprint, Section 6.

DO NOT add business logic here. These are pure data shapes.
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


# ── 6.1  API Request & Response ──────────────────────────────────────────────

class QueryRequest(BaseModel):
    """Incoming from UI → Gateway (POST /query body)."""
    session_id: Optional[str] = None        # None = new session
    user_text: str                           # raw natural language query
    active_student_id: str                  # e.g. "S_000123"


class Citation(BaseModel):
    source: str
    page: Optional[int] = None


class QueryResponse(BaseModel):
    """Outgoing from Gateway → UI."""
    session_id: str
    answer_text: str
    citations: list[Citation] = []
    status: str                             # "ok" | "error" | "clarification_needed"


# ── 6.2  Structured Query (QU Layer → Orchestrator) ──────────────────────────

class EntitySet(BaseModel):
    course_code: Optional[str] = None
    role_id: Optional[str] = None
    track_id: Optional[str] = None
    skill_id: Optional[str] = None


class SessionOverrides(BaseModel):
    """
    Detected by Query Understanding Layer — applied by Session Manager.
    Never populated by Session Manager itself.
    """
    added_courses: list[str] = []
    target_role: Optional[str] = None


class StructuredQuery(BaseModel):
    """
    Output of Query Understanding Layer.
    Consumed by Session Manager (for override detection) and Orchestrator.
    """
    intent: str                             # e.g. "get_prerequisites"
    engine_pattern: str                     # "kg" | "rag" | "mixed"
    query_type: str                         # "student_aware" | "non_student_aware"
    entities: EntitySet = EntitySet()
    needs_clarification: bool = False
    clarification_prompt: Optional[str] = None
    session_overrides: SessionOverrides = SessionOverrides()


# ── 6.3  Student Context (Provider → Session Manager → Orchestrator) ─────────

class CourseRecord(BaseModel):
    course_code: str
    course_name: str
    credit_hours: int
    grade: Optional[str] = None             # letter grade: A+, A, B+...F, Abs
    grade_points: Optional[float] = None    # 4.0, 3.7, 3.2 ... 0.0
    semester_taken: str                     # e.g. "Fall 2023"
    status: str                             # "passed" | "failed" | "in_progress"


class StudentContext(BaseModel):
    # Identity
    student_id: str
    name: str
    track_id: str
    level: int                              # academic year: 1-4
    current_semester: str                   # "Fall" | "Spring"

    # Academic standing (handbook-derived)
    cgpa: float
    academic_standing: str                  # "good" | "probation"
    total_credit_hours_earned: int
    credit_hours_remaining: int             # 133 - earned
    max_credit_hours_allowed: int           # 12 | 15 | 18 | 21 based on CGPA

    # Source of truth
    course_history: list[CourseRecord] = []

    # Derived views — computed at load time by StudentContextProvider
    completed_courses: list[str] = []       # course_codes with status="passed"
    failed_courses: list[str] = []          # course_codes with status="failed"
    in_progress_courses: list[str] = []     # course_codes with status="in_progress"
    planned_courses: list[str] = []         # intended next semester courses


# ── 6.4  Session State (stored by Session Manager) ───────────────────────────

class LastReferenced(BaseModel):
    course_code: Optional[str] = None
    role_id: Optional[str] = None
    workflow: Optional[str] = None


class SessionState(BaseModel):
    session_id: str
    active_student_id: str
    last_referenced: LastReferenced = LastReferenced()
    overrides: SessionOverrides = SessionOverrides()


# ── 6.5  Aggregated Result Package (Orchestrator → Response Composer) ─────────

class RAGResult(BaseModel):
    answer: Optional[str] = None
    citations: list[Citation] = []


class ResultPackage(BaseModel):
    original_query: str
    engine_pattern: str                     # "kg" | "rag" | "mixed"
    kg_result: Optional[dict] = None
    rag_result: Optional[RAGResult] = None
    student_context: Optional[StudentContext] = None
    status: str                             # "ok" | "error" | "clarification_needed"
    error_detail: Optional[str] = None
