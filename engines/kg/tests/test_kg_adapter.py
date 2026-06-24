"""
KGAdapter contract tests — Step 1C.

Scope:
  - Tests adapters/kg_adapter.py via adapter.call(operation, params) only.
  - Does NOT call queries.py functions directly.
  - KGAdapter dispatch layer only: no QU, no Orchestrator, no Composer, no API.
  - Requires a running Neo4j instance with the current PathFinder KG loaded.

Expected KG state:
  Course 59 | Track 5 | Skill 52 | Role 20 | PrerequisiteConstraint 2
  Removed: SK_Reinforcement_Learning, RL_IT_Manager

Run from project root:
    pytest engines/kg/tests/test_kg_adapter.py -v
"""

from __future__ import annotations
import os
import pytest

os.environ.setdefault("NEO4J_PASSWORD", "institution123")
os.environ.setdefault("NEO4J_URI",      "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER",     "neo4j")
os.environ.setdefault("NEO4J_DATABASE", "neo4j")

from adapters.kg_adapter import KGAdapter

# ── Shared student course sets ────────────────────────────────────────────────

DS_COURSES = ["C-CS111", "C-CS112", "C-CS213", "C-ST211", "C-DE211"]

ADAPTER_ERRORS = {"unknown_operation", "bad_params", "kg_unavailable", "kg_error"}


def _no_adapter_error(result: dict, operation: str = "") -> None:
    """Assert result is not an adapter-level error dict."""
    for err in ADAPTER_ERRORS:
        assert result.get("error") != err, (
            f"Adapter error for {operation!r}: {result}"
        )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def adapter():
    a = KGAdapter()
    if a._client is None:
        pytest.skip("Neo4j unavailable — skipping all KGAdapter tests")
    yield a
    a.close()


# ─────────────────────────────────────────────────────────────────────────────
# A. Adapter availability / connection
# ─────────────────────────────────────────────────────────────────────────────

class TestAConnection:

    def test_adapter_instantiates(self, adapter):
        assert adapter is not None

    def test_client_is_connected(self, adapter):
        assert adapter._client is not None

    def test_close_does_not_raise(self):
        a = KGAdapter()
        if a._client is None:
            pytest.skip("Neo4j unavailable")
        a.close()


# ─────────────────────────────────────────────────────────────────────────────
# B. Dispatch smoke test — all 18 operations
# ─────────────────────────────────────────────────────────────────────────────

VALID_CALLS = {
    "get_course_profile":             {"course_code": "C-CS219"},
    "get_prerequisites":              {"course_code": "C-CS219", "depth": "direct"},
    "get_skills_taught":              {"course_code": "C-CS219"},
    "search_courses_by_skill":        {"skill_ids": ["SK_CPP"]},
    "get_role_profile":               {"role_id": "RL_Data_Scientist"},
    "get_roles_by_track":             {"track_id": "AI"},
    "compute_skill_gap":              {"role_id": "RL_Data_Scientist", "completed_courses": DS_COURSES},
    "compute_alignment_score":        {"role_id": "RL_Data_Scientist", "completed_courses": DS_COURSES},
    "recommend_courses_to_close_gap": {"role_id": "RL_Data_Scientist", "completed_courses": DS_COURSES},
    "estimate_alignment_improvement": {"role_id": "RL_Data_Scientist", "completed_courses": DS_COURSES, "planned_courses": ["C-AI321", "C-DE312"]},
    "find_best_matching_roles":       {"completed_courses": DS_COURSES},
    "get_track_overview":             {"track_id": "AI"},
    "compare_tracks":                 {"track_id_1": "AI", "track_id_2": "DSE"},
    "recommend_track_for_role":       {"role_id": "RL_Data_Scientist"},
    "recommend_track_for_skill":      {"skill_id": "SK_ML"},
    "get_courses_by_track":           {"track_id": "AI"},
    "get_focus_courses_for_target":   {"target_id": "RL_Data_Scientist", "target_type": "role", "completed_courses": DS_COURSES},
    "resolve_entity":                 {"entity_type": "course", "entity_text": "advanced programming"},
}

