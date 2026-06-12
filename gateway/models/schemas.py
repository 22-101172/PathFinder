from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    session_id: Optional[str] = None
    user_text: str
    student_id: str


class Citation(BaseModel):
    source: str
    page: Optional[int] = None


class QueryResponse(BaseModel):
    session_id: str
    session_name: str
    answer_text: str
    citations: list[Citation] = []
    status: str


class EntitySet(BaseModel):
    course_code: Optional[str] = None
    role_id: Optional[str] = None
    track_id: Optional[str] = None
    skill_id: Optional[str] = None


class SessionOverrides(BaseModel):
    added_courses: list[str] = []
    assumed_failed_courses: list[str] = []
    assumed_passed_courses: list[str] = []
    target_role: Optional[str] = None
    course_override_type: Literal[
        "planned",
        "assumed_done",
        "assumed_failed",
        "assumed_passed",
        "gpa_scenario",
        "none"
    ] = "none"
    override_action: Literal[
        "accumulate",
        "replace",
        "clear"
    ] = "accumulate"


class StructuredQuery(BaseModel):
    intent: str
    engine_pattern: str
    query_type: str
    original_text: Optional[str] = None
    entities: EntitySet = EntitySet()
    secondary_entities: Optional[EntitySet] = None
    needs_clarification: bool = False
    clarification_prompt: Optional[str] = None
    session_overrides: SessionOverrides = SessionOverrides()


class CourseRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    course_code: str
    credit_hours: int
    grade: Optional[str] = None
    semester_taken: str
    status: Literal["passed", "repeated", "failed", "in_progress",
                    "withdrawn", "incomplete"]


class StudentContext(BaseModel):
    student_id: str
    name: str
    program: str
    track_id: str
    level: int
    first_semester: str
    study_status: str
    military_status: Optional[str] = None
    cgpa: Optional[float] = None
    last_semester_gpa: Optional[float] = None
    total_credit_hours_earned: int
    cumulative_chs: Optional[int] = None
    cumulative_cps: Optional[float] = None
    last_semester_chs: Optional[int] = None
    last_semester_cps: Optional[float] = None
    last_semester_phs: Optional[int] = None
    current_semester_chs: int = 0
    consecutive_warnings: int = 0
    total_warnings: int = 0
    last_semester_warning: Optional[int] = None
    course_history: list[CourseRecord] = []
    completed_courses: list[str] = []
    failed_courses: list[str] = []
    in_progress_courses: list[str] = []
    completed_regular_semesters: int = 0
    zero_credit_courses_passed: list[str] = Field(default_factory=list)
    retake_count: dict[str, int] = Field(default_factory=dict)
    total_improve_retakes_used: int = 0


class LastReferenced(BaseModel):
    course_code: Optional[str] = None
    role_id: Optional[str] = None
    track_id: Optional[str] = None


class QUContext(BaseModel):
    user_text: str
    recent_turns: list[dict]
    last_referenced: LastReferenced
    current_overrides: SessionOverrides


class SessionState(BaseModel):
    session_id: str
    student_id: str
    session_name: str
    student_context: StudentContext
    last_referenced: LastReferenced = LastReferenced()
    overrides: SessionOverrides = SessionOverrides()
    turn_history: list[dict] = []


class RAGResult(BaseModel):
    answer: Optional[str] = None
    citations: list[Citation] = []


class ComposerContext(BaseModel):
    track_id: str
    level: int
    cgpa: Optional[float]
    academic_standing: str
    study_status: str
    total_credit_hours_earned: int
    current_semester: str
    consecutive_warnings: int = 0


class ResultPackage(BaseModel):
    original_query: str
    intent: str
    engine_pattern: str
    kg_result: Optional[dict] = None
    rag_result: Optional[RAGResult] = None
    ale_result: Optional[dict] = None
    composer_context: Optional[ComposerContext] = None
    status: str
    error_detail: Optional[str] = None


class SessionSummary(BaseModel):
    session_id: str
    session_name: str
    last_updated: str


class StudentSessionsResponse(BaseModel):
    student_id: str
    sessions: list[SessionSummary] = []


class SessionHistoryResponse(BaseModel):
    session_id: str
    session_name: str
    turns: list[dict] = []
