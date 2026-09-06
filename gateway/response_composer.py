"""
ResponseComposer — LLM-centered narration layer.

Receives TurnWrapper + original user text from the /chat handler.
Returns a student-facing QueryResponse.

Pipeline:
  1. Build a deterministic narration packet from each PerSQResult
     (intent-aware field extraction, no raw StudentContext)
  2. Try the Composer LLM chain (primary → fallbacks) to produce NLG
  3. If the LLM is unavailable/fails/disabled, return a deterministic
     plain-text answer from the same packet

Hard boundaries:
  - Never calls KG, RAG, ALE, or QU
  - Never receives raw StudentContext
  - Never changes academic facts, numbers, grades, credits, or decisions
  - Never modifies session state

LLM model chain env vars:
    COMPOSER_USE_LLM         (default: true)
    COMPOSER_PRIMARY_MODEL   (default: qwen/qwen3-32b)
    COMPOSER_FALLBACK_MODELS (default: llama-3.1-8b-instant,openai/gpt-oss-20b)
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

_TRACE = os.getenv("PATHFINDER_TRACE", "").lower() in ("true", "1", "yes")

from gateway.llm_client import LLMClient, LLMError, LLMNotConfigured, get_llm_client
from gateway.models.schemas import Citation, PerSQResult, QueryResponse, TurnWrapper

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULT_PRIMARY = "qwen/qwen3-32b"
_DEFAULT_FALLBACKS = ["llama-3.1-8b-instant", "openai/gpt-oss-20b"]

# ── Track display names ───────────────────────────────────────────────────────

_TRACK_DISPLAY_MAP: dict[str, str] = {
    "AI": "Artificial Intelligence (AI)",
    "CYS": "Cyber Security (CYS)",
    "DSE": "Data Science and Engineering (DSE)",
    "SWE": "Software Engineering (SWE)",
    "GEN": "General Program (GEN)",
}

_LEVEL_DISPLAY: dict[int, str] = {1: "Freshman", 2: "Sophomore", 3: "Junior", 4: "Senior"}

_D6_INTERNAL_TERMS = frozenset({
    "packet", "payload", "orchestrator", "composer", "kg", "ale", "rag",
    "engine", "structured result", "sq ", "the sq", "intent",
})

# ── Display formatting helpers ────────────────────────────────────────────────

def _fmt_course_label(code: str, name: str, credits=None) -> str:
    """Format a course as 'Course Name (COURSE_CODE)' with optional credits."""
    if name and code:
        label = f"{name} ({code})"
    else:
        label = name or code or ""
    if credits is not None:
        label += f" — {credits} credits"
    return label


def _fmt_role_label(role_id: str, name: str = "") -> str:
    """Prefer role name; convert RL_Foo_Bar → Foo Bar if no name available."""
    if name:
        return name
    if role_id:
        if role_id.startswith("RL_"):
            return role_id[3:].replace("_", " ")
        return role_id
    return ""


def _fmt_skill_label(skill_id: str, name: str = "") -> str:
    """Prefer skill name; convert SK_Foo_Bar → Foo Bar if no name available."""
    if name:
        return name
    if skill_id:
        if skill_id.startswith("SK_"):
            return skill_id[3:].replace("_", " ")
        return skill_id
    return ""


def _fmt_track_label(track_id: str, name: str = "") -> str:
    """Format track with friendly canonical name; canonical map overrides KG-provided name."""
    if track_id in _TRACK_DISPLAY_MAP:
        return _TRACK_DISPLAY_MAP[track_id]
    if name:
        return f"{name} ({track_id})" if track_id and track_id not in name else name
    return track_id or ""


def _render_course_detail(detail: dict) -> str:
    """Render a course detail dict as 'Course Name (CODE)' or gracefully when KG profile missing."""
    code = detail.get("course_code", "")
    name_val = detail.get("course_name")  # None means KG profile lookup failed
    name = name_val or ""
    if name:
        return _fmt_course_label(code, name)
    # Explicitly None (set by _enrich_course_details on KG error) → graceful label
    if "course_name" in detail and name_val is None and code:
        return f"{code} (details not available in catalogue)"
    return code or ""


def _load_composer_timeout() -> float:
    """Read COMPOSER_TIMEOUT_SECONDS from env; return 30.0 if missing or invalid."""
    try:
        return float(os.getenv("COMPOSER_TIMEOUT_SECONDS", "30"))
    except (TypeError, ValueError):
        return 30.0


def _duration_ms(start: float) -> int:
    """Elapsed milliseconds since start (from time.monotonic())."""
    return int((time.monotonic() - start) * 1000)


def _safe_session_id(session_id: str) -> str:
    """Return first 8 chars of session_id, or 'unknown' if empty."""
    return (session_id or "")[:8] or "unknown"


def _summarize_packets(packets: list[dict]) -> dict:
    """Safe packet summary for logging — counts and intent/status labels only."""
    return {
        "packet_count": len(packets),
        "intents": [p.get("intent") for p in packets],
        "statuses": [p.get("status") for p in packets],
        "citations_count": sum(len(p.get("citations") or []) for p in packets),
        "assumptions_active_count": sum(1 for p in packets if p.get("notice_assumptions_active")),
        "assumptions_excluded_count": sum(1 for p in packets if p.get("notice_assumptions_excluded")),
        "override_active_count": sum(1 for p in packets if p.get("notice_override_active")),
    }

# ── LLM system prompt ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are PathFinder's academic advising response composer for EUI students.

You receive a narration packet containing structured results from PathFinder's academic engines. \
Your only job is to turn this packet into a clear, friendly, student-facing answer.

HARD RULES:
1. Use ONLY the provided narration packet. Do not add any facts not present in the packet.
2. Do NOT alter numbers, course codes, course names, grades, GPA/CGPA values, credit hours, \
statuses, eligibility decisions, warnings, or citations.
3. Do NOT mention KG, Neo4j, ALE, RAG, or any technical system names. \
Say "our records", "the curriculum", or "PathFinder's analysis".
4. Do NOT call any external system or infer new academic rules.
5. Keep the tone friendly, concise, and suitable for a university student.
6. Use 1–4 paragraphs. Use bullet points for lists (courses, skills, roles).
7. For multi-result turns, combine into one coherent answer with clear section headings.
8. If the packet contains notice_assumptions_active, state clearly that the answer reflects \
what-if assumptions and is not based on the official academic record.
9. If the packet contains notice_assumptions_excluded, state clearly that the graduation audit \
uses the official academic record only and active assumptions are excluded.
10. If a result status is clarification_needed, ask the clarification question clearly and only that.
11. If a result status is out_of_scope, explain politely that PathFinder covers academic and \
career advising only.
12. If a result status is error, give a friendly message — no error codes, no stack traces.
13. If a result status is soft_no_evidence, answer cautiously and say handbook evidence was limited.
14. If a result status is informational, explain the result and any reason codes helpfully.
15. If citations are present in the packet, end with a "Sources:" line listing them.
16. English only.
17. NEVER invent or mention sources, citations, handbooks, or document names that are not \
explicitly listed in the packet. If no citations are provided, do not add a Sources section at all.
18. NEVER fabricate graduation status, eligibility decisions, or course lists. \
If can_graduate is False, say so clearly. If eligibility is not explicitly true, do not assume it.
19. NEVER write placeholder text like [Course Name], [Course Code], [X], or similar. \
If a piece of information is absent from the packet, say "details not available" or omit that item.
20. If the packet contains already_graduated=True, tell the student they have already graduated \
according to our records. Do NOT say "you are eligible to graduate this semester" — \
that would be misleading since they already completed their degree.
21. If the packet contains not_auditable=True, explain that a graduation audit cannot be run \
because of their current study status (e.g. Transferred Out, Suspended). Be factual, not alarming.
22. For eligibility results: check eligibility_status before the eligible boolean. \
"in_progress" → the student is already enrolled/currently taking this course. \
"already_completed" → the student has already passed/completed this course. \
"retake_cap_exceeded" → the retake cap has been reached. \
Only then use eligible=True ("you are eligible") or eligible=False (list missing prerequisites).
23. NEVER ask the student for information about their academic record — you already have it in \
the packet. If the packet data shows a not-found result with attempted_candidate, tell the student \
what you couldn't find using that candidate name, then stop. Do NOT ask which course/skill they \
meant unless clarification_prompt is explicitly in the packet. NEVER write phrases like \
"please share your courses", "I'll need more details", "could you provide your GPA", \
"please tell me your completed courses", or any similar request. \
If the packet data is insufficient, say so ("details not available") and stop — do not prompt.
24. If academic_standing is "warning" and the student asks about academic danger or probation, \
confirm directly: "Based on your CGPA of X, you are currently on academic warning." \
Never make the student infer their standing from a raw number or generic policy table.
25. For credit-limit questions: if the packet contains the student's cgpa AND a credit-limit \
policy excerpt, apply the policy to their specific CGPA and state a single concrete number \
(e.g. "With your CGPA of 1.91 you can register up to 15 credit hours"). \
Do not just restate the full policy table.
26. Display courses as "Course Name (COURSE_CODE)", never "COURSE_CODE — Course Name". \
For example: "Advanced Programming (C-CS219)", not "C-CS219 — Advanced Programming".
27. Never expose raw role IDs (RL_*) or skill IDs (SK_*) in your answer. \
Convert them: RL_Data_Scientist → "Data Scientist", SK_Machine_Learning → "Machine Learning". \
Display tracks as "Artificial Intelligence (AI)", "Cyber Security (CYS)", \
"Data Science and Engineering (DSE)", "Software Engineering (SWE)", or "General Program (GEN)". \
For alignment scores: use the word "role" not "track". Say "Software Engineer role" not "Software Engineer track". \
Phrase alignment scores as curriculum-skill alignment, not employability guarantees. \
Phrase roles by track as related/connected roles, not guaranteed jobs. \
Do not narrate focus/gap courses as registration plans.
28. When what-if assumptions are cleared, say exactly: \
"I cleared your what-if assumptions. You are back to your official academic record." \
Never write "Your academic record has been updated" or anything implying registrar data changed.
29. For D2 course-info answers: include semester offering and track when present in the packet. \
Do NOT mention eligibility unless user asked "can I take". Do NOT invent missing fields.
30. For skills answers: show readable skill names (convert SK_* → readable: \
SK_Machine_Learning → "Machine Learning"). If no skills found, say \
"I couldn't find mapped skills for this course in the current curriculum data." \
NOT generic "details unavailable."
31. For skill-search answers: show course names first ("Course Name (CODE)"). \
Show the matched skill name. If skill not found, include the attempted skill name in the response.
32. If packet contains attempted_candidate or attempted_skill, mention it in the not-found \
response so the student knows what was searched.
33. For D6 failed_course_history: show all historically failed attempts, note that some may \
have been retaken and passed. This is different from current failed_courses (unresolved fails only).
34. RESPONSE DETAIL MATCHING: Match the level of detail the student requested. \
If they asked "what is my CGPA?", give a natural sentence like "Your current CGPA is 1.91." \
If they explicitly used phrasing like "just the number" or "yes or no only", be minimal. \
For list questions (failed courses, in-progress courses), always include the relevant list \
unless the student explicitly said not to. response_style is a tone hint only — it should \
never suppress directly relevant facts.
35. For academic warning (academic_standing="warning") WITHOUT a policy citation in the packet, \
state the fact in measured terms ("you are currently on academic warning") but do NOT speculate \
about consequences such as suspension, dismissal, or probation timelines. \
Only add consequence language when the packet contains explicit RAG policy citations.
36. NEVER derive or infer a course name from its course code. If a course has no name or \
display_label in the packet, display ONLY the code: e.g., "C-AI321" or \
"C-AI321 (name not available)". Do NOT construct names like "Artificial Intelligence 321" \
or "Computer Science 496" from the code format.
37. For eligibility_status="in_progress": do NOT promise retake availability. State that the \
student is currently enrolled and cannot register again while enrolled. \
Retake eligibility depends on the final grade once the course is complete.
38. When the packet contains checked_course_metadata or in_progress_course_metadata, \
use the provided name for each code. Format as "Course Name (CODE)". \
Do not invent names; if the metadata entry has name=null or is absent, show ONLY the code.
"""

