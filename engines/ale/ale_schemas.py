"""
ale/schemas.py
==============
Single source of truth for all ALE Pydantic models.

Sections:
    1. Rule Bundle Models         — injected from RAG, never hardcoded in ALE logic
    2. Shared Sub-models          — reused across multiple functions
    3. Function Input Models      — one per ALE function
    4. Output Sub-models          — nested models used inside output models
    5. Function Output Models     — one per ALE function

Step 5 additions (fields not in Step 4 contract, added during algorithm design):
    - PlannedCourseGPA.improve_retake_number
    - PlannedCourseGPA.is_currently_in_progress
    - SimulateGPAForwardInput.excluded_in_progress_courses
    - SolveTargetGPAInput.planned_course_source
    - GenerateGraduationRoadmapInput.military_status
    - GenerateGraduationRoadmapInput.completed_regular_semesters
    - GenerateGraduationRoadmapInput.starting_year

Audit additions (Phase 1 ALE audit — added during function-by-function audit):
    - PlannedCourseGPA.attempt_type: str → Literal["first_attempt","failed_retake","improve_retake"]
    - PlannedCourseTarget.attempt_type: str → same Literal
    - CheckCourseEligibilityInput.attempt_type: str → same Literal
    - GenerateSemesterPlanInput.target_semester_type: str → Literal["Fall","Spring","Summer"]
    - GenerateSemesterPlanInput.student_level: str → Literal["Freshman","Sophomore","Junior","Senior"]
    - GenerateGraduationRoadmapInput.student_level: str → same student-level Literal
    - GenerateGraduationRoadmapInput.target_semester_type: str → same semester-type Literal
    - GenerateGraduationRoadmapInput.target_end_semester_type: target-semester mode stop
    - GenerateGraduationRoadmapInput.target_end_year: target-semester mode stop
    - GenerateGraduationRoadmapInput.consecutive_warnings: warning projection input
    - GenerateGraduationRoadmapInput.total_warnings: warning projection input
    - GenerateGraduationRoadmapInput.warning_rules: warning projection rule bundle
    - GenerateGraduationRoadmapInput.failed_retake_grade_cap_points: pre-resolved by adapter
    - SolveTargetGPAInput.completed_regular_semesters: multi-semester remaining-sems calculation
    - GenerateGraduationRoadmapOutput.target_reached_without_graduation: target-semester mode result
    - GenerateGraduationRoadmapOutput.projected_consecutive_warnings: warning projection output
    - GenerateGraduationRoadmapOutput.projected_total_warnings: warning projection output
    - GenerateGraduationRoadmapOutput.warning_limit_reached_in_semester: warning projection output
"""

from typing import Literal

from pydantic import BaseModel, Field


# =============================================================================
# SECTION 1 — RULE BUNDLE MODELS
# Values injected at runtime from RAG. Never hardcoded in ALE logic.
# =============================================================================

class RetakeRules(BaseModel):
    """
    Governs rules for retaking failed or passed courses.
    Used by: check_course_eligibility, simulate_gpa_forward, solve_target_gpa,
             generate_semester_plan, generate_graduation_roadmap (failed-retake cap
             pre-resolved from failed_first_retake_grade_cap + GradingScaleRules by ALEAdapter)
    """
    failed_first_retake_grade_cap: str = Field(
        description="Max letter grade on first retake after failure. Handbook: B"
    )
    improve_retake_first_attempt_cap: str | None = Field(
        description="Cap on first improve retake. Handbook: None (no cap — highest grade achieved)"
    )
    improve_retake_subsequent_cap: str = Field(
        description="Cap on second+ improve retake. Handbook: B"
    )
    improve_retake_max_courses_cgpa_above_2: int = Field(
        description="Max distinct courses a student with CGPA >= 2.0 can improve-retake. Handbook: 3"
    )
    improve_retake_unlimited_below_cgpa: float = Field(
        description="Below this CGPA, improve retakes are unlimited. Handbook: 2.0"
    )


class CreditLimitRules(BaseModel):
    """
    Governs credit hour limits per regular semester based on CGPA.
    Used by: generate_semester_plan, generate_graduation_roadmap
    """
    cgpa_above_3_limit: int = Field(
        description="Max credits when CGPA >= 3.0. Handbook: 21. Requires dean approval."
    )
    cgpa_between_2_and_3_limit: int = Field(
        description="Max credits when 2.0 <= CGPA < 3.0. Handbook: 18"
    )
    cgpa_between_1_and_2_limit: int = Field(
        description="Max credits when 1.0 <= CGPA < 2.0. Handbook: 15"
    )
    cgpa_below_1_limit: int = Field(
        description="Max credits when CGPA < 1.0. Handbook: 12"
    )
    minimum_per_semester: int = Field(
        description="Minimum credits to register per semester. Below this -> advisor warning. Handbook: 9"
    )
    final_semester_override: int = Field(
        description="Max credits in final graduation semester regardless of CGPA. Handbook: 21. Requires dean approval."
    )
    incomplete_extra_course_allowed: bool = Field(
        description="Whether a student with Incomplete grade may register one extra course. Handbook: True. Requires faculty council approval — flagged as warning, never auto-applied."
    )