# Per-operation required keys — adapter must not strip these
REQUIRED_KEYS = {
    "get_course_profile":             {"course_code", "name"},
    "get_prerequisites":              {"direct_prerequisites", "has_prerequisites"},
    "get_skills_taught":              {"course_code", "skills_taught"},
    "search_courses_by_skill":        {"results", "queried_skill_ids"},
    "get_role_profile":               {"role_id", "role_name"},
    "get_roles_by_track":             {"track_id", "results"},
    "compute_skill_gap":              {"role_id", "missing_skills"},
    "compute_alignment_score":        {"role_id", "alignment_score"},
    "recommend_courses_to_close_gap": {"role_id", "missing_skills"},
    "estimate_alignment_improvement": {"role_id", "current_alignment_score", "projected_alignment_score"},
    "find_best_matching_roles":       {"ranked_roles"},
    "get_track_overview":             {"track_id", "courses"},
    "compare_tracks":                 {"track_1", "track_2", "courses", "skills", "role_alignment"},
    "recommend_track_for_role":       {"role_id", "ranked_tracks"},
    "recommend_track_for_skill":      {"skill_id", "ranked_tracks"},
    "get_courses_by_track":           {"track_id", "courses"},
    "get_focus_courses_for_target":   {"target_id", "focus_courses"},
    "resolve_entity":                 {"status", "resolved_id"},
}