# ── Narration packet — per-PerSQResult extraction ─────────────────────────────

def _safe_code_name(data: dict, code_key: str, name_key: str) -> str:
    """Format as 'Name (Code)' — name-first display."""
    code = data.get(code_key)
    name = data.get(name_key)
    if code and name:
        return f"{name} ({code})"
    return str(code or name or "")


def _cap_list(val: list, limit: int = 20) -> list:
    return val[:limit] if len(val) > limit else val


def _resolve_alignment_pct(data: dict) -> str | None:
    """Return user-facing percentage string for alignment data.

    Prefers alignment_percentage (already 0-100) over alignment_score (0-1 decimal).
    Converts alignment_score ∈ [0,1] → ×100; values >1 treated as already percentage-like.
    Formats cleanly: 60.0 → "60%", 69.12 → "69.12%" (no trailing zeros via :g).
    """
    pct = data.get("alignment_percentage")
    if isinstance(pct, (int, float)):
        return f"{pct:g}%"
    score = data.get("alignment_score")
    if isinstance(score, (int, float)):
        pct_val = score * 100 if 0.0 <= score <= 1.0 else score
        return f"{pct_val:g}%"
    return None


def _extract_packet(result: PerSQResult) -> dict:
    """Build a compact, safe narration packet from a single PerSQResult."""
    packet: dict = {
        "intent": result.intent,
        "status": result.status,
    }

    # Terminal statuses — no data needed beyond the control field
    if result.status == "error":
        packet["error"] = result.error_detail or "An error occurred processing your request."
        if result.error_code:
            packet["error_code"] = result.error_code
        return packet

    if result.status == "clarification_needed":
        packet["clarification_prompt"] = (
            result.clarification_prompt or "Could you clarify your question?"
        )
        return packet

    if result.status == "out_of_scope":
        packet["scope_explanation"] = (
            result.scope_explanation
            or "This query is outside PathFinder's academic advising scope."
        )
        return packet

    data = result.data or {}

    # Assumption / override notices (must survive LLM polishing)
    if result.assumptions_active:
        packet["notice_assumptions_active"] = (
            "This result reflects active what-if assumptions you set "
            "(e.g. assumed passed/failed courses). "
            "It is NOT based on your official academic record."
        )
    if result.assumptions_excluded:
        packet["notice_assumptions_excluded"] = (
            "This graduation audit uses your official academic record only. "
            "Active what-if assumptions are excluded from this result."
        )
    if result.override_state_active:
        packet["notice_override_active"] = (
            "Scenario overrides are currently active for this session."
        )

    if result.status == "soft_no_evidence":
        packet["evidence_limited"] = True

    # Intent-specific field extraction
    intent = result.intent

    if intent == "check_course_eligibility":
        _extract_eligibility(packet, data)
    elif intent in ("simulate_gpa_forward", "solve_target_gpa"):
        _extract_gpa(packet, data)
    elif intent == "run_graduation_audit":
        _extract_graduation_audit(packet, data)
    elif intent in ("plan_semester", "generate_graduation_roadmap"):
        _extract_plan(packet, data)
    elif intent == "get_course_info":
        _extract_course_info(packet, data)
    elif intent == "get_course_prerequisites":
        _extract_prereqs(packet, data)
    elif intent == "get_skills_taught":
        _extract_skills_taught(packet, data)
    elif intent == "search_courses_by_skill":
        _extract_courses_by_skill(packet, data)
    elif intent == "get_role_profile":
        _extract_role_profile(packet, data)
    elif intent == "get_roles_by_track":
        _extract_roles_by_track(packet, data)
    elif intent in (
        "compute_skill_gap", "compute_alignment_score",
        "recommend_courses_to_close_gap", "find_best_matching_roles",
        "estimate_alignment_improvement", "get_focus_courses_for_target",
    ):
        _extract_career(packet, data, intent)
        if intent == "compute_alignment_score":
            pct = _resolve_alignment_pct(packet)
            if pct:
                packet["alignment_pct_display"] = pct
                # Remove raw decimal score so LLM shows only the percentage form
                packet.pop("alignment_score", None)
                packet.pop("alignment_percentage", None)
    elif intent in (
        "get_track_overview", "compare_tracks",
        "recommend_track_for_role", "recommend_track_for_skill",
    ):
        _extract_track(packet, data, intent)
    elif intent == "policy_query":
        _extract_policy(packet, data)
    elif intent == "get_student_record":
        _extract_student_record(packet, data)
    else:
        # Unknown intent — pass a safe subset of data
        packet["data"] = dict(list(data.items())[:20])

    if result.citations:
        packet["citations"] = result.citations

    return packet


# ── Intent-specific extractors ────────────────────────────────────────────────

_ELIGIBILITY_STATUSES = frozenset({
    "eligible", "not_eligible", "already_completed", "in_progress", "retake_cap_exceeded",
})


def _extract_eligibility(packet: dict, data: dict) -> None:
    for k in (
        "eligible", "eligibility_status", "reason", "reason_code",
        "missing_prerequisites", "missing_credits", "credit_threshold",
        "message", "target_course_code", "attempt_type",
        "warnings", "cannot_compute",
        "completed_prerequisites", "retake_count_for_course", "max_retake_cap",
        "credit_threshold_required", "credit_threshold_met",
        # course name fields for display
        "target_course_name", "course_name", "name",
    ):
        if k in data:
            packet[k] = data[k]
    # Map ALE "status" field to eligibility_status whenever it's a recognized value
    ale_status = data.get("status")
    if ale_status in _ELIGIBILITY_STATUSES:
        if "eligibility_status" not in packet:
            packet["eligibility_status"] = ale_status
        if "eligible" not in packet:
            packet["eligible"] = (ale_status == "eligible")


def _extract_gpa(packet: dict, data: dict) -> None:
    for k in (
        "current_cgpa", "projected_cgpa", "target_cgpa",
        "cgpa_gap", "credits_needed", "semesters_needed",
        "required_grade", "required_gpa_each_semester",
        "already_met", "status", "message", "cannot_compute",
        "scenario_summary", "warnings", "reason_code", "reason_codes",
        "delta", "required_data_missing",
        "per_course_breakdown", "grade_overrides",
        "delta_needed", "required_average_letter", "required_average_grade_points",
        "semester_projections", "per_course_targets",
    ):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 20)
            packet[k] = val


def _extract_graduation_audit(packet: dict, data: dict) -> None:
    for k in (
        "can_graduate", "graduation_status", "gaps",
        "total_credits_earned", "total_credits_required",
        "credit_gap", "cgpa", "cgpa_required",
        "honors", "academic_standing", "warnings",
        "reason_codes", "message", "cannot_compute",
        # ALE actual field names
        "current_cgpa", "checks", "next_steps",
        "honors_status", "honors_checks",
        "is_final_semester", "required_data_missing",
    ):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 15)
            packet[k] = val
    # ALE status mapping → can_graduate / special cases
    ale_status = data.get("status")
    if "can_graduate" not in packet:
        if ale_status == "eligible":
            packet["can_graduate"] = True
        elif ale_status == "not_eligible":
            packet["can_graduate"] = False
        elif ale_status == "already_graduated":
            packet["can_graduate"] = True
            packet["already_graduated"] = True
        elif ale_status == "not_auditable":
            packet["can_graduate"] = None
            packet["not_auditable"] = True
            reason_codes = data.get("reason_codes") or []
            if reason_codes:
                packet["not_auditable_reason"] = reason_codes[0]
    # Flatten checks into gaps[] using next_steps (most readable)
    if "gaps" not in packet:
        if data.get("next_steps"):
            packet["gaps"] = _cap_list(data["next_steps"], 10)
        elif "checks" in data:
            packet["gaps"] = [
                c.get("gap") or f"{c.get('name','?')}: {c.get('actual_value','?')} (required: {c.get('required_value','?')})"
                for c in (data.get("checks") or [])
                if not c.get("passed")
            ]
    if "current_cgpa" in data and "cgpa" not in packet:
        packet["cgpa"] = data["current_cgpa"]


