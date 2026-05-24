import os
import json
from typing import Dict, Any, List

from engines.ale.schemas import GraduationAuditInput, GraduationAuditResult, CurriculumRules


class GraduationAudit:
    _curriculum_cache = None

    @classmethod
    def _load_curriculum(cls) -> Dict[str, Any]:
        if cls._curriculum_cache is None:
            rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", "curriculum_2026.json")
            with open(rules_path, "r", encoding="utf-8") as f:
                cls._curriculum_cache = json.load(f)
        return cls._curriculum_cache

    @classmethod
    def _get_credit_limit(cls, cgpa: float, curriculum: Dict) -> int:
        limits = curriculum.get("global_rules", {}).get("credit_limits", {})
        if cgpa >= 3.0: return limits.get("above_3_0", 21)
        elif cgpa >= 2.0: return limits.get("between_2_0_and_3_0", 18)
        elif cgpa >= 1.0: return limits.get("below_2_0", 15)
        else: return limits.get("below_1_0", 12)

    @classmethod
    def process(cls, input_data: Dict[str, Any]) -> Dict[str, Any]:
        data    = GraduationAuditInput(**input_data)
        student = data.student_context

        # ── Load curriculum rules ────────────────────────────────────────────
        if data.curriculum_rules:
            rules            = data.curriculum_rules
            total_required   = rules.total_credits_required
            min_semesters    = rules.minimum_semesters
            required_courses = rules.required_courses
        else:
            curriculum   = cls._load_curriculum()
            tracks       = curriculum.get("tracks", {})
            global_rules = curriculum.get("global_rules", {})

            track_data = tracks.get(student.track_id, {})
            if not track_data and student.track_id == "General":
                track_data = tracks.get("CS", {})

            if not track_data:
                return GraduationAuditResult(
                    status="error", decision="not_eligible",
                    reason_codes=["unknown_track"],
                    missing_requirements=[],
                    warnings=[f"Track '{student.track_id}' not found in curriculum rules."],
                    required_data_missing=["valid track_id"],
                    next_steps=["Verify your track ID with your academic advisor."],
                ).model_dump()

            total_required   = track_data.get("total_credits_required", 133)
            min_semesters    = track_data.get("minimum_semesters", global_rules.get("minimum_semesters", 6))
            required_courses = track_data.get("required_courses", [])

        # ── Analyse course history ───────────────────────────────────────────
        course_history = student.course_history

        passed_courses     = {c.course_code for c in course_history if c.status == "passed"}
        failed_courses_all = {c.course_code for c in course_history if c.status == "failed"}
        in_progress_all    = {c.course_code for c in course_history if c.status == "in_progress"}

        semesters = {c.semester_taken for c in course_history if c.semester_taken}
        semesters_completed = len(semesters)

        credits_earned    = student.total_credit_hours_earned
        credits_remaining = max(0, total_required - credits_earned)

        unmet_required_courses       = [c for c in required_courses if c not in passed_courses]
        failed_required_courses      = [c for c in required_courses if c in failed_courses_all]
        in_progress_required_courses = [c for c in required_courses if c in in_progress_all]

        min_cgpa    = 2.0
        cgpa_ok     = student.cgpa >= min_cgpa
        standing_ok = student.academic_standing.lower() == "good"

        reason_codes: List[str] = []
        next_steps:   List[str] = []
        warnings:     List[str] = []

        # ── [1] Study Status Guard ───────────────────────────────────────────
        study_status_ok  = True
        valid_statuses   = {"Studying", "Graduated"}
        raw_study_status = (getattr(student, "study_status", None) or "Studying")
        if raw_study_status not in valid_statuses:
            study_status_ok = False
            reason_codes.append("invalid_study_status")
            next_steps.append(
                f"Your study status is '{raw_study_status}', which is not eligible for a "
                "graduation audit. Only students with status 'Studying' or 'Graduated' may "
                "apply. Please contact your academic advisor to clarify your enrollment status."
            )

        # ── [2] Academic Dismissal ───────────────────────────────────────────
        warnings_check = True
        consec_warn    = int(getattr(student, "consecutive_warnings", 0) or 0)
        total_warn     = int(getattr(student, "total_warnings", 0) or 0)
        if consec_warn >= 4 or total_warn >= 6:
            warnings_check = False
            reason_codes.append("academic_dismissal")
            next_steps.append(
                f"You have accumulated {consec_warn} consecutive warning(s) and "
                f"{total_warn} total warning(s). "
                "Under university policy, students reaching 4 or more consecutive warnings "
                "or 6 or more total warnings are subject to academic dismissal and are not "
                "eligible to graduate. Please meet urgently with your academic advisor."
            )

        # ── [3] Military Training Check ──────────────────────────────────────
        military_check  = True
        military_status = getattr(student, "military_status", None)
        if military_status is not None:
            deferred_statuses = {"Deferred", "Not Yet Deferred", "Temporary Exemption"}
            passed_hum011     = "HUM011" in passed_courses

            if military_status in ("Done", "Exempted"):
                military_check = True
            elif military_status in deferred_statuses and passed_hum011:
                military_check = True
            else:
                military_check = False
                if military_status in deferred_statuses:
                    next_steps.append(
                        f"Your military training status is '{military_status}'. "
                        "To graduate while your status is deferred, you must have passed "
                        "the Military Training course (HUM011). Please register for and "
                        "pass HUM011, or resolve your deferment before applying for graduation."
                    )
                else:
                    next_steps.append(
                        f"Your military training status is '{military_status}'. "
                        "You must complete or be officially exempted from military training "
                        "before you can graduate. Please consult the military training office."
                    )
                reason_codes.append("military_training_unmet")

        # ── Standard academic checks ─────────────────────────────────────────
        if not cgpa_ok:
            reason_codes.append("cgpa_below_minimum")
            next_steps.append(
                f"Your CGPA is {student.cgpa:.2f} — below the 2.0 minimum. "
                "Focus on improving grades. Consider retaking low-grade required courses."
            )

        if not standing_ok:
            reason_codes.append("academic_standing_issue")
            next_steps.append(
                f"Your academic standing is '{student.academic_standing}'. "
                "Meet with your advisor to create a recovery plan and resolve your standing."
            )

        if credits_remaining > 0:
            reason_codes.append("credits_remaining")
            next_steps.append(
                f"You have {credits_remaining} credit(s) remaining toward the "
                f"{total_required} required. Continue enrolling each semester."
            )

        truly_missing = [
            c for c in unmet_required_courses
            if c not in failed_required_courses and c not in in_progress_required_courses
        ]
        if truly_missing:
            reason_codes.append("missing_required_courses")
            next_steps.append(
                f"The following required courses have not been completed: "
                f"{', '.join(truly_missing)}. Plan to take them as soon as you are eligible."
            )

        if failed_required_courses:
            if "missing_required_courses" not in reason_codes:
                reason_codes.append("missing_required_courses")
            next_steps.append(
                f"Required course(s) previously failed: {', '.join(failed_required_courses)}. "
                "You must retake and pass these to graduate."
            )

        if in_progress_required_courses:
            warnings.append(
                f"Required course(s) currently in progress: "
                f"{', '.join(in_progress_required_courses)}. "
                "These will be verified upon completion."
            )

        if semesters_completed < min_semesters:
            reason_codes.append("minimum_semesters_not_met")
            remaining_sems = min_semesters - semesters_completed
            next_steps.append(
                f"You have completed {semesters_completed} semester(s). "
                f"A minimum of {min_semesters} is required. "
                f"You need at least {remaining_sems} more semester(s)."
            )

        # ── Final decision ────────────────────────────────────────────────────
        graduation_eligible = (
            cgpa_ok and standing_ok and credits_remaining == 0
            and len(unmet_required_courses) == 0 and semesters_completed >= min_semesters
            and warnings_check and military_check and study_status_ok
        )

        decision = "eligible" if graduation_eligible else "not_eligible"

        if graduation_eligible:
            next_steps = [
                "You meet all graduation requirements. "
                "Contact your advisor to initiate the graduation process."
            ]

        return GraduationAuditResult(
            status="ok", decision=decision, reason_codes=reason_codes,
            missing_requirements=unmet_required_courses, warnings=warnings,
            required_data_missing=[], next_steps=next_steps,
            credits_earned=credits_earned, credits_required=total_required,
            credits_remaining=credits_remaining, unmet_required_courses=unmet_required_courses,
            failed_required_courses=failed_required_courses,
            in_progress_required_courses=in_progress_required_courses,
            semesters_completed=semesters_completed, cgpa_check=cgpa_ok,
            standing_check=standing_ok, military_check=military_check,
            warnings_check=warnings_check,
        ).model_dump()
