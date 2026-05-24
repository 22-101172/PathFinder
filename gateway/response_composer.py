from __future__ import annotations
import json
import logging
from typing import Optional

from gateway.llm_client import get_llm_client, LLMError
from gateway.models.schemas import Citation, QueryResponse, ResultPackage

logger = logging.getLogger(__name__)

SYSTEM = """You are PathFinder's response presenter for EUI students.
Present the data below as a clear, friendly, helpful academic advising answer.

RULES:
1. Use ONLY the provided data. Never invent facts.
2. Do NOT decide eligibility, graduation, GPA yourself — present what the system returned.
3. Never mention KG, Neo4j, ALE, RAG, or technical internals. Say "the curriculum" or "our records".
4. Keep answers focused: 1-4 paragraphs. Use bullet points for lists of courses/skills/roles.
5. If citations present, end with: Sources: <source>, p.<page>
6. Use the student's track and level naturally when relevant.
7. English only.
8. If data shows "ALE engine not connected" or "stub" — say this feature is coming soon, apologize briefly.
"""

_ERRORS = {
    "course_not_found": "I couldn't find that course in the curriculum. Please check the course code.",
    "role_not_found": "I couldn't find that role in our catalogue.",
    "track_not_found": "I couldn't find that track.",
    "skill_not_found": "I couldn't find that skill.",
    "invalid_course_code": "That doesn't look like a valid course code. Try the format C-CS301.",
    "no_courses_provided": "I need your course history to answer that question.",
    "no_role_provided": "Please specify a target role.",
    "no_track_provided": "Please specify a track.",
    "no_skill_provided": "Please specify a skill.",
    "unknown_kg_intent": "I couldn't understand what you're asking. Please try rephrasing.",
}


class ResponseComposer:

    def __init__(self):
        self._llm = get_llm_client()

    def compose(self, result: ResultPackage, original_query: str) -> QueryResponse:
        logger.info("Composer: intent=%s status=%s", result.intent, result.status)

        if result.status == "clarification_needed":
            return QueryResponse(session_id="", session_name="",
                                 answer_text=result.error_detail or "Could you clarify your question?",
                                 citations=[], status="clarification_needed")

        if result.status == "error":
            msg = _ERRORS.get((result.error_detail or "").strip().lower(),
                               result.error_detail or "I couldn't process that. Please try rephrasing.")
            return QueryResponse(session_id="", session_name="", answer_text=msg,
                                 citations=[], status="error")

        citations = []
        if result.rag_result and result.rag_result.citations:
            citations = list(result.rag_result.citations)

        data_block = {}
        if result.kg_result:
            data_block["curriculum_data"] = result.kg_result
        if result.rag_result and result.rag_result.answer:
            data_block["handbook_answer"] = result.rag_result.answer
        if result.ale_result:
            data_block["academic_decision"] = result.ale_result
        if result.composer_context:
            data_block["student_summary"] = result.composer_context.model_dump()

        user_msg = f"Student asked: {original_query}\n\nData:\n{json.dumps(data_block, indent=2, default=str)}"

        try:
            answer = self._llm.chat(system=SYSTEM, user=user_msg, temperature=0.3)
            answer = (answer or "").strip()
            if not answer:
                raise LLMError("empty")
        except LLMError as exc:
            logger.warning("Composer: LLM failed (%s), fallback", exc)
            answer = _fallback(result)

        logger.info("Composer: answer %d chars", len(answer))
        return QueryResponse(session_id="", session_name="",
                             answer_text=answer, citations=citations, status="ok")


def _fallback(result: ResultPackage) -> str:
    if result.kg_result and "error" not in result.kg_result:
        return f"Here is what I found:\n{json.dumps(result.kg_result, indent=2, default=str)}"
    if result.rag_result and result.rag_result.answer:
        return result.rag_result.answer
    if result.ale_result:
        return f"Academic result:\n{json.dumps(result.ale_result, indent=2, default=str)}"
    return "I found information but couldn't format a response. Please try again."
