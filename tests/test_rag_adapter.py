"""
Integration tests for RAGAdapter.
Tests the two modes the orchestrator depends on:
  Mode 1 — free_text: natural language handbook query → answer string
  Mode 2 — rule_bundle: structured rule fetch → typed rule dict

Requires: ChromaDB ingested (run ingest.py first), GROQ_API_KEY in .env
Run from project root: pytest tests/test_rag_adapter.py -v
"""

import pytest
from adapters.rag_adapter import RAGAdapter


@pytest.fixture(scope="module")
def adapter():
    return RAGAdapter()


@pytest.fixture(scope="module")
def rule_bundles(adapter):
    """Fetch all 8 rule bundles once per test session — each call hits RAG+Groq.

    get_rule_bundles() fires 11 structured LLM calls (3 bundles use two-query
    merge: graduation, academic_warning, credit_limit).  Groq's free tier
    enforces a per-minute request limit; if those calls arrive too fast the
    trailing ones return HTTP 429, the Pydantic conversion fails, and the method
    returns {}.  Tests that depend on this fixture skip automatically when that
    happens rather than producing misleading failures.
    """
    import time
    # Brief pause so the free-text tests above drain any rate-limit window.
    time.sleep(5)
    return adapter.get_rule_bundles()


def _require_bundles(rule_bundles: dict) -> None:
    """Skip the calling test if get_rule_bundles() returned empty (rate limit)."""
    if not rule_bundles:
        pytest.skip(
            "get_rule_bundles() returned {} — Groq rate limit (HTTP 429) hit "
            "during one or more structured calls.  Re-run after waiting ~60s."
        )


# ---------------------------------------------------------------------------
# Mode 1 — Free text query
# ---------------------------------------------------------------------------

def test_free_text_returns_string_answer(adapter):
    """Free text query must return a non-empty string answer."""
    result = adapter.execute(
        sub_query="What is the maximum number of credit hours a student can take per semester?",
    )
    assert result is not None, "RAG adapter must return a result"
    assert "error" not in result, f"RAG returned error: {result}"
    answer = result.get("answer")
    assert answer is not None, f"Result must contain an answer field, got keys: {list(result.keys())}"
    assert isinstance(answer, str), f"Answer must be a string, got {type(answer)}"
    assert len(answer.strip()) > 20, f"Answer is too short to be meaningful: {answer!r}"


def test_free_text_retake_policy(adapter):
    """Free text query about retake policy must return a non-empty answer."""
    result = adapter.execute(
        sub_query="What are the rules for retaking a failed course?",
    )
    assert result is not None
    assert "error" not in result, f"RAG returned error: {result}"
    answer = result.get("answer")
    assert answer is not None, f"Result must contain an answer field, got keys: {list(result.keys())}"
    assert len(answer.strip()) > 20, f"Answer is too short: {answer!r}"


def test_free_text_returns_extracted_facts(adapter):
    """execute() must return an extracted_facts list alongside the answer."""
    result = adapter.execute(
        sub_query="What is the minimum CGPA required to graduate?",
    )
    assert result is not None
    assert "extracted_facts" in result, f"Missing extracted_facts, got keys: {list(result.keys())}"
    assert isinstance(result["extracted_facts"], list), "extracted_facts must be a list"


def test_free_text_empty_query_returns_gracefully(adapter):
    """Empty query must not raise — must return a safe fallback dict."""
    result = adapter.execute(sub_query="")
    assert result is not None
    assert isinstance(result, dict)
    assert "answer" in result


# ---------------------------------------------------------------------------
# Mode 2 — Rule bundle fetch
# ---------------------------------------------------------------------------

def test_rule_bundles_returns_all_eight_keys(rule_bundles):
    """get_rule_bundles() must return all 8 ALE-required bundles."""
    _require_bundles(rule_bundles)
    expected_keys = {
        "grading_scale_rules",
        "graduation_requirement_rules",
        "academic_warning_rules",
        "honors_rules",
        "credit_limit_rules",
        "retake_rules",
        "summer_semester_rules",
        "student_level_rules",
    }
    assert rule_bundles, "get_rule_bundles() returned empty — RAG or Groq may be unavailable"
    missing = expected_keys - set(rule_bundles.keys())
    assert not missing, f"get_rule_bundles() is missing bundles: {missing}"


def test_rule_bundle_grading_scale_has_required_keys(rule_bundles):
    """Grading scale bundle must contain letter_to_points mapping.

    CIS Handbook Table 1: A+ = 4.0, A = 3.7 (not a 4.0-scale A).
    """
    _require_bundles(rule_bundles)
    assert "grading_scale_rules" in rule_bundles, "grading_scale_rules bundle missing"
    bundle = rule_bundles["grading_scale_rules"]
    ltp = bundle.letter_to_points
    assert "A+" in ltp, "letter_to_points must contain grade A+"
    assert "A" in ltp, "letter_to_points must contain grade A"
    assert "F" in ltp, "letter_to_points must contain grade F"
    assert ltp["A+"] == 4.0, f"Grade A+ must map to 4.0, got {ltp['A+']}"
    assert ltp["A"] == 3.7, f"Grade A must map to 3.7 per CIS Handbook, got {ltp['A']}"
    assert ltp["F"] == 0.0, f"Grade F must map to 0.0, got {ltp['F']}"
    assert ltp.get("Abs") == 0.0, (
        f"Grade Abs must map to 0.0 (same as F per CIS Handbook Table 1), got {ltp.get('Abs')}"
    )


