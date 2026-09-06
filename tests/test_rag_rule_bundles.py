"""
Unit tests for RAGAdapter.get_rule_bundles() — rule-bundle extraction and
Pydantic conversion path.

All tests stub execute_structured() on the adapter instance. No live calls.
Validates field names and values based on what ALE functions consume and what
the CIS Handbook defines (confirmed via CIS_Handbook.md).

Handbook-source references (all on file):
  - CreditLimitRules:         p.7 (21/18/15/12 caps, 9 minimum, 21 final override)
  - SummerSemesterRules:      p.7 (2 default, 3 for CGPA≥3)
  - GraduationRequirementRules: p.10-11 (133 hrs, CGPA≥2.0, ≥6 sems, ≤16 sems)
  - AcademicWarningRules:     p.9  (4 consec / 6 total warnings, 80%, +2 sems, +1 summer)
  - HonorsRules:              p.12 (CGPA≥3.0 throughout, 6–8 sems, no F, no penalties)
  - RetakeRules:              p.8-9 (B cap on first retake, 3 improve-retake max, 2.0 threshold)
  - GradingScaleRules:        p.11 Table 1 (A+=4.0, A=3.7, ..., D-=1.0, F=0.0, Abs=0.0, P=None)
  - StudentLevelRules:        p.2a (Freshman 0–26, Sophomore 27–59, Junior 60–93, Senior 94–133)

Run:
    pytest tests/test_rag_rule_bundles.py -v
"""

import time
import pytest
from adapters.rag_adapter import RAGAdapter


# ── Realistic per-bundle data ────────────────────────────────────────────────
# These values are drawn directly from CIS_Handbook.md and must match exactly
# what ALE schemas require.

_GRADING_SCALE_DATA = {
    "letter_to_points": {
        "A+": 4.0, "A": 3.7, "A-": 3.4,
        "B+": 3.2, "B": 3.0, "B-": 2.8,
        "C+": 2.6, "C": 2.4, "C-": 2.2,
        "D+": 2.0, "D": 1.5, "D-": 1.0,
        "F": 0.0, "Abs": 0.0, "P": None,
    },
    "percentage_to_letter": [
        {"min_pct": 96.0,  "max_pct": 100.0, "letter": "A+"},
        {"min_pct": 92.0,  "max_pct": 95.9,  "letter": "A"},
        {"min_pct": 88.0,  "max_pct": 91.9,  "letter": "A-"},
        {"min_pct": 84.0,  "max_pct": 87.9,  "letter": "B+"},
        {"min_pct": 80.0,  "max_pct": 83.9,  "letter": "B"},
        {"min_pct": 76.0,  "max_pct": 79.9,  "letter": "B-"},
        {"min_pct": 72.0,  "max_pct": 75.9,  "letter": "C+"},
        {"min_pct": 68.0,  "max_pct": 71.9,  "letter": "C"},
        {"min_pct": 64.0,  "max_pct": 67.9,  "letter": "C-"},
        {"min_pct": 60.0,  "max_pct": 63.9,  "letter": "D+"},
        {"min_pct": 55.0,  "max_pct": 59.9,  "letter": "D"},
        {"min_pct": 50.0,  "max_pct": 54.9,  "letter": "D-"},
        {"min_pct": 0.0,   "max_pct": 49.9,  "letter": "F"},
    ],
}
_GRADUATION_DATA = {
    "total_credits_required": 133,
    "minimum_cgpa": 2.0,
    "minimum_regular_semesters": 6,
    "maximum_regular_semesters": 16,
    "must_pass_zero_credit_courses": True,
    "military_training_required_for_males": True,
}
_WARNING_DATA = {
    "cgpa_warning_threshold": 2.0,
    "max_consecutive_warnings": 4,
    "max_total_warnings": 6,
    "warning_exempt_first_semester": True,
    "dismissal_extension_credits_percentage": 0.80,
    "dismissal_extension_extra_semesters": 2,
    "dismissal_extension_extra_summer_semesters": 1,
}
_HONORS_DATA = {
    "minimum_cgpa_throughout": 3.0,
    "minimum_semesters": 6,
    "maximum_semesters": 8,
    "no_f_grade_allowed": True,
    "no_disciplinary_penalties": True,
}
_CREDIT_LIMIT_DATA = {
    "cgpa_above_3_limit": 21,
    "cgpa_between_2_and_3_limit": 18,
    "cgpa_between_1_and_2_limit": 15,
    "cgpa_below_1_limit": 12,
    "minimum_per_semester": 9,
    "final_semester_override": 21,
    "incomplete_extra_course_allowed": True,
}
_RETAKE_DATA = {
    "failed_first_retake_grade_cap": "B",
    "improve_retake_first_attempt_cap": None,
    "improve_retake_subsequent_cap": "B",
    "improve_retake_max_courses_cgpa_above_2": 3,
    "improve_retake_unlimited_below_cgpa": 2.0,
}
_SUMMER_DATA = {
    "default_max_courses": 2,
    "cgpa_above_3_max_courses": 3,
    "cgpa_threshold_for_extra_course": 3.0,
}
_STUDENT_LEVEL_DATA = {
    "freshman_max_hours": 26,
    "sophomore_min_hours": 27, "sophomore_max_hours": 59,
    "junior_min_hours": 60,    "junior_max_hours": 93,
    "senior_min_hours": 94,    "senior_max_hours": 133,
}

