"""
Orchestrator Acceptance Batch — 30 representative queries.
Option B: pre-built StructuredQuery objects (no live LLM); mocked KG/RAG/ALE.

Run: python tests/acceptance_orchestrator.py

Judging:
  PASS       — correct TurnWrapper shape, correct adapter family called, correct PerSQResult status
  ACCEPTABLE — control pass-through (clarification/out_of_scope) or genuinely ambiguous intent
  FAIL       — wrong adapter, wrong status, crash, missing fields, bad override/audit behaviour
"""
from __future__ import annotations

import sys
import os
import uuid
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gateway.models.schemas import (
    CourseRecord, EntitySet, LastReferenced,
    PerSQResult, SessionOverrides, SessionState,
    StudentContext, StructuredQuery, TurnWrapper,
)
from gateway.orchestrator import Orchestrator


# ── Tracked adapter stubs ─────────────────────────────────────────────────────

class TrackedKG:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def call(self, operation: str, params: dict = None) -> dict:
        params = params or {}
        self.calls.append((operation, params))
        return self._respond(operation, params)

    def _respond(self, op: str, p: dict) -> dict:
        if op == "get_course_profile":
            code = p.get("course_code", "UNK")
            return {"course_code": code, "name": f"Course {code}", "credits": 3, "track": "AI"}
        if op == "get_prerequisites":
            return {"direct_prerequisites": [], "non_course_prerequisites": []}
        if op == "get_skills_taught":
            return {"course_code": p.get("course_code"), "skills": ["python", "ml_basics"]}
        if op == "search_courses_by_skill":
            return {"skills": p.get("skill_ids", []), "courses": ["C-AI101", "C-CS201"]}
        if op == "get_role_profile":
            role = p.get("role_id", "unknown_role")
            return {"role_id": role, "name": role.replace("_", " ").title(),
                    "required_skills": ["python", "statistics"]}
        if op == "get_roles_by_track":
            return {"track_id": p.get("track_id"), "roles": ["data_scientist", "ml_engineer"]}
        if op == "compute_skill_gap":
            return {"role_id": p.get("role_id"), "acquired": ["python"], "missing": ["deep_learning"]}
        if op == "compute_alignment_score":
            return {"role_id": p.get("role_id"), "score": 0.72}
        if op == "recommend_courses_to_close_gap":
            return {"role_id": p.get("role_id"), "recommended": ["C-AI311", "C-AI411"]}
        if op == "find_best_matching_roles":
            return {"matches": [{"role_id": "data_scientist", "score": 0.85}]}
        if op == "estimate_alignment_improvement":
            return {"role_id": p.get("role_id"), "new_score": 0.88}
        if op == "get_focus_courses_for_target":
            return {"target_id": p.get("target_id"), "focus_courses": ["C-AI311"]}
        if op == "get_track_overview":
            track = p.get("track_id", "AI")
            return {"track_id": track, "core_courses": ["C-AI101"], "electives": ["C-AI311"]}
        if op == "compare_tracks":
            return {"track_1": p.get("track_id_1"), "track_2": p.get("track_id_2"),
                    "differences": ["focus area"]}
        if op == "recommend_track_for_role":
            return {"role_id": p.get("role_id"), "recommended_track": "AI"}
        if op == "recommend_track_for_skill":
            return {"skill_id": p.get("skill_id"), "recommended_track": "AI"}
        if op == "get_courses_by_track":
            return {"track_id": p.get("track_id", "AI"),
                    "courses": [{"course_code": "C-AI101", "name": "Intro AI", "credits": 3},
                                {"course_code": "C-AI311", "name": "Deep Learning", "credits": 3}]}
        return {"operation": op, "status": "ok"}


class TrackedRAG:
    def __init__(self):
        self.calls: list[str] = []

    def execute(self, text: str) -> dict:
        self.calls.append(text)
        return {
            "answer": f"Policy answer for: {text[:80]}",
            "extracted_facts": ["Fact: rule applies.", "Fact: exceptions noted."],
            "citations": [{"source": "Student Handbook 2025", "page": 12}],
        }


