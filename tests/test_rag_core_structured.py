"""
Unit tests for rag_core.extract_structured() — structured extraction path.

Uses monkeypatching. No live Groq or RAG infrastructure calls.
Covers the 8 required cases from Step 2D Part B.

Run:
    pytest tests/test_rag_core_structured.py -v
"""

import os
import pytest
import engines.rag.rag_core as rag_core


_SCHEMA = {"total_credits_required": "int", "minimum_cgpa": "float"}

# ── Shared helpers ───────────────────────────────────────────────────────────

class _FakeDoc:
    """Minimal LangChain Document-like stub."""
    def __init__(self, page, content):
        self.metadata = {"page": page}
        self.page_content = content


def _make_retriever(docs=None, raise_on_retrieve=False):
    """Return a minimal retriever stub."""
    class _R:
        def retrieve(self, query, **kwargs):
            if raise_on_retrieve:
                raise RuntimeError("Chroma connection refused")
            return docs or []
    return _R()


# ── 1. Empty query ────────────────────────────────────────────────────────────

def test_extract_structured_empty_query(monkeypatch):
    """Empty / whitespace query must return error='empty_query' without touching retriever."""
    monkeypatch.setattr(rag_core, "retriever", _make_retriever())
    for bad in ("", "  ", "\t"):
        result = rag_core.extract_structured(bad, _SCHEMA)
        assert result["error"] == "empty_query"
        assert result["data"] == {}


# ── 2. Invalid schema ─────────────────────────────────────────────────────────

def test_extract_structured_invalid_schema(monkeypatch):
    """None, non-dict, or empty-dict schema must return error='invalid_schema'."""
    monkeypatch.setattr(rag_core, "retriever", _make_retriever())
    for bad in (None, "a string", [], {}):
        result = rag_core.extract_structured("Credits?", bad)
        assert result["error"] == "invalid_schema", f"Expected invalid_schema for schema={bad!r}"
        assert result["data"] == {}


# ── 3. No API key ─────────────────────────────────────────────────────────────

def test_extract_structured_no_api_key(monkeypatch):
    """Missing GROQ_API_KEY must return error='rag_not_configured'."""
    docs = [_FakeDoc(10, "133 credit hours.")]
    monkeypatch.setattr(rag_core, "retriever", _make_retriever(docs=docs))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    result = rag_core.extract_structured("Credits?", _SCHEMA)

    assert result["error"] == "rag_not_configured"
    assert result["data"] == {}


# ── 4. Retriever is None ──────────────────────────────────────────────────────

def test_extract_structured_retriever_none(monkeypatch):
    """Module-level retriever=None must return error='rag_unavailable'."""
    monkeypatch.setattr(rag_core, "retriever", None)

    result = rag_core.extract_structured("Credits?", _SCHEMA)

    assert result["error"] == "rag_unavailable"
    assert result["data"] == {}


# ── 5. Retriever raises exception ─────────────────────────────────────────────

def test_extract_structured_retriever_raises(monkeypatch):
    """Retriever exception must return error='rag_retrieval_error'."""
    monkeypatch.setattr(rag_core, "retriever", _make_retriever(raise_on_retrieve=True))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    result = rag_core.extract_structured("Credits?", _SCHEMA)

    assert result["error"] == "rag_retrieval_error"
    assert result["data"] == {}


# ── 6. All models in chain fail → rag_llm_error ──────────────────────────────

def test_extract_structured_all_models_fail(monkeypatch):
    """If every model in the chain raises, return error='rag_llm_error'."""
    docs = [_FakeDoc(10, "133 credit hours.")]
    monkeypatch.setattr(rag_core, "retriever", _make_retriever(docs=docs))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def fail_all(context, question, api_key, model, system_prompt=None):
        raise ConnectionError("Groq unreachable")
    monkeypatch.setattr(rag_core, "_call_groq", fail_all)
    # Single-model chain so the loop exhausts in one iteration
    monkeypatch.setattr(rag_core, "_load_rag_model_chain", lambda m: ["only-model"])

    result = rag_core.extract_structured("Credits?", _SCHEMA, allow_fallback=False)

    assert result["error"] == "rag_llm_error"
    assert result["data"] == {}
    assert "Groq" not in result.get("error", ""), "error must be a safe code, not raw exception text"


# ── 7. Model chain fallback: first fails, second succeeds ────────────────────

def test_extract_structured_model_chain_fallback(monkeypatch):
    """If first model fails but second succeeds, return data from second model."""
    docs = [_FakeDoc(10, "133 credit hours.")]
    monkeypatch.setattr(rag_core, "retriever", _make_retriever(docs=docs))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    call_log = []

    def partial_failure(context, question, api_key, model, system_prompt=None):
        call_log.append(model)
        if model == "model-a":
            raise ConnectionError("model-a down")
        return {"total_credits_required": 133, "minimum_cgpa": 2.0}

    monkeypatch.setattr(rag_core, "_call_groq", partial_failure)
    monkeypatch.setattr(rag_core, "_load_rag_model_chain",
                        lambda m: ["model-a", "model-b"])

    result = rag_core.extract_structured("Credits?", _SCHEMA)

    assert "error" not in result
    assert result["data"] == {"total_credits_required": 133, "minimum_cgpa": 2.0}
    assert call_log == ["model-a", "model-b"], f"Both models tried: {call_log}"


# ── 8. Success: returns data, source_documents, query ────────────────────────

def test_extract_structured_success_shape(monkeypatch):
    """On success, result must contain data, source_documents, and query."""
    docs = [_FakeDoc(10, "133 credit hours required to graduate.")]
    monkeypatch.setattr(rag_core, "retriever", _make_retriever(docs=docs))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def good_call(context, question, api_key, model, system_prompt=None):
        return {"total_credits_required": 133, "minimum_cgpa": 2.0}
    monkeypatch.setattr(rag_core, "_call_groq", good_call)
    monkeypatch.setattr(rag_core, "_load_rag_model_chain",
                        lambda m: ["test-model"])

    q = "How many credits to graduate?"
    result = rag_core.extract_structured(q, _SCHEMA)

    assert "error" not in result
    assert result["data"] == {"total_credits_required": 133, "minimum_cgpa": 2.0}
    assert result["query"] == q
    assert isinstance(result["source_documents"], list)
    assert len(result["source_documents"]) == 1
    assert result["source_documents"][0]["page"] == 10
