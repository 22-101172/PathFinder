"""
Live KG-Engine operation tests — OP1 through OP18.

Scope:
  - Tests engines/kg/queries.py functions directly via Neo4jClient.
  - KG Engine only: no QU, no KGAdapter, no Orchestrator, no Composer, no API.
  - Requires a running Neo4j instance with the current PathFinder KG loaded.

Expected KG state:
  Course 59 | Track 5 | Skill 52 | Role 20 | PrerequisiteConstraint 2
  PREREQ 47 | HAS_PREREQ_CONSTRAINT 6 | BELONGS_TO 61 | TEACHES 101 | REQUIRES 180
  Removed: SK_Reinforcement_Learning, RL_IT_Manager

Run from project root:
    pytest engines/kg/tests/test_queries_operations.py -v
"""

import os
import pytest

os.environ.setdefault("NEO4J_PASSWORD", "institution123")
os.environ.setdefault("NEO4J_URI",      "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER",     "neo4j")
os.environ.setdefault("NEO4J_DATABASE", "neo4j")

from engines.kg.neo4j_client import Neo4jClient
from engines.kg.queries import (
    q_get_course_profile,
    q_get_prerequisites,
    q_get_skills_taught,
    q_search_courses_by_skill,
    q_get_role_profile,
    q_get_roles_by_track,
    q_compute_skill_gap,
    q_compute_alignment_score,
    q_recommend_courses_to_close_gap,
    q_estimate_alignment_improvement,
    q_find_best_matching_roles,
    q_get_track_overview,
    q_compare_tracks,
    q_recommend_track_for_role,
    q_recommend_track_for_skill,
    q_get_courses_by_track,
    q_get_focus_courses_for_target,
    q_resolve_entity,
)

# ── Realistic student course sets ────────────────────────────────────────────

FOUNDATION_STUDENT = ["HUM111", "C-PH111", "C-CS111", "C-MA111", "HUM126"]

CS_PROGRESS_STUDENT = [
    "C-CS111", "C-CS112", "C-CS213", "C-CS218",
    "C-ST211", "C-DE211",
]

AI_PROGRESS_STUDENT = [
    "C-CS111", "C-CS112", "C-CS213", "C-CS218",
    "C-ST211", "C-AI311", "C-AI321",
]

SWE_PROGRESS_STUDENT = [
    "C-CS111", "C-CS112", "C-CS213",
    "C-SW312", "C-SW421",
]

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def client():
    try:
        c = Neo4jClient()
        c.connect()
    except Exception as exc:
        pytest.skip(f"Neo4j unavailable — skipping all KG tests: {exc}")
    yield c
    c.close()


# ─────────────────────────────────────────────────────────────────────────────
# OP1 — q_get_course_profile
# ─────────────────────────────────────────────────────────────────────────────

class TestOP1GetCourseProfile:

    REQUIRED_KEYS = {
        "course_code", "name", "credits", "level",
        "semester_offering", "tracks", "description", "credit_threshold",
    }

    def _assert_profile_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert isinstance(result["semester_offering"], list)
        assert isinstance(result["tracks"], list)
        assert isinstance(result["credits"], (int, float))
        assert result["course_code"] == result["course_code"].strip()

    def test_cs219_advanced_programming(self, client):
        r = q_get_course_profile(client, "C-CS219")
        self._assert_profile_contract(r)
        assert r["course_code"] == "C-CS219"
        assert r["name"]
        assert r["credits"] > 0

    def test_ai321_machine_learning(self, client):
        r = q_get_course_profile(client, "C-AI321")
        self._assert_profile_contract(r)
        assert r["course_code"] == "C-AI321"
        assert r["level"] in (1, 2, 3, 4)

    def test_cs351_selected_topics(self, client):
        r = q_get_course_profile(client, "C-CS351")
        self._assert_profile_contract(r)
        assert r["course_code"] == "C-CS351"

    def test_gp411a_graduation_project_has_credit_threshold(self, client):
        r = q_get_course_profile(client, "C-GP411A")
        self._assert_profile_contract(r)
        assert r["course_code"] == "C-GP411A"
        assert isinstance(r["credit_threshold"], int), (
            "C-GP411A must carry a CREDIT_THRESHOLD constraint"
        )
        assert r["credit_threshold"] > 0

    def test_empty_string_returns_invalid_code_error(self, client):
        r = q_get_course_profile(client, "")
        assert r.get("error") == "invalid_course_code"

    def test_whitespace_only_returns_invalid_code_error(self, client):
        r = q_get_course_profile(client, "   ")
        assert r.get("error") == "invalid_course_code"

    def test_punctuation_only_returns_invalid_code_error(self, client):
        r = q_get_course_profile(client, "???")
        assert r.get("error") == "invalid_course_code"

    def test_fake_code_returns_not_found(self, client):
        r = q_get_course_profile(client, "C-FAKE999")
        assert r.get("error") == "course_not_found"
        assert r.get("submitted_code") == "C-FAKE999"


# ─────────────────────────────────────────────────────────────────────────────
# OP2 — q_get_prerequisites
# ─────────────────────────────────────────────────────────────────────────────

