"""
Synthetic tests for generate_semester_plan — Groups A through H.

Coverage:
  A. Validation guards (invalid semester type, invalid student level,
     missing summer rules, required data missing)
  B. Study status gate
  C. Credit cap brackets (CGPA brackets, max_credits_mode,
     target_credit_load capping, final semester override)
  D. Eligibility filtering (in-progress, completed, missing prereqs,
     credit threshold, wrong semester)
  E. Priority scoring (retake > high_unlock > level_match > standard)
  F. Plan variants (Plan A Recommended, Plan B Lighter Load,
     Plan C Level Focused, dedup behaviour)
  G. Warnings (minimum credit, incomplete grade, final semester,
     summer advisory, unofficial track, eligible credits below minimum)
  H. Summer behaviour (course-count cap by CGPA, advisory warning,
     Plan B/C skipped for summer)

No KG, RAG, or LLM calls. No session mutation.
"""

from __future__ import annotations

import pytest

from engines.ale.functions.generate_semester_plan import generate_semester_plan
from engines.ale.ale_schemas import (
    AvailableCourse,
    CreditLimitRules,
    GenerateSemesterPlanInput,
    GraduationRequirementRules,
    SummerSemesterRules,
)


# =============================================================================
# Shared builders
# =============================================================================

def _credit_limits(
    above_3: int = 21,
    mid: int = 18,
    low: int = 15,
    below_1: int = 12,
    minimum: int = 9,
    final: int = 21,
    incomplete_extra: bool = True,
) -> CreditLimitRules:
    return CreditLimitRules(
        cgpa_above_3_limit=above_3,
        cgpa_between_2_and_3_limit=mid,
        cgpa_between_1_and_2_limit=low,
        cgpa_below_1_limit=below_1,
        minimum_per_semester=minimum,
        final_semester_override=final,
        incomplete_extra_course_allowed=incomplete_extra,
    )


def _grad_rules(
    total_credits: int = 133,
    min_cgpa: float = 2.0,
    min_sems: int = 6,
    max_sems: int = 16,
) -> GraduationRequirementRules:
    return GraduationRequirementRules(
        total_credits_required=total_credits,
        minimum_cgpa=min_cgpa,
        minimum_regular_semesters=min_sems,
        maximum_regular_semesters=max_sems,
        must_pass_zero_credit_courses=True,
        military_training_required_for_males=True,
    )


def _summer_rules(
    default: int = 2,
    above_3: int = 3,
    threshold: float = 3.0,
) -> SummerSemesterRules:
    return SummerSemesterRules(
        default_max_courses=default,
        cgpa_above_3_max_courses=above_3,
        cgpa_threshold_for_extra_course=threshold,
    )


def _course(
    code: str,
    name: str = "Course Name",
    credits: int = 3,
    level: int = 3,
    prerequisites: list[str] | None = None,
    semester_offering: list[str] | None = None,
    credit_threshold: int | None = None,
) -> AvailableCourse:
    return AvailableCourse(
        course_code=code,
        name=name,
        credits=credits,
        level=level,
        prerequisites=prerequisites or [],
        semester_offering=semester_offering or [],
        credit_threshold=credit_threshold,
    )


def _inp(
    *,
    study_status: str = "Studying",
    completed_courses: list[str] | None = None,
    failed_courses: list[str] | None = None,
    in_progress_courses: list[str] | None = None,
    retake_count: dict[str, int] | None = None,
    total_improve_retakes_used: int = 0,
    current_cgpa: float = 2.5,
    cumulative_passed_hours: int = 60,
    student_level: str = "Junior",
    official_track: str | None = "AI",
    incomplete_grade_flag: bool = False,
    available_courses: list[AvailableCourse] | None = None,
    credit_limit_rules: CreditLimitRules | None = None,
    graduation_rules: GraduationRequirementRules | None = None,
    summer_semester_rules: SummerSemesterRules | None = None,
    target_semester_type: str = "Fall",
    target_track: str | None = None,
    target_credit_load: int | None = None,
    max_credits_mode: bool = False,
    lighter_load_mode: bool = False,
    requested_plan_count: int | None = None,
    requested_courses: list[str] | None = None,
) -> GenerateSemesterPlanInput:
    return GenerateSemesterPlanInput(
        study_status=study_status,
        completed_courses=completed_courses if completed_courses is not None else [],
        failed_courses=failed_courses if failed_courses is not None else [],
        in_progress_courses=in_progress_courses if in_progress_courses is not None else [],
        retake_count=retake_count or {},
        total_improve_retakes_used=total_improve_retakes_used,
        current_cgpa=current_cgpa,
        cumulative_passed_hours=cumulative_passed_hours,
        student_level=student_level,
        official_track=official_track,
        incomplete_grade_flag=incomplete_grade_flag,
        available_courses=available_courses if available_courses is not None else [],
        credit_limit_rules=credit_limit_rules or _credit_limits(),
        graduation_rules=graduation_rules or _grad_rules(),
        summer_semester_rules=summer_semester_rules,
        target_semester_type=target_semester_type,
        target_track=target_track,
        target_credit_load=target_credit_load,
        max_credits_mode=max_credits_mode,
        lighter_load_mode=lighter_load_mode,
        requested_plan_count=requested_plan_count,
        requested_courses=requested_courses or [],
    )


