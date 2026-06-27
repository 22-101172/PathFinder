from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager
from typing import Callable, Optional

from dotenv import load_dotenv
load_dotenv(override=True)

_TRACE = os.getenv("PATHFINDER_TRACE", "").lower() in ("true", "1", "yes")

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
    get_session_history_for_student,
    update_session_after_turn,
    merge_turn_overrides,
    delete_session_for_student,
    delete_all_sessions_for_student,
    delete_all_sessions_global,
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


def _mask_student_id(sid: str) -> str:
    """Truncate student ID for logs — shows first 3 chars only."""
    return (sid[:3] + "***") if len(sid) > 3 else "***"


def _make_turn_history_summary(turn) -> str:
    """Compact structured summary stored as answer_text until Composer is wired."""
    intents = [r.intent for r in turn.results]
    statuses = [r.status for r in turn.results]
    return f"Executed intents: {intents}. Statuses: {statuses}. Turn status: {turn.turn_status}."


def _is_dev_mode() -> bool:
    """
    Return True only when APP_ENV=dev or DEV_MODE=true.
    Developer-only endpoints must call this before executing any state-mutating action.
    """
    return (
        os.getenv("APP_ENV", "").lower() == "dev"
        or os.getenv("DEV_MODE", "").lower() == "true"
    )


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
    # Service readiness guard — startup may have failed silently
    if _orchestrator is None or _composer is None:
        logger.error(
            "POST /chat | service not ready: orchestrator=%s composer=%s",
            _orchestrator is not None, _composer is not None,
        )
        raise HTTPException(status_code=503, detail="Service not ready — startup may have failed")

    logger.info(
        "POST /chat | student=%s session=%s query_len=%d",
        _mask_student_id(request.student_id),
        (request.session_id or "")[:8] or "new",
        len(request.user_text or ""),
    )

    # 1. Load student context
    ctx = get_context(request.student_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Student {request.student_id!r} not found")

    session, session_is_new = get_or_create_session(
        request.session_id, request.student_id, ctx, request.user_text,
    )

    if _TRACE:
        ov = session.overrides
        lr = session.last_referenced
        logger.info(
            "=== PathFinder Turn Start ===\n"
            "student: %s\n"
            "session: %s\n"
            "session_state: %s\n"
            "query: \"%s\"\n"
            "last_refs_before:\n"
            "  course_code: %s\n"
            "  role_id: %s\n"
            "  track_id: %s\n"
            "  skill_id: %s\n"
            "overrides_before:\n"
            "  assumed_passed_courses: %s\n"
            "  assumed_failed_courses: %s\n"
            "  added_courses: %s\n"
            "  target_role: %s",
            _mask_student_id(request.student_id),
            session.session_id[:8],
            "new" if session_is_new else "reused",
            (request.user_text or "")[:120],
            lr.course_code if lr else None,
            lr.role_id if lr else None,
            lr.track_id if lr else None,
            lr.skill_id if lr else None,
            len(ov.assumed_passed_courses) if ov.assumed_passed_courses else [],
            len(ov.assumed_failed_courses) if ov.assumed_failed_courses else [],
            len(ov.added_courses) if ov.added_courses else [],
            ov.target_role,
        )

    # 2–4. QU → Orchestrator → Composer pipeline
    try:
        sqs = understand_query(
            user_text=request.user_text,
            last_referenced=session.last_referenced,
            recent_turns=list(session.turn_history[-QU_CONTEXT_TURNS:]),
            resolver=_resolver,
        )
        tw = _orchestrator.execute_turn(sqs, session, _rule_bundles)
        qr = _composer.compose(
            user_text=request.user_text,
            turn=tw,
            session_id=session.session_id,
            session_name=session.session_name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "POST /chat pipeline error | student=%s session=%s error=%s",
            _mask_student_id(request.student_id),
            session.session_id[:8],
            type(exc).__name__,
        )
        raise HTTPException(status_code=500, detail="Internal pipeline error")

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

    if _TRACE:
        logger.info(
            "=== PathFinder Turn End ===\n"
            "sq_intents: %s\n"
            "sq_statuses: %s\n"
            "qr_status: %s\n"
            "answer_len: %d",
            [r.intent for r in tw.results],
            [r.status for r in tw.results],
            qr.status,
            len(qr.answer_text),
        )

    intent_statuses = {r.intent: r.status for r in tw.results}
    logger.info(
        "POST /chat done | student=%s session=%s qr_status=%s intent_statuses=%s answer_len=%d",
        _mask_student_id(request.student_id),
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


@app.get("/session/{session_id}/history")
async def session_history(session_id: str):
    """
    [DEPRECATED] Unsafe endpoint.
    Returns 410 Gone. Clients must use GET /students/{student_id}/sessions/{session_id}/history instead.
    """
    raise HTTPException(
        status_code=410,
        detail="This endpoint is deprecated for security reasons. Use /students/{student_id}/sessions/{session_id}/history instead."
    )


@app.get(
    "/students/{student_id}/sessions/{session_id}/history",
    response_model=SessionHistoryResponse,
)
async def student_session_history(student_id: str, session_id: str):
    """
    Return the turn history for a session, with ownership verification.

    Returns 404 if the session does not exist OR belongs to a different student.
    """
    logger.info("GET /students/%s/sessions/%s/history", student_id, session_id)
    result = get_session_history_for_student(student_id, session_id)
    if not result:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@app.delete("/students/{student_id}/sessions/{session_id}")
async def delete_student_session(student_id: str, session_id: str):
    """
    Delete a specific session owned by the given student.

    Returns 404 if the session does not exist or does not belong to this student.
    Ownership is verified before deletion.
    """
    logger.info("DELETE /students/%s/sessions/%s", student_id, session_id)
    deleted = delete_session_for_student(student_id, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found or not owned by student")
    return {"deleted": True, "session_id": session_id}


@app.delete("/dev/students/{student_id}/sessions")
async def dev_delete_student_sessions(student_id: str):
    """
    [DEV ONLY] Delete all sessions for a specific student.

    Requires APP_ENV=dev or DEV_MODE=true. Returns 403 otherwise.
    """
    if not _is_dev_mode():
        raise HTTPException(status_code=403, detail="This endpoint is only available in dev mode.")
    logger.warning("DELETE /dev/students/%s/sessions (dev mode)", student_id)
    count = delete_all_sessions_for_student(student_id)
    return {"deleted": True, "student_id": student_id, "count": count}


@app.delete("/dev/sessions")
async def dev_delete_all_sessions():
    """
    [DEV ONLY] Delete ALL sessions globally.

    Requires APP_ENV=dev or DEV_MODE=true. Returns 403 otherwise.
    Use with caution — this clears every session in the store.
    """
    if not _is_dev_mode():
        raise HTTPException(status_code=403, detail="This endpoint is only available in dev mode.")
    logger.warning("DELETE /dev/sessions (dev mode) — clearing all sessions")
    count = delete_all_sessions_global()
    return {"deleted": True, "count": count}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "PathFinder"}
