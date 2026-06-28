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
import re
import time
from typing import Any, Callable

from gateway.llm_client import get_llm_client, LLMNotConfigured
from gateway.models.schemas import EntitySet, LastReferenced, SessionOverrides, StructuredQuery
from gateway.qu_intents import LOCKED_INTENTS
from gateway.qu_llm_chain import QUModelChain, AllModelsFailedError
import os

from gateway.qu_preprocessing import (
    COURSE_CODE_RE,
    STRICT_COURSE_CODE_RE,
    PreprocessResult,
    detect_d6_record_focus,
    detect_d6_response_style,
    detect_d2_course_info_verb,
    detect_d2_prereq_verb,
    detect_d2_skills_taught_verb,
    detect_d2_skill_search_verb,
    extract_d2_course_candidate,
    extract_d2_skill_candidate,
    detect_policy_signal,
    detect_out_of_scope,
    preprocess,
)

_TRACE = os.getenv("PATHFINDER_TRACE", "").lower() in ("true", "1", "yes")
from gateway.qu_prompt import build_system_prompt, build_user_message

logger = logging.getLogger(__name__)

# Callable type alias for KG resolve_entity injection
Resolver = Callable[[str, str], dict]

_VALID_OVERRIDE_TYPES = frozenset({
    "planned", "assumed_done", "assumed_failed", "assumed_passed", "gpa_scenario", "none",
})
_VALID_OVERRIDE_ACTIONS = frozenset({"accumulate", "replace", "clear"})

_VALID_SEMESTER_TYPES: dict[str, str] = {"fall": "Fall", "spring": "Spring", "summer": "Summer"}
_SEMESTER_FORMAT_RE = re.compile(r'^(fall|spring|summer)\s+(\d{4})$', re.IGNORECASE)

_VALID_RECORD_FOCUSES: frozenset[str] = frozenset({
    "full_record", "progress_summary", "completed_courses", "in_progress_courses",
    "failed_courses", "failed_course_history", "completed_credits", "cgpa", "last_semester_gpa",
    "academic_level", "academic_standing", "academic_warnings", "probation_status",
    "study_status", "track", "current_semester", "course_status_check",
    "assumption_acknowledgement", "reset_assumptions",
})

_VALID_RESPONSE_STYLES: frozenset[str] = frozenset({
    "normal", "concise", "only", "yes_no", "one_sentence",
})


# ── Public API ────────────────────────────────────────────────────────────────

