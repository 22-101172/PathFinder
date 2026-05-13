"""
query_understanding.py  [T07 — Integration sprint implementation]
─────────────────────────────────────────────────────────────────
Hybrid (rule-based + LLM fallback) query classifier.

Responsibility (Blueprint §5.5):
  Transform raw user text + student context into a `StructuredQuery`.
  This is the ONLY component that interprets natural language for routing
  and override detection.

Design:
  Layer 1 — deterministic keyword rules. Covers the common, well-known
            phrasings and resolves entities against KG reference data
            (CSVs in `KG_DATA_DIR`). Fast, offline, and the only path used
            by unit tests by default.
  Layer 2 — LLM fallback for everything Layer 1 cannot classify confidently.
            Privacy: never sees student PII. Receives only the user text,
            the allowed intent list, the JSON output schema, alias hints
            from KG reference data, and non-personal session hints
            (last_referenced.* and overrides.target_role).

Hard boundaries (do not change without revisiting the plan):
  ✗ Does not call KG, RAG, Neo4j, or any engine
  ✗ Does not write to the session (SessionManager applies overrides)
  ✗ Does not generate user-facing answers (ResponseComposer does that)
  ✗ Does not access student PII inside `_build_llm_prompt`
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from gateway.kg_data import KGReferenceData, load_kg_reference_data
from gateway.llm_client import LLMClient, LLMError, parse_json_object
from gateway.models.schemas import (
    EntitySet,
    SessionOverrides,
    StructuredQuery,
    StudentContext,
)
from gateway.session_manager import SessionState as RuntimeSessionState

logger = logging.getLogger(__name__)


# ── Vocabulary tables ────────────────────────────────────────────────────────
#
# Ordered list so that more specific intents win over more generic ones.
# Each entry is `(intent, [keyword_or_phrase, ...])`. Matching is case-
# insensitive substring matching on the normalized text.
INTENT_RULES: list[tuple[str, list[str]]] = [
    ("get_prerequisites", [
        "prerequisite", "prereq", "before i take",
        "required for", "what do i need to take first",
        "what comes before",
    ]),
    ("get_skills_taught", [
        "skills taught", "what skills does", "what skills are taught",
        "what does this course teach", "skills from",
        "what will i learn in",
    ]),
    ("search_courses_by_skill", [
        "courses that teach", "which courses teach",
        "courses for skill", "course teaches", "which courses cover",
    ]),
    ("estimate_alignment_improvement", [
        "what if i take", "if i take", "if i complete",
        "after taking", "estimate improvement", "improve my alignment",
        "would my alignment", "would it improve",
    ]),
    ("recommend_courses_to_close_gap", [
        "which courses should i take", "courses to close",
        "recommend courses", "close the gap", "close my gap",
        "courses i should take",
    ]),
    ("compute_alignment_score", [
        "alignment score", "how aligned", "how close am i",
        "match score", "alignment percentage",
    ]),
    ("skill_gap_analysis", [
        "skill gap", "skills am i missing", "what am i missing",
        "what do i need for", "missing skills for",
        "what skills do i need for", "what skills do i still need",
    ]),
    ("role_recommendation", [
        "what role", "career match", "best role for me",
        "which job", "recommend a role", "recommend roles",
        "roles fit me", "which careers fit",
    ]),
    ("get_roles_by_track", [
        "roles in", "careers in", "jobs in the",
        "what roles can i get from", "roles from",
    ]),
    ("get_role_profile", [
        "what does a", "describe the role", "role profile",
        "skills required for", "what is a ", "what is an ",
    ]),
    ("recommend_track_for_role", [
        "best track for the role", "best track for becoming",
        "which track for the role", "track for becoming",
    ]),
    ("recommend_track_for_skill", [
        "best track to learn", "which track teaches",
        "track for the skill", "track to learn",
    ]),
    ("track_guidance", [
        "which track", "compare track", "best track",
        "track overview", "tell me about the track",
        "about the ai track", "about the dse track",
        "about the swe track", "about the cys track",
    ]),
    ("get_course_profile", [
        "tell me about", "what is the course", "describe the course",
        "course profile", "how many credits",
        "what is c-", "describe c-",
    ]),
    ("handbook_policy_query", [
        "rule", "policy", "regulation", "probation", "gpa requirement",
        "credit limit", "retake", "attendance", "grade appeal",
        "drop period", "minimum gpa", "withdraw",
        "academic standing rules", "handbook",
    ]),
]


# Trigger phrases that announce hypothetical course additions.
OVERRIDE_ADD_TRIGGERS: tuple[str, ...] = (
    "assume i", "if i add", "if i complete", "if i take",
    "what if i take", "pretend i", "suppose i",
    "imagine i took", "say i pass", "hypothetically",
)

# Trigger phrases that announce a new target role.
OVERRIDE_TARGET_ROLE_TRIGGERS: tuple[str, ...] = (
    "i want to become", "i want to be", "my target role is",
    "target role:", "target role is", "i'm aiming for",
    "i am aiming for", "i plan to be", "i plan to become",
)

# Phrases that imply the student is asking about themselves.
STUDENT_AWARE_PRONOUN_RE = re.compile(
    r"\b(i|i'm|i've|i'll|me|my|mine)\b", re.IGNORECASE,
)

# Phrases that imply the user is referring to an entity from a previous turn.
FOLLOWUP_PRONOUN_RE = re.compile(
    r"\b(its|it|that course|this course|that role|this role|that career|this career|"
    r"the previous one|the same)\b",
    re.IGNORECASE,
)

# Course-code regex tolerant of the variants seen in the curriculum:
#   HUM110, C-AI311, C-CS213, MTH101, AIN221, DSE301, SEN311, CYS301
COURSE_CODE_RE = re.compile(r"\b(?:C-)?[A-Z]{2,4}\d{2,4}[A-Z]?\b")

# Intents whose primary engine path is "kg".
KG_INTENTS: frozenset[str] = frozenset({
    "get_course_profile", "get_prerequisites", "get_skills_taught",
    "search_courses_by_skill", "get_role_profile", "get_roles_by_track",
    "skill_gap_analysis", "compute_alignment_score",
    "recommend_courses_to_close_gap", "estimate_alignment_improvement",
    "role_recommendation", "track_guidance",
    "recommend_track_for_role", "recommend_track_for_skill",
})

RAG_INTENTS: frozenset[str] = frozenset({"handbook_policy_query"})
MIXED_INTENTS: frozenset[str] = frozenset({"course_and_policy_query"})

# Intents that always require student context (independent of pronouns).
STUDENT_AWARE_INTENTS: frozenset[str] = frozenset({
    "skill_gap_analysis", "compute_alignment_score",
    "recommend_courses_to_close_gap", "estimate_alignment_improvement",
    "role_recommendation",
})

# Allowed values that Layer 2 (LLM) may return.
ALLOWED_INTENTS_FOR_LLM: frozenset[str] = (
    KG_INTENTS | RAG_INTENTS | MIXED_INTENTS | frozenset({"ambiguous"})
)
ALLOWED_ENGINE_PATTERNS: frozenset[str] = frozenset({"kg", "rag", "mixed"})
ALLOWED_QUERY_TYPES: frozenset[str] = frozenset({"student_aware", "non_student_aware"})


# ── Layer 1 helpers ──────────────────────────────────────────────────────────

def _resolve_engine_pattern(intent: str) -> str:
    if intent in KG_INTENTS:
        return "kg"
    if intent in RAG_INTENTS:
        return "rag"
    if intent in MIXED_INTENTS:
        return "mixed"
    return "kg"  # default for unknown / ambiguous intents


def _normalize_for_keyword_match(text: str) -> str:
    """Lowercase, collapse whitespace. Preserves digits and hyphens so course
    codes survive (`C-AI311` stays intact)."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _intent_has_keyword(text_lower: str, keywords: list[str]) -> bool:
    return any(kw in text_lower for kw in keywords)


