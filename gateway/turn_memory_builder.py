"""
TurnMemoryBuilder — builds one compact TurnMemory per completed turn.

Called after Orchestrator.execute_turn() returns a TurnWrapper.
Extracts only what is safe to inject into QU for follow-up resolution:
  - what the student asked (brief, capped)
  - what entities were involved (resolved IDs only)
  - what was exposed in the answer (courses, skills, roles, tracks, policy text)
  - ordered display items for "first one" / "second one" resolution
  - ambiguity metadata

Never stores: raw Composer answer, full StudentContext, student name,
student ID, full transcript, or hardcoded symbolic policy labels.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from gateway.models.schemas import (
    AnswerMemory, AmbiguityGroup, AmbiguityMeta, Citation,
    DisplayItem, PerSQResult, PolicyText, QueryMemory,
    StructuredQuery, TurnMemory, TurnWrapper,
)

logger = logging.getLogger(__name__)

# ── Caps ──────────────────────────────────────────────────────────────────────

_CAP_COURSES = 8
_CAP_SKILLS = 10
_CAP_ROLES = 8
_CAP_TRACKS = 5
_CAP_PLANNING = 8
_CAP_DISPLAY = 12
_CAP_POLICY_BRIEF = 500
_CAP_USER_TEXT = 160
_CAP_RECORD_SUMMARY = 300

# ── Domain detection ──────────────────────────────────────────────────────────

_POLICY_INTENTS = frozenset({"policy_query"})
_RECORD_INTENTS = frozenset({"get_student_record"})
_COURSE_INTENTS = frozenset({
    "get_course_info", "get_course_prerequisites",
    "get_skills_taught", "search_courses_by_skill",
})
_CAREER_INTENTS = frozenset({
    "get_role_profile", "get_roles_by_track", "compute_skill_gap",
    "compute_alignment_score", "recommend_courses_to_close_gap",
    "find_best_matching_roles", "estimate_alignment_improvement",
    "get_focus_courses_for_target",
})
_TRACK_INTENTS = frozenset({
    "get_track_overview", "compare_tracks",
    "recommend_track_for_role", "recommend_track_for_skill",
})
_PLANNING_INTENTS = frozenset({
    "plan_semester", "generate_graduation_roadmap", "run_graduation_audit",
    "check_course_eligibility", "simulate_gpa_forward", "solve_target_gpa",
})
_CONTROL_INTENTS = frozenset({"clarification_needed", "out_of_scope"})


def _primary_domain(intents: list[str]) -> str:
    domains: set[str] = set()
    for intent in intents:
        if intent in _POLICY_INTENTS:
            domains.add("policy")
        elif intent in _RECORD_INTENTS:
            domains.add("student_record")
        elif intent in _COURSE_INTENTS:
            domains.add("course")
        elif intent in _CAREER_INTENTS:
            domains.add("career")
        elif intent in _TRACK_INTENTS:
            domains.add("track")
        elif intent in _PLANNING_INTENTS:
            domains.add("planning")
        elif intent in _CONTROL_INTENTS:
            domains.add("control")
    if len(domains) == 1:
        return domains.pop()
    if len(domains) > 1:
        return "mixed"
    return "mixed"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cap(items: list, n: int) -> list:
    return items[:n]


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _str_list(val: Any, cap: int) -> list[str]:
    """Extract a list of strings from a value, respecting cap."""
    if isinstance(val, list):
        return _cap([str(x) for x in val if x], cap)
    return []


def _extract_id_from_dict(d: Any, *keys: str) -> Optional[str]:
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v and isinstance(v, str):
            return v
    return None


# ── Query memory ──────────────────────────────────────────────────────────────

def _build_query_memory(
    sqs: list[StructuredQuery],
    user_text: str,
) -> QueryMemory:
    intents = [sq.intent for sq in sqs]

    courses: list[str] = []
    skills: list[str] = []
    roles: list[str] = []
    tracks: list[str] = []
    params: dict[str, Any] = {}

    for sq in sqs:
        e = sq.entities
        if e.course_code:
            courses.append(e.course_code)
        if e.skill_id:
            skills.append(e.skill_id)
        if e.role_id:
            roles.append(e.role_id)
        if e.track_id:
            tracks.append(e.track_id)
        if sq.secondary_entities and sq.secondary_entities.track_id:
            tracks.append(sq.secondary_entities.track_id)
        # Important params only: target_gpa, record_focus, depth, skill_candidates
        for key in ("target_gpa", "record_focus", "depth", "skill_candidates",
                    "planned_courses", "expected_grades"):
            if key in sq.params and key not in params:
                params[key] = sq.params[key]

    student_referential = any(sq.student_referential_fallback for sq in sqs)

    return QueryMemory(
        user_text_brief=user_text[:_CAP_USER_TEXT],
        intents=intents,
        entities={
            "courses": _dedupe(courses),
            "skills": _dedupe(skills),
            "roles": _dedupe(roles),
            "tracks": _dedupe(tracks),
        },
        params=params,
        student_referential=student_referential,
    )


# ── Answer memory per intent ──────────────────────────────────────────────────

def _extract_policy(result: PerSQResult) -> tuple[Optional[PolicyText], list[DisplayItem]]:
    """Extract compact natural-language policy memory — no symbolic labels."""
    data = result.data or {}
    answer_raw = data.get("answer") or ""
    if not answer_raw:
        return None, []
    brief = str(answer_raw)[:_CAP_POLICY_BRIEF]

    citations: list[Citation] = []
    raw_cits = result.citations or []
    for c in raw_cits[:5]:
        if isinstance(c, dict):
            src = c.get("source") or c.get("title") or ""
            page = c.get("page")
            if src:
                citations.append(Citation(
                    source=str(src),
                    page=int(page) if isinstance(page, (int, float)) else None,
                ))

    pt = PolicyText(answer_brief=brief, citations=citations)
    di = DisplayItem(
        type="policy",
        name=brief[:60] + ("…" if len(brief) > 60 else ""),
        source_intent=result.intent,
        source_field="answer",
        rank=0,
    )
    return pt, [di]


def _extract_student_record(result: PerSQResult) -> tuple[Optional[str], list[DisplayItem]]:
    """Extract a short, safe record summary — no name, ID, grades, or full transcript.

    Focus-aware: only adds display items relevant to the declared record_focus.
    Scalar focuses and control focuses produce no course display items.
    """
    data = result.data or {}
    focus = data.get("record_focus", "")
    parts: list[str] = []
    if focus:
        parts.append(f"focus={focus}")
    # Safe aggregate fields only
    if data.get("academic_standing"):
        parts.append(f"standing={data['academic_standing']}")
    if data.get("cgpa") is not None:
        parts.append(f"cgpa={data['cgpa']}")
    if data.get("total_credit_hours_earned") is not None:
        parts.append(f"credits={data['total_credit_hours_earned']}")

    summary = (", ".join(parts))[:_CAP_RECORD_SUMMARY] if parts else None

    # ── Focus-aware display item extraction ───────────────────────────────────
    courses_disp: list[DisplayItem] = []

    # Scalar / control focuses: no course display items
    _SCALAR_FOCUSES = frozenset({
        "cgpa", "academic_level", "academic_standing", "academic_warnings",
        "probation_status", "completed_credits", "last_semester_gpa",
        "study_status", "track", "current_semester",
        "assumption_acknowledgement", "reset_assumptions",
    })
    if focus in _SCALAR_FOCUSES:
        return summary, []

    def _items_from_details(details: list, field_name: str) -> list[DisplayItem]:
        """Build DisplayItems preferring enriched details (with names) over raw codes."""
        items: list[DisplayItem] = []
        seen: set[str] = set()
        for d in details[:4]:
            if isinstance(d, dict):
                code = d.get("course_code", "")
                name = d.get("course_name") or None
                if code and code not in seen:
                    seen.add(code)
                    items.append(DisplayItem(
                        type="course",
                        code=code,
                        name=name,
                        source_intent=result.intent,
                        source_field=field_name,
                        rank=len(courses_disp) + len(items),
                    ))
        return items

    def _items_from_codes(codes: list, field_name: str) -> list[DisplayItem]:
        """Build DisplayItems from raw code list (fallback when no enriched details)."""
        items: list[DisplayItem] = []
        for code in _str_list(codes, 4):
            items.append(DisplayItem(
                type="course",
                code=code,
                source_intent=result.intent,
                source_field=field_name,
                rank=len(courses_disp) + len(items),
            ))
        return items

    def _course_items_for(detail_field: str, raw_field: str) -> list[DisplayItem]:
        """Return display items using enriched details if available, else raw codes."""
        details = data.get(detail_field) or []
        if details:
            return _items_from_details(details, detail_field)
        raw = data.get(raw_field) or []
        if raw:
            return _items_from_codes(raw, raw_field)
        return []

    if focus == "in_progress_courses":
        courses_disp = _course_items_for("in_progress_course_details", "in_progress_courses")
    elif focus == "completed_courses":
        courses_disp = _course_items_for("completed_course_details", "completed_courses")
    elif focus == "failed_courses":
        courses_disp = _course_items_for("failed_course_details", "failed_courses")
    elif focus == "failed_course_history":
        # Use failed_history_details or failed_history_codes
        details = data.get("failed_history_details") or []
        if details:
            courses_disp = _items_from_details(details, "failed_history_details")
        else:
            codes = data.get("failed_history_codes") or []
            courses_disp = _items_from_codes(codes, "failed_history_codes")
    elif focus == "course_status_check":
        # Use checked_course_codes with names from details when available
        checked = data.get("checked_course_codes") or []
        all_details: list[dict] = []
        for df in ("completed_course_details", "in_progress_course_details", "failed_course_details"):
            for d in data.get(df) or []:
                if isinstance(d, dict) and d.get("course_code"):
                    all_details.append(d)
        details_by_code: dict[str, dict] = {d["course_code"]: d for d in all_details}
        for code in _str_list(checked, 4):
            d = details_by_code.get(code)
            name = d.get("course_name") if d else None
            courses_disp.append(DisplayItem(
                type="course",
                code=code,
                name=name,
                source_intent=result.intent,
                source_field="checked_course_codes",
                rank=len(courses_disp),
            ))
    else:
        # full_record, progress_summary, unknown → all three, capped at 4 each
        for detail_field, raw_field in (
            ("in_progress_course_details", "in_progress_courses"),
            ("completed_course_details", "completed_courses"),
            ("failed_course_details", "failed_courses"),
        ):
            courses_disp.extend(_course_items_for(detail_field, raw_field))

    return summary, courses_disp[:_CAP_DISPLAY]


def _extract_course_intents(
    result: PerSQResult,
) -> tuple[list[str], list[str], list[DisplayItem]]:
    """Returns (courses, skills, display_items) for D2 course intents."""
    intent = result.intent
    data = result.data or {}
    courses: list[str] = []
    skills: list[str] = []
    display: list[DisplayItem] = []

    if intent == "get_course_info":
        code = data.get("course_code") or ""
        name = data.get("name") or ""
        if code:
            courses.append(code)
            display.append(DisplayItem(
                type="course", code=code, name=name or None,
                source_intent=intent, source_field="course_code", rank=0,
            ))

    elif intent == "get_course_prerequisites":
        code = data.get("course_code") or ""
        if code:
            courses.append(code)
            display.append(DisplayItem(
                type="course", code=code,
                source_intent=intent, source_field="course_code", rank=0,
            ))
        prereqs = _str_list(data.get("direct_prerequisites") or data.get("full_prerequisite_tree"), _CAP_COURSES)
        for i, pre in enumerate(prereqs):
            if isinstance(pre, dict):
                pre = pre.get("course_code") or pre.get("code") or ""
            if pre:
                courses.append(str(pre))
                display.append(DisplayItem(
                    type="course", code=str(pre),
                    source_intent=intent, source_field="prerequisites", rank=i + 1,
                ))

    elif intent == "get_skills_taught":
        code = data.get("course_code") or ""
        if code:
            courses.append(code)
        raw_skills = data.get("skills_taught") or data.get("skills") or []
        for i, sk in enumerate(raw_skills[:_CAP_SKILLS]):
            sk_id = _extract_id_from_dict(sk, "skill_id", "id") if isinstance(sk, dict) else str(sk)
            sk_name = (_extract_id_from_dict(sk, "name", "skill_name") if isinstance(sk, dict) else None)
            if sk_id:
                skills.append(sk_id)
                display.append(DisplayItem(
                    type="skill", code=sk_id, name=sk_name,
                    source_intent=intent, source_field="skills_taught", rank=i,
                ))

    elif intent == "search_courses_by_skill":
        sk_id = data.get("skill_id") or ""
        if sk_id:
            skills.append(sk_id)
        raw_courses = data.get("courses") or data.get("results") or []
        for i, c in enumerate(raw_courses[:_CAP_COURSES]):
            code = _extract_id_from_dict(c, "course_code", "code") if isinstance(c, dict) else str(c)
            name = (_extract_id_from_dict(c, "course_name", "name") if isinstance(c, dict) else None)
            if code:
                courses.append(code)
                display.append(DisplayItem(
                    type="course", code=code, name=name,
                    source_intent=intent, source_field="courses", rank=i,
                ))

    return (
        _dedupe(courses)[:_CAP_COURSES],
        _dedupe(skills)[:_CAP_SKILLS],
        display[:_CAP_DISPLAY],
    )


def _extract_career_intents(
    result: PerSQResult,
) -> tuple[list[str], list[str], list[str], list[DisplayItem]]:
    """Returns (courses, skills, roles, display_items) for D3 career intents."""
    intent = result.intent
    data = result.data or {}
    courses: list[str] = []
    skills: list[str] = []
    roles: list[str] = []
    display: list[DisplayItem] = []

    role_id = data.get("role_id") or ""
    if role_id:
        roles.append(role_id)

    if intent == "get_role_profile":
        if role_id:
            display.append(DisplayItem(
                type="role", code=role_id,
                name=data.get("name") or data.get("role_name"),
                source_intent=intent, source_field="role_id", rank=0,
            ))
        for i, sk in enumerate((data.get("required_skills") or [])[:_CAP_SKILLS]):
            sk_id = _extract_id_from_dict(sk, "skill_id", "id") if isinstance(sk, dict) else str(sk)
            sk_name = _extract_id_from_dict(sk, "name") if isinstance(sk, dict) else None
            if sk_id:
                skills.append(sk_id)
                display.append(DisplayItem(
                    type="skill", code=sk_id, name=sk_name,
                    source_intent=intent, source_field="required_skills", rank=i,
                ))

    elif intent == "get_roles_by_track":
        for i, r in enumerate((data.get("roles") or data.get("results") or [])[:_CAP_ROLES]):
            r_id = _extract_id_from_dict(r, "role_id", "id") if isinstance(r, dict) else str(r)
            r_name = _extract_id_from_dict(r, "name", "role_name") if isinstance(r, dict) else None
            if r_id:
                roles.append(r_id)
                display.append(DisplayItem(
                    type="role", code=r_id, name=r_name,
                    source_intent=intent, source_field="roles", rank=i,
                ))

    elif intent == "compute_skill_gap":
        if role_id:
            display.append(DisplayItem(
                type="role", code=role_id,
                name=data.get("role_name"),
                source_intent=intent, source_field="role_id", rank=0,
            ))
        for i, sk in enumerate((data.get("missing_skills") or [])[:_CAP_SKILLS]):
            sk_id = _extract_id_from_dict(sk, "skill_id", "id") if isinstance(sk, dict) else str(sk)
            sk_name = _extract_id_from_dict(sk, "name") if isinstance(sk, dict) else None
            if sk_id:
                skills.append(sk_id)
                display.append(DisplayItem(
                    type="skill", code=sk_id, name=sk_name,
                    source_intent=intent, source_field="missing_skills", rank=i + 1,
                ))

    elif intent in ("recommend_courses_to_close_gap", "get_focus_courses_for_target"):
        if role_id:
            display.append(DisplayItem(
                type="role", code=role_id,
                name=data.get("role_name"),
                source_intent=intent, source_field="role_id", rank=0,
            ))
        course_field = "recommended_courses" if intent == "recommend_courses_to_close_gap" else "focus_courses"
        for i, c in enumerate((data.get(course_field) or [])[:_CAP_COURSES]):
            code = _extract_id_from_dict(c, "course_code", "code") if isinstance(c, dict) else str(c)
            name = _extract_id_from_dict(c, "course_name", "name") if isinstance(c, dict) else None
            if code:
                courses.append(code)
                display.append(DisplayItem(
                    type="course", code=code, name=name,
                    source_intent=intent, source_field=course_field, rank=i + 1,
                ))

    elif intent == "find_best_matching_roles":
        for i, r in enumerate((data.get("ranked_roles") or [])[:_CAP_ROLES]):
            r_id = _extract_id_from_dict(r, "role_id", "id") if isinstance(r, dict) else str(r)
            r_name = _extract_id_from_dict(r, "name", "role_name") if isinstance(r, dict) else None
            if r_id:
                roles.append(r_id)
                display.append(DisplayItem(
                    type="role", code=r_id, name=r_name,
                    source_intent=intent, source_field="ranked_roles", rank=i,
                ))

    elif intent == "estimate_alignment_improvement":
        if role_id:
            display.append(DisplayItem(
                type="role", code=role_id,
                name=data.get("role_name"),
                source_intent=intent, source_field="role_id", rank=0,
            ))

    elif intent == "compute_alignment_score":
        if role_id:
            display.append(DisplayItem(
                type="role", code=role_id,
                name=data.get("role_name"),
                source_intent=intent, source_field="role_id", rank=0,
            ))

    return (
        _dedupe(courses)[:_CAP_COURSES],
        _dedupe(skills)[:_CAP_SKILLS],
        _dedupe(roles)[:_CAP_ROLES],
        display[:_CAP_DISPLAY],
    )


def _extract_track_intents(
    result: PerSQResult,
) -> tuple[list[str], list[str], list[DisplayItem]]:
    """Returns (roles, tracks, display_items) for D4 track intents."""
    intent = result.intent
    data = result.data or {}
    roles: list[str] = []
    tracks: list[str] = []
    display: list[DisplayItem] = []

    if intent == "get_track_overview":
        t_id = data.get("track_id") or ""
        if t_id:
            tracks.append(t_id)
            display.append(DisplayItem(
                type="track", code=t_id,
                name=data.get("name"),
                source_intent=intent, source_field="track_id", rank=0,
            ))
        for i, c in enumerate((data.get("courses") or [])[:_CAP_COURSES]):
            code = _extract_id_from_dict(c, "course_code", "code") if isinstance(c, dict) else str(c)
            name = _extract_id_from_dict(c, "course_name", "name") if isinstance(c, dict) else None
            if code:
                display.append(DisplayItem(
                    type="course", code=code, name=name,
                    source_intent=intent, source_field="courses", rank=i + 1,
                ))

    elif intent == "compare_tracks":
        for field in ("track_id_1", "track_id_2", "track_id"):
            t_id = data.get(field) or ""
            if t_id and t_id not in tracks:
                tracks.append(t_id)
                display.append(DisplayItem(
                    type="track", code=t_id,
                    source_intent=intent, source_field=field, rank=len(tracks) - 1,
                ))

    elif intent in ("recommend_track_for_role", "recommend_track_for_skill"):
        ranking = data.get("ranking") or []
        if not ranking and data.get("recommended_track"):
            ranking = [{"track_id": data["recommended_track"]}]
        for i, r in enumerate(ranking[:_CAP_TRACKS]):
            t_id = _extract_id_from_dict(r, "track_id", "id") if isinstance(r, dict) else str(r)
            t_name = _extract_id_from_dict(r, "name", "track_name") if isinstance(r, dict) else None
            if t_id:
                tracks.append(t_id)
                display.append(DisplayItem(
                    type="track", code=t_id, name=t_name,
                    source_intent=intent, source_field="ranking", rank=i,
                ))

    return (
        _dedupe(roles)[:_CAP_ROLES],
        _dedupe(tracks)[:_CAP_TRACKS],
        display[:_CAP_DISPLAY],
    )


def _extract_planning_intents(
    result: PerSQResult,
) -> tuple[list[str], Optional[str], list[DisplayItem]]:
    """Returns (planning_items, gpa_scenario, display_items) for D1 intents."""
    intent = result.intent
    data = result.data or {}
    planning: list[str] = []
    gpa_scenario: Optional[str] = None
    display: list[DisplayItem] = []

    if intent == "plan_semester":
        courses = (
            data.get("recommended_courses")
            or data.get("planned_courses")
            or []
        )
        # plans[0].courses format from ALE
        if not courses and data.get("plans"):
            plans = data.get("plans") or []
            if plans:
                courses = plans[0].get("courses") or []
        for i, c in enumerate(courses[:_CAP_PLANNING]):
            code = _extract_id_from_dict(c, "course_code", "code") if isinstance(c, dict) else str(c)
            name = _extract_id_from_dict(c, "course_name", "name") if isinstance(c, dict) else None
            if code:
                planning.append(code)
                display.append(DisplayItem(
                    type="planning_item", code=code, name=name,
                    source_intent=intent, source_field="planned_courses", rank=i,
                ))

    elif intent == "generate_graduation_roadmap":
        # Store only the first 2 semesters' near-term courses
        roadmap = data.get("semester_plans") or data.get("semesters") or data.get("roadmap") or []
        count = 0
        for sem_plan in roadmap[:2]:
            courses = []
            if isinstance(sem_plan, dict):
                courses = sem_plan.get("courses") or sem_plan.get("planned_courses") or []
            for c in courses[:4]:
                code = _extract_id_from_dict(c, "course_code", "code") if isinstance(c, dict) else str(c)
                name = _extract_id_from_dict(c, "course_name", "name") if isinstance(c, dict) else None
                if code:
                    planning.append(code)
                    display.append(DisplayItem(
                        type="planning_item", code=code, name=name,
                        source_intent=intent, source_field="roadmap", rank=count,
                    ))
                    count += 1

    elif intent == "check_course_eligibility":
        code = data.get("target_course_code") or data.get("course_code") or ""
        name = data.get("target_course_name") or data.get("course_name") or None
        if code:
            planning.append(code)
            display.append(DisplayItem(
                type="course", code=code, name=name,
                source_intent=intent, source_field="target_course_code", rank=0,
            ))

    elif intent in ("simulate_gpa_forward", "solve_target_gpa"):
        target = data.get("target_cgpa") or data.get("projected_cgpa")
        if target is not None:
            gpa_scenario = f"target_gpa={target}"
        elif data.get("scenario_summary"):
            gpa_scenario = str(data["scenario_summary"])[:100]

    return _dedupe(planning)[:_CAP_PLANNING], gpa_scenario, display[:_CAP_DISPLAY]


# ── Ambiguity detection ───────────────────────────────────────────────────────

def _build_ambiguity(am: AnswerMemory) -> AmbiguityMeta:
    groups: list[AmbiguityGroup] = []
    if am.courses:
        groups.append(AmbiguityGroup(label="courses", item_type="course", items=am.courses[:5]))
    if am.skills:
        groups.append(AmbiguityGroup(label="skills", item_type="skill", items=am.skills[:5]))
    if am.roles:
        groups.append(AmbiguityGroup(label="roles", item_type="role", items=am.roles[:5]))
    if am.tracks:
        groups.append(AmbiguityGroup(label="tracks", item_type="track", items=am.tracks[:3]))
    if am.planning_items:
        groups.append(AmbiguityGroup(label="planning_items", item_type="planning_item", items=am.planning_items[:5]))

    # Ambiguous only when 2+ different *types* of items are present
    distinct_types = {g.item_type for g in groups}
    has_multiple = len(distinct_types) > 1
    return AmbiguityMeta(has_multiple_reference_groups=has_multiple, groups=groups if has_multiple else [])


# ── Main builder ──────────────────────────────────────────────────────────────

def build_turn_memory(
    sqs: list[StructuredQuery],
    tw: TurnWrapper,
    user_text: str,
) -> TurnMemory:
    """
    Build a TurnMemory from one completed turn.
    Safe to call even when TurnWrapper has errors — degrades gracefully.
    """
    try:
        return _build_turn_memory_impl(sqs, tw, user_text)
    except Exception:
        logger.exception("TurnMemoryBuilder: unexpected error — returning empty TurnMemory")
        intents = [sq.intent for sq in sqs]
        return TurnMemory(
            source_intents=intents,
            primary_domain="mixed",
            query_memory=QueryMemory(
                user_text_brief=user_text[:_CAP_USER_TEXT],
                intents=intents,
            ),
        )


def _build_turn_memory_impl(
    sqs: list[StructuredQuery],
    tw: TurnWrapper,
    user_text: str,
) -> TurnMemory:
    intents = [sq.intent for sq in sqs]
    query_mem = _build_query_memory(sqs, user_text)

    # Collect answer memory across all successful/informational results
    policy_text: Optional[PolicyText] = None
    record_summary: Optional[str] = None
    all_courses: list[str] = []
    all_skills: list[str] = []
    all_roles: list[str] = []
    all_tracks: list[str] = []
    all_planning: list[str] = []
    gpa_scenario: Optional[str] = None
    all_display: list[DisplayItem] = []

    for result in tw.results:
        if result.status in ("error", "out_of_scope"):
            continue

        intent = result.intent

        if intent == "policy_query":
            pt, di = _extract_policy(result)
            if pt and policy_text is None:
                policy_text = pt
            all_display.extend(di)

        elif intent == "get_student_record":
            rec_sum, di = _extract_student_record(result)
            if rec_sum and record_summary is None:
                record_summary = rec_sum
            all_display.extend(di)

        elif intent in _COURSE_INTENTS:
            c, s, di = _extract_course_intents(result)
            all_courses.extend(c)
            all_skills.extend(s)
            all_display.extend(di)

        elif intent in _CAREER_INTENTS:
            c, s, r, di = _extract_career_intents(result)
            all_courses.extend(c)
            all_skills.extend(s)
            all_roles.extend(r)
            all_display.extend(di)

        elif intent in _TRACK_INTENTS:
            r, t, di = _extract_track_intents(result)
            all_roles.extend(r)
            all_tracks.extend(t)
            all_display.extend(di)

        elif intent in _PLANNING_INTENTS:
            p, gpa, di = _extract_planning_intents(result)
            all_planning.extend(p)
            if gpa and gpa_scenario is None:
                gpa_scenario = gpa
            all_display.extend(di)

    # Renumber display items globally in order
    seen_codes: set[str] = set()
    deduped_display: list[DisplayItem] = []
    for item in all_display:
        key = f"{item.type}:{item.code or item.name or ''}"
        if key not in seen_codes:
            seen_codes.add(key)
            deduped_display.append(item)
    for i, item in enumerate(deduped_display[:_CAP_DISPLAY]):
        deduped_display[i] = item.model_copy(update={"rank": i})

    answer_mem = AnswerMemory(
        policy_text=policy_text,
        student_record_summary=record_summary,
        courses=_dedupe(all_courses)[:_CAP_COURSES],
        skills=_dedupe(all_skills)[:_CAP_SKILLS],
        roles=_dedupe(all_roles)[:_CAP_ROLES],
        tracks=_dedupe(all_tracks)[:_CAP_TRACKS],
        planning_items=_dedupe(all_planning)[:_CAP_PLANNING],
        gpa_scenario=gpa_scenario,
        ordered_display_items=deduped_display[:_CAP_DISPLAY],
    )

    ambiguity = _build_ambiguity(answer_mem)

    return TurnMemory(
        source_intents=intents,
        primary_domain=_primary_domain(intents),
        query_memory=query_mem,
        answer_memory=answer_mem,
        ambiguity=ambiguity,
    )
