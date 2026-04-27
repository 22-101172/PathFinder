"""
main.py  [T04 — Person B]
──────────────────────────
Gateway / Backend API — single entry point for all PathFinder queries.

One endpoint handles all 6 workflow types:
  POST /query  →  returns QueryResponse

Architecture note:
  This file wires everything together. It does not contain business logic.
  Business logic lives in the components it calls (session_manager,
  query_understanding, orchestrator, response_composer).

Done when:
  - curl test returns valid QueryResponse JSON
  - Invalid request body returns structured 422 error
  - session_id is assigned on first request and echoed back
  - Correct response shape for all 6 workflow types
"""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models.schemas import QueryRequest, QueryResponse
from student_context_provider import StudentContextProvider
from session_manager import SessionManager
from query_understanding import QueryUnderstandingLayer
from orchestrator import Orchestrator
from response_composer import ResponseComposer
from wrappers.kg_wrapper import KGWrapper
from wrappers.rag_wrapper import RAGWrapper


# ── Component instantiation ───────────────────────────────────────────────────
# All components are instantiated once at startup and reused across requests.

student_provider = StudentContextProvider()
session_manager  = SessionManager(student_provider)
qu_layer         = QueryUnderstandingLayer()
kg_wrapper       = KGWrapper()
rag_wrapper      = RAGWrapper()
orchestrator     = Orchestrator(kg_wrapper, rag_wrapper)
composer         = ResponseComposer()


# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="PathFinder Gateway API",
    description="Integration phase — KG + RAG orchestration backend",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten this for production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "pathfinder-gateway"}


# ── Main endpoint ─────────────────────────────────────────────────────────────

@app.post("/query", response_model=QueryResponse)
def handle_query(request: QueryRequest) -> QueryResponse:
    """
    End-to-end query handler. Processes all 6 workflow types.

    Flow (Section 8.1 of blueprint):
      Phase 1 — Preparation:    session + student context loaded
      Phase 2 — Understanding:  QU layer classifies query, detects overrides
      Phase 3 — Execution:      Orchestrator calls engines, aggregates results
      Phase 4 — Response:       Composer generates natural-language answer
    """

    # ── Phase 1: Load session and build effective context ─────────────────────
    # TODO:
    #   session = session_manager.get_or_create_session(request.session_id, request.active_student_id)
    #   effective_context = session_manager.build_effective_context(session)

    # ── Phase 2: Query Understanding ─────────────────────────────────────────
    # TODO:
    #   structured_query = qu_layer.classify(request.user_text, effective_context)
    #   session = session_manager.apply_overrides_from_structured_query(session, structured_query.session_overrides)
    #   effective_context = session_manager.build_effective_context(session)  # rebuild with new overrides

    # ── Phase 3: Orchestration ────────────────────────────────────────────────
    # TODO:
    #   result_package = orchestrator.run(structured_query, effective_context, request.user_text)

    # ── Phase 4: Compose response and save session ────────────────────────────
    # TODO:
    #   response = composer.compose(result_package)
    #   session_manager.update_last_referenced(session, ...)
    #   session_manager.save_session(session)
    #   response.session_id = session.session_id
    #   return response

    # ── Stub response (remove once all components are implemented) ────────────
    return QueryResponse(
        session_id=request.session_id or "stub-session-001",
        answer_text="[STUB] Gateway is running. Wire components in the TODO blocks above.",
        citations=[],
        status="ok",
    )