class SummerSemesterRules(BaseModel):
    """
    Governs course count limits for summer semester.
    Summer uses course count limit, not credit hour limit.
    Used by: generate_semester_plan, generate_graduation_roadmap
    """
    default_max_courses: int = Field(
        description="Default max courses in summer. Handbook: 2"
    )
    cgpa_above_3_max_courses: int = Field(
        description="Max courses in summer when CGPA >= threshold. Handbook: 3"
    )
    cgpa_threshold_for_extra_course: float = Field(
        description="CGPA threshold to access higher summer course limit. Handbook: 3.0"
    )


class GraduationRequirementRules(BaseModel):
    """
    Governs official graduation requirements.
    Used by: run_graduation_audit, generate_semester_plan, generate_graduation_roadmap
    Note: military_deferred_pass_course_code removed — HUM011 does not exist in catalogue.
    Military check simplified to: military_status in [Done, Exempted].
    """
    total_credits_required: int = Field(
        description="Total passed credit hours required for graduation. Handbook: 133"
    )
    minimum_cgpa: float = Field(
        description="Minimum CGPA at graduation time. Handbook: 2.0"
    )
    minimum_regular_semesters: int = Field(
        description="Minimum Fall/Spring semesters required. Handbook: 6"
    )
    maximum_regular_semesters: int = Field(
        description="Maximum regular semesters before dismissal. Handbook: 16"
    )
    must_pass_zero_credit_courses: bool = Field(
        description="All 0-credit program courses must be passed. Handbook: True"
    )
    military_training_required_for_males: bool = Field(
        description="Male students must pass military training. Handbook: True"
    )


class AcademicWarningRules(BaseModel):
    """
    Governs academic warning issuance and dismissal conditions.
    Used by: run_graduation_audit, generate_graduation_roadmap
    """
    cgpa_warning_threshold: float = Field(
        description="CGPA below this triggers academic warning (except first semester). Handbook: 2.0"
    )
    max_consecutive_warnings: int = Field(
        description="Consecutive warnings triggering dismissal. Handbook: 4"
    )
    max_total_warnings: int = Field(
        description="Total separate warnings triggering dismissal. Handbook: 6"
    )
    warning_exempt_first_semester: bool = Field(
        description="First semester is exempt from academic warning. Handbook: True"
    )
    dismissal_extension_credits_percentage: float = Field(
        description="Fraction of total credits that must be passed to be eligible for dismissal appeal. Handbook: 0.80"
    )
    dismissal_extension_extra_semesters: int = Field(
        description="Extra regular semesters granted on successful appeal. Handbook: 2"
    )
    dismissal_extension_extra_summer_semesters: int = Field(
        description="Extra summer semesters granted on successful appeal. Handbook: 1"
    )


class HonorsRules(BaseModel):
    """
    Governs honors degree eligibility criteria.
    Used by: run_graduation_audit (as sub-result, always computed)
    """
    minimum_cgpa_throughout: float = Field(
        description="CGPA must never drop below this at any semester boundary. Handbook: 3.0"
    )
    minimum_semesters: int = Field(
        description="Must complete at least this many regular semesters. Handbook: 6"
    )
    maximum_semesters: int = Field(
        description="Must not exceed this many regular semesters. Handbook: 8"
    )
    no_f_grade_allowed: bool = Field(
        description="Student must never have received F or Abs grade. Handbook: True"
    )
    no_disciplinary_penalties: bool = Field(
        description="No disciplinary penalties throughout study. Cannot be verified by system — always returns cannot_verify."
    )


class PercentageRange(BaseModel):
    """A single percentage-to-letter-grade mapping range."""
    min_pct: float
    max_pct: float
    letter: str


class GradingScaleRules(BaseModel):
    """
    Maps between letter grades, percentages, and grade points.
    Used by: simulate_gpa_forward, solve_target_gpa, grade_resolver (shared utility),
             ALEAdapter (pre-resolves failed_retake_grade_cap_points for roadmap)
    letter_to_points: maps letter grade -> grade points (None for P-grade, 0.0 for Abs)
    percentage_to_letter: ordered list of ranges, highest to lowest
    """
    letter_to_points: dict[str, float | None] = Field(
        description="Letter grade to grade points mapping. P maps to None (gpa_neutral)."
    )
    percentage_to_letter: list[PercentageRange] = Field(
        description="Ordered percentage ranges mapping to letter grades. Scan top-down to find match."
    )


