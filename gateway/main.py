"""
main.py  [T04]
──────────────
Gateway API — single entry point for all PathFinder queries.

One endpoint handles all 6 workflow types:
  POST /query  →  returns QueryResponse

Data flow per request (Blueprint §8.1):
  Phase 1 — Preparation:    session + student context loaded
  Phase 2 — Understanding:  QU layer classifies query, detects overrides
  Phase 3 — Execution:      Orchestrator calls engines, aggregates results
  Phase 4 — Response:       Composer generates natural-language answer
"""

import logging
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

logger = logging.getLogger(__name__)


# ── Component instantiation ───────────────────────────────────────────────────
# All components are created once at module load time and shared across requests.

student_provider = StudentContextProvider()
kg_wrapper       = KGWrapper()
rag_wrapper      = RAGWrapper()
session_manager  = SessionManager(student_provider)
qu_layer         = QueryUnderstandingLayer()
orchestrator     = Orchestrator(kg_wrapper, rag_wrapper)
composer         = ResponseComposer()


# ── App setup ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    kg_wrapper.close()


app = FastAPI(
    title="PathFinder Gateway API",
    description="Integration phase — KG + RAG orchestration backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten for production
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

    Returns 404 if the student is not found.
    Returns 503 while pipeline components (T07–T09) are still being implemented.
    """

    # ── Phase 1: Load session and student context ─────────────────────────────
    session_id = session_manager.get_or_create_session(
        request.active_student_id,
        request.session_id,
    )

    base_context = student_provider.get_student(request.active_student_id)
    if base_context is None:
        raise HTTPException(
            status_code=404,
            detail=f"Student {request.active_student_id!r} not found.",
        )

    effective_context = session_manager.build_effective_context(base_context, session_id)

    # ── Phase 2: Query Understanding ─────────────────────────────────────────
    try:
        structured_query = qu_layer.classify(request.user_text, effective_context)
    except NotImplementedError:
        raise HTTPException(
            status_code=503,
            detail="QueryUnderstandingLayer not yet implemented (T07 pending).",
        )

    # Apply session overrides detected by QU layer, then rebuild context.
    overrides = structured_query.session_overrides
    if overrides.added_courses or overrides.target_role:
        session_manager.apply_overrides(
            session_id,
            {
                "added_courses": overrides.added_courses,
                "target_role": overrides.target_role,
            },
        )
        effective_context = session_manager.build_effective_context(base_context, session_id)

    # ── Phase 3: Orchestration ────────────────────────────────────────────────
    try:
        result_package = orchestrator.run(
            structured_query, effective_context, request.user_text
        )
    except NotImplementedError:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator not yet implemented (T08 pending).",
        )

    # ── Phase 4: Compose response and save session ────────────────────────────
    try:
        response = composer.compose(result_package)
    except NotImplementedError:
        raise HTTPException(
            status_code=503,
            detail="ResponseComposer not yet implemented (T09 pending).",
        )

    session_manager.update_last_referenced(
        session_id,
        course_code=structured_query.entities.course_code,
        role_id=structured_query.entities.role_id,
        workflow=structured_query.intent,
    )
    session_manager.record_turn(session_id)

    response.session_id = session_id
    return response
