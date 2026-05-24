from typing import Dict, Any, List, Set
from engines.ale.schemas import EligibilityInput, EligibilityResult


class EligibilityChecker:
    @staticmethod
    def process(input_data: Dict[str, Any]) -> Dict[str, Any]:
        data = EligibilityInput(**input_data)

        student   = data.student_snapshot
        target    = data.target_course
        term      = data.term_context

        completed    = set(student.completed_courses)
        in_progress  = set(student.in_progress_courses)
        failed       = set(student.failed_courses)
        grade_points = student.course_grade_points

        reason_codes         : List[str] = []
        missing_requirements : List[str] = []
        warnings             : List[str] = []
        next_steps           : List[str] = []
        required_data_missing: List[str] = []

        # ── 1. Already passed? ───────────────────────────────────────────────
        if target.course_code in completed:
            return EligibilityResult(
                status="ok", decision="already_passed", reason_codes=["already_completed"],
                missing_requirements=[], warnings=[], required_data_missing=[],
                next_steps=["You have already passed this course. No further action needed."],
                eligible=True, reasoning=f"{target.course_code} is already in your completed courses.",
            ).model_dump()

        # ── 1.5. Currently In Progress? ──────────────────────────────────────
        if target.course_code in in_progress:
            return EligibilityResult(
                status="ok", decision="not_eligible", reason_codes=["currently_in_progress"],
                missing_requirements=[], warnings=[], required_data_missing=[],
                next_steps=["You are currently taking this course. You cannot register for it again until final grades are released."],
                eligible=False, reasoning=f"{target.course_code} is currently in progress.",
            ).model_dump()

        # ── 2. Retake detection ───────────────────────────────────────────────
        is_retake = target.course_code in failed
        if is_retake:
            warnings.append(f"{target.course_code} was previously failed. This would be a retake.")
            reason_codes.append("retake")
            next_steps.append(f"You are eligible to retake {target.course_code}. Note: your new grade will replace the previous one.")

        # ── 3. Direct Prerequisites Check ────────────────────────────────────
        prereqs        = target.prerequisites
        direct_prereqs = prereqs.direct

        for req in direct_prereqs:
            if isinstance(req, list):
                if any(r in completed for r in req):
                    pass
                else:
                    missing_requirements.append(f"One of {req}")
                    if "missing_prerequisite" not in reason_codes:
                        reason_codes.append("missing_prerequisite")
                    next_steps.append(f"Complete at least one of these courses: {', '.join(req)}.")
            else:
                if req in completed:
                    pass
                else:
                    missing_requirements.append(req)
                    if "missing_prerequisite" not in reason_codes:
                        reason_codes.append("missing_prerequisite")
                    next_steps.append(f"Complete '{req}' before you can register for '{target.course_code}'.")

        # ── 4. Non-course prerequisites ──────────────────────────────────────
        for req in prereqs.non_course:
            if req.type == "grade_minimum":
                course   = req.course
                min_gp   = req.min_grade_points

                if course in completed:
                    actual = grade_points.get(course, None)
                    if actual is None:
                        required_data_missing.append(f"grade_points for {course} (needed for grade minimum check)")
                        warnings.append(f"Cannot verify grade minimum for '{course}' — grade points were not provided.")
                        if "grade_not_recorded" not in reason_codes: reason_codes.append("grade_not_recorded")
                        next_steps.append(f"Provide the grade points for '{course}' to verify the grade minimum requirement.")
                    elif actual < min_gp:
                        missing_requirements.append(course)
                        if "grade_minimum_not_met" not in reason_codes: reason_codes.append("grade_minimum_not_met")
                        next_steps.append(f"Your grade in '{course}' is {actual:.1f} — the minimum required is {min_gp:.1f}. Retake '{course}' to meet this requirement.")
                else:
                    if course not in missing_requirements: missing_requirements.append(course)
                    if "missing_prerequisite" not in reason_codes: reason_codes.append("missing_prerequisite")
                    next_steps.append(f"Complete '{course}' with a minimum of {min_gp:.1f} grade points.")
            elif req.type.upper() == "CREDIT_THRESHOLD":
                min_cr = req.min_credits
                if min_cr is None and req.min_grade_points is not None:
                    min_cr = int(req.min_grade_points)
                if min_cr is not None:
                    actual_credits = student.total_credit_hours_earned
                    if actual_credits < min_cr:
                        if "credit_threshold_not_met" not in reason_codes:
                            reason_codes.append("credit_threshold_not_met")
                        missing_requirements.append(f"credit_threshold_{min_cr}")
                        next_steps.append(f"You have completed {actual_credits} credit hours, but '{target.course_code}' requires at least {min_cr} completed credit hours to enroll.")

        # ── 5. Credit-hour limit check ───────────────────────────────────────
        if student.max_credit_hours_allowed > 0 and target.credits > 0:
            credits_after = student.total_credit_hours_in_progress + target.credits
            if credits_after > student.max_credit_hours_allowed:
                if "credit_limit_exceeded" not in reason_codes: reason_codes.append("credit_limit_exceeded")
                missing_requirements.append("credit_limit")
                next_steps.append(f"Adding {target.credits} credit(s) would bring your total to {credits_after}, exceeding your limit of {student.max_credit_hours_allowed}. Wait until your CGPA improves or remove another course.")

        # ── 6. Final decision ────────────────────────────────────────────────
        blocking_codes = [c for c in reason_codes if c != "retake"]
        eligible       = len(blocking_codes) == 0

        if eligible:
            decision = "eligible"
            if not blocking_codes:
                if "retake" in reason_codes:
                    reasoning = f"All prerequisites satisfied. Note: {target.course_code} is a retake (previously failed)."
                else:
                    reasoning = "All prerequisites satisfied."
                if warnings:
                    reasoning += " " + "; ".join(w for w in warnings if "retake" not in w.lower())
        else:
            decision  = "not_eligible"
            reasoning = " | ".join(f"[{code}]" for code in blocking_codes)

        return EligibilityResult(
            status="ok", decision=decision, reason_codes=reason_codes,
            missing_requirements=missing_requirements, warnings=warnings,
            required_data_missing=required_data_missing, next_steps=next_steps,
            eligible=eligible, reasoning=reasoning,
        ).model_dump()
