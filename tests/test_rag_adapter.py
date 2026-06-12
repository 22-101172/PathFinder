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

    get_rule_bundles() fires 8 structured LLM calls back-to-back.  Groq's free
    tier enforces a per-minute request limit; if those calls arrive too fast the
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
    """Grading scale bundle must contain letter_to_points mapping."""
    _require_bundles(rule_bundles)
    assert "grading_scale_rules" in rule_bundles, "grading_scale_rules bundle missing"
    bundle = rule_bundles["grading_scale_rules"]
    ltp = bundle.letter_to_points
    assert "A" in ltp, "letter_to_points must contain grade A"
    assert "F" in ltp, "letter_to_points must contain grade F"
    assert ltp["A"] == 4.0, f"Grade A must map to 4.0, got {ltp['A']}"
    assert ltp["F"] == 0.0, f"Grade F must map to 0.0, got {ltp['F']}"


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
