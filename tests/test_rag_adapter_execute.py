"""
Unit tests for RAGAdapter.execute() — normal policy path only.

All tests use stubs/monkeypatching on the adapter instance.
No live Groq calls. No RAG infrastructure (Chroma/embeddings) required.

Covers the 10 required cases from Step 2C:
  1.  found=True  — facts + source_documents present
  2.  found=False — no evidence, no error
  3.  rag_core returns error="rag_llm_error"
  4.  rag_core returns error="rag_retrieval_error"
  5.  extract_facts() raises an exception
  6.  Empty / whitespace query
  7.  RAG unavailable (extract_facts is None)
  8.  source_documents key absent
  9.  Malformed source_documents list
  10. student_context privacy — never forwarded to extract_facts

execute_structured() and get_rule_bundles() are NOT tested here (Step 2D scope).

Run:
    pytest tests/test_rag_adapter_execute.py -v
"""

import pytest
from adapters.rag_adapter import RAGAdapter, _ANSWER_NOT_FOUND, _ANSWER_UNAVAILABLE, _ANSWER_FAILURE


@pytest.fixture
def adapter():
    """RAGAdapter instance with extract_facts pre-stubbed to None.

    RAG infrastructure (Chroma, embeddings) is not required.
    Each test sets adapter.extract_facts to a specific stub before calling execute().
    """
    a = RAGAdapter()
    a.extract_facts = None   # baseline: each test injects its own stub
    return a


# ── 1. found=True with facts and source_documents ──────────────────────────

def test_execute_found_true_returns_expected_shape(adapter):
    """When rag_core returns found=True with facts and source_documents,
    execute() must return found=True, joined answer, original facts, and citations."""
    def stub(query):
        return {
            "found": True,
            "extracted_facts": ["Fact one.", "Fact two."],
            "source_documents": [{"page": 5, "text": "Handbook excerpt."}],
            "query": query,
        }
    adapter.extract_facts = stub

    result = adapter.execute("What is the minimum GPA?")

    assert result["found"] is True
    assert result["extracted_facts"] == ["Fact one.", "Fact two."]
    assert result["answer"] == "Fact one. Fact two."
    assert result["citations"] == [{"source": "CIS Handbook", "page": 5, "text": "Handbook excerpt."}]
    assert "error" not in result


# ── 2. found=False — no evidence, no error ──────────────────────────────────

def test_execute_no_evidence_returns_not_found(adapter):
    """When rag_core returns found=False with no facts, execute() must return
    found=False, the canonical not-found answer, empty facts, empty citations,
    and NO 'error' key."""
    def stub(query):
        return {
            "found": False,
            "extracted_facts": [],
            "source_documents": [],
            "query": query,
        }
    adapter.extract_facts = stub

    result = adapter.execute("Does the handbook predict my GPA?")

    assert result["found"] is False
    assert result["extracted_facts"] == []
    assert result["citations"] == []
    assert result["answer"] == _ANSWER_NOT_FOUND
    assert "error" not in result


# ── 3. rag_core returns error="rag_llm_error" ────────────────────────────────

def test_execute_rag_llm_error_preserved(adapter):
    """When rag_core returns an error code, execute() must preserve the code
    and return a generic safe answer — the error code must not appear in the answer."""
    def stub(query):
        return {
            "found": False,
            "extracted_facts": [],
            "source_documents": [],
            "error": "rag_llm_error",
            "query": query,
        }
    adapter.extract_facts = stub

    result = adapter.execute("What is the credit limit?")

    assert result["found"] is False
    assert result["error"] == "rag_llm_error"
    assert result["extracted_facts"] == []
    assert result["citations"] == []
    assert result["answer"] == _ANSWER_FAILURE
    assert "rag_llm_error" not in result["answer"]


# ── 4. rag_core returns error="rag_retrieval_error" ──────────────────────────

def test_execute_rag_retrieval_error_preserved(adapter):
    """rag_retrieval_error must behave identically to rag_llm_error."""
    def stub(query):
        return {
            "found": False,
            "extracted_facts": [],
            "source_documents": [],
            "error": "rag_retrieval_error",
            "query": query,
        }
    adapter.extract_facts = stub

    result = adapter.execute("What is the attendance requirement?")

    assert result["found"] is False
    assert result["error"] == "rag_retrieval_error"
    assert result["answer"] == _ANSWER_FAILURE
    assert result["extracted_facts"] == []
    assert result["citations"] == []


# ── 5. extract_facts() raises an exception ──────────────────────────────────

def test_execute_exception_returns_safe_error(adapter):
    """If extract_facts() raises, execute() must catch it, return
    error='rag_adapter_error', and keep ALL raw exception text out of the answer."""
    secret = "sk-supersecret-groq-token-12345"

    def stub(query):
        raise RuntimeError(f"Connection failed: {secret}")
    adapter.extract_facts = stub

    result = adapter.execute("What is the grading scale?")

    assert result["found"] is False
    assert result["error"] == "rag_adapter_error"
    assert result["extracted_facts"] == []
    assert result["citations"] == []
    assert secret not in result["answer"], "Raw exception text must not appear in the user-facing answer"
    assert result["answer"] == _ANSWER_FAILURE