class StudentLevelRules(BaseModel):
    """
    Governs student academic level thresholds based on passed credit hours.
    Used by: generate_semester_plan, generate_graduation_roadmap
    Source: CIS Handbook — Student Level thresholds.
    """
    freshman_max_hours: int = Field(description="0 to this value = Freshman")
    sophomore_min_hours: int
    sophomore_max_hours: int
    junior_min_hours: int
    junior_max_hours: int
    senior_min_hours: int
    senior_max_hours: int = Field(description="= total_credits_required")


# =============================================================================
# SECTION 2 — SHARED SUB-MODELS
# Reused across multiple functions.
# =============================================================================

class AvailableCourse(BaseModel):
    """
    A course available for planning. Used by generate_semester_plan and
    generate_graduation_roadmap. Populated from KG get_courses_by_track.
    semester_offering is metadata — orchestrator has already filtered for
    Fall/Spring plans. ALE filters per pass for roadmap.
    """
    course_code: str
    name: str
    credits: int
    level: int = Field(description="Course level 1-4")
    semester_offering: list[str] = Field(
        description="e.g. ['Fall', 'Spring']. Metadata only for semester plan. ALE filters per pass for roadmap."
    )
    prerequisites: list[str] = Field(
        default_factory=list,
        description="Direct prerequisite course codes"
    )
    credit_threshold: int | None = Field(
        default=None,
        description="Minimum passed credit hours required as prerequisite, if applicable"
    )
    track: list[str] = Field(
        default_factory=list,
        description="Track IDs this course belongs to"
    )


class PlannedCourse(BaseModel):
    """
    A course selected for inclusion in a semester plan or roadmap pass.
    Used by generate_semester_plan and generate_graduation_roadmap output.
    priority_level values: retake | high_unlock | level_match | standard
    """
    course_code: str
    course_name: str
    credits: int
    reason: str = Field(
        description="Plain-English justification e.g. 'Required retake — previously failed', 'Core requirement — unlocks 3 courses'"
    )
    priority_level: str = Field(
        description="retake | high_unlock | level_match | standard"
    )
    is_retake: bool


class GradeOverride(BaseModel):
    """
    Records a grade that was capped by retake rules.
    Used by simulate_gpa_forward and solve_target_gpa output.
    """
    course_code: str
    requested_grade: str | float = Field(
        description="What the student hypothesized or targeted (raw input format)"
    )
    applied_grade: str = Field(
        description="The grade actually used after retake cap e.g. 'B'"
    )
    cap_reason: str = Field(
        description="e.g. 'First retake after failure — maximum grade is B'"
    )


class CourseHistoryEntry(BaseModel):
    """
    A single attempt entry from the student's transcript history.
    Used by run_graduation_audit for honors CGPA trajectory computation.
    status values: passed | failed | in_progress | withdrawn | incomplete | con
    semester_type values: regular | summer
    Abs grade maps to status=failed with grade_points=0.0
    """
    course_code: str
    semester: str = Field(description="e.g. 'Fall 2023'")
    semester_type: str = Field(description="regular | summer")
    grade_points: float | None = Field(
        default=None,
        description="None if in_progress, withdrawn, incomplete, or con"
    )
    credits: int
    status: str = Field(
        description="passed | failed | in_progress | withdrawn | incomplete | con"
    )


# =============================================================================
# SECTION 3 — FUNCTION INPUT MODELS
# One model per ALE function.
# =============================================================================

class PlannedCourseGPA(BaseModel):
    """
    A course with a hypothesized grade for GPA simulation.
    Used by SimulateGPAForwardInput.
    Step 5 additions: improve_retake_number, is_currently_in_progress
    """
    course_code: str
    course_name: str
    credits: int = Field(description="0 is valid for zero-credit courses — treated as gpa_neutral")
    expected_grade: str | float = Field(
        description="Letter grade, percentage (>4.0), or grade points (0.0-4.0)"
    )
    attempt_type: Literal["first_attempt", "failed_retake", "improve_retake"] = Field(
        description="first_attempt | failed_retake | improve_retake"
    )
    has_cgpa_footprint: bool = Field(
        description="True if course has a previous Passed/Failed/Abs attempt in history"
    )
    old_grade: str | float | None = Field(
        default=None,
        description="Required when has_cgpa_footprint=True. Raw format — ALE resolves."
    )
    # Step 5 addition — needed to distinguish first vs subsequent improve retake cap
    improve_retake_number: int | None = Field(
        default=None,
        description="1 = first improve retake (no cap), 2+ = subsequent (B cap). None when attempt_type != improve_retake. SC derives from transcript history."
    )
    # Step 5 addition — needed to add per-course in-progress warning in output
    is_currently_in_progress: bool = Field(
        default=False,
        description="True if this course is currently in-progress and student explicitly named it. Grade is hypothetical."
    )


