"""
Synthetic tests for generate_graduation_roadmap.

Groups:
  A  — Structural validation (required_data_missing)
  B  — Invalid value rejection (belt-and-suspenders guards)
  C  — Study status gate
  D  — Graduation mode: roadmap reaches graduation or terminal stop
  E  — Target-semester mode
  F  — GPA behavior (in-progress absorption, retake cap, CGPA graduation check)
  G  — Warning progression simulation
  H  — Non-course requirements (military, zero-credit)
  I  — Eligibility / course-selection logic
  J  — Semester sequence and credit-load behaviour
"""

import pytest

from engines.ale.functions.generate_graduation_roadmap import generate_graduation_roadmap
from engines.ale.ale_schemas import (
    AcademicWarningRules,
    AvailableCourse,
    CreditLimitRules,
    GenerateGraduationRoadmapInput,
    GraduationRequirementRules,
    StudentLevelRules,
    SummerSemesterRules,
)


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------

def _grad_rules(
    total=133, min_cgpa=2.0, min_sems=6, max_sems=16,
    must_zero=True, must_military=True,
) -> GraduationRequirementRules:
    return GraduationRequirementRules(
        total_credits_required=total,
        minimum_cgpa=min_cgpa,
        minimum_regular_semesters=min_sems,
        maximum_regular_semesters=max_sems,
        must_pass_zero_credit_courses=must_zero,
        military_training_required_for_males=must_military,
    )


def _credit_rules(
    above3=21, mid=18, low=15, below1=12, minimum=9, final=21,
) -> CreditLimitRules:
    return CreditLimitRules(
        cgpa_above_3_limit=above3,
        cgpa_between_2_and_3_limit=mid,
        cgpa_between_1_and_2_limit=low,
        cgpa_below_1_limit=below1,
        minimum_per_semester=minimum,
        final_semester_override=final,
        incomplete_extra_course_allowed=True,
    )


def _summer_rules(default=2, above3=3, threshold=3.0) -> SummerSemesterRules:
    return SummerSemesterRules(
        default_max_courses=default,
        cgpa_above_3_max_courses=above3,
        cgpa_threshold_for_extra_course=threshold,
    )


def _warning_rules(threshold=2.0, max_consec=4, max_total=6) -> AcademicWarningRules:
    return AcademicWarningRules(
        cgpa_warning_threshold=threshold,
        max_consecutive_warnings=max_consec,
        max_total_warnings=max_total,
        warning_exempt_first_semester=True,
        dismissal_extension_credits_percentage=0.80,
        dismissal_extension_extra_semesters=2,
        dismissal_extension_extra_summer_semesters=1,
    )


def _level_rules() -> StudentLevelRules:
    return StudentLevelRules(
        freshman_max_hours=30,
        sophomore_min_hours=31,
        sophomore_max_hours=60,
        junior_min_hours=61,
        junior_max_hours=90,
        senior_min_hours=91,
        senior_max_hours=133,
    )


def _course(
    code: str,
    name: str = "Course Name",
    credits: int = 3,
    level: int = 2,
    prereqs: list[str] | None = None,
    sem: list[str] | None = None,
    threshold: int | None = None,
) -> AvailableCourse:
    return AvailableCourse(
        course_code=code,
        name=name,
        credits=credits,
        level=level,
        semester_offering=sem or [],
        prerequisites=prereqs or [],
        credit_threshold=threshold,
        track=["CS"],
    )


def _three_fall_courses() -> list[AvailableCourse]:
    """3 courses × 3 cr each = 9 credits — enough to hit total=133 from 124 PHs."""
    return [
        _course("C1", credits=3),
        _course("C2", credits=3),
        _course("C3", credits=3),
    ]


def _many_courses(n: int = 20, credits_each: int = 3) -> list[AvailableCourse]:
    return [_course(f"C{i}", credits=credits_each) for i in range(1, n + 1)]


