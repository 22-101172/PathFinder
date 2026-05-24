import os
import json
from typing import Dict, Any, List, Set

from engines.ale.schemas import (
    SemesterPlanInput, SemesterPlanResult, PlanItem, PlanOption
)
from engines.ale.a3_eligibility import EligibilityChecker


class SemesterPlanner:
    _curriculum_cache = None

    @classmethod
    def _load_curriculum(cls) -> Dict[str, Any]:
        if cls._curriculum_cache is None:
            rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", "curriculum_2026.json")
            with open(rules_path, "r", encoding="utf-8") as f:
                cls._curriculum_cache = json.load(f)
        return cls._curriculum_cache

    @classmethod
    def _derive_max_credits(cls, cgpa: float, is_final_semester: bool) -> tuple:
        curriculum = cls._load_curriculum()
        limits     = curriculum.get("global_rules", {}).get("credit_limits", {})
        final_exc  = curriculum.get("global_rules", {}).get("final_semester_max_credits_exception", 21)
        if is_final_semester:
            return final_exc, "final_semester"
        if cgpa >= 3.0: return limits.get("above_3_0", 21), "cgpa_rule"
        elif cgpa >= 2.0: return limits.get("between_2_0_and_3_0", 18), "cgpa_rule"
        elif cgpa >= 1.0: return limits.get("below_2_0", 15), "cgpa_rule"
        else: return limits.get("below_1_0", 12), "cgpa_rule"

    @classmethod
    def _count_unlocks(cls, course_code: str, all_offerings: List) -> int:
        count = 0
        for offering in all_offerings:
            if course_code in offering.prerequisites.direct:
                count += 1
        return count

    @classmethod
    def process(cls, input_data: Dict[str, Any]) -> Dict[str, Any]:
        data        = SemesterPlanInput(**input_data)
        student     = data.student_context
        offerings   = data.available_offerings
        constraints = data.constraints

        # ── [1] Study Status Guard ───────────────────────────────────────────
        raw_study_status = (getattr(student, "study_status", None) or "Studying")
        if raw_study_status not in {"Studying", "Graduated"}:
            return SemesterPlanResult(
                status="ok", decision="no_eligible_courses",
                reason_codes=["invalid_study_status"],
                missing_requirements=[],
                warnings=[
                    f"Your study status is '{raw_study_status}'. "
                    "Semester plans can only be generated for students with "
                    "status 'Studying'. Please contact your academic advisor."
                ],
                required_data_missing=[],
                next_steps=["Contact your academic advisor to clarify your enrollment status."],
                plan_options=[], max_credits_allowed=0, credit_limit_source="",
            ).model_dump()

        # ── [1.5] Academic Dismissal Guard ───────────────────────────────────
        if student.consecutive_warnings >= 4 or student.total_warnings >= 6:
            return SemesterPlanResult(
                status="ok", decision="no_eligible_courses",
                reason_codes=["academic_dismissal"],
                missing_requirements=[],
                warnings=["You have reached the maximum allowed academic warnings and are subject to academic dismissal."],
                required_data_missing=[],
                next_steps=["Please contact the registrar immediately. Semester planning is locked."],
                plan_options=[], max_credits_allowed=0, credit_limit_source="",
            ).model_dump()

        # ── [2] Use pre-computed disjoint course sets ────────────────────────
        passed_set      = (
            set(student.completed_courses)
            if student.completed_courses
            else {c.course_code for c in student.course_history if c.status == "passed"}
        )
        failed_set      = (
            set(student.failed_courses)
            if student.failed_courses
            else {c.course_code for c in student.course_history if c.status == "failed"}
        )
        in_progress_set = (
            set(student.in_progress_courses)
            if student.in_progress_courses
            else {c.course_code for c in student.course_history if c.status == "in_progress"}
        )

        # ── [3] Grade points map ─────────────────────────────────────────────
        grade_map = {
            c.course_code: c.grade_points
            for c in student.course_history
            if c.status == "passed" and c.grade_points is not None
        }

        # ── [4] Curriculum rules ─────────────────────────────────────────────
        if data.curriculum_rules:
            required_courses = set(data.curriculum_rules.required_courses)
            elective_options = None
        else:
            curriculum = cls._load_curriculum()
            tracks     = curriculum.get("tracks", {})
            track_data = tracks.get(student.track_id, {})
            if not track_data and student.track_id == "General":
                track_data = tracks.get("CS", {})
            required_courses = set(track_data.get("required_courses", []))
            elective_options = set(track_data.get("elective_options", []))

        # ── [5] Credit limit ─────────────────────────────────────────────────
        if constraints.max_credits > 0:
            max_credits, credit_limit_src = constraints.max_credits, "provided"
        elif student.max_credit_hours_allowed > 0:
            max_credits, credit_limit_src = student.max_credit_hours_allowed, "student_context"
        else:
            max_credits, credit_limit_src = cls._derive_max_credits(student.cgpa, constraints.is_final_semester)

        # ── [6] Compute in-progress credits ──────────────────────────────────
        total_in_progress_credits = sum(
            c.credit_hours for c in student.course_history if c.status == "in_progress"
        )

        # ── [7] Filter eligible offerings ────────────────────────────────────
        eligible_offerings = []
        for offering in offerings:
            code = offering.course_code
            if code in passed_set or code in in_progress_set:
                continue
            if elective_options is not None:
                if code not in required_courses and code not in elective_options:
                    continue

            student_snapshot = {
                "completed_courses":              list(passed_set),
                "in_progress_courses":            list(in_progress_set),
                "failed_courses":                 list(failed_set),
                "cgpa":                           student.cgpa,
                "total_credit_hours_earned":      student.total_credit_hours_earned,
                "max_credit_hours_allowed":       max_credits,
                "total_credit_hours_in_progress": total_in_progress_credits,
                "course_grade_points":            grade_map,
            }
            target_course = {
                "course_code":   code,
                "credits":       offering.credits,
                "prerequisites": offering.prerequisites.model_dump(),
            }
            eligibility_payload = {
                "student_snapshot": student_snapshot,
                "target_course":    target_course,
                "term_context": {
                    "term":              constraints.term,
                    "is_final_semester": constraints.is_final_semester,
                }
            }

            elig_res = EligibilityChecker.process(eligibility_payload)
            if elig_res.get("decision") == "eligible":
                eligible_offerings.append(offering)

        if not eligible_offerings:
            return SemesterPlanResult(
                status="ok", decision="no_eligible_courses",
                reason_codes=["no_eligible_courses"],
                missing_requirements=[], warnings=["No eligible courses found."],
                required_data_missing=[], next_steps=["Check with your advisor."],
                plan_options=[], max_credits_allowed=max_credits, credit_limit_source=credit_limit_src,
            ).model_dump()

        unlock_scores = {
            o.course_code: cls._count_unlocks(o.course_code, offerings)
            for o in eligible_offerings
        }

        # ── [8] Classify and sort offerings ──────────────────────────────────
        retakes, cores, electives = [], [], []
        for o in eligible_offerings:
            if o.course_code in failed_set and o.course_code in required_courses:
                retakes.append(o)
            elif o.course_code in required_courses or o.is_core:
                cores.append(o)
            else:
                electives.append(o)

        retakes.sort(key=lambda x: -unlock_scores.get(x.course_code, 0))
        cores.sort(key=lambda x: -unlock_scores.get(x.course_code, 0))
        electives.sort(key=lambda x: -unlock_scores.get(x.course_code, 0))

        locked_core    = []
        locked_credits = 0
        for r in retakes:
            if locked_credits + r.credits <= max_credits:
                locked_core.append(r)
                locked_credits += r.credits

        # ── [9] Build plan options ────────────────────────────────────────────
        def build_plan(name, pref_1, pref_2):
            plan_items   = []
            curr_credits = 0
            for c in locked_core:
                plan_items.append(PlanItem(
                    course_code=c.course_code, course_name=c.course_name,
                    credits=c.credits, reason="retake_required",
                    unlocks_count=unlock_scores.get(c.course_code, 0)
                ))
                curr_credits += c.credits
            for c in pref_1:
                if curr_credits + c.credits <= max_credits:
                    reason = "core_required" if c in cores else "elective"
                    plan_items.append(PlanItem(
                        course_code=c.course_code, course_name=c.course_name,
                        credits=c.credits, reason=reason,
                        unlocks_count=unlock_scores.get(c.course_code, 0)
                    ))
                    curr_credits += c.credits
            for c in pref_2:
                if curr_credits + c.credits <= max_credits:
                    reason = "core_required" if c in cores else "elective"
                    plan_items.append(PlanItem(
                        course_code=c.course_code, course_name=c.course_name,
                        credits=c.credits, reason=reason,
                        unlocks_count=unlock_scores.get(c.course_code, 0)
                    ))
                    curr_credits += c.credits
            return PlanOption(plan_name=name, total_credits=curr_credits, courses=plan_items)

        plan_a = build_plan("Plan A: Track Core Focus", cores, electives)
        plan_b = build_plan("Plan B: Balanced Elective Focus", electives, cores)

        mixed_pool = []
        c_idx, e_idx = 0, 0
        while c_idx < len(cores) or e_idx < len(electives):
            if c_idx < len(cores):
                mixed_pool.append(cores[c_idx]); c_idx += 1
            if e_idx < len(electives):
                mixed_pool.append(electives[e_idx]); e_idx += 1
        plan_c = build_plan("Plan C: Mixed Distribution", mixed_pool, [])

        unique_plans = []
        seen_hashes  = set()
        for p in [plan_a, plan_b, plan_c]:
            if not p.courses:
                continue
            p_hash = tuple(sorted([c.course_code for c in p.courses]))
            if p_hash not in seen_hashes:
                seen_hashes.add(p_hash)
                unique_plans.append(p)

        next_steps = []
        if locked_core:
            retake_codes = ", ".join(p.course_code for p in locked_core)
            next_steps.append(f"All plans include locked retake(s): {retake_codes}.")
        next_steps.append(
            f"We generated {len(unique_plans)} personalized plan option(s) for you to choose from."
        )

        decision = "plan_generated" if unique_plans else "no_eligible_courses"

        return SemesterPlanResult(
            status="ok", decision=decision, reason_codes=[],
            missing_requirements=[], warnings=[], required_data_missing=[],
            next_steps=next_steps, plan_options=unique_plans,
            max_credits_allowed=max_credits, credit_limit_source=credit_limit_src,
        ).model_dump()