class TestOP2GetPrerequisites:

    REQUIRED_KEYS = {
        "course_code", "name", "direct_prerequisites",
        "non_course_prerequisites", "has_prerequisites", "full_prerequisite_tree",
    }

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert isinstance(result["direct_prerequisites"], list)
        assert isinstance(result["non_course_prerequisites"], list)
        assert isinstance(result["has_prerequisites"], bool)
        assert isinstance(result["full_prerequisite_tree"], list)
        for prereq in result["direct_prerequisites"]:
            assert "course_code" in prereq
            assert "name" in prereq

    def test_cs219_direct_has_prerequisites(self, client):
        r = q_get_prerequisites(client, "C-CS219", depth="direct")
        self._assert_contract(r)
        assert r["course_code"] == "C-CS219"
        assert r["has_prerequisites"] is True
        assert r["full_prerequisite_tree"] == []

    def test_cs219_full_tree(self, client):
        r = q_get_prerequisites(client, "C-CS219", depth="full")
        self._assert_contract(r)
        assert r["full_prerequisite_tree"] != [] or r["has_prerequisites"] is True

    def test_cs351_selected_topics_direct(self, client):
        r = q_get_prerequisites(client, "C-CS351", depth="direct")
        self._assert_contract(r)
        assert r["course_code"] == "C-CS351"

    def test_gp411a_has_credit_threshold_constraint(self, client):
        r = q_get_prerequisites(client, "C-GP411A", depth="direct")
        self._assert_contract(r)
        threshold_entries = [
            nc for nc in r["non_course_prerequisites"]
            if nc.get("type") == "CREDIT_THRESHOLD"
        ]
        assert threshold_entries, "C-GP411A must have a CREDIT_THRESHOLD non-course prerequisite"
        assert isinstance(threshold_entries[0]["value"], int)

    def test_hum111_has_prerequisite_hum110(self, client):
        # HUM111 (English) requires HUM110 (English Fundamentals) in the KG.
        r = q_get_prerequisites(client, "HUM111", depth="direct")
        self._assert_contract(r)
        assert r["has_prerequisites"] is True
        prereq_codes = [p["course_code"] for p in r["direct_prerequisites"]]
        assert "HUM110" in prereq_codes

    def test_no_prerequisites_course_ma111(self, client):
        r = q_get_prerequisites(client, "C-MA111", depth="direct")
        self._assert_contract(r)
        assert r["has_prerequisites"] is False

    def test_invalid_depth_returns_error(self, client):
        r = q_get_prerequisites(client, "C-CS219", depth="recursive")
        assert r.get("error") == "invalid_depth_value"
        assert "submitted_depth" in r

    def test_fake_course_returns_not_found(self, client):
        r = q_get_prerequisites(client, "C-FAKE999", depth="direct")
        assert r.get("error") == "course_not_found"

    def test_empty_code_returns_invalid(self, client):
        r = q_get_prerequisites(client, "", depth="direct")
        assert r.get("error") == "invalid_course_code"


# ─────────────────────────────────────────────────────────────────────────────
# OP3 — q_get_skills_taught
# ─────────────────────────────────────────────────────────────────────────────

class TestOP3GetSkillsTaught:

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert "skills_taught" in result
        assert "total_skills" in result
        assert "course_code" in result
        assert "name" in result
        assert isinstance(result["skills_taught"], list)
        assert result["total_skills"] == len(result["skills_taught"])
        for sk in result["skills_taught"]:
            assert "skill_id" in sk
            assert "name" in sk
            assert "category" in sk

    def test_cs219_has_skills(self, client):
        r = q_get_skills_taught(client, "C-CS219")
        self._assert_contract(r)
        assert r["total_skills"] > 0

    def test_cs213_data_structures_has_skills(self, client):
        r = q_get_skills_taught(client, "C-CS213")
        self._assert_contract(r)
        assert r["total_skills"] > 0

    def test_ai422_has_skills(self, client):
        r = q_get_skills_taught(client, "C-AI422")
        self._assert_contract(r)
        assert r["total_skills"] > 0

    def test_de324_big_data_has_skills(self, client):
        r = q_get_skills_taught(client, "C-DE324")
        self._assert_contract(r)

    def test_gp411a_may_have_no_skills(self, client):
        r = q_get_skills_taught(client, "C-GP411A")
        self._assert_contract(r)
        assert r["total_skills"] >= 0

    def test_cs351_selected_topics_returns_success(self, client):
        r = q_get_skills_taught(client, "C-CS351")
        self._assert_contract(r)

    def test_fake_course_returns_not_found(self, client):
        r = q_get_skills_taught(client, "C-FAKE999")
        assert r.get("error") == "course_not_found"

    def test_empty_code_returns_invalid(self, client):
        r = q_get_skills_taught(client, "")
        assert r.get("error") == "invalid_course_code"


# ─────────────────────────────────────────────────────────────────────────────
# OP4 — q_search_courses_by_skill
# ─────────────────────────────────────────────────────────────────────────────

class TestOP4SearchCoursesBySkill:

    REQUIRED_KEYS = {"queried_skill_ids", "unrecognized_skill_ids", "results", "total_results"}

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert result["total_results"] == len(result["results"])
        for course in result["results"]:
            assert "course_code" in course
            assert "name" in course
            assert "matched_skill_ids" in course
            assert "matched_skills" in course
            assert isinstance(course["matched_skill_ids"], list)
            assert isinstance(course["matched_skills"], list)
            ms_ids = {sk["skill_id"] for sk in course["matched_skills"]}
            assert ms_ids == set(course["matched_skill_ids"]), (
                "matched_skills ids must match matched_skill_ids"
            )
            for sk in course["matched_skills"]:
                assert "skill_id" in sk
                assert "name" in sk
                assert "category" in sk

    def test_single_valid_skill(self, client):
        r = q_search_courses_by_skill(client, ["SK_CPP"])
        self._assert_contract(r)
        assert r["total_results"] > 0
        assert "SK_CPP" in r["queried_skill_ids"]
        assert "SK_CPP" not in r["unrecognized_skill_ids"]

    def test_two_valid_skills(self, client):
        r = q_search_courses_by_skill(client, ["SK_CPP", "SK_OOP"])
        self._assert_contract(r)
        assert r["total_results"] > 0
        assert set(r["queried_skill_ids"]) == {"SK_CPP", "SK_OOP"}

    def test_duplicate_input_deduplicates(self, client):
        r = q_search_courses_by_skill(client, ["SK_CPP", "SK_CPP"])
        self._assert_contract(r)
        assert r["queried_skill_ids"] == ["SK_CPP"]

    def test_mixed_valid_and_fake(self, client):
        r = q_search_courses_by_skill(client, ["SK_CPP", "SK_FAKE"])
        self._assert_contract(r)
        assert "SK_FAKE" in r["unrecognized_skill_ids"]
        assert "SK_CPP" not in r["unrecognized_skill_ids"]
        assert r["total_results"] > 0

    def test_only_fake_skill_unrecognized(self, client):
        r = q_search_courses_by_skill(client, ["SK_FAKE"])
        self._assert_contract(r)
        assert "SK_FAKE" in r["unrecognized_skill_ids"]
        assert r["total_results"] == 0

    def test_empty_list_returns_error(self, client):
        r = q_search_courses_by_skill(client, [])
        assert r.get("error") == "no_skill_ids_provided"