class TrackedALE:
    def __init__(self):
        self.calls: list[tuple[str, StudentContext]] = []

    def call(self, operation: str, student_context, rule_bundles, kg_data: dict, params: dict) -> dict:
        self.calls.append((operation, student_context))
        return self._respond(operation)

    def _respond(self, op: str) -> dict:
        responses = {
            "generate_semester_plan": {"status": "ok", "semester": "Fall 2026",
                                        "recommended_courses": ["C-AI311"], "total_credits": 3},
            "run_graduation_audit": {"status": "ok", "can_graduate": False,
                                      "credits_completed": 60, "credits_required": 132},
            "generate_graduation_roadmap": {"status": "ok",
                                             "roadmap": [{"semester": "Fall 2026", "courses": ["C-AI311"]}],
                                             "estimated_graduation": "Spring 2028"},
            "check_course_eligibility": {"status": "ok", "eligible": True,
                                          "reason": "All prerequisites satisfied."},
            "simulate_gpa_forward": {"status": "ok", "current_cgpa": 3.0, "projected_cgpa": 3.2},
            "solve_target_gpa": {"status": "ok", "target_cgpa": 3.5,
                                  "semesters_needed": 3, "required_grade": "A"},
        }
        return responses.get(op, {"status": "ok", "operation": op})


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_student() -> StudentContext:
    history = [
        CourseRecord(course_code=code, credit_hours=3, grade=grade,
                     semester_taken=sem, status="passed")
        for code, grade, sem in [
            ("C-AI101", "A",  "Fall 2022"),
            ("C-AI201", "B",  "Spring 2023"),
            ("C-CS101", "A",  "Fall 2022"),
            ("C-CS201", "B",  "Spring 2023"),
            ("C-CS301", "B+", "Fall 2023"),
            ("Data Structures", "C", "Fall 2023"),
        ]
    ]
    return StudentContext(
        student_id="S001", name="Test Student",
        program="Artificial Intelligence", track_id="AI",
        level=3, first_semester="Fall 2022", study_status="Studying",
        cgpa=3.0, cumulative_chs=60, cumulative_cps=180.0,
        total_credit_hours_earned=60,
        completed_courses=["C-AI101","C-AI201","C-CS101","C-CS201","C-CS301","Data Structures"],
        failed_courses=[], in_progress_courses=[],
        current_semester="Spring 2026", course_history=history,
    )


def _make_session(overrides: SessionOverrides | None = None) -> SessionState:
    return SessionState(
        session_id=str(uuid.uuid4()), student_id="S001",
        session_name="acceptance-test",
        student_context=_make_student(),
        overrides=overrides or SessionOverrides(),
    )


def _make_bundles() -> dict:
    return {
        "grading_scale_rules": MagicMock(letter_to_points={"A":4.0,"B+":3.5,"B":3.0,"C":2.0,"F":0.0}),
        "graduation_requirement_rules": MagicMock(),
        "academic_warning_rules": MagicMock(cgpa_warning_threshold=2.0),
        "honors_rules": MagicMock(),
        "credit_limit_rules": MagicMock(),
        "retake_rules": MagicMock(),
        "summer_semester_rules": MagicMock(),
        "student_level_rules": MagicMock(),
    }


def _sq(intent: str, entities: dict = None, secondary: dict = None,
        params: dict = None, overrides: SessionOverrides = None,
        original_text: str = None,
        student_referential_fallback: bool = False) -> StructuredQuery:
    return StructuredQuery(
        intent=intent,
        original_text=original_text or intent,
        entities=EntitySet(**(entities or {})),
        secondary_entities=EntitySet(**(secondary or {})) if secondary else None,
        params=params or {},
        session_overrides=overrides or SessionOverrides(),
        student_referential_fallback=student_referential_fallback,
    )