# ── 6. Empty / whitespace query ──────────────────────────────────────────────

def test_execute_empty_query_returns_error_without_calling_rag(adapter):
    """Empty or whitespace-only query must NOT call extract_facts and must
    return error='empty_query' with found=False."""
    calls = []

    def stub(query):
        calls.append(query)
        return {"found": True, "extracted_facts": ["fact"], "source_documents": [], "query": query}
    adapter.extract_facts = stub

    for bad_query in ("", "   ", "\t", "\n  \t"):
        result = adapter.execute(bad_query)
        assert result["found"] is False, f"Expected found=False for query {bad_query!r}"
        assert result["error"] == "empty_query"
        assert result["extracted_facts"] == []
        assert result["citations"] == []

    assert calls == [], f"extract_facts must NOT be called for empty queries, got: {calls}"


# ── 7. RAG unavailable (extract_facts is None) ──────────────────────────────

def test_execute_rag_unavailable(adapter):
    """When extract_facts is None (RAG init failed), execute() must return
    error='rag_unavailable' with the unavailable answer, without crashing."""
    adapter.extract_facts = None  # already the fixture default, but explicit here

    result = adapter.execute("What are graduation requirements?")

    assert result["found"] is False
    assert result["error"] == "rag_unavailable"
    assert result["answer"] == _ANSWER_UNAVAILABLE
    assert result["extracted_facts"] == []
    assert result["citations"] == []


# ── 8. source_documents key absent ──────────────────────────────────────────

def test_execute_missing_source_documents_returns_empty_citations(adapter):
    """If source_documents is absent from the rag_core result, execute() must
    still return facts correctly with citations=[], without crashing."""
    def stub(query):
        return {
            "found": True,
            "extracted_facts": ["You need 133 credit hours."],
            # source_documents key deliberately absent
            "query": query,
        }
    adapter.extract_facts = stub

    result = adapter.execute("How many credits to graduate?")

    assert result["found"] is True
    assert result["extracted_facts"] == ["You need 133 credit hours."]
    assert result["citations"] == []
    assert result["answer"] == "You need 133 credit hours."


# ── 9. Malformed source_documents ───────────────────────────────────────────

def test_execute_malformed_source_documents_does_not_crash(adapter):
    """Malformed items in source_documents must be skipped gracefully.
    Valid dict entries must still produce citations. execute() must not raise."""
    def stub(query):
        return {
            "found": True,
            "extracted_facts": ["Attendance is 75%."],
            "source_documents": [
                {"page": 3, "text": "valid entry"},   # good
                "bare string — malformed",             # skip
                None,                                  # skip
                42,                                    # skip
                {"page": 7},                           # missing text → text=""
            ],
            "query": query,
        }
    adapter.extract_facts = stub

    result = adapter.execute("What is the attendance rule?")

    assert result["found"] is True
    assert isinstance(result["citations"], list)
    # Valid dict with text must appear
    assert any(c.get("page") == 3 and c.get("text") == "valid entry" for c in result["citations"])
    # Dict with missing text key must appear with text=""
    assert any(c.get("page") == 7 and c.get("text") == "" for c in result["citations"])
    # Non-dict malformed entries are skipped → exactly 2 citations
    assert len(result["citations"]) == 2
    # All citations carry the correct source tag
    assert all(c["source"] == "CIS Handbook" for c in result["citations"])


# ── 10. student_context privacy ─────────────────────────────────────────────

def test_execute_student_context_never_forwarded_to_rag(adapter):
    """student_context must NEVER reach extract_facts — only the query string
    is passed. This is a hard privacy boundary for the normal RAG path."""
    call_log = []

    def stub(*args, **kwargs):
        call_log.append({"args": args, "kwargs": kwargs})
        return {
            "found": True,
            "extracted_facts": ["Policy fact."],
            "source_documents": [],
            "query": args[0] if args else "",
        }
    adapter.extract_facts = stub

    adapter.execute(
        "What is the withdrawal policy?",
        student_context={
            "student_id": "22-101172",
            "cgpa": 3.1,
            "completed_courses": ["C-CS101"],
        },
    )

    assert len(call_log) == 1, "extract_facts must be called exactly once"
    call = call_log[0]
    # Only the query string as a positional arg
    assert call["args"] == ("What is the withdrawal policy?",), (
        f"extract_facts received unexpected positional args: {call['args']}"
    )
    # No keyword args — especially not student_context
    assert call["kwargs"] == {}, (
        f"extract_facts received unexpected keyword args: {call['kwargs']}"
    )
    # Belt-and-suspenders: student ID must not appear anywhere in what was sent
    assert "22-101172" not in str(call), "student_id must not leak into extract_facts call"
