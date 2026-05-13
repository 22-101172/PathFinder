"""
test_query_endpoint.py
──────────────────────
End-to-end tests of the `/query` and `/health` HTTP endpoints.

We replace the module-level singletons in `gateway.main` (qu_layer,
orchestrator, composer) with stubs so the tests do not require live KG/RAG/LLM
infrastructure.
"""

from __future__ import annotations

from typing import Optional

import pytest
from fastapi.testclient import TestClient

from gateway import main as gateway_main
from gateway.models.schemas import (
    EntitySet,
    QueryResponse,
    ResultPackage,
    SessionOverrides,
    StructuredQuery,
)


# ── Stubs ────────────────────────────────────────────────────────────────────

class StubQU:
    """Returns a fixed StructuredQuery, or a clarification for `???`."""

    def __init__(self) -> None:
        self.last_call: Optional[str] = None

    def classify(self, user_text: str, student_context=None, session_state=None) -> StructuredQuery:
        self.last_call = user_text
        if user_text.strip() == "???":
            return StructuredQuery(
                intent="ambiguous",
                engine_pattern="kg",
                query_type="non_student_aware",
                entities=EntitySet(),
                needs_clarification=True,
                clarification_prompt="Could you clarify?",
                session_overrides=SessionOverrides(),
            )
        return StructuredQuery(
            intent="get_prerequisites",
            engine_pattern="kg",
            query_type="non_student_aware",
            entities=EntitySet(course_code="C-AI311"),
            session_overrides=SessionOverrides(),
        )


class StubOrchestrator:
    def __init__(self) -> None:
        self.last_query: Optional[StructuredQuery] = None

    def run(self, query: StructuredQuery, context, original_query: str) -> ResultPackage:
        self.last_query = query
        if query.needs_clarification:
            return ResultPackage(
                original_query=original_query,
                engine_pattern=query.engine_pattern,
                status="clarification_needed",
                error_detail=query.clarification_prompt,
            )
        return ResultPackage(
            original_query=original_query,
            engine_pattern="kg",
            kg_result={"course_code": "C-AI311", "name": "Intro AI",
                       "direct_prerequisites": [], "non_course_prerequisites": [],
                       "has_prerequisites": False, "full_prerequisite_tree": []},
            status="ok",
        )


class StubComposer:
    def compose(self, result: ResultPackage) -> QueryResponse:
        if result.status == "clarification_needed":
            return QueryResponse(
                session_id="",
                answer_text=result.error_detail or "Clarify?",
                citations=[],
                status="clarification_needed",
            )
        return QueryResponse(
            session_id="",
            answer_text=f"OK: {result.kg_result.get('course_code') if result.kg_result else ''}",
            citations=[],
            status="ok",
        )


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    """TestClient with the gateway pipeline stubbed out."""
    monkeypatch.setattr(gateway_main, "qu_layer", StubQU())
    monkeypatch.setattr(gateway_main, "orchestrator", StubOrchestrator())
    monkeypatch.setattr(gateway_main, "composer", StubComposer())
    return TestClient(gateway_main.app)


# ── Tests ────────────────────────────────────────────────────────────────────

def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "pathfinder-gateway"}


def test_query_returns_ok_for_prerequisites(client):
    body = {
        "active_student_id": "S_000123",
        "user_text": "What are the prerequisites for C-AI311?",
    }
    response = client.post("/query", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["session_id"].startswith("sess_")
    assert "C-AI311" in payload["answer_text"]


def test_query_returns_clarification_for_ambiguous(client):
    body = {
        "active_student_id": "S_000123",
        "user_text": "???",
    }
    response = client.post("/query", json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "clarification_needed"


def test_unknown_student_returns_404(client):
    body = {
        "active_student_id": "S_DOES_NOT_EXIST",
        "user_text": "anything",
    }
    response = client.post("/query", json=body)
    assert response.status_code == 404


def test_session_reused_across_turns(client):
    body = {
        "active_student_id": "S_000123",
        "user_text": "What are the prerequisites for C-AI311?",
    }
    r1 = client.post("/query", json=body)
    sid = r1.json()["session_id"]
    assert sid

    body_with_sid = dict(body, session_id=sid)
    r2 = client.post("/query", json=body_with_sid)
    assert r2.status_code == 200
    assert r2.json()["session_id"] == sid
