from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager
from typing import Callable, Optional

from dotenv import load_dotenv
load_dotenv(override=True)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from gateway.models.schemas import (
    QueryRequest,
    QueryResponse,
    StudentSessionsResponse,
    SessionHistoryResponse,
)
from gateway.student_context_provider import load_excel, get_context
from gateway.session_manager import (
    get_or_create_session,
    get_student_sessions,
    get_session_history,
    update_session_after_turn,
    merge_turn_overrides,
    QU_CONTEXT_TURNS,
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

_kg: Optional[KGAdapter] = None
_rag: Optional[RAGAdapter] = None
_ale: Optional[ALEAdapter] = None
_orchestrator: Optional[Orchestrator] = None
_composer: Optional[ResponseComposer] = None
_rule_bundles: dict = {}
_resolver: Optional[Callable] = None


def _make_resolver(kg: KGAdapter) -> Optional[Callable]:
    """Build a KG entity resolver closure for QU. Returns None when KG is unavailable."""
    if kg._client is None:
        logger.warning("PathFinder: KG unavailable at startup — QU entity resolver disabled.")
        return None
    def _resolve(entity_type: str, entity_text: str) -> dict:
        return kg.call("resolve_entity", {"entity_type": entity_type, "entity_text": entity_text})
    return _resolve


def _make_turn_history_summary(turn) -> str:
    """Compact structured summary stored as answer_text until Composer is wired."""
    intents = [r.intent for r in turn.results]
    statuses = [r.status for r in turn.results]
    return f"Executed intents: {intents}. Statuses: {statuses}. Turn status: {turn.turn_status}."


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _kg, _rag, _ale, _orchestrator, _composer, _rule_bundles, _resolver

    logger.info("PathFinder: starting up...")

    excel_path = os.path.join(os.path.dirname(__file__), "data", "students_anonymous.xlsx")
    load_excel(excel_path)

    _kg = KGAdapter()
    _rag = RAGAdapter()
    _ale = ALEAdapter()
    _orchestrator = Orchestrator(_kg, _rag, _ale)
    _composer = ResponseComposer()

    logger.info("PathFinder: loading rule bundles from RAG...")
    _rule_bundles = _rag.get_rule_bundles()
    if not _rule_bundles:
        logger.warning("PathFinder: rule bundles empty — ALE intents will return engine_error.")
    else:
        logger.info("PathFinder: loaded %d rule bundles.", len(_rule_bundles))

    _resolver = _make_resolver(_kg)

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

    # 1. Load student context and get/create session
    ctx = get_context(request.student_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Student {request.student_id!r} not found")

    session, _ = get_or_create_session(
        request.session_id, request.student_id, ctx, request.user_text,
    )

    # 2. Query Understanding — returns list[StructuredQuery]
    sqs = understand_query(
        user_text=request.user_text,
        last_referenced=session.last_referenced,
        recent_turns=list(session.turn_history[-QU_CONTEXT_TURNS:]),
        resolver=_resolver,
    )

    # 3. Orchestrator execution
    tw = _orchestrator.execute_turn(sqs, session, _rule_bundles)

    # 4. Response Composer — LLM narration with deterministic fallback
    qr = _composer.compose(
        user_text=request.user_text,
        turn=tw,
        session_id=session.session_id,
        session_name=session.session_name,
    )

    # 5. Persist session state (store composed answer for QU context in future turns)
    turn_overrides, had_clear = merge_turn_overrides(sqs)
    new_last_ref = _orchestrator.extract_last_referenced(sqs)

    update_session_after_turn(
        session_id=session.session_id,
        user_text=request.user_text,
        answer_text=qr.answer_text,
        new_overrides=turn_overrides,
        new_last_referenced=new_last_ref,
        replace_overrides=had_clear,
    )

    intent_statuses = {r.intent: r.status for r in tw.results}
    logger.info(
        "POST /chat done | student=%s session=%s qr_status=%s intent_statuses=%s answer_len=%d",
        request.student_id,
        session.session_id[:8],
        qr.status,
        intent_statuses,
        len(qr.answer_text),
    )

    return qr


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
