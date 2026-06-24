// ============================================================
// PathFinder KG-Engine — Verification Queries
// Run after load.cypher to confirm data integrity.
//
// Expected counts (derived from audited CSV files):
//   Course                  59
//   Track                    5
//   Skill                   52
//   Role                    20
//   PrerequisiteConstraint   2
//   PREREQ                  47
//   HAS_PREREQ_CONSTRAINT    6
//   BELONGS_TO              61
//   TEACHES                101
//   REQUIRES               180
// ============================================================


// ── 1. Node Counts per Label ─────────────────────────────────
MATCH (c:Course)                 RETURN 'Course'                 AS label, count(c) AS count
UNION ALL
MATCH (t:Track)                  RETURN 'Track'                  AS label, count(t) AS count
UNION ALL
MATCH (s:Skill)                  RETURN 'Skill'                  AS label, count(s) AS count
UNION ALL
MATCH (r:Role)                   RETURN 'Role'                   AS label, count(r) AS count
UNION ALL
MATCH (pc:PrerequisiteConstraint) RETURN 'PrerequisiteConstraint' AS label, count(pc) AS count;


// ── 2. Relationship Counts per Type ──────────────────────────
MATCH ()-[r:PREREQ]->()              RETURN 'PREREQ'                AS type, count(r) AS count
UNION ALL
MATCH ()-[r:HAS_PREREQ_CONSTRAINT]->() RETURN 'HAS_PREREQ_CONSTRAINT' AS type, count(r) AS count
UNION ALL
MATCH ()-[r:BELONGS_TO]->()          RETURN 'BELONGS_TO'            AS type, count(r) AS count
UNION ALL
MATCH ()-[r:TEACHES]->()             RETURN 'TEACHES'               AS type, count(r) AS count
UNION ALL
MATCH ()-[r:REQUIRES]->()            RETURN 'REQUIRES'              AS type, count(r) AS count;


// ── 3. Count Assertions — every row must show PASS ───────────
MATCH (c:Course) WITH count(c) AS actual
RETURN 'Course count'                AS check, actual, 59  AS expected,
       CASE WHEN actual = 59  THEN 'PASS' ELSE 'FAIL' END AS status
UNION ALL
MATCH (t:Track) WITH count(t) AS actual
RETURN 'Track count'                 AS check, actual, 5   AS expected,
       CASE WHEN actual = 5   THEN 'PASS' ELSE 'FAIL' END AS status
UNION ALL
MATCH (s:Skill) WITH count(s) AS actual
RETURN 'Skill count'                 AS check, actual, 52  AS expected,
       CASE WHEN actual = 52  THEN 'PASS' ELSE 'FAIL' END AS status
UNION ALL
MATCH (r:Role) WITH count(r) AS actual
RETURN 'Role count'                  AS check, actual, 20  AS expected,
       CASE WHEN actual = 20  THEN 'PASS' ELSE 'FAIL' END AS status
UNION ALL
MATCH (pc:PrerequisiteConstraint) WITH count(pc) AS actual
RETURN 'PrerequisiteConstraint count' AS check, actual, 2  AS expected,
       CASE WHEN actual = 2   THEN 'PASS' ELSE 'FAIL' END AS status
UNION ALL
MATCH ()-[r:PREREQ]->() WITH count(r) AS actual
RETURN 'PREREQ count'                AS check, actual, 47  AS expected,
       CASE WHEN actual = 47  THEN 'PASS' ELSE 'FAIL' END AS status
UNION ALL
MATCH ()-[r:HAS_PREREQ_CONSTRAINT]->() WITH count(r) AS actual
RETURN 'HAS_PREREQ_CONSTRAINT count' AS check, actual, 6   AS expected,
       CASE WHEN actual = 6   THEN 'PASS' ELSE 'FAIL' END AS status
UNION ALL
MATCH ()-[r:BELONGS_TO]->() WITH count(r) AS actual
RETURN 'BELONGS_TO count'            AS check, actual, 61  AS expected,
       CASE WHEN actual = 61  THEN 'PASS' ELSE 'FAIL' END AS status
UNION ALL
MATCH ()-[r:TEACHES]->() WITH count(r) AS actual
RETURN 'TEACHES count'               AS check, actual, 101 AS expected,
       CASE WHEN actual = 101 THEN 'PASS' ELSE 'FAIL' END AS status
UNION ALL
MATCH ()-[r:REQUIRES]->() WITH count(r) AS actual
RETURN 'REQUIRES count'              AS check, actual, 180 AS expected,
       CASE WHEN actual = 180 THEN 'PASS' ELSE 'FAIL' END AS status;


// ── 4. Schema-Change Assertions — every row must show PASS ───
// Removed entities must be absent; new entities must exist.
OPTIONAL MATCH (s:Skill {skill_id: 'SK_Reinforcement_Learning'})
RETURN 'SK_Reinforcement_Learning absent' AS check,
       CASE WHEN s IS NULL THEN 'PASS' ELSE 'FAIL — still in graph' END AS status
UNION ALL
OPTIONAL MATCH (r:Role {role_id: 'RL_IT_Manager'})
RETURN 'RL_IT_Manager absent' AS check,
       CASE WHEN r IS NULL THEN 'PASS' ELSE 'FAIL — still in graph' END AS status
UNION ALL
OPTIONAL MATCH (r:Role {role_id: 'RL_MLOps_Engineer'})
RETURN 'RL_MLOps_Engineer exists' AS check,
       CASE WHEN r IS NOT NULL THEN 'PASS' ELSE 'FAIL — missing' END AS status
