"""
conftest.py
───────────
Shared test fixtures and lightweight fakes so the new gateway pipeline tests
can run without Neo4j, RAG, or live LLM calls.

Why fakes (not mocks):
  The contracts we need to stub are tiny and stable. Plain classes give us
  obvious call recording (`fake.calls`) and easy "set the next response"
  shape without pulling in a mocking library beyond the standard one.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from gateway.kg_data import KGReferenceData
from gateway.llm_client import LLMError
from gateway.models.schemas import CourseRecord, StudentContext


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeKGAdapter:
    """Drop-in stand-in for `KGAdapter`.

    Use `set_response(op, value)` to control what `call()` returns. Calls
    are recorded in `self.calls = [(operation, params), ...]`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, Any] = {}
        self.default: Any = {"error": "course_not_found"}

    def set_response(self, op: str, value: Any) -> None:
        self.responses[op] = value

    def set_default(self, value: Any) -> None:
        self.default = value

    def call(self, operation: str, params: dict) -> dict:
        self.calls.append((operation, dict(params)))
        return self.responses.get(operation, self.default)

    def close(self) -> None:
        pass


class FakeRAGAdapter:
    """Drop-in stand-in for `RAGAdapter`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Optional[dict]]] = []
        self.next_response: dict[str, Any] = {"answer": "stub", "citations": []}

    def set_next(self, answer: str, citations: Optional[list] = None) -> None:
        self.next_response = {
            "answer": answer,
            "citations": citations if citations is not None else [],
        }

    def execute(self, sub_query: str, student_context: Optional[dict] = None) -> dict:
        self.calls.append((sub_query, student_context))
        return self.next_response


class FakeLLMClient:
    """Drop-in stand-in for `LLMClient`.

    The `configured`, `fail`, and `next_response` knobs cover the four
    behaviour combinations our tests need (configured/not, ok/fail)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.configured: bool = True
        self.fail: bool = False
        self.next_response: str = "ok"

    def is_configured(self) -> bool:
        return self.configured

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        self.calls.append({
            "system": system,
            "user": user,
            "json_mode": json_mode,
            "model": model,
            "temperature": temperature,
        })
        if self.fail:
            raise LLMError("forced for test")
        return self.next_response


# ── Reference data fixture ───────────────────────────────────────────────────

@pytest.fixture
def kg_data_stub() -> KGReferenceData:
    """Hand-built `KGReferenceData` with a handful of known entities.

    Mirrors the IDs/aliases the real CSVs publish so test phrasings are
    realistic."""
    data = KGReferenceData()
    data.courses = {
        "C-AI311": "Introduction to Artificial Intelligence",
        "C-AI321": "Introduction to Machine Learning",
        "C-CS213": "Data Structures",
        "C-CS111": "Introduction to Computing and Programming",
    }
    data.course_name_to_code = {
        "introduction to artificial intelligence": "C-AI311",
        "introduction to machine learning": "C-AI321",
        "data structures": "C-CS213",
        "introduction to computing and programming": "C-CS111",
    }
    data.roles = {
        "RL_Data_Scientist": "Data Scientist",
        "RL_Data_Engineer": "Data Engineer",
        "RL_ML_Engineer": "Machine Learning Engineer",
        "RL_Software_Engineer": "Software Engineer",
        "RL_AI_Engineer": "AI Engineer",
    }
    data.role_name_to_id = {
        "data scientist": "RL_Data_Scientist",
        "data engineer": "RL_Data_Engineer",
        "machine learning engineer": "RL_ML_Engineer",
        "software engineer": "RL_Software_Engineer",
        "ai engineer": "RL_AI_Engineer",
    }
    data.tracks = {
        "AI": "Artificial Intelligence",
        "DSE": "Data Science and Engineering",
        "SWE": "Software Engineering",
        "CYS": "Cybersecurity",
    }
    data.track_name_to_id = {
        "artificial intelligence": "AI",
        "data science and engineering": "DSE",
        "software engineering": "SWE",
        "cybersecurity": "CYS",
    }
    data.skills = {
        "SK_ML": "Machine Learning",
        "SK_NLP": "Natural Language Processing",
        "SK_Python": "Python",
    }
    data.skill_name_to_id = {
        "machine learning": "SK_ML",
        "natural language processing": "SK_NLP",
        "python": "SK_Python",
    }
    data.skill_aliases = {
        "ml": "SK_ML",
        "nlp": "SK_NLP",
    }
    return data


# ── Student context fixture ──────────────────────────────────────────────────

@pytest.fixture
def fake_student_context() -> StudentContext:
    """A StudentContext useful for student-aware tests. Realistic but minimal."""
    history = [
        CourseRecord(
            course_code="C-CS111",
            course_name="Introduction to Computing and Programming",
            credit_hours=4,
            grade="A",
            grade_points=3.7,
            semester_taken="Fall 2022",
            status="passed",
        ),
        CourseRecord(
            course_code="C-AI311",
            course_name="Introduction to Artificial Intelligence",
            credit_hours=4,
            grade="B+",
            grade_points=3.2,
            semester_taken="Fall 2024",
            status="passed",
        ),
    ]
    return StudentContext(
        student_id="S_TEST_001",
        name="Test Student",
        track_id="AI",
        level=3,
        current_semester="Spring",
        cgpa=3.4,
        academic_standing="good",
        total_credit_hours_earned=60,
        course_history=history,
        completed_courses=["C-CS111", "C-AI311"],
        failed_courses=[],
        in_progress_courses=[],
        planned_courses=[],
    )


# ── Adapter fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def fake_kg() -> FakeKGAdapter:
    return FakeKGAdapter()


@pytest.fixture
def fake_rag() -> FakeRAGAdapter:
    return FakeRAGAdapter()


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    return FakeLLMClient()
