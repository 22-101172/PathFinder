"""
queries.py
All Cypher query functions for the PathFinder KG-Engine.

Each function returns a plain dict/list ready for the KGEngine layer.
No exceptions are raised for business-logic failures — structured error
dicts with an "error" key are returned instead.
"""

import json
import os
import re
import logging

logger = logging.getLogger(__name__)

# ── Validation helpers ───────────────────────────────────────────────────────

# Valid course codes: letters / digits / hyphens (e.g. C-AI321, HUM110)
_COURSE_CODE_RE = re.compile(r'^[A-Za-z][A-Za-z0-9\-]*$')


def _is_valid_course_code(code: str) -> bool:
    return bool(code and _COURSE_CODE_RE.match(code.strip()))


def _parse_credit_threshold_constraint(constraint_type, constraint_value):
    """Extract the integer hour value from a CREDIT_THRESHOLD PrerequisiteConstraint.

    pc.type is stored as "CREDIT_THRESHOLD"; pc.value is a free-text string
    like "Passing 59 Credit Hours".  Returns the embedded integer, or None if
    the type is not CREDIT_THRESHOLD, the value is absent, or no digit is found.
    """
    if constraint_type != "CREDIT_THRESHOLD":
        return None
    if constraint_value is None:
        return None
    m = re.search(r"\d+", str(constraint_value))
    return int(m.group()) if m else None


# ═══════════════════════════════════════════════════════════════════════════════
# A1 — Course Catalogue Lookup
# ═══════════════════════════════════════════════════════════════════════════════

# ── OP1: Get Course Profile ──────────────────────────────────────────────────
def q_get_course_profile(client, course_code: str) -> dict:
    """
    Returns the full profile of a course.
    """
    #empty string or None or whitespace
    if not course_code or not str(course_code).strip():
        return {"error": "invalid_course_code", "submitted_code": course_code}

    #invalid course code format
    code = str(course_code).strip()
    if not _is_valid_course_code(code):
        return {"error": "invalid_course_code", "submitted_code": code}

    #execute query to get course profile
    rows = client.execute_query(
        """
        MATCH (c:Course {course_code: $code})
        OPTIONAL MATCH (c)-[:BELONGS_TO]->(t:Track)
        WITH c,
            collect(DISTINCT CASE
                WHEN t IS NOT NULL THEN {track_id: t.track_id, name: t.name}
            END) AS tracks
        OPTIONAL MATCH (c)-[:HAS_PREREQ_CONSTRAINT]->(pc:PrerequisiteConstraint)
        RETURN
            c.course_code AS course_code,
            c.name AS name,
            c.credits AS credits,
            c.level AS level,
            c.semester_offering AS semester_offering,
            c.description AS description,
            tracks,
            pc.type  AS constraint_type,
            pc.value AS constraint_value
        """,
        {"code": code}
    )

    #course not found
    if not rows:
        return {"error": "course_not_found", "submitted_code": code}

    #process the result
    row = rows[0]
    raw_sem = row.get("semester_offering") or ""
    sem_list = [s.strip() for s in raw_sem.split(",") if s.strip()] if raw_sem else []

    tracks = [t for t in (row.get("tracks") or []) if t]

    #return the result
    return {
        "course_code": row["course_code"],
        "name": row["name"],
        "credits": row["credits"],
        "level": row["level"],
        "semester_offering": sem_list,
        "tracks": tracks,
        "description": row.get("description"),
        "credit_threshold": _parse_credit_threshold_constraint(
            row.get("constraint_type"), row.get("constraint_value")
        ),
    }

# ── OP2: Get Prerequisites ───────────────────────────────────────────────────

def _build_prereq_tree(client, code: str, visited: set) -> list:
    """Recursively build the course-based prerequisite tree, detecting cycles."""
    if code in visited:
        raise RecursionError("circular_dependency_detected")

    visited = visited | {code}

    rows = client.execute_query(
        """
        MATCH (c:Course {course_code: $code})-[:PREREQ]->(p:Course)
        RETURN p.course_code AS prereq_code, p.name AS prereq_name
        ORDER BY p.course_code
        """,
        {"code": code}
    )

    tree = []
    for row in rows:
        node = {
            "course_code": row["prereq_code"],
            "name": row["prereq_name"],
            "requires": _build_prereq_tree(client, row["prereq_code"], visited)
        }
        tree.append(node)

    return tree


def q_get_prerequisites(client, course_code: str, depth: str = "direct") -> dict:
    """
    Returns prerequisites.
    depth = "direct" | "full"
    """
    if not course_code or not str(course_code).strip():
        return {"error": "invalid_course_code", "submitted_code": course_code}

    code = str(course_code).strip()
    if not _is_valid_course_code(code):
        return {"error": "invalid_course_code", "submitted_code": code}

    if depth not in ("direct", "full"):
        return {"error": "invalid_depth_value", "submitted_depth": depth}

    rows = client.execute_query(
        """
        MATCH (c:Course {course_code: $code})

        OPTIONAL MATCH (c)-[:PREREQ]->(p:Course)
        WITH c, collect(DISTINCT CASE
            WHEN p IS NOT NULL THEN {
                course_code: p.course_code,
                name: p.name
            }
        END) AS direct_prereqs

        OPTIONAL MATCH (c)-[:HAS_PREREQ_CONSTRAINT]->(pc:PrerequisiteConstraint)
        RETURN
            c.course_code AS course_code,
            c.name AS course_name,
            [x IN direct_prereqs WHERE x IS NOT NULL] AS direct_prerequisites,
            collect(DISTINCT CASE
                WHEN pc IS NOT NULL THEN {
                    type: pc.type,
                    value: pc.value
                }
            END) AS non_course_prerequisites
        """,
        {"code": code}
    )

    if not rows:
        return {"error": "course_not_found", "submitted_code": code}

    row = rows[0]

    direct_prereqs = row.get("direct_prerequisites") or []

    raw_non_course = [x for x in (row.get("non_course_prerequisites") or []) if x is not None]
    non_course = []
    for x in raw_non_course:
        parsed_value = _parse_credit_threshold_constraint(x.get("type"), x.get("value"))
        non_course.append({
            "type": x["type"],
            "value": parsed_value if x.get("type") == "CREDIT_THRESHOLD" else x.get("value"),
        })

    has_prereqs = bool(direct_prereqs or non_course)

    result = {
        "course_code": code,
        "name": row["course_name"],
        "direct_prerequisites": direct_prereqs,
        "non_course_prerequisites": non_course,
        "has_prerequisites": has_prereqs,
    }

    if depth == "full":
        try:
            result["full_prerequisite_tree"] = _build_prereq_tree(client, code, set())
        except RecursionError:
            return {"error": "circular_dependency_detected", "course_code": code}
    else:
        result["full_prerequisite_tree"] = []

    return result


# ── OP3: Get Skills Taught ───────────────────────────────────────────────────

def q_get_skills_taught(client, course_code: str) -> dict:
    """Returns skills taught by a course."""
    if not course_code or not str(course_code).strip():
        return {"error": "invalid_course_code", "submitted_code": course_code}

    code = str(course_code).strip()
    if not _is_valid_course_code(code):
        return {"error": "invalid_course_code", "submitted_code": code}

    rows = client.execute_query(
        """
        MATCH (c:Course {course_code: $code})
        OPTIONAL MATCH (c)-[:TEACHES]->(s:Skill)
        RETURN
            c.course_code AS course_code,
            c.name AS course_name,
            collect(DISTINCT CASE
                WHEN s IS NOT NULL THEN {
                    skill_id: s.skill_id,
                    name: s.name,
                    category: s.category
                }
            END) AS skills_taught
        """,
        {"code": code}
    )

    if not rows:
        return {"error": "course_not_found", "submitted_code": code}

    row = rows[0]
    skills = [s for s in (row.get("skills_taught") or []) if s]
    skills.sort(key=lambda x: x["name"])

    return {
        "course_code": code,
        "name": row["course_name"],
        "skills_taught": skills,
        "total_skills": len(skills),
    }