# Map from query keyword to stub data — used by _make_stub_execute_structured()
_QUERY_DATA_MAP = {
    "grading scale":       _GRADING_SCALE_DATA,
    "grading_scale":       _GRADING_SCALE_DATA,
    "graduation requirem": _GRADUATION_DATA,
    "maximum number of re": _GRADUATION_DATA,     # second graduation query
    "academic warning":    _WARNING_DATA,
    "conditions and exten":_WARNING_DATA,         # second warning query
    "honors":              _HONORS_DATA,
    "credit limit":        _CREDIT_LIMIT_DATA,
    "incomplete grade in": _CREDIT_LIMIT_DATA,    # second credit-limit query
    "retaking failed":     _RETAKE_DATA,
    "summer semester":     _SUMMER_DATA,
    "student levels":      _STUDENT_LEVEL_DATA,
    "credit hour threshold": _STUDENT_LEVEL_DATA,
}


def _make_stub_execute_structured(override_query: str = None, error_for_query: str = None):
    """Return a stub for execute_structured().

    If override_query matches the sub_query, return an error instead of data.
    """
    def _stub(sub_query, expected_schema):
        # Return error for a specific query (used in one-bundle-fails tests)
        if error_for_query and error_for_query in sub_query:
            return {"data": {}, "citations": [], "error": "rag_llm_error"}
        for keyword, data in _QUERY_DATA_MAP.items():
            if keyword.lower() in sub_query.lower():
                return {"data": dict(data), "citations": []}
        # Unknown query — return empty data (safe fallback)
        return {"data": {}, "citations": []}
    return _stub


@pytest.fixture
def adapter_with_stub():
    """RAGAdapter with execute_structured() stubbed to all-good data."""
    a = RAGAdapter()
    a.execute_structured = _make_stub_execute_structured()
    return a


# ── 1. All 8 bundles convert to Pydantic objects ─────────────────────────────

def test_get_rule_bundles_all_eight_convert(adapter_with_stub):
    """get_rule_bundles() must return exactly 8 keys, all Pydantic-instantiated."""
    bundles = adapter_with_stub.get_rule_bundles(inter_call_delay=0)

    expected_keys = {
        "grading_scale_rules", "graduation_requirement_rules",
        "academic_warning_rules", "honors_rules", "credit_limit_rules",
        "retake_rules", "summer_semester_rules", "student_level_rules",
    }
    assert set(bundles.keys()) == expected_keys
    for name, bundle in bundles.items():
        assert bundle is not None, f"Bundle '{name}' must not be None"
        assert hasattr(bundle, "model_dump"), f"Bundle '{name}' must be a Pydantic model"


# ── 2. One bundle extraction error → that bundle is None, others survive ─────

def test_get_rule_bundles_one_error_others_survive():
    """If one bundle's execute_structured() returns an error, only that bundle
    becomes None while all other bundles succeed."""
    a = RAGAdapter()
    # grading_scale query contains "grading scale" → will receive error
    a.execute_structured = _make_stub_execute_structured(
        error_for_query="grading scale"
    )
    bundles = a.get_rule_bundles(inter_call_delay=0)

    assert bundles.get("grading_scale_rules") is None, (
        "grading_scale_rules must be None when extraction fails"
    )
    # All other bundles must be non-None Pydantic models
    other_keys = [k for k in bundles if k != "grading_scale_rules"]
    for k in other_keys:
        assert bundles[k] is not None, f"Bundle '{k}' must survive a grading_scale failure"


