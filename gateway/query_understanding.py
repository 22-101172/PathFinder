from __future__ import annotations
import logging
import re
from typing import Optional

from gateway.llm_client import get_llm_client, parse_json_object, LLMError
from gateway.models.schemas import EntitySet, LastReferenced, SessionOverrides, StructuredQuery

logger = logging.getLogger(__name__)

QU_SYSTEM_PROMPT = """You are the Query Understanding layer of PathFinder, an academic advising system for Egyptian University of Informatics (EUI).

Your ONLY job is to classify the student's query and output a JSON object. Output ONLY the JSON. No explanation.

OUTPUT FORMAT:
{
  "intent": "<intent_name>",
  "engine_pattern": "<kg|rag|ale|mixed|clarification>",
  "query_type": "<student_aware|general>",
  "entities": {
    "course_code": "<CODE or null>",
    "role_id": "<role_id or null>",
    "track_id": "<track_id or null>",
    "skill_id": "<skill_id or null>"
  },
  "secondary_entities": {
    "course_code": null,
    "role_id": null,
    "track_id": "<second track for compare or null>",
    "skill_id": null
  },
  "needs_clarification": false,
  "clarification_prompt": null,
  "session_overrides": {
    "added_courses": [],
    "target_role": null
  }
}

INTENTS:

KG INTENTS (engine_pattern="kg"):
- get_course_profile: asks about a course's info, credits, description, level. e.g. "tell me about C-CS301", "what is Algorithms", "info on Machine Learning"
- get_prerequisites: asks what is needed before a course. e.g. "prerequisites for C-CS301", "what do I need before taking OS"
- get_skills_taught: asks what skills a course teaches. e.g. "what does C-AI311 teach", "skills in Deep Learning"
- search_courses_by_skill: asks which courses teach a skill. e.g. "courses that teach Python", "what teaches NLP skills"
- get_role_profile: asks about a career role. e.g. "tell me about Data Scientist", "what is a cybersecurity analyst"
- get_roles_by_track: asks what careers a track leads to. e.g. "what jobs can I get with AI track", "careers in Cyber Security"
- compute_skill_gap: asks what skills student is missing for a role (student_aware). e.g. "what am I missing to become a Data Scientist"
- compute_alignment_score: asks student's match % with a role (student_aware). e.g. "how aligned am I with ML Engineer", "my match with Software Engineer"
- recommend_courses_to_close_gap: asks which courses to take for a role (student_aware). e.g. "what courses should I take for Data Scientist"
- estimate_alignment_improvement: asks how planned courses improve role alignment (student_aware). e.g. "if I take C-AI311 how does my Data Scientist alignment improve"
- find_best_matching_roles: asks what careers match student best (student_aware). e.g. "what careers suit me", "best job matches for me", "what roles fit my profile"
- get_track_overview: asks for track overview. e.g. "tell me about AI track", "overview of Cyber Security"
- compare_tracks: compares two tracks. e.g. "compare AI and CS", "AI vs Cyber Security", "difference between Data Science and SW tracks"
- recommend_track_for_role: asks which track fits a role. e.g. "which track for Data Scientist", "best track for ML Engineer"
- recommend_track_for_skill: asks which track teaches a skill. e.g. "which track teaches most Python", "best track for deep learning skills"

ALE INTENTS (engine_pattern="ale"):
- check_eligibility: asks if student can take a course (student_aware). e.g. "can I take C-CS401", "am I eligible for Operating Systems", "can I register for C-AI421"
- run_graduation_audit: asks about graduation status (student_aware). e.g. "can I graduate", "how many credits left", "am I on track to graduate", "graduation audit"
- generate_semester_plan: asks for next semester plan (student_aware). e.g. "plan my semester", "what should I take next", "recommend courses for next term"
- simulate_gpa: asks to simulate GPA impact (student_aware). e.g. "if I get A in C-CS301 what is my GPA", "GPA simulation", "what if I get all Bs next semester"

RAG INTENTS (engine_pattern="rag"):
- handbook_query: asks about university rules, policies, regulations, attendance, warning system, grading, academic calendar, probation. e.g. "what is the warning policy", "how many absences allowed", "grading system", "what happens if I fail twice"

MIXED INTENTS (engine_pattern="mixed"):
- mixed_course_policy: asks about a course AND a related policy together. e.g. "tell me about C-CS301 and what happens if I fail it"

CLARIFICATION (engine_pattern="clarification"):
- Only when query is completely ambiguous and no intent is determinable. Ask ONE specific clarifying question.

ENTITY EXTRACTION:
- course_code: Extract exact code if present (C-CS301, C-AI421, HUM011). If student uses name only (e.g. "Algorithms"), put null — orchestrator handles name lookup.
- track_id: Normalize to one of: "AI", "CS", "Cyber", "Data Science", "SW", "General". Map: "artificial intelligence"→"AI", "cyber"/"cybersecurity"→"Cyber", "data science"→"Data Science", "software engineering"→"SW", "computer science"→"CS"
- role_id: Extract as lowercase with underscores e.g. "data_scientist", "ml_engineer", "software_engineer", "cybersecurity_analyst"
- skill_id: Extract skill name as given
- secondary_entities: ONLY for compare_tracks — put second track_id here
- query_type: "student_aware" for all ALE intents and KG intents that use student history; "general" for all others

SESSION OVERRIDES:
- "assume I took X" / "pretend I completed X" → add X to added_courses
- Student mentions target career role → set target_role

MULTI-TURN RESOLUTION:
When student says "it", "that course", "this role", "the same track" — use the provided context references to resolve.
"""

