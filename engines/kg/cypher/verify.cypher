// ============================================================
// PathFinder KG-Engine — Verification Queries
// Run after load.cypher to confirm data integrity
// ============================================================

// ── 1. Node Counts per Label ─────────────────────────────────
MATCH (c:Course) RETURN 'Course' AS label, count(c) AS count
UNION ALL
MATCH (t:Track)  RETURN 'Track'  AS label, count(t) AS count
UNION ALL
MATCH (s:Skill)  RETURN 'Skill'  AS label, count(s) AS count
UNION ALL
MATCH (r:Role)   RETURN 'Role'   AS label, count(r) AS count;

// ── 2. Relationship Counts per Type ──────────────────────────
MATCH ()-[r:PREREQ]->()    RETURN 'PREREQ'     AS type, count(r) AS count
UNION ALL
MATCH ()-[r:BELONGS_TO]->() RETURN 'BELONGS_TO' AS type, count(r) AS count
UNION ALL
MATCH ()-[r:TEACHES]->()   RETURN 'TEACHES'    AS type, count(r) AS count
UNION ALL
MATCH ()-[r:REQUIRES]->()  RETURN 'REQUIRES'   AS type, count(r) AS count;

// ── 3. Duplicate Check — should return 0 for each ────────────
MATCH (c:Course)
WITH c.course_code AS code, count(*) AS cnt
WHERE cnt > 1
RETURN 'Duplicate Course' AS issue, code, cnt;

MATCH (t:Track)
WITH t.track_id AS id, count(*) AS cnt
WHERE cnt > 1
RETURN 'Duplicate Track' AS issue, id, cnt;

MATCH (s:Skill)
WITH s.skill_id AS id, count(*) AS cnt
WHERE cnt > 1
RETURN 'Duplicate Skill' AS issue, id, cnt;

MATCH (r:Role)
WITH r.role_id AS id, count(*) AS cnt
WHERE cnt > 1
RETURN 'Duplicate Role' AS issue, id, cnt;

// ── 4. Sample: Prerequisites of C-AI422 ─────────────────────
MATCH (c:Course {course_code: 'C-AI422'})-[:PREREQ]->(p:Course)
RETURN c.course_code AS course, collect(p.course_code) AS direct_prerequisites;

// ── 5. Sample: Skills taught by C-AI321 ─────────────────────
MATCH (c:Course {course_code: 'C-AI321'})-[:TEACHES]->(s:Skill)
RETURN c.course_code AS course, collect(s.name) AS skills_taught;

// ── 6. Sample: Skills required by RL_Data_Scientist ──────────
MATCH (r:Role {role_id: 'RL_Data_Scientist'})-[rel:REQUIRES]->(s:Skill)
RETURN r.role_id AS role, s.name AS skill, rel.weight AS weight
ORDER BY rel.weight DESC;

// ── 7. Sample: All courses in the AI track ───────────────────
MATCH (c:Course)-[:BELONGS_TO]->(t:Track {track_id: 'AI'})
RETURN t.name AS track, collect(c.course_code) AS courses;

// ── 8. Full Prerequisite Chain for C-AI422 (recursive) ───────
MATCH path = (c:Course {course_code: 'C-AI422'})-[:PREREQ*]->(p:Course)
RETURN [node IN nodes(path) | node.course_code] AS prerequisite_chain
ORDER BY length(path);