# ── OP4: Search Courses by Skill ─────────────────────────────────────────────

def q_search_courses_by_skill(client, skills: list) -> dict:
    """Given a list of skill names, return all courses that teach any of them."""
    if not skills:
        return {"error": "no_skills_provided"}

    skill_names = [s.strip() for s in skills if s and s.strip()]
    if not skill_names:
        return {"error": "no_skills_provided"}

    cleaned_skill_names = []
    seen_inputs = set()
    for s in skill_names:
        s_lower = s.lower()
        if s_lower not in seen_inputs:
            seen_inputs.add(s_lower)
            cleaned_skill_names.append(s)

    rows = client.execute_query(
        """
        UNWIND $skill_names AS sname
        MATCH (s:Skill)
        WHERE toLower(s.name) = toLower(sname)
        WITH s, sname
        MATCH (c:Course)-[:TEACHES]->(s)
        OPTIONAL MATCH (c)-[:BELONGS_TO]->(t:Track)
        RETURN
            sname AS queried_skill,
            s.name AS matched_skill,
            c.course_code AS course_code,
            c.name AS name,
            collect(DISTINCT CASE
                WHEN t IS NOT NULL THEN {
                    track_id: t.track_id,
                    name: t.name
                }
            END) AS tracks
        ORDER BY c.course_code
        """,
        {"skill_names": cleaned_skill_names}
    )

    recognized_skills = set()
    seen_courses = {}
    for r in rows:
        recognized_skills.add(r["queried_skill"].lower())
        cc = r["course_code"]
        if cc not in seen_courses:
            seen_courses[cc] = {
                "course_code": cc,
                "name": r["name"],
                "tracks": [t for t in (r.get("tracks") or []) if t],
                "matched_skills": [r["matched_skill"]] if r.get("matched_skill") else [],
            }
        else:
            for t in (r.get("tracks") or []):
                if t and t not in seen_courses[cc]["tracks"]:
                    seen_courses[cc]["tracks"].append(t)
            if r.get("matched_skill") and r["matched_skill"] not in seen_courses[cc]["matched_skills"]:
                seen_courses[cc]["matched_skills"].append(r["matched_skill"])

    results = list(seen_courses.values())
    results.sort(key=lambda x: (-len(x["matched_skills"]), x["course_code"]))

    unrecognized_skills = [
        s for s in cleaned_skill_names
        if s.lower() not in recognized_skills
    ]

    return {
        "queried_skills": cleaned_skill_names,
        "unrecognized_skills": unrecognized_skills,
        "results": results[:10],
        "total_results": len(results),
    }



# ── OP5: Get Focus Courses for Target ───────────────────────────────────────
def q_get_focus_courses_for_target(
    client,
    target_id: str,
    target_type: str,
    completed_courses: list,
) -> dict:
    """
    Given a target (track or role) and the student's completed courses, returns
    the courses they have NOT yet taken, ranked by how many skills relevant to
    that target each course teaches. Empty completed_courses is valid (freshman).
    """
    if not target_id or not str(target_id).strip():
        return {"error": "no_target_provided"}

    tid = str(target_id).strip()

    if target_type not in ("track", "role"):
        return {"error": "invalid_target_type", "accepted_values": ["track", "role"]}

    #deduplicate completed_courses; empty list is valid
    cleaned = []
    seen = set()
    for c in (completed_courses or []):
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen:
                seen.add(code)
                cleaned.append(code)

    #target existence check + skill fetch
    if target_type == "track":
        exists = client.execute_query(
            "MATCH (t:Track {track_id: $tid}) RETURN t.name AS name", {"tid": tid}
        )
        if not exists:
            return {"error": "track_not_found", "submitted_target_id": tid}
        target_name = exists[0]["name"]

        skill_rows = client.execute_query(
            """
            MATCH (t:Track {track_id: $tid})<-[:BELONGS_TO]-(c:Course)-[:TEACHES]->(s:Skill)
            RETURN DISTINCT s.skill_id AS skill_id, s.name AS skill_name
            ORDER BY s.skill_id
            """,
            {"tid": tid}
        )
    else:
        exists = client.execute_query(
            "MATCH (r:Role {role_id: $tid}) RETURN r.name AS name", {"tid": tid}
        )
        if not exists:
            return {"error": "role_not_found", "submitted_target_id": tid}
        target_name = exists[0]["name"]

        skill_rows = client.execute_query(
            """
            MATCH (r:Role {role_id: $tid})-[:REQUIRES]->(s:Skill)
            RETURN s.skill_id AS skill_id, s.name AS skill_name
            ORDER BY s.skill_id
            """,
            {"tid": tid}
        )

    if not skill_rows:
        return {
            "target_id": tid,
            "target_type": target_type,
            "target_name": target_name,
            "total_target_skills": 0,
            "focus_courses": [],
            "total_focus_courses": 0,
            "warning": "No skills mapped to this target.",
        }

    target_skill_ids = [r["skill_id"] for r in skill_rows]

    course_rows = client.execute_query(
        """
        MATCH (c:Course)-[:TEACHES]->(s:Skill)
        WHERE s.skill_id IN $target_skill_ids
          AND NOT c.course_code IN $completed
        WITH c, collect(DISTINCT {skill_id: s.skill_id, skill_name: s.name}) AS relevant_skills
        RETURN
            c.course_code AS course_code,
            c.name        AS name,
            c.credits     AS credits,
            c.level       AS level,
            relevant_skills
        ORDER BY size(relevant_skills) DESC, c.level ASC
        """,
        {"target_skill_ids": target_skill_ids, "completed": cleaned}
    )

    focus_courses = [
        {
            "course_code": r["course_code"],
            "name": r["name"],
            "credits": r["credits"],
            "level": r["level"],
            "relevant_skill_count": len(r["relevant_skills"]),
            "relevant_skills": sorted(
                r["relevant_skills"], key=lambda x: x.get("skill_id") or ""
            ),
        }
        for r in course_rows
    ]

    return {
        "target_id": tid,
        "target_type": target_type,
        "target_name": target_name,
        "total_target_skills": len(target_skill_ids),
        "focus_courses": focus_courses,
        "total_focus_courses": len(focus_courses),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# A2 — Career Role Exploration (Curriculum-aware)
# ═══════════════════════════════════════════════════════════════════════════════

def _weight_to_tier(weight: float) -> str:
    if weight >= 0.8:
        return "core"
    elif weight >= 0.6:
        return "supporting"
    else:
        return "optional"


def q_get_role_profile(client, role_id: str) -> dict:
    """Returns the profile of a role including required skills."""
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}

    rid = str(role_id).strip()

    rows = client.execute_query(
        """
        MATCH (r:Role {role_id: $rid})
        OPTIONAL MATCH (r)-[rel:REQUIRES]->(s:Skill)
        RETURN
            r.role_id AS role_id,
            r.name AS role_name,
            r.domain AS domain,
            collect(DISTINCT CASE
                WHEN s IS NOT NULL THEN {
                    skill_id: s.skill_id,
                    name: s.name,
                    category: s.category,
                    weight: rel.weight
                }
            END) AS required_skills
        """,
        {"rid": rid}
    )

    if not rows:
        return {"error": "role_not_found", "submitted_role_id": rid}

    row = rows[0]
    domain = row.get("domain")
    domains = [domain] if domain else []

    raw_skills = [s for s in (row.get("required_skills") or []) if s]
    raw_skills.sort(key=lambda x: (-(x.get("weight") if x.get("weight") is not None else -1), x.get("name") or ""))

    required_skills = [
        {
            "skill_id": sk.get("skill_id"),
            "name": sk.get("name"),
            "category": sk.get("category"),
            "tier": _weight_to_tier(sk["weight"]) if sk.get("weight") is not None else None,
            "weight": sk.get("weight"),
        }
        for sk in raw_skills
    ]

    return {
        "role_id": rid,
        "role_name": row["role_name"],
        "domains": domains,
        "required_skills": required_skills,
        "total_required_skills": len(required_skills),
    }