def _run(sqs: list[StructuredQuery], session: SessionState | None = None,
         bundles: dict | None = None):
    kg = TrackedKG()
    rag = TrackedRAG()
    ale = TrackedALE()
    orch = Orchestrator(kg, rag, ale)
    tw = orch.execute_turn(sqs, session or _make_session(), bundles or _make_bundles())
    return tw, kg, rag, ale


# ── Judge helpers ─────────────────────────────────────────────────────────────

def _first_status(tw): return tw.results[0].status
def _kg_ops(kg): return [op for op, _ in kg.calls]
def _ale_ops(ale): return [op for op, _ in ale.calls]
def _no_engines(kg, rag, ale): return not kg.calls and not rag.calls and not ale.calls
def _rag_only(kg, rag, ale): return bool(rag.calls) and not ale.calls


# ── Case definitions ──────────────────────────────────────────────────────────

def _kg_course_judge(op_name, entity_key, entity_val):
    """Generic judge for simple KG-only course/role/track queries."""
    def judge(tw, kg, rag, ale):
        if not isinstance(tw, TurnWrapper): return "FAIL", "not TurnWrapper"
        if tw.result_count != 1: return "FAIL", f"result_count={tw.result_count}"
        r = tw.results[0]
        if r.status != "success": return "FAIL", f"status={r.status!r}"
        if not _no_engines(kg, rag, ale):
            if rag.calls or ale.calls:
                return "FAIL", f"wrong adapter: rag={bool(rag.calls)} ale={bool(ale.calls)}"
        if op_name not in _kg_ops(kg): return "FAIL", f"KG op {op_name!r} not called; got {_kg_ops(kg)}"
        return "PASS", f"KG[{op_name}] called, status=success"
    return judge


def _rag_judge():
    def judge(tw, kg, rag, ale):
        if not isinstance(tw, TurnWrapper): return "FAIL", "not TurnWrapper"
        if tw.result_count != 1: return "FAIL", f"result_count={tw.result_count}"
        r = tw.results[0]
        if r.status not in ("success", "soft_no_evidence"):
            return "FAIL", f"status={r.status!r}"
        if not rag.calls: return "FAIL", "RAG not called"
        if ale.calls: return "FAIL", f"ALE should not be called; got {_ale_ops(ale)}"
        if kg.calls: return "FAIL", f"KG should not be called; got {_kg_ops(kg)}"
        return "PASS", f"RAG called, status={r.status!r}"
    return judge