# ─────────────────────────────────────────────────────────────────────────────
# OP5 — q_get_role_profile
# ─────────────────────────────────────────────────────────────────────────────

class TestOP5GetRoleProfile:

    REQUIRED_KEYS = {"role_id", "role_name", "domains", "required_skills", "total_required_skills"}

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert isinstance(result["required_skills"], list)
        assert result["total_required_skills"] == len(result["required_skills"])
        for sk in result["required_skills"]:
            assert "skill_id" in sk
            assert "name" in sk
            assert "category" in sk
            assert "tier" in sk
            assert "weight" in sk
            assert sk["tier"] in ("core", "supporting", "optional")
            assert 0.0 <= sk["weight"] <= 1.0

    def test_data_scientist_profile(self, client):
        r = q_get_role_profile(client, "RL_Data_Scientist")
        self._assert_contract(r)
        assert r["role_id"] == "RL_Data_Scientist"
        assert r["total_required_skills"] > 0

    def test_mlops_engineer_profile(self, client):
        r = q_get_role_profile(client, "RL_MLOps_Engineer")
        self._assert_contract(r)
        assert r["total_required_skills"] > 0

    def test_cloud_engineer_profile(self, client):
        r = q_get_role_profile(client, "RL_Cloud_Engineer")
        self._assert_contract(r)
        assert r["total_required_skills"] > 0

    def test_qa_engineer_profile(self, client):
        r = q_get_role_profile(client, "RL_QA_Engineer")
        self._assert_contract(r)
        assert r["total_required_skills"] > 0

    def test_removed_it_manager_returns_not_found(self, client):
        r = q_get_role_profile(client, "RL_IT_Manager")
        assert r.get("error") == "role_not_found"
        assert r.get("submitted_role_id") == "RL_IT_Manager"

    def test_fake_role_returns_not_found(self, client):
        r = q_get_role_profile(client, "RL_FAKE_ROLE")
        assert r.get("error") == "role_not_found"

    def test_empty_role_returns_error(self, client):
        r = q_get_role_profile(client, "")
        assert r.get("error") == "no_role_provided"


# ─────────────────────────────────────────────────────────────────────────────
# OP6 — q_get_roles_by_track
# ─────────────────────────────────────────────────────────────────────────────

class TestOP6GetRolesByTrack:

    REQUIRED_KEYS = {"track", "track_id", "results", "total_results"}

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert isinstance(result["results"], list)
        assert result["total_results"] == len(result["results"])
        for role in result["results"]:
            assert "role_id" in role
            assert "role_name" in role

    def test_ai_track_has_roles(self, client):
        r = q_get_roles_by_track(client, "AI")
        self._assert_contract(r)
        assert r["total_results"] > 0

    def test_swe_track_has_roles(self, client):
        r = q_get_roles_by_track(client, "SWE")
        self._assert_contract(r)
        assert r["total_results"] > 0

    def test_dse_track_has_roles(self, client):
        r = q_get_roles_by_track(client, "DSE")
        self._assert_contract(r)
        assert r["total_results"] > 0

    def test_cys_track_has_roles(self, client):
        r = q_get_roles_by_track(client, "CYS")
        self._assert_contract(r)
        assert r["total_results"] > 0

    def test_gen_track(self, client):
        r = q_get_roles_by_track(client, "GEN")
        self._assert_contract(r)

    def test_fake_track_returns_not_found(self, client):
        r = q_get_roles_by_track(client, "FAKETRACK")
        assert r.get("error") == "track_not_found"

    def test_empty_track_returns_error(self, client):
        r = q_get_roles_by_track(client, "")
        assert r.get("error") == "no_track_provided"


# ─────────────────────────────────────────────────────────────────────────────
# OP7 — q_compute_skill_gap
# ─────────────────────────────────────────────────────────────────────────────

class TestOP7ComputeSkillGap:

    REQUIRED_KEYS = {
        "role_id", "role_name", "covered_skills", "missing_skills",
        "total_covered", "total_missing", "total_required",
    }

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert result["total_required"] == result["total_covered"] + result["total_missing"]
        for sk in result["covered_skills"]:
            assert "skill_id" in sk
            assert "covered_by" in sk
            assert isinstance(sk["covered_by"], list)
            assert len(sk["covered_by"]) > 0
        for sk in result["missing_skills"]:
            assert "skill_id" in sk
            assert "name" in sk
            assert "tier" in sk
            assert "weight" in sk

    def test_data_scientist_cs_progress(self, client):
        r = q_compute_skill_gap(client, "RL_Data_Scientist", CS_PROGRESS_STUDENT)
        self._assert_contract(r)
        assert r["total_required"] > 0
        assert r["total_covered"] >= 0

    def test_mlops_ai_progress(self, client):
        r = q_compute_skill_gap(client, "RL_MLOps_Engineer", AI_PROGRESS_STUDENT)
        self._assert_contract(r)

    def test_qa_engineer_swe_progress(self, client):
        r = q_compute_skill_gap(client, "RL_QA_Engineer", SWE_PROGRESS_STUDENT)
        self._assert_contract(r)

    def test_fake_role_returns_not_found(self, client):
        r = q_compute_skill_gap(client, "RL_FAKE", CS_PROGRESS_STUDENT)
        assert r.get("error") == "role_not_found"

    def test_empty_completed_courses_returns_error(self, client):
        r = q_compute_skill_gap(client, "RL_Data_Scientist", [])
        assert r.get("error") == "no_courses_provided"

    def test_only_fake_courses_returns_no_valid(self, client):
        r = q_compute_skill_gap(client, "RL_Data_Scientist", ["C-FAKE001", "C-FAKE002"])
        assert r.get("error") == "no_valid_courses_provided"
        assert "unrecognized_courses" in r

    def test_mixed_valid_and_fake_courses_includes_unrecognized(self, client):
        courses = CS_PROGRESS_STUDENT + ["C-FAKE001"]
        r = q_compute_skill_gap(client, "RL_Data_Scientist", courses)
        self._assert_contract(r)
        assert "unrecognized_courses" in r
        assert "C-FAKE001" in r["unrecognized_courses"]


# ─────────────────────────────────────────────────────────────────────────────
# OP8 — q_compute_alignment_score
# ─────────────────────────────────────────────────────────────────────────────

