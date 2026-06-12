"""
Live integration smoke test for KGAdapter.
Tests all 18 registered operations against the real Neo4j instance.
No mocking — every call goes to the actual graph database.

Run from the project root:
    python tests/test_kg_adapter.py
"""

import os
import sys

# Ensure NEO4J_PASSWORD is available before any import triggers load_dotenv.
# If .env already sets it this is a no-op; otherwise falls back to the known
# local dev password so the script is self-contained.
os.environ.setdefault("NEO4J_PASSWORD", "institution123")

# Make sure the project root is on sys.path regardless of where the script
# is invoked from.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from adapters.kg_adapter import KGAdapter  # noqa: E402


# ── Constants ────────────────────────────────────────────────────────────────

COURSE_CODE   = "C-CS111"   # fall back course; exists in real graph
TRACK_ID      = "AI"
TRACK_ID_2    = "SWE"
# Alignment / gap operations reject empty completed_courses at the
# validation layer (returns error: no_courses_provided), so we supply
# a real course to actually exercise the Neo4j path.
COMPLETED     = ["C-CS111"]
PLANNED       = ["C-AI321"]

TOTAL_OPS     = 18


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_pass(result: dict) -> bool:
    return "error" not in result


def _run(adapter: KGAdapter, operation: str, params: dict) -> tuple[bool, dict]:
    result = adapter.call(operation, params)
    return _is_pass(result), result


def _print_result(operation: str, passed: bool, result: dict) -> None:
    if passed:
        print(f"  [PASS]  {operation}")
    else:
        print(f"  [FAIL]  {operation}  —  error: {result.get('error', 'unknown')}")


# ── Bootstrap: discover dynamic values from the graph ────────────────────────