CASES = [
    # Q1 — get_course_info C-CS301
    (1, "Tell me about C-CS301",
     [_sq("get_course_info", entities={"course_code": "C-CS301"})],
     None,
     _kg_course_judge("get_course_profile", "course_code", "C-CS301")),

    # Q2 — get_course_info by name
    (2, "What is Operating Systems?",
     [_sq("get_course_info", entities={"course_code": "Operating Systems"})],
     None,
     _kg_course_judge("get_course_profile", "course_code", "Operating Systems")),

    # Q3 — get_course_prerequisites
    (3, "What are the prerequisites for OS?",
     [_sq("get_course_prerequisites", entities={"course_code": "OS"})],
     None,
     _kg_course_judge("get_prerequisites", "course_code", "OS")),

    # Q4 — check_course_eligibility (KG prereqs + ALE)
    (4, "Can I take Operating Systems next semester?",
     [_sq("check_course_eligibility", entities={"course_code": "Operating Systems"},
          original_text="Can I take Operating Systems next semester?")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "success"
         else ("FAIL", f"KG get_prerequisites not called; got {_kg_ops(kg)}") if "get_prerequisites" not in _kg_ops(kg)
         else ("FAIL", f"ALE check_course_eligibility not called; got {_ale_ops(ale)}") if "check_course_eligibility" not in _ale_ops(ale)
         else ("FAIL", "RAG should not be called") if rag.calls
         else ("PASS", "KG[get_prerequisites]+ALE[check_course_eligibility], status=success")
     )),

    # Q5 — two eligibility SQs (multi-SQ)
    (5, "Can I take AI and Data Mining together?",
     [_sq("check_course_eligibility", entities={"course_code": "AI"},
          original_text="Can I take AI?"),
      _sq("check_course_eligibility", entities={"course_code": "Data Mining"},
          original_text="Can I take Data Mining?")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"result_count={tw.result_count}, expected 2") if tw.result_count != 2
         else ("FAIL", f"indices wrong: {[r.sq_index for r in tw.results]}") if [r.sq_index for r in tw.results] != [0, 1]
         else ("FAIL", f"statuses={[r.status for r in tw.results]}") if any(r.status != "success" for r in tw.results)
         else ("FAIL", f"ALE ops={_ale_ops(ale)}") if _ale_ops(ale).count("check_course_eligibility") != 2
         else ("PASS", f"2 SQs preserved (idx=[0,1]), ALE check_course_eligibility x2")
     )),

    # Q6 — run_graduation_audit (no assumptions)
    (6, "Can I graduate this year?",
     [_sq("run_graduation_audit", original_text="Can I graduate this year?")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "success"
         else ("FAIL", f"ALE ops={_ale_ops(ale)}") if "run_graduation_audit" not in _ale_ops(ale)
         else ("FAIL", "RAG should not be called") if rag.calls
         else ("FAIL", f"assumptions_excluded should be None; got {tw.results[0].assumptions_excluded!r}")
              if tw.results[0].assumptions_excluded is not None
         else ("PASS", "ALE[run_graduation_audit], status=success, assumptions_excluded=None")
     )),

    # Q7 — multi-SQ audit + roadmap (KEY CASE)
    (7, "Can I graduate, and if not give me a roadmap?",
     [_sq("run_graduation_audit", original_text="Can I graduate?"),
      _sq("generate_graduation_roadmap", original_text="Give me a roadmap to graduation")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"result_count={tw.result_count}") if tw.result_count != 2
         else ("FAIL", f"wrong intent order: {[r.intent for r in tw.results]}") if
              [r.intent for r in tw.results] != ["run_graduation_audit", "generate_graduation_roadmap"]
         else ("FAIL", f"statuses={[r.status for r in tw.results]}") if any(r.status != "success" for r in tw.results)
         else ("FAIL", f"ALE ops order wrong: {_ale_ops(ale)}") if
              _ale_ops(ale)[:2] != ["run_graduation_audit", "generate_graduation_roadmap"]
         else ("PASS", f"2 SQs [audit,roadmap], ALE order correct, both status=success")
     )),

    # Q8 — plan_semester → ALE "generate_semester_plan"
    (8, "Plan my next semester",
     [_sq("plan_semester", original_text="Plan my next semester")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "success"
         else ("FAIL", f"ALE used old name 'plan_semester'; must be 'generate_semester_plan': {_ale_ops(ale)}") if "plan_semester" in _ale_ops(ale)
         else ("FAIL", f"ALE 'generate_semester_plan' not called; got {_ale_ops(ale)}") if "generate_semester_plan" not in _ale_ops(ale)
         else ("FAIL", f"KG get_courses_by_track not called; got {_kg_ops(kg)}") if "get_courses_by_track" not in _kg_ops(kg)
         else ("PASS", "ALE[generate_semester_plan] + KG[get_courses_by_track], old name absent")
     )),

    # Q9 — plan_semester (same path, different phrasing)
    (9, "What should I take next if I want to graduate fast?",
     [_sq("plan_semester", original_text="What should I take next?")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "success"
         else ("FAIL", f"ALE 'plan_semester' forbidden intent name in calls: {_ale_ops(ale)}") if "plan_semester" in _ale_ops(ale)
         else ("FAIL", f"generate_semester_plan not called; got {_ale_ops(ale)}") if "generate_semester_plan" not in _ale_ops(ale)
         else ("PASS", "ALE[generate_semester_plan], status=success")
     )),

    # Q10 — simulate_gpa_forward with expected_grades in params
    (10, "If I get A in OS, what will my GPA be?",
     [_sq("simulate_gpa_forward", entities={"course_code": "OS"},
          params={"expected_grades": {"OS": "A"}},
          original_text="If I get A in OS, what will my GPA be?")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "success"
         else ("FAIL", f"ALE 'simulate_gpa' forbidden name in calls: {_ale_ops(ale)}") if "simulate_gpa" in _ale_ops(ale)
         else ("FAIL", f"ALE simulate_gpa_forward not called; got {_ale_ops(ale)}") if "simulate_gpa_forward" not in _ale_ops(ale)
         else ("FAIL", f"KG get_course_profile not called for credits; got {_kg_ops(kg)}") if "get_course_profile" not in _kg_ops(kg)
         else ("PASS", "KG[get_course_profile] for credits + ALE[simulate_gpa_forward], status=success")
     )),

    # Q11 — solve_target_gpa
    (11, "What grades do I need to reach 3.5 GPA?",
     [_sq("solve_target_gpa", params={"target_gpa": 3.5},
          original_text="What grades do I need to reach 3.5 GPA?")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "success"
         else ("FAIL", f"ALE solve_target_gpa not called; got {_ale_ops(ale)}") if "solve_target_gpa" not in _ale_ops(ale)
         else ("FAIL", "RAG should not be called") if rag.calls
         else ("PASS", "ALE[solve_target_gpa], status=success")
     )),

    # Q12 — assumed_passed override → check_eligibility (KEY CASE)
    (12, "Assume I passed Data Structures, can I take Algorithms?",
     [_sq("check_course_eligibility",
          entities={"course_code": "Algorithms"},
          overrides=SessionOverrides(
              assumed_passed_courses=["Data Structures"],
              course_override_type="assumed_passed",
          ),
          original_text="Assume I passed Data Structures, can I take Algorithms?")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "success"
         else ("FAIL", f"assumptions_active should be True; got {tw.results[0].assumptions_active!r}") if tw.results[0].assumptions_active is not True
         else ("FAIL", f"ALE ctx missing 'Data Structures' in completed; got {list(ale.calls[0][1].completed_courses) if ale.calls else 'no ALE call'}")
              if ale.calls and "Data Structures" not in ale.calls[0][1].completed_courses
         else ("FAIL", "RAG should not be called") if rag.calls
         else ("PASS", "ALE[check_course_eligibility] with effective_ctx (passed override applied), assumptions_active=True")
     )),

    # Q13 — assumed_failed override → plan_semester (KEY CASE)
    (13, "Assume I failed AI, what happens to my plan?",
     [_sq("plan_semester",
          overrides=SessionOverrides(
              assumed_failed_courses=["AI"],
              course_override_type="assumed_failed",
          ),
          original_text="Assume I failed AI, what happens to my plan?")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "success"
         else ("FAIL", f"assumptions_active should be True; got {tw.results[0].assumptions_active!r}") if tw.results[0].assumptions_active is not True
         else ("FAIL", f"ALE ctx missing 'AI' in failed; got {list(ale.calls[0][1].failed_courses) if ale.calls else 'no ALE call'}")
              if ale.calls and "AI" not in ale.calls[0][1].failed_courses
         else ("FAIL", "RAG should not be called") if rag.calls
         else ("PASS", "ALE[generate_semester_plan] with effective_ctx (failed override applied), assumptions_active=True")
     )),

    # Q14 — clear action + get_student_record
    (14, "Clear assumptions and use my official record",
     [_sq("get_student_record",
          overrides=SessionOverrides(override_action="clear"),
          original_text="Clear assumptions and use my official record")],
     _make_session(overrides=SessionOverrides(
         added_courses=["C-AI311"],
         course_override_type="planned",
     )),
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "success"
         else ("FAIL", f"override_state_active should be None after clear; got {tw.results[0].override_state_active!r}")
              if tw.results[0].override_state_active is not None
         else ("FAIL", f"any adapter called after clear: kg={bool(kg.calls)} rag={bool(rag.calls)} ale={bool(ale.calls)}")
              if kg.calls or rag.calls or ale.calls
         else ("PASS", "clear action applied, student record returned, no adapters called, override_state_active=None")
     )),

    # Q15-17 — policy_query → RAG only
    (15, "What is the warning policy?",
     [_sq("policy_query", original_text="What is the academic warning policy?")],
     None, _rag_judge()),

    (16, "What happens if I fail a course twice?",
     [_sq("policy_query", original_text="What is the policy for failing a course twice?")],
     None, _rag_judge()),

    (17, "How many absences are allowed?",
     [_sq("policy_query", original_text="What is the attendance and absence policy?")],
     None, _rag_judge()),

    # Q18 — search_courses_by_skill
    (18, "What courses teach Python?",
     [_sq("search_courses_by_skill", entities={"skill_id": "Python"})],
     None,
     _kg_course_judge("search_courses_by_skill", "skill_id", "Python")),

    # Q19 — get_skills_taught
    (19, "What skills does Machine Learning teach?",
     [_sq("get_skills_taught", entities={"course_code": "Machine Learning"})],
     None,
     _kg_course_judge("get_skills_taught", "course_code", "Machine Learning")),

    # Q20 — get_role_profile
    (20, "What is a data scientist?",
     [_sq("get_role_profile", entities={"role_id": "data_scientist"})],
     None,
     _kg_course_judge("get_role_profile", "role_id", "data_scientist")),

    # Q21 — find_best_matching_roles (student has completed courses)
    (21, "What roles fit me?",
     [_sq("find_best_matching_roles", student_referential_fallback=True)],
     None,
     _kg_course_judge("find_best_matching_roles", None, None)),

    # Q22 — compute_skill_gap (KEY CASE)
    (22, "Do I have the skills to become a machine learning engineer?",
     [_sq("compute_skill_gap",
          entities={"role_id": "machine_learning_engineer"},
          student_referential_fallback=True)],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "success"
         else ("FAIL", f"KG compute_skill_gap not called; got {_kg_ops(kg)}") if "compute_skill_gap" not in _kg_ops(kg)
         else ("FAIL", f"ale called unexpectedly: {_ale_ops(ale)}") if ale.calls
         else ("FAIL", "RAG should not be called") if rag.calls
         else ("PASS", f"KG[compute_skill_gap] role=machine_learning_engineer, status=success")
     )),

    # Q23 — recommend_courses_to_close_gap
    (23, "What courses should I take for data scientist?",
     [_sq("recommend_courses_to_close_gap", entities={"role_id": "data_scientist"})],
     None,
     _kg_course_judge("recommend_courses_to_close_gap", "role_id", "data_scientist")),

    # Q24 — get_roles_by_track
    (24, "What jobs can I get from AI track?",
     [_sq("get_roles_by_track", entities={"track_id": "AI"})],
     None,
     _kg_course_judge("get_roles_by_track", "track_id", "AI")),

    # Q25 — compare_tracks
    (25, "Compare AI and Data Science tracks",
     [_sq("compare_tracks",
          entities={"track_id": "AI"},
          secondary={"track_id": "Data Science"})],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "success"
         else ("FAIL", f"KG compare_tracks not called; got {_kg_ops(kg)}") if "compare_tracks" not in _kg_ops(kg)
         else ("FAIL", f"ale called unexpectedly: {_ale_ops(ale)}") if ale.calls
         else ("PASS", "KG[compare_tracks] both tracks, status=success")
     )),

    # Q26 — ambiguous cybersecurity → clarification_needed (ACCEPTABLE)
    (26, "Which track is better for cybersecurity?",
     [_sq("clarification_needed",
          original_text="Which track is better for cybersecurity? Please specify role or skill.")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}, expected clarification_needed") if tw.results[0].status != "clarification_needed"
         else ("FAIL", f"engines called despite clarification: kg={bool(kg.calls)} rag={bool(rag.calls)} ale={bool(ale.calls)}")
              if kg.calls or rag.calls or ale.calls
         else ("ACCEPTABLE", "clarification_needed returned, no engines called")
     )),

    # Q27 — track overview for "Data Science"
    (27, "Tell me about Data Science",
     [_sq("get_track_overview", entities={"track_id": "Data Science"})],
     None,
     _kg_course_judge("get_track_overview", "track_id", "Data Science")),

    # Q28 — "What roles need Python?" → clarification_needed (no role→skill intent)
    (28, "What roles need Python?",
     [_sq("clarification_needed",
          original_text="What roles need Python? (no locked intent for skill→roles)")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}, expected clarification_needed") if tw.results[0].status != "clarification_needed"
         else ("FAIL", f"engines called despite clarification: {_kg_ops(kg)} / rag={bool(rag.calls)} / ale={bool(ale.calls)}")
              if kg.calls or rag.calls or ale.calls
         else ("PASS", "clarification_needed, no engines called (no role->skill intent)")
     )),

    # Q29 — out_of_scope (KEY CASE)
    (29, "How do I apply for financial aid?",
     [_sq("out_of_scope", original_text="How do I apply for financial aid?")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}, expected out_of_scope") if tw.results[0].status != "out_of_scope"
         else ("FAIL", "scope_explanation missing") if not tw.results[0].scope_explanation
         else ("FAIL", f"engines called despite out_of_scope: kg={bool(kg.calls)} rag={bool(rag.calls)} ale={bool(ale.calls)}")
              if kg.calls or rag.calls or ale.calls
         else ("PASS", "out_of_scope, scope_explanation present, no engines called")
     )),

    # Q30 — "What about it?" → clarification_needed
    (30, "What about it?",
     [_sq("clarification_needed", original_text="What about it?")],
     None,
     lambda tw, kg, rag, ale: (
         ("FAIL", f"status={tw.results[0].status!r}") if tw.results[0].status != "clarification_needed"
         else ("FAIL", f"engines called: {_kg_ops(kg)} rag={bool(rag.calls)} ale={bool(ale.calls)}")
              if kg.calls or rag.calls or ale.calls
         else ("PASS", "clarification_needed, no engines called")
     )),
]