class TestOP8ComputeAlignmentScore:

    REQUIRED_KEYS = {
        "role_id", "role_name", "alignment_score", "alignment_percentage",
        "covered_weight", "total_weight",
    }

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert 0.0 <= result["alignment_score"] <= 1.0
        assert 0.0 <= result["alignment_percentage"] <= 100.0
        assert result["covered_weight"] <= result["total_weight"]

    def test_data_scientist_cs_progress(self, client):
        r = q_compute_alignment_score(client, "RL_Data_Scientist", CS_PROGRESS_STUDENT)
        self._assert_contract(r)

    def test_mlops_ai_progress(self, client):
        r = q_compute_alignment_score(client, "RL_MLOps_Engineer", AI_PROGRESS_STUDENT)
        self._assert_contract(r)

    def test_qa_engineer_swe_progress(self, client):
        r = q_compute_alignment_score(client, "RL_QA_Engineer", SWE_PROGRESS_STUDENT)
        self._assert_contract(r)

    def test_fake_role_returns_not_found(self, client):
        r = q_compute_alignment_score(client, "RL_FAKE", CS_PROGRESS_STUDENT)
        assert r.get("error") == "role_not_found"

    def test_empty_courses_returns_error(self, client):
        r = q_compute_alignment_score(client, "RL_Data_Scientist", [])
        assert r.get("error") == "no_courses_provided"

    def test_only_fake_courses_returns_no_valid(self, client):
        r = q_compute_alignment_score(client, "RL_Data_Scientist", ["C-FAKE001"])
        assert r.get("error") == "no_valid_courses_provided"

    def test_mixed_valid_and_fake_includes_unrecognized(self, client):
        courses = AI_PROGRESS_STUDENT + ["C-FAKE001"]
        r = q_compute_alignment_score(client, "RL_MLOps_Engineer", courses)
        self._assert_contract(r)
        assert "unrecognized_courses" in r


# ─────────────────────────────────────────────────────────────────────────────
# OP9 — q_recommend_courses_to_close_gap
# ─────────────────────────────────────────────────────────────────────────────

class TestOP9RecommendCoursesToCloseGap:

    REQUIRED_KEYS = {
        "role_id", "role_name", "missing_skills",
        "total_missing_skills", "total_recommended_courses",
    }

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        for sk in result["missing_skills"]:
            assert "skill_id" in sk
            assert "name" in sk
            assert "tier" in sk
            assert "weight" in sk
            assert "taught_by" in sk
            for course in sk["taught_by"]:
                assert "course_code" in course
                assert "name" in course

    def test_data_scientist_cs_progress(self, client):
        r = q_recommend_courses_to_close_gap(client, "RL_Data_Scientist", CS_PROGRESS_STUDENT)
        self._assert_contract(r)
        assert r["role_id"] == "RL_Data_Scientist"

    def test_mlops_ai_progress(self, client):
        r = q_recommend_courses_to_close_gap(client, "RL_MLOps_Engineer", AI_PROGRESS_STUDENT)
        self._assert_contract(r)

    def test_qa_engineer_swe_progress(self, client):
        r = q_recommend_courses_to_close_gap(client, "RL_QA_Engineer", SWE_PROGRESS_STUDENT)
        self._assert_contract(r)

    def test_recommendations_exclude_completed_courses(self, client):
        r = q_recommend_courses_to_close_gap(client, "RL_Data_Scientist", CS_PROGRESS_STUDENT)
        self._assert_contract(r)
        completed_set = set(CS_PROGRESS_STUDENT)
        for sk in r["missing_skills"]:
            for course in sk["taught_by"]:
                assert course["course_code"] not in completed_set, (
                    f"Completed course {course['course_code']} appeared in recommendations"
                )

    def test_fake_role_returns_not_found(self, client):
        r = q_recommend_courses_to_close_gap(client, "RL_FAKE", CS_PROGRESS_STUDENT)
        assert r.get("error") == "role_not_found"

    def test_empty_courses_returns_error(self, client):
        r = q_recommend_courses_to_close_gap(client, "RL_Data_Scientist", [])
        assert r.get("error") == "no_courses_provided"

    def test_only_fake_courses_returns_no_valid(self, client):
        r = q_recommend_courses_to_close_gap(client, "RL_Data_Scientist", ["C-FAKE001"])
        assert r.get("error") == "no_valid_courses_provided"

    def test_mixed_valid_and_fake_includes_unrecognized(self, client):
        courses = CS_PROGRESS_STUDENT + ["C-FAKE001"]
        r = q_recommend_courses_to_close_gap(client, "RL_Data_Scientist", courses)
        self._assert_contract(r)
        assert "unrecognized_courses" in r


# ─────────────────────────────────────────────────────────────────────────────
# OP10 — q_estimate_alignment_improvement
# ─────────────────────────────────────────────────────────────────────────────

class TestOP10EstimateAlignmentImprovement:

    REQUIRED_KEYS = {
        "role_id", "role_name",
        "current_alignment_score", "current_alignment_percentage",
        "projected_alignment_score", "projected_alignment_percentage",
        "alignment_improvement",
        "newly_covered_skills", "still_missing_skills",
        "total_newly_covered", "total_still_missing",
    }

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert 0.0 <= result["current_alignment_score"] <= 1.0
        assert 0.0 <= result["projected_alignment_score"] <= 1.0
        assert result["projected_alignment_score"] >= result["current_alignment_score"] - 1e-9

    def test_data_scientist_cs_progress_with_planned(self, client):
        r = q_estimate_alignment_improvement(
            client,
            "RL_Data_Scientist",
            completed_courses=CS_PROGRESS_STUDENT,
            planned_courses=["C-AI321", "C-DE312"],
        )
        self._assert_contract(r)
        assert r["role_id"] == "RL_Data_Scientist"

    def test_mlops_ai_progress_with_planned(self, client):
        r = q_estimate_alignment_improvement(
            client,
            "RL_MLOps_Engineer",
            completed_courses=AI_PROGRESS_STUDENT,
            planned_courses=["C-DE324", "C-SW422"],
        )
        self._assert_contract(r)

    def test_qa_with_planned_course(self, client):
        r = q_estimate_alignment_improvement(
            client,
            "RL_QA_Engineer",
            completed_courses=["C-CS111", "C-CS112"],
            planned_courses=["C-SW421"],
        )
        self._assert_contract(r)

    def test_empty_planned_courses_returns_error(self, client):
        r = q_estimate_alignment_improvement(
            client,
            "RL_Data_Scientist",
            completed_courses=CS_PROGRESS_STUDENT,
            planned_courses=[],
        )
        assert r.get("error") == "no_planned_courses_provided"

    def test_fake_planned_only_returns_no_valid_planned(self, client):
        r = q_estimate_alignment_improvement(
            client,
            "RL_Data_Scientist",
            completed_courses=CS_PROGRESS_STUDENT,
            planned_courses=["C-FAKE999"],
        )
        assert r.get("error") == "no_valid_planned_courses_provided"

    def test_mixed_valid_and_fake_planned(self, client):
        r = q_estimate_alignment_improvement(
            client,
            "RL_Data_Scientist",
            completed_courses=CS_PROGRESS_STUDENT,
            planned_courses=["C-AI321", "C-FAKE999"],
        )
        self._assert_contract(r)
        assert "unrecognized_planned_courses" in r
        assert "C-FAKE999" in r["unrecognized_planned_courses"]

    def test_fake_role_returns_not_found(self, client):
        r = q_estimate_alignment_improvement(
            client,
            "RL_FAKE",
            completed_courses=CS_PROGRESS_STUDENT,
            planned_courses=["C-AI321"],
        )
        assert r.get("error") == "role_not_found"