def q_get_roles_by_track(client, track_id: str) -> dict:
    """Returns roles reachable through the Track → Course → Skill → Role path."""
    if not track_id or not str(track_id).strip():
        return {"error": "no_track_provided"}

    tid = str(track_id).strip()

    rows = client.execute_query(
        """
        MATCH (t:Track {track_id: $tid})
        OPTIONAL MATCH (t)<-[:BELONGS_TO]-(c:Course)-[:TEACHES]->(s:Skill)<-[:REQUIRES]-(r:Role)
        RETURN
            t.track_id AS track_id,
            t.name AS track_name,
            collect(DISTINCT CASE
                WHEN r IS NOT NULL THEN {
                    role_id: r.role_id,
                    role_name: r.name,
                    domain: r.domain
                }
            END) AS roles
        """,
        {"tid": tid}
    )

    if not rows:
        return {"error": "track_not_found", "submitted_track_id": tid}

    row = rows[0]
    raw_roles = [r for r in (row.get("roles") or []) if r]

    results = [
        {
            "role_id": r.get("role_id"),
            "role_name": r.get("role_name"),
            "domains": [r["domain"]] if r.get("domain") else [],
        }
        for r in raw_roles
    ]
    results.sort(key=lambda x: x["role_name"] or "")

    return {
        "track": row["track_name"],
        "track_id": tid,
        "results": results,
        "total_results": len(results),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# A3 — Skill Gap & Alignment
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_courses(client, course_codes: list):
    if not course_codes:
        return [], []
    rows = client.execute_query(
        "UNWIND $codes AS code MATCH (c:Course {course_code: code}) RETURN c.course_code AS course_code",
        {"codes": course_codes}
    )
    found = {r["course_code"] for r in rows}
    valid = [c for c in course_codes if c in found]
    unrecognized = [c for c in course_codes if c not in found]
    return valid, unrecognized


def _get_role_skills(client, role_id: str):
    rows = client.execute_query(
        """
        MATCH (r:Role {role_id: $rid})-[rel:REQUIRES]->(s:Skill)
        RETURN s.skill_id AS skill_id, s.name AS name,
               s.category AS category, rel.weight AS weight
        ORDER BY rel.weight DESC, s.name
        """,
        {"rid": role_id}
    )
    return rows


def _get_skills_from_courses(client, course_codes: list) -> set:
    if not course_codes:
        return set()
    rows = client.execute_query(
        """
        UNWIND $codes AS code
        MATCH (c:Course {course_code: code})-[:TEACHES]->(s:Skill)
        RETURN DISTINCT s.skill_id AS skill_id
        """,
        {"codes": course_codes}
    )
    return {r["skill_id"] for r in rows}


def _get_skills_with_courses(client, course_codes: list) -> dict:
    if not course_codes:
        return {}
    rows = client.execute_query(
        """
        UNWIND $codes AS code
        MATCH (c:Course {course_code: code})-[:TEACHES]->(s:Skill)
        RETURN s.skill_id AS skill_id, collect(DISTINCT code) AS covered_by
        """,
        {"codes": course_codes}
    )
    return {r["skill_id"]: r["covered_by"] for r in rows}


def _compute_alignment_metrics(role_skills: list, covered_skill_ids: set) -> dict:
    """Single source of truth for weighted alignment calculation."""
    total_weight = sum(sk["weight"] for sk in role_skills)
    covered_weight = sum(
        sk["weight"] for sk in role_skills
        if sk["skill_id"] in covered_skill_ids
    )
    score = round(covered_weight / total_weight, 4) if total_weight > 0 else 0.0
    return {
        "alignment_score": score,
        "alignment_percentage": round(score * 100, 2),
        "covered_weight": round(covered_weight, 4),
        "total_weight": round(total_weight, 4),
    }


def q_compute_skill_gap(client, role_id: str, completed_courses: list) -> dict:
    """Computes covered and missing skills for a role given completed courses."""
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}
    rid = str(role_id).strip()
    if not completed_courses:
        return {"error": "no_courses_provided"}

    cleaned_courses = []
    seen_courses = set()
    for c in completed_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_courses:
                seen_courses.add(code)
                cleaned_courses.append(code)

    if not cleaned_courses:
        return {"error": "no_courses_provided"}

    exists = client.execute_query("MATCH (r:Role {role_id: $rid}) RETURN r.name AS name", {"rid": rid})
    if not exists:
        return {"error": "role_not_found", "submitted_role_id": rid}
    role_name = exists[0]["name"]

    role_skills = _get_role_skills(client, rid)
    if not role_skills:
        return {"error": "role_has_no_required_skills", "role_id": rid}

    valid_courses, unrecognized = _resolve_courses(client, cleaned_courses)
    if not valid_courses:
        return {"error": "no_valid_courses_provided", "unrecognized_courses": unrecognized}

    skill_coverage = _get_skills_with_courses(client, valid_courses)

    covered, missing = [], []
    for sk in role_skills:
        sid = sk["skill_id"]
        tier = _weight_to_tier(sk["weight"])
        if sid in skill_coverage:
            covered.append({"skill_id": sid, "name": sk["name"], "category": sk["category"],
                            "tier": tier, "weight": sk["weight"], "covered_by": skill_coverage[sid]})
        else:
            missing.append({"skill_id": sid, "name": sk["name"], "category": sk["category"],
                            "tier": tier, "weight": sk["weight"]})

    result = {
        "role_id": rid, "role_name": role_name,
        "covered_skills": covered, "missing_skills": missing,
        "total_covered": len(covered), "total_missing": len(missing), "total_required": len(role_skills),
    }
    if unrecognized:
        result["unrecognized_courses"] = unrecognized
    return result


def q_compute_alignment_score(client, role_id: str, completed_courses: list) -> dict:
    """Computes the weighted alignment score [0,1]."""
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}
    rid = str(role_id).strip()
    if not completed_courses:
        return {"error": "no_courses_provided"}

    cleaned_courses = []
    seen_courses = set()
    for c in completed_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_courses:
                seen_courses.add(code)
                cleaned_courses.append(code)

    if not cleaned_courses:
        return {"error": "no_courses_provided"}

    exists = client.execute_query("MATCH (r:Role {role_id: $rid}) RETURN r.name AS name", {"rid": rid})
    if not exists:
        return {"error": "role_not_found", "submitted_role_id": rid}
    role_name = exists[0]["name"]

    role_skills = _get_role_skills(client, rid)
    if not role_skills:
        return {"error": "role_has_no_required_skills", "role_id": rid}

    valid_courses, unrecognized = _resolve_courses(client, cleaned_courses)
    if not valid_courses:
        return {"error": "no_valid_courses_provided", "unrecognized_courses": unrecognized}

    covered_ids = _get_skills_from_courses(client, valid_courses)
    metrics = _compute_alignment_metrics(role_skills, covered_ids)

    result = {
        "role_id": rid, "role_name": role_name,
        "alignment_score": metrics["alignment_score"],
        "alignment_percentage": metrics["alignment_percentage"],
        "covered_weight": metrics["covered_weight"],
        "total_weight": metrics["total_weight"],
    }
    if unrecognized:
        result["unrecognized_courses"] = unrecognized
    return result