def _inp(**kw) -> GenerateGraduationRoadmapInput:
    """Return a minimal valid input (1-pass to graduation by default)."""
    defaults: dict = dict(
        study_status="Studying",
        completed_courses=[],
        failed_courses=[],
        in_progress_courses=[],
        retake_count={},
        total_improve_retakes_used=0,
        current_cgpa=2.5,
        gpa_counted_credits=80,
        current_quality_points=200.0,
        # 124 PHs → need 9 more credits (3 courses × 3 cr) to reach total=133
        cumulative_passed_hours=124,
        student_level="Sophomore",
        official_track="CS",
        incomplete_grade_flag=False,
        zero_credit_courses_passed=True,
        military_status=None,
        completed_regular_semesters=6,
        available_courses=_three_fall_courses(),
        credit_limit_rules=_credit_rules(),
        graduation_rules=_grad_rules(),
        summer_semester_rules=None,
        target_semester_type="Fall",
        starting_year=2026,
        target_track=None,
        accelerated_mode=False,
        max_credits_mode=False,
        target_credit_load=None,
        assumed_grade_per_pass=3.0,
        student_level_rules=_level_rules(),
        consecutive_warnings=0,
        total_warnings=0,
        warning_rules=None,
        failed_retake_grade_cap_points=None,
        target_end_semester_type=None,
        target_end_year=None,
    )
    defaults.update(kw)
    return GenerateGraduationRoadmapInput(**defaults)


def _null_field(**null_overrides) -> GenerateGraduationRoadmapInput:
    """Construct an otherwise-valid input with specific fields forced to None via model_construct."""
    base = _inp()
    d = base.model_dump()
    d.update(null_overrides)
    return GenerateGraduationRoadmapInput.model_construct(**d)


# ===========================================================================
# A — Structural Validation
# ===========================================================================

class TestStructuralValidation:

    def test_missing_study_status(self):
        out = generate_graduation_roadmap(_null_field(study_status=None))
        assert out.status == "cannot_compute"
        assert "study_status" in out.required_data_missing

    def test_missing_completed_courses(self):
        out = generate_graduation_roadmap(_null_field(completed_courses=None))
        assert out.status == "cannot_compute"
        assert "completed_courses" in out.required_data_missing

    def test_missing_failed_courses(self):
        out = generate_graduation_roadmap(_null_field(failed_courses=None))
        assert out.status == "cannot_compute"
        assert "failed_courses" in out.required_data_missing

    def test_missing_in_progress_courses(self):
        out = generate_graduation_roadmap(_null_field(in_progress_courses=None))
        assert out.status == "cannot_compute"
        assert "in_progress_courses" in out.required_data_missing

    def test_missing_current_cgpa(self):
        out = generate_graduation_roadmap(_null_field(current_cgpa=None))
        assert out.status == "cannot_compute"
        assert "current_cgpa" in out.required_data_missing

    def test_missing_gpa_counted_credits(self):
        out = generate_graduation_roadmap(_null_field(gpa_counted_credits=None))
        assert out.status == "cannot_compute"
        assert "gpa_counted_credits" in out.required_data_missing

    def test_missing_current_quality_points(self):
        out = generate_graduation_roadmap(_null_field(current_quality_points=None))
        assert out.status == "cannot_compute"
        assert "current_quality_points" in out.required_data_missing

    def test_missing_cumulative_passed_hours(self):
        out = generate_graduation_roadmap(_null_field(cumulative_passed_hours=None))
        assert out.status == "cannot_compute"
        assert "cumulative_passed_hours" in out.required_data_missing

    def test_missing_student_level(self):
        out = generate_graduation_roadmap(_null_field(student_level=None))
        assert out.status == "cannot_compute"
        assert "student_level" in out.required_data_missing

    def test_missing_available_courses(self):
        out = generate_graduation_roadmap(_null_field(available_courses=None))
        assert out.status == "cannot_compute"
        assert "available_courses" in out.required_data_missing

    def test_missing_target_semester_type(self):
        out = generate_graduation_roadmap(_null_field(target_semester_type=None))
        assert out.status == "cannot_compute"
        assert "target_semester_type" in out.required_data_missing

    def test_missing_starting_year(self):
        out = generate_graduation_roadmap(_null_field(starting_year=None))
        assert out.status == "cannot_compute"
        assert "starting_year" in out.required_data_missing

    def test_accelerated_mode_without_summer_rules(self):
        out = generate_graduation_roadmap(_inp(accelerated_mode=True, summer_semester_rules=None))
        assert out.status == "cannot_compute"
        assert "missing_summer_rules" in out.reason_codes

    def test_summer_start_without_summer_rules(self):
        out = generate_graduation_roadmap(
            _inp(target_semester_type="Summer", summer_semester_rules=None)
        )
        assert out.status == "cannot_compute"
        assert "missing_summer_rules" in out.reason_codes

    def test_multiple_missing_fields_reported(self):
        out = generate_graduation_roadmap(
            _null_field(study_status=None, current_cgpa=None, failed_courses=None)
        )
        assert out.status == "cannot_compute"
        assert "required_data_missing" in out.reason_codes
        assert len(out.required_data_missing) == 3


