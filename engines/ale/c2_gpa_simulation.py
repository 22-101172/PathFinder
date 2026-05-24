import os
import json
from typing import Dict, Any

from engines.ale.schemas import GPASimulationInput, GPASimulationResult, SimCourse


class GPASimulator:
    @staticmethod
    def process(input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        C2 — GPA Simulation.

        Required payload keys:
            student_context.cgpa                   (float)
            student_context.total_credit_hours_earned (int)
            simulation_scenario.courses            (list of {course_code, credits, expected_grade_points})
        """
        data = GPASimulationInput(**input_data)

        current_cgpa   = data.student_context.cgpa
        total_earned   = data.student_context.total_credit_hours_earned
        courses        = data.simulation_scenario.courses

        if not courses:
            return GPASimulationResult(
                status="error", decision="simulation_failed",
                reason_codes=["no_courses_provided"],
                warnings=[], missing_requirements=[],
                required_data_missing=["simulation_scenario.courses"],
                next_steps=["Provide at least one course with its credits and expected grade points."],
            ).model_dump()

        # ── Load grading scale from curriculum_2026.json ─────────────────────
        rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules", "curriculum_2026.json")
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                curriculum = json.load(f)
        except Exception:
            curriculum = {}

        grading_scale = curriculum.get("grading_scale", [])
        letter_to_points = {}
        points_to_letter = {}

        fallback_scale = {
            "A+": 4.0, "A": 3.7, "A-": 3.4,
            "B+": 3.2, "B": 3.0, "B-": 2.8,
            "C+": 2.6, "C": 2.4, "C-": 2.2,
            "D+": 2.0, "D": 1.5, "D-": 1.0,
            "F": 0.0
        }

        for g in grading_scale:
            let = g.get("letter")
            pt = g.get("points")
            if let is not None and pt is not None:
                letter_to_points[let.strip().upper()] = float(pt)
                points_to_letter[float(pt)] = let.strip()

        if not letter_to_points:
            for let, pt in fallback_scale.items():
                letter_to_points[let.upper()] = pt
                points_to_letter[pt] = let

        min_gpa = curriculum.get("global_rules", {}).get("minimum_cgpa", 2.0)
        safe_threshold = round(min_gpa + 0.2, 1)

        safe_letter = "B"
        for g in grading_scale:
            pt = g.get("points")
            if pt is not None and abs(float(pt) - 3.0) < 0.01:
                safe_letter = g.get("letter", "B")
                break

        # ── Resolve and validate grades ──────────────────────────────────────
        warnings = []
        resolved_courses = []
        new_points = 0.0
        new_credits = 0

        for course in courses:
            expected_points = None
            expected_letter = None

            if course.expected_grade:
                letter_key = course.expected_grade.strip().upper()
                if letter_key in letter_to_points:
                    expected_points = letter_to_points[letter_key]
                    expected_letter = course.expected_grade.strip()
                else:
                    warnings.append(
                        f"Grade letter '{course.expected_grade}' for '{course.course_code}' is not a standard grade in the grading scale."
                    )
                    expected_points = 0.0
                    expected_letter = course.expected_grade.strip()
            elif course.expected_grade_points is not None:
                expected_points = float(course.expected_grade_points)
                closest_letter = "F"
                closest_diff = 999.0
                for pt, let in points_to_letter.items():
                    diff = abs(pt - expected_points)
                    if diff < closest_diff:
                        closest_diff = diff
                        closest_letter = let
                expected_letter = closest_letter
            else:
                warnings.append(
                    f"Neither expected_grade nor expected_grade_points was provided for '{course.course_code}'. Defaulting to F."
                )
                expected_points = 0.0
                expected_letter = "F"

            new_points  += course.credits * expected_points
            new_credits += course.credits

            resolved_courses.append(SimCourse(
                course_code=course.course_code,
                credits=course.credits,
                expected_grade=expected_letter,
                expected_grade_points=expected_points
            ))

        # ── Weighted CGPA formula ────────────────────────────────────────────
        current_total_points = current_cgpa * total_earned
        total_projected_credits = total_earned + new_credits
        if total_projected_credits == 0:
            projected_gpa = 0.0
        else:
            projected_gpa = (current_total_points + new_points) / total_projected_credits

        projected_gpa = round(projected_gpa, 2)
        gpa_delta     = round(projected_gpa - current_cgpa, 2)

        next_steps = []
        if gpa_delta < 0:
            next_steps.append(
                f"These grades would lower your GPA by {abs(gpa_delta):.2f} points. "
                "Consider aiming for higher grades or taking fewer courses."
            )
        if projected_gpa < min_gpa:
            next_steps.append(
                f"Your projected GPA of {projected_gpa} is below the {min_gpa} minimum. "
                "You may be placed on academic probation. Aim for higher grades."
            )
        elif projected_gpa < safe_threshold:
            next_steps.append(
                f"Your projected GPA of {projected_gpa} is close to the {min_gpa} threshold. "
                f"Try to aim for at least {safe_letter} grades to stay safely above probation risk."
            )

        return GPASimulationResult(
            status="ok", decision="simulation_complete",
            reason_codes=[], missing_requirements=[], warnings=warnings,
            required_data_missing=[], next_steps=next_steps,
            projected_gpa=projected_gpa, gpa_delta=gpa_delta,
            is_simulation=True,
            disclaimer="This is a projected estimate, not an official GPA value.",
            simulation_courses=resolved_courses,
        ).model_dump()