def q_recommend_courses_to_close_gap(client, role_id: str, completed_courses: list) -> dict:
    """Recommends courses that teach missing skills, excluding already completed."""
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}
    rid = str(role_id).strip()
    if not completed_courses:
        return {"error": "no_courses_provided"}

    cleaned_courses = []
    seen_courses = set()
    for c in completed_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_courses:
                seen_courses.add(code)
                cleaned_courses.append(code)

    if not cleaned_courses:
        return {"error": "no_courses_provided"}

    exists = client.execute_query("MATCH (r:Role {role_id: $rid}) RETURN r.name AS name", {"rid": rid})
    if not exists:
        return {"error": "role_not_found", "submitted_role_id": rid}
    role_name = exists[0]["name"]

    role_skills = _get_role_skills(client, rid)
    if not role_skills:
        return {"error": "role_has_no_required_skills", "role_id": rid}

    valid_courses, unrecognized = _resolve_courses(client, cleaned_courses)
    if not valid_courses:
        return {"error": "no_valid_courses_provided", "unrecognized_courses": unrecognized}

    covered_ids = _get_skills_from_courses(client, valid_courses)
    missing_skills = [sk for sk in role_skills if sk["skill_id"] not in covered_ids]

    if not missing_skills:
        result = {"role_id": rid, "role_name": role_name,
                  "missing_skills": [], "total_missing_skills": 0, "total_recommended_courses": 0}
        if unrecognized:
            result["unrecognized_courses"] = unrecognized
        return result

    missing_skill_ids = [sk["skill_id"] for sk in missing_skills]
    rows = client.execute_query(
        """
        UNWIND $skill_ids AS sid
        MATCH (s:Skill {skill_id: sid})
        OPTIONAL MATCH (c:Course)-[:TEACHES]->(s)
        WHERE c IS NULL OR NOT c.course_code IN $completed
        RETURN sid,
               collect(DISTINCT CASE
                   WHEN c IS NULL THEN null
                   ELSE {
                       course_code: c.course_code,
                       name: c.name,
                       semester_offering: c.semester_offering
                   }
               END) AS taught_by_raw
        """,
        {"skill_ids": missing_skill_ids, "completed": valid_courses}
    )

    sid_to_teaching = {}
    for r in rows:
        sid_to_teaching.setdefault(r["sid"], [])
        for cb in (r.get("taught_by_raw") or []):
            if cb and cb.get("course_code"):
                raw_sem = cb.get("semester_offering") or ""
                sem_list = [s.strip() for s in raw_sem.split(",") if s.strip()]
                sid_to_teaching[r["sid"]].append({
                    "course_code": cb["course_code"],
                    "name": cb["name"],
                    "semester_offering": sem_list,
                })

    all_rec_codes = list({
        c["course_code"]
        for courses in sid_to_teaching.values()
        for c in courses
    })

    track_map = {}
    if all_rec_codes:
        track_rows = client.execute_query(
            """
            UNWIND $codes AS code
            MATCH (c:Course {course_code: code})-[:BELONGS_TO]->(t:Track)
            RETURN code,
                   collect(DISTINCT CASE
                       WHEN t IS NOT NULL THEN {track_id: t.track_id, name: t.name}
                   END) AS tracks
            """,
            {"codes": all_rec_codes}
        )
        track_map = {
            r["code"]: sorted([t for t in (r.get("tracks") or []) if t],
                              key=lambda x: (x.get("name") or "", x.get("track_id") or ""))
            for r in track_rows
        }

    missing_out = []
    all_recommended = set()
    for sk in missing_skills:
        sid = sk["skill_id"]
        taught_by = sid_to_teaching.get(sid, [])
        for c in taught_by:
            c["tracks"] = track_map.get(c["course_code"], [])
            all_recommended.add(c["course_code"])
        taught_by.sort(key=lambda x: x["course_code"])
        missing_out.append({
            "skill_id": sid, "name": sk["name"],
            "tier": _weight_to_tier(sk["weight"]), "weight": sk["weight"],
            "taught_by": taught_by,
        })

    result = {
        "role_id": rid, "role_name": role_name,
        "missing_skills": missing_out,
        "total_missing_skills": len(missing_out),
        "total_recommended_courses": len(all_recommended),
    }
    if unrecognized:
        result["unrecognized_courses"] = unrecognized
    return result