# ── 3. Pydantic conversion failure → failed bundle is None, no crash ─────────

def test_get_rule_bundles_bad_pydantic_data_is_none():
    """When extracted data cannot satisfy the Pydantic schema, that bundle
    becomes None and no exception is raised."""
    a = RAGAdapter()
    # Return garbage for grading_scale but good data for everything else
    def stub(sub_query, schema):
        if "grading scale" in sub_query.lower():
            return {"data": {"nonsense_key": "bad_value"}, "citations": []}
        for kw, data in _QUERY_DATA_MAP.items():
            if kw.lower() in sub_query.lower():
                return {"data": dict(data), "citations": []}
        return {"data": {}, "citations": []}
    a.execute_structured = stub

    bundles = a.get_rule_bundles(inter_call_delay=0)

    assert bundles.get("grading_scale_rules") is None
    # No exception raised, other bundles survive
    assert bundles.get("graduation_requirement_rules") is not None


# ── 4. grading_scale_rules values ────────────────────────────────────────────

def test_get_rule_bundles_grading_scale_values(adapter_with_stub):
    """Verify key letter-to-points values from CIS Handbook Table 1."""
    bundles = adapter_with_stub.get_rule_bundles(inter_call_delay=0)
    bundle = bundles["grading_scale_rules"]

    ltp = bundle.letter_to_points
    assert ltp["A+"] == 4.0,  f"A+ must be 4.0, got {ltp['A+']}"
    assert ltp["A"]  == 3.7,  f"A must be 3.7, got {ltp['A']}"
    assert ltp["B+"] == 3.2,  f"B+ must be 3.2, got {ltp['B+']}"
    assert ltp["B"]  == 3.0,  f"B must be 3.0, got {ltp['B']}"
    assert ltp["D-"] == 1.0,  f"D- must be 1.0, got {ltp['D-']}"
    assert ltp["F"]  == 0.0,  f"F must be 0.0, got {ltp['F']}"
    assert ltp["Abs"] == 0.0, f"Abs must be 0.0, got {ltp['Abs']}"
    assert ltp["P"] is None,  f"P must be None (GPA-neutral), got {ltp['P']}"

    # percentage_to_letter must be non-empty list of PercentageRange
    ptl = bundle.percentage_to_letter
    assert len(ptl) > 0, "percentage_to_letter must not be empty"
    assert all(isinstance(r.min_pct, float) for r in ptl)
    assert all(isinstance(r.max_pct, float) for r in ptl)


# ── 5. credit_limit_rules values ─────────────────────────────────────────────

def test_get_rule_bundles_credit_limit_values(adapter_with_stub):
    """Verify CGPA-bracket credit caps from CIS Handbook p.7."""
    bundles = adapter_with_stub.get_rule_bundles(inter_call_delay=0)
    bundle = bundles["credit_limit_rules"]

    assert bundle.cgpa_above_3_limit        == 21, f"CGPA≥3 cap: got {bundle.cgpa_above_3_limit}"
    assert bundle.cgpa_between_2_and_3_limit == 18, f"CGPA 2-3 cap: got {bundle.cgpa_between_2_and_3_limit}"
    assert bundle.cgpa_between_1_and_2_limit == 15, f"CGPA 1-2 cap: got {bundle.cgpa_between_1_and_2_limit}"
    assert bundle.cgpa_below_1_limit         == 12, f"CGPA<1 cap: got {bundle.cgpa_below_1_limit}"
    assert bundle.final_semester_override    == 21, f"Final semester override: got {bundle.final_semester_override}"
    assert bundle.minimum_per_semester       == 9,  f"Minimum per semester: got {bundle.minimum_per_semester}"
    assert bundle.incomplete_extra_course_allowed is True


# ── 6. academic_warning_rules values ─────────────────────────────────────────