def _extract_plan(packet: dict, data: dict) -> None:
    for k in (
        "planned_courses", "recommended_courses", "semesters",
        "roadmap", "status", "message", "cannot_compute",
        "credit_load", "total_semesters", "warnings",
        "target_semester_type", "starting_semester",
        # ALE actual field names
        "plans", "is_final_semester", "credit_cap_applied",
        "total_eligible_courses", "ineligibility_summary",
        # graduation roadmap specific
        "reason_codes", "non_course_blockers", "remaining_graduation_gaps",
        "projected_graduation_semester", "required_data_missing",
        "total_passes", "semester_plans", "simulation_disclaimer",
        # new redesign fields
        "cgpa_bracket_max", "planning_target_credits",
        "excluded_requested_courses", "in_progress_failed_courses",
        "retake_warning_courses", "requested_plans_count", "requested_plans_requested",
    ):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 30)
            packet[k] = val
    # ALE returns plans: [{plan_label, courses: [{course_code, course_name, credits}]}]
    # Flatten into recommended_courses for the narration layer
    if "recommended_courses" not in packet and "plans" in data:
        plans = data.get("plans") or []
        if plans:
            # Use the first (Recommended) plan
            primary = plans[0]
            flat_courses = []
            for c in (primary.get("courses") or [])[:15]:
                code = c.get("course_code", "")
                name = c.get("course_name", "")
                cred = c.get("credits")
                retake = c.get("is_retake", False)
                # Name-first: "Course Name (COURSE_CODE) — N credits"
                label = _fmt_course_label(code, name)
                if cred:
                    label += f" — {cred} credits"
                if retake:
                    label += " [retake]"
                flat_courses.append(label)
            packet["recommended_courses"] = flat_courses
            packet["credit_load"] = primary.get("total_credits")
            # Expose all plan labels for multi-plan summary
            if len(plans) > 1:
                packet["plan_variants"] = [
                    f"{p.get('plan_label','Plan')}: {p.get('total_credits', '?')} cr "
                    f"({len(p.get('courses',[]))} courses)"
                    for p in plans[:4]
                ]


def _extract_course_info(packet: dict, data: dict) -> None:
    for k in (
        "course_code", "name", "credits", "level", "description",
        "semester_offering", "tracks", "credit_threshold",
        "error", "attempted_candidate",
    ):
        if k in data:
            packet[k] = data[k]


def _extract_prereqs(packet: dict, data: dict) -> None:
    for k in (
        "course_code", "name", "direct_prerequisites",
        "transitive_prerequisites", "non_course_prerequisites",
        "full_prerequisite_tree", "error", "attempted_candidate",
    ):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 30)
            packet[k] = val


def _extract_skills_taught(packet: dict, data: dict) -> None:
    for k in ("course_code", "name", "skills_taught", "skills", "error", "attempted_candidate"):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 25)
            packet[k] = val
    # Normalize: skills_taught may be the actual KG field name
    if "skills_taught" in packet and "skills" not in packet:
        packet["skills"] = packet["skills_taught"]


def _extract_courses_by_skill(packet: dict, data: dict) -> None:
    for k in ("skill_id", "skill_name", "courses", "results", "error", "attempted_skill",
              "topic_fallback", "original_mention"):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 20)
            packet[k] = val
    # Normalize: results may be the KG field, courses is the display field
    if "results" in packet and "courses" not in packet:
        packet["courses"] = packet["results"]
    # Expose attempted_skill if no skill_name
    if "attempted_skill" in packet and not packet.get("skill_name"):
        packet["skill_display"] = packet["attempted_skill"]


def _extract_role_profile(packet: dict, data: dict) -> None:
    for k in ("role_id", "name", "role_name", "description", "required_skills", "recommended_tracks", "error"):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 20)
            packet[k] = val
    # KG may return "role_name" instead of "name" — normalize for Composer display
    if "name" not in packet and "role_name" in packet:
        packet["name"] = packet["role_name"]


def _extract_roles_by_track(packet: dict, data: dict) -> None:
    for k in ("track_id", "track_name", "roles", "error", "total_results"):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 20)
            packet[k] = val
    # KG may return "results" instead of "roles" — normalize
    if "roles" not in packet and "results" in data:
        val = data["results"]
        if isinstance(val, list):
            val = _cap_list(val, 20)
        packet["roles"] = val
    # KG may return "track" instead of "track_name" — normalize
    if "track_name" not in packet and "track" in data:
        packet["track_name"] = data["track"]


def _extract_career(packet: dict, data: dict, intent: str) -> None:
    for k in (
        "role_id", "role_name", "alignment_score", "alignment_percentage",
        "missing_skills", "covered_skills",
        "recommended_courses", "ranked_roles",
        "projected_improvement", "current_alignment", "new_alignment",
        "focus_courses", "skill_gap_count",
        # estimate_alignment_improvement — KG field names
        "current_alignment_score", "current_alignment_percentage",
        "projected_alignment_score", "projected_alignment_percentage",
        "alignment_improvement",
        "newly_covered_skills", "still_missing_skills",
        "total_newly_covered", "total_still_missing",
        "unresolved_planned_names",
        # get_focus_courses_for_target enrichment
        "personalized_focus", "completed_courses_excluded",
        "error", "reason", "message",
    ):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 20)
            packet[k] = val


def _sanitize_skill_entry(entry) -> dict:
    """Convert a raw skill entry to display-safe form with only a readable name."""
    if isinstance(entry, dict):
        sid = entry.get("skill_id", "")
        name = entry.get("name", "") or entry.get("skill_name", "")
        return {"name": _fmt_skill_label(sid, name)}
    return {"name": _fmt_skill_label(str(entry))}


def _sanitize_skills_field(val) -> object:
    """Replace raw skill_id entries with display names so LLM cannot expose SK_* IDs."""
    if isinstance(val, list):
        return [_sanitize_skill_entry(s) for s in val]
    if isinstance(val, dict):
        out = {}
        for k, v in val.items():
            if isinstance(v, list) and k not in ("total_track_1_only", "total_track_2_only", "total_shared"):
                out[k] = [_sanitize_skill_entry(s) for s in v]
            else:
                out[k] = v
        return out
    return val


def _extract_track(packet: dict, data: dict, intent: str) -> None:
    for k in (
        "track_id", "track_id_1", "track_id_2",
        "name", "track_1_name", "track_2_name",
        "description", "shared_courses", "different_courses",
        "skills", "role_alignment", "recommended_track", "ranking",
        "courses", "error",
        # recommendation intents — KG returns these
        "ranked_tracks", "total_tracks_evaluated",
        "role_id", "role_name", "skill_id", "skill_name",
    ):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 20)
            # Sanitize skills to prevent raw SK_* IDs reaching the LLM
            if k == "skills":
                val = _sanitize_skills_field(val)
            packet[k] = val


def _extract_policy(packet: dict, data: dict) -> None:
    for k in ("answer", "extracted_facts"):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 15)
            packet[k] = val


def _extract_student_record(packet: dict, data: dict) -> None:
    # response_style is kept as a tone hint but never strips fields from the packet.
    # The deterministic narration layer handles focus-specific rendering.
    for k in (
        "record_focus", "response_style",
        "track_id", "program", "level", "level_display", "cgpa",
        "last_semester_gpa", "last_semester_chs", "last_semester_cps",
        "academic_standing", "study_status", "total_credit_hours_earned",
        "current_semester", "consecutive_warnings", "total_warnings",
        "completed_courses", "in_progress_courses", "failed_courses",
        "completed_course_details", "in_progress_course_details", "failed_course_details",
        "failed_history_codes", "failed_history_details",
        "scenario_completed_credits", "assumed_failed_courses", "assumed_passed_courses",
        "assumptions_cleared", "message",
        # multi-course status check metadata
        "checked_course_codes", "status_filter",
        # display enrichment labels from Orchestrator
        "course_display_labels",
        # startup-cache metadata for course_status_check display
        "checked_course_metadata",
        "in_progress_course_metadata",
    ):
        if k in data:
            val = data[k]
            if isinstance(val, list):
                val = _cap_list(val, 30)
            packet[k] = val


# ── Deterministic fallback narration ─────────────────────────────────────────

def _personalize_credit_limit(packets: list[dict]) -> Optional[str]:
    """
    If packets include a student CGPA (from get_student_record) and a policy
    packet clearly about credit limits, return a single personalized sentence.
    """
    cgpa: Optional[float] = None
    is_credit_limit_query = False
    for p in packets:
        if p.get("intent") == "get_student_record" and p.get("cgpa") is not None:
            cgpa = p["cgpa"]
        if p.get("intent") == "policy_query":
            answer = (p.get("answer") or "").lower()
            facts_text = " ".join(str(f) for f in (p.get("extracted_facts") or [])).lower()
            combined = answer + " " + facts_text
            credit_terms = ("credit hour", "credit limit", "maximum credit", "max credit", "credit cap")
            if any(t in combined for t in credit_terms):
                is_credit_limit_query = True
    if cgpa is None or not is_credit_limit_query:
        return None
    if cgpa > 3.0:
        max_ch = 21
    elif cgpa >= 2.0:
        max_ch = 18
    elif cgpa >= 1.0:
        max_ch = 15
    else:
        max_ch = 12
    return (
        f"Based on your CGPA of {cgpa:.2f}, "
        f"you can register up to {max_ch} credit hours this semester."
    )


def _deterministic_answer(packets: list[dict]) -> str:
    """Build a plain-text answer from narration packets without any LLM call."""
    parts: list[str] = []

    for p in packets:
        intent = p.get("intent", "unknown")
        status = p.get("status", "unknown")
        lines: list[str] = []

        if status == "error":
            err = p.get("error", "An error occurred.")
            parts.append(f"I wasn't able to complete your request: {err}")
            continue

        if status == "clarification_needed":
            parts.append(p.get("clarification_prompt", "Could you clarify your question?"))
            continue

        if status == "out_of_scope":
            parts.append(
                p.get(
                    "scope_explanation",
                    "That question is outside PathFinder's scope. "
                    "PathFinder covers courses, GPA, graduation, career tracks, and academic policies.",
                )
            )
            continue

        if status == "soft_no_evidence":
            lines.append("(Note: Handbook evidence for this topic was limited.)")

        _narrate_intent(p, intent, lines)

        # Assumption / override notices
        for notice_key in (
            "notice_assumptions_active",
            "notice_assumptions_excluded",
            "notice_override_active",
        ):
            if p.get(notice_key):
                lines.append("")
                lines.append(f"⚠ {p[notice_key]}")

        if lines:
            parts.append("\n".join(lines))

    # Credit-limit personalisation: only when student record + credit policy are both present
    credit_note = _personalize_credit_limit(packets)
    if credit_note:
        parts.append(credit_note)

    citations_block = _citations_text_from_packets(packets)
    if citations_block:
        parts.append(citations_block)

    return "\n\n".join(parts) if parts else (
        "I was unable to generate a response. Please try again."
    )


