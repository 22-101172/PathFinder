from __future__ import annotations
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RAGAdapter:

    def __init__(self) -> None:
        try:
            from engines.rag.app import ask as _ask, _get_retriever
            self._ask = _ask
            _get_retriever()
            logger.info("RAGAdapter: initialized successfully")
        except Exception as exc:
            logger.error("RAGAdapter: init failed: %s", exc)
            self._ask = None

    def execute(self, sub_query: str, student_context: Optional[Any] = None) -> dict[str, Any]:
        # intentionally unused: RAG handles handbook policy only.
        if not sub_query or not sub_query.strip():
            return {"answer": "No query provided.", "citations": [], "status": "error", "metadata": {"reason": "empty_query"}}
        if self._ask is None:
            return {"answer": "RAG engine is currently unavailable.", "citations": [], "status": "error", "metadata": {"reason": "rag_unavailable"}}
        try:
            result = self._ask(sub_query)
            return {
                "answer": result.get("answer", "No answer returned."),
                "citations": result.get("citations", []),
                "status": result.get("status", "ok"),
                "metadata": result.get("metadata", {}),
            }
        except Exception as exc:
            logger.error("RAGAdapter.execute failed: %s", exc)
            return {"answer": "RAG error occurred.", "citations": [], "status": "error", "metadata": {"reason": "adapter_exception"}}