# ─────────────────────────────────────────────────────────────────────────────
# OP11 — q_find_best_matching_roles
# ─────────────────────────────────────────────────────────────────────────────

class TestOP11FindBestMatchingRoles:

    REQUIRED_KEYS = {"completed_courses", "ranked_roles", "total_roles_evaluated"}

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert isinstance(result["ranked_roles"], list)
        assert result["total_roles_evaluated"] > 0
        scores = [r["alignment_score"] for r in result["ranked_roles"]]
        assert scores == sorted(scores, reverse=True), "Roles must be sorted descending by score"
        for role in result["ranked_roles"]:
            assert "role_id" in role
            assert "role_name" in role
            assert role["alignment_score"] > 0.0

    def test_foundation_student_returns_some_roles(self, client):
        r = q_find_best_matching_roles(client, FOUNDATION_STUDENT)
        self._assert_contract(r)

    def test_cs_progress_student(self, client):
        r = q_find_best_matching_roles(client, CS_PROGRESS_STUDENT)
        self._assert_contract(r)
        assert len(r["ranked_roles"]) > 0

    def test_ai_progress_student(self, client):
        r = q_find_best_matching_roles(client, AI_PROGRESS_STUDENT)
        self._assert_contract(r)

    def test_swe_progress_student(self, client):
        r = q_find_best_matching_roles(client, SWE_PROGRESS_STUDENT)
        self._assert_contract(r)

    def test_mixed_valid_and_fake_courses(self, client):
        courses = CS_PROGRESS_STUDENT + ["C-FAKE001"]
        r = q_find_best_matching_roles(client, courses)
        self._assert_contract(r)
        assert "unrecognized_courses" in r

    def test_empty_courses_returns_error(self, client):
        r = q_find_best_matching_roles(client, [])
        assert r.get("error") == "no_courses_provided"

    def test_only_fake_courses_returns_no_valid(self, client):
        r = q_find_best_matching_roles(client, ["C-FAKE001", "C-FAKE002"])
        assert r.get("error") == "no_valid_courses_provided"


# ─────────────────────────────────────────────────────────────────────────────
# OP12 — q_get_track_overview
# ─────────────────────────────────────────────────────────────────────────────

class TestOP12GetTrackOverview:

    REQUIRED_KEYS = {
        "track_id", "track_name", "courses", "total_courses",
        "skills_taught", "total_skills", "supported_roles", "total_supported_roles",
    }

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert isinstance(result["courses"], list)
        assert isinstance(result["skills_taught"], list)
        assert isinstance(result["supported_roles"], list)
        assert result["total_courses"] == len(result["courses"])
        assert result["total_skills"] == len(result["skills_taught"])
        assert result["total_supported_roles"] == len(result["supported_roles"])
        for course in result["courses"]:
            assert "course_code" in course
            assert "name" in course
        for role in result["supported_roles"]:
            assert "role_id" in role
            assert "role_name" in role

    def test_ai_track(self, client):
        r = q_get_track_overview(client, "AI")
        self._assert_contract(r)
        assert r["total_courses"] > 0
        assert r["total_skills"] > 0

    def test_swe_track(self, client):
        r = q_get_track_overview(client, "SWE")
        self._assert_contract(r)
        assert r["total_courses"] > 0

    def test_dse_track(self, client):
        r = q_get_track_overview(client, "DSE")
        self._assert_contract(r)

    def test_cys_track(self, client):
        r = q_get_track_overview(client, "CYS")
        self._assert_contract(r)

    def test_gen_track(self, client):
        r = q_get_track_overview(client, "GEN")
        self._assert_contract(r)

    def test_fake_track_returns_not_found(self, client):
        r = q_get_track_overview(client, "FAKETRACK")
        assert r.get("error") == "track_not_found"

    def test_empty_track_returns_error(self, client):
        r = q_get_track_overview(client, "")
        assert r.get("error") == "no_track_provided"


# ─────────────────────────────────────────────────────────────────────────────
# OP13 — q_compare_tracks
# ─────────────────────────────────────────────────────────────────────────────

class TestOP13CompareTracks:

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert "track_1" in result
        assert "track_2" in result
        assert "courses" in result
        assert "skills" in result
        assert "role_alignment" in result
        for section in ("courses", "skills", "role_alignment"):
            s = result[section]
            assert "track_1_only" in s
            assert "track_2_only" in s
            assert "shared" in s
            assert "total_track_1_only" in s
            assert "total_track_2_only" in s
            assert "total_shared" in s

    def test_ai_vs_dse(self, client):
        r = q_compare_tracks(client, "AI", "DSE")
        self._assert_contract(r)
        assert r["track_1"]["track_id"] == "AI"
        assert r["track_2"]["track_id"] == "DSE"

    def test_swe_vs_cys(self, client):
        r = q_compare_tracks(client, "SWE", "CYS")
        self._assert_contract(r)

    def test_identical_tracks_returns_error(self, client):
        r = q_compare_tracks(client, "AI", "AI")
        assert r.get("error") == "identical_tracks_provided"

    def test_fake_track_returns_not_found(self, client):
        r = q_compare_tracks(client, "AI", "FAKETRACK")
        assert r.get("error") == "track_not_found"
        assert "FAKETRACK" in r.get("not_found_ids", [])

    def test_missing_track_id_returns_error(self, client):
        r = q_compare_tracks(client, "AI", "")
        assert r.get("error") == "missing_track_ids"


