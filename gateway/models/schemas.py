from __future__ import annotations
from typing import Any, Optional, Literal, TypedDict
from pydantic import BaseModel, ConfigDict, Field


class Turn(TypedDict):
    """One turn in conversation history: user query and system answer."""
    user: str
    answer: str


class Citation(BaseModel):
    source: str
    page: Optional[int] = None


# ── Structured Turn Memory ────────────────────────────────────────────────────

class DisplayItem(BaseModel):
    """One visible item from the previous answer, ordered for follow-up resolution."""
    type: Literal["course", "skill", "role", "track", "policy", "planning_item"]
    code: Optional[str] = None
    name: Optional[str] = None
    source_intent: str
    source_field: Optional[str] = None
    rank: int = 0


class PolicyText(BaseModel):
    """Compact natural-language policy memory — no symbolic labels, no full chunks."""
    answer_brief: str   # capped to 500 chars
    citations: list[Citation] = Field(default_factory=list)


class QueryMemory(BaseModel):
    """What the student asked and what QU understood last turn."""
    user_text_brief: str = ""  # capped to 160 chars
    intents: list[str] = Field(default_factory=list)
    entities: dict[str, list[str]] = Field(default_factory=dict)  # {courses,skills,roles,tracks}
    params: dict[str, Any] = Field(default_factory=dict)
    student_referential: bool = False


class AnswerMemory(BaseModel):
    """What the system exposed in the last answer — safe for QU context injection."""
    policy_text: Optional[PolicyText] = None
    student_record_summary: Optional[str] = None  # capped to 300 chars; no PII
    courses: list[str] = Field(default_factory=list)        # max 8 course codes
    skills: list[str] = Field(default_factory=list)         # max 10 skill IDs
    roles: list[str] = Field(default_factory=list)          # max 8 role IDs
    tracks: list[str] = Field(default_factory=list)         # max 5 track IDs
    planning_items: list[str] = Field(default_factory=list) # max 8 course codes
    gpa_scenario: Optional[str] = None
    ordered_display_items: list[DisplayItem] = Field(default_factory=list)  # max 12


class AmbiguityGroup(BaseModel):
    label: str
    item_type: str
    items: list[str] = Field(default_factory=list)


class AmbiguityMeta(BaseModel):
    has_multiple_reference_groups: bool = False
    groups: list[AmbiguityGroup] = Field(default_factory=list)


class TurnMemory(BaseModel):
    """
    One compact structured memory object per completed turn.
    Never stores: raw Composer answer, full StudentContext, student name,
    student ID, full transcript, or hardcoded symbolic policy labels.
    """
    source_intents: list[str] = Field(default_factory=list)
    primary_domain: Literal[
        "policy", "student_record", "course", "career",
        "track", "planning", "mixed", "control"
    ] = "mixed"
    query_memory: QueryMemory = Field(default_factory=QueryMemory)
    answer_memory: AnswerMemory = Field(default_factory=AnswerMemory)
    ambiguity: AmbiguityMeta = Field(default_factory=AmbiguityMeta)


# ── API / Request / Response ──────────────────────────────────────────────────

class QueryRequest(BaseModel):
    session_id: Optional[str] = None
    user_text: str
    student_id: str


class QueryResponse(BaseModel):
    session_id: str
    session_name: str
    answer_text: str
    citations: list[Citation] = Field(default_factory=list)
    status: Literal["ok", "error", "clarification_needed"] = "ok"


class EntitySet(BaseModel):
    course_code: Optional[str] = None
    role_id: Optional[str] = None
    track_id: Optional[str] = None
    skill_id: Optional[str] = None


class SessionOverrides(BaseModel):
    added_courses: list[str] = Field(default_factory=list)
    assumed_failed_courses: list[str] = Field(default_factory=list)
    assumed_passed_courses: list[str] = Field(default_factory=list)
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
    original_text: Optional[str] = None
    entities: EntitySet = Field(default_factory=EntitySet)
    secondary_entities: Optional[EntitySet] = None
    params: dict[str, Any] = Field(default_factory=dict)
    session_overrides: SessionOverrides = Field(default_factory=SessionOverrides)
    student_referential_fallback: bool = False
    # Legacy fields kept for backward compatibility with existing tests and orchestrator
    engine_pattern: Optional[str] = None
    query_type: Optional[str] = None
    needs_clarification: bool = False
    clarification_prompt: Optional[str] = None


class CourseRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    course_code: str
    credit_hours: Optional[int] = None  # None = credits unknown; KG/Orchestrator must patch
    grade: Optional[str] = None
    semester_taken: str
    status: Literal["passed", "repeated", "failed", "in_progress",
                    "withdrawn", "incomplete"]


class StudentContext(BaseModel):
    student_id: str
    name: str
    program: str
    track_id: Optional[str] = None        # None for unsupported/unknown programs
    track_status: Literal["supported", "unsupported"] = "supported"
    track_error_code: Optional[str] = None  # e.g. "unsupported_track"
    level: Optional[int] = None            # None for invalid/blank level
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
    course_history: list[CourseRecord] = Field(default_factory=list)
    completed_courses: list[str] = Field(default_factory=list)
    failed_courses: list[str] = Field(default_factory=list)
    in_progress_courses: list[str] = Field(default_factory=list)
    completed_regular_semesters: int = 0
    current_semester: Optional[str] = None
    zero_credit_courses_passed: list[str] = Field(default_factory=list)
    retake_count: dict[str, int] = Field(default_factory=dict)
    total_improve_retakes_used: int = 0


class LastReferenced(BaseModel):
    course_code: Optional[str] = None
    role_id: Optional[str] = None
    track_id: Optional[str] = None
    skill_id: Optional[str] = None  # mirrors EntitySet.skill_id; merged per-turn


class QUContext(BaseModel):
    user_text: str
    recent_turns: list[Turn] = Field(default_factory=list)
    last_referenced: LastReferenced
    current_overrides: SessionOverrides


class SessionState(BaseModel):
    session_id: str
    student_id: str
    session_name: str
    student_context: StudentContext
    last_referenced: LastReferenced = Field(default_factory=LastReferenced)
    overrides: SessionOverrides = Field(default_factory=SessionOverrides)
    turn_history: list[Turn] = Field(default_factory=list)
    last_turn_memory: Optional[TurnMemory] = None


class RAGResult(BaseModel):
    answer: Optional[str] = None
    citations: list[Citation] = Field(default_factory=list)


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
    status: Literal["ok", "error", "clarification_needed"] = "ok"
    error_detail: Optional[str] = None


class PerSQResult(BaseModel):
    sq_index: int
    intent: str
    status: Literal[
        "success", "error", "clarification_needed",
        "out_of_scope", "informational", "soft_no_evidence"
    ]
    data: Optional[dict] = None
    error_code: Optional[str] = None
    error_category: Optional[str] = None
    error_detail: Optional[str] = None
    clarification_prompt: Optional[str] = None
    scope_explanation: Optional[str] = None
    assumptions_active: Optional[bool] = None
    assumptions_excluded: Optional[bool] = None
    override_state_active: Optional[bool] = None
    citations: Optional[list[dict]] = None


class TurnWrapper(BaseModel):
    turn_id: str
    session_id: str
    timestamp: str
    results: list[PerSQResult]
    result_count: int
    turn_status: Literal[
        "completed", "needs_clarification", "partial_success", "failed", "out_of_scope"
    ]
    has_error: bool
    has_clarification: bool
    has_informational: bool
    has_soft_no_evidence: bool
    turn_summary: Optional[str] = None


class TurnResponse(BaseModel):
    """Interim API response returned by /chat until the Response Composer is wired."""
    session_id: str
    session_name: str
    turn: TurnWrapper


class SessionSummary(BaseModel):
    session_id: str
    session_name: str
    last_updated: str


class StudentSessionsResponse(BaseModel):
    student_id: str
    sessions: list[SessionSummary] = Field(default_factory=list)


class SessionHistoryResponse(BaseModel):
    session_id: str
    session_name: str
    turns: list[dict] = Field(default_factory=list)