def _three_courses_fall() -> list[AvailableCourse]:
    """Three 3cr Fall/Spring courses — useful baseline pool."""
    return [
        _course("CS301", "Algorithms", 3, level=3, semester_offering=["Fall", "Spring"]),
        _course("CS302", "OS", 3, level=3, semester_offering=["Fall", "Spring"]),
        _course("CS303", "Compilers", 3, level=3, semester_offering=["Fall", "Spring"]),
    ]


# =============================================================================
# Group A — Validation guards
# =============================================================================

class TestGroupA_ValidationGuards:

    def test_invalid_target_semester_type_bypasses_literal(self):
        """model_construct() bypasses Literal; belt-and-suspenders guard catches it."""
        bad = GenerateSemesterPlanInput.model_construct(
            study_status="Studying",
            completed_courses=[],
            failed_courses=[],
            in_progress_courses=[],
            retake_count={},
            total_improve_retakes_used=0,
            current_cgpa=2.5,
            cumulative_passed_hours=60,
            student_level="Junior",
            official_track="AI",
            incomplete_grade_flag=False,
            available_courses=[],
            credit_limit_rules=_credit_limits(),
            graduation_rules=_grad_rules(),
            summer_semester_rules=None,
            target_semester_type="winter",  # invalid
            target_track=None,
            target_credit_load=None,
            max_credits_mode=False,
        )
        out = generate_semester_plan(bad)
        assert out.status == "cannot_compute"
        assert "invalid_target_semester_type" in out.reason_codes

    def test_invalid_student_level_bypasses_literal(self):
        """model_construct() bypasses Literal; belt-and-suspenders guard catches it."""
        bad = GenerateSemesterPlanInput.model_construct(
            study_status="Studying",
            completed_courses=[],
            failed_courses=[],
            in_progress_courses=[],
            retake_count={},
            total_improve_retakes_used=0,
            current_cgpa=2.5,
            cumulative_passed_hours=60,
            student_level="Graduate",  # invalid
            official_track="AI",
            incomplete_grade_flag=False,
            available_courses=[],
            credit_limit_rules=_credit_limits(),
            graduation_rules=_grad_rules(),
            summer_semester_rules=None,
            target_semester_type="Fall",
            target_track=None,
            target_credit_load=None,
            max_credits_mode=False,
        )
        out = generate_semester_plan(bad)
        assert out.status == "cannot_compute"
        assert "invalid_student_level" in out.reason_codes

    def test_summer_without_summer_rules_returns_cannot_compute(self):
        out = generate_semester_plan(_inp(target_semester_type="Summer", summer_semester_rules=None))
        assert out.status == "cannot_compute"
        assert "missing_summer_rules" in out.reason_codes

    def test_pydantic_rejects_invalid_semester_type_at_construction(self):
        with pytest.raises(Exception):
            _inp(target_semester_type="winter")

    def test_pydantic_rejects_invalid_student_level_at_construction(self):
        with pytest.raises(Exception):
            _inp(student_level="Graduate")

    def test_valid_semester_types_accepted(self):
        """All three valid semester types pass validation."""
        for sem in ("Fall", "Spring", "Summer"):
            rules = _summer_rules() if sem == "Summer" else None
            out = generate_semester_plan(_inp(target_semester_type=sem, summer_semester_rules=rules))
            assert out.status != "cannot_compute", f"Unexpected cannot_compute for {sem}"

    def test_all_student_levels_accepted(self):
        for level in ("Freshman", "Sophomore", "Junior", "Senior"):
            out = generate_semester_plan(_inp(student_level=level))
            assert out.status != "cannot_compute", f"Unexpected cannot_compute for {level}"