# ─────────────────────────────────────────────────────────────────────────────
# OP14 — q_recommend_track_for_role
# ─────────────────────────────────────────────────────────────────────────────

class TestOP14RecommendTrackForRole:

    REQUIRED_KEYS = {"role_id", "role_name", "ranked_tracks", "total_tracks_evaluated"}

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert isinstance(result["ranked_tracks"], list)
        scores = [t["alignment_score"] for t in result["ranked_tracks"]]
        assert scores == sorted(scores, reverse=True), "Tracks must be sorted descending"
        for track in result["ranked_tracks"]:
            assert "track_id" in track
            assert "track_name" in track
            assert "alignment_score" in track
            assert "rank" in track

    def test_data_scientist_recommends_tracks(self, client):
        r = q_recommend_track_for_role(client, "RL_Data_Scientist")
        self._assert_contract(r)
        assert len(r["ranked_tracks"]) > 0

    def test_mlops_engineer(self, client):
        r = q_recommend_track_for_role(client, "RL_MLOps_Engineer")
        self._assert_contract(r)

    def test_cloud_engineer(self, client):
        r = q_recommend_track_for_role(client, "RL_Cloud_Engineer")
        self._assert_contract(r)

    def test_qa_engineer(self, client):
        r = q_recommend_track_for_role(client, "RL_QA_Engineer")
        self._assert_contract(r)

    def test_cybersecurity_analyst(self, client):
        r = q_recommend_track_for_role(client, "RL_Cybersecurity_Analyst")
        self._assert_contract(r)

    def test_removed_it_manager_returns_not_found(self, client):
        r = q_recommend_track_for_role(client, "RL_IT_Manager")
        assert r.get("error") == "role_not_found"

    def test_fake_role_returns_not_found(self, client):
        r = q_recommend_track_for_role(client, "RL_FAKE")
        assert r.get("error") == "role_not_found"

    def test_empty_role_returns_error(self, client):
        r = q_recommend_track_for_role(client, "")
        assert r.get("error") == "no_role_provided"


# ─────────────────────────────────────────────────────────────────────────────
# OP15 — q_recommend_track_for_skill
# ─────────────────────────────────────────────────────────────────────────────

class TestOP15RecommendTrackForSkill:

    REQUIRED_KEYS = {"skill_id", "skill_name", "ranked_tracks", "total_tracks_evaluated"}

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert isinstance(result["ranked_tracks"], list)
        counts = [t["course_count"] for t in result["ranked_tracks"]]
        assert counts == sorted(counts, reverse=True) or len(counts) <= 1
        for track in result["ranked_tracks"]:
            assert "track_id" in track
            assert "track_name" in track
            assert "courses_teaching_skill" in track
            assert "course_count" in track
            assert "rank" in track

    def test_machine_learning_sk_ml(self, client):
        r = q_recommend_track_for_skill(client, "SK_ML")
        self._assert_contract(r)
        assert r["skill_id"] == "SK_ML"
        assert len(r["ranked_tracks"]) > 0

    def test_computer_vision(self, client):
        r = q_recommend_track_for_skill(client, "SK_Computer_Vision")
        self._assert_contract(r)

    def test_sql(self, client):
        r = q_recommend_track_for_skill(client, "SK_SQL")
        self._assert_contract(r)

    def test_software_testing(self, client):
        r = q_recommend_track_for_skill(client, "SK_Software_Testing")
        self._assert_contract(r)

    def test_cloud_computing(self, client):
        r = q_recommend_track_for_skill(client, "SK_Cloud_Computing")
        self._assert_contract(r)

    def test_cpp(self, client):
        r = q_recommend_track_for_skill(client, "SK_CPP")
        self._assert_contract(r)
        assert len(r["ranked_tracks"]) > 0

    def test_removed_reinforcement_learning_returns_not_found(self, client):
        r = q_recommend_track_for_skill(client, "SK_Reinforcement_Learning")
        assert r.get("error") == "skill_not_found"

    def test_fake_skill_returns_not_found(self, client):
        r = q_recommend_track_for_skill(client, "SK_FAKE_SKILL")
        assert r.get("error") == "skill_not_found"

    def test_empty_skill_returns_error(self, client):
        r = q_recommend_track_for_skill(client, "")
        assert r.get("error") == "no_skill_provided"


# ─────────────────────────────────────────────────────────────────────────────
# OP16 — q_get_courses_by_track
# ─────────────────────────────────────────────────────────────────────────────

class TestOP16GetCoursesByTrack:

    REQUIRED_KEYS = {"track_id", "track_name", "courses", "total_courses"}

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert result["total_courses"] == len(result["courses"])
        for course in result["courses"]:
            assert "course_code" in course
            assert "name" in course
            assert "credits" in course
            assert "level" in course
            assert "prerequisites" in course
            assert "semester_offering" in course
            assert "credit_threshold" in course
            assert isinstance(course["prerequisites"], list)
            assert isinstance(course["semester_offering"], list)

    def test_ai_track_courses(self, client):
        r = q_get_courses_by_track(client, "AI")
        self._assert_contract(r)
        assert r["total_courses"] > 0

    def test_swe_track_courses(self, client):
        r = q_get_courses_by_track(client, "SWE")
        self._assert_contract(r)
        assert r["total_courses"] > 0

    def test_dse_track_courses(self, client):
        r = q_get_courses_by_track(client, "DSE")
        self._assert_contract(r)

    def test_cys_track_courses(self, client):
        r = q_get_courses_by_track(client, "CYS")
        self._assert_contract(r)

    def test_gen_track_courses(self, client):
        r = q_get_courses_by_track(client, "GEN")
        self._assert_contract(r)

    def test_no_student_eligibility_filtering(self, client):
        r = q_get_courses_by_track(client, "AI")
        self._assert_contract(r)
        codes = {c["course_code"] for c in r["courses"]}
        assert len(codes) == r["total_courses"], "Each course should appear exactly once"

    def test_fake_track_returns_not_found(self, client):
        r = q_get_courses_by_track(client, "FAKETRACK")
        assert r.get("error") == "track_not_found"

    def test_empty_track_returns_error(self, client):
        r = q_get_courses_by_track(client, "")
        assert r.get("error") == "no_track_provided"