def q_estimate_alignment_improvement(client, role_id: str, completed_courses: list, planned_courses: list) -> dict:
    """Estimates how much planned courses improve alignment score."""
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}
    rid = str(role_id).strip()
    if not completed_courses:
        return {"error": "no_courses_provided"}
    if not planned_courses:
        return {"error": "no_planned_courses_provided"}

    cleaned_completed = []
    seen_completed = set()
    for c in completed_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_completed:
                seen_completed.add(code)
                cleaned_completed.append(code)

    cleaned_planned = []
    seen_planned = set()
    for c in planned_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_planned:
                seen_planned.add(code)
                cleaned_planned.append(code)

    if not cleaned_completed:
        return {"error": "no_courses_provided"}
    if not cleaned_planned:
        return {"error": "no_planned_courses_provided"}

    exists = client.execute_query("MATCH (r:Role {role_id: $rid}) RETURN r.name AS name", {"rid": rid})
    if not exists:
        return {"error": "role_not_found", "submitted_role_id": rid}
    role_name = exists[0]["name"]

    role_skills = _get_role_skills(client, rid)
    if not role_skills:
        return {"error": "role_has_no_required_skills", "role_id": rid}

    valid_completed, unrecognized_c = _resolve_courses(client, cleaned_completed)
    if not valid_completed:
        return {"error": "no_valid_courses_provided", "unrecognized_courses": unrecognized_c}

    valid_planned, unrecognized_p = _resolve_courses(client, cleaned_planned)
    if not valid_planned:
        return {"error": "no_valid_planned_courses_provided", "unrecognized_planned_courses": unrecognized_p}

    #track which planned courses overlap with already-completed courses
    completed_set = set(valid_completed)
    ignored_planned = [c for c in valid_planned if c in completed_set]
    valid_planned = [c for c in valid_planned if c not in completed_set]

    #compute current alignment BEFORE checking whether planned list is empty
    current_covered = _get_skills_from_courses(client, valid_completed)
    current_metrics = _compute_alignment_metrics(role_skills, current_covered)

    #CASE A: no valid planned courses remain after deduplication
    if not valid_planned:
        still_missing_ids = {sk["skill_id"] for sk in role_skills} - current_covered
        still_missing = [
            {"skill_id": sk["skill_id"], "name": sk["name"],
             "tier": _weight_to_tier(sk["weight"]), "weight": sk["weight"]}
            for sk in role_skills if sk["skill_id"] in still_missing_ids
        ]
        result = {
            "role_id": rid, "role_name": role_name,
            "current_alignment_score": current_metrics["alignment_score"],
            "current_alignment_percentage": current_metrics["alignment_percentage"],
            "projected_alignment_score": current_metrics["alignment_score"],
            "projected_alignment_percentage": current_metrics["alignment_percentage"],
            "alignment_improvement": 0.0,
            "newly_covered_skills": [],
            "still_missing_skills": still_missing,
            "total_newly_covered": 0,
            "total_still_missing": len(still_missing),
            "warning": "no_new_valid_planned_courses_after_deduplication",
        }
        if unrecognized_c: result["unrecognized_courses"] = unrecognized_c
        if unrecognized_p: result["unrecognized_planned_courses"] = unrecognized_p
        return result

    #compute projected alignment using current + valid planned courses
    projected_covered = _get_skills_from_courses(client, valid_completed + valid_planned)
    projected_metrics = _compute_alignment_metrics(role_skills, projected_covered)

    current_score = current_metrics["alignment_score"]
    projected_score = projected_metrics["alignment_score"]
    improvement = round(projected_score - current_score, 4)

    new_skill_ids = projected_covered - current_covered
    planned_coverage_map = {}
    if new_skill_ids:
        coverage_rows = client.execute_query(
            """
            UNWIND $codes AS code
            MATCH (c:Course {course_code: code})-[:TEACHES]->(s:Skill)
            WHERE s.skill_id IN $skill_ids
            RETURN s.skill_id AS skill_id, collect(DISTINCT code) AS courses
            """,
            {"codes": valid_planned, "skill_ids": list(new_skill_ids)}
        )
        planned_coverage_map = {r["skill_id"]: sorted(r.get("courses") or []) for r in coverage_rows}

    newly_covered = []
    for sk in role_skills:
        if sk["skill_id"] in new_skill_ids:
            newly_covered.append({
                "skill_id": sk["skill_id"], "name": sk["name"],
                "tier": _weight_to_tier(sk["weight"]), "weight": sk["weight"],
                "covered_by_planned": planned_coverage_map.get(sk["skill_id"], []),
            })

    still_missing_ids = {sk["skill_id"] for sk in role_skills} - projected_covered
    still_missing = [
        {"skill_id": sk["skill_id"], "name": sk["name"],
         "tier": _weight_to_tier(sk["weight"]), "weight": sk["weight"]}
        for sk in role_skills if sk["skill_id"] in still_missing_ids
    ]

    result = {
        "role_id": rid, "role_name": role_name,
        "current_alignment_score": current_score,
        "current_alignment_percentage": current_metrics["alignment_percentage"],
        "projected_alignment_score": projected_score,
        "projected_alignment_percentage": projected_metrics["alignment_percentage"],
        "alignment_improvement": improvement,
        "newly_covered_skills": newly_covered, "still_missing_skills": still_missing,
        "total_newly_covered": len(newly_covered), "total_still_missing": len(still_missing),
    }
    #CASE B: some planned courses were already completed and skipped
    if ignored_planned:
        result["warning"] = "some_planned_courses_already_completed"
        result["ignored_planned_courses"] = ignored_planned
    if unrecognized_c: result["unrecognized_courses"] = unrecognized_c
    if unrecognized_p: result["unrecognized_planned_courses"] = unrecognized_p
    return result


def q_find_best_matching_roles(client, completed_courses: list) -> dict:
    """Ranks all roles by alignment score for the given completed courses."""
    if not completed_courses:
        return {"error": "no_courses_provided"}

    cleaned_courses = []
    seen_courses = set()
    for c in completed_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_courses:
                seen_courses.add(code)
                cleaned_courses.append(code)

    if not cleaned_courses:
        return {"error": "no_courses_provided"}

    valid_courses, unrecognized = _resolve_courses(client, cleaned_courses)
    if not valid_courses:
        return {"error": "no_valid_courses_provided", "unrecognized_courses": unrecognized}

    covered_ids = _get_skills_from_courses(client, valid_courses)

    rows = client.execute_query(
        """
        MATCH (r:Role)
        OPTIONAL MATCH (r)-[rel:REQUIRES]->(s:Skill)
        RETURN
            r.role_id AS role_id,
            r.name AS role_name,
            r.domain AS domain,
            collect(DISTINCT CASE
                WHEN s IS NOT NULL THEN {skill_id: s.skill_id, weight: rel.weight}
            END) AS role_skills
        """,
        {}
    )

    if not rows:
        return {"error": "no_roles_in_graph"}

    ranked = []
    total_roles_evaluated = len(rows)

    for row in rows:
        role_skills = [sk for sk in (row.get("role_skills") or []) if sk]
        if not role_skills:
            continue
        total_weight = sum(sk["weight"] for sk in role_skills)
        covered_weight = sum(sk["weight"] for sk in role_skills if sk["skill_id"] in covered_ids)
        score = round(covered_weight / total_weight, 4) if total_weight else 0.0
        if score > 0.0:
            ranked.append({
                "role_id": row["role_id"], "role_name": row["role_name"],
                "domains": [row["domain"]] if row.get("domain") else [],
                "alignment_score": score, "alignment_percentage": round(score * 100, 2),
            })

    ranked.sort(key=lambda x: (-x["alignment_score"], x["role_name"]))
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i

    result = {
        "completed_courses": valid_courses,
        "ranked_roles": ranked,
        "total_roles_evaluated": total_roles_evaluated,
    }
    if unrecognized:
        result["unrecognized_courses"] = unrecognized
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# A4 — Track Guidance & Comparison
# ═══════════════════════════════════════════════════════════════════════════════

def _track_alignment_score(track_skill_ids: set, role_skills: list) -> float:
    total_weight = sum(sk["weight"] for sk in role_skills)
    covered_weight = sum(sk["weight"] for sk in role_skills if sk["skill_id"] in track_skill_ids)
    return round(covered_weight / total_weight, 4) if total_weight else 0.0


def _get_track_skills(client, track_id: str) -> list:
    rows = client.execute_query(
        """
        MATCH (t:Track {track_id: $tid})<-[:BELONGS_TO]-(c:Course)-[:TEACHES]->(s:Skill)
        RETURN DISTINCT s.skill_id AS skill_id, s.name AS name, s.category AS category
        ORDER BY s.name
        """,
        {"tid": track_id}
    )
    return [{"skill_id": r["skill_id"], "name": r["name"], "category": r["category"]} for r in rows]


def _get_track_courses(client, track_id: str) -> list:
    rows = client.execute_query(
        """
        MATCH (c:Course)-[:BELONGS_TO]->(t:Track {track_id: $tid})
        RETURN c.course_code AS course_code, c.name AS name, c.credits AS credits,
               c.level AS level, c.semester_offering AS semester_offering
        ORDER BY c.course_code
        """,
        {"tid": track_id}
    )
    result = []
    for r in rows:
        raw_sem = r.get("semester_offering") or ""
        sem_list = [s.strip() for s in raw_sem.split(",") if s.strip()]
        result.append({
            "course_code": r["course_code"], "name": r["name"],
            "credits": r["credits"], "level": r["level"], "semester_offering": sem_list,
        })
    return result


