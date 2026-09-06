"""Single source of truth for QU's locked intent taxonomy."""
from __future__ import annotations

LOCKED_INTENTS: frozenset[str] = frozenset({
    # Domain 1 — Academic Planning
    "plan_semester",
    "generate_graduation_roadmap",
    "run_graduation_audit",
    "check_course_eligibility",
    "simulate_gpa_forward",
    "solve_target_gpa",
    # Domain 2 — Course Info
    "get_course_info",
    "get_course_prerequisites",
    "get_skills_taught",
    "search_courses_by_skill",
    # Domain 3 — Career / Role
    "get_role_profile",
    "get_roles_by_track",
    "compute_skill_gap",
    "compute_alignment_score",
    "recommend_courses_to_close_gap",
    "find_best_matching_roles",
    "estimate_alignment_improvement",
    "get_focus_courses_for_target",
    # Domain 4 — Track Guidance
    "get_track_overview",
    "compare_tracks",
    "recommend_track_for_role",
    "recommend_track_for_skill",
    # Domain 5 — Policy
    "policy_query",
    # Domain 6 — Student Record
    "get_student_record",
    # QU Control
    "clarification_needed",
    "out_of_scope",
})

FORBIDDEN_INTENTS: frozenset[str] = frozenset({
    "get_prerequisites",
    "handbook_query",
    "check_eligibility",
    "simulate_gpa",
    "generate_semester_plan",
    "mixed_course_policy",
    "get_courses_in_track",
    "get_track_courses_for_role",
    "get_roles_by_skill",
    "graduation_audit_with_roadmap",
    "compare_courses",
    "rank_courses",
    "get_course_profile",
    "plan_next_semester",
})

INTENT_DESCRIPTIONS: dict[str, str] = {
    "plan_semester": "Plan courses for next or a specific semester",
    "generate_graduation_roadmap": "Multi-semester roadmap to graduation",
    "run_graduation_audit": "Official graduation status check — 'can I graduate'",
    "check_course_eligibility": "Can student take a specific course? — 'can I take X'",
    "simulate_gpa_forward": "GPA projection with expected grades — 'if I get A in X'",
    "solve_target_gpa": "Grades needed to reach a target CGPA",
    "get_course_info": "Course details: description, credits, level",
    "get_course_prerequisites": "Prerequisites for a course (NOT get_prerequisites)",
    "get_skills_taught": "Skills taught by a course",
    "search_courses_by_skill": "Courses that teach a specific skill",
    "get_role_profile": "Career role description and requirements",
    "get_roles_by_track": "Careers/jobs associated with a track",
    "compute_skill_gap": "Missing skills for a target role (student-aware)",
    "compute_alignment_score": "Student match % with a role (student-aware)",
    "recommend_courses_to_close_gap": "Courses to close skill gap for a role (student-aware)",
    "find_best_matching_roles": "Best career matches for student (student-aware)",
    "estimate_alignment_improvement": "Alignment improvement if taking planned courses",
    "get_focus_courses_for_target": "Core/focus courses for a role or track",
    "get_track_overview": "Track overview: courses, description (NOT get_courses_in_track)",
    "compare_tracks": "Compare two academic tracks",
    "recommend_track_for_role": "Best track for a career role",
    "recommend_track_for_skill": "Best track for a skill",
    "policy_query": "University policy/handbook/regulation question",
    "get_student_record": "Student's academic record snapshot",
    "clarification_needed": "Query is ambiguous or missing required entity",
    "out_of_scope": "Query is outside PathFinder academic/career advising scope",
}
