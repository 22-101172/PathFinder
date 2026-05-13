"""
main.py  [T04 — gateway HTTP entry point]
─────────────────────────────────────────
FastAPI application that wires the four pipeline phases together for the
PathFinder integration MVP.

Pipeline (Blueprint §8.1):
  Phase 1 — Preparation:    session + base student context
  Phase 2 — Understanding:  QueryUnderstandingLayer.classify(...)
  Phase 3 — Execution:      Orchestrator.run(...)
  Phase 4 — Response:       ResponseComposer.compose(...)

This module is intentionally thin: it owns wiring, request shape, and the
trace log lines. All decisions live in the pipeline components.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from gateway.adapters.kg_adapter import KGAdapter
from gateway.adapters.rag_adapter import RAGAdapter
from gateway.kg_data import load_kg_reference_data
from gateway.llm_client import get_llm_client
from gateway.models.schemas import QueryRequest, QueryResponse
from gateway.orchestrator import Orchestrator
from gateway.query_understanding import QueryUnderstandingLayer
from gateway.response_composer import ResponseComposer
from gateway.session_manager import SessionManager
from gateway.student_context_provider import StudentContextProvider

logger = logging.getLogger(__name__)


# ── Component instantiation ───────────────────────────────────────────────────
# All shared singletons created once at import time.

student_provider = StudentContextProvider()
kg_adapter       = KGAdapter()
rag_adapter      = RAGAdapter()
session_manager  = SessionManager(student_provider)

# Reference data is loaded eagerly so we hit the "starter dataset" warning
# (if any) at start-up rather than on the first request.
_kg_reference    = load_kg_reference_data()
_llm_client      = get_llm_client()

qu_layer         = QueryUnderstandingLayer(
    kg_data=_kg_reference,
    llm_client=_llm_client,
)
orchestrator     = Orchestrator(kg_adapter, rag_adapter)
composer         = ResponseComposer(llm_client=_llm_client)


# ── App setup ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    kg_adapter.close()


app = FastAPI(
    title="PathFinder Gateway API",
    description="Integration phase — KG + RAG orchestration backend",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten for production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Privacy helper ────────────────────────────────────────────────────────────

def _hash_student_id(student_id: str) -> str:
    """Short, deterministic hash so trace logs can correlate requests
    without writing raw student IDs."""
    if not student_id:
        return "none"
    return hashlib.sha1(student_id.encode("utf-8")).hexdigest()[:8]


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "pathfinder-gateway"}


# ── Main endpoint ─────────────────────────────────────────────────────────────

@app.post("/query", response_model=QueryResponse)
def handle_query(request: QueryRequest) -> QueryResponse:
    """End-to-end query handler.

    Returns 404 if the requested student is not in the data store. All other
    failures are surfaced as a valid `QueryResponse` with status `error` or
    `clarification_needed` — never a 500."""

    student_tag = _hash_student_id(request.active_student_id)
    logger.info(
        "gateway.request.received student=%s session=%s text_len=%d",
        student_tag,
        "present" if request.session_id else "none",
        len(request.user_text or ""),
    )

    # ── Phase 1: Load session and student context ─────────────────────────────
    session_id = session_manager.get_or_create_session(
        request.active_student_id,
        request.session_id,
    )

    base_context = student_provider.get_student(request.active_student_id)
    if base_context is None:
        logger.warning("gateway.student_not_found student=%s", student_tag)
        raise HTTPException(
            status_code=404,
            detail=f"Student {request.active_student_id!r} not found.",
        )

    effective_context = session_manager.build_effective_context(base_context, session_id)
    session_state = session_manager.get_session(session_id)

    # ── Phase 2: Query Understanding ──────────────────────────────────────────
    structured_query = qu_layer.classify(
        request.user_text,
        effective_context,
        session_state=session_state,
    )

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
        logger.debug(
            "gateway.overrides.applied added=%d target_role=%s",
            len(overrides.added_courses),
            bool(overrides.target_role),
        )

    # ── Phase 3: Orchestration ────────────────────────────────────────────────
    result_package = orchestrator.run(
        structured_query, effective_context, request.user_text
    )

    # ── Phase 4: Compose response and save session ────────────────────────────
    response = composer.compose(result_package)

    session_manager.update_last_referenced(
        session_id,
        course_code=structured_query.entities.course_code,
        role_id=structured_query.entities.role_id,
        workflow=structured_query.intent,
    )
    session_manager.record_turn(session_id)

    response.session_id = session_id
    logger.info(
        "gateway.response.sent student=%s session=%s status=%s",
        student_tag, session_id, response.status,
    )
    return response