_COURSE_CODE_RE = re.compile(r'\b([A-Z]+-?[A-Z]*\d{2,4}[A-Z]?)\b')


def _pre_extract_code(text: str) -> Optional[str]:
    matches = _COURSE_CODE_RE.findall(text.upper())
    return matches[0] if matches else None


def _build_user_msg(user_text: str, last_referenced: LastReferenced, recent_turns: list[dict]) -> str:
    parts = []
    if recent_turns:
        conv = "\n".join(
            f"Student: {t['user']}\nAdvisor: {t['answer'][:120]}..."
            for t in recent_turns[-2:]
        )
        parts.append(f"Recent conversation:\n{conv}")

    refs = []
    if last_referenced.course_code:
        refs.append(f"last course: {last_referenced.course_code}")
    if last_referenced.role_id:
        refs.append(f"last role: {last_referenced.role_id}")
    if last_referenced.track_id:
        refs.append(f"last track: {last_referenced.track_id}")
    if refs:
        parts.append("Reference context: " + ", ".join(refs))

    parts.append(f"Student query: {user_text}")
    return "\n\n".join(parts)


def understand_query(
    user_text: str,
    last_referenced: LastReferenced,
    recent_turns: list[dict],
) -> StructuredQuery:
    logger.info("QU: classifying — %s", user_text[:100])
    pre_code = _pre_extract_code(user_text)

    try:
        raw = get_llm_client().chat(
            system=QU_SYSTEM_PROMPT,
            user=_build_user_msg(user_text, last_referenced, recent_turns),
            json_mode=True,
            temperature=0.0,
        )
        data = parse_json_object(raw)
    except LLMError as exc:
        logger.error("QU: LLM failed: %s — falling back to rag", exc)
        return StructuredQuery(intent="handbook_query", engine_pattern="rag", query_type="general", original_text=user_text)

    e_raw = data.get("entities") or {}
    entities = EntitySet(
        course_code=e_raw.get("course_code") or pre_code,
        role_id=e_raw.get("role_id"),
        track_id=e_raw.get("track_id"),
        skill_id=e_raw.get("skill_id"),
    )

    s_raw = data.get("secondary_entities") or {}
    secondary = EntitySet(**{k: s_raw.get(k) for k in ["course_code", "role_id", "track_id", "skill_id"]})
    secondary = secondary if any([secondary.course_code, secondary.role_id, secondary.track_id, secondary.skill_id]) else None

    o_raw = data.get("session_overrides") or {}
    overrides = SessionOverrides(
        added_courses=o_raw.get("added_courses") or [],
        target_role=o_raw.get("target_role"),
    )

    sq = StructuredQuery(
        intent=data.get("intent", "handbook_query"),
        engine_pattern=data.get("engine_pattern", "rag"),
        query_type=data.get("query_type", "general"),
        original_text=user_text,
        entities=entities,
        secondary_entities=secondary,
        needs_clarification=bool(data.get("needs_clarification", False)),
        clarification_prompt=data.get("clarification_prompt"),
        session_overrides=overrides,
    )

    logger.info("QU: intent=%s engine=%s type=%s entities=%s", sq.intent, sq.engine_pattern, sq.query_type, sq.entities)
    return sq
