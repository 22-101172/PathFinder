"""
queries.py
All Cypher query functions for the PathFinder KG-Engine.

Each function returns a plain dict/list ready for the KGEngine layer.
No exceptions are raised for business-logic failures — structured error
dicts with an "error" key are returned instead.
"""

import re

# ── Validation helpers ───────────────────────────────────────────────────────

# Valid course codes: letters / digits / hyphens (e.g. C-AI321, HUM110)
_COURSE_CODE_RE = re.compile(r'^[A-Za-z][A-Za-z0-9\-]*$')


def _is_valid_course_code(code: str) -> bool:
    return bool(code and _COURSE_CODE_RE.match(code.strip()))


# ═══════════════════════════════════════════════════════════════════════════════
# A2 — Course Catalogue Lookup
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
        RETURN
            c.course_code AS course_code,
            c.name AS name,
            c.credits AS credits,
            c.level AS level,
            c.semester_offering AS semester_offering,
            c.description AS description,
            collect(DISTINCT CASE
                WHEN t IS NOT NULL THEN {track_id: t.track_id, name: t.name}
            END) AS tracks
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

    Notes:
    - full_prerequisite_tree is course-based only
    - non_course_prerequisites are returned separately
    """
    if not course_code or not str(course_code).strip():
        return {"error": "invalid_course_code", "submitted_code": course_code}

    code = str(course_code).strip()
    if not _is_valid_course_code(code):
        return {"error": "invalid_course_code", "submitted_code": code}

    if depth not in ("direct", "full"):
        return {"error": "invalid_depth_value", "submitted_depth": depth}

    # Single main query:
    # - verifies course existence
    # - gets course name
    # - gets direct course prerequisites
    # - gets non-course prerequisite constraints
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
    non_course = [x for x in (row.get("non_course_prerequisites") or []) if x is not None]

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
    #empty string or None or whitespace
    if not course_code or not str(course_code).strip():
        return {"error": "invalid_course_code", "submitted_code": course_code}

    #invalid course code format
    code = str(course_code).strip()
    if not _is_valid_course_code(code):
        return {"error": "invalid_course_code", "submitted_code": code}

    #execute one query to get course info and taught skills
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

    #course not found
    if not rows:
        return {"error": "course_not_found", "submitted_code": code}

    #process the result
    row = rows[0]
    skills = [s for s in (row.get("skills_taught") or []) if s]

    #sort skills alphabetically by name
    skills.sort(key=lambda x: x["name"])

    #return result
    return {
        "course_code": code,
        "name": row["course_name"],
        "skills_taught": skills,
        "total_skills": len(skills),
    }


# ── OP4: Search Courses by Skill ─────────────────────────────────────────────

def q_search_courses_by_skill(client, skills: list) -> dict:
    """
    Given a list of skill names, return all courses that teach any of them.
    """
    #no skills provided
    if not skills:
        return {"error": "no_skills_provided"}

    #remove empty values and whitespace
    skill_names = [s.strip() for s in skills if s and s.strip()]
    if not skill_names:
        return {"error": "no_skills_provided"}

    #deduplicate queried skills while preserving order
    cleaned_skill_names = []
    seen_inputs = set()
    for s in skill_names:
        s_lower = s.lower()
        if s_lower not in seen_inputs:
            seen_inputs.add(s_lower)
            cleaned_skill_names.append(s)

    #execute query to get matched skills and related courses
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

    #track which queried skills were recognized in the KG
    recognized_skills = set()

    #merge duplicate courses and collect matched skills
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
            #merge tracks
            for t in (r.get("tracks") or []):
                if t and t not in seen_courses[cc]["tracks"]:
                    seen_courses[cc]["tracks"].append(t)

            #merge matched skills
            if r.get("matched_skill") and r["matched_skill"] not in seen_courses[cc]["matched_skills"]:
                seen_courses[cc]["matched_skills"].append(r["matched_skill"])

    #build final results list
    results = list(seen_courses.values())

    #rank results by number of matched skills descending, then by course code ascending
    results.sort(key=lambda x: (-len(x["matched_skills"]), x["course_code"]))

    #identify unrecognized queried skills
    unrecognized_skills = [
        s for s in cleaned_skill_names
        if s.lower() not in recognized_skills
    ]

    #return result
    return {
        "queried_skills": cleaned_skill_names,
        "unrecognized_skills": unrecognized_skills,
        "results": results [:10],
        "total_results": len(results),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# B1 — Career Role Exploration (Curriculum-aware)
# ═══════════════════════════════════════════════════════════════════════════════

# Tier metadata — derived from weight thresholds (NOT stored in graph)
def _weight_to_tier(weight: float) -> str:
    if weight >= 0.8:
        return "core"
    elif weight >= 0.6:
        return "supporting"
    else:
        return "optional"


def q_get_role_profile(client, role_id: str) -> dict:
    """Returns the profile of a role including required skills."""
    #empty string or None or whitespace
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}

    #normalize role id
    rid = str(role_id).strip()

    #execute one query to get role info and required skills
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

    #role not found
    if not rows:
        return {"error": "role_not_found", "submitted_role_id": rid}

    #process result
    row = rows[0]
    domain = row.get("domain")
    domains = [domain] if domain else []

    raw_skills = [s for s in (row.get("required_skills") or []) if s]

    #sort skills by weight descending, then name ascending
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

    #return result
    return {
        "role_id": rid,
        "role_name": row["role_name"],
        "domains": domains,
        "required_skills": required_skills,
        "total_required_skills": len(required_skills),
    }

# ── OP2: Get Roles by Track ──────────────────────────────────────────────────

def q_get_roles_by_track(client, track_id: str) -> dict:
    """
    Returns roles reachable through the Track → Course → Skill → Role path.
    Results are ordered alphabetically by role_name.
    """
    #empty string or None or whitespace
    if not track_id or not str(track_id).strip():
        return {"error": "no_track_provided"}

    #normalize track id
    tid = str(track_id).strip()

    #execute one query to get track info and reachable roles
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

    #track not found
    if not rows:
        return {"error": "track_not_found", "submitted_track_id": tid}

    #process result
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

    #sort alphabetically by role name
    results.sort(key=lambda x: x["role_name"] or "")

    #return result
    return {
        "track": row["track_name"],
        "track_id": tid,
        "results": results,
        "total_results": len(results),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# B2 — Skill Gap & Alignment
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_courses(client, course_codes: list):
    """
    Given a list of course_codes, return (valid_codes, unrecognized_codes).
    """
    #empty input
    if not course_codes:
        return [], []

    #execute query to resolve existing course codes
    rows = client.execute_query(
        "UNWIND $codes AS code MATCH (c:Course {course_code: code}) RETURN c.course_code AS course_code",
        {"codes": course_codes}
    )

    #split into valid and unrecognized
    found = {r["course_code"] for r in rows}
    valid = [c for c in course_codes if c in found]
    unrecognized = [c for c in course_codes if c not in found]

    return valid, unrecognized

def _get_role_skills(client, role_id: str):
    """Returns list of {skill_id, name, category, weight} for a role."""
    #execute query to get all required skills of the role
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
    """Returns set of skill_ids taught by the given courses."""
    #empty input
    if not course_codes:
        return set()

    #execute query to get distinct taught skills
    rows = client.execute_query(
        """
        UNWIND $codes AS code
        MATCH (c:Course {course_code: code})-[:TEACHES]->(s:Skill)
        RETURN DISTINCT s.skill_id AS skill_id
        """,
        {"codes": course_codes}
    )

    #return as set for fast membership checks
    return {r["skill_id"] for r in rows}

def _get_skills_with_courses(client, course_codes: list) -> dict:
    """Returns {skill_id: [course_codes that teach it]} for covered skills."""
    #empty input
    if not course_codes:
        return {}

    #execute query to map each skill to the courses that teach it
    rows = client.execute_query(
        """
        UNWIND $codes AS code
        MATCH (c:Course {course_code: code})-[:TEACHES]->(s:Skill)
        RETURN s.skill_id AS skill_id, collect(DISTINCT code) AS covered_by
        """,
        {"codes": course_codes}
    )

    #return mapping
    return {r["skill_id"]: r["covered_by"] for r in rows}

# ── OP1: Compute Skill Gap ───────────────────────────────────────────────────

def q_compute_skill_gap(client, role_id: str, completed_courses: list) -> dict:
    """Computes covered and missing skills for a role given completed courses."""
    #empty string or None or whitespace for role id
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}

    #normalize role id
    rid = str(role_id).strip()

    #no courses provided
    if not completed_courses:
        return {"error": "no_courses_provided"}

    #deduplicate completed courses while preserving order and removing empty values
    cleaned_courses = []
    seen_courses = set()
    for c in completed_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_courses:
                seen_courses.add(code)
                cleaned_courses.append(code)

    #all provided values were empty/invalid strings
    if not cleaned_courses:
        return {"error": "no_courses_provided"}

    #check role existence
    exists = client.execute_query(
        "MATCH (r:Role {role_id: $rid}) RETURN r.name AS name",
        {"rid": rid}
    )

    #role not found
    if not exists:
        return {"error": "role_not_found", "submitted_role_id": rid}

    #get role name
    role_name = exists[0]["name"]

    #get required role skills
    role_skills = _get_role_skills(client, rid)

    #role has no required skills mapped
    if not role_skills:
        return {"error": "role_has_no_required_skills", "role_id": rid}

    #resolve valid and unrecognized courses
    valid_courses, unrecognized = _resolve_courses(client, cleaned_courses)

    #no valid courses found
    if not valid_courses:
        return {"error": "no_valid_courses_provided", "unrecognized_courses": unrecognized}

    #get covered skills with the courses that cover them
    skill_coverage = _get_skills_with_courses(client, valid_courses)

    #split role skills into covered and missing
    covered, missing = [], []
    for sk in role_skills:
        sid = sk["skill_id"]
        tier = _weight_to_tier(sk["weight"])

        if sid in skill_coverage:
            covered.append({
                "skill_id": sid,
                "name": sk["name"],
                "category": sk["category"],
                "tier": tier,
                "weight": sk["weight"],
                "covered_by": skill_coverage[sid],
            })
        else:
            missing.append({
                "skill_id": sid,
                "name": sk["name"],
                "category": sk["category"],
                "tier": tier,
                "weight": sk["weight"],
            })

    #return result
    result = {
        "role_id": rid,
        "role_name": role_name,
        "covered_skills": covered,
        "missing_skills": missing,
        "total_covered": len(covered),
        "total_missing": len(missing),
        "total_required": len(role_skills),
    }

    #include unrecognized courses if any
    if unrecognized:
        result["unrecognized_courses"] = unrecognized

    return result

# ── OP2: Compute Alignment Score ────────────────────────────────────────────

def q_compute_alignment_score(client, role_id: str, completed_courses: list) -> dict:
    """Computes the weighted alignment score [0,1]."""
    #empty string or None or whitespace for role id
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}

    #normalize role id
    rid = str(role_id).strip()

    #no courses provided
    if not completed_courses:
        return {"error": "no_courses_provided"}

    #deduplicate completed courses while preserving order and removing empty values
    cleaned_courses = []
    seen_courses = set()
    for c in completed_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_courses:
                seen_courses.add(code)
                cleaned_courses.append(code)

    #all provided values were empty/invalid strings
    if not cleaned_courses:
        return {"error": "no_courses_provided"}

    #check role existence
    exists = client.execute_query(
        "MATCH (r:Role {role_id: $rid}) RETURN r.name AS name",
        {"rid": rid}
    )

    #role not found
    if not exists:
        return {"error": "role_not_found", "submitted_role_id": rid}

    #get role name
    role_name = exists[0]["name"]

    #get required role skills
    role_skills = _get_role_skills(client, rid)

    #role has no required skills mapped
    if not role_skills:
        return {"error": "role_has_no_required_skills", "role_id": rid}

    #resolve valid and unrecognized courses
    valid_courses, unrecognized = _resolve_courses(client, cleaned_courses)

    #no valid courses found
    if not valid_courses:
        return {"error": "no_valid_courses_provided", "unrecognized_courses": unrecognized}

    #get covered skill ids from valid completed courses
    covered_ids = _get_skills_from_courses(client, valid_courses)

    #compute total and covered weight
    total_weight = sum(sk["weight"] for sk in role_skills)
    covered_weight = sum(sk["weight"] for sk in role_skills if sk["skill_id"] in covered_ids)

    #compute normalized alignment score
    score = round(covered_weight / total_weight, 4) if total_weight > 0 else 0.0

    #return result
    result = {
        "role_id": rid,
        "role_name": role_name,
        "alignment_score": score,
        "alignment_percentage": round(score * 100, 2),
        "covered_weight": round(covered_weight, 4),
        "total_weight": round(total_weight, 4),
    }

    #include unrecognized courses if any
    if unrecognized:
        result["unrecognized_courses"] = unrecognized

    return result
# ── OP3: Recommend Courses to Close Gap ─────────────────────────────────────

def q_recommend_courses_to_close_gap(client, role_id: str, completed_courses: list) -> dict:
    
    """Recommends courses that teach missing skills, excluding already completed."""
    
    #empty string or None or whitespace for role id
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}

    #normalize role id
    rid = str(role_id).strip()

    #no courses provided
    if not completed_courses:
        return {"error": "no_courses_provided"}

    #deduplicate completed courses while preserving order and removing empty values
    cleaned_courses = []
    seen_courses = set()
    for c in completed_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_courses:
                seen_courses.add(code)
                cleaned_courses.append(code)

    #all provided values were empty/invalid strings
    if not cleaned_courses:
        return {"error": "no_courses_provided"}

    #check role existence
    exists = client.execute_query(
        "MATCH (r:Role {role_id: $rid}) RETURN r.name AS name",
        {"rid": rid}
    )

    #role not found
    if not exists:
        return {"error": "role_not_found", "submitted_role_id": rid}

    #get role name
    role_name = exists[0]["name"]

    #get required role skills
    role_skills = _get_role_skills(client, rid)

    #role has no required skills mapped
    if not role_skills:
        return {"error": "role_has_no_required_skills", "role_id": rid}

    #resolve valid and unrecognized courses
    valid_courses, unrecognized = _resolve_courses(client, cleaned_courses)

    #no valid courses found
    if not valid_courses:
        return {"error": "no_valid_courses_provided", "unrecognized_courses": unrecognized}

    #get already covered skill ids from completed courses
    covered_ids = _get_skills_from_courses(client, valid_courses)

    #identify missing role skills only
    missing_skills = [sk for sk in role_skills if sk["skill_id"] not in covered_ids]

    #no missing skills -> no recommendations needed
    if not missing_skills:
        result = {
            "role_id": rid,
            "role_name": role_name,
            "missing_skills": [],
            "total_missing_skills": 0,
            "total_recommended_courses": 0,
        }
        if unrecognized:
            result["unrecognized_courses"] = unrecognized
        return result

    #for each missing skill, find courses that teach it excluding already completed courses
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

    #build course-level details per missing skill
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

    #get track info for all recommended courses
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
                       WHEN t IS NOT NULL THEN {
                           track_id: t.track_id,
                           name: t.name
                       }
                   END) AS tracks
            """,
            {"codes": all_rec_codes}
        )
        track_map = {
            r["code"]: sorted(
                [t for t in (r.get("tracks") or []) if t],
                key=lambda x: (x.get("name") or "", x.get("track_id") or "")
            )
            for r in track_rows
        }

    #attach track info and build final missing-skill output
    missing_out = []
    all_recommended = set()

    for sk in missing_skills:
        sid = sk["skill_id"]
        taught_by = sid_to_teaching.get(sid, [])

        for c in taught_by:
            c["tracks"] = track_map.get(c["course_code"], [])
            all_recommended.add(c["course_code"])

        #sort recommended courses for stable output
        taught_by.sort(key=lambda x: x["course_code"])

        missing_out.append({
            "skill_id": sid,
            "name": sk["name"],
            "tier": _weight_to_tier(sk["weight"]),
            "weight": sk["weight"],
            "taught_by": taught_by,
        })

    #return result
    result = {
        "role_id": rid,
        "role_name": role_name,
        "missing_skills": missing_out,
        "total_missing_skills": len(missing_out),
        "total_recommended_courses": len(all_recommended),
    }

    #include unrecognized courses if any
    if unrecognized:
        result["unrecognized_courses"] = unrecognized

    return result