def test_rule_bundle_retake_rules_has_required_keys(rule_bundles):
    """Retake rules bundle must contain the keys the ALE adapter expects."""
    _require_bundles(rule_bundles)
    assert "retake_rules" in rule_bundles, "retake_rules bundle missing"
    bundle = rule_bundles["retake_rules"]
    assert hasattr(bundle, "failed_first_retake_grade_cap"), "retake_rules missing failed_first_retake_grade_cap"
    assert hasattr(bundle, "improve_retake_max_courses_cgpa_above_2"), "retake_rules missing improve_retake_max_courses_cgpa_above_2"
    assert hasattr(bundle, "improve_retake_unlimited_below_cgpa"), "retake_rules missing improve_retake_unlimited_below_cgpa"


def test_rule_bundle_graduation_requirements_has_required_keys(rule_bundles):
    """Graduation requirement rules must contain credit hours and CGPA threshold."""
    _require_bundles(rule_bundles)
    assert "graduation_requirement_rules" in rule_bundles, "graduation_requirement_rules bundle missing"
    bundle = rule_bundles["graduation_requirement_rules"]
    assert hasattr(bundle, "total_credits_required"), "Missing total_credits_required"
    assert hasattr(bundle, "minimum_cgpa"), "Missing minimum_cgpa"
    assert bundle.total_credits_required == 133, (
        f"Expected 133 credit hours required, got {bundle.total_credits_required}"
    )
    assert bundle.minimum_cgpa == 2.0, (
        f"Expected minimum CGPA 2.0, got {bundle.minimum_cgpa}"
    )


def test_rule_bundle_credit_limit_rules_has_required_keys(rule_bundles):
    """Credit limit rules must contain CGPA bracket limits."""
    _require_bundles(rule_bundles)
    assert "credit_limit_rules" in rule_bundles, "credit_limit_rules bundle missing"
    bundle = rule_bundles["credit_limit_rules"]
    required_attrs = [
        "cgpa_above_3_limit",
        "cgpa_between_2_and_3_limit",
        "cgpa_between_1_and_2_limit",
        "cgpa_below_1_limit",
    ]
    for attr in required_attrs:
        assert hasattr(bundle, attr), f"credit_limit_rules missing required attribute: {attr}"


# ---------------------------------------------------------------------------
# All-bundles health check
# ---------------------------------------------------------------------------

def test_all_bundles_are_non_none(rule_bundles):
    """Every one of the 8 bundles must have passed Pydantic instantiation."""
    _require_bundles(rule_bundles)
    failed = [name for name, bundle in rule_bundles.items() if bundle is None]
    assert not failed, f"Bundles that failed Pydantic instantiation: {failed}"


# ---------------------------------------------------------------------------
# Grading scale — extended semantic checks
# ---------------------------------------------------------------------------

def test_rule_bundle_grading_scale_p_is_gpa_neutral(rule_bundles):
    """P grade must map to None (GPA-neutral per handbook)."""
    _require_bundles(rule_bundles)
    bundle = rule_bundles.get("grading_scale_rules")
    if bundle is None:
        pytest.skip("grading_scale_rules bundle failed instantiation")
    ltp = bundle.letter_to_points
    assert "P" in ltp, "letter_to_points must contain grade P"
    assert ltp["P"] is None, f"Grade P must be None (GPA-neutral), got {ltp['P']}"


def test_rule_bundle_grading_scale_percentage_ranges_are_floats(rule_bundles):
    """Percentage range boundaries must be floats to preserve decimal precision."""
    _require_bundles(rule_bundles)
    bundle = rule_bundles.get("grading_scale_rules")
    if bundle is None:
        pytest.skip("grading_scale_rules bundle failed instantiation")
    ranges = bundle.percentage_to_letter
    assert len(ranges) > 0, "percentage_to_letter must not be empty"
    for pr in ranges:
        assert isinstance(pr.min_pct, float), (
            f"min_pct must be float, got {type(pr.min_pct)} for letter {pr.letter}"
        )
        assert isinstance(pr.max_pct, float), (
            f"max_pct must be float, got {type(pr.max_pct)} for letter {pr.letter}"
        )


# ---------------------------------------------------------------------------
# Academic warning rules — P0 semantic checks
# ---------------------------------------------------------------------------