class TestBDispatchSmoke:

    @pytest.mark.parametrize("operation", list(VALID_CALLS.keys()))
    def test_dispatch_returns_dict(self, adapter, operation):
        params = VALID_CALLS[operation]
        result = adapter.call(operation, params)
        assert isinstance(result, dict), f"{operation} did not return dict: {result!r}"

    @pytest.mark.parametrize("operation", list(VALID_CALLS.keys()))
    def test_no_adapter_error(self, adapter, operation):
        params = VALID_CALLS[operation]
        result = adapter.call(operation, params)
        _no_adapter_error(result, operation)

    @pytest.mark.parametrize("operation", list(VALID_CALLS.keys()))
    def test_required_keys_present(self, adapter, operation):
        params = VALID_CALLS[operation]
        result = adapter.call(operation, params)
        _no_adapter_error(result, operation)
        for key in REQUIRED_KEYS[operation]:
            assert key in result, (
                f"{operation}: missing required key {key!r} — got {list(result.keys())}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# C. Full prerequisites adapter test
# ─────────────────────────────────────────────────────────────────────────────

class TestCFullPrerequisites:

    def test_cs219_full_prerequisites_no_adapter_error(self, adapter):
        result = adapter.call("get_prerequisites", {"course_code": "C-CS219", "depth": "full"})
        _no_adapter_error(result, "get_prerequisites depth=full")
        assert result.get("course_code") == "C-CS219"

    def test_cs219_full_prerequisites_has_prerequisites_true(self, adapter):
        result = adapter.call("get_prerequisites", {"course_code": "C-CS219", "depth": "full"})
        _no_adapter_error(result, "get_prerequisites depth=full")
        assert result.get("has_prerequisites") is True

    def test_cs219_full_prerequisites_tree_exists_and_is_list(self, adapter):
        result = adapter.call("get_prerequisites", {"course_code": "C-CS219", "depth": "full"})
        _no_adapter_error(result, "get_prerequisites depth=full")
        assert "full_prerequisite_tree" in result
        assert isinstance(result["full_prerequisite_tree"], list)

    def test_cs219_full_prerequisites_direct_prereqs_exist(self, adapter):
        result = adapter.call("get_prerequisites", {"course_code": "C-CS219", "depth": "full"})
        _no_adapter_error(result, "get_prerequisites depth=full")
        assert isinstance(result.get("direct_prerequisites"), list)
        assert len(result["direct_prerequisites"]) > 0, (
            "C-CS219 should have at least one direct prerequisite"
        )

    def test_cs219_full_tree_includes_cs112_direct(self, adapter):
        result = adapter.call("get_prerequisites", {"course_code": "C-CS219", "depth": "full"})
        _no_adapter_error(result, "get_prerequisites depth=full")
        direct_codes = [p.get("course_code") for p in result.get("direct_prerequisites", [])]
        assert "C-CS112" in direct_codes, (
            f"Expected C-CS112 as direct prerequisite of C-CS219, got: {direct_codes}"
        )

    def test_cs219_full_tree_not_empty_when_prereqs_exist(self, adapter):
        result = adapter.call("get_prerequisites", {"course_code": "C-CS219", "depth": "full"})
        _no_adapter_error(result, "get_prerequisites depth=full")
        if result.get("has_prerequisites"):
            tree = result.get("full_prerequisite_tree", [])
            assert len(tree) > 0, (
                "full_prerequisite_tree should not be empty when has_prerequisites is True "
                "and depth=full was requested — check that adapter passes depth correctly"
            )

    def test_cs219_full_tree_entries_have_course_codes(self, adapter):
        result = adapter.call("get_prerequisites", {"course_code": "C-CS219", "depth": "full"})
        _no_adapter_error(result, "get_prerequisites depth=full")
        for entry in result.get("full_prerequisite_tree", []):
            assert "course_code" in entry, (
                f"full_prerequisite_tree entry missing course_code: {entry}"
            )

    def test_ai422_full_prerequisites_passes_depth(self, adapter):
        result = adapter.call("get_prerequisites", {"course_code": "C-AI422", "depth": "full"})
        _no_adapter_error(result, "get_prerequisites C-AI422 depth=full")
        assert "full_prerequisite_tree" in result
        assert isinstance(result["full_prerequisite_tree"], list)
        assert isinstance(result.get("direct_prerequisites"), list)

    def test_depth_direct_returns_empty_full_tree(self, adapter):
        result = adapter.call("get_prerequisites", {"course_code": "C-CS219", "depth": "direct"})
        _no_adapter_error(result, "get_prerequisites depth=direct")
        assert result.get("full_prerequisite_tree") == [], (
            "depth=direct should return empty full_prerequisite_tree"
        )


# ─────────────────────────────────────────────────────────────────────────────
# D. Query-level business errors must pass through unchanged
# ─────────────────────────────────────────────────────────────────────────────

class TestDBusinessErrorPassthrough:

    def test_fake_course_returns_course_not_found(self, adapter):
        result = adapter.call("get_course_profile", {"course_code": "C-FAKE999"})
        assert result.get("error") == "course_not_found", (
            f"Expected course_not_found, got: {result}"
        )

    def test_removed_role_returns_role_not_found(self, adapter):
        result = adapter.call("get_role_profile", {"role_id": "RL_IT_Manager"})
        assert result.get("error") == "role_not_found", (
            f"Expected role_not_found, got: {result}"
        )

    def test_removed_skill_returns_skill_not_found(self, adapter):
        result = adapter.call("recommend_track_for_skill", {"skill_id": "SK_Reinforcement_Learning"})
        assert result.get("error") == "skill_not_found", (
            f"Expected skill_not_found, got: {result}"
        )

    def test_ambiguous_resolve_entity_returns_ambiguous_status(self, adapter):
        result = adapter.call("resolve_entity", {"entity_type": "course", "entity_text": "ai"})
        assert result.get("status") == "ambiguous", (
            f"Expected status=ambiguous for short/ambiguous query, got: {result}"
        )
        assert "matches" in result, "Ambiguous result should include matches list"

    def test_business_errors_are_not_wrapped_as_kg_error(self, adapter):
        result = adapter.call("get_course_profile", {"course_code": "C-FAKE999"})
        assert result.get("error") != "kg_error", (
            "Business errors (course_not_found) must pass through unchanged, "
            "not be wrapped as kg_error"
        )


# ─────────────────────────────────────────────────────────────────────────────
# E. Adapter-level errors
# ─────────────────────────────────────────────────────────────────────────────

class TestEAdapterErrors:

    def test_unknown_operation_returns_error(self, adapter):
        result = adapter.call("not_a_real_operation", {})
        assert result.get("error") == "unknown_operation"
        assert result.get("detail") == "not_a_real_operation"

    def test_bad_params_wrong_key(self, adapter):
        result = adapter.call("get_course_profile", {"wrong_param": "C-CS219"})
        assert result.get("error") == "bad_params", (
            f"Expected bad_params for wrong key, got: {result}"
        )

    def test_bad_params_missing_required_key(self, adapter):
        result = adapter.call("compare_tracks", {"track_id_1": "AI"})
        assert result.get("error") == "bad_params", (
            f"Expected bad_params for missing track_id_2, got: {result}"
        )

    def test_kg_unavailable_when_client_is_none(self):
        a = KGAdapter()
        a._client = None
        result = a.call("get_course_profile", {"course_code": "C-CS219"})
        assert result.get("error") == "kg_unavailable", (
            f"Expected kg_unavailable when _client is None, got: {result}"
        )

    def test_unknown_operation_includes_operation_name_in_detail(self, adapter):
        result = adapter.call("totally_fake_op", {})
        assert result.get("error") == "unknown_operation"
        assert "totally_fake_op" in str(result.get("detail", ""))


# ─────────────────────────────────────────────────────────────────────────────
# F. Adapter preserves useful fields (field passthrough)
# ─────────────────────────────────────────────────────────────────────────────

class TestFFieldPassthrough:

    def test_op4_search_courses_by_skill_matched_skills_structure(self, adapter):
        result = adapter.call("search_courses_by_skill", {"skill_ids": ["SK_CPP"]})
        _no_adapter_error(result, "search_courses_by_skill")
        assert "results" in result
        assert "queried_skill_ids" in result
        assert len(result["results"]) > 0, "Expected at least one course result for SK_CPP"
        first = result["results"][0]
        assert "matched_skill_ids" in first, (
            f"Course result missing matched_skill_ids: {list(first.keys())}"
        )
        assert "matched_skills" in first, (
            f"Course result missing matched_skills: {list(first.keys())}"
        )

    def test_op4_search_courses_by_skill_matched_skills_fields(self, adapter):
        result = adapter.call("search_courses_by_skill", {"skill_ids": ["SK_CPP"]})
        _no_adapter_error(result, "search_courses_by_skill")
        for course in result.get("results", []):
            for skill in course.get("matched_skills", []):
                assert "skill_id" in skill, f"Skill entry missing skill_id: {skill}"
                assert "name" in skill, f"Skill entry missing name: {skill}"
                assert "category" in skill, f"Skill entry missing category: {skill}"

    def test_op16_get_courses_by_track_course_fields(self, adapter):
        result = adapter.call("get_courses_by_track", {"track_id": "AI"})
        _no_adapter_error(result, "get_courses_by_track")
        assert "courses" in result
        assert len(result["courses"]) > 0, "Expected at least one course for AI track"
        required_course_fields = {
            "course_code", "name", "credits", "level",
            "semester_offering", "prerequisites", "credit_threshold",
        }
        for course in result["courses"]:
            for field in required_course_fields:
                assert field in course, (
                    f"Course entry missing {field!r}: {list(course.keys())}"
                )

    def test_op17_get_focus_courses_excludes_completed(self, adapter):
        completed = DS_COURSES
        result = adapter.call(
            "get_focus_courses_for_target",
            {"target_id": "RL_Data_Scientist", "target_type": "role", "completed_courses": completed},
        )
        _no_adapter_error(result, "get_focus_courses_for_target")
        assert "focus_courses" in result
        for course in result["focus_courses"]:
            code = course.get("course_code", "")
            assert code not in completed, (
                f"Completed course {code!r} should not appear in focus_courses"
            )

    def test_op17_get_focus_courses_has_name_field(self, adapter):
        result = adapter.call(
            "get_focus_courses_for_target",
            {"target_id": "RL_Data_Scientist", "target_type": "role", "completed_courses": DS_COURSES},
        )
        _no_adapter_error(result, "get_focus_courses_for_target")
        for course in result.get("focus_courses", []):
            assert "name" in course, (
                f"Focus course entry missing name: {list(course.keys())}"
            )

    def test_op17_get_focus_courses_has_evidence_field(self, adapter):
        result = adapter.call(
            "get_focus_courses_for_target",
            {"target_id": "RL_Data_Scientist", "target_type": "role", "completed_courses": DS_COURSES},
        )
        _no_adapter_error(result, "get_focus_courses_for_target")
        evidence_keys = {"relevant_skills", "matched_skills", "skill_ids", "skills"}
        for course in result.get("focus_courses", []):
            found = any(k in course for k in evidence_keys)
            assert found, (
                f"Focus course entry has no skill evidence field; got keys: {list(course.keys())}"
            )

    def test_op17_not_converted_to_planning_response(self, adapter):
        result = adapter.call(
            "get_focus_courses_for_target",
            {"target_id": "RL_Data_Scientist", "target_type": "role", "completed_courses": DS_COURSES},
        )
        _no_adapter_error(result, "get_focus_courses_for_target")
        planning_keys = {"plan", "schedule", "eligibility", "academic_plan"}
        for key in planning_keys:
            assert key not in result, (
                f"Adapter added unexpected planning key {key!r} — adapter must not add advising logic"
            )