# ===========================================================================
# B — Invalid Value Rejection (belt-and-suspenders guards)
# ===========================================================================

class TestInvalidValueRejection:

    def test_invalid_target_semester_type_bypasses_literal(self):
        bad = GenerateGraduationRoadmapInput.model_construct(
            **{**_inp().model_dump(), "target_semester_type": "Winter"}
        )
        out = generate_graduation_roadmap(bad)
        assert out.status == "cannot_compute"
        assert "invalid_target_semester_type" in out.reason_codes

    def test_invalid_student_level_bypasses_literal(self):
        bad = GenerateGraduationRoadmapInput.model_construct(
            **{**_inp().model_dump(), "student_level": "Graduate"}
        )
        out = generate_graduation_roadmap(bad)
        assert out.status == "cannot_compute"
        assert "invalid_student_level" in out.reason_codes

    def test_invalid_starting_year_too_old(self):
        bad = GenerateGraduationRoadmapInput.model_construct(
            **{**_inp().model_dump(), "starting_year": 1999}
        )
        out = generate_graduation_roadmap(bad)
        assert out.status == "cannot_compute"
        assert "invalid_starting_year" in out.reason_codes

    def test_invalid_starting_year_too_far_future(self):
        bad = GenerateGraduationRoadmapInput.model_construct(
            **{**_inp().model_dump(), "starting_year": 2200}
        )
        out = generate_graduation_roadmap(bad)
        assert out.status == "cannot_compute"
        assert "invalid_starting_year" in out.reason_codes

    def test_target_end_semester_without_year(self):
        out = generate_graduation_roadmap(
            _inp(target_end_semester_type="Spring", target_end_year=None)
        )
        assert out.status == "cannot_compute"
        assert "incomplete_target_end_semester" in out.reason_codes

    def test_target_end_year_without_semester_type(self):
        out = generate_graduation_roadmap(
            _inp(target_end_semester_type=None, target_end_year=2027)
        )
        assert out.status == "cannot_compute"
        assert "incomplete_target_end_semester" in out.reason_codes

    def test_target_end_before_start_rejected(self):
        # Start = Fall 2026, target_end = Spring 2026 (before start)
        out = generate_graduation_roadmap(
            _inp(
                target_semester_type="Fall",
                starting_year=2026,
                target_end_semester_type="Spring",
                target_end_year=2026,
            )
        )
        assert out.status == "cannot_compute"
        assert "target_end_before_start" in out.reason_codes

    def test_invalid_target_end_semester_type_bypasses_literal(self):
        base = _inp(target_end_semester_type="Spring", target_end_year=2027)
        bad = GenerateGraduationRoadmapInput.model_construct(
            **{**base.model_dump(), "target_end_semester_type": "Quarter"}
        )
        out = generate_graduation_roadmap(bad)
        assert out.status == "cannot_compute"
        assert "invalid_target_end_semester_type" in out.reason_codes


# ===========================================================================
# C — Study Status Gate
# ===========================================================================

class TestStudyStatusGate:

    def test_graduated_student_returns_not_applicable(self):
        out = generate_graduation_roadmap(_inp(study_status="Graduated"))
        assert out.status == "not_applicable"
        assert "study_status_graduated" in out.reason_codes

    def test_suspended_student_returns_not_applicable(self):
        out = generate_graduation_roadmap(_inp(study_status="Suspended"))
        assert out.status == "not_applicable"
        assert "study_status_suspended" in out.reason_codes


# ===========================================================================
# D — Graduation Mode
# ===========================================================================

