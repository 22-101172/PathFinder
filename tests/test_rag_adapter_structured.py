"""
Unit tests for RAGAdapter.execute_structured() — structured extraction path.

All tests use stubs on adapter.extract_structured_fn. No live Groq calls.
Covers the 9 required cases from Step 2D Part A.

Run:
    pytest tests/test_rag_adapter_structured.py -v
"""

import pytest
from adapters.rag_adapter import RAGAdapter


@pytest.fixture
def adapter():
    """RAGAdapter with extract_structured_fn stubbed to None.
    Each test sets its own stub before calling execute_structured()."""
    a = RAGAdapter()
    a.extract_structured_fn = None
    return a


_SCHEMA = {"total_credits_required": "int", "minimum_cgpa": "float"}


# ── 1. Success: data + dict source_documents ────────────────────────────────

def test_execute_structured_success_builds_citations(adapter):
    """On success, execute_structured() must return data and built citations."""
    def stub(query, schema):
        return {
            "data": {"total_credits_required": 133, "minimum_cgpa": 2.0},
            "source_documents": [{"page": 10, "text": "133 credit hours required."}],
            "query": query,
        }
    adapter.extract_structured_fn = stub

    result = adapter.execute_structured(
        "What are graduation requirements?", _SCHEMA
    )

    assert "error" not in result
    assert result["data"] == {"total_credits_required": 133, "minimum_cgpa": 2.0}
    assert result["citations"] == [
        {"source": "CIS Handbook", "page": 10, "text": "133 credit hours required."}
    ]


# ── 2. Success: source_documents missing ────────────────────────────────────

def test_execute_structured_missing_source_documents(adapter):
    """If source_documents key is absent, citations=[] and data still returned."""
    def stub(query, schema):
        return {
            "data": {"total_credits_required": 133},
            # source_documents deliberately absent
            "query": query,
        }
    adapter.extract_structured_fn = stub

    result = adapter.execute_structured("How many credits?", _SCHEMA)

    assert "error" not in result
    assert result["data"] == {"total_credits_required": 133}
    assert result["citations"] == []


# ── 3. Malformed source_documents ───────────────────────────────────────────

def test_execute_structured_malformed_source_documents_no_crash(adapter):
    """Malformed items in source_documents are skipped; valid items still appear."""
    def stub(query, schema):
        return {
            "data": {"total_credits_required": 133},
            "source_documents": [
                {"page": 10, "text": "valid"},
                "a bare string",
                None,
                {"page": 11},     # missing text key → text=""
            ],
            "query": query,
        }
    adapter.extract_structured_fn = stub

    result = adapter.execute_structured("Credits?", _SCHEMA)

    assert "error" not in result
    assert isinstance(result["citations"], list)
    assert any(c["page"] == 10 and c["text"] == "valid" for c in result["citations"])
    assert any(c["page"] == 11 and c["text"] == "" for c in result["citations"])
    assert len(result["citations"]) == 2   # bare string and None skipped


# ── 4. rag_core returns error="rag_llm_error" ────────────────────────────────

def test_execute_structured_rag_llm_error_preserved(adapter):
    """When rag_core returns rag_llm_error, execute_structured() preserves it."""
    def stub(query, schema):
        return {
            "data": {},
            "source_documents": [{"page": 5, "text": "some context"}],
            "error": "rag_llm_error",
            "query": query,
        }
    adapter.extract_structured_fn = stub

    result = adapter.execute_structured("Credits?", _SCHEMA)

    assert result["data"] == {}
    assert result["error"] == "rag_llm_error"
    # Citations still built from available source_documents
    assert result["citations"] == [{"source": "CIS Handbook", "page": 5, "text": "some context"}]


# ── 5. rag_core returns error="rag_retrieval_error" ──────────────────────────

def test_execute_structured_rag_retrieval_error_preserved(adapter):
    """rag_retrieval_error must be preserved identically."""
    def stub(query, schema):
        return {
            "data": {},
            "source_documents": [],
            "error": "rag_retrieval_error",
            "query": query,
        }
    adapter.extract_structured_fn = stub

    result = adapter.execute_structured("Credits?", _SCHEMA)

    assert result["data"] == {}
    assert result["error"] == "rag_retrieval_error"
    assert result["citations"] == []


# ── 6. RAG unavailable ───────────────────────────────────────────────────────

def test_execute_structured_rag_unavailable(adapter):
    """When extract_structured_fn is None, return error='rag_unavailable'."""
    adapter.extract_structured_fn = None

    result = adapter.execute_structured("Credits?", _SCHEMA)

    assert result["data"] == {}
    assert result["error"] == "rag_unavailable"
    assert result["citations"] == []


# ── 7. Empty query ───────────────────────────────────────────────────────────

def test_execute_structured_empty_query_does_not_call_fn(adapter):
    """Empty / whitespace query must not call extract_structured_fn."""
    calls = []

    def stub(query, schema):
        calls.append(query)
        return {"data": {}, "source_documents": [], "query": query}
    adapter.extract_structured_fn = stub

    for bad in ("", "   ", "\t\n"):
        result = adapter.execute_structured(bad, _SCHEMA)
        assert result["error"] == "empty_query"
        assert result["data"] == {}

    assert calls == [], f"stub must not be called for empty queries, got: {calls}"


# ── 8. Invalid schema ────────────────────────────────────────────────────────

def test_execute_structured_invalid_schema_does_not_call_fn(adapter):
    """None, non-dict, or empty-dict schema must return error='invalid_schema'."""
    calls = []

    def stub(query, schema):
        calls.append((query, schema))
        return {"data": {}, "source_documents": [], "query": query}
    adapter.extract_structured_fn = stub

    for bad_schema in (None, "a string", [], {}):
        result = adapter.execute_structured("Credits?", bad_schema)
        assert result["error"] == "invalid_schema", f"Expected invalid_schema for schema={bad_schema!r}"
        assert result["data"] == {}

    assert calls == [], f"stub must not be called for invalid schemas, got: {calls}"


# ── 9. extract_structured_fn raises exception ────────────────────────────────

def test_execute_structured_exception_returns_safe_error(adapter):
    """Unexpected exception must be caught, returning rag_adapter_error without
    exposing raw exception text."""
    secret = "sk-internal-secret-9999"

    def stub(query, schema):
        raise ConnectionError(f"Network timeout: {secret}")
    adapter.extract_structured_fn = stub

    result = adapter.execute_structured("Credits?", _SCHEMA)

    assert result["data"] == {}
    assert result["error"] == "rag_adapter_error"
    assert result["citations"] == []
    assert secret not in str(result), "Raw exception text must not appear in adapter result"