def test_rule_bundle_academic_warning_first_semester_exempt(rule_bundles):
    """First semester must be exempt from academic warning per handbook."""
    _require_bundles(rule_bundles)
    bundle = rule_bundles.get("academic_warning_rules")
    if bundle is None:
        pytest.skip("academic_warning_rules bundle failed instantiation")
    assert bundle.warning_exempt_first_semester is True, (
        f"Expected warning_exempt_first_semester=True, got {bundle.warning_exempt_first_semester}"
    )


def test_rule_bundle_academic_warning_dismissal_extension_is_decimal(rule_bundles):
    """Dismissal extension credits percentage must be decimal fraction (0 < x <= 1)."""
    _require_bundles(rule_bundles)
    bundle = rule_bundles.get("academic_warning_rules")
    if bundle is None:
        pytest.skip("academic_warning_rules bundle failed instantiation")
    pct = bundle.dismissal_extension_credits_percentage
    assert pct is not None, "dismissal_extension_credits_percentage must not be None"
    assert 0.0 < pct <= 1.0, (
        f"dismissal_extension_credits_percentage must be a decimal fraction (0 < x <= 1), got {pct}. "
        "Handbook states 80% → expected 0.80"
    )
    assert abs(pct - 0.80) < 0.05, (
        f"Handbook states 80% of credits → expected ~0.80, got {pct}"
    )


def test_rule_bundle_academic_warning_extra_summer_semesters(rule_bundles):
    """Dismissal appeal must grant exactly 1 extra summer semester per handbook."""
    _require_bundles(rule_bundles)
    bundle = rule_bundles.get("academic_warning_rules")
    if bundle is None:
        pytest.skip("academic_warning_rules bundle failed instantiation")
    assert bundle.dismissal_extension_extra_summer_semesters == 1, (
        f"Expected 1 extra summer semester on appeal, got "
        f"{bundle.dismissal_extension_extra_summer_semesters}"
    )


# ---------------------------------------------------------------------------
# Summer semester rules — P0 semantic checks
# ---------------------------------------------------------------------------

def test_rule_bundle_summer_semester_cgpa_threshold_extractable(rule_bundles):
    """Summer CGPA threshold for extra course must be non-None and equal to 3.0."""
    _require_bundles(rule_bundles)
    bundle = rule_bundles.get("summer_semester_rules")
    if bundle is None:
        pytest.skip("summer_semester_rules bundle failed instantiation")
    assert bundle.cgpa_threshold_for_extra_course is not None, (
        "cgpa_threshold_for_extra_course must not be None — required by ALE summer planning"
    )
    assert bundle.cgpa_threshold_for_extra_course == 3.0, (
        f"Expected CGPA threshold 3.0 for extra summer course, got "
        f"{bundle.cgpa_threshold_for_extra_course}"
    )


# ---------------------------------------------------------------------------
# Credit limit rules — P1 semantic checks
# ---------------------------------------------------------------------------

def test_rule_bundle_credit_limit_incomplete_extra_course_allowed(rule_bundles):
    """Incomplete extra course exception must be True per handbook."""
    _require_bundles(rule_bundles)
    bundle = rule_bundles.get("credit_limit_rules")
    if bundle is None:
        pytest.skip("credit_limit_rules bundle failed instantiation")
    assert bundle.incomplete_extra_course_allowed is True, (
        f"Expected incomplete_extra_course_allowed=True per handbook, "
        f"got {bundle.incomplete_extra_course_allowed}"
    )


# ---------------------------------------------------------------------------
# Honors rules — structural check
# ---------------------------------------------------------------------------

def test_rule_bundle_honors_rules_has_required_fields(rule_bundles):
    """Honors rules must contain minimum CGPA and semester count bounds."""
    _require_bundles(rule_bundles)
    bundle = rule_bundles.get("honors_rules")
    if bundle is None:
        pytest.skip("honors_rules bundle failed instantiation")
    required_attrs = [
        "minimum_cgpa_throughout",
        "minimum_semesters",
        "maximum_semesters",
        "no_f_grade_allowed",
        "no_disciplinary_penalties",
    ]
    for attr in required_attrs:
        assert hasattr(bundle, attr), f"honors_rules missing required attribute: {attr}"


# ---------------------------------------------------------------------------
# Student level rules — structural check
# ---------------------------------------------------------------------------

def test_rule_bundle_student_level_has_all_thresholds(rule_bundles):
    """Student level rules must contain all four academic level thresholds."""
    _require_bundles(rule_bundles)
    bundle = rule_bundles.get("student_level_rules")
    if bundle is None:
        pytest.skip("student_level_rules bundle failed instantiation")
    required_attrs = [
        "freshman_max_hours",
        "sophomore_min_hours", "sophomore_max_hours",
        "junior_min_hours", "junior_max_hours",
        "senior_min_hours",
    ]
    for attr in required_attrs:
        assert hasattr(bundle, attr), f"student_level_rules missing required attribute: {attr}"
        val = getattr(bundle, attr)
        assert val is not None and val > 0, (
            f"student_level_rules.{attr} must be a positive int, got {val}"
        )