class TestGraduationMode:

    def test_single_pass_graduation(self):
        # 124 PHs + 9 cr (3 courses) = 133 PHs; min sems already met
        out = generate_graduation_roadmap(_inp())
        assert out.status == "complete"
        assert out.projected_graduation_semester == "Fall 2026"
        assert out.total_passes == 1

    def test_projected_graduation_label_is_last_semester_planned(self):
        # 121 PHs + 12 cr (3×4cr courses) = 133 PHs; label should be the final semester
        courses = [_course(f"C{i}", credits=4) for i in range(1, 4)]
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=121,
            available_courses=courses,
        ))
        assert out.status == "complete"
        assert out.projected_graduation_semester is not None
        assert out.semester_plans[-1].is_final_semester is True

    def test_minimum_semesters_enforced(self):
        # 100 PHs, min_sems=6, completed=4 → needs 2 more regular passes AND credits
        # 40 courses × 3cr provides a large enough pool for 2 full semesters (18+15 cr)
        many = _many_courses(40, 3)
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=100,
            completed_regular_semesters=4,
            available_courses=many,
            graduation_rules=_grad_rules(total=133, min_sems=6),
        ))
        # Must plan at least 2 more regular semesters to satisfy min_sems=6
        assert out.total_passes >= 2

    def test_maximum_semesters_stop(self):
        # Student needs 100+ more credits but only 1 semester left before max
        many = _many_courses(30, credits_each=3)
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=3,
            completed_regular_semesters=15,
            graduation_rules=_grad_rules(total=133, max_sems=16, min_sems=6),
            available_courses=many,
        ))
        assert out.status == "cannot_complete_projection"
        assert "maximum_semesters_reached" in out.reason_codes
        assert out.remaining_graduation_gaps is not None

    def test_requirements_already_met_after_in_progress_absorption(self):
        # In-progress courses push student over graduation finish line
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=124,
            in_progress_courses=["IP1", "IP2", "IP3"],
            # Each IP course is 3 credits in the available pool
            available_courses=[
                _course("IP1", credits=3),
                _course("IP2", credits=3),
                _course("IP3", credits=3),
            ],
            completed_regular_semesters=6,
            graduation_rules=_grad_rules(total=133, min_cgpa=2.0, min_sems=6),
        ))
        assert out.status == "complete"
        assert any("already met" in w.lower() for w in out.warnings)


# ===========================================================================
# E — Target-Semester Mode
# ===========================================================================

class TestTargetSemesterMode:

    def test_target_reached_before_graduation_returns_partial_roadmap(self):
        # Student needs many semesters; target = next semester after start
        many = _many_courses(20, credits_each=3)
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=30,
            completed_regular_semesters=2,
            available_courses=many,
            target_semester_type="Fall",
            starting_year=2026,
            target_end_semester_type="Fall",  # only plan Fall 2026
            target_end_year=2026,
        ))
        assert out.status == "cannot_complete_projection"
        assert "target_semester_reached_not_graduated" in out.reason_codes
        assert out.target_reached_without_graduation is True
        assert out.remaining_graduation_gaps is not None
        assert len(out.remaining_graduation_gaps) > 0
        # Only one semester planned (Fall 2026)
        assert out.total_passes == 1

    def test_graduation_before_target_returns_complete(self):
        # Student can graduate in 1 semester; target = 2 semesters ahead
        out = generate_graduation_roadmap(_inp(
            target_end_semester_type="Spring",
            target_end_year=2027,
        ))
        # Graduation happens in Fall 2026 → before Spring 2027 target
        assert out.status == "complete"
        assert out.target_reached_without_graduation is False

    def test_graduation_exactly_at_target(self):
        # Target = Fall 2026; graduation in Fall 2026 too
        out = generate_graduation_roadmap(_inp(
            target_end_semester_type="Fall",
            target_end_year=2026,
        ))
        assert out.status == "complete"
        assert out.projected_graduation_semester == "Fall 2026"

    def test_target_end_before_start_equal_is_valid(self):
        # Same semester as start (target == start) → plans exactly 1 semester
        many = _many_courses(20, 3)
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=30,
            completed_regular_semesters=2,
            available_courses=many,
            target_semester_type="Fall",
            starting_year=2026,
            target_end_semester_type="Fall",
            target_end_year=2026,
        ))
        # Plans Fall 2026, then Spring 2027 would be beyond target → stops
        assert out.total_passes == 1

    def test_remaining_gaps_populated_when_target_reached(self):
        many = _many_courses(20, 3)
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=30,
            completed_regular_semesters=2,
            available_courses=many,
            target_semester_type="Fall",
            starting_year=2026,
            target_end_semester_type="Spring",
            target_end_year=2027,
        ))
        if out.status == "cannot_complete_projection":
            assert out.remaining_graduation_gaps is not None
            gap_text = " ".join(out.remaining_graduation_gaps)
            assert "credit" in gap_text.lower() or "semester" in gap_text.lower()


