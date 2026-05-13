import logging
import os
from typing import Dict, Any, Optional
import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

class RAGAdapter:
    """
    Interface to the existing RAG pipeline.
    Takes a sub_query and optional student context, passes them to the RAG pipeline,
    and formats the output as a strict data contract: { "answer": str, "citations": list }.
    """
    def __init__(self):
        # We load the retriever here
        try:
            from engines.rag.retriever import get_retriever

            self.retriever = get_retriever()
            logger.info("RAGAdapter successfully initialized with HybridRetriever.")
        except Exception as e:
            logger.error(f"RAGAdapter failed to initialize retriever: {e}")
            self.retriever = None

    def execute(self, sub_query: str, student_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Executes a RAG query.
        """
        if not sub_query or sub_query.strip() == "":
            return {
                "answer": "Not found in handbook.",
                "citations": []
            }

        if not self.retriever:
            return {
                "answer": "RAG Engine is currently unavailable.",
                "citations": []
            }

        try:
            # 1. Retrieve the top chunks
            logger.info(f"RAG wrapper retrieving for: {sub_query}")
            docs = self.retriever.retrieve(sub_query, k_vec=20, k_bm25=15, k_final=6)
            
            citations = [
                {"source": "CIS Student Handbook", "page": d.metadata.get("page")}
                for d in docs
            ]

            # 2. Generation Step: Build Payload and call Colab API
            if not docs:
                return {
                    "answer": "Not found in the handbook.",
                    "citations": []
                }

            # Build context text from retrieved chunks
            context_text = "\n\n---\n\n".join(d.page_content for d in docs)
            
            # Formulate strict prompt (mimicking original app.py)
            strict_question = f"""HANDBOOK EXCERPT:
{context_text}

STUDENT QUESTION: {sub_query}

STRICT RULES YOU MUST FOLLOW:
1. Keep your answer EXTREMELY concise and direct. Do not add conversational filler, unsolicited advice, or irrelevant rules.
2. If the Excerpt contains the answer, DO NOT include the phrase 'Not found in the handbook.' anywhere in your answer.
3. If, and ONLY if, the entire Excerpt completely lacks the exact answer, your ENTIRE output must be EXACTLY: 'Not found in the handbook.' with no other text.
4. When extracting numbers, grades, or points from tables, copy the exact value from the table. Do not use outside knowledge.
5. Format your answer using short, professional bullet points."""

            if student_context:
                strict_question += f"\n\n[STUDENT CONTEXT APPLIED: {student_context}]"

            system_instruction = "You are a highly strict, rules-based academic advisor for the Egyptian University of Informatics (EUI). You only state facts explicitly found in the provided text."
            
            payload = {
                "system": system_instruction,
                "context": "",
                "question": strict_question
            }
            
            load_dotenv(override=True)
            colab_url = os.getenv("COLAB_LLM_URL")
            if not colab_url:
                logger.error("COLAB_LLM_URL environment variable is missing.")
                return {
                    "answer": "Backend configuration error: LLM endpoint is not set.",
                    "citations": citations
                }
                
            endpoint = f"{colab_url.rstrip('/')}/generate" if "modal.run" not in colab_url else colab_url
            
            logger.info(f"Calling LLM endpoint: {endpoint}")
            response = requests.post(
                endpoint,
                json=payload,
                headers={"Bypass-Tunnel-Reminder": "true"},
                timeout=120
            )
            response.raise_for_status()
            answer = response.json().get("answer", "No answer returned.")
            
            return {
                "answer": answer,
                "citations": citations
            }

        except Exception as e:
            logger.error(f"RAG Engine failed on sub_query '{sub_query}': {str(e)}")
            return {
                "answer": f"An error occurred while searching the handbook: {str(e)}",
                "citations": []
            }