def _get_track_supported_roles(client, track_id: str) -> list:
    rows = client.execute_query(
        """
        MATCH (t:Track {track_id: $tid})<-[:BELONGS_TO]-(c:Course)-[:TEACHES]->(s:Skill)
        RETURN DISTINCT s.skill_id AS skill_id
        """,
        {"tid": track_id}
    )
    track_skill_ids = {r["skill_id"] for r in rows}

    role_rows = client.execute_query(
        """
        MATCH (r:Role)
        OPTIONAL MATCH (r)-[rel:REQUIRES]->(s:Skill)
        RETURN r.role_id AS role_id, r.name AS role_name, r.domain AS domain,
               collect(DISTINCT CASE WHEN s IS NOT NULL THEN {skill_id: s.skill_id, weight: rel.weight} END) AS role_skills
        """,
        {}
    )

    supported = []
    for row in role_rows:
        role_skills = [sk for sk in (row.get("role_skills") or []) if sk]
        if not role_skills:
            continue
        score = _track_alignment_score(track_skill_ids, role_skills)
        if score > 0.0:
            supported.append({
                "role_id": row["role_id"], "role_name": row["role_name"],
                "domains": [row["domain"]] if row.get("domain") else [],
            })
    return supported


def q_get_track_overview(client, track_id: str) -> dict:
    if not track_id or not str(track_id).strip():
        return {"error": "no_track_provided"}
    tid = str(track_id).strip()
    exists = client.execute_query("MATCH (t:Track {track_id: $tid}) RETURN t.name AS name", {"tid": tid})
    if not exists:
        return {"error": "track_not_found", "submitted_track_id": tid}
    track_name = exists[0]["name"]
    courses = _get_track_courses(client, tid)
    skills_taught = _get_track_skills(client, tid)
    supported_roles = _get_track_supported_roles(client, tid)
    return {
        "track_id": tid, "track_name": track_name,
        "courses": courses, "total_courses": len(courses),
        "skills_taught": skills_taught, "total_skills": len(skills_taught),
        "supported_roles": supported_roles, "total_supported_roles": len(supported_roles),
    }


def q_compare_tracks(client, track_id_1: str, track_id_2: str) -> dict:
    if not track_id_1 or not track_id_2:
        return {"error": "missing_track_ids"}
    tid1 = str(track_id_1).strip()
    tid2 = str(track_id_2).strip()
    if not tid1 or not tid2:
        return {"error": "missing_track_ids"}
    if tid1 == tid2:
        return {"error": "identical_tracks_provided", "track_id": tid1}

    t1 = client.execute_query("MATCH (t:Track {track_id: $tid}) RETURN t.name AS name", {"tid": tid1})
    t2 = client.execute_query("MATCH (t:Track {track_id: $tid}) RETURN t.name AS name", {"tid": tid2})
    not_found = []
    if not t1: not_found.append(tid1)
    if not t2: not_found.append(tid2)
    if not_found:
        return {"error": "track_not_found", "not_found_ids": not_found}

    t1_name = t1[0]["name"]
    t2_name = t2[0]["name"]

    c1_list = _get_track_courses(client, tid1)
    c2_list = _get_track_courses(client, tid2)
    c1_map = {c["course_code"]: c for c in c1_list}
    c2_map = {c["course_code"]: c for c in c2_list}
    shared_c_codes = set(c1_map) & set(c2_map)
    courses = {
        "track_1_only": [c1_map[k] for k in sorted(c1_map) if k not in shared_c_codes],
        "track_2_only": [c2_map[k] for k in sorted(c2_map) if k not in shared_c_codes],
        "shared": [c1_map[k] for k in sorted(shared_c_codes)],
        "total_track_1_only": len(c1_map) - len(shared_c_codes),
        "total_track_2_only": len(c2_map) - len(shared_c_codes),
        "total_shared": len(shared_c_codes),
    }

    s1_list = _get_track_skills(client, tid1)
    s2_list = _get_track_skills(client, tid2)
    s1_map = {s["skill_id"]: s for s in s1_list}
    s2_map = {s["skill_id"]: s for s in s2_list}
    shared_s_ids = set(s1_map) & set(s2_map)
    skills = {
        "track_1_only": [s1_map[k] for k in sorted(s1_map) if k not in shared_s_ids],
        "track_2_only": [s2_map[k] for k in sorted(s2_map) if k not in shared_s_ids],
        "shared": [s1_map[k] for k in sorted(shared_s_ids)],
        "total_track_1_only": len(s1_map) - len(shared_s_ids),
        "total_track_2_only": len(s2_map) - len(shared_s_ids),
        "total_shared": len(shared_s_ids),
    }

    t1_skill_ids = set(s1_map.keys())
    t2_skill_ids = set(s2_map.keys())

    role_rows = client.execute_query(
        """
        MATCH (r:Role)
        OPTIONAL MATCH (r)-[rel:REQUIRES]->(s:Skill)
        RETURN r.role_id AS role_id, r.name AS role_name, r.domain AS domain,
               collect(DISTINCT CASE WHEN s IS NOT NULL THEN {skill_id: s.skill_id, weight: rel.weight} END) AS role_skills
        """,
        {}
    )

    t1_role_ids, t2_role_ids = set(), set()
    role_map = {}
    for row in role_rows:
        role_skills = [sk for sk in (row.get("role_skills") or []) if sk]
        if not role_skills:
            continue
        sc1 = _track_alignment_score(t1_skill_ids, role_skills)
        sc2 = _track_alignment_score(t2_skill_ids, role_skills)
        rid = row["role_id"]
        role_map[rid] = {
            "role_id": rid, "role_name": row["role_name"],
            "domains": [row["domain"]] if row.get("domain") else [],
            "track_1_score": sc1, "track_2_score": sc2,
        }
        if sc1 > 0.0: t1_role_ids.add(rid)
        if sc2 > 0.0: t2_role_ids.add(rid)

    shared_role_ids = t1_role_ids & t2_role_ids

    def _role_entry(rid):
        rr = role_map[rid]
        return {"role_id": rr["role_id"], "role_name": rr["role_name"], "domains": rr["domains"]}

    role_alignment = {
        "track_1_only": [_role_entry(r) for r in sorted(t1_role_ids - shared_role_ids)],
        "track_2_only": [_role_entry(r) for r in sorted(t2_role_ids - shared_role_ids)],
        "shared": [_role_entry(r) for r in sorted(shared_role_ids)],
        "total_track_1_only": len(t1_role_ids - shared_role_ids),
        "total_track_2_only": len(t2_role_ids - shared_role_ids),
        "total_shared": len(shared_role_ids),
    }

    return {
        "track_1": {"track_id": tid1, "track_name": t1_name},
        "track_2": {"track_id": tid2, "track_name": t2_name},
        "courses": courses, "skills": skills, "role_alignment": role_alignment,
    }


def q_recommend_track_for_role(client, role_id: str) -> dict:
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}
    rid = str(role_id).strip()
    exists = client.execute_query("MATCH (r:Role {role_id: $rid}) RETURN r.name AS name", {"rid": rid})
    if not exists:
        return {"error": "role_not_found", "submitted_role_id": rid}
    role_name = exists[0]["name"]
    role_skills = _get_role_skills(client, rid)
    if not role_skills:
        return {"error": "role_has_no_required_skills", "role_id": rid}

    all_tracks = client.execute_query("MATCH (t:Track) RETURN t.track_id AS track_id, t.name AS name")
    if not all_tracks:
        return {"error": "no_tracks_in_graph"}

    track_skill_rows = client.execute_query(
        """
        MATCH (t:Track)<-[:BELONGS_TO]-(c:Course)-[:TEACHES]->(s:Skill)
        RETURN t.track_id AS track_id, collect(DISTINCT s.skill_id) AS skill_ids
        """,
        {}
    )
    track_skill_map = {r["track_id"]: set(r.get("skill_ids") or []) for r in track_skill_rows}
    total_weight = sum(sk["weight"] for sk in role_skills)

    ranked = []
    for tr in all_tracks:
        tid = tr["track_id"]
        track_skill_ids = track_skill_map.get(tid, set())
        score = _track_alignment_score(track_skill_ids, role_skills)
        if score > 0.0:
            covered_weight = sum(sk["weight"] for sk in role_skills if sk["skill_id"] in track_skill_ids)
            ranked.append({
                "track_id": tid, "track_name": tr["name"],
                "alignment_score": score, "alignment_percentage": round(score * 100, 2),
                "covered_weight": round(covered_weight, 4), "total_weight": round(total_weight, 4),
            })

    ranked.sort(key=lambda x: (-x["alignment_score"], x["track_name"]))
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i

    return {
        "role_id": rid, "role_name": role_name,
        "ranked_tracks": ranked, "total_tracks_evaluated": len(all_tracks),
    }