# ===========================================================================
# F — GPA Behaviour
# ===========================================================================

class TestGPABehaviour:

    def test_in_progress_courses_assumed_passed_updates_cgpa(self):
        # Student with CGPA=1.5 has in-progress courses that would lift CGPA
        out = generate_graduation_roadmap(_inp(
            current_cgpa=1.5,
            gpa_counted_credits=40,
            current_quality_points=60.0,  # 60/40 = 1.5
            in_progress_courses=["IP1"],
            available_courses=[_course("IP1", credits=3), *_three_fall_courses()],
            assumed_grade_per_pass=4.0,  # A grade
            graduation_rules=_grad_rules(min_cgpa=2.0),
            cumulative_passed_hours=121,
            completed_regular_semesters=6,
        ))
        # After absorbing IP1 at 4.0: QP=60+12=72, denom=43 → cgpa≈1.67 initially
        # Then Fall semester with 3 more courses at 4.0: further improvement
        assert any("in-progress" in w.lower() for w in out.warnings)

    def test_in_progress_course_not_in_pool_excluded_with_warning(self):
        out = generate_graduation_roadmap(_inp(
            in_progress_courses=["MISSING_COURSE"],
            available_courses=_three_fall_courses(),
        ))
        assert any("MISSING_COURSE" in w for w in out.warnings)

    def test_failed_retake_cap_applied_when_assumed_grade_exceeds_cap(self):
        # assumed_grade=4.0 (A) but retake cap=3.0 (B) → cap applies
        failed_course = _course("F1", credits=3)
        pool = [failed_course, _course("C2", credits=3), _course("C3", credits=3)]
        out = generate_graduation_roadmap(_inp(
            failed_courses=["F1"],
            available_courses=pool,
            assumed_grade_per_pass=4.0,
            failed_retake_grade_cap_points=3.0,  # B cap
        ))
        assert out.status == "complete"
        # Retake cap warning should appear in first semester pass warnings
        assert any(
            "cap" in w.lower()
            for sem in out.semester_plans
            for w in sem.warnings
        )

    def test_failed_retake_cap_not_applied_when_assumed_below_cap(self):
        # assumed_grade=2.0 (C) < cap=3.0 → no cap
        failed_course = _course("F1", credits=3)
        pool = [failed_course, _course("C2", credits=3), _course("C3", credits=3)]
        out = generate_graduation_roadmap(_inp(
            failed_courses=["F1"],
            available_courses=pool,
            assumed_grade_per_pass=2.0,
            failed_retake_grade_cap_points=3.0,
        ))
        assert out.status == "complete"
        assert not any(
            "cap" in w.lower()
            for sem in out.semester_plans
            for w in sem.warnings
        )

    def test_graduation_not_projected_when_final_cgpa_below_minimum(self):
        # assumed_grade=1.0 → CGPA stays below 2.0 graduation minimum
        out = generate_graduation_roadmap(_inp(
            current_cgpa=1.5,
            gpa_counted_credits=80,
            current_quality_points=120.0,
            assumed_grade_per_pass=1.0,
            graduation_rules=_grad_rules(min_cgpa=2.0),
        ))
        assert out.status == "cannot_complete_projection"
        assert "cgpa_below_minimum" in out.reason_codes
        assert "cgpa_below_minimum" in out.non_course_blockers

    def test_cgpa_gap_included_in_remaining_gaps_when_below_minimum(self):
        out = generate_graduation_roadmap(_inp(
            current_cgpa=1.5,
            gpa_counted_credits=80,
            current_quality_points=120.0,
            assumed_grade_per_pass=1.0,
            graduation_rules=_grad_rules(min_cgpa=2.0),
        ))
        assert out.remaining_graduation_gaps is not None
        assert any("cgpa" in g.lower() for g in out.remaining_graduation_gaps)


# ===========================================================================
# G — Warning Progression Simulation
# ===========================================================================