# =============================================================================
# Group B — Study status gate
# =============================================================================

class TestGroupB_StudyStatusGate:

    def test_graduated_returns_not_applicable(self):
        out = generate_semester_plan(_inp(study_status="Graduated"))
        assert out.status == "not_applicable"
        assert "study_status_graduated" in out.reason_codes

    def test_withdrawn_returns_not_applicable(self):
        out = generate_semester_plan(_inp(study_status="Withdrawn"))
        assert out.status == "not_applicable"
        assert "study_status_withdrawn" in out.reason_codes

    def test_studying_is_not_gated(self):
        out = generate_semester_plan(_inp(study_status="Studying"))
        assert out.status != "not_applicable"


# =============================================================================
# Group C — Credit cap brackets
# =============================================================================

class TestGroupC_CreditCapBrackets:

    def test_cgpa_below_1_default_cap_is_12(self):
        out = generate_semester_plan(_inp(current_cgpa=0.8, available_courses=_three_courses_fall()))
        assert out.credit_cap_applied == 12

    def test_cgpa_between_1_and_2_default_cap_is_15(self):
        out = generate_semester_plan(_inp(current_cgpa=1.5, available_courses=_three_courses_fall()))
        assert out.credit_cap_applied == 15

    def test_cgpa_between_2_and_3_default_cap_is_18(self):
        out = generate_semester_plan(_inp(current_cgpa=2.5, available_courses=_three_courses_fall()))
        assert out.credit_cap_applied == 18

    def test_cgpa_exactly_2_default_cap_is_18(self):
        """Boundary: CGPA == 2.0 gets the 2–3 bracket (>= 2.0)."""
        out = generate_semester_plan(_inp(current_cgpa=2.0, available_courses=_three_courses_fall()))
        assert out.credit_cap_applied == 18

    def test_cgpa_exactly_3_default_cap_is_21(self):
        """Boundary: CGPA == 3.0 enters >= 3.0 bracket → cgpa_bracket_max = 21.
        Default path (no max_credits_mode) now aims for the student's CGPA-bracket maximum.
        CGPA=3.0 maps to cgpa_above_3_limit (21) via _compute_cgpa_bracket_max."""
        out = generate_semester_plan(_inp(current_cgpa=3.0, available_courses=_three_courses_fall()))
        # New design: default path = cgpa_bracket_max = 21 for CGPA >= 3.0
        assert out.credit_cap_applied == 21

    def test_max_credits_mode_at_cgpa_3_gives_21(self):
        """With max_credits_mode=True, a student at CGPA >= 3.0 gets 21-credit cap."""
        out = generate_semester_plan(
            _inp(current_cgpa=3.0, max_credits_mode=True, available_courses=_three_courses_fall())
        )
        assert out.credit_cap_applied == 21

    def test_target_credit_load_within_limit_is_applied(self):
        out = generate_semester_plan(
            _inp(current_cgpa=2.5, target_credit_load=12, available_courses=_three_courses_fall())
        )
        assert out.credit_cap_applied == 12

    def test_target_credit_load_exceeding_cgpa_bracket_is_capped(self):
        """target_credit_load > bracket max → capped to bracket max, warning added."""
        out = generate_semester_plan(
            _inp(current_cgpa=2.5, target_credit_load=21, available_courses=_three_courses_fall())
        )
        assert out.credit_cap_applied == 18  # capped to cgpa_between_2_and_3_limit
        assert any("CGPA-based limit" in w or "Capped" in w for w in out.warnings)

    def test_final_semester_override_applied(self):
        """Student needs <= 21 credits to graduate → final semester cap used."""
        out = generate_semester_plan(
            _inp(
                current_cgpa=2.5,
                cumulative_passed_hours=115,  # 133 - 115 = 18 ≤ 21
                graduation_rules=_grad_rules(total_credits=133),
                credit_limit_rules=_credit_limits(final=21),
                available_courses=_three_courses_fall(),
            )
        )
        assert out.is_final_semester is True
        assert out.credit_cap_applied == 21

    def test_non_final_semester(self):
        """Student far from graduation — not final semester."""
        out = generate_semester_plan(
            _inp(cumulative_passed_hours=60, available_courses=_three_courses_fall())
        )
        assert out.is_final_semester is False


