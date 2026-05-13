"""
response_composer.py  [T09 — Integration sprint implementation]
───────────────────────────────────────────────────────────────
LLM-based presentation layer with a deterministic fallback.

Responsibility (Blueprint §5.8):
  Convert the aggregated `ResultPackage` into a `QueryResponse` ready for the
  UI. The composer presents facts — it does not invent them.

Hard boundaries:
  ✗ Never invokes engines or adapters.
  ✗ Never controls workflow.
  ✗ Never adds information not present in the structured input.
  ✗ Never alters factual content.
  ✗ Never decides eligibility, graduation, GPA impact, or credit-limit.

Privacy contract:
  The user-side LLM prompt receives only:
    - the original user query
    - the KG result dict
    - the RAG answer and citation tuples
    - a non-personal student summary: track_id, level, current_semester
  It MUST NOT receive: name, student_id, cgpa, academic_standing,
  total_credit_hours_earned, course_history, completed/failed/in-progress
  course buckets, or planned_courses.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from gateway.llm_client import LLMClient, LLMError, get_llm_client
from gateway.models.schemas import (
    Citation,
    QueryResponse,
    RAGResult,
    ResultPackage,
    StudentContext,
)

logger = logging.getLogger(__name__)


# ── User-facing error messages ───────────────────────────────────────────────
#
# These are presented when the composer takes the deterministic error path,
# i.e. without calling the LLM. Keeping the table inline (not externalized)
# avoids surprise i18n complexity — the MVP is English-only.
_FRIENDLY_ERRORS: dict[str, str] = {
    "course_not_found":              "I couldn't find that course in the curriculum.",
    "role_not_found":                "I couldn't find that role in our role catalogue.",
    "track_not_found":                "I couldn't find that track.",
    "skill_not_found":                "I couldn't find that skill.",
    "invalid_course_code":            "That doesn't look like a valid course code.",
    "no_courses_provided":            "I don't have any of your completed courses to work with.",
    "no_valid_courses_provided":      "None of the courses you mentioned are in the curriculum.",
    "no_planned_courses_provided":    "I need at least one planned course for that calculation.",
    "no_valid_planned_courses_provided": "None of the planned courses are in the curriculum.",
    "no_skills_provided":             "Please tell me which skill to search for.",
    "no_role_provided":               "Please specify a target role.",
    "no_track_provided":              "Please specify a track.",
    "no_skill_provided":              "Please specify a skill.",
    "role_has_no_required_skills":    "That role has no required skills recorded yet.",
    "circular_dependency_detected":   "The prerequisite chain for that course has a circular dependency, so I can't display the full tree.",
    "student_context_required":       "I need your academic record to answer that question.",
}

_DEFAULT_ERROR_TEXT = "I couldn't process your question. Please try rephrasing."


def _friendly_error_text(detail: Optional[str]) -> str:
    if not detail:
        return _DEFAULT_ERROR_TEXT
    key = detail.strip().lower()
    return _FRIENDLY_ERRORS.get(key, detail if len(detail) < 160 else _DEFAULT_ERROR_TEXT)


# ── Privacy-safe student summary ─────────────────────────────────────────────

def _sanitize_student_summary(context: Optional[StudentContext]) -> Optional[dict]:
    """Return a dict containing ONLY non-personal student facts.

    The allow-list is intentionally short. Any new field needs an explicit
    review against the privacy contract above."""
    if context is None:
        return None
    return {
        "track_id": context.track_id,
        "level": context.level,
        "current_semester": context.current_semester,
    }


# ── Composer ─────────────────────────────────────────────────────────────────

class ResponseComposer:

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        # Accept a client for testability; fall back to the lazy singleton so
        # production code is one line.
        self._llm = llm_client if llm_client is not None else get_llm_client()

    # ── Main entry point ─────────────────────────────────────────────────────

    def compose(self, result: ResultPackage) -> QueryResponse:
        """Turn a ResultPackage into a QueryResponse.

        The gateway fills in `session_id` after this returns."""
        if result.status == "clarification_needed":
            logger.debug("composer.mode mode=clarification")
            return self._compose_clarification(result)
        if result.status == "error":
            logger.debug("composer.mode mode=error")
            return self._compose_error(result)
        logger.debug("composer.mode mode=ok")
        return self._compose_answer(result)

    # ── Mode handlers ────────────────────────────────────────────────────────

    def _compose_clarification(self, result: ResultPackage) -> QueryResponse:
        """Return the clarification prompt verbatim. Never calls the LLM."""
        return QueryResponse(
            session_id="",
            answer_text=result.error_detail or "Could you clarify your question?",
            citations=[],
            status="clarification_needed",
        )

    def _compose_error(self, result: ResultPackage) -> QueryResponse:
        """Return a friendly error. Never calls the LLM."""
        return QueryResponse(
            session_id="",
            answer_text=_friendly_error_text(result.error_detail),
            citations=[],
            status="error",
        )

    def _compose_answer(self, result: ResultPackage) -> QueryResponse:
        """Compose an `ok` answer.

        Order:
          1. If the LLM is configured, ask it to present the data; on any
             failure, fall back to deterministic text.
          2. If the LLM is not configured, go straight to the deterministic
             fallback — the system must still serve the request."""
        citations = list(result.rag_result.citations) if result.rag_result else []

        if self._llm is None or not self._llm.is_configured():
            logger.debug("composer.llm status=skipped reason=not_configured")
            return self._fallback_response(result, citations)

        system, user = self._build_prompt(result)
        try:
            model = os.getenv("LLM_MODEL_COMPOSER") or None  # let chat() pick default
            answer = self._llm.chat(system, user, model=model, temperature=0.3)
        except LLMError as exc:
            logger.warning("composer.llm status=fallback reason=%s", exc.__class__.__name__)
            return self._fallback_response(result, citations)

        answer_text = (answer or "").strip()
        if not answer_text:
            logger.warning("composer.llm status=fallback reason=empty_response")
            return self._fallback_response(result, citations)

        logger.debug("composer.llm status=ok chars=%d", len(answer_text))
        return QueryResponse(
            session_id="",
            answer_text=answer_text,
            citations=citations,
            status="ok",
        )

    # ── Prompt construction ───────────────────────────────────────────────────

    def _build_prompt(self, result: ResultPackage) -> tuple[str, str]:
        """Build (system, user) prompts. The user prompt never carries PII."""
        system = (
            "You are PathFinder's presentation layer. Present the facts below "
            "as a clear, friendly answer to the student's question.\n\n"
            "STRICT RULES:\n"
            "1. Do NOT invent facts. Use ONLY the data provided.\n"
            "2. Do NOT decide whether the student is eligible, will graduate, "
            "can register, or is allowed to take a course. The system does "
            "not perform eligibility checks.\n"
            "3. Do NOT estimate GPA impact, credit limits, or graduation "
            "timelines.\n"
            "4. If RAG citations are present, end your answer with a single "
            "line: 'Sources: <Source>, p. <page>; ...' for each citation.\n"
            "5. If the curriculum data is empty for a field, say 'no "
            "information available' — never invent.\n"
            "6. Use plain prose; 1–4 short paragraphs. Use bullet points for "
            "course/skill/role lists.\n"
            "7. Refer to the curriculum graph as 'the curriculum' — do not "
            "mention KG, Neo4j, or other internals.\n"
        )

        kg_block = (
            json.dumps(result.kg_result, indent=2, default=str)
            if result.kg_result else "(none)"
        )
        rag_answer = result.rag_result.answer if result.rag_result else "(none)"
        rag_citations = (
            [(c.source, c.page) for c in result.rag_result.citations]
            if result.rag_result else []
        )
        student_summary = _sanitize_student_summary(result.student_context)

        user = (
            f"USER QUESTION:\n{result.original_query}\n\n"
            f"CURRICULUM DATA (KG):\n{kg_block}\n\n"
            f"HANDBOOK DATA (RAG):\n"
            f"ANSWER:\n{rag_answer}\n"
            f"CITATIONS:\n{rag_citations or '(none)'}\n\n"
            f"STUDENT (non-personal context):\n{student_summary or '(none)'}\n"
        )
        return system, user

    # ── Deterministic fallback ───────────────────────────────────────────────

    def _fallback_response(
        self,
        result: ResultPackage,
        citations: list[Citation],
    ) -> QueryResponse:
        """Render `ResultPackage` deterministically when the LLM is unavailable.

        The aim is *useful* output, not pretty prose. We render KG data in
        bullet form and pass any RAG answer through unchanged."""
        parts: list[str] = []

        kg_text = _format_kg_result(result.kg_result, result.engine_pattern)
        if kg_text:
            parts.append(kg_text)

        if result.rag_result and result.rag_result.answer:
            if parts:
                parts.append("")  # blank line between blocks
            parts.append(result.rag_result.answer.strip())

        if not parts:
            answer = (
                "I couldn't find anything to show for that question. "
                "Please try rephrasing."
            )
        else:
            answer = "\n".join(parts).strip()

        return QueryResponse(
            session_id="",
            answer_text=answer,
            citations=citations,
            status="ok",
        )


# ── Deterministic KG formatters ──────────────────────────────────────────────
#
# Each KG operation returns a slightly different dict shape. The functions
# below print the most useful subset of each shape as plain text. They are
# best-effort — anything missing is silently skipped.

def _format_kg_result(kg_result: Optional[dict], engine_pattern: str) -> str:
    if not kg_result or not isinstance(kg_result, dict):
        return ""
    # Detect by shape — there is no single discriminator field.
    if "course_code" in kg_result and "direct_prerequisites" in kg_result:
        return _format_prerequisites(kg_result)
    if "course_code" in kg_result and "skills_taught" in kg_result:
        return _format_skills_taught(kg_result)
    if "course_code" in kg_result and "credits" in kg_result:
        return _format_course_profile(kg_result)
    if "role_id" in kg_result and "missing_skills" in kg_result and "covered_skills" in kg_result:
        return _format_skill_gap(kg_result)
    if "role_id" in kg_result and "alignment_score" in kg_result and "newly_covered_skills" not in kg_result:
        return _format_alignment_score(kg_result)
    if "role_id" in kg_result and "newly_covered_skills" in kg_result:
        return _format_alignment_improvement(kg_result)
    if "role_id" in kg_result and "missing_skills" in kg_result:
        return _format_recommended_courses(kg_result)
    if "role_id" in kg_result and "required_skills" in kg_result:
        return _format_role_profile(kg_result)
    if "track_id" in kg_result and "courses" in kg_result and "supported_roles" in kg_result:
        return _format_track_overview(kg_result)
    if "ranked_roles" in kg_result:
        return _format_best_roles(kg_result)
    if "ranked_tracks" in kg_result and "role_id" in kg_result:
        return _format_track_for_role(kg_result)
    if "ranked_tracks" in kg_result and "skill_id" in kg_result:
        return _format_track_for_skill(kg_result)
    if "results" in kg_result and "queried_skills" in kg_result:
        return _format_search_by_skill(kg_result)
    if "track_id" in kg_result and "results" in kg_result:
        return _format_roles_by_track(kg_result)
    # Fallback — present the keys so the response is at least debuggable.
    keys = ", ".join(sorted(kg_result.keys()))
    return f"Curriculum data returned the following fields: {keys}."


def _format_course_profile(r: dict) -> str:
    name = r.get("name") or r.get("course_code", "")
    code = r.get("course_code", "")
    credits = r.get("credits")
    level = r.get("level")
    sem = ", ".join(r.get("semester_offering") or []) or "—"
    lines = [f"{code} — {name}"]
    if credits is not None:
        lines.append(f"- Credits: {credits}")
    if level is not None:
        lines.append(f"- Level: {level}")
    lines.append(f"- Offered in: {sem}")
    desc = (r.get("description") or "").strip()
    if desc:
        lines.append(f"- Description: {desc[:300]}{'…' if len(desc) > 300 else ''}")
    return "\n".join(lines)


def _format_prerequisites(r: dict) -> str:
    name = r.get("name", "")
    code = r.get("course_code", "")
    direct = r.get("direct_prerequisites") or []
    non_course = r.get("non_course_prerequisites") or []
    if not direct and not non_course:
        return f"{code} — {name}: no prerequisites recorded."
    lines = [f"Prerequisites for {code} — {name}:"]
    for p in direct:
        lines.append(f"- {p.get('course_code', '?')} — {p.get('name', '')}")
    for p in non_course:
        lines.append(f"- {p.get('type', '?')}: {p.get('value', '')}")
    return "\n".join(lines)


def _format_skills_taught(r: dict) -> str:
    code = r.get("course_code", "")
    name = r.get("name", "")
    skills = r.get("skills_taught") or []
    if not skills:
        return f"{code} — {name}: no skills recorded."
    lines = [f"Skills taught by {code} — {name}:"]
    for s in skills:
        lines.append(f"- {s.get('name', '?')}")
    return "\n".join(lines)


def _format_search_by_skill(r: dict) -> str:
    results = r.get("results") or []
    if not results:
        return "No courses match the requested skills."
    lines = ["Courses matching the requested skills:"]
    for c in results[:10]:
        lines.append(f"- {c.get('course_code', '?')} — {c.get('name', '')}")
    return "\n".join(lines)


def _format_role_profile(r: dict) -> str:
    name = r.get("role_name", "")
    skills = r.get("required_skills") or []
    if not skills:
        return f"Role {name}: no required skills recorded."
    lines = [f"{name} — required skills:"]
    for s in skills[:15]:
        tier = s.get("tier")
        suffix = f" ({tier})" if tier else ""
        lines.append(f"- {s.get('name', '?')}{suffix}")
    return "\n".join(lines)


def _format_roles_by_track(r: dict) -> str:
    track = r.get("track", "")
    roles = r.get("results") or []
    if not roles:
        return f"Track {track}: no roles found."
    lines = [f"Roles supported by {track}:"]
    for role in roles[:15]:
        lines.append(f"- {role.get('role_name', '?')}")
    return "\n".join(lines)


def _format_skill_gap(r: dict) -> str:
    name = r.get("role_name", "")
    missing = r.get("missing_skills") or []
    covered = r.get("covered_skills") or []
    lines = [
        f"Skill gap for {name}: "
        f"{len(covered)} covered, {len(missing)} missing."
    ]
    if missing:
        lines.append("Missing skills:")
        for s in missing[:10]:
            tier = s.get("tier")
            suffix = f" ({tier})" if tier else ""
            lines.append(f"- {s.get('name', '?')}{suffix}")
    return "\n".join(lines)


def _format_alignment_score(r: dict) -> str:
    name = r.get("role_name", "")
    pct = r.get("alignment_percentage")
    if pct is None:
        return f"Alignment with {name}: no data."
    return f"Your alignment with {name} is {pct}%."


def _format_alignment_improvement(r: dict) -> str:
    name = r.get("role_name", "")
    cur = r.get("current_alignment_percentage")
    proj = r.get("projected_alignment_percentage")
    delta = r.get("alignment_improvement")
    new = r.get("newly_covered_skills") or []
    lines = [
        f"Alignment with {name}: currently {cur}%, projected {proj}% "
        f"(change: {delta})."
    ]
    if new:
        lines.append("Newly covered skills:")
        for s in new[:10]:
            lines.append(f"- {s.get('name', '?')}")
    return "\n".join(lines)


def _format_recommended_courses(r: dict) -> str:
    name = r.get("role_name", "")
    missing = r.get("missing_skills") or []
    if not missing:
        return f"You already cover all required skills for {name}."
    lines = [f"Suggested courses to close the gap for {name}:"]
    for s in missing[:10]:
        taught_by = s.get("taught_by") or []
        if not taught_by:
            continue
        codes = ", ".join(c.get("course_code", "?") for c in taught_by[:3])
        lines.append(f"- {s.get('name', '?')}: {codes}")
    return "\n".join(lines)


def _format_best_roles(r: dict) -> str:
    ranked = r.get("ranked_roles") or []
    if not ranked:
        return "No roles aligned with the provided courses."
    lines = ["Top role matches based on your completed courses:"]
    for role in ranked[:5]:
        pct = role.get("alignment_percentage", "?")
        lines.append(f"- {role.get('role_name', '?')} ({pct}%)")
    return "\n".join(lines)


def _format_track_overview(r: dict) -> str:
    name = r.get("track_name", "")
    courses = r.get("courses") or []
    skills = r.get("skills_taught") or []
    roles = r.get("supported_roles") or []
    lines = [
        f"Track {name}: {len(courses)} courses, "
        f"{len(skills)} skills, {len(roles)} supported roles."
    ]
    if roles:
        lines.append("Supported roles:")
        for role in roles[:10]:
            lines.append(f"- {role.get('role_name', '?')}")
    return "\n".join(lines)


def _format_track_for_role(r: dict) -> str:
    name = r.get("role_name", "")
    ranked = r.get("ranked_tracks") or []
    if not ranked:
        return f"No track strongly aligns with {name}."
    lines = [f"Tracks ranked for {name}:"]
    for t in ranked[:5]:
        pct = t.get("alignment_percentage", "?")
        lines.append(f"- {t.get('track_name', '?')} ({pct}%)")
    return "\n".join(lines)


def _format_track_for_skill(r: dict) -> str:
    name = r.get("skill_name", "")
    ranked = r.get("ranked_tracks") or []
    if not ranked:
        return f"No track teaches {name}."
    lines = [f"Tracks ranked by coverage of {name}:"]
    for t in ranked[:5]:
        lines.append(f"- {t.get('track_name', '?')} ({t.get('course_count', 0)} courses)")
    return "\n".join(lines)
