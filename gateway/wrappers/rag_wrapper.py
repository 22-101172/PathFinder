"""
wrappers/rag_wrapper.py  [T06 — Person B]
──────────────────────────────────────────
Responsibility:
  Accept a natural-language sub-query, call the RAG pipeline,
  and return { answer: str, citations: list }.

Rules (from blueprint Section 5.7):
  ✓ Accept sub_query string + optional student_context
  ✓ Call existing RAG pipeline
  ✓ Return { answer: str, citations: list[Citation] }
  ✗ Must NOT perform reasoning or routing
  ✗ Treat RAG pipeline as a black box — only care about the output contract

Done when:
  - Handbook query returns answer + at least one citation
  - Off-topic query returns fallback message (not an exception)
"""

import os
import httpx
from typing import Optional

from models.schemas import RAGResult, Citation, StudentContext


class RAGWrapper:
    """
    Thin adapter between the Orchestrator and the existing RAG pipeline.

    The RAG pipeline is treated as a black box HTTP service.
    If you prefer direct Python import, change _call_pipeline() accordingly.
    """

    def __init__(self):
        self._rag_url = os.environ.get("RAG_ENGINE_URL", "http://rag-engine:8002")

    def query(
        self,
        sub_query: str,
        student_context: Optional[StudentContext] = None,
    ) -> RAGResult:
        """
        Send sub_query to RAG pipeline. Return answer + citations.
        Never raises — returns a fallback RAGResult on any error.
        """
        # TODO: implement
        # Steps:
        #   1. Build request payload: { "query": sub_query }
        #      Optionally include student track for context-aware retrieval
        #   2. Call _call_pipeline(payload)
        #   3. Parse response into RAGResult
        #   4. On any error → return RAGResult(answer="I could not find relevant information in the handbook.", citations=[])
        raise NotImplementedError

    def _call_pipeline(self, payload: dict) -> dict:
        """
        HTTP POST to the RAG engine service.
        Returns raw response dict.

        If using direct Python import instead of HTTP, replace this method body.
        """
        # TODO: implement
        # response = httpx.post(f"{self._rag_url}/query", json=payload, timeout=30)
        # response.raise_for_status()
        # return response.json()
        raise NotImplementedError

    def _parse_citations(self, raw_citations: list) -> list[Citation]:
        """
        Normalize whatever citation format the RAG pipeline returns
        into a list[Citation] matching the data contract.
        """
        # TODO: implement
        raise NotImplementedError
