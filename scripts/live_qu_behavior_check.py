#!/usr/bin/env python3
"""
Live QU behavior smoke test (Layer B - Step 6D-1).

Calls understand_query() directly with a fake resolver.
Does NOT call /chat, Orchestrator, Composer, ALE, RAG, or KG business ops.

Usage:
  python scripts/live_qu_behavior_check.py
  QU_TIMEOUT_SECONDS=15 LIVE_QU_MAX_CASES=10 LIVE_QU_DELAY_SECONDS=5 python scripts/live_qu_behavior_check.py
  python scripts/live_qu_behavior_check.py --only-failed-from reports/qu_live_behavior_results.json

Environment overrides:
  LIVE_QU_MAX_CASES        max cases to run (default: 10)
  LIVE_QU_DELAY_SECONDS    delay between calls in seconds (default: 5)
  QU_TIMEOUT_SECONDS       per-model timeout override (default: .env value or 30)

Output:
  reports/qu_live_behavior_results.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from gateway.models.schemas import LastReferenced
from gateway.query_understanding import understand_query

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ── Fake resolver (no Neo4j needed) ──────────────────────────────────────────

_RESOLVE_DB: dict[str, dict[str, dict]] = {
    "course": {
        "operating systems": {"status": "ok", "resolved_id": "C-CS316"},
        "os": {"status": "ok", "resolved_id": "C-CS316"},
        "advanced programming": {"status": "ok", "resolved_id": "C-CS219"},
        "oop": {"status": "ok", "resolved_id": "C-CS112"},
        "data structures": {"status": "ok", "resolved_id": "C-CS221"},
        "machine learning": {"status": "ok", "resolved_id": "C-AI421"},
        "databases": {"status": "ok", "resolved_id": "C-CS231"},
        "intro ai": {"status": "ok", "resolved_id": "C-AI311"},
    },
    "role": {
        "data scientist": {"status": "ok", "resolved_id": "RL_Data_Scientist"},
        "ml engineer": {"status": "ok", "resolved_id": "RL_ML_Engineer"},
        "machine learning engineer": {"status": "ok", "resolved_id": "RL_ML_Engineer"},
    },
    "track": {
        "ai": {"status": "ok", "resolved_id": "AI"},
        "cyber": {"status": "ok", "resolved_id": "CYS"},
        "cybersecurity": {"status": "ok", "resolved_id": "CYS"},
        "data science": {"status": "ok", "resolved_id": "DSE"},
        "software engineering": {"status": "ok", "resolved_id": "SWE"},
    },
    "skill": {
        "databases": {"status": "ok", "resolved_id": "SK_Database_Design"},
        "python": {"status": "ok", "resolved_id": "SK_Python"},
        "database": {"status": "ok", "resolved_id": "SK_Database_Design"},
    },
}


def fake_resolver(entity_type: str, entity_text: str) -> dict:
    lower = entity_text.lower().strip()
    entry = _RESOLVE_DB.get(entity_type, {}).get(lower)
    if entry is None:
        # LLM outputs roles/skills in underscore format per prompt instructions (e.g. "data_scientist").
        # Normalize to space format for lookup ("data_scientist" → "data scientist").
        normalized = lower.replace("_", " ")
        entry = _RESOLVE_DB.get(entity_type, {}).get(normalized)
    return entry if entry is not None else {"status": "not_found"}


# ── Rate-limit detector via log handler ───────────────────────────────────────

class _RateLimitDetector(logging.Handler):
    """Watches QU log output for 429 / rate-limit signals."""

    def __init__(self) -> None:
        super().__init__()
        self.detected = False
        self.last_message = ""

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        if "429" in msg or "rate" in msg.lower() or "rate_limit" in msg.lower():
            self.detected = True
            self.last_message = msg

    def reset(self) -> None:
        self.detected = False
        self.last_message = ""


_rl_detector = _RateLimitDetector()
logging.getLogger("gateway").addHandler(_rl_detector)
logging.getLogger("gateway").setLevel(logging.WARNING)


# ── Live test case table ──────────────────────────────────────────────────────

LIVE_CASES: list[dict[str, Any]] = [
    {
        "id": "LQ01",
        "category": "Academic Planning",
        "user_text": "what should i register next semester",
        "expected_intents": ["plan_semester"],
        "expected_params": {},
        "notes": "Basic plan_semester; student-referential",
    },
    {
        "id": "LQ02",
        "category": "Course Info",
        "user_text": "full prereqs for advanced programming",
        "expected_intents": ["get_course_prerequisites"],
        "expected_params": {"depth": "full"},
        "notes": "depth=full keyword test",
    },
    {
        "id": "LQ03",
        "category": "Academic Planning (compound)",
        "user_text": "can i graduate and if not give me a roadmap",
        "expected_intents": ["run_graduation_audit", "generate_graduation_roadmap"],
        "expected_params": {},
        "notes": "Multi-SQ decomposition",
    },
    {
        "id": "LQ04",
        "category": "Course Info",
        "user_text": "what courses teach databases",
        "expected_intents": ["search_courses_by_skill"],
        "expected_params": {},
        "notes": "search_courses_by_skill; skill entity not course",
    },
    {
        "id": "LQ05",
        "category": "Career / Role",
        "user_text": "i wanna be data scientist what am i missing",
        "expected_intents": ["compute_skill_gap"],
        "expected_params": {},
        "notes": "compute_skill_gap; student-referential",
    },
    {
        "id": "LQ06",
        "category": "Career / Role",
        "user_text": "important courses for data scientist",
        "expected_intents": ["get_focus_courses_for_target"],
        "expected_params": {"student_referential_fallback": False},
        "notes": "General focus; student_referential_fallback=false",
    },
    {
        "id": "LQ07",
        "category": "Career / Role",
        "user_text": "what focus courses should i still take for data scientist",
        "expected_intents": ["get_focus_courses_for_target"],
        "expected_params": {"student_referential_fallback": True},
        "notes": "Personal trigger 'still'; student_referential_fallback=true",
    },
    {
        "id": "LQ08",
        "category": "Track",
        "user_text": "compare ai and data science",
        "expected_intents": ["compare_tracks"],
        "expected_params": {},
        "notes": "Two-track comparison with secondary_entities",
    },
    {
        "id": "LQ09",
        "category": "Control",
        "user_text": "what roles need python",
        "expected_intents": ["clarification_needed"],
        "expected_params": {},
        "notes": "No locked intent maps skill->roles; must clarify",
    },
    {
        "id": "LQ10",
        "category": "Control",
        "user_text": "how much is tuition",
        "expected_intents": ["out_of_scope"],
        "expected_params": {},
        "notes": "Financial matter - out of scope",
    },
]

# Extended 30-case table (not run by default; activate with LIVE_QU_MAX_CASES=30)
LIVE_CASES_EXTENDED: list[dict[str, Any]] = LIVE_CASES + [
    {
        "id": "LQ11",
        "category": "Academic Planning",
        "user_text": "what grades do i need to reach 3.5",
        "expected_intents": ["solve_target_gpa"],
        "expected_params": {"target_gpa": 3.5},
        "notes": "solve_target_gpa with target_gpa param",
    },
    {
        "id": "LQ12",
        "category": "Academic Planning",
        "user_text": "if i get A in operating systems what will my gpa be",
        "expected_intents": ["simulate_gpa_forward"],
        "expected_params": {},
        "notes": "simulate_gpa_forward; expected_grades in params",
    },
    {
        "id": "LQ13",
        "category": "Academic Planning",
        "user_text": "can i take advanced programming",
        "expected_intents": ["check_course_eligibility"],
        "expected_params": {},
        "notes": "check_course_eligibility; course name resolved",
    },
    {
        "id": "LQ14",
        "category": "Academic Planning",
        "user_text": "help me plan Fall 2026",
        "expected_intents": ["plan_semester"],
        "expected_params": {"semester_resolution_source": "explicit"},
        "notes": "plan_semester with explicit semester param",
    },
    {
        "id": "LQ15",
        "category": "Academic Planning",
        "user_text": "plan next semester",
        "expected_intents": ["plan_semester"],
        "expected_params": {"semester_resolution_source": "relative"},
        "notes": "Relative semester -> target_semester_text",
    },
    {
        "id": "LQ16",
        "category": "Course Info",
        "user_text": "tell me about operating systems",
        "expected_intents": ["get_course_info"],
        "expected_params": {},
        "notes": "get_course_info by name",
    },
    {
        "id": "LQ17",
        "category": "Course Info",
        "user_text": "prereqs for os",
        "expected_intents": ["get_course_prerequisites"],
        "expected_params": {"depth": "direct"},
        "notes": "depth=direct (no 'full' keyword)",
    },
    {
        "id": "LQ18",
        "category": "Course Info",
        "user_text": "what skills does machine learning teach",
        "expected_intents": ["get_skills_taught"],
        "expected_params": {},
        "notes": "get_skills_taught by course name",
    },
    {
        "id": "LQ19",
        "category": "Career / Role",
        "user_text": "what does a data scientist do",
        "expected_intents": ["get_role_profile"],
        "expected_params": {},
        "notes": "get_role_profile by role name",
    },
    {
        "id": "LQ20",
        "category": "Career / Role",
        "user_text": "what careers are related to ai track",
        "expected_intents": ["get_roles_by_track"],
        "expected_params": {},
        "notes": "get_roles_by_track; track=AI",
    },
    {
        "id": "LQ21",
        "category": "Career / Role",
        "user_text": "how well do i match data scientist",
        "expected_intents": ["compute_alignment_score"],
        "expected_params": {},
        "notes": "compute_alignment_score; student-referential",
    },
    {
        "id": "LQ22",
        "category": "Career / Role",
        "user_text": "what courses should i take to close my data scientist gap",
        "expected_intents": ["recommend_courses_to_close_gap"],
        "expected_params": {},
        "notes": "recommend_courses_to_close_gap; student-referential",
    },
    {
        "id": "LQ23",
        "category": "Career / Role",
        "user_text": "what careers fit me best",
        "expected_intents": ["find_best_matching_roles"],
        "expected_params": {},
        "notes": "find_best_matching_roles; student-referential",
    },
    {
        "id": "LQ24",
        "category": "Track",
        "user_text": "tell me about ai track",
        "expected_intents": ["get_track_overview"],
        "expected_params": {},
        "notes": "get_track_overview; track=AI",
    },
    {
        "id": "LQ25",
        "category": "Track",
        "user_text": "which track is best for data scientist",
        "expected_intents": ["recommend_track_for_role"],
        "expected_params": {},
        "notes": "recommend_track_for_role",
    },
    {
        "id": "LQ26",
        "category": "Track",
        "user_text": "which track is best for cybersecurity skills",
        "expected_intents": ["recommend_track_for_skill"],
        "expected_params": {},
        "notes": "recommend_track_for_skill",
    },
    {
        "id": "LQ27",
        "category": "Policy",
        "user_text": "what is the withdrawal policy",
        "expected_intents": ["policy_query"],
        "expected_params": {},
        "notes": "policy_query; rewritten original_text",
    },
    {
        "id": "LQ28",
        "category": "Policy",
        "user_text": "what happens if my cgpa drops below 2",
        "expected_intents": ["policy_query"],
        "expected_params": {},
        "notes": "policy_query; CGPA warning topic",
    },
    {
        "id": "LQ29",
        "category": "Student Record",
        "user_text": "show my progress",
        "expected_intents": ["get_student_record"],
        "expected_params": {},
        "notes": "get_student_record; student-referential",
    },
    {
        "id": "LQ30",
        "category": "Session Override",
        "user_text": "reset assumptions",
        "expected_intents": ["get_student_record"],
        "expected_params": {"override_action": "clear"},
        "notes": "override_action=clear; no confirmation needed",
    },
]


# ── Case evaluation ───────────────────────────────────────────────────────────

def _evaluate_case(
    case: dict[str, Any],
    sqs: list,
    elapsed: float,
    rate_limited: bool,
    error: str | None,
) -> dict[str, Any]:
    predicted_intents = [sq.intent for sq in sqs]
    expected_intents = case["expected_intents"]
    expected_params = case.get("expected_params", {})

    # Check intent match
    intent_ok = predicted_intents == expected_intents

    # Check expected params (subset check)
    params_ok = True
    params_notes = []
    if expected_params and sqs:
        sq0 = sqs[0]
        for key, expected_val in expected_params.items():
            if key == "student_referential_fallback":
                actual = sq0.student_referential_fallback
            elif key == "override_action":
                actual = sq0.session_overrides.override_action
            elif key == "depth":
                actual = sq0.params.get("depth")
            else:
                actual = sq0.params.get(key)
            if actual != expected_val:
                params_ok = False
                params_notes.append(f"{key}: expected={expected_val!r} actual={actual!r}")

    passed = intent_ok and params_ok and not error

    status = "PASS" if passed else ("RATE_LIMIT?" if rate_limited else "FAIL")
    if error:
        status = "ERROR"

    return {
        "id": case["id"],
        "category": case["category"],
        "user_text": case["user_text"],
        "status": status,
        "expected_intents": expected_intents,
        "predicted_intents": predicted_intents,
        "intent_match": intent_ok,
        "params_match": params_ok,
        "params_notes": params_notes,
        "elapsed_s": round(elapsed, 2),
        "rate_limited": rate_limited,
        "error": error,
        "notes": case.get("notes", ""),
    }


def _print_result(r: dict[str, Any]) -> None:
    icon = "PASS" if r["status"] == "PASS" else ("WARN" if r["status"] == "RATE_LIMIT?" else "FAIL")
    print(f"  [{icon}] {r['id']} | {r['category']}")
    print(f"    Q: {r['user_text']!r}")
    print(f"    Expected: {r['expected_intents']}")
    print(f"    Got:      {r['predicted_intents']}  ({r['elapsed_s']}s)")
    if r["params_notes"]:
        print(f"    Param mismatch: {r['params_notes']}")
    if r["error"]:
        print(f"    Error: {r['error']}")
    if r["rate_limited"]:
        print(f"    WARNING: rate-limit signal detected")
    print()


# ── Main runner ───────────────────────────────────────────────────────────────

def run(cases: list[dict[str, Any]], delay: float) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    last_ref = LastReferenced()

    for i, case in enumerate(cases):
        print(f"[{i+1}/{len(cases)}] {case['id']} - {case['user_text']!r}")
        _rl_detector.reset()
        error: str | None = None
        sqs: list = []
        elapsed = 0.0

        t0 = time.monotonic()
        try:
            sqs = understand_query(
                user_text=case["user_text"],
                last_referenced=last_ref,
                recent_turns=[],
                resolver=fake_resolver,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            elapsed = time.monotonic() - t0

        rate_limited = _rl_detector.detected

        result = _evaluate_case(case, sqs, elapsed, rate_limited, error)
        results.append(result)
        _print_result(result)

        if rate_limited:
            extra_delay = max(delay, 30.0)
            print(f"  Rate-limit signal - extra delay {extra_delay}s before next call.\n")
            time.sleep(extra_delay)
        elif i < len(cases) - 1:
            time.sleep(delay)

    return results


def _save_report(results: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] not in ("PASS",))
    report = {
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": f"{passed/len(results)*100:.0f}%" if results else "0%",
        },
        "results": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Report saved -> {path}")


def _load_failed_ids(path: Path) -> list[str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [
        r["id"]
        for r in data.get("results", [])
        if r.get("status") not in ("PASS",)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Live QU behavior smoke test")
    parser.add_argument(
        "--only-failed-from",
        metavar="REPORT_JSON",
        help="Rerun only failed/timeouted cases from a previous report JSON",
    )
    parser.add_argument(
        "--extended",
        action="store_true",
        help="Use the extended 30-case table instead of the default 10",
    )
    args = parser.parse_args()

    max_cases = int(os.getenv("LIVE_QU_MAX_CASES", "10"))
    delay = float(os.getenv("LIVE_QU_DELAY_SECONDS", "5"))

    # Choose case table
    all_cases = LIVE_CASES_EXTENDED if args.extended else LIVE_CASES

    # Filter to failed cases if requested
    if args.only_failed_from:
        failed_ids = _load_failed_ids(Path(args.only_failed_from))
        cases = [c for c in all_cases if c["id"] in failed_ids]
        if not cases:
            print("No failed cases found in the report. Nothing to rerun.")
            return
        print(f"Rerunning {len(cases)} failed case(s): {[c['id'] for c in cases]}\n")
    else:
        cases = all_cases[:max_cases]

    print("=" * 70)
    print(f"PathFinder Live QU Smoke Test")
    print(f"Cases: {len(cases)}  Delay: {delay}s  Timeout: {os.getenv('QU_TIMEOUT_SECONDS', '30')}s")
    print("=" * 70 + "\n")

    results = run(cases, delay)

    # Summary
    passed = sum(1 for r in results if r["status"] == "PASS")
    print("=" * 70)
    print(f"SUMMARY: {passed}/{len(results)} PASSED")
    failed_results = [r for r in results if r["status"] != "PASS"]
    if failed_results:
        print(f"FAILED:")
        for r in failed_results:
            print(f"  {r['id']} [{r['status']}] - expected={r['expected_intents']} got={r['predicted_intents']}")
    print("=" * 70)

    report_path = ROOT / "reports" / "qu_live_behavior_results.json"
    _save_report(results, report_path)

    # Exit code for CI
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