# ── OP4: Estimate Alignment Improvement ─────────────────────────────────────

def q_estimate_alignment_improvement(
    client,
    role_id: str,
    completed_courses: list,
    planned_courses: list
) -> dict:
    """Estimates how much planned courses improve alignment score."""
    #empty string or None or whitespace for role id
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}

    #normalize role id
    rid = str(role_id).strip()

    #no completed courses provided
    if not completed_courses:
        return {"error": "no_courses_provided"}

    #no planned courses provided
    if not planned_courses:
        return {"error": "no_planned_courses_provided"}

    #deduplicate completed courses while preserving order and removing empty values
    cleaned_completed = []
    seen_completed = set()
    for c in completed_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_completed:
                seen_completed.add(code)
                cleaned_completed.append(code)

    #deduplicate planned courses while preserving order and removing empty values
    cleaned_planned = []
    seen_planned = set()
    for c in planned_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_planned:
                seen_planned.add(code)
                cleaned_planned.append(code)

    #all completed values were empty/invalid
    if not cleaned_completed:
        return {"error": "no_courses_provided"}

    #all planned values were empty/invalid
    if not cleaned_planned:
        return {"error": "no_planned_courses_provided"}

    #check role existence
    exists = client.execute_query(
        "MATCH (r:Role {role_id: $rid}) RETURN r.name AS name",
        {"rid": rid}
    )

    #role not found
    if not exists:
        return {"error": "role_not_found", "submitted_role_id": rid}

    #get role name
    role_name = exists[0]["name"]

    #get required role skills
    role_skills = _get_role_skills(client, rid)

    #role has no required skills mapped
    if not role_skills:
        return {"error": "role_has_no_required_skills", "role_id": rid}

    #resolve valid and unrecognized completed courses
    valid_completed, unrecognized_c = _resolve_courses(client, cleaned_completed)
    if not valid_completed:
        return {"error": "no_valid_courses_provided", "unrecognized_courses": unrecognized_c}

    #resolve valid and unrecognized planned courses
    valid_planned, unrecognized_p = _resolve_courses(client, cleaned_planned)
    if not valid_planned:
        return {
            "error": "no_valid_planned_courses_provided",
            "unrecognized_planned_courses": unrecognized_p
        }

    #remove overlap: planned courses already completed should not be counted again
    valid_planned = [c for c in valid_planned if c not in set(valid_completed)]

    #if no valid planned courses remain after removing overlap
    if not valid_planned:
        result = {
            "role_id": rid,
            "role_name": role_name,
            "current_alignment_score": 0.0,
            "current_alignment_percentage": 0.0,
            "projected_alignment_score": 0.0,
            "projected_alignment_percentage": 0.0,
            "alignment_improvement": 0.0,
            "newly_covered_skills": [],
            "still_missing_skills": [],
            "total_newly_covered": 0,
            "total_still_missing": 0,
            "warning": "no_new_valid_planned_courses_after_deduplication"
        }
        if unrecognized_c:
            result["unrecognized_courses"] = unrecognized_c
        if unrecognized_p:
            result["unrecognized_planned_courses"] = unrecognized_p
        return result

    #compute total role weight
    total_weight = sum(sk["weight"] for sk in role_skills)

    #get currently covered and projected covered skill ids
    current_covered = _get_skills_from_courses(client, valid_completed)
    projected_covered = _get_skills_from_courses(client, valid_completed + valid_planned)

    #compute current and projected covered weights
    current_weight = sum(sk["weight"] for sk in role_skills if sk["skill_id"] in current_covered)
    projected_weight = sum(sk["weight"] for sk in role_skills if sk["skill_id"] in projected_covered)

    #compute normalized scores
    current_score = round(current_weight / total_weight, 4) if total_weight else 0.0
    projected_score = round(projected_weight / total_weight, 4) if total_weight else 0.0
    improvement = round(projected_score - current_score, 4)

    #identify skills newly covered by planned courses only
    new_skill_ids = projected_covered - current_covered

    #get all planned-course coverage for newly covered skills in one query
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
        planned_coverage_map = {
            r["skill_id"]: sorted(r.get("courses") or [])
            for r in coverage_rows
        }

    #build newly covered skills list in role weight order
    newly_covered = []
    for sk in role_skills:
        if sk["skill_id"] in new_skill_ids:
            newly_covered.append({
                "skill_id": sk["skill_id"],
                "name": sk["name"],
                "tier": _weight_to_tier(sk["weight"]),
                "weight": sk["weight"],
                "covered_by_planned": planned_coverage_map.get(sk["skill_id"], []),
            })

    #build still missing skills list
    still_missing_ids = {sk["skill_id"] for sk in role_skills} - projected_covered
    still_missing = [
        {
            "skill_id": sk["skill_id"],
            "name": sk["name"],
            "tier": _weight_to_tier(sk["weight"]),
            "weight": sk["weight"],
        }
        for sk in role_skills
        if sk["skill_id"] in still_missing_ids
    ]

    #return result
    result = {
        "role_id": rid,
        "role_name": role_name,
        "current_alignment_score": current_score,
        "current_alignment_percentage": round(current_score * 100, 2),
        "projected_alignment_score": projected_score,
        "projected_alignment_percentage": round(projected_score * 100, 2),
        "alignment_improvement": improvement,
        "newly_covered_skills": newly_covered,
        "still_missing_skills": still_missing,
        "total_newly_covered": len(newly_covered),
        "total_still_missing": len(still_missing),
    }

    #include unrecognized inputs if any
    if unrecognized_c:
        result["unrecognized_courses"] = unrecognized_c
    if unrecognized_p:
        result["unrecognized_planned_courses"] = unrecognized_p

    return result