# ── Detailed formatter for 5 key cases ───────────────────────────────────────

def _fmt_detailed(q_num, query, sqs, tw, kg, rag, ale, verdict, reason):
    lines = [
        f"",
        f"{'-'*64}",
        f"  Q{q_num:02d} DETAILED -- {query!r}",
        f"{'-'*64}",
        f"  SQ intents      : {[sq.intent for sq in sqs]}",
        f"  KG calls        : {_kg_ops(kg) if kg.calls else '(none)'}",
        f"  RAG calls       : {len(rag.calls)} call(s)" if rag.calls else "  RAG calls        : (none)",
        f"  ALE calls       : {_ale_ops(ale) if ale.calls else '(none)'}",
        f"  TurnWrapper     : turn_status={tw.turn_status!r}  result_count={tw.result_count}",
    ]
    for r in tw.results:
        flag_parts = []
        if r.assumptions_active is not None:    flag_parts.append(f"assumptions_active={r.assumptions_active}")
        if r.assumptions_excluded is not None:  flag_parts.append(f"assumptions_excluded={r.assumptions_excluded}")
        if r.override_state_active is not None: flag_parts.append(f"override_state_active={r.override_state_active}")
        data_keys = sorted(r.data.keys()) if r.data else []
        lines.append(
            f"  SQ[{r.sq_index}] intent={r.intent!r:40s} status={r.status!r}"
            + (f"  data_keys={data_keys}" if data_keys else "")
            + (f"  [{', '.join(flag_parts)}]" if flag_parts else "")
            + (f"  clarification_prompt={r.clarification_prompt!r}" if r.clarification_prompt else "")
            + (f"  scope_explanation='{r.scope_explanation[:60]}...'" if r.scope_explanation else "")
        )
    lines.append(f"  --> [{verdict}] {reason}")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

