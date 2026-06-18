"""
Live QU smoke test — requires LLM_API_KEY in .env
Run: python tests/smoke_test_qu.py
"""
from __future__ import annotations

import os
import sys
import time
import logging

# Suppress noisy library logs during smoke test
logging.basicConfig(level=logging.WARNING)

from dotenv import load_dotenv
load_dotenv(override=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gateway.models.schemas import LastReferenced
from gateway.query_understanding import understand_query
from gateway.qu_intents import LOCKED_INTENTS, FORBIDDEN_INTENTS

SMOKE_QUERIES = [
    "Tell me about C-CS301",
    "Can I take Operating Systems?",
    "What are the prerequisites for OS?",
    "Can I graduate, and if not give me a roadmap?",
    "If I get A in OS, what will my GPA be?",
    "What is the warning policy?",
    "What courses are in the AI track?",
    "What roles need Python?",
]

RESOLVER_QUERIES = [
    "Can I take Operating Systems?",
    "What are the prerequisites for OS?",
    "Tell me about Algorithms",
    "Tell me about Data Science",
]


def _fmt_sq(sq, index: int) -> list[str]:
    lines = [f"  SQ[{index}] intent={sq.intent!r}"]
    e = sq.entities
    entity_parts = []
    if e.course_code:
        entity_parts.append(f"course={e.course_code!r}")
    if e.role_id:
        entity_parts.append(f"role={e.role_id!r}")
    if e.track_id:
        entity_parts.append(f"track={e.track_id!r}")
    if e.skill_id:
        entity_parts.append(f"skill={e.skill_id!r}")
    if entity_parts:
        lines.append(f"         entities: {', '.join(entity_parts)}")

    if sq.params:
        lines.append(f"         params: {sq.params}")

    ovr = sq.session_overrides
    non_default = (
        ovr.added_courses
        or ovr.assumed_passed_courses
        or ovr.assumed_failed_courses
        or ovr.target_role
        or ovr.course_override_type != "none"
        or ovr.override_action != "accumulate"
    )
    if non_default:
        lines.append(
            f"         session_overrides: type={ovr.course_override_type!r} "
            f"added={ovr.added_courses} passed={ovr.assumed_passed_courses} "
            f"failed={ovr.assumed_failed_courses} action={ovr.override_action!r}"
        )

    lines.append(f"         student_referential={sq.student_referential_fallback}")
    return lines


# Delay between queries to stay within Groq TPM limits.
# llama-3.3-70b-versatile allows 12K TPM; each QU call uses ~1000-1500 tokens.
# 6 seconds between queries keeps 8 back-to-back calls safely under the limit.
_QUERY_DELAY_SECONDS = float(os.getenv("SMOKE_DELAY_SECONDS", "6"))


def run_smoke(label: str, queries: list[str], resolver=None) -> tuple[int, int]:
    passed = 0
    failed = 0
    last_ref = LastReferenced()

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  (delay between queries: {_QUERY_DELAY_SECONDS}s)")
    print(f"{'='*60}\n")

    for idx, query in enumerate(queries):
        if idx > 0:
            print(f"  [waiting {_QUERY_DELAY_SECONDS}s ...]")
            time.sleep(_QUERY_DELAY_SECONDS)
        print(f"INPUT: {query!r}")
        ok = True
        try:
            results = understand_query(query, last_ref, [], resolver=resolver)

            if not isinstance(results, list) or not results:
                print("  [FAIL]: did not return non-empty list")
                ok = False
            else:
                for i, sq in enumerate(results):
                    for line in _fmt_sq(sq, i):
                        print(line)

                    if sq.intent in FORBIDDEN_INTENTS:
                        print(f"  [FAIL]: forbidden intent {sq.intent!r}")
                        ok = False
                    elif sq.intent not in LOCKED_INTENTS:
                        print(f"  [FAIL]: unlocked intent {sq.intent!r}")
                        ok = False

        except Exception as exc:
            print(f"  [ERROR]: {type(exc).__name__}: {exc}")
            ok = False

        if ok:
            print("  [PASS]")
            passed += 1
        else:
            failed += 1
        print()

    return passed, failed


def _build_resolver():
    """Try to connect KGAdapter and return an injectable resolver."""
    try:
        from adapters.kg_adapter import KGAdapter
        kg = KGAdapter()
        # Quick connectivity check
        result = kg.call("get_track_overview", {"track_id": "AI"})
        if "error" in result and result["error"] == "kg_unavailable":
            return None, "Neo4j unavailable"
        return (
            lambda et, et_text: kg.call("resolve_entity", {"entity_type": et, "entity_text": et_text}),
            "connected"
        )
    except Exception as exc:
        return None, f"KGAdapter error: {exc}"


if __name__ == "__main__":
    total_passed = 0
    total_failed = 0

    # --- Part 1: no resolver ---
    p, f = run_smoke("PART 1 — Live LLM, no KG resolver", SMOKE_QUERIES)
    total_passed += p
    total_failed += f

    # --- Part 2: with resolver if KG is up ---
    print(f"\n{'='*60}")
    print("  PART 2 — KG resolver probe")
    print(f"{'='*60}")
    resolver, kg_status = _build_resolver()
    print(f"  KG status: {kg_status}\n")

    if resolver is not None:
        p, f = run_smoke("PART 2 — Live LLM + KG resolver", RESOLVER_QUERIES, resolver=resolver)
        total_passed += p
        total_failed += f
    else:
        print("  Skipping resolver tests — KG not available.\n")

    # --- Summary ---
    print(f"{'='*60}")
    print(f"  SMOKE TOTAL: {total_passed} passed, {total_failed} failed")
    print(f"{'='*60}")
    sys.exit(0 if total_failed == 0 else 1)