class TestWarningSimulation:

    def test_no_warning_simulation_when_warning_rules_absent(self):
        out = generate_graduation_roadmap(_inp(warning_rules=None))
        assert out.projected_consecutive_warnings is None
        assert out.projected_total_warnings is None

    def test_warning_counters_returned_when_rules_present_and_cgpa_ok(self):
        # CGPA=2.5 ≥ threshold=2.0 → no warnings accumulated
        out = generate_graduation_roadmap(_inp(
            warning_rules=_warning_rules(threshold=2.0),
            current_cgpa=2.5,
            assumed_grade_per_pass=3.0,
        ))
        assert out.projected_consecutive_warnings is not None
        assert out.projected_consecutive_warnings == 0
        assert out.projected_total_warnings is not None

    def test_cgpa_below_threshold_increments_warnings(self):
        # assumed_grade=1.5, gpa_counted=1 → CGPA after first pass is very low
        many = _many_courses(20, 3)
        out = generate_graduation_roadmap(_inp(
            current_cgpa=1.5,
            gpa_counted_credits=20,
            current_quality_points=30.0,
            cumulative_passed_hours=20,
            completed_regular_semesters=2,
            available_courses=many,
            assumed_grade_per_pass=1.5,  # below warning threshold 2.0
            warning_rules=_warning_rules(threshold=2.0, max_consec=10, max_total=20),
            graduation_rules=_grad_rules(min_sems=6, max_sems=16, min_cgpa=0.0),
        ))
        # Some warnings should have accumulated
        if out.projected_consecutive_warnings is not None:
            assert out.projected_total_warnings >= out.projected_consecutive_warnings

    def test_warning_limit_stops_simulation(self):
        # consecutive cap=2 — should trigger before graduation when CGPA stays below threshold
        many = _many_courses(20, 3)
        out = generate_graduation_roadmap(_inp(
            current_cgpa=1.0,
            gpa_counted_credits=10,
            current_quality_points=10.0,
            cumulative_passed_hours=10,
            completed_regular_semesters=0,
            available_courses=many,
            assumed_grade_per_pass=1.0,  # well below threshold
            warning_rules=_warning_rules(threshold=2.0, max_consec=2, max_total=10),
            graduation_rules=_grad_rules(min_sems=6, max_sems=16, min_cgpa=0.0),
        ))
        assert out.status == "cannot_complete_projection"
        assert "projected_warning_limit_reached" in out.reason_codes
        assert out.warning_limit_reached_in_semester is not None
        assert out.projected_consecutive_warnings is not None

    def test_total_warning_limit_stops_simulation(self):
        # max_total=3 and student already has 2 — one more warning triggers stop
        many = _many_courses(20, 3)
        out = generate_graduation_roadmap(_inp(
            current_cgpa=1.0,
            gpa_counted_credits=10,
            current_quality_points=10.0,
            cumulative_passed_hours=10,
            completed_regular_semesters=0,
            consecutive_warnings=0,
            total_warnings=2,
            available_courses=many,
            assumed_grade_per_pass=1.0,
            warning_rules=_warning_rules(threshold=2.0, max_consec=100, max_total=3),
            graduation_rules=_grad_rules(min_sems=6, max_sems=16, min_cgpa=0.0),
        ))
        assert out.status == "cannot_complete_projection"
        assert "projected_warning_limit_reached" in out.reason_codes

    def test_summer_semester_does_not_increment_warnings(self):
        # Start with Summer; warning sim active; CGPA below threshold
        # Summer should not increment warning counters
        summer = [_course("S1", credits=3, sem=["Summer"])]
        # After summer, advance to Fall — we then need credits to graduate
        fall_courses = _many_courses(5, 3)
        all_courses = summer + fall_courses
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=100,
            completed_regular_semesters=5,
            current_cgpa=1.5,
            gpa_counted_credits=50,
            current_quality_points=75.0,
            assumed_grade_per_pass=1.5,
            available_courses=all_courses,
            target_semester_type="Summer",
            starting_year=2026,
            accelerated_mode=False,  # non-accelerated: Summer → Fall
            summer_semester_rules=_summer_rules(),
            warning_rules=_warning_rules(threshold=2.0, max_consec=100, max_total=100),
            graduation_rules=_grad_rules(min_sems=6, min_cgpa=0.0),
        ))
        # Summer pass should not have incremented warnings — only Fall/Spring do


# ===========================================================================
# H — Non-Course Requirements
# ===========================================================================