# ─────────────────────────────────────────────────────────────────────────────
# OP17 — q_get_focus_courses_for_target
# ─────────────────────────────────────────────────────────────────────────────

class TestOP17GetFocusCoursesForTarget:

    REQUIRED_KEYS = {
        "target_id", "target_type", "target_name",
        "total_target_skills", "focus_courses", "total_focus_courses",
    }

    def _assert_contract(self, result):
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing key: {key!r}"
        assert result["total_focus_courses"] == len(result["focus_courses"])
        for course in result["focus_courses"]:
            assert "course_code" in course
            assert "name" in course
            assert "relevant_skills" in course
            assert "relevant_skill_count" in course
            assert course["relevant_skill_count"] == len(course["relevant_skills"])

    def test_role_target_data_scientist_cs_progress(self, client):
        r = q_get_focus_courses_for_target(
            client, "RL_Data_Scientist", "role", CS_PROGRESS_STUDENT
        )
        self._assert_contract(r)
        assert r["target_type"] == "role"

    def test_role_target_mlops_ai_progress(self, client):
        r = q_get_focus_courses_for_target(
            client, "RL_MLOps_Engineer", "role", AI_PROGRESS_STUDENT
        )
        self._assert_contract(r)

    def test_track_target_ai_foundation_student(self, client):
        r = q_get_focus_courses_for_target(
            client, "AI", "track", FOUNDATION_STUDENT
        )
        self._assert_contract(r)
        assert r["target_type"] == "track"
        assert r["total_focus_courses"] > 0

    def test_track_target_dse_cs_progress(self, client):
        r = q_get_focus_courses_for_target(
            client, "DSE", "track", CS_PROGRESS_STUDENT
        )
        self._assert_contract(r)

    def test_focus_courses_exclude_completed(self, client):
        r = q_get_focus_courses_for_target(
            client, "RL_Data_Scientist", "role", CS_PROGRESS_STUDENT
        )
        self._assert_contract(r)
        completed_set = set(CS_PROGRESS_STUDENT)
        for course in r["focus_courses"]:
            assert course["course_code"] not in completed_set, (
                f"Completed course {course['course_code']} should not appear in focus list"
            )

    def test_empty_completed_courses_valid_freshman(self, client):
        r = q_get_focus_courses_for_target(
            client, "AI", "track", []
        )
        self._assert_contract(r)
        assert r["total_focus_courses"] > 0

    def test_fake_role_target_returns_not_found(self, client):
        r = q_get_focus_courses_for_target(
            client, "RL_FAKE_ROLE", "role", CS_PROGRESS_STUDENT
        )
        assert r.get("error") == "role_not_found"

    def test_fake_track_target_returns_not_found(self, client):
        r = q_get_focus_courses_for_target(
            client, "FAKETRACK", "track", FOUNDATION_STUDENT
        )
        assert r.get("error") == "track_not_found"

    def test_invalid_target_type_returns_error(self, client):
        r = q_get_focus_courses_for_target(
            client, "AI", "department", FOUNDATION_STUDENT
        )
        assert r.get("error") == "invalid_target_type"

    def test_removed_role_it_manager_returns_not_found(self, client):
        r = q_get_focus_courses_for_target(
            client, "RL_IT_Manager", "role", CS_PROGRESS_STUDENT
        )
        assert r.get("error") == "role_not_found"


# ─────────────────────────────────────────────────────────────────────────────
# OP18 — q_resolve_entity
# ─────────────────────────────────────────────────────────────────────────────

