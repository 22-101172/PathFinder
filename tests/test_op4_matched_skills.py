"""
Focused test for OP4 `matched_skills` addition.

Tests every case from the audit brief:
  1. ["SK_CPP"]
  2. ["SK_CPP", "SK_OOP"]
  3. ["SK_CPP", "SK_CPP"]   (duplicate input)
  4. ["SK_CPP", "SK_FAKE"]  (mixed valid/invalid)
  5. ["SK_FAKE"]             (all invalid)
  6. []                      (empty)

Run from project root:
    python tests/test_op4_matched_skills.py
"""

import os
import sys
import json

os.environ.setdefault("NEO4J_PASSWORD", "institution123")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engines.kg.neo4j_client import Neo4jClient          # noqa: E402
from engines.kg.queries import q_search_courses_by_skill  # noqa: E402


def _check_contract(result: dict, case_label: str) -> list[str]:
    """Return a list of violation strings; empty means contract is satisfied."""
    errors = []

    # Error responses only need "error" key — skip structural checks.
    if "error" in result:
        return errors

    # Top-level required keys
    for key in ("queried_skill_ids", "unrecognized_skill_ids", "results", "total_results"):
        if key not in result:
            errors.append(f"Missing top-level key: {key!r}")

    if "total_results" in result and "results" in result:
        if result["total_results"] != len(result["results"]):
            errors.append(
                f"total_results={result['total_results']} but len(results)={len(result['results'])}"
            )

    for i, course in enumerate(result.get("results", [])):
        prefix = f"results[{i}]"
        for key in ("course_code", "name", "tracks", "matched_skill_ids", "matched_skills"):
            if key not in course:
                errors.append(f"{prefix} missing key: {key!r}")

        # matched_skill_ids must still exist and be a list
        msi = course.get("matched_skill_ids", [])
        if not isinstance(msi, list):
            errors.append(f"{prefix} matched_skill_ids is not a list")

        # matched_skills must be a list of {skill_id, name, category}
        ms = course.get("matched_skills", [])
        if not isinstance(ms, list):
            errors.append(f"{prefix} matched_skills is not a list")
        else:
            seen_sids = set()
            for j, sk in enumerate(ms):
                sp = f"{prefix}.matched_skills[{j}]"
                for field in ("skill_id", "name", "category"):
                    if field not in sk:
                        errors.append(f"{sp} missing field: {field!r}")
                sid = sk.get("skill_id")
                if sid in seen_sids:
                    errors.append(f"{prefix} duplicate skill_id {sid!r} in matched_skills")
                seen_sids.add(sid)

            # matched_skills skill_ids must align with matched_skill_ids
            ms_ids = {sk.get("skill_id") for sk in ms}
            msi_set = set(msi)
            if ms_ids != msi_set:
                errors.append(
                    f"{prefix} matched_skills ids {ms_ids} != matched_skill_ids {msi_set}"
                )

    return errors


def run(client):
    cases = [
        ("single valid",         ["SK_CPP"]),
        ("two valid",            ["SK_CPP", "SK_OOP"]),
        ("duplicate input",      ["SK_CPP", "SK_CPP"]),
        ("mixed valid/invalid",  ["SK_CPP", "SK_FAKE"]),
        ("all invalid",          ["SK_FAKE"]),
        ("empty list",           []),
    ]

    all_passed = True

    for label, skill_ids in cases:
        result = q_search_courses_by_skill(client, skill_ids)
        violations = _check_contract(result, label)

        status = "PASS" if not violations else "FAIL"
        if violations:
            all_passed = False

        print(f"\n[{status}] {label}  ->  input={skill_ids!r}")

        if "error" in result:
            print(f"       error key: {result['error']!r}")
        else:
            print(f"       queried_skill_ids    : {result.get('queried_skill_ids')}")
            print(f"       unrecognized_skill_ids: {result.get('unrecognized_skill_ids')}")
            print(f"       total_results         : {result.get('total_results')}")
            for c in result.get("results", []):
                print(f"       course {c['course_code']:20s} matched_skill_ids={c['matched_skill_ids']}")
                for sk in c.get("matched_skills", []):
                    print(f"           -> {sk}")

        for v in violations:
            print(f"       !! {v}")

    print("\n" + "=" * 60)
    print("OP4 contract check:", "ALL PASS" if all_passed else "FAILURES DETECTED")
    print("=" * 60)
    return all_passed


def main():
    try:
        client = Neo4jClient()
        client.connect()
    except Exception as exc:
        print(f"[ABORT] Cannot connect to Neo4j: {exc}")
        sys.exit(1)

    ok = run(client)
    client.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