# ── OP5: Find Best-Matching Roles ────────────────────────────────────────────

def q_find_best_matching_roles(client, completed_courses: list) -> dict:
    """Ranks all roles by alignment score for the given completed courses."""
    #no courses provided
    if not completed_courses:
        return {"error": "no_courses_provided"}

    #deduplicate completed courses while preserving order and removing empty values
    cleaned_courses = []
    seen_courses = set()
    for c in completed_courses:
        if c and str(c).strip():
            code = str(c).strip()
            if code not in seen_courses:
                seen_courses.add(code)
                cleaned_courses.append(code)

    #all provided values were empty/invalid strings
    if not cleaned_courses:
        return {"error": "no_courses_provided"}

    #resolve valid and unrecognized courses
    valid_courses, unrecognized = _resolve_courses(client, cleaned_courses)

    #no valid courses found
    if not valid_courses:
        return {"error": "no_valid_courses_provided", "unrecognized_courses": unrecognized}

    #get all skill ids covered by the completed courses
    covered_ids = _get_skills_from_courses(client, valid_courses)

    #get all roles and their required skills in one query
    rows = client.execute_query(
        """
        MATCH (r:Role)
        OPTIONAL MATCH (r)-[rel:REQUIRES]->(s:Skill)
        RETURN
            r.role_id AS role_id,
            r.name AS role_name,
            r.domain AS domain,
            collect(DISTINCT CASE
                WHEN s IS NOT NULL THEN {
                    skill_id: s.skill_id,
                    weight: rel.weight
                }
            END) AS role_skills
        """,
        {}
    )

    #no roles in graph
    if not rows:
        return {"error": "no_roles_in_graph"}

    ranked = []
    total_roles_evaluated = len(rows)

    #compute alignment score for each role
    for row in rows:
        role_skills = [sk for sk in (row.get("role_skills") or []) if sk]

        #skip roles with no required skills
        if not role_skills:
            continue

        total_weight = sum(sk["weight"] for sk in role_skills)
        covered_weight = sum(
            sk["weight"] for sk in role_skills
            if sk["skill_id"] in covered_ids
        )

        score = round(covered_weight / total_weight, 4) if total_weight else 0.0

        #include only roles with positive alignment
        if score > 0.0:
            ranked.append({
                "role_id": row["role_id"],
                "role_name": row["role_name"],
                "domains": [row["domain"]] if row.get("domain") else [],
                "alignment_score": score,
                "alignment_percentage": round(score * 100, 2),
            })

    #sort descending by score, break ties alphabetically by role_name
    ranked.sort(key=lambda x: (-x["alignment_score"], x["role_name"]))

    #assign rank numbers
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i

    #return result
    result = {
        "completed_courses": valid_courses,
        "ranked_roles": ranked,
        "total_roles_evaluated": total_roles_evaluated,
    }

    #include unrecognized courses if any
    if unrecognized:
        result["unrecognized_courses"] = unrecognized

    return result