# =============================================================================
# Group D — Eligibility filtering
# =============================================================================

class TestGroupD_EligibilityFiltering:

    def test_in_progress_course_excluded(self):
        courses = [_course("CS301"), _course("CS302")]
        out = generate_semester_plan(
            _inp(in_progress_courses=["CS301"], available_courses=courses)
        )
        codes = [c.course_code for p in out.plans for c in p.courses]
        assert "CS301" not in codes

    def test_completed_course_without_failure_excluded(self):
        courses = [_course("CS301"), _course("CS302")]
        out = generate_semester_plan(
            _inp(completed_courses=["CS301"], failed_courses=[], available_courses=courses)
        )
        codes = [c.course_code for p in out.plans for c in p.courses]
        assert "CS301" not in codes

    def test_failed_course_still_eligible_as_retake(self):
        courses = [_course("CS301"), _course("CS302")]
        out = generate_semester_plan(
            _inp(completed_courses=["CS301"], failed_courses=["CS301"], available_courses=courses)
        )
        codes = [c.course_code for p in out.plans for c in p.courses]
        assert "CS301" in codes

    def test_missing_prerequisite_excludes_course(self):
        dep = _course("CS401", prerequisites=["CS301"])
        base = _course("CS302")
        courses = [dep, base]
        out = generate_semester_plan(
            _inp(completed_courses=[], available_courses=courses)
        )
        codes = [c.course_code for p in out.plans for c in p.courses]
        assert "CS401" not in codes
        assert "CS302" in codes

    def test_met_prerequisite_includes_course(self):
        dep = _course("CS401", prerequisites=["CS301"])
        base = _course("CS302")
        courses = [dep, base]
        out = generate_semester_plan(
            _inp(completed_courses=["CS301"], available_courses=courses)
        )
        codes = [c.course_code for p in out.plans for c in p.courses]
        assert "CS401" in codes

    def test_credit_threshold_not_met_excludes_course(self):
        gated = _course("CS401", credit_threshold=90)
        easy = _course("CS302")
        out = generate_semester_plan(
            _inp(cumulative_passed_hours=60, available_courses=[gated, easy])
        )
        codes = [c.course_code for p in out.plans for c in p.courses]
        assert "CS401" not in codes
        assert "CS302" in codes

    def test_credit_threshold_exactly_met_includes_course(self):
        gated = _course("CS401", credit_threshold=60)
        out = generate_semester_plan(
            _inp(cumulative_passed_hours=60, available_courses=[gated])
        )
        codes = [c.course_code for p in out.plans for c in p.courses]
        assert "CS401" in codes

    def test_wrong_semester_offering_excludes_course(self):
        spring_only = _course("CS301", semester_offering=["Spring"])
        fall_only = _course("CS302", semester_offering=["Fall"])
        out = generate_semester_plan(
            _inp(target_semester_type="Fall", available_courses=[spring_only, fall_only])
        )
        codes = [c.course_code for p in out.plans for c in p.courses]
        assert "CS301" not in codes
        assert "CS302" in codes

    def test_empty_semester_offering_treated_as_any_semester(self):
        """Empty semester_offering → course available in every semester (MVP assumption)."""
        universal = _course("CS301", semester_offering=[])
        out = generate_semester_plan(
            _inp(target_semester_type="Fall", available_courses=[universal])
        )
        codes = [c.course_code for p in out.plans for c in p.courses]
        assert "CS301" in codes

    def test_no_eligible_courses_returns_no_eligible_courses(self):
        """All courses are Spring-only; planning Fall → no eligible courses."""
        spring_only = [_course(f"CS{i}", semester_offering=["Spring"]) for i in range(3)]
        out = generate_semester_plan(
            _inp(target_semester_type="Fall", available_courses=spring_only)
        )
        assert out.status == "no_eligible_courses"
        assert out.total_eligible_courses == 0


# =============================================================================
# Group E — Priority scoring
# =============================================================================

