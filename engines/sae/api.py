"""
SAE FastAPI router.

Standalone (for local testing):
    uvicorn engines.sae.api:app --reload

Mount inside PathFinder's main.py:
    from engines.sae.api import router as sae_router
    app.include_router(sae_router)

Rules can be injected per-request via the optional `rules` query parameter
(JSON-encoded dict).  If omitted, DEFAULT_RULES are used.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query

from engines.sae.rule_engine import DEFAULT_RULES
from engines.sae.engine import (
    analyze_student,
    get_advisor_analysis,
    get_advisor_overview,
    get_alerts,
    get_cohort_comparison,
    get_course_risk,
    simulate_gpa,
)

# ── Router (mountable in PathFinder) ──────────────────────────────────────────
router = APIRouter(prefix="/sae", tags=["SAE"])


def _parse_rules(rules_json: str | None) -> dict:
    """Parse optional JSON rules string, fall back to DEFAULT_RULES."""
    if rules_json is None:
        return DEFAULT_RULES
    try:
        parsed = json.loads(rules_json)
        return {**DEFAULT_RULES, **parsed}   # caller overrides specific keys
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid rules JSON: {exc}")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/health")
async def health() -> dict[str, Any]:
    """Liveness check — confirms the SAE module is running."""
    return {"status": "ok"}


@router.get("/student/{student_id}")
async def student_profile(
    student_id: str,
    rules: str | None = Query(
        default=None,
        description="Optional JSON-encoded rules dict to override thresholds.",
    ),
) -> dict[str, Any]:
    """
    Full analytics profile for a single student.

    Returns GPA trend, credit pace, risk flags, anomaly detection,
    cohort standing, category performance, and prerequisite bottlenecks.
    """
    try:
        result = analyze_student(student_id, _parse_rules(rules))
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/student/{student_id}/analysis")
async def student_analysis(
    student_id: str,
    rules: str | None = Query(default=None),
) -> dict[str, Any]:
    """
    Advisor-focused analysis for a single student.

    Returns key_points, talking_points, LLM session guide,
    track performance, CGPA trend history, and risk flags.
    """
    try:
        return get_advisor_analysis(student_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/student/{student_id}/alerts")
async def student_alerts(
    student_id: str,
    rules: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    """
    Active alerts for a single student, sorted high → medium → low severity.
    """
    try:
        return get_alerts(student_id, _parse_rules(rules))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/student/{student_id}/cohort")
async def student_cohort(
    student_id: str,
) -> dict[str, Any]:
    """
    Student's standing within their Level × Program cohort.

    Returns cohort size, average CGPA, and the student's percentile rank.
    """
    try:
        return get_cohort_comparison(student_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/student/{student_id}/simulate")
async def simulate_student_gpa(
    student_id: str,
    body: dict[str, Any],
    rules: str | None = Query(default=None),
) -> dict[str, Any]:
    """
    Simulate CGPA impact of hypothetical grades on current courses.

    Request body: {"grades": {"C-MA111": "B+", "C-PH111": 75}}
    Grades can be letter grades ('A', 'B+') or percentages (85, 72.5).

    Returns current CGPA, projected CGPA, delta, and per-course breakdown.
    """
    grades = body.get("grades")
    if not grades or not isinstance(grades, dict):
        raise HTTPException(status_code=400, detail="Request body must include 'grades' dict.")
    try:
        return simulate_gpa(student_id, grades)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/advisor/overview")
async def advisor_overview(
    rules: str | None = Query(default=None),
    force_refresh: bool = Query(
        default=False,
        description="Bypass the 24-hour cache and recompute immediately.",
    ),
) -> list[dict[str, Any]]:
    """
    All active students sorted by risk severity descending.

    Served from a 24-hour file cache by default.
    Each record includes: id, name, level, program, official_cgpa,
    consecutive_warning, risk_level, risk_flags.
    """
    return get_advisor_overview(_parse_rules(rules), force_refresh=force_refresh)


@router.get("/courses/risk")
async def course_risk(
    level: str | None = Query(
        default=None,
        description=(
            "Optional academic level filter: Freshman, Sophomore, Junior, or Senior. "
            "If omitted, returns analytics across all students."
        ),
    ),
) -> list[dict[str, Any]]:
    """
    Course pass-rate analytics based on historical first-attempt data.

    Returns courses sorted by pass rate ascending (highest risk first).
    Excludes graduation project, field training, and 0-credit courses.
    """
    valid_levels = {"Freshman", "Sophomore", "Junior", "Senior", None}
    if level not in valid_levels:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid level '{level}'. Must be one of: Freshman, Sophomore, Junior, Senior.",
        )
    return get_course_risk(level=level)


# ── Standalone app (for uvicorn sae.api:app) ──────────────────────────────────
app = FastAPI(
    title="Student Analysis Engine (SAE)",
    description="Academic risk analysis API for PathFinder integration.",
    version="2.0.0",
)
app.include_router(router)