# ═══════════════════════════════════════════════════════════════════════════════
# B3 — Track Guidance & Comparison
# ═══════════════════════════════════════════════════════════════════════════════

def _track_alignment_score(track_skill_ids: set, role_skills: list) -> float:
    """Compute alignment score using pre-fetched track skill ids."""
    total_weight = sum(sk["weight"] for sk in role_skills)
    covered_weight = sum(
        sk["weight"] for sk in role_skills
        if sk["skill_id"] in track_skill_ids
    )
    return round(covered_weight / total_weight, 4) if total_weight else 0.0


def _get_track_skills(client, track_id: str) -> list:
    """Deduplicated list of skills taught by all courses in a track."""
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
    """Courses belonging to a track."""
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
            "course_code":       r["course_code"],
            "name":              r["name"],
            "credits":           r["credits"],
            "level":             r["level"],
            "semester_offering": sem_list,
        })
    return result


def _get_track_supported_roles(client, track_id: str) -> list:
    """Roles where track alignment score > 0.0."""

    #get all skills taught by this track once
    rows = client.execute_query(
        """
        MATCH (t:Track {track_id: $tid})<-[:BELONGS_TO]-(c:Course)-[:TEACHES]->(s:Skill)
        RETURN DISTINCT s.skill_id AS skill_id
        """,
        {"tid": track_id}
    )
    track_skill_ids = {r["skill_id"] for r in rows}

    #get all roles with their required skills in one query
    role_rows = client.execute_query(
        """
        MATCH (r:Role)
        OPTIONAL MATCH (r)-[rel:REQUIRES]->(s:Skill)
        RETURN
            r.role_id AS role_id,
            r.name AS role_name,
            r.domain AS domain,
            collect(DISTINCT CASE
                WHEN s IS NOT NULL THEN {
                    skill_id: s.skill_id,
                    weight: rel.weight
                }
            END) AS role_skills
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
                "role_id": row["role_id"],
                "role_name": row["role_name"],
                "domains": [row["domain"]] if row.get("domain") else [],
            })

    return supported


# ── OP1: Get Track Overview ──────────────────────────────────────────────────

def q_get_track_overview(client, track_id: str) -> dict:
    if not track_id or not str(track_id).strip():
        return {"error": "no_track_provided"}

    tid = str(track_id).strip()
    exists = client.execute_query(
        "MATCH (t:Track {track_id: $tid}) RETURN t.name AS name",
        {"tid": tid}
    )
    if not exists:
        return {"error": "track_not_found", "submitted_track_id": tid}

    track_name = exists[0]["name"]

    courses        = _get_track_courses(client, tid)
    skills_taught  = _get_track_skills(client, tid)
    supported_roles = _get_track_supported_roles(client, tid)

    return {
        "track_id":              tid,
        "track_name":            track_name,
        "courses":               courses,
        "total_courses":         len(courses),
        "skills_taught":         skills_taught,
        "total_skills":          len(skills_taught),
        "supported_roles":       supported_roles,
        "total_supported_roles": len(supported_roles),
    }


# ── OP2: Compare Tracks ──────────────────────────────────────────────────────

def q_compare_tracks(client, track_id_1: str, track_id_2: str) -> dict:
    #missing track ids
    if not track_id_1 or not track_id_2:
        return {"error": "missing_track_ids"}

    #normalize track ids
    tid1 = str(track_id_1).strip()
    tid2 = str(track_id_2).strip()

    #missing track ids after normalization
    if not tid1 or not tid2:
        return {"error": "missing_track_ids"}

    #same track provided twice
    if tid1 == tid2:
        return {"error": "identical_tracks_provided", "track_id": tid1}

    #get both track names
    t1 = client.execute_query(
        "MATCH (t:Track {track_id: $tid}) RETURN t.name AS name",
        {"tid": tid1}
    )
    t2 = client.execute_query(
        "MATCH (t:Track {track_id: $tid}) RETURN t.name AS name",
        {"tid": tid2}
    )

    #collect not found tracks
    not_found = []
    if not t1:
        not_found.append(tid1)
    if not t2:
        not_found.append(tid2)

    #one or both tracks not found
    if not_found:
        return {"error": "track_not_found", "not_found_ids": not_found}

    #extract track names
    t1_name = t1[0]["name"]
    t2_name = t2[0]["name"]

    #get track courses
    c1_list = _get_track_courses(client, tid1)
    c2_list = _get_track_courses(client, tid2)

    c1_map = {c["course_code"]: c for c in c1_list}
    c2_map = {c["course_code"]: c for c in c2_list}
    shared_c_codes = set(c1_map) & set(c2_map)

    #build course comparison
    courses = {
        "track_1_only": [c1_map[k] for k in sorted(c1_map) if k not in shared_c_codes],
        "track_2_only": [c2_map[k] for k in sorted(c2_map) if k not in shared_c_codes],
        "shared": [c1_map[k] for k in sorted(shared_c_codes)],
        "total_track_1_only": len(c1_map) - len(shared_c_codes),
        "total_track_2_only": len(c2_map) - len(shared_c_codes),
        "total_shared": len(shared_c_codes),
    }

    #get track skills
    s1_list = _get_track_skills(client, tid1)
    s2_list = _get_track_skills(client, tid2)

    s1_map = {s["skill_id"]: s for s in s1_list}
    s2_map = {s["skill_id"]: s for s in s2_list}
    shared_s_ids = set(s1_map) & set(s2_map)

    #build skills comparison
    skills = {
        "track_1_only": [s1_map[k] for k in sorted(s1_map) if k not in shared_s_ids],
        "track_2_only": [s2_map[k] for k in sorted(s2_map) if k not in shared_s_ids],
        "shared": [s1_map[k] for k in sorted(shared_s_ids)],
        "total_track_1_only": len(s1_map) - len(shared_s_ids),
        "total_track_2_only": len(s2_map) - len(shared_s_ids),
        "total_shared": len(shared_s_ids),
    }

    #get both track skill id sets once for role alignment comparison
    t1_skill_ids = set(s1_map.keys())
    t2_skill_ids = set(s2_map.keys())

    #get all roles with required skills in one query
    role_rows = client.execute_query(
        """
        MATCH (r:Role)
        OPTIONAL MATCH (r)-[rel:REQUIRES]->(s:Skill)
        RETURN
            r.role_id AS role_id,
            r.name AS role_name,
            r.domain AS domain,
            collect(DISTINCT CASE
                WHEN s IS NOT NULL THEN {
                    skill_id: s.skill_id,
                    weight: rel.weight
                }
            END) AS role_skills
        """,
        {}
    )

    t1_role_ids, t2_role_ids = set(), set()
    role_map = {}

    #compute role support for both tracks
    for row in role_rows:
        role_skills = [sk for sk in (row.get("role_skills") or []) if sk]
        if not role_skills:
            continue

        sc1 = _track_alignment_score(t1_skill_ids, role_skills)
        sc2 = _track_alignment_score(t2_skill_ids, role_skills)

        rid = row["role_id"]
        role_map[rid] = {
            "role_id": rid,
            "role_name": row["role_name"],
            "domains": [row["domain"]] if row.get("domain") else [],
            "track_1_score": sc1,
            "track_2_score": sc2,
        }

        if sc1 > 0.0:
            t1_role_ids.add(rid)
        if sc2 > 0.0:
            t2_role_ids.add(rid)

    shared_role_ids = t1_role_ids & t2_role_ids

    #helper to format role entry
    def _role_entry(rid):
        rr = role_map[rid]
        return {
            "role_id": rr["role_id"],
            "role_name": rr["role_name"],
            "domains": rr["domains"],
        }

    #build role alignment comparison
    role_alignment = {
        "track_1_only": [_role_entry(r) for r in sorted(t1_role_ids - shared_role_ids)],
        "track_2_only": [_role_entry(r) for r in sorted(t2_role_ids - shared_role_ids)],
        "shared": [_role_entry(r) for r in sorted(shared_role_ids)],
        "total_track_1_only": len(t1_role_ids - shared_role_ids),
        "total_track_2_only": len(t2_role_ids - shared_role_ids),
        "total_shared": len(shared_role_ids),
    }

    #return result
    return {
        "track_1": {"track_id": tid1, "track_name": t1_name},
        "track_2": {"track_id": tid2, "track_name": t2_name},
        "courses": courses,
        "skills": skills,
        "role_alignment": role_alignment,
    }

# ── OP3: Recommend Track for a Role ─────────────────────────────────────────

def q_recommend_track_for_role(client, role_id: str) -> dict:
    #empty string or None or whitespace for role id
    if not role_id or not str(role_id).strip():
        return {"error": "no_role_provided"}

    #normalize role id
    rid = str(role_id).strip()

    #check role existence
    exists = client.execute_query(
        "MATCH (r:Role {role_id: $rid}) RETURN r.name AS name",
        {"rid": rid}
    )

    #role not found
    if not exists:
        return {"error": "role_not_found", "submitted_role_id": rid}

    #get role name
    role_name = exists[0]["name"]

    #get role skills
    role_skills = _get_role_skills(client, rid)

    #role has no required skills mapped
    if not role_skills:
        return {"error": "role_has_no_required_skills", "role_id": rid}

    #get all tracks
    all_tracks = client.execute_query(
        "MATCH (t:Track) RETURN t.track_id AS track_id, t.name AS name"
    )

    #no tracks in graph
    if not all_tracks:
        return {"error": "no_tracks_in_graph"}

    #get all track-skill pairs once
    track_skill_rows = client.execute_query(
        """
        MATCH (t:Track)<-[:BELONGS_TO]-(c:Course)-[:TEACHES]->(s:Skill)
        RETURN t.track_id AS track_id, collect(DISTINCT s.skill_id) AS skill_ids
        """,
        {}
    )

    #build track -> skill ids mapping
    track_skill_map = {
        r["track_id"]: set(r.get("skill_ids") or [])
        for r in track_skill_rows
    }

    #compute total role weight once
    total_weight = sum(sk["weight"] for sk in role_skills)

    ranked = []

    #rank tracks by alignment to the role
    for tr in all_tracks:
        tid = tr["track_id"]
        track_skill_ids = track_skill_map.get(tid, set())

        score = _track_alignment_score(track_skill_ids, role_skills)

        if score > 0.0:
            covered_weight = sum(
                sk["weight"] for sk in role_skills
                if sk["skill_id"] in track_skill_ids
            )

            ranked.append({
                "track_id": tid,
                "track_name": tr["name"],
                "alignment_score": score,
                "alignment_percentage": round(score * 100, 2),
                "covered_weight": round(covered_weight, 4),
                "total_weight": round(total_weight, 4),
            })

    #sort descending by score, break ties alphabetically by track name
    ranked.sort(key=lambda x: (-x["alignment_score"], x["track_name"]))

    #assign ranks
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i

    #return result
    return {
        "role_id": rid,
        "role_name": role_name,
        "ranked_tracks": ranked,
        "total_tracks_evaluated": len(all_tracks),
    }


# ── OP4: Recommend Track for a Skill ────────────────────────────────────────

def q_recommend_track_for_skill(client, skill_id: str) -> dict:
    #empty string or None or whitespace for skill id
    if not skill_id or not str(skill_id).strip():
        return {"error": "no_skill_provided"}

    #normalize skill id
    sid = str(skill_id).strip()

    #check skill existence
    exists = client.execute_query(
        "MATCH (s:Skill {skill_id: $sid}) RETURN s.name AS name",
        {"sid": sid}
    )

    #skill not found
    if not exists:
        return {"error": "skill_not_found", "submitted_skill_id": sid}

    #get skill name
    skill_name = exists[0]["name"]

    #get all tracks
    all_tracks = client.execute_query(
        "MATCH (t:Track) RETURN t.track_id AS track_id, t.name AS name"
    )

    #no tracks in graph
    if not all_tracks:
        return {"error": "no_tracks_in_graph"}

    #get all tracks and their courses teaching this skill in one query
    rows = client.execute_query(
        """
        MATCH (t:Track)<-[:BELONGS_TO]-(c:Course)-[:TEACHES]->(s:Skill {skill_id: $sid})
        RETURN
            t.track_id AS track_id,
            t.name AS track_name,
            collect(DISTINCT {
                course_code: c.course_code,
                name: c.name
            }) AS courses_teaching_skill
        """,
        {"sid": sid}
    )

    ranked = []

    #build ranked track list
    for r in rows:
        courses = sorted(
            r.get("courses_teaching_skill") or [],
            key=lambda x: x["course_code"]
        )

        if courses:
            ranked.append({
                "track_id": r["track_id"],
                "track_name": r["track_name"],
                "courses_teaching_skill": courses,
                "course_count": len(courses),
            })

    #sort descending by course count, break ties alphabetically by track name
    ranked.sort(key=lambda x: (-x["course_count"], x["track_name"]))

    #assign ranks
    for i, item in enumerate(ranked, start=1):
        item["rank"] = i

    #return result
    return {
        "skill_id": sid,
        "skill_name": skill_name,
        "ranked_tracks": ranked,
        "total_tracks_evaluated": len(all_tracks),
    }