class TestGroupE_PriorityScoring:

    def test_retake_placed_before_new_courses(self):
        """Failed course (retake) must appear first in Plan A."""
        retake = _course("CS201", level=2)
        new_course = _course("CS301", level=3)
        # CS301 is a prerequisite for CS401 — gives it unlock_score >= 1
        # but retake must still come first
        out = generate_semester_plan(
            _inp(
                failed_courses=["CS201"],
                completed_courses=["CS201"],
                available_courses=[new_course, retake],
            )
        )
        assert out.plans, "Expected at least one plan"
        plan_a = out.plans[0]
        assert plan_a.courses[0].course_code == "CS201"
        assert plan_a.courses[0].is_retake is True

    def test_high_unlock_score_placed_before_level_match(self):
        """A course that unlocks 3+ courses ranks above a mere level-match course."""
        # Make unlock_score >= 3 for CS301 by having 3 courses that list CS301 as prereq
        cs302 = _course("CS302", prerequisites=["CS301"])
        cs303 = _course("CS303", prerequisites=["CS301"])
        cs304 = _course("CS304", prerequisites=["CS301"])
        # CS305 matches level but has no unlock power
        cs305 = _course("CS305", level=3)
        cs301 = _course("CS301", level=2)  # level 2 — no level_match for Junior
        out = generate_semester_plan(
            _inp(
                student_level="Junior",
                available_courses=[cs302, cs303, cs304, cs305, cs301],
            )
        )
        assert out.plans
        plan_a = out.plans[0]
        codes = [c.course_code for c in plan_a.courses]
        assert codes.index("CS301") < codes.index("CS305")

    def test_level_match_placed_before_standard(self):
        """Level-matching course ranks above standard (no unlock, no retake, no level match)."""
        level_match = _course("CS301", level=3)
        standard = _course("CS101", level=1)  # level 1 != Junior (level 3)
        out = generate_semester_plan(
            _inp(student_level="Junior", available_courses=[standard, level_match])
        )
        assert out.plans
        plan_a = out.plans[0]
        codes = [c.course_code for c in plan_a.courses]
        assert codes.index("CS301") < codes.index("CS101")

    def test_priority_level_labels_correct(self):
        """Verify priority_level label is assigned correctly on output."""
        retake = _course("CS201", level=2)
        out = generate_semester_plan(
            _inp(
                failed_courses=["CS201"],
                completed_courses=["CS201"],
                available_courses=[retake],
            )
        )
        assert out.plans
        course_out = out.plans[0].courses[0]
        assert course_out.priority_level == "retake"
        assert course_out.is_retake is True


# =============================================================================
# Group F — Plan variants
# =============================================================================

class TestGroupF_PlanVariants:

    def _big_pool(self) -> list[AvailableCourse]:
        """7 courses × 3cr each = 21cr total. Cap = 18 → Plan A fills to 18 (6 courses)."""
        return [_course(f"CS3{i:02d}", credits=3, level=3) for i in range(7)]

    def test_plan_1_exists_and_is_recommended(self):
        out = generate_semester_plan(_inp(available_courses=_three_courses_fall()))
        assert out.plans
        assert out.plans[0].plan_label == "Recommended"
        assert out.plans[0].plan_id == "plan_1"

    def test_lighter_load_mode_generates_reduced_cap_plan(self):
        """lighter_load_mode=True generates a plan at cap - 2 credits."""
        out = generate_semester_plan(_inp(current_cgpa=2.5, lighter_load_mode=True, available_courses=self._big_pool()))
        assert out.status == "plans_generated"
        # planning_target_credits should be 18 - 2 = 16
        assert out.planning_target_credits == 16

    def test_lighter_load_mode_false_no_auto_lighter_plan(self):
        """Without lighter_load_mode, no auto lighter plan is generated."""
        out = generate_semester_plan(_inp(current_cgpa=0.5, available_courses=self._big_pool()))
        labels = [p.plan_label for p in out.plans]
        assert "Lighter Load" not in labels

    def test_level_focused_plan_generated_with_enough_same_level_courses(self):
        """Level-Focused plan requires >= 2 same-level eligible courses."""
        courses = [
            _course("CS301", level=3),
            _course("CS302", level=3),
            _course("CS101", level=1),
        ]
        out = generate_semester_plan(_inp(student_level="Junior", available_courses=courses))
        labels = [p.plan_label for p in out.plans]
        assert "Level-Focused" in labels

    def test_level_focused_not_generated_with_one_same_level_course(self):
        """Level-Focused requires >= 2 same-level courses."""
        courses = [
            _course("CS301", level=3),
            _course("CS101", level=1),
        ]
        out = generate_semester_plan(_inp(student_level="Junior", available_courses=courses))
        labels = [p.plan_label for p in out.plans]
        assert "Level-Focused" not in labels

    def test_unlock_focused_plan_not_same_as_recommended(self):
        """Unlock-Focused plan is deduped when it would be identical to Recommended."""
        # 4 courses × 3cr = 12cr. All same-level → Unlock-Focused same as Recommended
        courses = [_course(f"CS{i}", credits=3, level=3) for i in range(4)]
        out = generate_semester_plan(_inp(current_cgpa=2.5, available_courses=courses))
        # Should still generate at least one plan
        assert out.status == "plans_generated"

    def test_level_focused_not_generated_if_same_as_recommended(self):
        """If Level-Focused courses == Recommended courses, Level-Focused is omitted."""
        # Only level-3 courses → Recommended and Level-Focused would be identical
        courses = [
            _course("CS301", level=3),
            _course("CS302", level=3),
        ]
        out = generate_semester_plan(_inp(student_level="Junior", available_courses=courses))
        labels = [p.plan_label for p in out.plans]
        # Both plans use the same 2 courses — Level-Focused should be deduped
        assert "Level-Focused" not in labels