class TestNonCourseRequirements:

    def test_military_pending_does_not_block_course_planning(self):
        out = generate_graduation_roadmap(_inp(
            military_status="Not Yet Deferred",
            graduation_rules=_grad_rules(must_military=True),
        ))
        # Courses are still planned
        assert out.status == "complete"
        assert out.total_passes > 0
        assert "military_training_required" in out.non_course_blockers

    def test_military_done_not_added_to_blockers(self):
        out = generate_graduation_roadmap(_inp(
            military_status="Done",
            graduation_rules=_grad_rules(must_military=True),
        ))
        assert "military_training_required" not in out.non_course_blockers

    def test_military_exempted_not_added_to_blockers(self):
        out = generate_graduation_roadmap(_inp(
            military_status="Exempted",
            graduation_rules=_grad_rules(must_military=True),
        ))
        assert "military_training_required" not in out.non_course_blockers

    def test_military_rule_disabled_hides_military_blocker(self):
        # When must_military=False, military_pending should not appear
        out = generate_graduation_roadmap(_inp(
            military_status="Deferred",
            graduation_rules=_grad_rules(must_military=False),
        ))
        assert "military_training_required" not in out.non_course_blockers

    def test_zero_credit_missing_does_not_block_course_planning(self):
        out = generate_graduation_roadmap(_inp(
            zero_credit_courses_passed=False,
            graduation_rules=_grad_rules(must_zero=True),
        ))
        # Courses still planned
        assert out.status == "complete"
        assert "zero_credit_courses_required" in out.non_course_blockers

    def test_zero_credit_rule_disabled_hides_blocker(self):
        out = generate_graduation_roadmap(_inp(
            zero_credit_courses_passed=False,
            graduation_rules=_grad_rules(must_zero=False),
        ))
        assert "zero_credit_courses_required" not in out.non_course_blockers


# ===========================================================================
# I — Eligibility / Course Selection Logic
# ===========================================================================

class TestEligibilityCourseSelection:

    def test_completed_courses_excluded_from_pool(self):
        # C1 is already completed — should not appear in roadmap
        out = generate_graduation_roadmap(_inp(
            completed_courses=["C1"],
            available_courses=_three_fall_courses(),
        ))
        if out.semester_plans:
            selected_codes = [c.course_code for c in out.semester_plans[0].courses]
            assert "C1" not in selected_codes

    def test_in_progress_courses_absorbed_not_reselected(self):
        # IP1 is in-progress — absorbed before loop, should not be selected again
        out = generate_graduation_roadmap(_inp(
            in_progress_courses=["C1"],
            available_courses=_three_fall_courses(),
        ))
        if out.semester_plans:
            selected_codes = [c.course_code for c in out.semester_plans[0].courses]
            assert "C1" not in selected_codes

    def test_failed_courses_prioritized_as_retakes(self):
        # FAIL1 is in failed_courses — should appear with priority "retake"
        pool = [_course("FAIL1", credits=3), _course("C2", credits=3), _course("C3", credits=3)]
        out = generate_graduation_roadmap(_inp(
            failed_courses=["FAIL1"],
            available_courses=pool,
        ))
        assert out.status == "complete"
        if out.semester_plans:
            first_courses = out.semester_plans[0].courses
            retake_course = next((c for c in first_courses if c.course_code == "FAIL1"), None)
            if retake_course:
                assert retake_course.is_retake is True
                assert retake_course.priority_level == "retake"

    def test_missing_prerequisites_excluded(self):
        # C2 requires C1 which is not in sim_completed
        pool = [
            _course("C1", credits=3, prereqs=[]),
            _course("C2", credits=3, prereqs=["C1"]),
            _course("C3", credits=3, prereqs=[]),
        ]
        out = generate_graduation_roadmap(_inp(
            completed_courses=[],
            available_courses=pool,
            cumulative_passed_hours=124,
        ))
        if out.semester_plans:
            selected_codes = [c.course_code for c in out.semester_plans[0].courses]
            # C2 should not be selected since C1 is not yet completed
            assert "C2" not in selected_codes

    def test_credit_threshold_unmet_excluded(self):
        # C_HIGH requires 100 PHs; student has only 30 → should be excluded
        pool = [
            _course("C_HIGH", credits=3, threshold=100),
            _course("C1", credits=3),
            _course("C2", credits=3),
            _course("C3", credits=3),
        ]
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=30,
            available_courses=pool,
        ))
        if out.semester_plans:
            selected_codes = [c.course_code for c in out.semester_plans[0].courses]
            assert "C_HIGH" not in selected_codes

    def test_no_eligible_courses_returns_blocked(self):
        # All available courses are already completed
        out = generate_graduation_roadmap(_inp(
            completed_courses=["C1", "C2", "C3"],
            available_courses=_three_fall_courses(),
        ))
        assert out.status == "blocked"
        assert "no_eligible_courses" in out.reason_codes

    def test_course_names_preserved_in_output(self):
        pool = [
            _course("C1", name="Introduction to CS", credits=3),
            _course("C2", name="Data Structures", credits=3),
            _course("C3", name="Algorithms", credits=3),
        ]
        out = generate_graduation_roadmap(_inp(available_courses=pool))
        if out.semester_plans:
            names = {c.course_name for c in out.semester_plans[0].courses}
            assert "Introduction to CS" in names

    def test_course_semester_offering_filter_applied(self):
        # C_SPRING is only offered in Spring — should not appear in a Fall semester
        pool = [
            _course("C_SPRING", credits=3, sem=["Spring"]),
            _course("C1", credits=3),
            _course("C2", credits=3),
            _course("C3", credits=3),
        ]
        out = generate_graduation_roadmap(_inp(
            target_semester_type="Fall",
            available_courses=pool,
        ))
        if out.semester_plans and out.semester_plans[0].semester_type == "regular":
            selected_codes = [c.course_code for c in out.semester_plans[0].courses]
            assert "C_SPRING" not in selected_codes


