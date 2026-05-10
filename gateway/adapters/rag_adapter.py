from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


class RAGAdapter:
    """Adapter from the gateway orchestrator to the local RAG engine code."""

    def __init__(self):
        try:
            from engines.rag.retriever import get_retriever

            self.retriever = get_retriever()
            logger.info("RAGAdapter initialized.")
        except Exception as exc:
            logger.error("RAGAdapter failed to initialize: %s", exc)
            self.retriever = None

    def execute(
        self,
        sub_query: str,
        student_context: Optional[Any] = None,
    ) -> dict[str, Any]:
        if not sub_query or sub_query.strip() == "":
            return {"answer": "Not found in handbook.", "citations": []}

        if not self.retriever:
            return {"answer": "RAG Engine unavailable.", "citations": []}

        try:
            docs = self.retriever.retrieve(sub_query, k_vec=20, k_bm25=15, k_final=6)

            citations = [
                {
                    "source": "Handbook",
                    "page": d.metadata.get("page"),
                    "text": d.page_content,
                }
                for d in docs
            ]

            if not docs:
                return {"answer": "Not found.", "citations": []}

            context_text = "\n\n---\n\n".join(d.page_content for d in docs)

            colab_url = os.getenv("COLAB_LLM_URL")
            if not colab_url:
                return {"answer": "LLM endpoint not set.", "citations": citations}

            strict_question = (
                f"HANDBOOK EXCERPT:\n{context_text}\n\n"
                f"STUDENT QUESTION: {sub_query}\n\n"
                f"STRICT RULES: Answer concisely."
            )

            payload = {
                "system": "Academic advisor.",
                "question": strict_question,
            }

            endpoint = (
                colab_url
                if "modal.run" in colab_url
                else f"{colab_url.rstrip('/')}/generate"
            )

            response = requests.post(
                endpoint,
                json=payload,
                headers={"Bypass-Tunnel-Reminder": "true"},
                timeout=120,
            )
            response.raise_for_status()
            answer = response.json().get("answer", "No answer.")

            return {"answer": answer, "citations": citations}
        except Exception as exc:
            return {"answer": f"Error: {str(exc)}", "citations": []}


RAGWrapper = RAGAdapter