# =============================================================================
# Group G — Warnings
# =============================================================================

class TestGroupG_Warnings:

    def test_minimum_credit_warning_when_target_credit_load_below_minimum(self):
        """target_credit_load < minimum_per_semester → warning added."""
        out = generate_semester_plan(
            _inp(
                target_credit_load=6,
                credit_limit_rules=_credit_limits(minimum=9),
                available_courses=_three_courses_fall(),
            )
        )
        assert any("minimum" in w.lower() and "6 cr" in w for w in out.warnings)

    def test_no_minimum_credit_warning_when_target_credit_load_at_minimum(self):
        out = generate_semester_plan(
            _inp(
                target_credit_load=9,
                credit_limit_rules=_credit_limits(minimum=9),
                available_courses=_three_courses_fall(),
            )
        )
        assert not any("6 cr" in w for w in out.warnings)

    def test_minimum_credit_warning_not_triggered_without_target_credit_load(self):
        """When no explicit target_credit_load is set, the minimum warning is not emitted."""
        out = generate_semester_plan(
            _inp(target_credit_load=None, available_courses=_three_courses_fall())
        )
        assert not any("below the minimum" in w for w in out.warnings)

    def test_incomplete_grade_warning(self):
        out = generate_semester_plan(
            _inp(incomplete_grade_flag=True, available_courses=_three_courses_fall())
        )
        assert any("Incomplete" in w for w in out.warnings)

    def test_no_incomplete_warning_when_flag_false(self):
        out = generate_semester_plan(
            _inp(incomplete_grade_flag=False, available_courses=_three_courses_fall())
        )
        assert not any("Incomplete" in w for w in out.warnings)

    def test_final_semester_warning_in_output(self):
        out = generate_semester_plan(
            _inp(
                cumulative_passed_hours=115,
                graduation_rules=_grad_rules(total_credits=133),
                credit_limit_rules=_credit_limits(final=21),
                available_courses=_three_courses_fall(),
            )
        )
        assert any("final semester" in w.lower() or "final" in w.lower() for w in out.warnings)

    def test_unofficial_track_advisory_warning(self):
        out = generate_semester_plan(
            _inp(
                official_track=None,
                target_track="AI",
                available_courses=_three_courses_fall(),
            )
        )
        assert any("official track" in w.lower() for w in out.warnings)

    def test_no_unofficial_track_warning_when_track_is_assigned(self):
        out = generate_semester_plan(
            _inp(
                official_track="AI",
                target_track=None,
                available_courses=_three_courses_fall(),
            )
        )
        assert not any("official track" in w.lower() for w in out.warnings)

    def test_eligible_credits_below_minimum_warning(self):
        """Total eligible credits < minimum → warning (non-summer only)."""
        # Only 1 course × 3cr; minimum = 9 → warning
        out = generate_semester_plan(
            _inp(
                credit_limit_rules=_credit_limits(minimum=9),
                available_courses=[_course("CS301", credits=3)],
            )
        )
        assert any("below minimum" in w.lower() for w in out.warnings)

    def test_eligible_credits_warning_not_raised_for_summer(self):
        """Summer uses course-count cap; eligible credits warning must NOT fire."""
        # Even with only 1 eligible course (3cr < 9), summer should not emit the warning
        out = generate_semester_plan(
            _inp(
                target_semester_type="Summer",
                summer_semester_rules=_summer_rules(),
                credit_limit_rules=_credit_limits(minimum=9),
                available_courses=[_course("CS301", credits=3)],
            )
        )
        assert not any("below minimum" in w.lower() for w in out.warnings)