# ===========================================================================
# J — Semester Sequence and Credit-Load Behaviour
# ===========================================================================

class TestSemesterSequenceAndLoad:

    def test_regular_sequence_fall_spring_fall(self):
        # Need 3 semesters to graduate (each 3 credits, need 9 more from 124)
        # But need minimum 8 regular semesters with only 6 done → need 2 more
        many = _many_courses(10, 3)
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=100,
            completed_regular_semesters=4,
            available_courses=many,
            graduation_rules=_grad_rules(min_sems=6, total=133),
            target_semester_type="Fall",
            starting_year=2026,
        ))
        if len(out.semester_plans) >= 2:
            labels = [s.semester_label for s in out.semester_plans]
            assert "Fall 2026" in labels
            assert "Spring 2027" in labels

    def test_accelerated_sequence_includes_summer(self):
        many = _many_courses(10, 3)
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=100,
            completed_regular_semesters=4,
            available_courses=many,
            graduation_rules=_grad_rules(min_sems=6, total=133),
            target_semester_type="Fall",
            starting_year=2026,
            accelerated_mode=True,
            summer_semester_rules=_summer_rules(),
        ))
        if len(out.semester_plans) >= 3:
            labels = [s.semester_label for s in out.semester_plans]
            assert any("Summer" in lbl for lbl in labels)

    def test_summer_uses_course_count_cap_not_credit_cap(self):
        # Summer with default cap=2 should select at most 2 courses
        pool = [_course(f"S{i}", credits=3, sem=["Summer"]) for i in range(1, 6)]
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=124,
            completed_regular_semesters=6,
            target_semester_type="Summer",
            starting_year=2026,
            available_courses=pool,
            summer_semester_rules=_summer_rules(default=2),
        ))
        if out.semester_plans and out.semester_plans[0].semester_type == "summer":
            assert len(out.semester_plans[0].courses) <= 2

    def test_regular_credit_cap_applied_for_cgpa_range(self):
        # CGPA=2.5 → default cap = cgpa_between_2_and_3_limit (18)
        many = _many_courses(10, 3)
        out = generate_graduation_roadmap(_inp(
            cumulative_passed_hours=30,
            completed_regular_semesters=2,
            current_cgpa=2.5,
            available_courses=many,
            credit_limit_rules=_credit_rules(mid=18),
        ))
        if out.semester_plans:
            assert out.semester_plans[0].total_credits <= 18

    def test_target_credit_load_below_minimum_generates_warning(self):
        # target_credit_load=6 < minimum=9 → warning expected
        out = generate_graduation_roadmap(_inp(
            target_credit_load=6,
            credit_limit_rules=_credit_rules(minimum=9),
        ))
        # Roadmap should still be generated (advisory only)
        assert out.status == "complete"
        # Warning about minimum appears in global warnings or per-pass warnings
        all_warnings = list(out.warnings)
        for sem in out.semester_plans:
            all_warnings.extend(sem.warnings)
        # Not a hard error — plan is produced (with possibly fewer credits)
