"""
response_composer.py  [T09 — Person A]
────────────────────────────────────────
Responsibility:
  Convert the aggregated ResultPackage into natural-language text.
  This is a PRESENTATION layer only — no reasoning, no fabrication.

Rules (from blueprint Section 5.8):
  ✓ Take original_query + ResultPackage
  ✓ Produce fluent natural-language response
  ✓ Merge KG and RAG outputs when both present
  ✓ Include citations from RAG results
  ✗ Must NOT invoke engines
  ✗ Must NOT control workflow
  ✗ Must NOT add information not present in the structured input
  ✗ Must NOT alter factual content — presenter only

LLM instruction:
  The LLM receives structured data and is told only to PRESENT it clearly.
  It must not hallucinate, infer beyond the data, or refuse to answer.

Done when:
  - KG-only result → readable, accurate sentence
  - Mixed result → merged paragraph with RAG citations included
  - Error result → honest "I couldn't find that" message
  - Clarification result → question returned to user
"""

import os
import json
from typing import Optional

from gateway.models.schemas import ResultPackage, QueryResponse, Citation


class ResponseComposer:

    def __init__(self):
        self._api_key = os.environ.get("LLM_API_KEY", "")
        self._base_url = os.environ.get("LLM_BASE_URL", "")
        self._model = os.environ.get("LLM_MODEL", "")

    # ── Main entry point ─────────────────────────────────────────────────────

    def compose(self, result: ResultPackage) -> QueryResponse:
        """
        Turn a ResultPackage into a QueryResponse ready for the UI.
        Handles all status values: ok, error, clarification_needed.
        """
        # TODO: implement
        # Dispatch based on result.status:
        #   "ok"                   → _compose_answer()
        #   "clarification_needed" → _compose_clarification()
        #   "error"                → _compose_error()
        raise NotImplementedError

    # ── Composition methods ───────────────────────────────────────────────────

    def _compose_answer(self, result: ResultPackage) -> QueryResponse:
        """Call LLM with structured data. Return natural-language answer."""
        # TODO: implement
        # Steps:
        #   1. Build prompt with _build_prompt()
        #   2. Call LLM API
        #   3. Extract citations from result.rag_result if present
        #   4. Return QueryResponse(session_id=..., answer_text=..., citations=..., status="ok")
        #      NOTE: session_id is filled in by the gateway (not here)
        raise NotImplementedError

    def _compose_clarification(self, result: ResultPackage) -> QueryResponse:
        """Return the clarification question to the user directly."""
        # TODO: implement
        raise NotImplementedError

    def _compose_error(self, result: ResultPackage) -> QueryResponse:
        """Return a user-friendly error message."""
        # TODO: implement
        raise NotImplementedError

    # ── LLM prompt building ───────────────────────────────────────────────────

    def _build_prompt(self, result: ResultPackage) -> str:
        """
        Build the LLM prompt from structured data.

        Critical instruction to include in the system prompt:
          "You are a presentation layer. Your job is to present the data
           below in clear, friendly language. Do NOT add information that
           is not in the data. Do NOT reason beyond the data provided."

        Include:
          - original_query (what the student asked)
          - kg_result (if present) — formatted as structured data
          - rag_result.answer (if present) — with citation note
          - student context summary (if present) — name, track, cgpa
        """
        # TODO: implement
        raise NotImplementedError

    def _call_llm(self, prompt: str) -> str:
        """
        Call the hosted LLM API.
        Returns the raw text response from the model.
        """
        # TODO: implement
        # Use self._api_key, self._base_url, self._model
        # Standard messages format: [{"role": "user", "content": prompt}]
        raise NotImplementedError