class TestOP18ResolveEntity:

    def _assert_ok(self, result, expected_id=None):
        assert result.get("status") == "ok", (
            f"Expected status=ok, got {result.get('status')!r} — {result}"
        )
        assert result.get("resolved_id"), "resolved_id must be non-empty"
        assert result.get("name"), "name must be non-empty"
        assert result.get("match_type") in (
            "exact_id", "exact_name", "alias", "partial_name"
        )
        assert "confidence" in result
        if expected_id:
            assert result["resolved_id"] == expected_id, (
                f"Expected {expected_id!r} but got {result['resolved_id']!r}"
            )

    def _assert_ambiguous(self, result):
        assert result.get("status") == "ambiguous", (
            f"Expected status=ambiguous, got {result.get('status')!r}"
        )
        assert len(result.get("matches", [])) >= 2

    def _assert_not_found(self, result):
        assert result.get("status") == "not_found", (
            f"Expected status=not_found, got {result.get('status')!r}"
        )
        assert result.get("matches") == []

    def _assert_error(self, result, error_code=None):
        assert result.get("status") == "error", (
            f"Expected status=error, got {result.get('status')!r}"
        )
        if error_code:
            assert result.get("error") == error_code

    # ── Course: exact ID ────────────────────────────────────────

    def test_course_exact_id_cs219(self, client):
        r = q_resolve_entity(client, "course", "C-CS219")
        self._assert_ok(r, "C-CS219")
        assert r["match_type"] == "exact_id"

    def test_course_exact_id_ai321(self, client):
        r = q_resolve_entity(client, "course", "C-AI321")
        self._assert_ok(r, "C-AI321")

    # ── Course: alias ────────────────────────────────────────────

    def test_course_alias_advanced_programming(self, client):
        # "Advanced Programming" is the exact course name, so may resolve via
        # exact_name before the alias check — both are correct match paths.
        r = q_resolve_entity(client, "course", "advanced programming")
        self._assert_ok(r, "C-CS219")

    def test_course_alias_database_systems_intro(self, client):
        r = q_resolve_entity(client, "course", "intro to database systems")
        self._assert_ok(r, "C-DE312")

    # ── Course: ambiguous ────────────────────────────────────────

    def test_course_ambiguous_ai(self, client):
        r = q_resolve_entity(client, "course", "ai")
        self._assert_ambiguous(r)

    def test_course_ambiguous_database(self, client):
        r = q_resolve_entity(client, "course", "database")
        self._assert_ambiguous(r)

    def test_course_ambiguous_machine_learning(self, client):
        r = q_resolve_entity(client, "course", "machine learning")
        self._assert_ambiguous(r)

    # ── Course: not found / invalid ──────────────────────────────

    def test_course_fake_returns_not_found(self, client):
        r = q_resolve_entity(client, "course", "zxqvmbnoplq999")
        assert r.get("status") in ("not_found",)

    def test_course_empty_text_returns_error(self, client):
        r = q_resolve_entity(client, "course", "")
        self._assert_error(r, "empty_entity_text")

    # ── Role: exact ID ───────────────────────────────────────────

    def test_role_exact_id_data_scientist(self, client):
        r = q_resolve_entity(client, "role", "RL_Data_Scientist")
        self._assert_ok(r, "RL_Data_Scientist")
        assert r["match_type"] == "exact_id"

    def test_role_exact_id_mlops_engineer(self, client):
        r = q_resolve_entity(client, "role", "RL_MLOps_Engineer")
        self._assert_ok(r, "RL_MLOps_Engineer")

    # ── Role: alias ──────────────────────────────────────────────

    def test_role_alias_data_scientist(self, client):
        # "Data Scientist" is the exact role name; resolves via exact_name, which
        # is higher-priority than alias — both are correct resolution paths.
        r = q_resolve_entity(client, "role", "data scientist")
        self._assert_ok(r, "RL_Data_Scientist")

    def test_role_alias_mlops_engineer(self, client):
        r = q_resolve_entity(client, "role", "mlops engineer")
        self._assert_ok(r, "RL_MLOps_Engineer")

    def test_role_alias_cloud_engineer(self, client):
        r = q_resolve_entity(client, "role", "cloud engineer")
        self._assert_ok(r, "RL_Cloud_Engineer")

    def test_role_alias_qa_engineer(self, client):
        r = q_resolve_entity(client, "role", "qa engineer")
        self._assert_ok(r, "RL_QA_Engineer")

    def test_role_alias_mlops_variant(self, client):
        r = q_resolve_entity(client, "role", "mlops")
        self._assert_ok(r, "RL_MLOps_Engineer")

    # ── Role: removed entity ─────────────────────────────────────

    def test_role_removed_it_manager_not_found(self, client):
        r = q_resolve_entity(client, "role", "it manager")
        assert r.get("status") in ("not_found",), (
            f"Removed role 'it manager' should not resolve; got {r.get('status')!r}"
        )

    def test_role_fake_returns_not_found(self, client):
        r = q_resolve_entity(client, "role", "senior quantum blockchain architect")
        assert r.get("status") in ("not_found",)

    def test_role_empty_text_returns_error(self, client):
        r = q_resolve_entity(client, "role", "")
        self._assert_error(r, "empty_entity_text")

    # ── Track: exact ID ─────────────────────────────────────────

    def test_track_exact_id_ai(self, client):
        r = q_resolve_entity(client, "track", "AI")
        self._assert_ok(r, "AI")
        assert r["match_type"] == "exact_id"

    # ── Track: alias ────────────────────────────────────────────

    def test_track_alias_artificial_intelligence(self, client):
        # "Artificial Intelligence" is the track's exact name; resolves via
        # exact_name before alias check — both paths yield the same result.
        r = q_resolve_entity(client, "track", "artificial intelligence")
        self._assert_ok(r, "AI")

    def test_track_alias_software_engineering(self, client):
        r = q_resolve_entity(client, "track", "software engineering")
        self._assert_ok(r, "SWE")

    def test_track_alias_cyber(self, client):
        r = q_resolve_entity(client, "track", "cyber")
        self._assert_ok(r, "CYS")

    def test_track_alias_data_science(self, client):
        r = q_resolve_entity(client, "track", "data science")
        self._assert_ok(r, "DSE")

    def test_track_fake_returns_not_found(self, client):
        r = q_resolve_entity(client, "track", "quantum computing track")
        assert r.get("status") in ("not_found",)

    def test_track_empty_text_returns_error(self, client):
        r = q_resolve_entity(client, "track", "")
        self._assert_error(r, "empty_entity_text")

    # ── Skill: exact ID ─────────────────────────────────────────

    def test_skill_exact_id_sk_ml(self, client):
        r = q_resolve_entity(client, "skill", "SK_ML")
        self._assert_ok(r, "SK_ML")
        assert r["match_type"] == "exact_id"

    # ── Skill: alias ────────────────────────────────────────────

    def test_skill_alias_oop(self, client):
        r = q_resolve_entity(client, "skill", "oop")
        self._assert_ok(r, "SK_OOP")
        assert r["match_type"] == "alias"

    def test_skill_alias_machine_learning(self, client):
        r = q_resolve_entity(client, "skill", "machine learning")
        self._assert_ok(r, "SK_ML")

    def test_skill_alias_nlp(self, client):
        r = q_resolve_entity(client, "skill", "nlp")
        self._assert_ok(r, "SK_NLP")

    def test_skill_alias_networking(self, client):
        r = q_resolve_entity(client, "skill", "networking")
        self._assert_ok(r, "SK_Computer_Networks")

    # ── Skill: removed entity ───────────────────────────────────

    def test_skill_removed_reinforcement_learning_not_found(self, client):
        r = q_resolve_entity(client, "skill", "reinforcement learning")
        assert r.get("status") in ("not_found",), (
            f"Removed skill should not resolve; got {r.get('status')!r}"
        )

    def test_skill_fake_returns_not_found(self, client):
        r = q_resolve_entity(client, "skill", "quantum neural embeddings")
        assert r.get("status") in ("not_found",)

    def test_skill_empty_text_returns_error(self, client):
        r = q_resolve_entity(client, "skill", "")
        self._assert_error(r, "empty_entity_text")

    # ── Unsupported entity type ──────────────────────────────────

    def test_unsupported_entity_type_returns_error(self, client):
        r = q_resolve_entity(client, "department", "Computer Science")
        self._assert_error(r, "unsupported_entity_type")
        assert "allowed_entity_types" in r