def _looks_like_compare_tracks(text_lower: str) -> bool:
    """Detect the deferred compare_tracks pattern so we can clarify politely."""
    return (
        "compare track" in text_lower
        or "compare the track" in text_lower
        or ("compare" in text_lower and "tracks" in text_lower)
    )


def _is_mixed_intent(text_lower: str, kg_intent: Optional[str]) -> bool:
    """True when the same query mentions both a KG-style intent and a policy
    keyword (handbook, policy, rule, regulation, …). Mixed queries call both
    engines so we can give the student curriculum facts and handbook citations
    in one answer."""
    if kg_intent is None or kg_intent not in KG_INTENTS:
        return False
    policy_keywords = INTENT_RULES_DICT.get("handbook_policy_query", [])
    return _intent_has_keyword(text_lower, policy_keywords)


# Build a quick dict view of INTENT_RULES for in-rule reuse.
INTENT_RULES_DICT: dict[str, list[str]] = dict(INTENT_RULES)


# ── Class ────────────────────────────────────────────────────────────────────

class QueryUnderstandingLayer:
    """
    Two-layer query classifier.

    Layer 1 (rule-based) handles deterministic phrasings and resolves entities
    against `KGReferenceData`. Layer 2 (LLM) is consulted only when Layer 1
    cannot match an intent, and only if an `LLMClient` is configured.
    """

    def __init__(
        self,
        kg_data: Optional[KGReferenceData] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self._kg_data = kg_data if kg_data is not None else load_kg_reference_data()
        self._llm = llm_client

    # ── Main entry point ─────────────────────────────────────────────────────

    def classify(
        self,
        user_text: str,
        student_context: Optional[StudentContext] = None,
        session_state: Optional[RuntimeSessionState] = None,
    ) -> StructuredQuery:
        """
        Classify `user_text` into a `StructuredQuery`.

        Returns an ambiguous result with `needs_clarification=True` for empty
        or trivially short inputs. Never raises.
        """
        if user_text is None or not user_text.strip():
            return _ambiguous_with_prompt(
                "Please ask a specific question about a course, role, track, "
                "or handbook policy."
            )

        text = user_text.strip()

        # Layer 1: deterministic rules.
        try:
            result = self._rule_layer(text, student_context, session_state)
        except Exception:
            logger.exception("qu.rule_layer.unexpected_error")
            result = None

        if result is not None and not (
            result.needs_clarification and result.intent == "ambiguous"
        ):
            # Even on a successful rule match we still want override detection
            # so a single sentence can both add a course and ask a question.
            overrides = self._detect_overrides(text)
            if overrides.added_courses or overrides.target_role is not None:
                result = result.model_copy(update={"session_overrides": overrides})
            _log_classification(result, layer="rule")
            return result

        # Layer 2: LLM fallback.
        llm_result = self._llm_layer(text, student_context, session_state)
        if llm_result is not None:
            overrides = self._detect_overrides(text)
            if overrides.added_courses or overrides.target_role is not None:
                llm_result = llm_result.model_copy(update={"session_overrides": overrides})
            _log_classification(llm_result, layer="llm")
            return llm_result

        # Final fallback: emit a clarification with whatever prompt the rule
        # layer surfaced, or a generic one. Override detection still applies —
        # a sentence like "I want to become a Data Scientist." has no intent
        # we can act on, but it does carry a target-role change the
        # SessionManager should record.
        fallback = result if result is not None else _ambiguous_with_prompt(
            "Could you rephrase that? For example, ask about a specific course "
            "code, role name, track, or handbook rule."
        )
        overrides = self._detect_overrides(text)
        if overrides.added_courses or overrides.target_role is not None:
            fallback = fallback.model_copy(update={"session_overrides": overrides})
        _log_classification(fallback, layer="fallback")
        return fallback

    # ── Layer 1: Rule-based ──────────────────────────────────────────────────

    def _rule_layer(
        self,
        text: str,
        context: Optional[StudentContext],
        session_state: Optional[RuntimeSessionState],
    ) -> Optional[StructuredQuery]:
        """Return a StructuredQuery if the rule layer is confident, else None.

        Returns an *ambiguous* StructuredQuery (not None) when we matched an
        intent but cannot resolve required entities — that way the caller
        does not waste an LLM round-trip on a clearly ambiguous prompt."""

        lower = _normalize_for_keyword_match(text)

        # Deferred feature: compare_tracks. We detect it explicitly and ask the
        # user to pick a single track for now.
        if _looks_like_compare_tracks(lower):
            return _ambiguous_with_prompt(
                "I can describe one track at a time right now. "
                "Which track would you like to know about?"
            )

        intent = self._detect_intent(lower)
        if intent is None:
            return None

        entities = self._extract_entities(text, session_state)

        # Promote to mixed if the same query also hits handbook keywords.
        is_mixed = (
            intent in KG_INTENTS
            and intent != "handbook_policy_query"
            and _is_mixed_intent(lower, intent)
        )
        if is_mixed:
            effective_intent = "course_and_policy_query"
            engine_pattern = "mixed"
        else:
            effective_intent = intent
            engine_pattern = _resolve_engine_pattern(intent)

        student_aware = self._detect_student_awareness(lower, effective_intent)
        query_type = "student_aware" if student_aware else "non_student_aware"

        # Validate required entities. If something necessary is missing we
        # short-circuit to a clarification rather than calling the engines.
        clarification = _validate_required_entities(
            effective_intent, entities, context
        )
        if clarification is not None:
            return clarification

        overrides = self._detect_overrides(text)

        return StructuredQuery(
            intent=effective_intent,
            engine_pattern=engine_pattern,
            query_type=query_type,
            entities=entities,
            needs_clarification=False,
            clarification_prompt=None,
            session_overrides=overrides,
        )

    def _detect_intent(self, text_lower: str) -> Optional[str]:
        """Return the first matching intent string, scanning specific → generic."""
        for intent, keywords in INTENT_RULES:
            if _intent_has_keyword(text_lower, keywords):
                return intent
        return None

    def _determine_engine_pattern(self, intent: str) -> str:
        """Public helper kept for symmetry with the original scaffold."""
        return _resolve_engine_pattern(intent)

    def _extract_entities(
        self,
        text: str,
        session_state: Optional[RuntimeSessionState],
    ) -> EntitySet:
        """
        Pull out course_code, role_id, track_id, skill_id.

        Resolution order:
          1. Course-code regex against KG data
          2. Course name (longest substring) against KG data
          3. Role name (alias → longest substring)
          4. Track name (bare code → longest substring)
          5. Skill (alias → longest substring)
          6. Session fallback for pronoun references ("it", "that role", …)
        """
        course_code = self._extract_course_code(text)
        if course_code is None:
            course_code = self._kg_data.resolve_course_name(text)

        role_id = self._kg_data.resolve_role(text)
        track_id = self._kg_data.resolve_track(text)
        skill_id = self._kg_data.resolve_skill(text)

        # Session-based follow-up resolution. Only kicks in when nothing was
        # detected directly AND the user used a pronoun / determiner.
        if session_state is not None and FOLLOWUP_PRONOUN_RE.search(text):
            last_ref = session_state.last_referenced
            if course_code is None and last_ref.get("course_code"):
                course_code = last_ref["course_code"]
            if role_id is None and last_ref.get("role_id"):
                role_id = last_ref["role_id"]

        # If the user references "my target role" in any form, prefer the
        # overrides slot. SessionManager's overrides are dicts.
        if session_state is not None:
            overrides = getattr(session_state, "overrides", None) or {}
            if role_id is None and overrides.get("target_role"):
                if "target role" in text.lower() or "my target" in text.lower():
                    role_id = overrides["target_role"]

        return EntitySet(
            course_code=course_code,
            role_id=role_id,
            track_id=track_id,
            skill_id=skill_id,
        )

    def _extract_course_code(self, text: str) -> Optional[str]:
        """Find a course code that appears verbatim in the KG data.

        We tolerate both `C-AI311` and `AI311` styles by checking the matched
        token against the canonical course list as-is, then a `C-` prefix
        variant, and finally a stripped variant."""
        for match in COURSE_CODE_RE.finditer(text):
            token = match.group(0)
            if token in self._kg_data.courses:
                return token
            if not token.startswith("C-") and f"C-{token}" in self._kg_data.courses:
                return f"C-{token}"
            if token.startswith("C-") and token[2:] in self._kg_data.courses:
                return token[2:]
        return None

    def _detect_student_awareness(self, text_lower: str, intent: str) -> bool:
        """An intent in the student-aware set always wins. Otherwise we look
        for first-person pronouns in the query text."""
        if intent in STUDENT_AWARE_INTENTS:
            return True
        return bool(STUDENT_AWARE_PRONOUN_RE.search(text_lower))

    # ── Override detection ────────────────────────────────────────────────────

    def _detect_overrides(self, text: str) -> SessionOverrides:
        """Extract hypothetical course additions and target-role changes.

        Course detection is done by trigger-phrase + course-code/name scan.
        We accept any course code or name from the trigger position to the
        end of the sentence (`.`, `;`, `,`, newline)."""
        text_l = text.lower()
        added_codes: list[str] = []

        for trigger in OVERRIDE_ADD_TRIGGERS:
            idx = text_l.find(trigger)
            while idx != -1:
                # Window: from trigger to the next sentence boundary.
                end_candidates = [
                    text.find(b, idx + len(trigger)) for b in (".", ";", "\n", "?")
                ]
                end_candidates = [c for c in end_candidates if c != -1]
                end = min(end_candidates) if end_candidates else len(text)
                window = text[idx + len(trigger) : end]

                # 1) Course codes
                for match in COURSE_CODE_RE.finditer(window):
                    token = match.group(0)
                    resolved = self._extract_course_code(token)
                    if resolved and resolved not in added_codes:
                        added_codes.append(resolved)

                # 2) Course names — longest substring match in window
                name_match = self._kg_data.resolve_course_name(window)
                if name_match and name_match not in added_codes:
                    added_codes.append(name_match)

                idx = text_l.find(trigger, end)

        # Target role detection.
        target_role: Optional[str] = None
        for trigger in OVERRIDE_TARGET_ROLE_TRIGGERS:
            idx = text_l.find(trigger)
            if idx == -1:
                continue
            end_candidates = [
                text.find(b, idx + len(trigger)) for b in (".", ";", "\n", "?", ",")
            ]
            end_candidates = [c for c in end_candidates if c != -1]
            end = min(end_candidates) if end_candidates else len(text)
            window = text[idx + len(trigger) : end]
            resolved = self._kg_data.resolve_role(window)
            if resolved:
                target_role = resolved
                break

        return SessionOverrides(
            added_courses=added_codes,
            target_role=target_role,
        )

    # ── Layer 2: LLM fallback ─────────────────────────────────────────────────

    def _llm_layer(
        self,
        text: str,
        context: Optional[StudentContext],
        session_state: Optional[RuntimeSessionState],
    ) -> Optional[StructuredQuery]:
        """Call the configured LLM to classify a query the rule layer missed.

        Returns None on any failure path so the caller falls back to a
        deterministic ambiguous response — we never let an LLM error break
        `/query`. Privacy: the prompt never receives student PII."""
        if self._llm is None or not self._llm.is_configured():
            logger.debug("qu.llm_fallback.skipped reason=not_configured")
            return None

        system, user = self._build_llm_prompt(text, session_state)
        try:
            raw = self._llm.chat(system, user, json_mode=True)
        except LLMError as exc:
            logger.warning("qu.llm_fallback.failed reason=%s", exc.__class__.__name__)
            return None

        try:
            obj = parse_json_object(raw)
        except LLMError:
            logger.warning("qu.llm_fallback.failed reason=bad_json")
            return None

        return self._validate_and_build_from_llm(obj, context)

    def _build_llm_prompt(
        self,
        text: str,
        session_state: Optional[RuntimeSessionState],
    ) -> tuple[str, str]:
        """Construct (system, user) prompts.

        PII rule: the user prompt contains the literal user text, the alias
        hints, and only `last_referenced` + `overrides.target_role` from
        session state. It never includes student name, student_id, cgpa,
        course history, or grades."""
        allowed_intents = ", ".join(sorted(ALLOWED_INTENTS_FOR_LLM))
        system = (
            "You are a query classifier for an academic advising backend. "
            "You output ONE JSON object only.\n\n"
            f"Allowed intents: {allowed_intents}.\n"
            f"Engine pattern in: {sorted(ALLOWED_ENGINE_PATTERNS)}.\n"
            f"Query type in: {sorted(ALLOWED_QUERY_TYPES)}.\n\n"
            "Output JSON schema:\n"
            "{\n"
            '  "intent": "...",\n'
            '  "engine_pattern": "kg|rag|mixed",\n'
            '  "query_type": "student_aware|non_student_aware",\n'
            '  "entities": {"course_code": null|"...",'
            ' "role_id": null|"RL_*",'
            ' "track_id": null|"AI|DSE|SWE|CYS",'
            ' "skill_id": null|"SK_*"},\n'
            '  "needs_clarification": false,\n'
            '  "clarification_prompt": null,\n'
            '  "session_overrides": {"added_courses": [], "target_role": null}\n'
            "}\n\n"
            "Resolve role/skill phrases to the IDs in the alias hints. If you "
            "cannot resolve an entity, leave it null. If the query is too "
            "vague, set needs_clarification=true and write one short "
            "clarification_prompt sentence. Output JSON only — no prose, no "
            "markdown."
        )

        # Compact alias hints — keep the list small to avoid token bloat.
        role_hints = _format_alias_hints(self._kg_data.roles, limit=20)
        track_hints = _format_alias_hints(self._kg_data.tracks, limit=10)
        skill_hints = _format_alias_hints(self._kg_data.skills, limit=20)

        # Non-personal session hints only.
        session_hints = {}
        if session_state is not None:
            last_ref = session_state.last_referenced or {}
            if last_ref.get("course_code"):
                session_hints["last_course_code"] = last_ref["course_code"]
            if last_ref.get("role_id"):
                session_hints["last_role_id"] = last_ref["role_id"]
            overrides = session_state.overrides or {}
            if overrides.get("target_role"):
                session_hints["target_role"] = overrides["target_role"]

        user = (
            f"USER QUERY:\n{text}\n\n"
            f"ROLES (name => id):\n{role_hints}\n\n"
            f"TRACKS (name => id):\n{track_hints}\n\n"
            f"SKILLS (name => id):\n{skill_hints}\n\n"
            f"SESSION HINTS (non-personal): {session_hints}\n"
        )
        return system, user

    def _validate_and_build_from_llm(
        self,
        obj: dict,
        context: Optional[StudentContext],
    ) -> Optional[StructuredQuery]:
        """Validate LLM JSON and project it into a `StructuredQuery`.

        Returns None when the LLM gave us something we cannot trust."""
        intent = obj.get("intent")
        if intent not in ALLOWED_INTENTS_FOR_LLM:
            logger.warning("qu.llm_fallback.failed reason=invalid_intent value=%r", intent)
            return None

        engine_pattern = obj.get("engine_pattern") or _resolve_engine_pattern(intent)
        if engine_pattern not in ALLOWED_ENGINE_PATTERNS:
            engine_pattern = _resolve_engine_pattern(intent)

        query_type = obj.get("query_type")
        if query_type not in ALLOWED_QUERY_TYPES:
            query_type = (
                "student_aware" if intent in STUDENT_AWARE_INTENTS else "non_student_aware"
            )

        raw_entities = obj.get("entities") or {}
        entities = EntitySet(
            course_code=_safe_str(raw_entities.get("course_code")),
            role_id=_safe_str(raw_entities.get("role_id")),
            track_id=_safe_str(raw_entities.get("track_id")),
            skill_id=_safe_str(raw_entities.get("skill_id")),
        )
        # Cross-check entities against KG reference data; drop unknown ids
        # rather than passing them to the KG layer where they would error.
        if entities.course_code and entities.course_code not in self._kg_data.courses:
            entities = entities.model_copy(update={"course_code": None})
        if entities.role_id and entities.role_id not in self._kg_data.roles:
            entities = entities.model_copy(update={"role_id": None})
        if entities.track_id and entities.track_id not in self._kg_data.tracks:
            entities = entities.model_copy(update={"track_id": None})
        if entities.skill_id and entities.skill_id not in self._kg_data.skills:
            entities = entities.model_copy(update={"skill_id": None})

        needs_clar = bool(obj.get("needs_clarification"))
        clar_prompt = obj.get("clarification_prompt")
        if not isinstance(clar_prompt, str) or not clar_prompt.strip():
            clar_prompt = None

        # If clarification is needed but the LLM gave no prompt, supply one.
        if needs_clar and clar_prompt is None:
            clar_prompt = "Could you give a more specific question?"

        # Validate required entities once more (covers the case where the LLM
        # filled in a plausible intent but no entity).
        clarification = _validate_required_entities(intent, entities, context)
        if clarification is not None:
            return clarification

        return StructuredQuery(
            intent=intent,
            engine_pattern=engine_pattern,
            query_type=query_type,
            entities=entities,
            needs_clarification=needs_clar,
            clarification_prompt=clar_prompt,
            session_overrides=SessionOverrides(),  # overrides come from rule layer
        )


# ── Module-level utilities ───────────────────────────────────────────────────

def _safe_str(value) -> Optional[str]:
    """Coerce an LLM-provided value to a non-empty string or None."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return None


def _format_alias_hints(mapping: dict[str, str], *, limit: int) -> str:
    """Format a dict as `name => id` lines, capped at `limit` rows."""
    lines: list[str] = []
    for ident, name in mapping.items():
        if not name:
            continue
        lines.append(f"  {name} => {ident}")
        if len(lines) >= limit:
            break
    return "\n".join(lines) if lines else "  (none)"


def _ambiguous_with_prompt(prompt: str) -> StructuredQuery:
    """Build a uniform ambiguous `StructuredQuery` with a clarification prompt."""
    return StructuredQuery(
        intent="ambiguous",
        engine_pattern="kg",
        query_type="non_student_aware",
        entities=EntitySet(),
        needs_clarification=True,
        clarification_prompt=prompt,
        session_overrides=SessionOverrides(),
    )


def _validate_required_entities(
    intent: str,
    entities: EntitySet,
    context: Optional[StudentContext],
) -> Optional[StructuredQuery]:
    """If a required entity is missing, return an ambiguous clarification.

    Returning None signals that the caller may proceed with the
    classification as-is."""
    # Student-aware intents need a student record; without one we cannot
    # answer regardless of how well-formed the query is.
    if intent in STUDENT_AWARE_INTENTS and context is None:
        return _ambiguous_with_prompt(
            "I need your academic record to answer that. Please sign in first."
        )

    # Course-centric intents
    if intent in {
        "get_course_profile", "get_prerequisites", "get_skills_taught",
    } and not entities.course_code:
        return _ambiguous_with_prompt(
            "Which course are you asking about? Please include the course code "
            "(e.g., C-AI311) or its full name."
        )

    # Role-centric intents that need an explicit role
    if intent in {
        "get_role_profile", "skill_gap_analysis", "compute_alignment_score",
        "recommend_courses_to_close_gap", "estimate_alignment_improvement",
        "recommend_track_for_role",
    } and not entities.role_id:
        return _ambiguous_with_prompt(
            "Which target role should I evaluate? Try, for example, "
            "'for Data Scientist' or 'for Software Engineer'."
        )

    # Track-centric intents
    if intent == "get_roles_by_track" and not entities.track_id:
        return _ambiguous_with_prompt(
            "Which track do you mean? Try 'AI', 'DSE', 'SWE', or 'CYS'."
        )
    if intent == "recommend_track_for_skill" and not entities.skill_id:
        return _ambiguous_with_prompt(
            "Which skill should I consider? Try a skill name like 'Machine Learning'."
        )

    # estimate_alignment_improvement also needs at least one planned course.
    if intent == "estimate_alignment_improvement":
        planned = (context.planned_courses if context is not None else []) or []
        added = entities.course_code  # the rule layer may attach planned via overrides
        if not planned and not added:
            return _ambiguous_with_prompt(
                "Tell me which courses to assume you've taken, for example "
                "'what if I take C-AI311 and C-AI321?'."
            )

    return None


def _log_classification(result: StructuredQuery, *, layer: str) -> None:
    """One-liner log with no PII for traceability."""
    logger.info(
        "qu.classified layer=%s intent=%s engine=%s type=%s clarification=%s "
        "has_course=%s has_role=%s has_track=%s has_skill=%s overrides_added=%d "
        "overrides_target=%s",
        layer,
        result.intent,
        result.engine_pattern,
        result.query_type,
        result.needs_clarification,
        bool(result.entities.course_code),
        bool(result.entities.role_id),
        bool(result.entities.track_id),
        bool(result.entities.skill_id),
        len(result.session_overrides.added_courses),
        bool(result.session_overrides.target_role),
    )