class SimulateGPAForwardInput(BaseModel):
    """Input for simulate_gpa_forward."""
    current_cgpa: float = Field(description="Maps from Cumulative GPA — stable official snapshot")
    gpa_counted_credits: int = Field(description="Maps from Cumulative CHs — GPA denominator")
    current_quality_points: float = Field(description="Maps from Cumulative CPs — GPA numerator. Required — no fallback.")
    planned_courses: list[PlannedCourseGPA]
    grading_scale_rules: GradingScaleRules
    retake_rules: RetakeRules
    # Step 5 addition — orchestrator passes which in-progress courses were silently excluded
    excluded_in_progress_courses: list[str] = Field(
        default_factory=list,
        description="Course codes of in-progress courses excluded from snapshot. Used to generate warning in output."
    )


class PlannedCourseTarget(BaseModel):
    """
    A course for target GPA solving.
    Used by SolveTargetGPAInput.
    Differs from PlannedCourseGPA: no expected_grade (ALE solves for it),
    adds related_completed_course and historical_grade for personalization layer.
    Step 5 addition: improve_retake_number
    """
    course_code: str
    course_name: str
    credits: int = Field(description="0 is valid — treated as gpa_neutral")
    attempt_type: Literal["first_attempt", "failed_retake", "improve_retake"] = Field(
        description="first_attempt | failed_retake | improve_retake"
    )
    has_cgpa_footprint: bool
    old_grade: str | float | None = Field(
        default=None,
        description="Required when has_cgpa_footprint=True"
    )
    # Step 5 addition
    improve_retake_number: int | None = Field(
        default=None,
        description="1 = first improve retake (no cap), 2+ = subsequent (B cap). None when not improve_retake."
    )
    # For personalized distribution layer
    related_completed_course: str | None = Field(
        default=None,
        description="Direct prerequisite found in student's completed history. From KG get_prerequisites."
    )
    historical_grade: str | float | None = Field(
        default=None,
        description="Grade in related_completed_course. Required when related_completed_course is not None."
    )


class SolveTargetGPAInput(BaseModel):
    """Input for solve_target_gpa."""
    current_cgpa: float
    gpa_counted_credits: int = Field(description="Maps from Cumulative CHs")
    current_quality_points: float = Field(description="Maps from Cumulative CPs. Required — no fallback.")
    target_cgpa: float = Field(description="What the student wants to reach")
    planned_courses: list[PlannedCourseTarget]
    grading_scale_rules: GradingScaleRules
    retake_rules: RetakeRules
    graduation_rules: GraduationRequirementRules = Field(
        description="Used for max_regular_semesters in multi-semester projection hard stop"
    )
    assumed_grade_per_semester: str | float | None = Field(
        default=None,
        description="For multi-semester projection. Letter, percentage, or grade points. Defaults to B (3.0) if None."
    )
    credits_per_semester: int = Field(
        default=18,
        description="Credits assumed per semester in projection. Orchestrator provides default of 18."
    )
    completed_regular_semesters: int | None = Field(
        default=None,
        description="Count of completed Fall/Spring semesters. When provided, multi-semester projection uses "
                    "remaining semesters: max(0, maximum_regular_semesters - completed_regular_semesters)."
    )
    # Step 5 addition
    planned_course_source: str = Field(
        default="explicit_user_courses",
        description="explicit_user_courses | current_in_progress_courses. Echoed in output for Composer framing."
    )


class CheckCourseEligibilityInput(BaseModel):
    """Input for check_course_eligibility."""
    target_course_code: str = Field(description="Resolved via KG entity mapper — never raw student input")
    target_course_prerequisites: list[str] = Field(
        default_factory=list,
        description="Direct prerequisite course codes from KG get_prerequisites"
    )
    target_course_credit_threshold: int | None = Field(
        default=None,
        description="Minimum passed credit hours as prerequisite, if applicable"
    )
    completed_courses: list[str] = Field(description="Course codes of all passed courses")
    in_progress_courses: list[str] = Field(description="Course codes currently being taken")
    retake_count: dict[str, int] = Field(
        default_factory=dict,
        description="{course_code: attempt_count} for all attempted courses"
    )
    total_improve_retakes_used: int = Field(
        description="Lifetime count of improve retakes used across all courses"
    )
    cumulative_passed_hours: int = Field(description="Maps from Cumulative PHs")
    current_cgpa: float = Field(description="Needed to determine which improve retake ruleset applies")
    attempt_type: Literal["first_attempt", "failed_retake", "improve_retake"] = Field(
        description="first_attempt | failed_retake | improve_retake"
    )
    retake_rules: RetakeRules