def _bootstrap(adapter: KGAdapter) -> tuple[str | None, str | None, str | None]:
    """Return (role_id, skill_id, skill_name) from live graph data."""
    role_id = skill_id = skill_name = None

    roles_result = adapter.call("get_roles_by_track", {"track_id": TRACK_ID})
    if "results" in roles_result and roles_result["results"]:
        role_id = roles_result["results"][0]["role_id"]

    skills_result = adapter.call("get_skills_taught", {"course_code": COURSE_CODE})
    if "skills_taught" in skills_result and skills_result["skills_taught"]:
        first_skill = skills_result["skills_taught"][0]
        skill_id   = first_skill["skill_id"]
        skill_name = first_skill["name"]

    return role_id, skill_id, skill_name


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("KGAdapter live smoke test")
    print(f"  course  : {COURSE_CODE}")
    print(f"  track   : {TRACK_ID}  vs  {TRACK_ID_2}")
    print("=" * 60)

    adapter = KGAdapter()

    if adapter._client is None:
        print("\n[ABORT] KGAdapter could not connect to Neo4j.")
        print("        Make sure Neo4j is running and NEO4J_PASSWORD is correct.")
        sys.exit(1)

    # Discover role_id and skill_id from live data before running the suite.
    role_id, skill_id, skill_name = _bootstrap(adapter)

    print(f"\n  Bootstrapped:  role_id={role_id!r}  "
          f"skill_id={skill_id!r}  skill_name={skill_name!r}\n")

    # Build the ordered list of (operation, params) for all 18 operations.
    test_cases: list[tuple[str, dict]] = [
        (
            "get_course_profile",
            {"course_code": COURSE_CODE},
        ),
        (
            "get_prerequisites",
            {"course_code": COURSE_CODE, "depth": "direct"},
        ),
        (
            "get_skills_taught",
            {"course_code": COURSE_CODE},
        ),
        (
            "search_courses_by_skill",
            {"skills": [skill_name] if skill_name else ["Python"]},
        ),
        (
            "get_role_profile",
            {"role_id": role_id or "UNKNOWN"},
        ),
        (
            "get_roles_by_track",
            {"track_id": TRACK_ID},
        ),
        (
            "compute_skill_gap",
            {"role_id": role_id or "UNKNOWN", "completed_courses": COMPLETED},
        ),
        (
            "compute_alignment_score",
            {"role_id": role_id or "UNKNOWN", "completed_courses": COMPLETED},
        ),
        (
            "recommend_courses_to_close_gap",
            {"role_id": role_id or "UNKNOWN", "completed_courses": COMPLETED},
        ),
        (
            "estimate_alignment_improvement",
            {
                "role_id": role_id or "UNKNOWN",
                "completed_courses": COMPLETED,
                "planned_courses": PLANNED,
            },
        ),
        (
            "find_best_matching_roles",
            {"completed_courses": COMPLETED},
        ),
        (
            "get_track_overview",
            {"track_id": TRACK_ID},
        ),
        (
            "compare_tracks",
            {"track_id_1": TRACK_ID, "track_id_2": TRACK_ID_2},
        ),
        (
            "recommend_track_for_role",
            {"role_id": role_id or "UNKNOWN"},
        ),
        (
            "recommend_track_for_skill",
            {"skill_id": skill_id or "UNKNOWN"},
        ),
        (
            "get_courses_by_track",
            {"track_id": TRACK_ID},
        ),
        (
            "get_focus_courses_for_target",
            {"target_id": TRACK_ID, "target_type": "track", "completed_courses": []},
        ),
        (
            "resolve_entity",
            {"entity_type": "course", "entity_text": "intro to programming"},
        ),
    ]

    assert len(test_cases) == TOTAL_OPS, (
        f"Expected {TOTAL_OPS} test cases, got {len(test_cases)}"
    )

    # Run each operation and collect results.
    passed_count = 0

    ok, result = _run(adapter, "get_course_profile", {"course_code": COURSE_CODE})
    _print_result("get_course_profile", ok, result)
    assert ok, "get_course_profile must not return error"
    assert result.get("course_code") == COURSE_CODE, f"Expected course_code {COURSE_CODE}, got {result.get('course_code')}"
    assert result.get("name") is not None, "get_course_profile must return a course name"
    assert isinstance(result.get("credits"), (int, float)), "credits must be numeric"
    passed_count += 1

    ok, result = _run(adapter, "get_prerequisites", {"course_code": COURSE_CODE, "depth": "direct"})
    _print_result("get_prerequisites", ok, result)
    assert ok, "get_prerequisites must not return error"
    assert "direct_prerequisites" in result, "get_prerequisites must return a direct_prerequisites key"
    assert isinstance(result["direct_prerequisites"], list), "direct_prerequisites must be a list"
    passed_count += 1

    ok, result = _run(adapter, "get_skills_taught", {"course_code": COURSE_CODE})
    _print_result("get_skills_taught", ok, result)
    assert ok, "get_skills_taught must not return error"
    assert "skills_taught" in result, "Missing skills_taught key"
    assert isinstance(result["skills_taught"], list), "skills_taught must be a list"
    passed_count += 1

    ok, result = _run(adapter, "search_courses_by_skill", {"skills": [skill_name] if skill_name else ["Python"]})
    _print_result("search_courses_by_skill", ok, result)
    assert ok, "search_courses_by_skill must not return error"
    assert "results" in result, "Missing results key"
    passed_count += 1

    ok, result = _run(adapter, "get_role_profile", {"role_id": role_id or "UNKNOWN"})
    _print_result("get_role_profile", ok, result)
    assert ok, "get_role_profile must not return error"
    assert result.get("role_id") == role_id, f"Expected role_id {role_id}"
    assert "required_skills" in result, "get_role_profile must return required_skills"
    assert isinstance(result["required_skills"], list), "required_skills must be a list"
    passed_count += 1

    ok, result = _run(adapter, "get_roles_by_track", {"track_id": TRACK_ID})
    _print_result("get_roles_by_track", ok, result)
    assert ok, "get_roles_by_track must not return error"
    assert "results" in result, "Missing results key"
    assert len(result["results"]) > 0, "AI track must have at least one role"
    passed_count += 1

    ok, result = _run(adapter, "compute_skill_gap", {"role_id": role_id or "UNKNOWN", "completed_courses": COMPLETED})
    _print_result("compute_skill_gap", ok, result)
    assert ok, "compute_skill_gap must not return error"
    assert "missing_skills" in result, "Missing missing_skills key"
    assert isinstance(result["missing_skills"], list), "missing_skills must be a list"
    passed_count += 1

    ok, result = _run(adapter, "compute_alignment_score", {"role_id": role_id or "UNKNOWN", "completed_courses": COMPLETED})
    _print_result("compute_alignment_score", ok, result)
    assert ok, "compute_alignment_score must not return error"
    assert "alignment_score" in result, "Missing alignment_score key"
    score = result["alignment_score"]
    assert 0.0 <= score <= 1.0, f"alignment_score must be between 0 and 1, got {score}"
    passed_count += 1

    ok, result = _run(adapter, "recommend_courses_to_close_gap", {"role_id": role_id or "UNKNOWN", "completed_courses": COMPLETED})
    _print_result("recommend_courses_to_close_gap", ok, result)
    assert ok, "recommend_courses_to_close_gap must not return error"
    assert "missing_skills" in result or "total_missing_skills" in result, "Missing expected keys"
    passed_count += 1

    ok, result = _run(adapter, "estimate_alignment_improvement", {
        "role_id": role_id or "UNKNOWN",
        "completed_courses": COMPLETED,
        "planned_courses": PLANNED,
    })
    _print_result("estimate_alignment_improvement", ok, result)
    assert ok, "estimate_alignment_improvement must not return error"
    assert "current_alignment_score" in result, "Missing current_alignment_score key"
    assert "projected_alignment_score" in result, "Missing projected_alignment_score key"
    assert 0.0 <= result["current_alignment_score"] <= 1.0, "current_alignment_score must be between 0 and 1"
    assert 0.0 <= result["projected_alignment_score"] <= 1.0, "projected_alignment_score must be between 0 and 1"
    passed_count += 1

    ok, result = _run(adapter, "find_best_matching_roles", {"completed_courses": COMPLETED})
    _print_result("find_best_matching_roles", ok, result)
    assert ok, "find_best_matching_roles must not return error"
    assert "ranked_roles" in result, "Missing ranked_roles key"
    assert isinstance(result["ranked_roles"], list), "ranked_roles must be a list"
    passed_count += 1

    ok, result = _run(adapter, "get_track_overview", {"track_id": TRACK_ID})
    _print_result("get_track_overview", ok, result)
    assert ok, "get_track_overview must not return error"
    assert result.get("track_id") == TRACK_ID, f"Expected track_id {TRACK_ID}"
    assert result.get("track_name") is not None, "track_name must not be None"
    passed_count += 1

    ok, result = _run(adapter, "compare_tracks", {"track_id_1": TRACK_ID, "track_id_2": TRACK_ID_2})
    _print_result("compare_tracks", ok, result)
    assert ok, "compare_tracks must not return error"
    assert "track_1" in result and "track_2" in result, "Missing track_1 or track_2 keys"
    passed_count += 1

    ok, result = _run(adapter, "recommend_track_for_role", {"role_id": role_id or "UNKNOWN"})
    _print_result("recommend_track_for_role", ok, result)
    assert ok, "recommend_track_for_role must not return error"
    assert "recommendations" in result or "ranked_tracks" in result, "Missing recommendations key"
    passed_count += 1

    ok, result = _run(adapter, "recommend_track_for_skill", {"skill_id": skill_id or "UNKNOWN"})
    _print_result("recommend_track_for_skill", ok, result)
    assert ok, "recommend_track_for_skill must not return error"
    assert "tracks" in result or "ranked_tracks" in result, "Missing tracks key"
    passed_count += 1

    ok, result = _run(adapter, "get_courses_by_track", {"track_id": TRACK_ID})
    _print_result("get_courses_by_track", ok, result)
    assert ok, "get_courses_by_track must not return error"
    assert result.get("track_id") == TRACK_ID, f"Expected track_id {TRACK_ID}"
    assert "courses" in result, "Missing courses key"
    assert len(result["courses"]) > 0, "AI track must have at least one course"
    for course in result["courses"]:
        assert "semester_offering" in course, f"Course {course.get('course_code')} missing semester_offering"
        assert isinstance(course["semester_offering"], list), "semester_offering must be a list"
        # Issue 2.1: credit_threshold must be present and typed correctly
        assert "credit_threshold" in course, (
            f"Course {course.get('course_code')} missing credit_threshold"
        )
        assert course["credit_threshold"] is None or isinstance(course["credit_threshold"], int), (
            f"credit_threshold must be int or None for {course.get('course_code')}, "
            f"got {type(course['credit_threshold'])}"
        )
    passed_count += 1

    ok, result = _run(adapter, "get_focus_courses_for_target", {"target_id": TRACK_ID, "target_type": "track", "completed_courses": []})
    _print_result("get_focus_courses_for_target", ok, result)
    assert ok, "get_focus_courses_for_target must not return error"
    assert "focus_courses" in result, "Missing focus_courses key"
    assert isinstance(result["focus_courses"], list), "focus_courses must be a list"
    passed_count += 1

    ok, result = _run(adapter, "resolve_entity", {"entity_type": "course", "entity_text": "intro to programming"})
    _print_result("resolve_entity", ok, result)
    assert ok, "resolve_entity must not return error"
    assert "resolved_id" in result, "Missing resolved_id key"
    resolved_id = result.get("resolved_id")
    assert resolved_id is not None, "resolve_entity must resolve 'intro to programming' to a known course"
    assert resolved_id == COURSE_CODE, (
        f"Expected resolve_entity to return {COURSE_CODE}, got {resolved_id}"
    )
    passed_count += 1

    # ── Issue 2.1: credit_threshold focused checks ───────────────────────────
    print()
    print("-- credit_threshold checks (Issue 2.1) --")

    # GEN track contains C-CS351 which has a 59-hour CREDIT_THRESHOLD
    ok, gen_result = _run(adapter, "get_courses_by_track", {"track_id": "GEN"})
    assert ok, "get_courses_by_track(GEN) must not return error"
    gen_by_code = {c["course_code"]: c for c in gen_result.get("courses", [])}
    assert "C-CS351" in gen_by_code, "C-CS351 must be present in GEN track"
    ct = gen_by_code["C-CS351"]["credit_threshold"]
    assert ct == 59, f"C-CS351 credit_threshold must be int 59, got {ct!r}"
    print("  [PASS]  get_courses_by_track GEN: C-CS351 credit_threshold == 59")

    # A normal course (C-CS111) in AI track must have credit_threshold == None
    ai_by_code = {c["course_code"]: c for c in result.get("courses", [])}
    if "C-CS111" in ai_by_code:
        ct_normal = ai_by_code["C-CS111"]["credit_threshold"]
        assert ct_normal is None, f"C-CS111 credit_threshold must be None, got {ct_normal!r}"
        print("  [PASS]  get_courses_by_track AI: C-CS111 credit_threshold is None")

    # get_prerequisites for C-GP411A must return value as int 80, not raw string
    ok, gp411a = _run(adapter, "get_prerequisites", {"course_code": "C-GP411A", "depth": "direct"})
    assert ok, "get_prerequisites(C-GP411A) must not return error"
    non_course = gp411a.get("non_course_prerequisites", [])
    assert len(non_course) == 1, (
        f"C-GP411A must have exactly 1 non-course prereq, got {len(non_course)}"
    )
    assert non_course[0]["type"] == "CREDIT_THRESHOLD"
    assert non_course[0]["value"] == 80, (
        f"C-GP411A non_course_prerequisites[0].value must be int 80, got {non_course[0]['value']!r}"
    )
    print("  [PASS]  get_prerequisites C-GP411A: value == 80 (int, not string)")

    # get_prerequisites for C-CS351 must return value as int 59
    ok, cs351 = _run(adapter, "get_prerequisites", {"course_code": "C-CS351", "depth": "direct"})
    assert ok, "get_prerequisites(C-CS351) must not return error"
    nc351 = cs351.get("non_course_prerequisites", [])
    assert len(nc351) == 1, f"C-CS351 must have exactly 1 non-course prereq, got {len(nc351)}"
    assert nc351[0]["value"] == 59, (
        f"C-CS351 non_course_prerequisites[0].value must be int 59, got {nc351[0]['value']!r}"
    )
    print("  [PASS]  get_prerequisites C-CS351: value == 59 (int, not string)")

    adapter.close()

    print()
    print("=" * 60)
    print(f"Summary: {passed_count}/{TOTAL_OPS} passed")
    print("=" * 60)


if __name__ == "__main__":
    main()