def test_get_rule_bundles_academic_warning_values(adapter_with_stub):
    """Verify warning/dismissal/appeal values from CIS Handbook p.9."""
    bundles = adapter_with_stub.get_rule_bundles(inter_call_delay=0)
    bundle = bundles["academic_warning_rules"]

    assert bundle.cgpa_warning_threshold     == 2.0,  f"Warning threshold: {bundle.cgpa_warning_threshold}"
    assert bundle.max_consecutive_warnings   == 4,    f"Consecutive warnings: {bundle.max_consecutive_warnings}"
    assert bundle.max_total_warnings         == 6,    f"Total warnings: {bundle.max_total_warnings}"
    assert bundle.warning_exempt_first_semester is True
    assert abs(bundle.dismissal_extension_credits_percentage - 0.80) < 0.01, (
        f"Extension credits %: expected 0.80, got {bundle.dismissal_extension_credits_percentage}"
    )
    assert bundle.dismissal_extension_extra_semesters        == 2
    assert bundle.dismissal_extension_extra_summer_semesters == 1


# ── 7. honors_rules values ───────────────────────────────────────────────────

def test_get_rule_bundles_honors_values(adapter_with_stub):
    """Verify honors eligibility values from CIS Handbook p.12."""
    bundles = adapter_with_stub.get_rule_bundles(inter_call_delay=0)
    bundle = bundles["honors_rules"]

    assert bundle.minimum_cgpa_throughout == 3.0, f"Min CGPA: {bundle.minimum_cgpa_throughout}"
    assert bundle.minimum_semesters       == 6,   f"Min semesters: {bundle.minimum_semesters}"
    assert bundle.maximum_semesters       == 8,   f"Max semesters: {bundle.maximum_semesters}"
    assert bundle.no_f_grade_allowed      is True
    assert bundle.no_disciplinary_penalties is True


# ── 8. summer_semester_rules values and fallback ─────────────────────────────

def test_get_rule_bundles_summer_values_from_data(adapter_with_stub):
    """Summer rules extracted from data must have handbook-correct values."""
    bundles = adapter_with_stub.get_rule_bundles(inter_call_delay=0)
    bundle = bundles["summer_semester_rules"]

    assert bundle.default_max_courses            == 2
    assert bundle.cgpa_above_3_max_courses       == 3
    assert bundle.cgpa_threshold_for_extra_course == 3.0


def test_get_rule_bundles_summer_fallback_fills_none_fields():
    """When RAG returns None for summer fields, the fallback fills them
    with handbook-correct constants (p.7) and the bundle still converts."""
    a = RAGAdapter()
    def stub(sub_query, schema):
        if "summer" in sub_query.lower():
            # Simulate RAG returning no summer data (all None)
            return {"data": {}, "citations": []}
        for kw, data in _QUERY_DATA_MAP.items():
            if kw.lower() in sub_query.lower():
                return {"data": dict(data), "citations": []}
        return {"data": {}, "citations": []}
    a.execute_structured = stub

    bundles = a.get_rule_bundles(inter_call_delay=0)
    bundle = bundles.get("summer_semester_rules")

    assert bundle is not None, "summer_semester_rules must not be None even when RAG returns empty"
    assert bundle.default_max_courses            == 2
    assert bundle.cgpa_above_3_max_courses       == 3
    assert bundle.cgpa_threshold_for_extra_course == 3.0


# ── 9. student_level_rules thresholds ────────────────────────────────────────

def test_get_rule_bundles_student_level_values(adapter_with_stub):
    """Verify student-level credit thresholds from CIS Handbook section 2a."""
    bundles = adapter_with_stub.get_rule_bundles(inter_call_delay=0)
    bundle = bundles["student_level_rules"]

    # Freshman: 0 to 26
    assert bundle.freshman_max_hours    == 26
    # Sophomore: 27 to 59
    assert bundle.sophomore_min_hours   == 27
    assert bundle.sophomore_max_hours   == 59
    # Junior: 60 to 93
    assert bundle.junior_min_hours      == 60
    assert bundle.junior_max_hours      == 93
    # Senior: 94 to 133
    assert bundle.senior_min_hours      == 94
    assert bundle.senior_max_hours      == 133


# ── 10. Delay: explicit inter_call_delay is forwarded to time.sleep ───────────

def test_get_rule_bundles_explicit_delay_is_used(monkeypatch, adapter_with_stub):
    """When inter_call_delay is passed explicitly, every time.sleep() call uses it."""
    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))
    adapter_with_stub.get_rule_bundles(inter_call_delay=1.23)
    assert sleep_calls, "time.sleep must be called at least once"
    assert all(s == 1.23 for s in sleep_calls), (
        f"Expected all sleeps to be 1.23, got: {sleep_calls}"
    )


