"""
QU Acceptance Batch — 30 live queries.
Run: python tests/acceptance_qu.py

Judging:
  PASS       — correct intent + correct major entities/overrides
  ACCEPTABLE — genuinely ambiguous or clarification_needed where that is valid
  FAIL       — wrong intent, forbidden intent, bad override behavior
"""
from __future__ import annotations

import os
import sys
import time
import logging

# Show rate-limit 429 warnings; suppress verbose INFO
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from dotenv import load_dotenv
load_dotenv(override=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gateway.models.schemas import LastReferenced, StructuredQuery
from gateway.query_understanding import understand_query
from gateway.qu_intents import LOCKED_INTENTS, FORBIDDEN_INTENTS

DELAY = float(os.getenv("SMOKE_DELAY_SECONDS", "6"))

# ── Judge helpers ─────────────────────────────────────────────────────────────

def _intents(r): return [sq.intent for sq in r]
def _first(r): return r[0].intent
def _any_sq(r, pred): return any(pred(sq) for sq in r)
def _has_forbidden(r): return _any_sq(r, lambda sq: sq.intent in FORBIDDEN_INTENTS or sq.intent not in LOCKED_INTENTS)


# ── Per-query judge functions ─────────────────────────────────────────────────

def _judge_simple(pass_intents, clarification_ok=False):
    """Factory for standard single-intent queries."""
    def judge(r):
        if _has_forbidden(r):
            bad = [sq.intent for sq in r if sq.intent not in LOCKED_INTENTS or sq.intent in FORBIDDEN_INTENTS]
            return "FAIL", f"bad intent(s): {bad}"
        fi = _first(r)
        if fi in pass_intents:
            return "PASS", f"intent={fi!r}"
        if fi == "clarification_needed" and clarification_ok:
            return "ACCEPTABLE", "clarification_needed (acceptable for this query)"
        return "FAIL", f"got {fi!r}, expected one of {pass_intents}"
    return judge


def _judge_q5(r):
    """'Can I take AI and Data Mining together?' — 2 eligibility SQs or clarification."""
    if _has_forbidden(r): return "FAIL", f"bad intents: {_intents(r)}"
    if all(sq.intent == "check_course_eligibility" for sq in r) and len(r) >= 2:
        return "PASS", f"decomposed into {len(r)} eligibility SQs"
    fi = _first(r)
    if fi == "check_course_eligibility":
        return "ACCEPTABLE", "single eligibility SQ (partial decomposition)"
    if fi == "clarification_needed":
        return "ACCEPTABLE", "clarification acceptable for ambiguous course names"
    return "FAIL", f"got {fi!r}"


def _judge_q7(r):
    """'Can I graduate, and if not give me a roadmap?' — must decompose into 2 SQs."""
    if _has_forbidden(r): return "FAIL", f"bad intents: {_intents(r)}"
    if (len(r) == 2
            and r[0].intent == "run_graduation_audit"
            and r[1].intent == "generate_graduation_roadmap"):
        return "PASS", "correctly decomposed: [run_graduation_audit, generate_graduation_roadmap]"
    if _first(r) == "run_graduation_audit":
        return "ACCEPTABLE", f"audit correct but roadmap missing; got {_intents(r)}"
    return "FAIL", f"decomposition wrong: {_intents(r)}"


def _judge_q10(r):
    """'If I get A in OS…' — simulate_gpa_forward + expected_grades in params, NOT override."""
    if _has_forbidden(r): return "FAIL", f"bad intents: {_intents(r)}"
    if _first(r) != "simulate_gpa_forward":
        return "FAIL", f"expected simulate_gpa_forward, got {_first(r)!r}"
    grade_in_override = _any_sq(r, lambda sq: sq.session_overrides.course_override_type == "gpa_scenario")
    if grade_in_override:
        return "FAIL", "expected_grade stored as gpa_scenario override — must be in params"
    in_params = _any_sq(r, lambda sq: "expected_grades" in sq.params)
    if in_params:
        return "PASS", "simulate_gpa_forward with expected_grades in params"
    return "ACCEPTABLE", "simulate_gpa_forward correct but expected_grades missing from params"


def _judge_q12(r):
    """'Assume I passed Data Structures, can I take Algorithms?' — eligibility + assumed_passed."""
    if _has_forbidden(r): return "FAIL", f"bad intents: {_intents(r)}"
    has_assumed = _any_sq(r, lambda sq: bool(sq.session_overrides.assumed_passed_courses))
    if _first(r) == "check_course_eligibility" and has_assumed:
        return "PASS", "check_course_eligibility with assumed_passed override"
    if _first(r) == "check_course_eligibility":
        return "ACCEPTABLE", "right intent but assumed_passed override not detected"
    return "FAIL", f"got {_first(r)!r}, expected check_course_eligibility"


def _judge_q13(r):
    """'Assume I failed AI, what happens to my plan?' — assumed_failed override + reasonable intent."""
    if _has_forbidden(r): return "FAIL", f"bad intents: {_intents(r)}"
    has_failed = _any_sq(r, lambda sq:
        bool(sq.session_overrides.assumed_failed_courses)
        or sq.session_overrides.course_override_type == "assumed_failed"
    )
    fi = _first(r)
    valid_intents = {"plan_semester", "check_course_eligibility", "run_graduation_audit", "generate_graduation_roadmap"}
    if fi in valid_intents and has_failed:
        return "PASS", f"{fi!r} with assumed_failed override"
    if fi == "clarification_needed":
        return "ACCEPTABLE", "clarification acceptable — 'what happens' is ambiguous"
    if fi in valid_intents and not has_failed:
        return "ACCEPTABLE", f"{fi!r} correct but assumed_failed override not detected"
    return "FAIL", f"unexpected: intent={fi!r}, override_detected={has_failed}"


def _judge_q14(r):
    """'Clear assumptions…' — override_action=clear must be present."""
    if _has_forbidden(r): return "FAIL", f"bad intents: {_intents(r)}"
    has_clear = _any_sq(r, lambda sq: sq.session_overrides.override_action == "clear")
    fi = _first(r)
    if has_clear:
        return "PASS", f"override_action=clear detected (intent={fi!r})"
    if fi == "clarification_needed":
        return "ACCEPTABLE", "clarification returned but override_action=clear not set"
    return "FAIL", f"override_action=clear not detected; intent={fi!r}"


def _judge_q28(r):
    """'What roles need Python?' — clarification_needed; must NOT invent get_roles_by_skill."""
    if _has_forbidden(r): return "FAIL", f"bad intents: {_intents(r)}"
    fi = _first(r)
    if fi == "clarification_needed":
        return "PASS", "correctly returned clarification_needed"
    if fi == "search_courses_by_skill":
        return "FAIL", "search_courses_by_skill returns COURSES not roles — wrong intent"
    if fi in {"recommend_track_for_skill", "get_focus_courses_for_target"}:
        return "ACCEPTABLE", f"{fi!r} is a reasonable fallback"
    return "FAIL", f"got {fi!r}"


# ── Test cases ────────────────────────────────────────────────────────────────

CASES = [
    (1,  "Tell me about C-CS301",
         _judge_simple({"get_course_info"})),
    (2,  "What is Operating Systems?",
         _judge_simple({"get_course_info"})),
    (3,  "What are the prerequisites for OS?",
         _judge_simple({"get_course_prerequisites"})),
    (4,  "Can I take Operating Systems next semester?",
         _judge_simple({"check_course_eligibility"})),
    (5,  "Can I take AI and Data Mining together?",
         _judge_q5),
    (6,  "Can I graduate this year?",
         _judge_simple({"run_graduation_audit"})),
    (7,  "Can I graduate, and if not give me a roadmap?",
         _judge_q7),
    (8,  "Plan my next semester",
         _judge_simple({"plan_semester"})),
    (9,  "What should I take next if I want to graduate fast?",
         _judge_simple({"plan_semester", "generate_graduation_roadmap"}, clarification_ok=True)),
    (10, "If I get A in OS, what will my GPA be?",
         _judge_q10),
    (11, "What grades do I need to reach 3.5 GPA?",
         _judge_simple({"solve_target_gpa"})),
    (12, "Assume I passed Data Structures, can I take Algorithms?",
         _judge_q12),
    (13, "Assume I failed AI, what happens to my plan?",
         _judge_q13),
    (14, "Clear assumptions and use my official record",
         _judge_q14),
    (15, "What is the warning policy?",
         _judge_simple({"policy_query"})),
    (16, "What happens if I fail a course twice?",
         _judge_simple({"policy_query"})),
    (17, "How many absences are allowed?",
         _judge_simple({"policy_query"})),
    (18, "What courses teach Python?",
         _judge_simple({"search_courses_by_skill"})),
    (19, "What skills does Machine Learning teach?",
         _judge_simple({"get_skills_taught"})),
    (20, "What is a data scientist?",
         _judge_simple({"get_role_profile"})),
    (21, "What roles fit me?",
         _judge_simple({"find_best_matching_roles"})),
    (22, "Do I have the skills to become a machine learning engineer?",
         _judge_simple({"compute_skill_gap", "compute_alignment_score"})),
    (23, "What courses should I take for data scientist?",
         _judge_simple({"recommend_courses_to_close_gap", "get_focus_courses_for_target"})),
    (24, "What jobs can I get from AI track?",
         _judge_simple({"get_roles_by_track"})),
    (25, "Compare AI and Data Science tracks",
         _judge_simple({"compare_tracks"})),
    (26, "Which track is better for cybersecurity?",
         _judge_simple({"recommend_track_for_role", "recommend_track_for_skill", "get_track_overview"},
                       clarification_ok=True)),
    (27, "Tell me about Data Science",
         _judge_simple({"get_track_overview", "get_role_profile", "get_course_info"},
                       clarification_ok=True)),
    (28, "What roles need Python?",
         _judge_q28),
    (29, "How do I apply for financial aid?",
         _judge_simple({"out_of_scope"})),
    (30, "What about it?",
         _judge_simple(set(), clarification_ok=True)),
]


# ── Result formatting ─────────────────────────────────────────────────────────

def _fmt_result(q_num, text, results, verdict, reason):
    lines = [f"Q{q_num:02d}: {text!r}"]
    for i, sq in enumerate(results):
        e = sq.entities
        ents = " | ".join(filter(None, [
            f"course={e.course_code!r}" if e.course_code else None,
            f"role={e.role_id!r}" if e.role_id else None,
            f"track={e.track_id!r}" if e.track_id else None,
            f"skill={e.skill_id!r}" if e.skill_id else None,
        ]))
        ovr = sq.session_overrides
        ovr_parts = []
        if ovr.added_courses: ovr_parts.append(f"added={ovr.added_courses}")
        if ovr.assumed_passed_courses: ovr_parts.append(f"passed={ovr.assumed_passed_courses}")
        if ovr.assumed_failed_courses: ovr_parts.append(f"failed={ovr.assumed_failed_courses}")
        if ovr.course_override_type != "none": ovr_parts.append(f"type={ovr.course_override_type!r}")
        if ovr.override_action != "accumulate": ovr_parts.append(f"action={ovr.override_action!r}")

        line = f"     SQ[{i}] intent={sq.intent!r}"
        if ents: line += f"  entities=[{ents}]"
        if sq.params: line += f"  params={sq.params}"
        if ovr_parts: line += f"  overrides=[{', '.join(ovr_parts)}]"
        if sq.student_referential_fallback: line += "  student_ref=True"
        lines.append(line)
    lines.append(f"     --> [{verdict}] {reason}")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    last_ref = LastReferenced()
    rows: list[tuple[int, str, str, str, str]] = []  # (q, text, verdict, reason, intents)
    fail_details: list[str] = []

    print(f"\n=== QU ACCEPTANCE BATCH ({len(CASES)} queries, {DELAY}s delay) ===\n")

    for idx, (q_num, text, judge_fn) in enumerate(CASES):
        if idx > 0:
            time.sleep(DELAY)

        try:
            results = understand_query(text, last_ref, [])
            assert isinstance(results, list) and results, "empty result"
            verdict, reason = judge_fn(results)
        except AssertionError as exc:
            results = []
            verdict, reason = "FAIL", f"empty/invalid result: {exc}"
        except Exception as exc:
            results = []
            verdict, reason = "FAIL", f"exception: {type(exc).__name__}: {exc}"

        intents_str = str([sq.intent for sq in results]) if results else "[]"
        rows.append((q_num, text, verdict, reason, intents_str))
        print(_fmt_result(q_num, text, results, verdict, reason))
        print()

        if verdict == "FAIL":
            fail_details.append(f"  Q{q_num:02d}: {text!r}\n       intents={intents_str}\n       reason={reason}")

    # ── Summary table ────────────────────────────────────────────────────────

    pass_count       = sum(1 for r in rows if r[2] == "PASS")
    acceptable_count = sum(1 for r in rows if r[2] == "ACCEPTABLE")
    fail_count       = sum(1 for r in rows if r[2] == "FAIL")
    total            = len(rows)
    pct              = 100.0 * (pass_count + acceptable_count) / total

    print("=" * 60)
    print("  ACCEPTANCE SUMMARY")
    print("=" * 60)
    print(f"  Total queries   : {total}")
    print(f"  PASS            : {pass_count}")
    print(f"  ACCEPTABLE      : {acceptable_count}")
    print(f"  FAIL            : {fail_count}")
    print(f"  Accepted (P+A)  : {pass_count + acceptable_count} / {total}  ({pct:.1f}%)")
    print(f"  Threshold       : 80%")
    print(f"  Result          : {'MEETS THRESHOLD' if pct >= 80 else 'BELOW THRESHOLD'}")

    if fail_details:
        print("\n  FAILED CASES:")
        for d in fail_details:
            print(d)

    print("=" * 60)
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
