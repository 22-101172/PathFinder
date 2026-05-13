"""
models/schemas.py
─────────────────
All Pydantic v2 data contracts for PathFinder.
These are the canonical schemas shared between every component.
Source of truth: Integration Phase Blueprint, Section 6.

DO NOT add business logic here. These are pure data shapes.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ── 6.1  API Request & Response ──────────────────────────────────────────────

class QueryRequest(BaseModel):
    """Incoming from UI → Gateway (POST /query body)."""

    session_id: Optional[str] = None    # None = new session
    user_text: str                       # raw natural-language query
    active_student_id: str              # e.g. "S_000123"


class Citation(BaseModel):
    source: str
    page: Optional[int] = None


class QueryResponse(BaseModel):
    """Outgoing from Gateway → UI."""

    session_id: str
    answer_text: str
    citations: list[Citation] = Field(default_factory=list)
    status: str                          # "ok" | "error" | "clarification_needed"


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

    added_courses: list[str] = Field(default_factory=list)
    target_role: Optional[str] = None


class StructuredQuery(BaseModel):
    """
    Output of Query Understanding Layer.
    Consumed by Session Manager (for override detection) and Orchestrator.
    """

    intent: str                                                  # e.g. "get_prerequisites"
    engine_pattern: str                                          # "kg" | "rag" | "mixed"
    query_type: str                                              # "student_aware" | "non_student_aware"
    entities: EntitySet = Field(default_factory=EntitySet)
    needs_clarification: bool = False
    clarification_prompt: Optional[str] = None
    session_overrides: SessionOverrides = Field(default_factory=SessionOverrides)


# ── 6.3  Student Context (Provider → Session Manager → Orchestrator) ─────────

class CourseRecord(BaseModel):
    """
    One entry in a student's academic transcript.

    Frozen (immutable) — a transcript record must never be mutated once loaded.
    Use model_copy() if a modified version is ever needed.
    """

    model_config = ConfigDict(frozen=True)

    course_code: str
    course_name: str
    credit_hours: int = Field(ge=0)     # some orientation courses carry 0 credits
    grade: Optional[str] = None         # letter grade: A+, A, B+…F, Abs; null if in_progress
    grade_points: Optional[float] = None  # 0.0–4.0 scale; null if in_progress
    semester_taken: str                 # e.g. "Fall 2023"
    status: Literal["passed", "failed", "in_progress"]


class StudentContext(BaseModel):
    """
    Full normalized student object flowing through the system.

    Produced by StudentContextProvider from the JSON / database record.
    Session Manager enriches it with planned_courses for the current turn.
    Orchestrator reads it; KGEngine receives the list fields directly.
    """

    # ── Stored: read directly from JSON / database ──────────────────────────
    student_id: str
    name: str
    track_id: str                        # e.g. "AI", "CS", "IS"
    level: int                           # academic year: 1–4
    current_semester: str                # "Fall" | "Spring"
    cgpa: float                          # 0.0–4.0
    academic_standing: str               # "good" | "probation"
    total_credit_hours_earned: int       # raw earned hours from the student record

    course_history: list[CourseRecord] = Field(default_factory=list)  # full transcript

    completed_courses: list[str] = Field(default_factory=list)    # codes where status="passed"
    failed_courses: list[str] = Field(default_factory=list)       # codes where status="failed"
    in_progress_courses: list[str] = Field(default_factory=list)  # codes where status="in_progress"

    # ── Override: always [] from provider; Session Manager populates per turn ─
    planned_courses: list[str] = Field(default_factory=list)
    # Hypothetical courses from "what if" scenarios.
    # Passed to KGEngine.estimate_alignment_improvement() as planned_courses arg.
    # Never persisted to the student record; discarded after each turn.


# ── 6.4  Session State (external data contract — stored by Session Manager) ──

class LastReferenced(BaseModel):
    """Entities referenced in the most recent turn — enables multi-turn follow-ups."""

    course_code: Optional[str] = None
    role_id: Optional[str] = None
    workflow: Optional[str] = None


class SessionState(BaseModel):
    """External session contract (Blueprint §6.4). Not the internal runtime dataclass."""

    session_id: str
    active_student_id: str
    last_referenced: LastReferenced = Field(default_factory=LastReferenced)
    overrides: SessionOverrides = Field(default_factory=SessionOverrides)


# ── 6.5  Aggregated Result Package (Orchestrator → Response Composer) ─────────

class RAGResult(BaseModel):
    answer: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)


class ResultPackage(BaseModel):
    """Everything the Response Composer needs to produce the final answer."""

    original_query: str
    engine_pattern: str                  # "kg" | "rag" | "mixed"
    kg_result: Optional[dict] = None     # structured KGEngine output
    rag_result: Optional[RAGResult] = None
    student_context: Optional[StudentContext] = None
    status: str                          # "ok" | "error" | "clarification_needed"
    error_detail: Optional[str] = None