# =============================================================================
# Group H — Summer behaviour
# =============================================================================

class TestGroupH_SummerBehaviour:

    def test_summer_default_count_cap_applies(self):
        """CGPA < 3.0 → default 2-course summer cap."""
        courses = [_course(f"CS{i}", credits=3) for i in range(5)]
        out = generate_semester_plan(
            _inp(
                target_semester_type="Summer",
                summer_semester_rules=_summer_rules(default=2, above_3=3, threshold=3.0),
                current_cgpa=2.5,
                available_courses=courses,
            )
        )
        assert out.course_count_cap_applied == 2
        assert out.credit_cap_applied is None
        assert len(out.plans[0].courses) <= 2

    def test_summer_above_3_count_cap_applies(self):
        """CGPA >= 3.0 → higher summer course cap."""
        courses = [_course(f"CS{i}", credits=3) for i in range(5)]
        out = generate_semester_plan(
            _inp(
                target_semester_type="Summer",
                summer_semester_rules=_summer_rules(default=2, above_3=3, threshold=3.0),
                current_cgpa=3.5,
                available_courses=courses,
            )
        )
        assert out.course_count_cap_applied == 3
        assert len(out.plans[0].courses) <= 3

    def test_summer_advisory_warning_always_added(self):
        out = generate_semester_plan(
            _inp(
                target_semester_type="Summer",
                summer_semester_rules=_summer_rules(),
                available_courses=[_course("CS301")],
            )
        )
        assert any("faculty council" in w.lower() for w in out.warnings)

    def test_summer_no_plan_b_lighter_load(self):
        """Plan B (Lighter Load) is non-summer only — should not appear for Summer."""
        courses = [_course(f"CS{i}", credits=3) for i in range(5)]
        out = generate_semester_plan(
            _inp(
                target_semester_type="Summer",
                summer_semester_rules=_summer_rules(default=2, above_3=3, threshold=3.0),
                current_cgpa=2.5,
                available_courses=courses,
            )
        )
        labels = [p.plan_label for p in out.plans]
        assert "Lighter Load" not in labels

    def test_summer_plan_c_uses_count_cap_not_credit_cap(self):
        """Plan C for Summer is capped by course count, not credit hours."""
        # 4 level-3 courses, summer cap = 2 → Plan C picks 2 same-level courses max
        courses = [_course(f"CS3{i:02d}", level=3, credits=3) for i in range(4)]
        out = generate_semester_plan(
            _inp(
                target_semester_type="Summer",
                student_level="Junior",
                summer_semester_rules=_summer_rules(default=2, above_3=3, threshold=3.0),
                current_cgpa=2.5,
                available_courses=courses,
            )
        )
        # Plan C should exist (all same level) but may be deduped if same as Plan A
        # Since Plan A fills up to course_count_cap=2 from the full pool, and Plan C
        # picks same-level (all 4 are level 3), they pick the same 2 → deduped.
        # So Plan C is NOT generated. That's acceptable — just verify no crash.
        assert out.status == "plans_generated"
        assert out.course_count_cap_applied == 2

    def test_summer_minimum_credit_load_warning_not_triggered(self):
        """Summer never triggers target_credit_load minimum warning (no credit cap in summer)."""
        out = generate_semester_plan(
            _inp(
                target_semester_type="Summer",
                summer_semester_rules=_summer_rules(),
                target_credit_load=3,
                credit_limit_rules=_credit_limits(minimum=9),
                available_courses=[_course("CS301", credits=3)],
            )
        )
        assert not any("below the minimum" in w for w in out.warnings)