class RunGraduationAuditInput(BaseModel):
    """Input for run_graduation_audit."""
    study_status: str = Field(
        description="Studying | Graduated | Suspended | Frozen | Transferred Out"
    )
    completed_courses: list[str] = Field(description="Course codes of all passed courses")
    failed_courses: list[str] = Field(description="Current failures with no subsequent pass")
    in_progress_courses: list[str] = Field(description="Course codes currently being taken")
    current_cgpa: float = Field(description="Maps from Cumulative GPA")
    cumulative_passed_hours: int = Field(description="Maps from Cumulative PHs — passed only, not CHs")
    consecutive_warnings: int = Field(
        description="Current consecutive warning count. First-semester exemption already applied by registrar."
    )
    total_warnings: int = Field(description="Total warning count throughout study")
    military_status: str | None = Field(
        description="Done | Exempted | Deferred | None. None for female students."
    )
    completed_regular_semesters: int = Field(
        description="Count of distinct Fall/Spring semesters with registered courses"
    )
    zero_credit_courses_passed: bool = Field(
        description="Pre-computed by orchestrator: all 0-credit course codes checked against completed_courses"
    )
    course_history: list[CourseHistoryEntry] = Field(
        description="Full attempt history for honors CGPA trajectory computation"
    )
    graduation_rules: GraduationRequirementRules
    warning_rules: AcademicWarningRules
    honors_rules: HonorsRules


class GenerateSemesterPlanInput(BaseModel):
    """Input for generate_semester_plan."""
    study_status: str
    completed_courses: list[str]
    failed_courses: list[str]
    in_progress_courses: list[str]
    retake_count: dict[str, int] = Field(default_factory=dict)
    total_improve_retakes_used: int
    current_cgpa: float
    cumulative_passed_hours: int
    student_level: Literal["Freshman", "Sophomore", "Junior", "Senior"] = Field(
        description="Freshman | Sophomore | Junior | Senior"
    )
    official_track: str | None = Field(description="Assigned track or None if General track")
    incomplete_grade_flag: bool = Field(description="Whether student has unresolved Incomplete grade")
    available_courses: list[AvailableCourse] = Field(
        description="From KG get_courses_by_track. Already semester-filtered for Fall/Spring. Unfiltered for Summer."
    )
    credit_limit_rules: CreditLimitRules
    graduation_rules: GraduationRequirementRules
    summer_semester_rules: SummerSemesterRules | None = Field(
        default=None,
        description="Only provided when target_semester_type = Summer"
    )
    target_semester_type: Literal["Fall", "Spring", "Summer"] = Field(
        description="Fall | Spring | Summer"
    )
    target_track: str | None = Field(
        default=None,
        description="Stated target track for unofficial track students"
    )
    target_credit_load: int | None = Field(
        default=None,
        description="Optional student-specified credit cap"
    )
    max_credits_mode: bool = Field(
        default=False,
        description="If True, always applies CGPA-bracket maximum"
    )