def _narrate_intent(p: dict, intent: str, lines: list[str]) -> None:  # noqa: C901 (complexity OK for a dispatcher)
    """Append deterministic narrative lines for one intent packet."""

    if intent == "check_course_eligibility":
        code = p.get("target_course_code", "the course")
        # Prefer an explicit course name field; fall back to bare code
        course_name = (
            p.get("target_course_name") or p.get("course_name") or p.get("name", "")
        )
        course_label = _fmt_course_label(code, course_name) if course_name else code
        eligibility_status = p.get("eligibility_status", "")
        eligible = p.get("eligible")
        reason = p.get("reason") or p.get("reason_code", "")
        missing = p.get("missing_prerequisites") or []
        attempt = p.get("attempt_type", "")

        if eligibility_status == "in_progress":
            lines.append(f"You are already enrolled in / currently taking {course_label}.")
            lines.append(
                "You cannot register for another attempt while currently enrolled. "
                "Retake or improve-retake eligibility will depend on your final grade "
                "once the course is complete."
            )
        elif eligibility_status == "already_completed":
            lines.append(f"You have already completed and passed {course_label}.")
        elif eligibility_status == "retake_cap_exceeded":
            retake_msg = p.get("message") or p.get("reason") or ""
            lines.append(f"You have reached the retake cap for {course_label}.")
            if retake_msg and retake_msg not in lines[-1]:
                lines.append(retake_msg)
        elif eligible is True:
            suffix = f" ({attempt.replace('_', ' ')})" if attempt else ""
            lines.append(f"You are eligible to take {course_label}{suffix}.")
        elif eligible is False:
            lines.append(f"You are not currently eligible to take {course_label}.")
            if reason:
                lines.append(f"Reason: {reason}")
            if missing:
                lines.append("Missing prerequisites:")
                for m in missing:
                    if isinstance(m, dict):
                        c, n = m.get("course_code", ""), m.get("name", "")
                        label = _fmt_course_label(c, n) if n else (c or str(m))
                    else:
                        label = str(m)
                    lines.append(f"  • {label}")
        else:
            lines.append(p.get("message") or f"Eligibility result for {course_label} is available.")
        for w in (p.get("warnings") or []):
            lines.append(f"Warning: {w}")

    elif intent in ("simulate_gpa_forward", "solve_target_gpa"):
        if (p.get("status") or "") == "cannot_compute" or p.get("cannot_compute"):
            reason_codes = p.get("reason_codes") or []
            missing = p.get("required_data_missing") or []
            lines.append("Could not compute the GPA projection with the current data.")
            if reason_codes:
                lines.append(f"Reason: {', '.join(reason_codes)}")
            for m in missing[:5]:
                lines.append(f"  • Missing: {m}")
        elif p.get("already_met"):
            lines.append("You have already met your target CGPA — no additional work needed.")
        else:
            curr = p.get("current_cgpa")
            proj = p.get("projected_cgpa")
            target = p.get("target_cgpa")
            delta = p.get("delta")
            if curr is not None:
                lines.append(f"Current CGPA: {curr:.2f}")
            if proj is not None:
                change = f" ({'+' if delta and delta >= 0 else ''}{delta:.2f})" if delta is not None else ""
                lines.append(f"Projected CGPA: {proj:.2f}{change}")
            if target is not None:
                lines.append(f"Target CGPA: {target:.2f}")
            req_letter = p.get("required_average_letter")
            if req_letter:
                lines.append(f"Required average grade: {req_letter}")
            if "credits_needed" in p:
                lines.append(f"Estimated credits needed: {p['credits_needed']}")
            if "semesters_needed" in p:
                lines.append(f"Estimated semesters needed: {p['semesters_needed']}")
            breakdown = p.get("per_course_breakdown") or []
            if breakdown:
                lines.append("Per-course breakdown:")
                for c in breakdown[:10]:
                    if isinstance(c, dict):
                        code = c.get("course_code", "")
                        grade = c.get("applied_grade") or c.get("expected_grade") or ""
                        contrib = c.get("cgpa_contribution")
                        cl = f"  • {code}: {grade}"
                        if contrib is not None:
                            cl += f" (contribution: {contrib:+.3f})"
                        lines.append(cl)
            msg = p.get("message")
            if msg:
                lines.append(msg)
        for w in (p.get("warnings") or []):
            lines.append(f"Warning: {w}")

    elif intent == "run_graduation_audit":
        if p.get("already_graduated"):
            lines.append("Our records show you have already graduated.")
            cgpa = p.get("cgpa")
            if cgpa is not None:
                lines.append(f"Graduated CGPA: {cgpa:.2f}")
            honors = p.get("honors_status") or p.get("honors")
            if honors and honors not in ("not_eligible", ""):
                lines.append(f"Honors: {honors}")
            return
        if p.get("not_auditable"):
            reason = (p.get("not_auditable_reason") or "")
            reason = reason.replace("study_status_", "").replace("_", " ").title()
            lines.append("A graduation audit cannot be run on your account.")
            if reason:
                lines.append(f"Study status: {reason}.")
            return
        can_grad = p.get("can_graduate")
        if can_grad is True:
            lines.append("You are eligible to graduate.")
        elif can_grad is False:
            lines.append("You are not yet eligible to graduate.")
        gaps = p.get("gaps") or []
        if gaps:
            lines.append("Remaining requirements:")
            for g in gaps[:10]:
                lines.append(f"  • {g}")
        cgpa = p.get("cgpa")
        req_cgpa = p.get("cgpa_required")
        if cgpa is not None:
            suffix = f" (required: {req_cgpa})" if req_cgpa is not None else ""
            lines.append(f"Your CGPA: {cgpa:.2f}{suffix}")
        honors = p.get("honors_status") or p.get("honors")
        if honors and honors not in ("not_eligible", ""):
            lines.append(f"Honors: {honors}")
        for w in (p.get("warnings") or []):
            lines.append(f"Warning: {w}")

    elif intent in ("plan_semester", "generate_graduation_roadmap"):
        if (p.get("status") or "") == "already_graduated":
            lines.append(p.get("message") or "You have already graduated.")
            return
        if p.get("cannot_compute") or (p.get("status") or "") == "cannot_compute":
            msg = p.get("message") or "Could not compute the plan with current data."
            lines.append(msg)
            reason_codes = p.get("reason_codes") or []
            if reason_codes:
                lines.append(f"Reason: {', '.join(reason_codes)}")
            blockers = p.get("non_course_blockers") or []
            for b in blockers:
                lines.append(f"  • Blocker: {b}")
            gaps = p.get("remaining_graduation_gaps") or []
            for g in gaps:
                lines.append(f"  • {g}")
        else:
            proj_grad = p.get("projected_graduation_semester")
            if proj_grad:
                lines.append(f"Projected graduation: {proj_grad}")
            semester_plans = p.get("semester_plans") or []
            roadmap = p.get("roadmap") or p.get("semesters") or []
            planned = p.get("planned_courses") or p.get("recommended_courses") or []
            if semester_plans:
                lines.append("Graduation roadmap:")
                for sem in semester_plans[:10]:
                    if isinstance(sem, dict):
                        label = sem.get("semester_label") or sem.get("semester", "Semester")
                        total_cr = sem.get("total_credits", "")
                        cgpa_after = sem.get("simulated_cgpa_after")
                        is_final = sem.get("is_final_semester", False)
                        courses = sem.get("courses") or []
                        course_labels = []
                        for c in courses[:8]:
                            if isinstance(c, dict):
                                cc = c.get("course_code", "")
                                cn = c.get("course_name", "")
                                cr = c.get("credits")
                                # Name-first: "Course Name (CODE) — N credits"
                                cl = _fmt_course_label(cc, cn)
                                if cr:
                                    cl += f" — {cr} credits"
                                course_labels.append(cl)
                            else:
                                course_labels.append(str(c))
                        sem_line = f"  {label} ({total_cr} cr)"
                        if cgpa_after is not None:
                            sem_line += f" — Projected CGPA: {cgpa_after:.2f}"
                        if is_final:
                            sem_line += " [Graduation semester]"
                        lines.append(sem_line)
                        for cl in course_labels:
                            lines.append(f"    • {cl}")
            elif roadmap:
                lines.append("Graduation roadmap:")
                for sem in roadmap[:8]:
                    if isinstance(sem, dict):
                        label = sem.get("semester", "Semester")
                        courses = sem.get("courses", [])
                        lines.append(f"  {label}: {', '.join(str(c) for c in courses[:8])}")
                    else:
                        lines.append(f"  • {sem}")
            elif planned:
                lines.append("Recommended courses for next semester:")
                for c in planned[:10]:
                    lines.append(f"  • {c}")
            total = p.get("total_semesters") or p.get("total_passes")
            if total:
                lines.append(f"Estimated semesters remaining: {total}")
            disclaimer = p.get("simulation_disclaimer")
            if disclaimer and semester_plans:
                lines.append(f"Note: {disclaimer}")
        for w in (p.get("warnings") or []):
            lines.append(f"Warning: {w}")

    elif intent == "get_course_info":
        # Handle not-found with candidate
        if p.get("error"):
            candidate = p.get("attempted_candidate")
            if candidate:
                lines.append(f"I couldn't find a course named '{candidate}' in the current CIS catalogue.")
            else:
                lines.append("Course details are not available.")
            return
        code = p.get("course_code", "")
        name = p.get("name", "")
        label = _fmt_course_label(code, name) if (code and name) else (name or code)
        if label:
            lines.append(label)
        credits = p.get("credits")
        if credits is not None:
            lines.append(f"Credits: {credits}")
        level = p.get("level")
        if level is not None:
            lines.append(f"Level: {level}")
        semester_offering = p.get("semester_offering")
        if semester_offering:
            lines.append(f"Offered: {semester_offering}")
        tracks = p.get("tracks")
        if tracks:
            if isinstance(tracks, list):
                lines.append(f"Tracks: {', '.join(str(t) for t in tracks)}")
            else:
                lines.append(f"Track: {tracks}")
        credit_threshold = p.get("credit_threshold")
        if credit_threshold is not None:
            lines.append(f"Credit threshold required: {credit_threshold}")
        desc = p.get("description", "")
        if desc:
            lines.append(desc)

    elif intent == "get_course_prerequisites":
        # Handle not-found with candidate — show what was searched
        if p.get("error"):
            candidate = p.get("attempted_candidate")
            if candidate:
                lines.append(f"I couldn't find prerequisite information for '{candidate}'. The course may not be in the current CIS catalogue.")
            else:
                lines.append("Prerequisite information is not available for this course.")
            return
        code = p.get("course_code", "the course")
        name = p.get("name", "")
        course_label = _fmt_course_label(code, name) if (code and name) else (name or code or "the course")
        direct = p.get("direct_prerequisites") or []
        non_course = p.get("non_course_prerequisites") or []
        if not direct and not non_course:
            lines.append(f"{course_label} has no prerequisites.")
        else:
            lines.append(f"Prerequisites for {course_label}:")
            for pr in direct:
                c = pr.get("course_code", pr) if isinstance(pr, dict) else pr
                lines.append(f"  • {c}")
            for nc in non_course:
                if isinstance(nc, dict):
                    lines.append(f"  • {nc.get('description', nc)}")

    elif intent == "get_skills_taught":
        code = p.get("course_code", "")
        name = p.get("name", "")
        course_label = _fmt_course_label(code, name) if (code and name) else (name or code)
        skills = p.get("skills") or p.get("skills_taught") or []
        if p.get("error"):
            candidate = p.get("attempted_candidate")
            if candidate:
                lines.append(f"I couldn't find mapped skills for '{candidate}'. The course may not be in the current curriculum data.")
            else:
                lines.append(f"No skill data found for {course_label or 'this course'}.")
            return
        if skills:
            lines.append(f"Skills taught in {course_label or code}:")
            for s in skills[:15]:
                if isinstance(s, dict):
                    skill_name = _fmt_skill_label(s.get("skill_id", ""), s.get("name", "") or s.get("skill_name", ""))
                else:
                    skill_name = _fmt_skill_label(str(s))
                lines.append(f"  • {skill_name}")
        else:
            lines.append(f"I couldn't find mapped skills for {course_label or code} in the current curriculum data.")

    elif intent == "search_courses_by_skill":
        raw_skill_id = p.get("skill_id", "")
        skill_name = p.get("skill_name", "") or p.get("skill_display", "")
        skill_display = _fmt_skill_label(raw_skill_id, skill_name) if raw_skill_id else skill_name
        if not skill_display:
            skill_display = p.get("attempted_skill", "that skill")
        courses = p.get("courses") or p.get("results") or []
        if p.get("error"):
            attempted = p.get("attempted_skill") or skill_display
            lines.append(f"I couldn't find a skill matching '{attempted}' in the current curriculum data.")
            return
        # Topic fallback: course intent rerouted to skill search — explain the shift
        if p.get("topic_fallback"):
            original = p.get("original_mention", "")
            if original:
                lines.append(
                    f"I don't see a course literally named '{original}' in the catalogue, "
                    f"but {skill_display} is a topic/skill in the curriculum. "
                    f"Courses that cover it:"
                )
            else:
                lines.append(f"Courses covering {skill_display} as a topic/skill:")
        elif courses:
            lines.append(f"Courses covering {skill_display}:")
        for c in courses[:15]:
            label = _safe_code_name(c, "course_code", "name") if isinstance(c, dict) else str(c)
            lines.append(f"  • {label}")
        if not courses:
            lines.append(f"No courses found for skill: {skill_display}.")

    elif intent == "get_role_profile":
        role_id = p.get("role_id", "")
        name = p.get("name", "")
        # Prefer name; clean up raw RL_* ID
        label = _fmt_role_label(role_id, name)
        if label:
            lines.append(label)
        desc = p.get("description", "")
        if desc:
            lines.append(desc)
        skills = p.get("required_skills") or []
        if skills:
            lines.append("Required skills:")
            for s in skills[:15]:
                if isinstance(s, dict):
                    skill_label = _fmt_skill_label(s.get("skill_id", ""), s.get("name", ""))
                else:
                    skill_label = _fmt_skill_label(str(s))
                lines.append(f"  • {skill_label}")

    elif intent == "get_roles_by_track":
        track_id = p.get("track_id", "")
        track_name = p.get("track_name", "")
        track_display = _fmt_track_label(track_id, track_name) if track_id else (track_name or "your track")
        roles = p.get("roles") or []
        if roles:
            lines.append(f"Roles connected to the {track_display} track:")
            for r in roles[:15]:
                if isinstance(r, dict):
                    label = _fmt_role_label(r.get("role_id", ""), r.get("name", ""))
                else:
                    label = _fmt_role_label(str(r))
                lines.append(f"  • {label}")
        else:
            lines.append(f"No roles found for track: {track_display}.")

    elif intent == "compute_skill_gap":
        role_name = p.get("role_name", "")
        role_id = p.get("role_id", "")
        role_display = _fmt_role_label(role_id, role_name) or "the target role"
        missing = p.get("missing_skills") or []
        covered = p.get("covered_skills") or []
        gap_count = p.get("skill_gap_count")
        lines.append(f"Curriculum-skill gap analysis for {role_display}:")
        if gap_count is not None:
            lines.append(f"  Missing skills: {gap_count}")
        elif missing:
            lines.append(f"  Missing skills ({len(missing)}):")
            for s in missing[:10]:
                if isinstance(s, dict):
                    skill_label = _fmt_skill_label(s.get("skill_id", ""), s.get("name", ""))
                else:
                    skill_label = _fmt_skill_label(str(s))
                lines.append(f"    • {skill_label}")
        if covered:
            lines.append(f"  Skills already covered ({len(covered)}):")
            for s in covered[:10]:
                if isinstance(s, dict):
                    skill_label = _fmt_skill_label(s.get("skill_id", ""), s.get("name", ""))
                    cb_list = s.get("covered_by") or []
                    cb_parts: list[str] = []
                    for cb in cb_list:
                        if isinstance(cb, dict):
                            code = cb.get("course_code", "")
                            name = cb.get("name")
                            if name and code:
                                cb_parts.append(f"{name} ({code})")
                            elif code:
                                cb_parts.append(code)
                        elif isinstance(cb, str):
                            cb_parts.append(cb)
                    if cb_parts:
                        lines.append(f"    • {skill_label} — covered by {', '.join(cb_parts)}")
                    else:
                        lines.append(f"    • {skill_label}")
                else:
                    lines.append(f"    • {_fmt_skill_label(str(s))}")

    elif intent == "compute_alignment_score":
        role_name = p.get("role_name", "")
        role_id = p.get("role_id", "")
        role_display = _fmt_role_label(role_id, role_name) or "the target role"
        pct_str = p.get("alignment_pct_display") or _resolve_alignment_pct(p) or "N/A"
        lines.append(f"Your curriculum alignment with the {role_display} role: {pct_str}")

    elif intent == "recommend_courses_to_close_gap":
        role_name = p.get("role_name", "")
        role_id = p.get("role_id", "")
        role_display = _fmt_role_label(role_id, role_name) or "the target role"
        courses = p.get("recommended_courses") or []
        lines.append(f"Courses to strengthen your skill coverage for {role_display}:")
        for c in courses[:15]:
            label = _safe_code_name(c, "course_code", "name") if isinstance(c, dict) else str(c)
            lines.append(f"  • {label}")

    elif intent == "find_best_matching_roles":
        roles = p.get("ranked_roles") or []
        if roles:
            lines.append("Best matching roles based on your completed courses:")
            for i, r in enumerate(roles[:10], 1):
                if isinstance(r, dict):
                    label = _fmt_role_label(r.get("role_id", ""), r.get("name", ""))
                    score = r.get("alignment_score")
                    suffix = f" ({score:.0%} curriculum alignment)" if isinstance(score, (int, float)) else ""
                    lines.append(f"  {i}. {label}{suffix}")
                else:
                    lines.append(f"  {i}. {r}")
        else:
            lines.append("No matching roles found for your completed courses.")

    elif intent == "estimate_alignment_improvement":
        role_name = p.get("role_name", "")
        role_id = p.get("role_id", "")
        role_display = _fmt_role_label(role_id, role_name) or "the target role"
        # Prefer percentage fields (0-100) from KG; fall back to score fields (0-1)
        curr_pct = p.get("current_alignment_percentage")
        proj_pct = p.get("projected_alignment_percentage")
        curr_score = p.get("current_alignment_score") or p.get("current_alignment")
        proj_score = (
            p.get("projected_alignment_score")
            or p.get("new_alignment")
            or p.get("projected_improvement")
        )
        if isinstance(curr_pct, (int, float)) and isinstance(proj_pct, (int, float)):
            delta = proj_pct - curr_pct
            delta_str = f"{'+' if delta >= 0 else ''}{delta:g}%"
            lines.append(
                f"Estimated alignment with {role_display}: "
                f"{curr_pct:g}% → {proj_pct:g}% ({delta_str})"
            )
        elif isinstance(curr_score, (int, float)) and isinstance(proj_score, (int, float)):
            delta = proj_score - curr_score
            delta_str = f"{'+' if delta >= 0 else ''}{delta:.0%}"
            lines.append(
                f"Estimated alignment with {role_display}: "
                f"{curr_score:.0%} → {proj_score:.0%} ({delta_str})"
            )
        else:
            msg = p.get("message") or ""
            if msg and "ready" not in msg.lower():
                lines.append(msg)
            else:
                lines.append(
                    f"Could not compute alignment improvement for {role_display}. "
                    "Make sure the planned courses are identified (resolved course codes) and try again."
                )
        newly = p.get("newly_covered_skills") or []
        if newly:
            lines.append(f"Newly covered skills ({len(newly)}):")
            for sk in newly[:5]:
                sname = sk.get("name", sk.get("skill_id", "?")) if isinstance(sk, dict) else str(sk)
                lines.append(f"  • {sname}")
        unresolved = p.get("unresolved_planned_names") or []
        if unresolved:
            lines.append(f"Note: Could not identify: {', '.join(str(n) for n in unresolved[:5])}.")

    elif intent == "get_focus_courses_for_target":
        role_name = p.get("role_name", "")
        role_id = p.get("role_id", "")
        track_id = p.get("track_id", "")
        if role_id or role_name:
            target = _fmt_role_label(role_id, role_name)
        elif track_id:
            target = _fmt_track_label(track_id)
        else:
            target = "your target"
        personalized = p.get("personalized_focus", False)
        completed_excluded = p.get("completed_courses_excluded", 0)
        courses = p.get("focus_courses") or p.get("recommended_courses") or []
        _FOCUS_CAP = 8
        if courses:
            if personalized and completed_excluded:
                lines.append(
                    f"Based on what you haven't completed yet, key courses for {target}:"
                )
            else:
                lines.append(f"Key courses to focus on for {target}:")
            for c in courses[:_FOCUS_CAP]:
                label = _safe_code_name(c, "course_code", "name") if isinstance(c, dict) else str(c)
                lines.append(f"  • {label}")
            if len(courses) > _FOCUS_CAP:
                lines.append(f"  … and {len(courses) - _FOCUS_CAP} more.")
        else:
            if personalized and completed_excluded:
                lines.append(
                    f"No remaining focus courses found for {target} — "
                    "you may have already covered the key areas."
                )
            else:
                lines.append(f"No focus courses found for {target}.")

    elif intent == "get_track_overview":
        track_id = p.get("track_id", "")
        name = p.get("name", "")
        label = _fmt_track_label(track_id, name) if (track_id or name) else ""
        if label:
            lines.append(label)
        desc = p.get("description", "")
        if desc:
            lines.append(desc)
        courses = p.get("courses") or []
        if courses:
            lines.append(f"Courses in this track ({len(courses)}):")
            for c in courses[:10]:
                cl = _safe_code_name(c, "course_code", "name") if isinstance(c, dict) else str(c)
                lines.append(f"  • {cl}")

    elif intent == "compare_tracks":
        t1_id = p.get("track_id_1", "")
        t1_name = p.get("track_1_name", "")
        t2_id = p.get("track_id_2", "")
        t2_name = p.get("track_2_name", "")
        t1 = _fmt_track_label(t1_id, t1_name) if (t1_id or t1_name) else "Track 1"
        t2 = _fmt_track_label(t2_id, t2_name) if (t2_id or t2_name) else "Track 2"
        shared = p.get("shared_courses") or []
        diff = p.get("different_courses") or {}
        lines.append(f"Comparing {t1} and {t2}:")
        if shared:
            lines.append(f"  Shared courses: {len(shared)}")
        if isinstance(diff, dict):
            for tk, courses in diff.items():
                if isinstance(courses, list) and courses:
                    tk_display = _fmt_track_label(tk)
                    lines.append(f"  Unique to {tk_display}: {len(courses)} course(s)")
        elif isinstance(diff, list) and diff:
            lines.append(f"  Different courses: {len(diff)}")

    elif intent in ("recommend_track_for_role", "recommend_track_for_skill"):
        # KG returns ranked_tracks; fall back to legacy fields for backward compatibility
        ranked = p.get("ranked_tracks")
        if not ranked:
            ranked = p.get("recommended_track") or p.get("ranking")

        if intent == "recommend_track_for_role":
            role_display = _fmt_role_label(p.get("role_id", ""), p.get("role_name", "")) or "this role"
            header = f"Recommended tracks for a {role_display} career path:"
        else:
            skill_display = _fmt_skill_label(p.get("skill_id", ""), p.get("skill_name", "")) or "this skill"
            header = f"Recommended tracks to develop {skill_display}:"

        if isinstance(ranked, list) and ranked:
            lines.append(header)
            for t in ranked[:5]:
                if isinstance(t, dict):
                    tid = t.get("track_id", "")
                    tname = t.get("track_name") or t.get("name", "")
                    label = _fmt_track_label(tid, tname)
                    rank = t.get("rank")
                    score = t.get("alignment_score")
                    course_count = t.get("course_count")
                    if isinstance(score, (int, float)):
                        label += f" — {score:.0%} alignment"
                    elif course_count is not None:
                        label += f" — {course_count} course(s)"
                    prefix = f"  {rank}." if rank is not None else "  •"
                    lines.append(f"{prefix} {label}")
                else:
                    lines.append(f"  • {_fmt_track_label(str(t))}")
            total = p.get("total_tracks_evaluated")
            if total is not None:
                lines.append(f"(Evaluated {total} tracks in total.)")
        elif ranked:
            lines.append(f"Recommended track: {_fmt_track_label(str(ranked))}")
        else:
            lines.append("No track recommendation is available in the current curriculum data.")

    elif intent == "policy_query":
        answer = p.get("answer", "")
        facts = p.get("extracted_facts") or []
        if p.get("evidence_limited"):
            lines.append("Note: Handbook evidence for this topic was limited.")
        if answer:
            lines.append(answer)
        elif facts:
            for f in facts[:10]:
                lines.append(f"  • {f}")
        else:
            lines.append("No policy information found for your question.")

    elif intent == "get_student_record":
        if p.get("assumptions_cleared"):
            lines.append(
                p.get("message")
                or "I cleared your what-if assumptions. You are back to your official academic record."
            )
            # Do not dump full record after reset unless focus is full_record
            record_focus = p.get("record_focus") or "reset_assumptions"
            if record_focus in ("reset_assumptions", "assumption_acknowledgement"):
                return

        record_focus = p.get("record_focus") or "full_record"
        response_style = p.get("response_style") or "normal"
        cgpa = p.get("cgpa")
        track_id = p.get("track_id", "")
        program = p.get("program", "")
        level = p.get("level")
        level_display = p.get("level_display") or (_LEVEL_DISPLAY.get(level) if level is not None else None)
        standing = p.get("academic_standing", "")
        chs = p.get("total_credit_hours_earned")
        scenario_credits = p.get("scenario_completed_credits")
        last_sem_gpa = p.get("last_semester_gpa")
        current_sem = p.get("current_semester", "")
        consecutive_warnings = p.get("consecutive_warnings") or 0
        total_warnings = p.get("total_warnings") or 0
        study_status = p.get("study_status", "")
        completed_details = p.get("completed_course_details") or []
        in_progress_details = p.get("in_progress_course_details") or []
        failed_details = p.get("failed_course_details") or []
        completed = p.get("completed_courses") or []
        in_progress = p.get("in_progress_courses") or []
        failed = p.get("failed_courses") or []
        assumed_failed = p.get("assumed_failed_courses") or []
        assumed_passed = p.get("assumed_passed_courses") or []
        override_active = bool(p.get("override_state_active"))

        # Assumption acknowledgement: not a full record display
        if record_focus == "assumption_acknowledgement":
            parts_ack: list[str] = []
            if assumed_failed:
                for code in assumed_failed:
                    # Find display name from failed_details or use code
                    detail = next((d for d in failed_details if d.get("course_code") == code), None)
                    label = _render_course_detail(detail) if detail else code
                    parts_ack.append(
                        f"I added a what-if assumption for this session: "
                        f"{label} is treated as failed. "
                        f"Your official academic record is unchanged."
                    )
            if assumed_passed:
                for code in assumed_passed:
                    detail = next((d for d in completed_details if d.get("course_code") == code), None)
                    label = _render_course_detail(detail) if detail else code
                    parts_ack.append(
                        f"I added a what-if assumption for this session: "
                        f"{label} is treated as passed. "
                        f"Your official academic record is unchanged."
                    )
            if not parts_ack:
                parts_ack.append("Assumption noted. Your official academic record is unchanged.")
            lines.extend(parts_ack)
            return

        # Build what-if preamble when assumptions are active (non-acknowledgement focus)
        if override_active and (assumed_failed or assumed_passed):
            scenario_parts: list[str] = []
            for code in assumed_failed:
                detail = next((d for d in failed_details if d.get("course_code") == code), None)
                label = _render_course_detail(detail) if detail else code
                scenario_parts.append(f"{label} treated as failed")
            for code in assumed_passed:
                detail = next((d for d in completed_details if d.get("course_code") == code), None)
                label = _render_course_detail(detail) if detail else code
                scenario_parts.append(f"{label} treated as passed")
            if scenario_parts:
                lines.append(
                    f"Note: This is a what-if view. Active assumptions: "
                    + ", ".join(scenario_parts)
                    + ". Your official academic record is unchanged."
                )

        # ── Focus-specific narration ───────────────────────────────────────────

        if record_focus == "cgpa":
            if cgpa is not None:
                answer = f"Your current CGPA is {cgpa:.2f}."
            else:
                answer = "Your CGPA is not available in our records."
            lines.append(answer)
            return

        if record_focus == "last_semester_gpa":
            if last_sem_gpa is not None:
                lines.append(f"Your last-semester GPA is {last_sem_gpa:.2f}.")
            else:
                lines.append("Your last-semester GPA is not available in our records.")
            return

        if record_focus == "academic_level":
            if level_display:
                lines.append(f"Your academic level is Level {level} ({level_display}).")
            elif level is not None:
                lines.append(f"Your academic level is Level {level}.")
            else:
                lines.append("Your academic level is not available in our records.")
            return

        if record_focus == "academic_standing":
            if standing == "good":
                cgpa_note = f" (CGPA: {cgpa:.2f})" if cgpa is not None else ""
                warn_note = ""
                if total_warnings == 0:
                    warn_note = " and no academic warnings recorded"
                lines.append(f"No — you are in good academic standing{cgpa_note}{warn_note}.")
            elif standing == "warning":
                cgpa_note = f" (CGPA: {cgpa:.2f})" if cgpa is not None else ""
                lines.append(f"You are under academic warning{cgpa_note}.")
            else:
                lines.append("Your academic standing is not available in our records.")
            return

        if record_focus == "probation_status":
            if standing == "warning":
                lines.append("Yes — you are under academic warning, which may lead to probation if unresolved.")
            elif standing == "good":
                lines.append("No — you are not on academic probation. You are in good standing.")
            else:
                lines.append("Your probation status is not available in our records.")
            return

        if record_focus == "academic_warnings":
            if consecutive_warnings > 0:
                lines.append(
                    f"You have {consecutive_warnings} consecutive warning(s) "
                    f"and {total_warnings} total warning(s) on record."
                )
            else:
                lines.append("You have no academic warnings on record.")
            return

        if record_focus == "completed_credits":
            if chs is not None:
                lines.append(f"You have completed {chs} credit hours.")
                if scenario_credits is not None and scenario_credits != chs:
                    lines.append(
                        f"In your active what-if scenario, completed-course credits total {scenario_credits}."
                    )
            else:
                lines.append("Your completed credit hours are not available in our records.")
            return

        if record_focus == "track":
            if track_id:
                lines.append(f"You are in the {_fmt_track_label(track_id)} track.")
                if program:
                    lines.append(f"Program: {program}.")
            else:
                lines.append("Your track information is not available in our records.")
            return

        if record_focus == "current_semester":
            if current_sem:
                lines.append(f"You are currently in {current_sem}.")
            else:
                lines.append("Your current semester is not available in our records.")
            return

        if record_focus == "study_status":
            if study_status:
                lines.append(f"Your study status is: {study_status}.")
            else:
                lines.append("Your study status is not available in our records.")
            return

        if record_focus == "completed_courses":
            if completed_details:
                lines.append(f"Completed courses ({len(completed_details)}):")
                for d in completed_details[:30]:
                    lines.append(f"  • {_render_course_detail(d)}")
            elif completed:
                lines.append(f"You have completed {len(completed)} course(s).")
            else:
                lines.append("No completed courses found in your record.")
            if response_style == "normal" and standing == "good" and not override_active:
                lines.append("Keep it up!")
            return

        if record_focus == "in_progress_courses":
            if in_progress_details:
                sem_note = f" — {current_sem}" if current_sem else ""
                lines.append(f"In-progress courses ({len(in_progress_details)}){sem_note}:")
                for d in in_progress_details[:30]:
                    lines.append(f"  • {_render_course_detail(d)}")
            elif in_progress:
                lines.append(f"You are currently enrolled in {len(in_progress)} course(s).")
            else:
                lines.append("No in-progress courses found in your record.")
            if response_style == "normal" and not override_active:
                lines.append("Good luck with your studies!")
            return

        if record_focus == "failed_courses":
            if failed_details:
                lines.append(f"Failed courses ({len(failed_details)}):")
                for d in failed_details[:30]:
                    lines.append(f"  • {_render_course_detail(d)}")
                if override_active and assumed_failed:
                    lines.append(
                        "Note: The list above reflects your active what-if scenario. "
                        "Officially, your failed courses may differ."
                    )
            elif failed:
                if override_active and assumed_failed:
                    lines.append(
                        f"In your active what-if scenario: {len(failed)} course(s) treated as failed. "
                        f"Officially, you have no failed courses recorded."
                    )
                else:
                    lines.append(f"You have {len(failed)} failed course(s) on record.")
            else:
                if override_active and assumed_failed:
                    for code in assumed_failed:
                        lines.append(
                            f"Officially, you have no failed courses recorded. "
                            f"In your active what-if scenario, {code} is treated as failed."
                        )
                else:
                    lines.append("You have no failed courses on record.")
            return

        if record_focus == "failed_course_history":
            failed_history = p.get("failed_history_details") or []
            failed_history_codes_list = p.get("failed_history_codes") or []
            if failed_history:
                lines.append(f"Courses you have failed at least once historically ({len(failed_history)}):")
                for d in failed_history[:30]:
                    lines.append(f"  • {_render_course_detail(d)}")
                lines.append("Note: This includes courses you may have later retaken and passed.")
            elif failed_history_codes_list:
                lines.append(f"Historically failed courses: {', '.join(str(c) for c in failed_history_codes_list[:15])}")
            else:
                lines.append("No historically failed courses found in your record.")
            return

        if record_focus == "course_status_check":
            checked_codes: list[str] = p.get("checked_course_codes") or []
            _status_filter: str = p.get("status_filter") or ""
            display_labels: dict[str, str] = p.get("course_display_labels") or {}

            if checked_codes:
                # Multi-course status check: report matched vs unmatched per category
                found_completed = {d.get("course_code") for d in completed_details if isinstance(d, dict)}
                found_in_progress = {d.get("course_code") for d in in_progress_details if isinstance(d, dict)}
                found_failed = {d.get("course_code") for d in failed_details if isinstance(d, dict)}

                checked_meta_map: dict = p.get("checked_course_metadata") or {}

                def _cl(code: str) -> str:
                    # Prefer enriched status-confirmed detail dicts
                    for dl in list(completed_details) + list(in_progress_details) + list(failed_details):
                        if isinstance(dl, dict) and dl.get("course_code") == code:
                            return _render_course_detail(dl)
                    # Use startup metadata cache
                    _meta = checked_meta_map.get(code)
                    if isinstance(_meta, dict) and _meta.get("name"):
                        return f"{_meta['name']} ({code})"
                    return display_labels.get(code) or code

                matched_completed = [c for c in checked_codes if c in found_completed]
                matched_in_progress = [c for c in checked_codes if c in found_in_progress]
                matched_failed = [c for c in checked_codes if c in found_failed]
                matched_any = set(matched_completed) | set(matched_in_progress) | set(matched_failed)
                not_matched = [c for c in checked_codes if c not in matched_any]

                if _status_filter == "completed":
                    if matched_completed:
                        lines.append(f"Of those, you have completed: {', '.join(_cl(c) for c in matched_completed)}.")
                    if not_matched:
                        lines.append(f"Not found in your completed courses: {', '.join(_cl(c) for c in not_matched)}.")
                    if not matched_completed and not not_matched:
                        lines.append("None of those courses were found in your completed courses.")
                elif _status_filter == "in_progress":
                    if matched_in_progress:
                        lines.append(f"You are currently enrolled in: {', '.join(_cl(c) for c in matched_in_progress)}.")
                    if not_matched:
                        lines.append(f"Not currently in progress: {', '.join(_cl(c) for c in not_matched)}.")
                elif _status_filter == "failed":
                    if matched_failed:
                        lines.append(f"Failed courses from that list: {', '.join(_cl(c) for c in matched_failed)}.")
                    if not_matched:
                        lines.append(f"Not in your failed courses: {', '.join(_cl(c) for c in not_matched)}.")
                else:
                    if matched_completed:
                        lines.append(f"Completed: {', '.join(_cl(c) for c in matched_completed)}.")
                    if matched_in_progress:
                        lines.append(f"In progress: {', '.join(_cl(c) for c in matched_in_progress)}.")
                    if matched_failed:
                        lines.append(f"Failed: {', '.join(_cl(c) for c in matched_failed)}.")
                    if not_matched:
                        lines.append(f"Not found in your record: {', '.join(_cl(c) for c in not_matched)}.")
                return

            # Single-course or unenriched fallback (original behavior)
            if in_progress_details:
                for d in in_progress_details[:5]:
                    lines.append(f"Yes — {_render_course_detail(d)} is in your current courses.")
                return
            if completed_details:
                for d in completed_details[:5]:
                    lines.append(f"Yes — you have completed {_render_course_detail(d)}.")
                return
            if failed_details:
                for d in failed_details[:5]:
                    lines.append(f"{_render_course_detail(d)} appears in your failed courses.")
                return
            # Fallback: show all current courses if no specific target was enriched
            if in_progress:
                sem_note = f" in {current_sem}" if current_sem else ""
                lines.append(f"Your current courses{sem_note}:")
                in_p_meta_map: dict = p.get("in_progress_course_metadata") or {}
                for c in in_progress[:10]:
                    _m = in_p_meta_map.get(c)
                    if isinstance(_m, dict) and _m.get("name"):
                        lines.append(f"  • {_m['name']} ({c})")
                    else:
                        lines.append(f"  • {c}")
            if completed:
                lines.append(f"Completed: {len(completed)} course(s)")
            return

        # ── Full record / progress summary (default) ──────────────────────────
        record_parts: list[str] = []
        if track_id:
            record_parts.append(f"Track: {_fmt_track_label(track_id)}")
        if level_display:
            record_parts.append(f"Level: {level} ({level_display})")
        elif level is not None:
            record_parts.append(f"Level: {level}")
        if cgpa is not None:
            record_parts.append(f"CGPA: {cgpa:.2f}")
        if standing:
            record_parts.append(f"Standing: {standing}")
        if chs is not None:
            record_parts.append(f"Credit hours earned: {chs}")
        if current_sem:
            record_parts.append(f"Semester: {current_sem}")
        if record_parts:
            lines.append(" | ".join(record_parts))

        if last_sem_gpa is not None:
            lines.append(f"Last semester GPA: {last_sem_gpa:.2f}")

        if completed_details:
            lines.append(f"Completed courses ({len(completed_details)}):")
            for d in completed_details[:30]:
                lines.append(f"  • {_render_course_detail(d)}")
        elif completed:
            lines.append(f"Completed: {len(completed)} course(s)")

        if in_progress_details:
            lines.append(f"In-progress courses ({len(in_progress_details)}):")
            for d in in_progress_details[:30]:
                lines.append(f"  • {_render_course_detail(d)}")
        elif in_progress:
            lines.append(f"In progress: {', '.join(str(c) for c in in_progress[:10])}")

        if failed_details:
            lines.append(f"Failed courses ({len(failed_details)}):")
            for d in failed_details[:30]:
                lines.append(f"  • {_render_course_detail(d)}")
        elif failed:
            lines.append(f"Failed: {len(failed)} course(s)")

        if scenario_credits is not None and scenario_credits != chs:
            lines.append(
                f"Official earned credits: {chs}. "
                f"In this what-if scenario, completed-course credits total {scenario_credits}."
            )

    else:
        msg = p.get("message") or p.get("answer") or p.get("result", "")
        lines.append(str(msg) if msg else f"Result for {intent} is available.")


