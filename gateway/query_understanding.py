"""
Query Understanding — orchestration entry point.

Converts a raw user message into an ordered list[StructuredQuery] for the Orchestrator.
QU is a parser/classifier only: it does not call ALE, RAG, or KG business operations.
Entity resolution via KG resolve_entity is the only KG call QU may make.

Privacy: never sends student_id, name, grades, CGPA, transcript, or full
StudentContext to any LLM.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from gateway.llm_client import get_llm_client, LLMNotConfigured
from gateway.models.schemas import EntitySet, LastReferenced, SessionOverrides, StructuredQuery
from gateway.qu_intents import LOCKED_INTENTS
from gateway.qu_llm_chain import QUModelChain, AllModelsFailedError
from gateway.qu_preprocessing import (
    COURSE_CODE_RE,
    PreprocessResult,
    detect_policy_signal,
    detect_out_of_scope,
    preprocess,
)
from gateway.qu_prompt import build_system_prompt, build_user_message

logger = logging.getLogger(__name__)

# Callable type alias for KG resolve_entity injection
Resolver = Callable[[str, str], dict]

_VALID_OVERRIDE_TYPES = frozenset({
    "planned", "assumed_done", "assumed_failed", "assumed_passed", "gpa_scenario", "none",
})
_VALID_OVERRIDE_ACTIONS = frozenset({"accumulate", "replace", "clear"})


# ── Public API ────────────────────────────────────────────────────────────────

def understand_query(
    user_text: str,
    last_referenced: LastReferenced,
    recent_turns: list[dict],
    resolver: Resolver | None = None,
) -> list[StructuredQuery]:
    """
    Parse user_text into an ordered list[StructuredQuery].

    Args:
        user_text: raw student message
        last_referenced: last referenced course/role/track from session
        recent_turns: recent conversation history (compact, no PII sent to LLM)
        resolver: optional KG entity resolver; (entity_type, entity_text) -> dict
                  If None, entity resolution is skipped (LLM extraction is trusted).

    Returns:
        Non-empty list[StructuredQuery]. Never raises; always returns at least one SQ.
    """
    logger.info("QU: classifying [%d chars]", len(user_text))

    pre = preprocess(user_text)
    sq_list = _classify(user_text, last_referenced, recent_turns, pre)

    if resolver is not None:
        sq_list = _resolve_all(sq_list, resolver)

    if not sq_list:
        sq_list = [_clarification("Could you clarify your question?")]

    logger.info("QU: %d SQ(s): %s", len(sq_list), [sq.intent for sq in sq_list])
    return sq_list


# ── Classification ────────────────────────────────────────────────────────────

def _classify(
    user_text: str,
    last_referenced: LastReferenced,
    recent_turns: list[dict],
    pre: PreprocessResult,
) -> list[StructuredQuery]:
    try:
        client = get_llm_client()
        if not client.is_configured():
            raise LLMNotConfigured("LLM not configured")

        chain = QUModelChain(client)
        raw_list = chain.call(
            system=build_system_prompt(),
            user_msg=build_user_message(user_text, last_referenced, recent_turns),
            valid_intents=LOCKED_INTENTS,
        )
        return [_parse_raw_sq(r, user_text) for r in raw_list if r]

    except AllModelsFailedError:
        logger.warning("QU: all LLMs failed — deterministic fallback")
    except LLMNotConfigured:
        logger.warning("QU: LLM not configured — deterministic fallback")
    except Exception as exc:
        logger.error("QU: unexpected error during LLM call: %s", type(exc).__name__)

    return _deterministic_fallback(user_text, pre)


# ── LLM Output Parsing ────────────────────────────────────────────────────────

def _parse_raw_sq(raw: dict[str, Any], fallback_text: str) -> StructuredQuery:
    """Convert one raw LLM-output dict to a StructuredQuery."""
    intent = raw.get("intent", "clarification_needed")
    if intent not in LOCKED_INTENTS:
        intent = "clarification_needed"

    original_text = raw.get("original_text") or fallback_text

    entities_raw = raw.get("entities") or {}
    entities = EntitySet(
        course_code=_nonempty(entities_raw.get("course_code")),
        role_id=_nonempty(entities_raw.get("role") or entities_raw.get("role_id")),
        track_id=_nonempty(entities_raw.get("track") or entities_raw.get("track_id")),
        skill_id=_nonempty(entities_raw.get("skill") or entities_raw.get("skill_id")),
    )

    sec_raw = raw.get("secondary_entities")
    secondary: EntitySet | None = None
    if isinstance(sec_raw, dict):
        sec = EntitySet(
            course_code=_nonempty(sec_raw.get("course_code")),
            role_id=_nonempty(sec_raw.get("role") or sec_raw.get("role_id")),
            track_id=_nonempty(sec_raw.get("track") or sec_raw.get("track_id")),
            skill_id=_nonempty(sec_raw.get("skill") or sec_raw.get("skill_id")),
        )
        if any([sec.course_code, sec.role_id, sec.track_id, sec.skill_id]):
            secondary = sec

    params: dict[str, Any] = raw.get("params") or {}

    ov_raw = raw.get("session_overrides") or {}
    session_overrides = SessionOverrides(
        added_courses=_str_list(ov_raw.get("added_courses")),
        assumed_passed_courses=_str_list(ov_raw.get("assumed_passed_courses")),
        assumed_failed_courses=_str_list(ov_raw.get("assumed_failed_courses")),
        target_role=_nonempty(ov_raw.get("target_role")),
        course_override_type=_safe_lit(
            ov_raw.get("course_override_type"), _VALID_OVERRIDE_TYPES, "none"
        ),
        override_action=_safe_lit(
            ov_raw.get("override_action"), _VALID_OVERRIDE_ACTIONS, "accumulate"
        ),
    )

    student_referential = bool(raw.get("student_referential_fallback", False))

    return StructuredQuery(
        intent=intent,
        original_text=original_text,
        entities=entities,
        secondary_entities=secondary,
        params=params,
        session_overrides=session_overrides,
        student_referential_fallback=student_referential,
    )


# ── Deterministic Fallback ────────────────────────────────────────────────────

def _deterministic_fallback(user_text: str, pre: PreprocessResult) -> list[StructuredQuery]:
    """Best-effort classification without LLM. Never crashes."""
    lower = user_text.lower()

    # Out-of-scope wins only when no policy signal overlaps
    if pre.out_of_scope_signal and not pre.policy_signal:
        return [StructuredQuery(intent="out_of_scope", original_text=user_text)]

    if pre.policy_signal:
        return [StructuredQuery(intent="policy_query", original_text=user_text)]

    if pre.course_codes:
        code = pre.course_codes[0]
        if any(kw in lower for kw in ("can i take", "am i eligible", "eligible for", "can i register")):
            return [StructuredQuery(
                intent="check_course_eligibility",
                original_text=user_text,
                entities=EntitySet(course_code=code),
                student_referential_fallback=True,
            )]
        if any(kw in lower for kw in ("prerequisite", "prereq", "what do i need before", "requirements for")):
            return [StructuredQuery(
                intent="get_course_prerequisites",
                original_text=user_text,
                entities=EntitySet(course_code=code),
            )]
        return [StructuredQuery(
            intent="get_course_info",
            original_text=user_text,
            entities=EntitySet(course_code=code),
        )]

    if pre.student_referential:
        if any(kw in lower for kw in ("graduate", "graduation", "how many credits")):
            return [StructuredQuery(
                intent="run_graduation_audit",
                original_text=user_text,
                student_referential_fallback=True,
            )]
        if any(kw in lower for kw in ("plan", "what should i take", "next semester", "recommend courses")):
            return [StructuredQuery(
                intent="plan_semester",
                original_text=user_text,
                student_referential_fallback=True,
            )]
        if any(kw in lower for kw in ("my record", "my progress", "my snapshot", "show my")):
            return [StructuredQuery(
                intent="get_student_record",
                original_text=user_text,
                student_referential_fallback=True,
            )]

    return [_clarification(
        "I couldn't understand your question. Could you clarify? "
        "I can help with courses, graduation planning, career guidance, or track information."
    )]


# ── Entity Resolution ─────────────────────────────────────────────────────────

def _resolve_all(sq_list: list[StructuredQuery], resolver: Resolver) -> list[StructuredQuery]:
    return [_resolve_sq(sq, resolver) for sq in sq_list]


def _resolve_sq(sq: StructuredQuery, resolver: Resolver) -> StructuredQuery:
    """Resolve entities in one SQ. Returns clarification_needed if a critical entity fails."""
    entities = sq.entities
    failures: list[str] = []

    course_code, fail = _resolve_course(entities.course_code, resolver)
    if fail:
        failures.append(fail)

    role_id, fail = _resolve_entity("role", entities.role_id, resolver)
    if fail:
        failures.append(fail)

    track_id, fail = _resolve_entity("track", entities.track_id, resolver)
    if fail:
        failures.append(fail)

    skill_id, fail = _resolve_entity("skill", entities.skill_id, resolver)
    if fail:
        failures.append(fail)

    if failures:
        return _clarification(failures[0])

    new_entities = EntitySet(
        course_code=course_code,
        role_id=role_id,
        track_id=track_id,
        skill_id=skill_id,
    )

    # Resolve secondary entities (track only for compare_tracks)
    new_secondary: EntitySet | None = None
    if sq.secondary_entities is not None:
        sec = sq.secondary_entities
        sec_track, fail = _resolve_entity("track", sec.track_id, resolver)
        if fail:
            return _clarification(fail)
        new_secondary = EntitySet(
            course_code=sec.course_code,
            role_id=sec.role_id,
            track_id=sec_track,
            skill_id=sec.skill_id,
        )

    return sq.model_copy(update={
        "entities": new_entities,
        "secondary_entities": new_secondary,
    })


def _resolve_course(
    mention: str | None,
    resolver: Resolver,
) -> tuple[str | None, str | None]:
    if not mention:
        return None, None
    # Already a canonical course code — trust it
    if COURSE_CODE_RE.fullmatch(mention.strip().upper()):
        return mention.strip().upper(), None
    # Name/alias — resolve via KG
    return _resolve_entity("course", mention, resolver)


def _resolve_entity(
    entity_type: str,
    mention: str | None,
    resolver: Resolver,
) -> tuple[str | None, str | None]:
    """Returns (resolved_id_or_None, failure_message_or_None)."""
    if not mention:
        return None, None
    try:
        result = resolver(entity_type, mention)
    except Exception as exc:
        logger.warning("QU: resolver error %s=%r: %s", entity_type, mention, type(exc).__name__)
        return mention, None  # degrade gracefully

    status = result.get("status")
    if status == "ok":
        resolved_id = result.get("resolved_id") or result.get("id") or result.get("entity_id")
        return resolved_id, None

    if status == "ambiguous":
        opts = [m.get("id") or m.get("name", "") for m in result.get("matches", [])[:5]]
        return None, f"Which {entity_type} did you mean? Options: {', '.join(str(o) for o in opts)}"

    if status in ("not_found", "error", "unsupported_entity_type", "empty_entity_text"):
        return None, f"I couldn't find a {entity_type} matching '{mention}'. Could you provide the exact name or ID?"

    # Unknown status — degrade gracefully
    logger.warning("QU: resolver unknown status %r for %s=%r", status, entity_type, mention)
    return mention, None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clarification(prompt: str) -> StructuredQuery:
    return StructuredQuery(
        intent="clarification_needed",
        original_text=prompt,
        params={"clarification_prompt": prompt},
    )


def _nonempty(val: Any) -> str | None:
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def _str_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x) for x in val if x]
    return []


def _safe_lit(val: Any, valid: frozenset[str], default: str) -> str:
    return val if isinstance(val, str) and val in valid else default