class GenerateGraduationRoadmapInput(BaseModel):
    """
    Input for generate_graduation_roadmap.
    Step 5 additions vs Step 4 contract:
        - military_status: needed for non-course blocker detection
        - completed_regular_semesters: needed for loop termination condition
        - starting_year: needed to generate semester labels e.g. 'Fall 2026'
    Audit additions (Phase 1 ALE audit):
        - student_level: Literal — enforces valid values at construction
        - target_semester_type: Literal — same
        - target_end_semester_type / target_end_year: target-semester mode
        - consecutive_warnings / total_warnings / warning_rules: warning projection
        - failed_retake_grade_cap_points: pre-resolved by adapter from retake_rules+grading scale
    """
    study_status: str
    completed_courses: list[str]
    failed_courses: list[str]
    in_progress_courses: list[str]
    retake_count: dict[str, int] = Field(default_factory=dict)
    total_improve_retakes_used: int
    current_cgpa: float
    gpa_counted_credits: int = Field(description="Maps from Cumulative CHs — starting GPA denominator")
    current_quality_points: float = Field(description="Maps from Cumulative CPs — starting GPA numerator")
    cumulative_passed_hours: int
    student_level: Literal["Freshman", "Sophomore", "Junior", "Senior"] = Field(
        description="Freshman | Sophomore | Junior | Senior"
    )
    official_track: str | None
    incomplete_grade_flag: bool
    zero_credit_courses_passed: bool
    # Step 5 addition
    military_status: str | None = Field(
        description="Done | Exempted | Deferred | None. Needed for non-course blocker detection."
    )
    # Step 5 addition
    completed_regular_semesters: int = Field(
        description="Count of completed Fall/Spring semesters. Needed for loop termination."
    )
    available_courses: list[AvailableCourse] = Field(
        description="Full curriculum from KG — no semester filter. Field Training excluded by orchestrator."
    )
    credit_limit_rules: CreditLimitRules
    graduation_rules: GraduationRequirementRules
    summer_semester_rules: SummerSemesterRules | None = Field(
        default=None,
        description="Only provided when accelerated_mode=True or starting semester is Summer"
    )
    target_semester_type: Literal["Fall", "Spring", "Summer"] = Field(
        description="Starting semester for first pass: Fall | Spring | Summer"
    )
    # Step 5 addition
    starting_year: int = Field(
        description="Calendar year of first pass e.g. 2026. Orchestrator derives from academic calendar."
    )
    target_track: str | None = Field(
        default=None,
        description="Mandatory if no official track. Orchestrator short-circuits if both are missing."
    )
    accelerated_mode: bool = Field(default=False)
    max_credits_mode: bool = Field(default=False)
    target_credit_load: int | None = Field(default=None)
    assumed_grade_per_pass: float | None = Field(
        default=None,
        description="Pre-resolved to grade points by orchestrator. Defaults to 2.6 (C+) if None."
    )
    student_level_rules: StudentLevelRules
    # Target-semester mode — both must be provided together or both None
    target_end_semester_type: Literal["Fall", "Spring", "Summer"] | None = Field(
        default=None,
        description="If provided with target_end_year, ALE stops simulation at this semester."
    )
    target_end_year: int | None = Field(
        default=None,
        description="Calendar year of the target-end semester. Must accompany target_end_semester_type."
    )
    # Warning projection — only active when warning_rules is provided
    consecutive_warnings: int = Field(
        default=0,
        description="Current consecutive academic warning count from student record."
    )
    total_warnings: int = Field(
        default=0,
        description="Lifetime total academic warning count from student record."
    )
    warning_rules: AcademicWarningRules | None = Field(
        default=None,
        description="When provided, ALE simulates warning progression and stops at warning limit."
    )
    # Failed-retake grade cap — pre-resolved from retake_rules+grading_scale by adapter
    failed_retake_grade_cap_points: float | None = Field(
        default=None,
        description="Max grade points for first retake after failure. Adapter resolves from RetakeRules."
    )


# =============================================================================
# SECTION 4 — OUTPUT SUB-MODELS
# Nested models used inside function output models.
# =============================================================================

# --- simulate_gpa_forward sub-models ---

class CourseContribution(BaseModel):
    """Per-course breakdown entry in simulate_gpa_forward output."""
    course_code: str
    course_name: str
    credits: int
    input_grade: str | float = Field(description="Raw value student provided")
    resolved_grade_points: float | None = Field(description="After format resolution. None if gpa_neutral.")
    applied_grade_points: float | None = Field(description="After retake cap. None if gpa_neutral.")
    quality_points_contribution: float | None = Field(
        description="applied_grade_points x credits. None if gpa_neutral."
    )
    footprint_action: str = Field(description="replacement | addition | gpa_neutral")
    in_progress_note: str | None = Field(
        default=None,
        description="Set when is_currently_in_progress=True: grade is hypothetical until officially released."
    )


# --- solve_target_gpa sub-models ---

class CourseTarget(BaseModel):
    """Per-course target in solve_target_gpa distribution output."""
    course_code: str
    course_name: str
    credits: int
    target_grade_points: float
    target_grade_letter: str = Field(description="Nearest letter from grading scale")
    is_personalized: bool = Field(description="True if this course's target differs from required average")


class MultiSemesterProjection(BaseModel):
    """Multi-semester forward projection when target is impossible in current semester."""
    status: str = Field(description="reachable | cannot_reach_within_program")
    semesters_needed: int | None = Field(description="None if cannot_reach_within_program")
    assumed_grade_points: float
    assumed_grade_letter: str
    credits_per_semester_used: int
    projected_cgpa_per_semester: list[float] = Field(
        description="CGPA after each projected semester"
    )


# --- run_graduation_audit sub-models ---

class AuditCheck(BaseModel):
    """Result of a single graduation requirement check."""
    name: str = Field(
        description="cgpa_check | credits_check | semesters_check | maximum_semesters_check | military_check | zero_credit_check | warning_dismissal_check"
    )
    passed: bool
    actual_value: str | float | int = Field(description="What the student currently has")
    required_value: str | float | int = Field(description="What is required")
    gap: str | None = Field(description="e.g. '13 credits remaining'. None if passed.")


