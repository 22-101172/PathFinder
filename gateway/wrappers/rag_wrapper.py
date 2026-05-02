import logging
import sys
import os
from typing import Dict, Any, Optional

# Ensure the rag directory is in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
rag_path = os.path.abspath(os.path.join(current_dir, "..", "..", "rag"))
if rag_path not in sys.path:
      sys.path.append(rag_path)

from retriever import get_retriever

logger = logging.getLogger(__name__)

class RAGWrapper:
      def __init__(self):
                try:
                              self.retriever = get_retriever()
                              logger.info("RAGWrapper initialized.")
except Exception as e:
            logger.error(f"RAGWrapper failed to initialize: {e}")
            self.retriever = None

    def execute(self, sub_query: str, student_context: Optional[Any] = None) -> Dict[str, Any]:
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
                                                    "text": d.page_content
                              }
                              for d in docs
            ]

            if not docs:
                              return {"answer": "Not found.", "citations": []}

            context_text = "\n\n---\n\n".join(d.page_content for d in docs)

            colab_url = os.getenv("COLAB_LLM_URL")
            if not colab_url:
                              return {"answer": "LLM endpoint not set.", "citations": citations}

            strict_question = f"HANDBOOK EXCERPT:\n{context_text}\n\nSTUDENT QUESTION: {sub_query}\n\nSTRICT RULES: Answer concisely."

            payload = {
                              "system": "Academic advisor.",
                              "question": strict_question
            }

            import requests
            endpoint = f"{colab_url.rstrip('/')}/generate" if "modal.run" not in colab_url else colab_url

            response = requests.post(
                              endpoint,
                              json=payload,
                              headers={"Bypass-Tunnel-Reminder": "true"},
                              timeout=120
            )
            response.raise_for_status()
            answer = response.json().get("answer", "No answer.")

            return {"answer": answer, "citations": citations}

except Exception as e:
            return {"answer": f"Error: {str(e)}", "citations": []}
