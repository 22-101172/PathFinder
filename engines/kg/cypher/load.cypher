// ============================================================
// PathFinder KG-Engine — Data Load Script
// Run this in Neo4j Browser or cypher-shell
// CSVs must be placed in Neo4j's import directory
// ============================================================

// ── 1. Uniqueness Constraints ────────────────────────────────
CREATE CONSTRAINT course_code_unique IF NOT EXISTS
  FOR (c:Course) REQUIRE c.course_code IS UNIQUE;

CREATE CONSTRAINT track_id_unique IF NOT EXISTS
  FOR (t:Track) REQUIRE t.track_id IS UNIQUE;

CREATE CONSTRAINT skill_id_unique IF NOT EXISTS
  FOR (s:Skill) REQUIRE s.skill_id IS UNIQUE;

CREATE CONSTRAINT role_id_unique IF NOT EXISTS
  FOR (r:Role) REQUIRE r.role_id IS UNIQUE;

CREATE CONSTRAINT prereq_constraint_id_unique IF NOT EXISTS
  FOR (pc:PrerequisiteConstraint) REQUIRE pc.constraint_id IS UNIQUE;


// ── 2. Load Track Nodes ──────────────────────────────────────
LOAD CSV WITH HEADERS FROM 'file:///tracks.csv' AS row
MERGE (t:Track {track_id: row.track_id})
  SET t.name = row.name;

// ── 3. Load Course Nodes ─────────────────────────────────────
LOAD CSV WITH HEADERS FROM 'file:///courses.csv' AS row
MERGE (c:Course {course_code: row.course_code})
  SET c.name             = row.name,
      c.credits          = toInteger(row.credits),
      c.level            = toInteger(row.level),
      c.semester_offering = row.semester_offering,
      c.description      = CASE WHEN trim(row.description) = '' THEN null ELSE row.description END;

// ── 4. Load Skill Nodes ──────────────────────────────────────
LOAD CSV WITH HEADERS FROM 'file:///skills.csv' AS row
MERGE (s:Skill {skill_id: row.skill_id})
  SET s.name     = row.name,
      s.category = row.category;

// ── 5. Load Role Nodes ───────────────────────────────────────
LOAD CSV WITH HEADERS FROM 'file:///roles.csv' AS row
MERGE (r:Role {role_id: row.role_id})
  SET r.name   = row.name,
      r.domain = row.domain;

// ── 6. Load PREREQ Relationships (COURSE type) ──────────
LOAD CSV WITH HEADERS FROM 'file:///prerequisites.csv' AS row
WITH row 
WHERE row.prereq_type = 'COURSE'
MATCH (c:Course {course_code: row.course_code})
MATCH (p:Course {course_code: row.prereq_course_code})
MERGE (c)-[:PREREQ]->(p);

// ── Load non-course prerequisites (e.g. CREDIT_THRESHOLD) ──
LOAD CSV WITH HEADERS FROM 'file:///prerequisites.csv' AS row
WITH row
WHERE row.prereq_type <> 'COURSE'
  AND row.prereq_value IS NOT NULL
  AND trim(row.prereq_value) <> ''
MATCH (c:Course {course_code: row.course_code})
MERGE (pc:PrerequisiteConstraint {
  constraint_id: row.prereq_type + '::' + trim(row.prereq_value)
})
SET pc.type = row.prereq_type,
    pc.value = trim(row.prereq_value)
MERGE (c)-[:HAS_PREREQ_CONSTRAINT]->(pc);

// ── 7. Load BELONGS_TO Relationships ─────────────────────────
LOAD CSV WITH HEADERS FROM 'file:///course_track.csv' AS row
MATCH (c:Course {course_code: row.course_code})
MATCH (t:Track  {track_id: row.track_id})
MERGE (c)-[:BELONGS_TO]->(t);

// ── 8. Load TEACHES Relationships ────────────────────────────
LOAD CSV WITH HEADERS FROM 'file:///course_skill.csv' AS row
MATCH (c:Course {course_code: row.course_code})
MATCH (s:Skill  {skill_id: row.skill_id})
MERGE (c)-[:TEACHES]->(s);

// ── 9. Load REQUIRES Relationships ───────────────────────────
LOAD CSV WITH HEADERS FROM 'file:///role_skill.csv' AS row
MATCH (r:Role  {role_id: row.role_id})
MATCH (s:Skill {skill_id: row.skill_id})
MERGE (r)-[rel:REQUIRES]->(s)
  SET rel.weight = toFloat(row.weight);