def q_recommend_track_for_skill(client, skill_id: str) -> dict:
    if not skill_id or not str(skill_id).strip():
        return {"error": "no_skill_provided"}
    sid = str(skill_id).strip()
    exists = client.execute_query("MATCH (s:Skill {skill_id: $sid}) RETURN s.name AS name", {"sid": sid})
    if not exists:
        return {"error": "skill_not_found", "submitted_skill_id": sid}
    skill_name = exists[0]["name"]

    all_tracks = client.execute_query("MATCH (t:Track) RETURN t.track_id AS track_id, t.name AS name")
    if not all_tracks:
        return {"error": "no_tracks_in_graph"}

    rows = client.execute_query(
        """
        MATCH (t:Track)<-[:BELONGS_TO]-(c:Course)-[:TEACHES]->(s:Skill {skill_id: $sid})
        RETURN t.track_id AS track_id, t.name AS track_name,
               collect(DISTINCT {course_code: c.course_code, name: c.name}) AS courses_teaching_skill
        """,
        {"sid": sid}
    )

    ranked = []
    for r in rows:
        courses = sorted(r.get("courses_teaching_skill") or [], key=lambda x: x["course_code"])
        if courses:
            ranked.append({
                "track_id": r["track_id"], "track_name": r["track_name"],
                "courses_teaching_skill": courses, "course_count": len(courses),
            })

    ranked.sort(key=lambda x: (-x["course_count"], x["track_name"]))
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i

    return {
        "skill_id": sid, "skill_name": skill_name,
        "ranked_tracks": ranked, "total_tracks_evaluated": len(all_tracks),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# A5 — ALE Planning Support
# ═══════════════════════════════════════════════════════════════════════════════

# ── OP6: Get Courses by Track ────────────────────────────────────────────────
def q_get_courses_by_track(client, track_id: str) -> dict:
    """
    Returns all courses belonging to a track with full planning metadata:
    code, name, credits, level, prerequisites (course codes), semester_offering,
    and track. Ordered by level then course_code.
    Used by the ALE for semester planning and graduation roadmap generation.
    """
    if not track_id or not str(track_id).strip():
        return {"error": "no_track_provided"}

    tid = str(track_id).strip()

    exists = client.execute_query(
        "MATCH (t:Track {track_id: $tid}) RETURN t.name AS name", {"tid": tid}
    )
    if not exists:
        return {"error": "track_not_found", "submitted_track_id": tid}
    track_name = exists[0]["name"]

    rows = client.execute_query(
        """
        MATCH (c:Course)-[:BELONGS_TO]->(t:Track {track_id: $tid})
        OPTIONAL MATCH (c)-[:PREREQ]->(p:Course)
        WITH c, t, collect(DISTINCT CASE WHEN p IS NOT NULL THEN p.course_code END) AS prerequisites
        OPTIONAL MATCH (c)-[:HAS_PREREQ_CONSTRAINT]->(pc:PrerequisiteConstraint)
        RETURN
            c.course_code       AS course_code,
            c.name              AS name,
            c.credits           AS credits,
            c.level             AS level,
            c.semester_offering AS semester_offering,
            prerequisites,
            pc.type             AS constraint_type,
            pc.value            AS constraint_value
        ORDER BY c.level, c.course_code
        """,
        {"tid": tid}
    )

    courses = []
    for r in rows:
        raw_sem = r.get("semester_offering") or ""
        sem_list = [s.strip() for s in raw_sem.split(",") if s.strip()]
        prereqs = sorted([p for p in (r.get("prerequisites") or []) if p])
        courses.append({
            "course_code": r["course_code"],
            "name": r["name"],
            "credits": r["credits"],
            "level": r["level"],
            "prerequisites": prereqs,
            "semester_offering": sem_list,
            "credit_threshold": _parse_credit_threshold_constraint(
                r.get("constraint_type"), r.get("constraint_value")
            ),
            "track": {"track_id": tid, "name": track_name},
        })

    return {
        "track_id": tid,
        "track_name": track_name,
        "courses": courses,
        "total_courses": len(courses),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# A6 — Entity Resolution
# ═══════════════════════════════════════════════════════════════════════════════

_ENTITY_CONFIG = {
    "course": {"label": "Course", "id_property": "course_code", "name_property": "name"},
    "role":   {"label": "Role",   "id_property": "role_id",     "name_property": "name"},
    "track":  {"label": "Track",  "id_property": "track_id",    "name_property": "name"},
    "skill":  {"label": "Skill",  "id_property": "skill_id",    "name_property": "name"},
}

# None = not yet loaded; False = load failed; dict = loaded successfully
_entity_aliases_cache = None

_FILLER_WORDS = {
    "course": ["course", "subject", "class"],
    "role":   ["role", "job", "career", "position"],
    "track":  ["track", "specialization", "major"],
    "skill":  ["skill", "topic"],
}

_MATCH_TYPE_ORDER = {
    "exact_id": 0,
    "exact_name": 1,
    "alias": 2,
    "explicit_ambiguous": 3,
    "partial_name": 4,
}

# Alias file lives next to this module under data/
_ALIAS_FILE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'entity_aliases.json')


def _normalize_entity_text(text: str, entity_type: str) -> str:
    result = text.lower()
    result = result.strip()
    result = re.sub(r' +', ' ', result)
    result = result.replace('_', ' ').replace('-', ' ')
    result = re.sub(r'[\[\]()\.,;:]', '', result)
    for word in _FILLER_WORDS.get(entity_type, []):
        result = re.sub(r'\b' + re.escape(word) + r'\b', '', result)
    result = re.sub(r' +', ' ', result).strip()
    return result


def _load_entity_aliases():
    """Load and cache entity_aliases.json. Returns dict on success, False on failure."""
    global _entity_aliases_cache
    if _entity_aliases_cache is not None:
        return _entity_aliases_cache
    try:
        with open(_ALIAS_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        _entity_aliases_cache = data
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load entity aliases from %s: %s", _ALIAS_FILE_PATH, exc)
        _entity_aliases_cache = False
        return False


def _get_entity_by_id(client, entity_type: str, entity_id: str):
    """Return {id, name} for an entity matching id_property exactly, or None."""
    cfg = _ENTITY_CONFIG[entity_type]
    rows = client.execute_query(
        f"MATCH (n:{cfg['label']}) WHERE n.{cfg['id_property']} = $eid "
        f"RETURN n.{cfg['id_property']} AS id, n.{cfg['name_property']} AS name",
        {"eid": entity_id}
    )
    return rows[0] if rows else None


def _get_all_entities(client, entity_type: str) -> list:
    """Return [{id, name}] for all entities of the given type."""
    cfg = _ENTITY_CONFIG[entity_type]
    rows = client.execute_query(
        f"MATCH (n:{cfg['label']}) "
        f"RETURN n.{cfg['id_property']} AS id, n.{cfg['name_property']} AS name",
        {}
    )
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def _format_resolver_ok(entity_type, entity_text, normalized, entity_id, name, match_type, score):
    return {
        "status": "ok",
        "entity_type": entity_type,
        "entity_text": entity_text,
        "normalized_entity_text": normalized,
        "resolved_id": entity_id,
        "name": name,
        "match_type": match_type,
        "confidence": score,
        "matches": [{"id": entity_id, "name": name, "match_type": match_type, "score": score}],
        "total_matches": 1,
        "returned_matches": 1,
    }


def _format_resolver_ambiguous(entity_type, entity_text, normalized, matches):
    matches.sort(key=lambda m: (_MATCH_TYPE_ORDER.get(m["match_type"], 99), m.get("name") or ""))
    limited = matches[:10]
    return {
        "status": "ambiguous",
        "entity_type": entity_type,
        "entity_text": entity_text,
        "normalized_entity_text": normalized,
        "matches": limited,
        "total_matches": len(matches),
        "returned_matches": len(limited),
    }


def _format_resolver_not_found(entity_type, entity_text, normalized):
    return {
        "status": "not_found",
        "entity_type": entity_type,
        "entity_text": entity_text,
        "normalized_entity_text": normalized,
        "matches": [],
        "total_matches": 0,
        "returned_matches": 0,
    }


def _format_resolver_error(error_code, entity_type, entity_text, **extra):
    result = {
        "status": "error",
        "error": error_code,
        "entity_type": entity_type,
        "entity_text": entity_text,
    }
    result.update(extra)
    return result


def q_resolve_entity(client, entity_type: str, entity_text: str) -> dict:
    """
    Resolve a natural-language entity reference to a graph ID.

    Supported entity_type values: "course", "role", "track", "skill".
    Returns a dict with "status": "ok" | "ambiguous" | "not_found" | "error".
    Never raises exceptions for business-logic failures.
    """
    logger.debug("Resolver called: entity_type=%s, entity_text=%r", entity_type, entity_text)

    # ── Step 1: Input validation ─────────────────────────────────────────────

    if entity_type not in _ENTITY_CONFIG:
        return _format_resolver_error(
            "unsupported_entity_type", entity_type, entity_text or "",
            allowed_entity_types=list(_ENTITY_CONFIG.keys())
        )

    if not entity_text or not str(entity_text).strip():
        return _format_resolver_error("empty_entity_text", entity_type, entity_text or "")

    entity_text = str(entity_text)
    trimmed = entity_text.strip()
    normalized = _normalize_entity_text(trimmed, entity_type)

    # ── Step 2: Exact ID match (uses original trimmed text) ──────────────────

    match = _get_entity_by_id(client, entity_type, trimmed)
    if match:
        result = _format_resolver_ok(
            entity_type, entity_text, normalized,
            match["id"], match["name"], "exact_id", 1.0
        )
        logger.debug("Resolver result: status=ok, match_type=exact_id, id=%s", match["id"])
        return result

    # ── Step 3: Exact normalized graph-name match ────────────────────────────

    all_entities = _get_all_entities(client, entity_type)
    exact_name_hits = [
        e for e in all_entities
        if e["name"] and _normalize_entity_text(e["name"], entity_type) == normalized
    ]

    if len(exact_name_hits) == 1:
        m = exact_name_hits[0]
        result = _format_resolver_ok(
            entity_type, entity_text, normalized,
            m["id"], m["name"], "exact_name", 1.0
        )
        logger.debug("Resolver result: status=ok, match_type=exact_name, id=%s", m["id"])
        return result

    if len(exact_name_hits) > 1:
        matches = [
            {"id": m["id"], "name": m["name"], "match_type": "exact_name", "score": 1.0}
            for m in exact_name_hits
        ]
        result = _format_resolver_ambiguous(entity_type, entity_text, normalized, matches)
        logger.debug("Resolver result: status=ambiguous, match_type=exact_name, count=%d", len(matches))
        return result

    # ── Step 4: Alias match ──────────────────────────────────────────────────

    aliases_data = _load_entity_aliases()
    if aliases_data is False:
        return _format_resolver_error("alias_file_error", entity_type, entity_text)

    entity_section = aliases_data.get(entity_type, {})
    aliases_section = entity_section.get("aliases", {})

    alias_match_id = None
    for eid, alias_list in aliases_section.items():
        if normalized in alias_list:
            if alias_match_id is not None:
                logger.warning(
                    "Duplicate alias '%s' maps to both '%s' and '%s' for entity_type '%s'",
                    normalized, alias_match_id, eid, entity_type
                )
                return _format_resolver_error("alias_validation_error", entity_type, entity_text)
            alias_match_id = eid

    if alias_match_id is not None:
        target = _get_entity_by_id(client, entity_type, alias_match_id)
        if target is None:
            logger.warning(
                "Alias target '%s' not found in graph for entity_type '%s'",
                alias_match_id, entity_type
            )
            return _format_resolver_error("alias_validation_error", entity_type, entity_text)
        result = _format_resolver_ok(
            entity_type, entity_text, normalized,
            target["id"], target["name"], "alias", 0.95
        )
        logger.debug("Resolver result: status=ok, match_type=alias, id=%s", target["id"])
        return result

    # ── Step 5: Explicit ambiguous term match ────────────────────────────────

    ambiguous_section = entity_section.get("ambiguous_terms", {})

    if normalized in ambiguous_section:
        candidate_ids = ambiguous_section[normalized]
        matches = []
        for cid in candidate_ids:
            target = _get_entity_by_id(client, entity_type, cid)
            if target is None:
                logger.warning(
                    "Ambiguous term target '%s' not found in graph for entity_type '%s'",
                    cid, entity_type
                )
                return _format_resolver_error("alias_validation_error", entity_type, entity_text)
            matches.append({
                "id": target["id"],
                "name": target["name"],
                "match_type": "explicit_ambiguous",
                "score": 0.85,
            })
        result = _format_resolver_ambiguous(entity_type, entity_text, normalized, matches)
        logger.debug(
            "Resolver result: status=ambiguous, match_type=explicit_ambiguous, count=%d",
            len(matches)
        )
        return result

    # ── Step 6: Partial graph-name match ─────────────────────────────────────

    partial_hits = [
        e for e in all_entities
        if e["name"] and normalized in _normalize_entity_text(e["name"], entity_type)
    ]

    if len(partial_hits) == 1:
        m = partial_hits[0]
        result = _format_resolver_ok(
            entity_type, entity_text, normalized,
            m["id"], m["name"], "partial_name", 0.70
        )
        logger.debug("Resolver result: status=ok, match_type=partial_name, id=%s", m["id"])
        return result

    if len(partial_hits) >= 2:
        matches = [
            {"id": m["id"], "name": m["name"], "match_type": "partial_name", "score": 0.70}
            for m in partial_hits
        ]
        result = _format_resolver_ambiguous(entity_type, entity_text, normalized, matches)
        logger.debug(
            "Resolver result: status=ambiguous, match_type=partial_name, count=%d",
            len(matches)
        )
        return result

    # ── Not found ─────────────────────────────────────────────────────────────

    result = _format_resolver_not_found(entity_type, entity_text, normalized)
    logger.debug("Resolver result: status=not_found for entity_type=%s", entity_type)
    return result
