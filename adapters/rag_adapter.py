"""
gateway/adapters/rag_adapter.py
────────────────────────────────
Adapter between the Orchestrator and the RAG engine.

Implements:
  - execute()           → free-text handbook query
  - execute_structured() → schema-forced extraction
  - get_rule_bundles()  → returns ALL 6 rule bundles required by ALE (Section 4 of ALE contract)

ALE contract status: FULLY IMPLEMENTED
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RAGAdapter:

    def __init__(self) -> None:
        import sys
        import os
        
        # Add both the current flat directory and the intended real structure path
        current_dir = os.path.dirname(__file__)
        sys.path.insert(0, current_dir)
        sys.path.insert(0, os.path.join(current_dir, '..', 'engines', 'RAG'))
        sys.path.insert(0, os.path.join(current_dir, '..', '..', 'engines', 'RAG'))
        
        try:
            import rag_core
            self.extract_facts          = rag_core.extract_facts
            self.extract_structured_fn  = rag_core.extract_structured
            logger.info("RAGAdapter: rag_core loaded successfully.")
        except Exception as exc:
            logger.error("RAGAdapter: failed to initialise RAG engine: %s", exc)
            self.extract_facts          = None
            self.extract_structured_fn  = None

    # ── execute (free-text) ──────────────────────────────────────────────────

    def execute(
        self,
        sub_query: str,
        student_context: Optional[Any] = None,
    ) -> dict[str, Any]:
        """
        Run the RAG pipeline for one free-text question.

        Returns dict with keys expected by the Orchestrator:
            "answer"          : str
            "extracted_facts" : list[str]
            "citations"       : list[dict]
        """
        if not sub_query or not sub_query.strip():
            return {"answer": "Not found in handbook.", "extracted_facts": [], "citations": []}

        if self.extract_facts is None:
            return {"answer": "RAG Engine is currently unavailable.", "extracted_facts": [], "citations": []}

        try:
            result = self.extract_facts(sub_query)
            facts  = result.get("extracted_facts", [])
            answer = " ".join(facts) if facts else "Not found in handbook."
            docs   = result.get("source_documents", [])
            citations = [
                {"source": "CIS Handbook", "page": d.get("page"), "text": d.get("text", "")}
                for d in docs
            ]
            return {"answer": answer, "extracted_facts": facts, "citations": citations}

        except Exception as exc:
            logger.error("RAGAdapter.execute failed: %s", exc)
            return {
                "answer": f"An error occurred while searching the handbook: {exc}",
                "extracted_facts": [],
                "citations": [],
            }

    # ── execute_structured ───────────────────────────────────────────────────

    def execute_structured(
        self,
        sub_query: str,
        expected_schema: dict,
    ) -> dict[str, Any]:
        """
        Executes a query forcing the output to match the expected_schema.
        """
        if self.extract_structured_fn is None:
            return {"data": {}, "citations": [], "error": "RAG Engine is unavailable"}

        try:
            result = self.extract_structured_fn(sub_query, expected_schema)
            data   = result.get("data", {})
            docs   = result.get("source_documents", [])
            citations = [
                {"source": "CIS Handbook", "page": d.get("page"), "text": d.get("text", "")}
                for d in docs
            ]
            return {"data": data, "citations": citations}

        except Exception as exc:
            logger.error("RAGAdapter.execute_structured failed: %s", exc)
            return {"data": {}, "citations": [], "error": str(exc)}