def _citations_text_from_packets(packets: list[dict]) -> str:
    seen: set = set()
    entries: list[str] = []
    for p in packets:
        for c in (p.get("citations") or []):
            if not isinstance(c, dict):
                continue
            source = c.get("source", "")
            page = c.get("page")
            key = (source, page)
            if source and key not in seen:
                seen.add(key)
                entry = source
                if page is not None:
                    entry += f", p.{page}"
                entries.append(entry)
    return ("Sources: " + "; ".join(entries)) if entries else ""


# ── LLM model chain ───────────────────────────────────────────────────────────

def _composer_models() -> tuple[str, list[str]]:
    primary = os.getenv("COMPOSER_PRIMARY_MODEL", _DEFAULT_PRIMARY)
    raw_fallbacks = os.getenv(
        "COMPOSER_FALLBACK_MODELS", ",".join(_DEFAULT_FALLBACKS)
    )
    fallbacks = [m.strip() for m in raw_fallbacks.split(",") if m.strip()]
    return primary, fallbacks


def _strip_think_tags(text: str) -> str:
    """Remove <think>…</think> reasoning blocks emitted by models like Qwen3."""
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


import re as _re
_OFF_SCRIPT_RE = _re.compile(
    r"(?:please\s+(?:share|provide|tell\s+me)|i.?ll\s+need\s+(?:more\s+)?(?:info|detail|context)|"
    r"could\s+you\s+(?:share|provide|tell\s+me|clarify\s+which)|"
    r"let\s+me\s+know\s+(?:your|which|what\s+(?:course|track|role|gpa|cgpa))|"
    r"can\s+you\s+(?:share|provide)\s+(?:your|the)\s+(?:gpa|cgpa|courses|credits|record|transcript)|"
    r"(?:please\s+)?(?:provide|share)\s+(?:your|the)\s+(?:completed\s+)?(?:courses|gpa|credits|record|transcript))",
    _re.IGNORECASE,
)