class HonorsCheck(BaseModel):
    """Result of a single honors eligibility criterion check."""
    name: str = Field(
        description="cgpa_throughout | semester_count | no_failures | no_disciplinary_penalties"
    )
    passed: bool | None = Field(description="None when cannot_verify (disciplinary check) or uncertain (semester count)")
    actual_value: str | float | int | None
    required_value: str | float | int | None
    note: str | None = Field(
        default=None,
        description="Registrar disclaimer for disciplinary check, or uncertainty note for semester count"
    )


# --- generate_semester_plan sub-models ---

class SemesterPlan(BaseModel):
    """A single plan option within generate_semester_plan output."""
    plan_id: str = Field(description="e.g. 'plan_a', 'plan_b'")
    plan_label: str = Field(description="e.g. 'Recommended', 'Lighter Load', 'Level Focused'")
    courses: list[PlannedCourse]
    total_credits: int


# --- generate_graduation_roadmap sub-models ---

class RoadmapSemester(BaseModel):
    """A single semester pass in the graduation roadmap."""
    pass_number: int = Field(description="1, 2, 3...")
    semester_label: str = Field(description="e.g. 'Fall 2026'")
    semester_type: str = Field(description="regular | summer")
    courses: list[PlannedCourse]
    total_credits: int
    simulated_cgpa_after: float
    cumulative_passed_hours_after: int
    is_final_semester: bool
    warnings: list[str] = Field(default_factory=list)


# =============================================================================
# SECTION 5 — FUNCTION OUTPUT MODELS
# One model per ALE function.
# All inherit base fields: status, reason_codes, warnings, required_data_missing
# =============================================================================

class SimulateGPAForwardOutput(BaseModel):
    """Output of simulate_gpa_forward."""
    # Base fields
    status: str = Field(description="projected | cannot_compute")
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_data_missing: list[str] = Field(default_factory=list)
    # Result fields
    current_cgpa: float
    projected_cgpa: float | None = Field(description="None when cannot_compute")
    delta: float | None = Field(description="projected_cgpa - current_cgpa. Positive = improvement. None when cannot_compute.")
    simulation_label: str = Field(
        default="Hypothetical simulation — not official academic standing."
    )
    per_course_breakdown: list[CourseContribution] = Field(default_factory=list)
    grade_overrides: list[GradeOverride] = Field(default_factory=list)
    gpa_neutral_courses: list[str] = Field(
        default_factory=list,
        description="Course codes excluded from GPA math (P-grade or 0-credit)"
    )


class SolveTargetGPAOutput(BaseModel):
    """Output of solve_target_gpa."""
    # Base fields
    status: str = Field(description="solvable | impossible | already_met | cannot_compute")
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_data_missing: list[str] = Field(default_factory=list)
    # Core result
    current_cgpa: float
    target_cgpa: float
    delta_needed: float | None = Field(description="target_cgpa - current_cgpa. None when cannot_compute.")
    required_average_grade_points: float | None = Field(description="None when impossible/already_met/cannot_compute")
    required_average_letter: str | None = Field(description="Nearest letter grade. None when not applicable.")
    simulation_label: str = Field(
        default="Hypothetical simulation — not official academic standing."
    )
    gpa_neutral_courses: list[str] = Field(default_factory=list)
    # Distributions
    default_distribution: list[CourseTarget] | None = Field(
        default=None,
        description="Equal target per course. None when impossible/already_met/cannot_compute."
    )
    personalized_distribution: list[CourseTarget] | None = Field(
        default=None,
        description="Tailored targets. None if no prerequisite history available."
    )
    personalized_distribution_valid: bool | None = Field(
        default=None,
        description="None if personalized_distribution is None."
    )
    grade_overrides: list[GradeOverride] = Field(default_factory=list)
    # Impossible-only fields
    maximum_reachable_cgpa: float | None = Field(
        default=None,
        description="Only populated when status=impossible"
    )
    multi_semester_projection: MultiSemesterProjection | None = Field(
        default=None,
        description="Only populated when status=impossible"
    )
    # Composer framing
    planned_course_source: str = Field(
        default="explicit_user_courses",
        description="Echoed from input for Composer framing"
    )


