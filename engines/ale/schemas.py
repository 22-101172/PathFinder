from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union

# ══════════════════════════════════════════════════════════════════
# BASE RESULT
# ══════════════════════════════════════════════════════════════════
class ALEBaseResult(BaseModel):
    status: str
    decision: str
    reason_codes: List[str] = []
    missing_requirements: List[str] = []
    warnings: List[str] = []
    required_data_missing: List[str] = []
    next_steps: List[str] = []

# ══════════════════════════════════════════════════════════════════
# SHARED STUDENT MODELS
# ══════════════════════════════════════════════════════════════════
class CourseHistoryRecord(BaseModel):
    course_code: str
    course_name: str = ""
    credit_hours: int = 0
    grade: Optional[str] = None
    grade_points: Optional[float] = None
    semester_taken: str = ""
    status: str

class StudentContext(BaseModel):
    student_id: str = ""
    name: str = ""
    track_id: str
    level: int = 0
    current_semester: str = ""

    cgpa: float
    academic_standing: str
    total_credit_hours_earned: int
    credit_hours_remaining: int = 0
    max_credit_hours_allowed: int = 0

    military_status: Optional[str] = None
    study_status: str = "Studying"
    consecutive_warnings: int = 0
    total_warnings: int = 0
    last_semester_warning: Optional[int] = None
    last_semester_gpa: Optional[float] = None
    last_semester_chs: Optional[int] = None
    last_semester_cps: Optional[float] = None
    last_semester_phs: Optional[int] = None
    cumulative_chs: Optional[int] = None
    cumulative_cps: Optional[float] = None
    cumulative_phs: Optional[int] = None
    current_semester_chs: int = 0
    first_semester: str = ""

    course_history: List[CourseHistoryRecord] = []

    completed_courses: List[str] = []
    failed_courses: List[str] = []
    in_progress_courses: List[str] = []
    planned_courses: List[str] = []

# ══════════════════════════════════════════════════════════════════
# A3 — ELIGIBILITY CHECK
# ══════════════════════════════════════════════════════════════════
class NonCoursePrereq(BaseModel):
    type: str
    course: Optional[str] = None
    min_grade_points: Optional[float] = None
    min_credits: Optional[int] = None

class CoursePrereqs(BaseModel):
    direct: List[Union[str, List[str]]] = []
    non_course: List[NonCoursePrereq] = []

class TargetCourse(BaseModel):
    course_code: str
    credits: int = 0
    prerequisites: CoursePrereqs = Field(default_factory=CoursePrereqs)

class TermContext(BaseModel):
    term: str = ""
    is_final_semester: bool = False

class StudentSnapshot(BaseModel):
    completed_courses: List[str] = []
    in_progress_courses: List[str] = []
    failed_courses: List[str] = []
    cgpa: float = 0.0
    total_credit_hours_earned: int = 0
    max_credit_hours_allowed: int = 21
    total_credit_hours_in_progress: int = 0
    course_grade_points: Dict[str, float] = {}

class EligibilityInput(BaseModel):
    student_snapshot: StudentSnapshot
    target_course: TargetCourse
    term_context: TermContext = Field(default_factory=TermContext)

class EligibilityResult(ALEBaseResult):
    eligible: bool
    reasoning: str = ""

# ══════════════════════════════════════════════════════════════════
# A4 — GRADUATION AUDIT
# ══════════════════════════════════════════════════════════════════
class CurriculumRules(BaseModel):
    track_id: str
    total_credits_required: int = 133
    minimum_semesters: int = 6
    required_courses: List[str] = []

class GraduationAuditInput(BaseModel):
    student_context: StudentContext
    curriculum_rules: Optional[CurriculumRules] = None

class GraduationAuditResult(ALEBaseResult):
    credits_earned: int = 0
    credits_required: int = 133
    credits_remaining: int = 0
    unmet_required_courses: List[str] = []
    failed_required_courses: List[str] = []
    in_progress_required_courses: List[str] = []
    semesters_completed: int = 0
    cgpa_check: bool = False
    standing_check: bool = False
    military_check: bool = False
    warnings_check: bool = False

# ══════════════════════════════════════════════════════════════════
# A5 — SEMESTER PLAN
# ══════════════════════════════════════════════════════════════════
class Offering(BaseModel):
    course_code: str
    course_name: str = ""
    credits: int
    is_core: bool = False
    prerequisites: CoursePrereqs = Field(default_factory=CoursePrereqs)
    co_requisites: List[str] = []

class PlanConstraints(BaseModel):
    max_credits: int = 0
    term: str = ""
    is_final_semester: bool = False

class SemesterPlanInput(BaseModel):
    student_context: StudentContext
    available_offerings: List[Offering]
    curriculum_rules: Optional[CurriculumRules] = None
    constraints: PlanConstraints = Field(default_factory=PlanConstraints)

class PlanItem(BaseModel):
    course_code: str
    course_name: str = ""
    credits: int
    reason: str
    unlocks_count: int = 0

class PlanOption(BaseModel):
    plan_name: str
    total_credits: int
    courses: List[PlanItem]

class SemesterPlanResult(ALEBaseResult):
    plan_options: List[PlanOption] = []
    max_credits_allowed: int = 0
    credit_limit_source: str = ""

# ══════════════════════════════════════════════════════════════════
# C2 — GPA SIMULATION
# ══════════════════════════════════════════════════════════════════
class SimCourse(BaseModel):
    course_code: str
    credits: int
    expected_grade: Optional[str] = None
    expected_grade_points: Optional[float] = None

class SimulationScenario(BaseModel):
    courses: List[SimCourse]

class GPASimulationInput(BaseModel):
    student_context: StudentContext
    simulation_scenario: SimulationScenario

class GPASimulationResult(ALEBaseResult):
    projected_gpa: float = 0.0
    gpa_delta: float = 0.0
    is_simulation: bool = True
    disclaimer: str = "This is a projected estimate, not an official GPA value."
    simulation_courses: List[SimCourse] = []