def understand_query(
    user_text: str,
    last_referenced: LastReferenced,
    recent_turns: list[dict],
    resolver: Resolver | None = None,
    trace_id: str = "",
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
    _t0 = time.perf_counter()
    logger.info(
        "QU.start trace_id=%s query_len=%d resolver_enabled=%s "
        "recent_turns=%d last_refs=%s",
        trace_id,
        len(user_text),
        resolver is not None,
        len(recent_turns),
        {"course": bool(last_referenced.course_code), "role": bool(last_referenced.role_id),
         "track": bool(last_referenced.track_id), "skill": bool(last_referenced.skill_id)},
    )

    pre = preprocess(user_text)
    logger.info(
        "QU.preprocess trace_id=%s detected_course_codes_count=%d policy=%s "
        "out_of_scope_signal=%s student_referential_signal=%s semester_signal=%s "
        "target_cgpa_signal=%s what_if_signal=%s reset_assumptions_signal=%s "
        "expected_grade_pairs_count=%d",
        trace_id,
        len(pre.course_codes),
        pre.policy_signal,
        pre.out_of_scope_signal,
        pre.student_referential,
        pre.semester is not None,
        pre.target_cgpa is not None,
        pre.override_signal,
        pre.reset_signal,
        len(pre.expected_grades),
    )

    # Build prompts once for size diagnostics and chain call
    _system_prompt = build_system_prompt()
    _user_message = build_user_message(user_text, last_referenced, recent_turns)
    _sys_chars = len(_system_prompt)
    _user_chars = len(_user_message)
    _total_chars = _sys_chars + _user_chars
    _est_tokens = (_total_chars + 3) // 4
    logger.info(
        "QU.prompt_size trace_id=%s system_prompt_chars=%d user_message_chars=%d "
        "total_prompt_chars=%d estimated_input_tokens=%d "
        "recent_turns_count=%d has_recent_turns=%s "
        "last_refs_present={course=%s role=%s track=%s skill=%s} "
        "resolver_enabled=%s",
        trace_id, _sys_chars, _user_chars, _total_chars, _est_tokens,
        len(recent_turns), bool(recent_turns),
        bool(last_referenced.course_code), bool(last_referenced.role_id),
        bool(last_referenced.track_id), bool(last_referenced.skill_id),
        resolver is not None,
    )

    sq_list, classification_source = _classify(
        user_text, last_referenced, recent_turns, pre, trace_id,
        system=_system_prompt, user_msg=_user_message,
    )
    sq_count_before = len(sq_list)

    if resolver is not None:
        sq_list = _resolve_all(sq_list, resolver)
    else:
        sq_list = _filter_unresolved(sq_list)

    _log_resolution_summary(sq_count_before, sq_list, resolver is not None, trace_id)

    if _TRACE:
        for n, sq in enumerate(sq_list):
            eg = sq.params.get("expected_grades", {})
            logger.info(
                "QU.sq_trace[%d] trace_id=%s intent=%s source=%s\n"
                "  original_text: %s\n"
                "  entities: course=%s role=%s track=%s skill=%s\n"
                "  params: %s\n"
                "  expected_grades_count: %d\n"
                "  session_overrides: action=%s passed=%s failed=%s added=%s\n"
                "  student_referential: %s",
                n, trace_id, sq.intent, classification_source,
                (sq.original_text or "")[:120],
                sq.entities.course_code, sq.entities.role_id,
                sq.entities.track_id, sq.entities.skill_id,
                {k: v for k, v in sq.params.items() if k not in ("expected_grades",)},
                len(eg) if isinstance(eg, dict) else 0,
                sq.session_overrides.override_action,
                sq.session_overrides.assumed_passed_courses,
                sq.session_overrides.assumed_failed_courses,
                sq.session_overrides.added_courses,
                sq.student_referential_fallback,
            )

    if not sq_list:
        sq_list = [_clarification("Could you clarify your question?")]

    duration_ms = int((time.perf_counter() - _t0) * 1000)
    logger.info(
        "QU.result trace_id=%s sq_count=%d intents=%s source=%s "
        "resolver_enabled=%s duration_ms=%d",
        trace_id,
        len(sq_list),
        [sq.intent for sq in sq_list],
        classification_source,
        resolver is not None,
        duration_ms,
    )
    return sq_list


# ── Classification ────────────────────────────────────────────────────────────

def _classify(
    user_text: str,
    last_referenced: LastReferenced,
    recent_turns: list[dict],
    pre: PreprocessResult,
    trace_id: str = "",
    system: str | None = None,
    user_msg: str | None = None,
) -> tuple[list[StructuredQuery], str]:
    source = "deterministic_fallback_unexpected_error"
    try:
        client = get_llm_client()
        if not client.is_configured():
            raise LLMNotConfigured("LLM not configured")

        if system is None:
            system = build_system_prompt()
        if user_msg is None:
            user_msg = build_user_message(user_text, last_referenced, recent_turns)

        chain = QUModelChain(client)
        raw_list = chain.call(
            system=system,
            user_msg=user_msg,
            valid_intents=LOCKED_INTENTS,
            trace_id=trace_id,
        )
        sq_list = [_parse_raw_sq(r, user_text) for r in raw_list if r]
        sq_list = _normalize_structured_queries_after_llm(sq_list, user_text, pre, last_referenced)
        return sq_list, "llm"

    except AllModelsFailedError:
        logger.warning("QU: all LLMs failed — deterministic fallback")
        source = "deterministic_fallback_all_models_failed"
    except LLMNotConfigured:
        logger.warning("QU: LLM not configured — deterministic fallback")
        source = "deterministic_fallback_llm_not_configured"
    except Exception as exc:
        logger.error("QU: unexpected error during LLM call: %s", type(exc).__name__)

    return _deterministic_fallback(user_text, pre), source


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
    # Accept target_cgpa as alias for target_gpa; normalize both to float in [0.0, 4.0]
    if "target_cgpa" in params and "target_gpa" not in params:
        params["target_gpa"] = params.pop("target_cgpa")
    elif "target_cgpa" in params:
        del params["target_cgpa"]
    if "target_gpa" in params:
        try:
            val = float(params["target_gpa"])
            if 0.0 <= val <= 4.0:
                params["target_gpa"] = val
            else:
                del params["target_gpa"]
        except (ValueError, TypeError):
            del params["target_gpa"]
    if "depth" in params:
        d = str(params["depth"]).lower()
        if d in ("all", "complete", "entire"):
            d = "full"
        if d not in ("direct", "full"):
            d = "direct"
        params["depth"] = d
    if "expected_grades" in params:
        if not isinstance(params["expected_grades"], dict):
            del params["expected_grades"]
    if "planned_courses" in params:
        if isinstance(params["planned_courses"], list):
            params["planned_courses"] = [str(c) for c in params["planned_courses"] if c]
        else:
            del params["planned_courses"]
    # Semester params normalization
    if "target_semester_type" in params:
        val = str(params["target_semester_type"]).strip().lower()
        if val in _VALID_SEMESTER_TYPES:
            params["target_semester_type"] = _VALID_SEMESTER_TYPES[val]
        else:
            del params["target_semester_type"]
    if "target_semester" in params:
        val = str(params["target_semester"]).strip()
        m = _SEMESTER_FORMAT_RE.match(val)
        if m:
            params["target_semester"] = f"{_VALID_SEMESTER_TYPES[m.group(1).lower()]} {m.group(2)}"
        else:
            del params["target_semester"]
    if "semester_resolution_source" in params:
        val = str(params["semester_resolution_source"]).strip().lower()
        if val in ("explicit", "relative"):
            params["semester_resolution_source"] = val
        else:
            del params["semester_resolution_source"]
    if "target_semester_text" in params:
        val = str(params["target_semester_text"]).strip()
        if val:
            params["target_semester_text"] = val
        else:
            del params["target_semester_text"]
    if "record_focus" in params:
        if params["record_focus"] not in _VALID_RECORD_FOCUSES:
            del params["record_focus"]
    if "response_style" in params:
        if params["response_style"] not in _VALID_RESPONSE_STYLES:
            del params["response_style"]

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


# ── Post-LLM Normalization Layer ──────────────────────────────────────────────

_D2_COURSE_INTENTS = frozenset({"get_course_info", "get_course_prerequisites", "get_skills_taught"})
_D2_SKILL_INTENTS = frozenset({"search_courses_by_skill"})


def _normalize_one_sq(
    sq: StructuredQuery,
    user_text: str,
    pre: PreprocessResult,
    last_referenced: LastReferenced,
) -> StructuredQuery:
    """Repair common shape issues in a single LLM-produced StructuredQuery."""
    try:
        intent = sq.intent
        text_for_extraction = sq.original_text or user_text

        # D2: course entity promotion for course-info intents
        if intent in _D2_COURSE_INTENTS and not sq.entities.course_code:
            candidate = None
            # Try entity_candidates first (from LLM — priority order)
            ec = sq.params.get("entity_candidates")
            if isinstance(ec, list) and ec:
                candidate = str(ec[0]).strip()
            # Then legacy param keys
            if not candidate:
                for param_key in ("course_name", "course", "target_course", "entity", "topic"):
                    if sq.params.get(param_key):
                        candidate = str(sq.params[param_key]).strip()
                        break
            # Fall back to text extraction
            if not candidate:
                candidate = extract_d2_course_candidate(text_for_extraction)
            if candidate:
                new_entities = sq.entities.model_copy(update={"course_code": candidate})
                new_params = {**sq.params, "attempted_candidate": candidate}
                sq = sq.model_copy(update={"entities": new_entities, "params": new_params})

        # D2: skill entity promotion for skill-search intent
        elif intent in _D2_SKILL_INTENTS and not sq.entities.skill_id:
            candidate = None
            # Try skill_candidates first (from LLM)
            sc = sq.params.get("skill_candidates")
            if isinstance(sc, list) and sc:
                candidate = str(sc[0]).strip()
            # Then entity_candidates
            if not candidate:
                ec = sq.params.get("entity_candidates")
                if isinstance(ec, list) and ec:
                    candidate = str(ec[0]).strip()
            # Then legacy param keys
            if not candidate:
                for param_key in ("skill_name", "skill", "entity", "topic"):
                    if sq.params.get(param_key):
                        candidate = str(sq.params[param_key]).strip()
                        break
            if not candidate:
                candidate = extract_d2_skill_candidate(text_for_extraction)
            if candidate:
                new_entities = sq.entities.model_copy(update={"skill_id": candidate})
                new_params = {**sq.params, "attempted_skill": candidate}
                sq = sq.model_copy(update={"entities": new_entities, "params": new_params})

        # D6: fill missing record_focus from deterministic detection
        elif intent == "get_student_record":
            if not sq.params.get("record_focus"):
                focus = detect_d6_record_focus(text_for_extraction)
                if focus and focus in _VALID_RECORD_FOCUSES:
                    new_params = {**sq.params, "record_focus": focus}
                    sq = sq.model_copy(update={"params": new_params})
            if not sq.params.get("response_style"):
                style = detect_d6_response_style(text_for_extraction)
                if style and style != "normal":
                    new_params = {**sq.params, "response_style": style}
                    sq = sq.model_copy(update={"params": new_params})

    except Exception:
        pass  # Never crash normalization; return original sq
    return sq


def _normalize_structured_queries_after_llm(
    sqs: list[StructuredQuery],
    user_text: str,
    pre: PreprocessResult,
    last_referenced: LastReferenced,
) -> list[StructuredQuery]:
    """Post-LLM repair: promote entity candidates, fix common shape issues."""
    result = []
    for sq in sqs:
        sq = _normalize_one_sq(sq, user_text, pre, last_referenced)
        result.append(sq)
    return result


# ── Deterministic Fallback ────────────────────────────────────────────────────

def _deterministic_fallback(user_text: str, pre: PreprocessResult) -> list[StructuredQuery]:
    """Best-effort classification without LLM. Never crashes."""
    lower = user_text.lower()

    # Out-of-scope wins only when no policy signal overlaps
    if pre.out_of_scope_signal and not pre.policy_signal:
        return [StructuredQuery(intent="out_of_scope", original_text=user_text)]

    if pre.reset_signal:
        return [StructuredQuery(
            intent="get_student_record",
            original_text=user_text,
            params={"record_focus": "reset_assumptions", "response_style": "normal"},
            session_overrides=SessionOverrides(override_action="clear"),
            student_referential_fallback=True,
        )]

    # Assumption-only: override signal present but no follow-up question → acknowledgement
    if pre.override_signal and not pre.reset_signal:
        focus = detect_d6_record_focus(user_text)
        if focus == "assumption_acknowledgement":
            return [StructuredQuery(
                intent="get_student_record",
                original_text=user_text,
                params={"record_focus": "assumption_acknowledgement", "response_style": "normal"},
                student_referential_fallback=True,
            )]

    if pre.policy_signal and not pre.override_signal:
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
            depth = "full" if any(kw in lower for kw in ("full", "complete", "entire", "recursive", "all prereq")) else "direct"
            return [StructuredQuery(
                intent="get_course_prerequisites",
                original_text=user_text,
                entities=EntitySet(course_code=code),
                params={"depth": depth},
            )]
        # Course status check when student-referential
        if pre.student_referential:
            focus = detect_d6_record_focus(user_text)
            if focus == "course_status_check":
                style = detect_d6_response_style(user_text)
                params: dict = {"record_focus": "course_status_check"}
                if style != "normal":
                    params["response_style"] = style
                return [StructuredQuery(
                    intent="get_student_record",
                    original_text=user_text,
                    entities=EntitySet(course_code=code),
                    params=params,
                    student_referential_fallback=True,
                )]
        return [StructuredQuery(
            intent="get_course_info",
            original_text=user_text,
            entities=EntitySet(course_code=code),
        )]

    # D2: natural language course-info without explicit course code
    # Guard: skip D2 when the extracted candidate is a student-personal phrase (starts with "my ")
    # — e.g. "what is my cgpa?" must not route to get_course_info
    if detect_d2_course_info_verb(lower) or detect_d2_prereq_verb(lower) or detect_d2_skills_taught_verb(lower):
        candidate = extract_d2_course_candidate(user_text)
        if candidate and not candidate.lower().startswith("my "):
            if detect_d2_prereq_verb(lower):
                depth = "full" if any(kw in lower for kw in ("full", "complete", "entire", "chain", "all prereq", "recursive")) else "direct"
                return [StructuredQuery(
                    intent="get_course_prerequisites",
                    original_text=user_text,
                    entities=EntitySet(course_code=candidate),
                    params={"depth": depth, "attempted_candidate": candidate},
                )]
            elif detect_d2_skills_taught_verb(lower):
                return [StructuredQuery(
                    intent="get_skills_taught",
                    original_text=user_text,
                    entities=EntitySet(course_code=candidate),
                    params={"attempted_candidate": candidate},
                )]
            else:
                return [StructuredQuery(
                    intent="get_course_info",
                    original_text=user_text,
                    entities=EntitySet(course_code=candidate),
                    params={"attempted_candidate": candidate},
                )]

    if detect_d2_skill_search_verb(lower):
        candidate = extract_d2_skill_candidate(user_text)
        if candidate:
            return [StructuredQuery(
                intent="search_courses_by_skill",
                original_text=user_text,
                entities=EntitySet(skill_id=candidate),
                params={"attempted_skill": candidate},
            )]

    if pre.student_referential:
        if any(kw in lower for kw in ("graduate", "graduation", "how many credits left to graduate")):
            return [StructuredQuery(
                intent="run_graduation_audit",
                original_text=user_text,
                student_referential_fallback=True,
            )]
        if any(kw in lower for kw in ("plan", "what should i take", "next semester", "recommend courses", "schedule")):
            return [StructuredQuery(
                intent="plan_semester",
                original_text=user_text,
                student_referential_fallback=True,
            )]
        # Check for D6 focus
        focus = detect_d6_record_focus(user_text)
        if focus or any(kw in lower for kw in ("my record", "my progress", "my snapshot", "show my", "my courses", "my level", "my cgpa", "my gpa", "my standing", "my track", "my semester", "my status")):
            actual_focus = focus or "progress_summary"
            style = detect_d6_response_style(user_text)
            params = {"record_focus": actual_focus}
            if style != "normal":
                params["response_style"] = style
            return [StructuredQuery(
                intent="get_student_record",
                original_text=user_text,
                params=params,
                student_referential_fallback=True,
            )]

    # Even without a student_referential signal, if D6 focus is clearly detectable
    # (e.g. "only cgpa pls", "what courses have I completed so far?"), route to get_student_record
    focus = detect_d6_record_focus(user_text)
    if focus:
        style = detect_d6_response_style(user_text)
        params = {"record_focus": focus}
        if style != "normal":
            params["response_style"] = style
        return [StructuredQuery(
            intent="get_student_record",
            original_text=user_text,
            params=params,
            student_referential_fallback=True,
        )]

    return [_clarification(
        "I couldn't understand your question. Could you clarify? "
        "I can help with courses, graduation planning, career guidance, or track information."
    )]


# ── Entity Resolution ─────────────────────────────────────────────────────────

# Canonical track IDs recognized without a resolver
_CANONICAL_TRACKS: frozenset[str] = frozenset({"AI", "CYS", "DSE", "SWE", "GEN"})


def _log_resolution_summary(
    sq_count_before: int,
    sq_list: list[StructuredQuery],
    resolver_enabled: bool,
    trace_id: str = "",
) -> None:
    clar_count = sum(1 for sq in sq_list if sq.intent == "clarification_needed")
    oos_count = sum(1 for sq in sq_list if sq.intent == "out_of_scope")
    course_count = sum(1 for sq in sq_list if sq.entities.course_code)
    role_count = sum(1 for sq in sq_list if sq.entities.role_id)
    track_count = sum(1 for sq in sq_list if sq.entities.track_id)
    skill_count = sum(1 for sq in sq_list if sq.entities.skill_id)
    params_keys = sorted({k for sq in sq_list for k in sq.params})
    overrides_active = any(
        sq.session_overrides.added_courses
        or sq.session_overrides.assumed_passed_courses
        or sq.session_overrides.assumed_failed_courses
        or sq.session_overrides.override_action != "accumulate"
        or sq.session_overrides.course_override_type != "none"
        for sq in sq_list
    )
    override_actions = sorted({
        sq.session_overrides.override_action
        for sq in sq_list
        if sq.session_overrides.override_action != "accumulate"
    })
    logger.info(
        "QU.resolve trace_id=%s resolver=%s "
        "structured_queries_before_resolution=%d structured_queries_after_resolution=%d "
        "clarification=%d out_of_scope_count=%d "
        "resolved_entity_counts={course=%d role=%d track=%d skill=%d} "
        "structured_query_param_keys=%s "
        "overrides_active=%s override_actions=%s",
        trace_id, resolver_enabled,
        sq_count_before, len(sq_list), clar_count, oos_count,
        course_count, role_count, track_count, skill_count,
        params_keys, overrides_active, override_actions,
    )


def _filter_unresolved(sq_list: list[StructuredQuery]) -> list[StructuredQuery]:
    """
    When the KG resolver is unavailable, null out entity fields that are not already
    in canonical form. Prevents raw natural-language names (e.g. 'Operating Systems',
    'Data Scientist') from reaching the Orchestrator/KG where they would always fail.

    Canonical forms accepted without resolver:
    - course_code: matches STRICT_COURSE_CODE_RE (C-XXXNNN)
    - role_id:     starts with 'RL_'
    - track_id:    one of {AI, CYS, DSE, SWE, GEN}
    - skill_id:    starts with 'SK_'

    session_overrides and params.expected_grades are left untouched — their
    non-canonical values silently fail downstream, which is the same degraded
    behavior as before any resolver is wired.
    """
    result = []
    for sq in sq_list:
        e = sq.entities
        new_entities = EntitySet(
            course_code=(
                e.course_code if (e.course_code and STRICT_COURSE_CODE_RE.fullmatch(e.course_code.strip().upper()))
                else None
            ),
            role_id=e.role_id if (e.role_id and e.role_id.startswith("RL_")) else None,
            track_id=(
                e.track_id if (e.track_id and e.track_id.upper() in _CANONICAL_TRACKS)
                else None
            ),
            skill_id=e.skill_id if (e.skill_id and e.skill_id.startswith("SK_")) else None,
        )

        new_secondary: EntitySet | None = None
        if sq.secondary_entities is not None:
            sec = sq.secondary_entities
            filtered_sec = EntitySet(
                course_code=(
                    sec.course_code if (sec.course_code and STRICT_COURSE_CODE_RE.fullmatch(sec.course_code.strip().upper()))
                    else None
                ),
                role_id=sec.role_id if (sec.role_id and sec.role_id.startswith("RL_")) else None,
                track_id=(
                    sec.track_id if (sec.track_id and sec.track_id.upper() in _CANONICAL_TRACKS)
                    else None
                ),
                skill_id=sec.skill_id if (sec.skill_id and sec.skill_id.startswith("SK_")) else None,
            )
            if any([filtered_sec.course_code, filtered_sec.role_id, filtered_sec.track_id, filtered_sec.skill_id]):
                new_secondary = filtered_sec

        if new_entities != e or new_secondary != sq.secondary_entities:
            sq = sq.model_copy(update={"entities": new_entities, "secondary_entities": new_secondary})
        result.append(sq)
    return result


def _resolve_all(sq_list: list[StructuredQuery], resolver: Resolver) -> list[StructuredQuery]:
    return [_resolve_sq(sq, resolver) for sq in sq_list]


def _resolve_sq(sq: StructuredQuery, resolver: Resolver) -> StructuredQuery:
    """Resolve entities in one SQ. Returns clarification_needed if a critical entity fails."""
    entities = sq.entities
    failures: list[str] = []
    failure_info: list[tuple[str, str | None]] = []  # (entity_type, resolver_status)

    # ── Course resolution with entity_candidates fallback ─────────────────────
    course_code, fail, fail_status = _resolve_course(entities.course_code, resolver)
    if fail:
        # Try entity_candidates in priority order before giving up
        entity_candidates: list[str] = []
        raw_ec = sq.params.get("entity_candidates")
        if isinstance(raw_ec, list):
            entity_candidates = [str(c).strip() for c in raw_ec if c and str(c).strip()]
        # Skip the primary mention (already tried via _resolve_course)
        primary_mention = (entities.course_code or "").strip()
        for cand in entity_candidates:
            if cand == primary_mention:
                continue
            res_cand, fail_cand, _ = _resolve_entity("course", cand, resolver)
            if res_cand:
                course_code = res_cand
                fail = None
                fail_status = None
                logger.info(
                    "QU.resolve_candidate_success intent=%s candidate=%r resolved=%r",
                    sq.intent, cand, res_cand,
                )
                break
        # If course still failed but intent is course-info — try skill fallback
        if fail and sq.intent in _D2_COURSE_INTENTS:
            skill_fallback_mention = primary_mention or (entity_candidates[0] if entity_candidates else None)
            if skill_fallback_mention:
                res_skill, fail_skill, _ = _resolve_entity("skill", skill_fallback_mention, resolver)
                if res_skill:
                    logger.info(
                        "QU.topic_fallback intent=%s mention=%r skill=%r -> search_courses_by_skill",
                        sq.intent, skill_fallback_mention, res_skill,
                    )
                    # Reroute: course-intent → search_courses_by_skill with resolved skill
                    new_entities = EntitySet(skill_id=res_skill)
                    new_params = {
                        **sq.params,
                        "topic_fallback": True,
                        "original_intent": sq.intent,
                        "attempted_candidate": primary_mention,
                    }
                    return sq.model_copy(update={
                        "intent": "search_courses_by_skill",
                        "entities": new_entities,
                        "params": new_params,
                    })
        if fail:
            failures.append(fail)
            failure_info.append(("course", fail_status))

    role_id, fail, fail_status = _resolve_entity("role", entities.role_id, resolver)
    if fail:
        failures.append(fail)
        failure_info.append(("role", fail_status))

    track_id, fail, fail_status = _resolve_entity("track", entities.track_id, resolver)
    if fail:
        failures.append(fail)
        failure_info.append(("track", fail_status))

    # ── Skill resolution with skill_candidates fallback ───────────────────────
    skill_id, fail, fail_status = _resolve_entity("skill", entities.skill_id, resolver)
    if fail:
        # Try skill_candidates in priority order
        skill_candidates: list[str] = []
        raw_sc = sq.params.get("skill_candidates")
        if isinstance(raw_sc, list):
            skill_candidates = [str(s).strip() for s in raw_sc if s and str(s).strip()]
        primary_skill = (entities.skill_id or "").strip()
        for cand in skill_candidates:
            if cand == primary_skill:
                continue
            res_cand, fail_cand, _ = _resolve_entity("skill", cand, resolver)
            if res_cand:
                skill_id = res_cand
                fail = None
                fail_status = None
                logger.info(
                    "QU.resolve_skill_candidate_success intent=%s candidate=%r resolved=%r",
                    sq.intent, cand, res_cand,
                )
                break
        if fail:
            failures.append(fail)
            failure_info.append(("skill", fail_status))

    if failures:
        et, st = failure_info[0]
        logger.warning(
            "QU.resolve_failed intent=%s entity_type=%s status=%s -> clarification_needed",
            sq.intent, et, st,
        )
        return _clarification(failures[0])

    new_added = []
    for c in sq.session_overrides.added_courses:
        res, fail, _ = _resolve_course(c, resolver)
        if fail: failures.append(fail)
        elif res: new_added.append(res)

    new_passed = []
    for c in sq.session_overrides.assumed_passed_courses:
        res, fail, _ = _resolve_course(c, resolver)
        if fail: failures.append(fail)
        elif res: new_passed.append(res)

    new_failed = []
    for c in sq.session_overrides.assumed_failed_courses:
        res, fail, _ = _resolve_course(c, resolver)
        if fail: failures.append(fail)
        elif res: new_failed.append(res)

    new_grades = {}
    for k, v in sq.params.get("expected_grades", {}).items():
        res, fail, _ = _resolve_course(k, resolver)
        if fail: failures.append(fail)
        elif res: new_grades[res] = str(v)

    if failures:
        logger.warning(
            "QU.resolve_failed intent=%s entity_type=course status=resolution_failed "
            "-> clarification_needed",
            sq.intent,
        )
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
        sec_track, fail, fail_status = _resolve_entity("track", sec.track_id, resolver)
        if fail:
            logger.warning(
                "QU.resolve_failed intent=%s entity_type=track status=%s -> clarification_needed",
                sq.intent, fail_status,
            )
            return _clarification(fail)
        new_secondary = EntitySet(
            course_code=sec.course_code,
            role_id=sec.role_id,
            track_id=sec_track,
            skill_id=sec.skill_id,
        )

    new_params = sq.params.copy()
    if new_grades or "expected_grades" in new_params:
        new_params["expected_grades"] = new_grades

    # Multi-skill: for search_courses_by_skill, resolve all skill_candidates and store resolved IDs
    if sq.intent == "search_courses_by_skill":
        raw_sc = sq.params.get("skill_candidates")
        if isinstance(raw_sc, list) and raw_sc:
            resolved_skill_ids: list[str] = []
            if skill_id:
                resolved_skill_ids.append(skill_id)
            primary_skill = (entities.skill_id or "").strip()
            for cand in raw_sc:
                cand = str(cand).strip()
                if not cand or cand == primary_skill:
                    continue
                res_cand, _, _ = _resolve_entity("skill", cand, resolver)
                if res_cand and res_cand not in resolved_skill_ids:
                    resolved_skill_ids.append(res_cand)
            if resolved_skill_ids:
                new_params["resolved_skill_ids"] = resolved_skill_ids

    return sq.model_copy(update={
        "entities": new_entities,
        "secondary_entities": new_secondary,
        "params": new_params,
        "session_overrides": sq.session_overrides.model_copy(update={
            "added_courses": new_added,
            "assumed_passed_courses": new_passed,
            "assumed_failed_courses": new_failed,
        })
    })


def _resolve_course(
    mention: str | None,
    resolver: Resolver,
) -> tuple[str | None, str | None, str | None]:
    if not mention:
        return None, None, None
    # Only bypass resolver for strict canonical course-code forms with the C- prefix
    if STRICT_COURSE_CODE_RE.fullmatch(mention.strip().upper()):
        return mention.strip().upper(), None, None
    # Name/alias — resolve via KG
    return _resolve_entity("course", mention, resolver)


def _resolve_entity(
    entity_type: str,
    mention: str | None,
    resolver: Resolver,
) -> tuple[str | None, str | None, str | None]:
    """Returns (resolved_id_or_None, failure_message_or_None, resolver_status_or_None)."""
    if not mention:
        return None, None, None
    try:
        result = resolver(entity_type, mention)
    except Exception as exc:
        logger.warning("QU: resolver error entity_type=%s exc=%s", entity_type, type(exc).__name__)
        return None, f"I encountered an error looking up {entity_type} '{mention}'.", "exception"

    status = result.get("status")
    if status == "ok":
        resolved_id = result.get("resolved_id") or result.get("id") or result.get("entity_id")
        if not resolved_id:
            return None, f"I couldn't uniquely identify the {entity_type} '{mention}'.", "error"
        return resolved_id, None, None

    if status == "ambiguous":
        opts = [m.get("id") or m.get("name", "") for m in result.get("matches", [])[:5]]
        return None, f"Which {entity_type} did you mean? Options: {', '.join(str(o) for o in opts)}", "ambiguous"

    if status in ("not_found", "error", "unsupported_entity_type", "empty_entity_text"):
        return None, f"I couldn't find a {entity_type} matching '{mention}'. Could you provide the exact name or ID?", "not_found"

    # Unknown status — degrade gracefully
    logger.warning("QU: resolver unknown status=%r entity_type=%s", status, entity_type)
    return None, f"I couldn't verify the {entity_type} '{mention}'.", "unknown_status"


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