def _is_off_script(answer: str) -> bool:
    """Return True if the LLM started asking the student for information."""
    return bool(_OFF_SCRIPT_RE.search(answer))


def _strip_fabricated_sources(text: str) -> str:
    """Remove trailing Sources/References section when LLM adds it without real citations."""
    import re
    text = re.sub(
        r"\n+[ \t]*\*{0,2}(?:Sources?|References?)\*{0,2}:.*",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return text.strip()


def _classify_composer_failure(exc: Exception) -> str:
    """Classify a Composer chain failure into a diagnostic category."""
    if isinstance(exc, LLMError):
        msg = str(exc).lower()
        if "timed out" in msg or "timeout" in msg:
            return "timeout"
        if "http 429" in msg:
            return "rate_limit"
        if "http 413" in msg:
            return "payload_too_large"
        if "not valid json" in msg or "not a json object" in msg:
            return "invalid_json"
        return "llm_error"
    return "unknown"


def _try_llm_chain(
    llm: LLMClient,
    user_msg: str,
    primary: str,
    fallbacks: list[str],
    timeout_seconds: float,
    trace_id: str = "",
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Try primary then fallback models. Returns (answer, model_used, failure_reason)."""
    all_models = [primary] + fallbacks
    n_models = len(all_models)
    sys_chars = len(_SYSTEM_PROMPT)
    user_chars = len(user_msg)
    est_tokens = (sys_chars + user_chars + 3) // 4
    had_error = False
    had_empty = False

    for attempt_idx, model in enumerate(all_models):
        _attempt_t0 = time.monotonic()
        logger.info(
            "Composer.model_attempt trace_id=%s attempt=%d/%d model=%s "
            "timeout=%.1fs estimated_tokens=%d",
            trace_id, attempt_idx + 1, n_models, model, timeout_seconds, est_tokens,
        )
        try:
            answer = llm.chat(
                system=_SYSTEM_PROMPT,
                user=user_msg,
                temperature=0.25,
                model=model,
                timeout_seconds=timeout_seconds,
            )
            answer = _strip_think_tags(answer or "")
            _attempt_ms = int((time.monotonic() - _attempt_t0) * 1000)
            if answer:
                logger.info(
                    "Composer.model_attempt_result trace_id=%s attempt=%d/%d model=%s "
                    "result=success answer_len=%d duration_ms=%d "
                    "exact_token_usage=not_available",
                    trace_id, attempt_idx + 1, n_models, model,
                    len(answer), _attempt_ms,
                )
                return answer, model, None
            logger.warning(
                "Composer.model_attempt_result trace_id=%s attempt=%d/%d model=%s "
                "result=empty duration_ms=%d trying_next",
                trace_id, attempt_idx + 1, n_models, model, _attempt_ms,
            )
            had_empty = True
        except LLMNotConfigured:
            logger.info(
                "Composer.model_attempt_result trace_id=%s result=not_configured "
                "— skipping LLM path.",
                trace_id,
            )
            return None, None, "llm_not_configured"
        except LLMError as exc:
            _attempt_ms = int((time.monotonic() - _attempt_t0) * 1000)
            fail_cat = _classify_composer_failure(exc)
            logger.warning(
                "Composer.model_attempt_result trace_id=%s attempt=%d/%d model=%s "
                "result=failed error_type=%s error_category=%s "
                "error=%s duration_ms=%d trying_next",
                trace_id, attempt_idx + 1, n_models, model,
                type(exc).__name__, fail_cat, str(exc)[:120], _attempt_ms,
            )
            had_error = True

    failure_reason = "all_models_failed" if had_error else "empty_response"
    logger.warning(
        "Composer.model_chain failure_reason=%s trace_id=%s — using deterministic fallback.",
        failure_reason, trace_id,
    )
    return None, None, failure_reason


# ── Citation collection ───────────────────────────────────────────────────────

def _collect_citations(results: list[PerSQResult]) -> list[Citation]:
    """Merge and deduplicate citations from all PerSQResults."""
    seen: set = set()
    out: list[Citation] = []
    for r in results:
        for c in (r.citations or []):
            if not isinstance(c, dict):
                continue
            source = c.get("source", "")
            page = c.get("page")
            key = (source, page)
            if source and key not in seen:
                seen.add(key)
                out.append(Citation(source=source, page=page))
    return out


# ── Status mapping ────────────────────────────────────────────────────────────

def _map_turn_status(turn: TurnWrapper) -> str:
    """Map TurnWrapper.turn_status to the QueryResponse status literal."""
    if turn.turn_status == "needs_clarification":
        return "clarification_needed"
    if turn.turn_status == "failed":
        return "error"
    # completed / partial_success / out_of_scope / informational → ok
    return "ok"


# ── Public API ────────────────────────────────────────────────────────────────

class ResponseComposer:
    """
    LLM-centered narration layer.

    Public API:
        compose(user_text, turn, session_id, session_name) -> QueryResponse

    LLM is primary; deterministic fallback always available.
    """

    def __init__(self) -> None:
        self._llm: LLMClient = get_llm_client()
        self._use_llm: bool = (
            os.getenv("COMPOSER_USE_LLM", "true").lower() not in ("false", "0", "no")
        )
        self._primary, self._fallbacks = _composer_models()
        self._timeout: float = _load_composer_timeout()
        logger.info(
            "Composer: initialised use_llm=%s primary=%s fallbacks=%s timeout=%ss",
            self._use_llm, self._primary, self._fallbacks, self._timeout,
        )

    def compose(
        self,
        user_text: str,
        turn: TurnWrapper,
        session_id: str,
        session_name: str,
        trace_id: str = "",
    ) -> QueryResponse:
        """Narrate a TurnWrapper into a student-facing QueryResponse."""
        start = time.monotonic()
        safe_sid = _safe_session_id(session_id)
        logger.info(
            "Composer.compose start trace_id=%s session=%s turn_status=%s results=%d",
            trace_id, safe_sid, turn.turn_status, turn.result_count,
        )

        # Ordered by sq_index so multi-SQ answers follow the original query order
        sorted_results = sorted(turn.results, key=lambda r: r.sq_index)

        # Deterministic narration packet (grounding layer)
        packets = [_extract_packet(r) for r in sorted_results]

        # Merged citations from all results
        citations = _collect_citations(sorted_results)

        # QueryResponse.status mapping
        qr_status = _map_turn_status(turn)

        if _TRACE:
            student_record_focuses = [p.get("record_focus") for p in packets if p.get("intent") == "get_student_record"]
            student_record_response_styles = [p.get("response_style") for p in packets if p.get("intent") == "get_student_record"]
            has_course_details = any(
                p.get("course_code") or p.get("courses") or p.get("skills")
                for p in packets
            )
            logger.info(
                "Composer.packet_trace trace_id=%s session=%s\n"
                "  packet_count: %d\n"
                "  intents: %s\n"
                "  statuses: %s\n"
                "  student_record_focuses: %s\n"
                "  student_record_response_styles: %s\n"
                "  has_course_details: %s",
                trace_id, safe_sid,
                len(packets),
                [p.get("intent") for p in packets],
                [p.get("status") for p in packets],
                student_record_focuses, student_record_response_styles, has_course_details,
            )

        # LLM NLG (primary) → deterministic fallback
        answer_text, gen_meta = self._generate(user_text, packets, qr_status, trace_id=trace_id)

        if _TRACE:
            logger.info(
                "Composer.generate_trace trace_id=%s llm_used=%s model=%s "
                "fallback=%s deterministic_used=%s",
                trace_id,
                gen_meta["llm_used"],
                gen_meta["model_used"],
                gen_meta["fallback_reason"],
                not gen_meta["llm_used"],
            )

        logger.info(
            "Composer.compose result trace_id=%s session=%s turn_status=%s "
            "qr_status=%s llm_used=%s winning_model=%s fallback_reason=%s "
            "answer_len=%d citations=%d duration_ms=%d packet_summary=%s",
            trace_id, safe_sid, turn.turn_status,
            qr_status,
            gen_meta["llm_used"],
            gen_meta["model_used"],
            gen_meta["fallback_reason"],
            len(answer_text),
            len(citations),
            _duration_ms(start),
            _summarize_packets(packets),
        )

        return QueryResponse(
            session_id=session_id,
            session_name=session_name,
            answer_text=answer_text,
            citations=citations,
            status=qr_status,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _generate(
        self, user_text: str, packets: list[dict], qr_status: str = "ok",
        trace_id: str = "",
    ) -> tuple[str, dict]:
        """Returns (answer_text, gen_meta). gen_meta: llm_used, model_used, fallback_reason."""
        gen_meta: dict = {"llm_used": False, "model_used": None, "fallback_reason": None}
        if self._use_llm:
            user_msg = (
                f"Student query: {user_text}\n\n"
                f"Narration packet:\n"
                f"{json.dumps(packets, indent=2, default=str)}"
            )
            _sys_chars = len(_SYSTEM_PROMPT)
            _user_chars = len(user_msg)
            _narr_chars = len(json.dumps(packets, default=str))
            _est_tokens = (_sys_chars + _user_chars + 3) // 4
            logger.info(
                "Composer.prompt_size trace_id=%s result_count=%d "
                "narration_packet_chars=%d system_prompt_chars=%d "
                "user_message_chars=%d estimated_input_tokens=%d "
                "exact_token_usage=not_available",
                trace_id, len(packets), _narr_chars, _sys_chars, _user_chars, _est_tokens,
            )
            answer, model_used, failure_reason = _try_llm_chain(
                self._llm, user_msg, self._primary, self._fallbacks, self._timeout,
                trace_id=trace_id,
            )
            if answer:
                has_real_citations = any(p.get("citations") for p in packets)
                if not has_real_citations:
                    answer = _strip_fabricated_sources(answer)
                if qr_status != "clarification_needed" and _is_off_script(answer):
                    logger.warning(
                        "Composer: LLM went off-script (asked student for info) — "
                        "falling back to deterministic narration."
                    )
                    gen_meta["model_used"] = model_used
                    gen_meta["fallback_reason"] = "off_script"
                    return _deterministic_answer(packets), gen_meta
                gen_meta["llm_used"] = True
                gen_meta["model_used"] = model_used
                return answer, gen_meta
            gen_meta["fallback_reason"] = failure_reason
        else:
            gen_meta["fallback_reason"] = "llm_disabled"
        return _deterministic_answer(packets), gen_meta