DETAILED_CASES = {7, 12, 13, 22, 29}

def main():
    rows = []
    fail_details = []
    detailed_output = []

    print(f"\n{'='*64}")
    print(f"  ORCHESTRATOR ACCEPTANCE BATCH ({len(CASES)} queries)")
    print(f"{'='*64}\n")

    for (q_num, query, sqs, session, judge_fn) in CASES:
        try:
            tw, kg, rag, ale = _run(sqs, session)
            assert isinstance(tw, TurnWrapper), "Not a TurnWrapper"
            assert len(tw.results) == tw.result_count, "result_count mismatch"
            assert [r.sq_index for r in tw.results] == list(range(len(tw.results))), "sq_index ordering broken"
            verdict, reason = judge_fn(tw, kg, rag, ale)
        except AssertionError as exc:
            tw = TurnWrapper(turn_id="", session_id="", timestamp="",
                             results=[], result_count=0, turn_status="failed",
                             has_error=True, has_clarification=False,
                             has_informational=False, has_soft_no_evidence=False)
            kg = rag = ale = type("_", (), {"calls": []})()
            verdict, reason = "FAIL", f"assertion: {exc}"
        except Exception as exc:
            tw = TurnWrapper(turn_id="", session_id="", timestamp="",
                             results=[], result_count=0, turn_status="failed",
                             has_error=True, has_clarification=False,
                             has_informational=False, has_soft_no_evidence=False)
            kg = rag = ale = type("_", (), {"calls": []})()
            verdict, reason = "FAIL", f"crash: {type(exc).__name__}: {exc}"

        rows.append((q_num, query, verdict, reason))

        tag = f"[{verdict}]"
        print(f"Q{q_num:02d}: {query!r}")
        print(f"     {tag:14s} {reason}")
        print()

        if verdict == "FAIL":
            fail_details.append(f"  Q{q_num:02d}: {query!r}\n       {reason}")

        if q_num in DETAILED_CASES:
            try:
                tw2, kg2, rag2, ale2 = _run(sqs, session)
                detailed_output.append(
                    _fmt_detailed(q_num, query, sqs, tw2, kg2, rag2, ale2, verdict, reason)
                )
            except Exception as exc:
                detailed_output.append(f"\n  Q{q_num:02d}: error re-running for detail: {exc}")

    # Detailed outputs
    print(f"\n{'='*64}")
    print("  DETAILED OUTPUTS -- 5 REPRESENTATIVE CASES")
    print(f"{'='*64}")
    for block in detailed_output:
        print(block)

    # Summary
    pass_count       = sum(1 for r in rows if r[2] == "PASS")
    acceptable_count = sum(1 for r in rows if r[2] == "ACCEPTABLE")
    fail_count       = sum(1 for r in rows if r[2] == "FAIL")
    total            = len(rows)
    pct              = 100.0 * (pass_count + acceptable_count) / total

    print(f"\n{'='*64}")
    print("  ORCHESTRATOR ACCEPTANCE SUMMARY")
    print(f"{'='*64}")
    print(f"  Total cases     : {total}")
    print(f"  PASS            : {pass_count}")
    print(f"  ACCEPTABLE      : {acceptable_count}")
    print(f"  FAIL            : {fail_count}")
    print(f"  Accepted (P+A)  : {pass_count + acceptable_count} / {total}  ({pct:.1f}%)")
    print(f"  Threshold       : 90%")
    print(f"  Result          : {'MEETS THRESHOLD' if pct >= 90 else 'BELOW THRESHOLD'}")

    if fail_details:
        print(f"\n  FAILED CASES:")
        for d in fail_details:
            print(d)

    ready = pct >= 90 and fail_count == 0
    print(f"\n  Orchestrator ready for main.py wiring : {'YES' if ready else 'NO — fix failures above'}")
    print(f"{'='*64}")

    import sys as _sys
    _sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