UNION ALL
OPTIONAL MATCH (r:Role {role_id: 'RL_Cloud_Engineer'})
RETURN 'RL_Cloud_Engineer exists' AS check,
       CASE WHEN r IS NOT NULL THEN 'PASS' ELSE 'FAIL — missing' END AS status
UNION ALL
OPTIONAL MATCH (r:Role {role_id: 'RL_QA_Engineer'})
RETURN 'RL_QA_Engineer exists' AS check,
       CASE WHEN r IS NOT NULL THEN 'PASS' ELSE 'FAIL — missing' END AS status
UNION ALL
OPTIONAL MATCH (s:Skill {skill_id: 'SK_CPP'})
RETURN 'SK_CPP exists' AS check,
       CASE WHEN s IS NOT NULL THEN 'PASS' ELSE 'FAIL — missing' END AS status
UNION ALL
OPTIONAL MATCH (s:Skill {skill_id: 'SK_OOP'})
RETURN 'SK_OOP exists' AS check,
       CASE WHEN s IS NOT NULL THEN 'PASS' ELSE 'FAIL — missing' END AS status
UNION ALL
OPTIONAL MATCH (s:Skill {skill_id: 'SK_Data_Structures'})
RETURN 'SK_Data_Structures exists' AS check,
       CASE WHEN s IS NOT NULL THEN 'PASS' ELSE 'FAIL — missing' END AS status
UNION ALL
OPTIONAL MATCH (s:Skill {skill_id: 'SK_AI_Algorithms'})
RETURN 'SK_AI_Algorithms exists' AS check,
       CASE WHEN s IS NOT NULL THEN 'PASS' ELSE 'FAIL — missing' END AS status
UNION ALL
OPTIONAL MATCH (s:Skill {skill_id: 'SK_Distributed_Systems'})
RETURN 'SK_Distributed_Systems exists' AS check,
       CASE WHEN s IS NOT NULL THEN 'PASS' ELSE 'FAIL — missing' END AS status
UNION ALL
OPTIONAL MATCH (s:Skill {skill_id: 'SK_Version_Control'})
RETURN 'SK_Version_Control exists' AS check,
       CASE WHEN s IS NOT NULL THEN 'PASS' ELSE 'FAIL — missing' END AS status;


// ── 5. Duplicate Check — every query must return 0 rows ──────
MATCH (c:Course)
WITH c.course_code AS code, count(*) AS cnt WHERE cnt > 1
RETURN 'Duplicate Course' AS issue, code, cnt;

MATCH (t:Track)
WITH t.track_id AS id, count(*) AS cnt WHERE cnt > 1
RETURN 'Duplicate Track' AS issue, id, cnt;

MATCH (s:Skill)
WITH s.skill_id AS id, count(*) AS cnt WHERE cnt > 1
RETURN 'Duplicate Skill' AS issue, id, cnt;

MATCH (r:Role)
WITH r.role_id AS id, count(*) AS cnt WHERE cnt > 1
RETURN 'Duplicate Role' AS issue, id, cnt;


// ── 6. Dangling-Reference Checks — every query must return 0 rows
// Courses referenced in PREREQ that don't exist as Course nodes
MATCH (c:Course)-[:PREREQ]->(p)
WHERE NOT p:Course
RETURN 'Dangling PREREQ target' AS issue, c.course_code AS source, p AS target;

// Course nodes missing from any track (expected: 0 — every course has BELONGS_TO)
MATCH (c:Course)
WHERE NOT (c)-[:BELONGS_TO]->(:Track)
RETURN 'Course with no track' AS issue, c.course_code AS course_code;

// Skills taught but with no incoming TEACHES from any course
MATCH (s:Skill)
WHERE NOT ()-[:TEACHES]->(s)
RETURN 'Skill never taught' AS issue, s.skill_id;

// Roles with no REQUIRES edges
MATCH (r:Role)
WHERE NOT (r)-[:REQUIRES]->()
RETURN 'Role with no skills' AS issue, r.role_id;


// ── 7. Spot-check Samples ────────────────────────────────────
// Prerequisites of C-AI422 — expect [C-CS215, C-ST211]
MATCH (c:Course {course_code: 'C-AI422'})-[:PREREQ]->(p:Course)
RETURN c.course_code AS course, collect(p.course_code) AS direct_prerequisites;

// Skills taught by C-AI321
MATCH (c:Course {course_code: 'C-AI321'})-[:TEACHES]->(s:Skill)
RETURN c.course_code AS course, collect(s.name) AS skills_taught;

// Skills required by RL_Data_Scientist
MATCH (r:Role {role_id: 'RL_Data_Scientist'})-[rel:REQUIRES]->(s:Skill)
RETURN r.role_id AS role, s.name AS skill, rel.weight AS weight
ORDER BY rel.weight DESC;

// Courses in the AI track
MATCH (c:Course)-[:BELONGS_TO]->(t:Track {track_id: 'AI'})
RETURN t.name AS track, collect(c.course_code) AS courses;

// Full prerequisite chain for C-AI422
MATCH path = (c:Course {course_code: 'C-AI422'})-[:PREREQ*]->(p:Course)
RETURN [node IN nodes(path) | node.course_code] AS prerequisite_chain
ORDER BY length(path);

// Skills required by new role RL_MLOps_Engineer
MATCH (r:Role {role_id: 'RL_MLOps_Engineer'})-[rel:REQUIRES]->(s:Skill)
RETURN r.role_id AS role, s.name AS skill, rel.weight AS weight
ORDER BY rel.weight DESC;