def test_get_rule_bundles_reads_env_delay(monkeypatch):
    """When inter_call_delay is omitted, RAG_RULE_BUNDLE_DELAY_SECONDS is used."""
    monkeypatch.setenv("RAG_RULE_BUNDLE_DELAY_SECONDS", "1.5")
    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))
    a = RAGAdapter()
    a.execute_structured = _make_stub_execute_structured()
    a.get_rule_bundles()  # no inter_call_delay argument
    assert sleep_calls, "time.sleep must be called at least once"
    assert all(s == 1.5 for s in sleep_calls), (
        f"Expected all sleeps to be 1.5 from env, got: {sleep_calls}"
    )


def test_load_rule_bundle_delay_invalid_env_falls_back(monkeypatch):
    """Invalid RAG_RULE_BUNDLE_DELAY_SECONDS env value must produce 2.0 default."""
    monkeypatch.setenv("RAG_RULE_BUNDLE_DELAY_SECONDS", "not-a-number")
    from adapters.rag_adapter import _load_rule_bundle_delay
    assert _load_rule_bundle_delay() == 2.0


# ── 11. Graduation normalization: False → True ────────────────────────────────

def test_graduation_normalization_overrides_false():
    """When RAG returns False for the two boolean graduation requirements,
    the handbook-backed normalization must force them to True."""
    a = RAGAdapter()

    def stub(sub_query, schema):
        if "graduation" in sub_query.lower() or "maximum number of re" in sub_query.lower():
            return {
                "data": {
                    "total_credits_required": 133,
                    "minimum_cgpa": 2.0,
                    "minimum_regular_semesters": 6,
                    "maximum_regular_semesters": 16,
                    "must_pass_zero_credit_courses": False,
                    "military_training_required_for_males": False,
                },
                "citations": [],
            }
        for kw, data in _QUERY_DATA_MAP.items():
            if kw.lower() in sub_query.lower():
                return {"data": dict(data), "citations": []}
        return {"data": {}, "citations": []}

    a.execute_structured = stub
    bundles = a.get_rule_bundles(inter_call_delay=0)
    bundle = bundles.get("graduation_requirement_rules")

    assert bundle is not None, "graduation_requirement_rules must not be None"
    assert bundle.must_pass_zero_credit_courses is True, (
        "must_pass_zero_credit_courses must be True after normalization"
    )
    assert bundle.military_training_required_for_males is True, (
        "military_training_required_for_males must be True after normalization"
    )


def test_graduation_normalization_preserves_other_values():
    """Normalization must not alter numeric/non-boolean graduation fields."""
    a = RAGAdapter()

    def stub(sub_query, schema):
        if "graduation" in sub_query.lower() or "maximum number of re" in sub_query.lower():
            return {
                "data": {
                    "total_credits_required": 133,
                    "minimum_cgpa": 2.0,
                    "minimum_regular_semesters": 6,
                    "maximum_regular_semesters": 16,
                    "must_pass_zero_credit_courses": False,
                    "military_training_required_for_males": False,
                },
                "citations": [],
            }
        for kw, data in _QUERY_DATA_MAP.items():
            if kw.lower() in sub_query.lower():
                return {"data": dict(data), "citations": []}
        return {"data": {}, "citations": []}

    a.execute_structured = stub
    bundles = a.get_rule_bundles(inter_call_delay=0)
    bundle = bundles.get("graduation_requirement_rules")

    assert bundle is not None
    assert bundle.total_credits_required    == 133
    assert bundle.minimum_cgpa             == 2.0
    assert bundle.minimum_regular_semesters == 6
    assert bundle.maximum_regular_semesters == 16


# ── Regression: execute() normal path unbroken ───────────────────────────────

def test_normal_execute_still_works_after_structured_changes():
    """Smoke-test that the normal execute() path is unbroken by Part A fixes."""
    a = RAGAdapter()
    a.extract_facts = lambda q: {
        "found": True,
        "extracted_facts": ["Retake cap is B."],
        "source_documents": [{"page": 8, "text": "Max grade B on first retake."}],
        "query": q,
    }
    result = a.execute("What is the retake grade cap?")
    assert result["found"] is True
    assert result["extracted_facts"] == ["Retake cap is B."]
    assert result["citations"][0]["page"] == 8
