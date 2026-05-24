from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv(override=True)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from gateway.models.schemas import (
    QueryRequest, QueryResponse,
    StudentSessionsResponse, SessionHistoryResponse,
)
from gateway.student_context_provider import load_excel, get_context
from gateway.session_manager import (
    create_session, get_session, get_student_sessions,
    get_session_history, update_session_after_turn,
)
from gateway.query_understanding import understand_query
from gateway.orchestrator import Orchestrator
from gateway.response_composer import ResponseComposer
from adapters.kg_adapter import KGAdapter
from adapters.rag_adapter import RAGAdapter
from adapters.ale_adapter import ALEAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

_kg = _rag = _ale = _orchestrator = _composer = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _kg, _rag, _ale, _orchestrator, _composer
    logger.info("PathFinder: starting up...")

    excel_path = os.path.join(os.path.dirname(__file__), "data", "students_anonymous.xlsx")
    load_excel(excel_path)

    _kg = KGAdapter()
    _rag = RAGAdapter()
    _ale = ALEAdapter()
    _orchestrator = Orchestrator(_kg, _rag, _ale)
    _composer = ResponseComposer()

    logger.info("PathFinder: ready.")
    yield

    if _kg:
        _kg.close()
    logger.info("PathFinder: shutdown.")


app = FastAPI(title="PathFinder API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.post("/chat", response_model=QueryResponse)
async def chat(request: QueryRequest):
    logger.info("POST /chat | student=%s session=%s query=%s",
                request.student_id, request.session_id, request.user_text[:60])

    if request.session_id:
        session = get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        ctx = get_context(request.student_id)
        if not ctx:
            raise HTTPException(status_code=404, detail=f"Student {request.student_id} not found")
        session = create_session(request.student_id, ctx, request.user_text)

    sq = understand_query(
        user_text=request.user_text,
        last_referenced=session.last_referenced,
        recent_turns=session.turn_history[-4:],
    )

    result_package = _orchestrator.run(sq, session)
    response = _composer.compose(result_package, request.user_text)

    update_session_after_turn(
        session_id=session.session_id,
        user_text=request.user_text,
        answer_text=response.answer_text,
        new_overrides=sq.session_overrides,
        new_last_referenced=_orchestrator.extract_last_referenced(sq),
    )

    response.session_id = session.session_id
    response.session_name = session.session_name

    logger.info("POST /chat done | session=%s intent=%s status=%s",
                session.session_id, sq.intent, response.status)
    return response


@app.get("/sessions/{student_id}", response_model=StudentSessionsResponse)
async def list_sessions(student_id: str):
    logger.info("GET /sessions/%s", student_id)
    return get_student_sessions(student_id)


@app.get("/session/{session_id}/history", response_model=SessionHistoryResponse)
async def session_history(session_id: str):
    logger.info("GET /session/%s/history", session_id)
    result = get_session_history(session_id)
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@app.get("/health")
async def health():
    return {"status": "ok", "service": "PathFinder"}