class CheckCourseEligibilityOutput(BaseModel):
    """Output of check_course_eligibility."""
    # Base fields
    status: str = Field(
        description="eligible | not_eligible | already_completed | in_progress | retake_cap_exceeded | cannot_compute"
    )
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_data_missing: list[str] = Field(default_factory=list)
    # Result fields
    target_course_code: str = Field(description="Echoed for Composer")
    missing_prerequisites: list[str] = Field(default_factory=list)
    completed_prerequisites: list[str] = Field(default_factory=list)
    credit_threshold_required: int | None = Field(default=None)
    credit_threshold_met: bool | None = Field(default=None)
    retake_count_for_course: int = Field(description="How many times student has attempted this course")
    max_retake_cap: int | None = Field(
        default=None,
        description="3 when status=retake_cap_exceeded. None otherwise."
    )
    attempt_type: str = Field(description="Echoed from input for Composer framing")


class RunGraduationAuditOutput(BaseModel):
    """Output of run_graduation_audit."""
    # Base fields
    status: str = Field(
        description="eligible | not_eligible | already_graduated | not_auditable | dismissed_but_appeal_eligible | dismissed_no_appeal | cannot_compute"
    )
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_data_missing: list[str] = Field(default_factory=list)
    # Graduation result (populated when study_status=Studying)
    current_cgpa: float | None = Field(
        default=None,
        description="Echoed for Composer — needed for graduation grade label (Excellent/Very Good/Good/Pass)"
    )
    checks: list[AuditCheck] = Field(
        default_factory=list,
        description="One entry per graduation requirement checked. Empty for short-circuit cases."
    )
    next_steps: list[str] = Field(
        default_factory=list,
        description="Actionable plain-English items for student"
    )
    # Honors (always computed, always returned regardless of graduation status)
    honors_status: str | None = Field(
        default=None,
        description="eligible | not_eligible | cannot_fully_verify. None only for not_auditable."
    )
    honors_checks: list[HonorsCheck] = Field(
        default_factory=list,
        description="One entry per honors criterion. Always 4 entries when computed."
    )


class GenerateSemesterPlanOutput(BaseModel):
    """Output of generate_semester_plan."""
    # Base fields
    status: str = Field(description="plans_generated | not_applicable | no_eligible_courses | cannot_compute")
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_data_missing: list[str] = Field(default_factory=list)
    # Plan result (populated when status=plans_generated)
    target_semester_type: str | None = Field(default=None)
    credit_cap_applied: int | None = Field(
        default=None,
        description="Actual credit cap used. None if Summer (uses course count cap instead)."
    )
    course_count_cap_applied: int | None = Field(
        default=None,
        description="Course count cap used. None if not Summer."
    )
    is_final_semester: bool | None = Field(default=None)
    total_eligible_courses: int | None = Field(default=None)
    ineligibility_summary: str | None = Field(
        default=None,
        description="Brief summary of why courses were excluded. None if all eligible."
    )
    plans: list[SemesterPlan] = Field(default_factory=list)


class GenerateGraduationRoadmapOutput(BaseModel):
    """Output of generate_graduation_roadmap."""
    # Base fields
    status: str = Field(
        description="complete | cannot_complete_projection | blocked | not_applicable | cannot_compute"
    )
    reason_codes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_data_missing: list[str] = Field(default_factory=list)
    # Roadmap result
    total_passes: int = Field(default=0)
    projected_graduation_semester: str | None = Field(
        default=None,
        description="e.g. 'Spring 2027'. None if blocked or cannot_complete."
    )
    non_course_blockers: list[str] = Field(
        default_factory=list,
        description="e.g. ['cgpa_below_minimum', 'military_training_required', 'zero_credit_courses_required']"
    )
    remaining_graduation_gaps: list[str] | None = Field(
        default=None,
        description="Only populated on blocked/cannot_complete. e.g. ['25 credit hours still needed']"
    )
    simulation_disclaimer: str = Field(
        default="Projection based on assumed performance — not official academic plan."
    )
    semester_mode: str = Field(default="regular", description="regular | accelerated")
    credit_mode: str = Field(default="default", description="default | max_credits | student_specified")
    simulation_grade_used: float | None = Field(
        default=None,
        description="Grade points assumed per pass e.g. 2.6 for C+"
    )
    semester_plans: list[RoadmapSemester] = Field(default_factory=list)
    # Target-semester mode result
    target_reached_without_graduation: bool = Field(
        default=False,
        description="True when target-end semester was reached but graduation requirements are not met."
    )
    # Warning projection output — only populated when warning_rules was provided
    projected_consecutive_warnings: int | None = Field(
        default=None,
        description="Projected consecutive warnings at simulation end. None when warning simulation inactive."
    )
    projected_total_warnings: int | None = Field(
        default=None,
        description="Projected total warnings at simulation end. None when warning simulation inactive."
    )
    warning_limit_reached_in_semester: str | None = Field(
        default=None,
        description="Semester label where warning limit was reached, e.g. 'Fall 2028'. None if not triggered."
    )
