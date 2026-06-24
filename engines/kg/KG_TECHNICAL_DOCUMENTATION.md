# PathFinder KG Technical Documentation

---

## 1. Component Summary

The Knowledge Graph (KG) engine is PathFinder's curriculum, career, skill, and track fact layer. It stores and exposes structured graph data about courses, prerequisites, skills taught, career roles, role-skill requirements, and program tracks.

KG returns raw structured facts only. It does not make academic advising decisions.

**What KG owns:**
- Course catalogue and course profiles
- Prerequisite relationships (direct and recursive)
- Skills taught by courses
- Role profiles and role-skill requirements
- Track overviews, course-track membership, and track comparisons
- Curriculum-skill alignment calculations (role vs. completed courses)
- Entity resolution from natural language to canonical graph IDs

**What KG explicitly does not own:**

| Decision | Owner |
|---|---|
| Course eligibility (prerequisites met, credit limits) | ALE |
| Graduation status and academic warnings | ALE |
| Semester planning and course selection | ALE |
| GPA projections | ALE |
| Retake legality | ALE |
| Intent classification and entity type inference | QU |
| Routing, enrichment, planned-course source selection | Orchestrator |
| Final student-facing wording and display limits | Composer |
| Student transcript records | SCP / Session Manager |

KG alignment scores measure curriculum-skill coverage only. They do not imply employability, hiring readiness, or graduation eligibility.

---

## 2. Files and Responsibilities

| File | Responsibility |
|---|---|
| `engines/kg/neo4j_client.py` | Neo4j connection management, retry logic, query execution |
| `engines/kg/queries.py` | All 18 KG operation functions and their Cypher queries |
| `adapters/kg_adapter.py` | Orchestrator-facing adapter with `call(operation, params)` dispatch |
| `engines/kg/data/entity_aliases.json` | Manually maintained alias table for the entity resolver |
| `engines/kg/data/courses.csv` | Source course data |
| `engines/kg/data/skills.csv` | Source skill data |
| `engines/kg/data/roles.csv` | Source role data |
| `engines/kg/data/tracks.csv` | Source track data |
| `engines/kg/data/prerequisites.csv` | Course prerequisite relationships |
| `engines/kg/data/course_skill.csv` | Course-to-skill (TEACHES) mappings |
| `engines/kg/data/role_skill.csv` | Role-to-skill (REQUIRES) mappings with weights |
| `engines/kg/data/course_track.csv` | Course-to-track (BELONGS_TO) mappings |
| `engines/kg/Original Data source/Course Catalogue_Correct Version.xlsx` | Original course catalogue reference |
| `engines/kg/cypher/load.cypher` | Loads graph from CSV files |
| `engines/kg/cypher/reset.cypher` | Clears and reinitializes the graph |
| `engines/kg/cypher/verify.cypher` | Count assertions and integrity checks |
| `engines/kg/tests/test_queries_operations.py` | Direct live operation tests (OP1–OP18) |
| `engines/kg/tests/test_kg_adapter.py` | KGAdapter dispatch, error, and boundary tests |
| `tests/test_kg_adapter_logging.py` | Caplog tests for adapter logging (no live Neo4j required) |

---

## 3. Data Model

### 3.1 Locked Graph Counts

Verified counts from `engines/kg/cypher/verify.cypher` after full data load:

```
Nodes:
  Course:                  59
  Track:                    5
  Skill:                   52
  Role:                    20
  PrerequisiteConstraint:   2

Relationships:
  PREREQ:                  47
  HAS_PREREQ_CONSTRAINT:    6
  BELONGS_TO:              61
  TEACHES:                101
  REQUIRES:               180
```

### 3.2 Node Types

#### Course

| Property | Description |
|---|---|
| `course_code` | Canonical course identifier (e.g., `C-CS219`, `HUM110`) |
| `name` | Human-readable course name |
| `credits` | Credit hours |
| `level` | Course level (numeric) |
| `semester_offering` | Comma-separated offering semesters (e.g., `"Fall, Spring"`) |
| `description` | Course description (optional) |

#### Track

| Property | Description |
|---|---|
| `track_id` | Canonical track identifier |
| `name` | Track display name |

**Canonical track IDs:**

| Track ID | Display Name |
|---|---|
| `AI` | Artificial Intelligence |
| `CYS` | Cybersecurity |
| `DSE` | Data Science and Engineering |
| `SWE` | Software Engineering |
| `GEN` | General |

Old informal values (`Cyber`, `Data Science`, `SW`, `CS`, `General`) must be normalized to canonical IDs before any KG call. QU, SCP, and Orchestrator own this normalization.

#### Skill

| Property | Description |
|---|---|
| `skill_id` | Canonical skill identifier (e.g., `SK_OOP`, `SK_Data_Structures`) |
| `name` | Human-readable skill name |
| `category` | Skill category (e.g., Programming, Data) |

#### Role

| Property | Description |
|---|---|
| `role_id` | Canonical role identifier (e.g., `RL_Data_Scientist`, `RL_ML_Engineer`) |
| `name` | Human-readable role name |
| `domain` | Role domain (e.g., AI, Data, SWE, CYS) |

#### PrerequisiteConstraint

| Property | Description |
|---|---|
| `type` | Constraint type (currently only `CREDIT_THRESHOLD` is implemented) |
| `value` | Free-text value (e.g., `"Passing 59 Credit Hours"`) |

### 3.3 Relationships

| Relationship | Pattern | Notes |
|---|---|---|
| `PREREQ` | `(Course)-[:PREREQ]->(Course)` | Direct course prerequisite |
| `HAS_PREREQ_CONSTRAINT` | `(Course)-[:HAS_PREREQ_CONSTRAINT]->(PrerequisiteConstraint)` | Non-course prerequisites (credit thresholds) |
| `BELONGS_TO` | `(Course)-[:BELONGS_TO]->(Track)` | Track membership (a course may belong to multiple tracks) |
| `TEACHES` | `(Course)-[:TEACHES]->(Skill)` | Skills taught by a course |
| `REQUIRES` | `(Role)-[:REQUIRES {weight}]->(Skill)` | Skills required by a role, with numeric weight (0–1) |

The `REQUIRES.weight` drives alignment calculations. Weight tiers: `core` (≥ 0.8), `supporting` (≥ 0.6), `optional` (< 0.6).

---

## 4. Data Source and Loading

### 4.1 Graph Data Source

KG data comes from curated CSV files and the course catalogue reference:

- `courses.csv`, `skills.csv`, `roles.csv`, `tracks.csv` — canonical node data
- `prerequisites.csv`, `course_skill.csv`, `role_skill.csv`, `course_track.csv` — relationship data
- CSV files are the authoritative source; `entity_aliases.json` is usability support only and is not canonical graph data

### 4.2 Cypher Scripts

| Script | Purpose |
|---|---|
| `engines/kg/cypher/load.cypher` | Loads all nodes and relationships from CSV files |
| `engines/kg/cypher/reset.cypher` | Clears the graph (deletes all nodes and relationships) and reinitializes |
| `engines/kg/cypher/verify.cypher` | Runs count assertions, schema-change checks, duplicate checks, dangling-reference checks, and spot samples |

**Run order:** `reset.cypher` → `load.cypher` → `verify.cypher`

**Note:** These scripts are executed in Neo4j Browser or via `cypher-shell`. Commands depend on local Neo4j setup. Refer to README or local Neo4j documentation for the exact invocation.

### 4.3 Verification

After loading, run `verify.cypher`. Every count assertion must return `PASS`. Duplicate and dangling-reference queries must return 0 rows. See `verify.cypher` Section 4 for specific schema-change assertions (e.g., removed `SK_Reinforcement_Learning`, added `RL_MLOps_Engineer`, `RL_Cloud_Engineer`, `RL_QA_Engineer`).

---

## 5. Neo4j Connection Layer

**File:** `engines/kg/neo4j_client.py` — class `Neo4jClient`

### 5.1 Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | Database connection URI |
| `NEO4J_USER` | `neo4j` | Authentication username |
| `NEO4J_PASSWORD` | *(required)* | Raises `EnvironmentError` if missing |
| `NEO4J_DATABASE` | `neo4j` | Target database name |

Missing `NEO4J_PASSWORD` raises a clear `EnvironmentError` at `__init__` time with instructions to copy `.env.example` to `.env`.

### 5.2 Connection Behavior

- `connect()` is idempotent — calling it when already connected is a no-op.
- Retries up to **3 attempts** with a 2-second sleep between each.
- Uses `driver.verify_connectivity()` after each attempt.
- If all 3 attempts fail, raises `RuntimeError` with a clear message.
- Supports context manager protocol (`__enter__` / `__exit__`).

### 5.3 Query Execution

`execute_query(query, params)`:
- Opens a new session using `NEO4J_DATABASE`.
- Runs the query with parameters.
- Returns `list[dict]` — one dict per record.
- Does not raise for empty results; returns `[]`.
- Raises `RuntimeError` if called before `connect()`.

### 5.4 Logging

`Neo4jClient` logs:
- Each connection attempt with URI and attempt number (INFO)
- Successful connection (INFO)
- Each failed attempt with reason (WARNING)
- Final failure after all retries (ERROR)
- Connection close (INFO)

Query-level operation logging belongs to `KGAdapter`, not `Neo4jClient`.

---

## 6. KG Operation Inventory

All 18 operations are callable via `KGAdapter.call(operation, params)`. Function names are the stable runtime contract. OP numbers are documentation/grouping labels only — do not use them in code.

| OP | Adapter operation name | Query function | Group | Main inputs | Main output |
|---|---|---|---|---|---|
| 1 | `get_course_profile` | `q_get_course_profile` | A1 | `course_code` | Course metadata, tracks, credit threshold |
| 2 | `get_prerequisites` | `q_get_prerequisites` | A1 | `course_code`, `depth` | Direct prereqs, non-course prereqs, optional full tree |
| 3 | `get_skills_taught` | `q_get_skills_taught` | A1 | `course_code` | List of skills taught |
| 4 | `search_courses_by_skill` | `q_search_courses_by_skill` | A1 | `skill_ids` (list) | Courses teaching any of the given skills |
| 5 | `get_role_profile` | `q_get_role_profile` | A2 | `role_id` | Role metadata and required skills with weights/tiers |
| 6 | `get_roles_by_track` | `q_get_roles_by_track` | A2 | `track_id` | Roles reachable via Track→Course→Skill→Role path |
| 7 | `compute_skill_gap` | `q_compute_skill_gap` | A3 | `role_id`, `completed_courses` | Covered and missing skills for a role |
| 8 | `compute_alignment_score` | `q_compute_alignment_score` | A3 | `role_id`, `completed_courses` | Weighted alignment score [0,1] and percentage |
| 9 | `recommend_courses_to_close_gap` | `q_recommend_courses_to_close_gap` | A3 | `role_id`, `completed_courses` | Courses teaching missing skills, excluding completed |
| 10 | `estimate_alignment_improvement` | `q_estimate_alignment_improvement` | A3 | `role_id`, `completed_courses`, `planned_courses` | Current vs projected alignment, newly covered skills |
| 11 | `find_best_matching_roles` | `q_find_best_matching_roles` | A3 | `completed_courses` | All roles ranked by alignment score |
| 12 | `get_track_overview` | `q_get_track_overview` | A4 | `track_id` | Track courses, skills taught, supported roles |
| 13 | `compare_tracks` | `q_compare_tracks` | A4 | `track_id_1`, `track_id_2` | Course/skill/role overlap and differences between two tracks |
| 14 | `recommend_track_for_role` | `q_recommend_track_for_role` | A4 | `role_id` | Tracks ranked by alignment to role's required skills |
| 15 | `recommend_track_for_skill` | `q_recommend_track_for_skill` | A4 | `skill_id` | Tracks that teach the given skill, ranked by course count |
| 16 | `get_courses_by_track` | `q_get_courses_by_track` | A5 | `track_id` | All track courses with prereqs and full planning metadata |
| 17 | `get_focus_courses_for_target` | `q_get_focus_courses_for_target` | A6 | `target_id`, `target_type`, `completed_courses` | Uncompleted courses ranked by relevance to a role or track |
| 18 | `resolve_entity` | `q_resolve_entity` | A7 | `entity_type`, `entity_text` | Canonical graph ID and resolution status for a natural-language input |

---

## 7. Operation Details

### OP1 — `get_course_profile`

Retrieves full course metadata: code, name, credits, level, semester offerings, track memberships, description, and credit threshold.

- Validates course code format against `[A-Za-z][A-Za-z0-9\-]*` regex before querying.
- Returns `credit_threshold` (integer hours) extracted from any linked `PrerequisiteConstraint` of type `CREDIT_THRESHOLD`.
- Only `CREDIT_THRESHOLD` constraint type is handled; new types would need explicit branches added.
- The current graph has 2 `PrerequisiteConstraint` nodes; OP1 only surfaces the first if multiple constraints exist (latent issue, non-blocking unless new constraint types are added).
- Business errors: `invalid_course_code`, `course_not_found`.

### OP2 — `get_prerequisites`

Returns direct prerequisites and non-course prerequisites (credit thresholds). Optionally builds a full recursive prerequisite tree.

- `depth="direct"` → returns `direct_prerequisites` and `non_course_prerequisites`; `full_prerequisite_tree=[]`.
- `depth="full"` → additionally builds the recursive tree via `_build_prereq_tree()`.
- Circular dependency detection: if a cycle is detected during recursion, returns `error=circular_dependency_detected`.
- QU decides `direct` vs `full` based on query wording (e.g., "all prerequisites" → `full`).
- Full recursion is acceptable at current graph size; future optimization is possible.
- Business errors: `invalid_course_code`, `invalid_depth_value`, `course_not_found`.

### OP3 — `get_skills_taught`

Returns all skills taught by a course, sorted alphabetically by name, with `total_skills` count.

- Courses with no `TEACHES` relationships return a success result with `skills_taught=[]` and `total_skills=0`.
- Composer must handle empty skills gracefully (e.g., "no skills are mapped to this course in the current data").
- Business errors: `invalid_course_code`, `course_not_found`.

### OP4 — `search_courses_by_skill`

Given a list of skill IDs, returns all courses teaching any of them.

- Accepts `skill_ids` (list of canonical `SK_*` IDs), not raw skill name strings.
- Exact ID matching — no normalization or fuzzy matching.
- Returns `matched_skill_ids` (IDs) and `matched_skills` (readable skill objects with name and category) per course.
- Results sorted by number of matched skills descending, then by course code.
- `unrecognized_skill_ids` lists any IDs that returned no match in the graph.
- No result count cap; Composer handles display limits.
- Business errors: `no_skill_ids_provided`.

### OP5 — `get_role_profile`

Returns role metadata, its domain, and its required skills with weights and tiers.

- Skills are sorted by weight descending, then by name.
- Tier classification: `core` (weight ≥ 0.8), `supporting` (weight ≥ 0.6), `optional` (weight < 0.6).
- Business errors: `no_role_provided`, `role_not_found`.

### OP6 — `get_roles_by_track`

Returns roles reachable through the Track→Course→Skill→Role path.

- This is a graph traversal result, not an official "roles for this track" catalog.
- Composer must phrase results as "connected roles" or "related roles," not guaranteed career outcomes.
- Business errors: `no_track_provided`, `track_not_found`.

### OP7 — `compute_skill_gap`

Computes which skills required by a role are covered or missing given a set of completed courses.

- Requires non-empty `completed_courses`; returns `no_courses_provided` if empty (MVP behavior).
- Unrecognized course codes are reported in `unrecognized_courses` but do not block the call if valid courses remain.
- Returns `covered_skills`, `missing_skills`, `total_covered`, `total_missing`, `total_required`.
- Results are curriculum-skill alignment only, not employability guarantees.
- Business errors: `no_role_provided`, `no_courses_provided`, `role_not_found`, `role_has_no_required_skills`, `no_valid_courses_provided`.

### OP8 — `compute_alignment_score`

Computes the weighted alignment score ([0,1]) between a role's required skills and completed courses.

- Uses `_compute_alignment_metrics()` — single source of truth for weighted calculations.
- Returns `alignment_score` (0–1), `alignment_percentage`, `covered_weight`, `total_weight`.
- Same empty-courses MVP behavior as OP7.
- Business errors: same as OP7.

### OP9 — `recommend_courses_to_close_gap`

Recommends courses that teach missing skills, excluding already completed courses.

- Returns a per-missing-skill list, each with `taught_by` courses that are not in `completed_courses`.
- If all skills are already covered, returns `missing_skills=[]` and `total_missing_skills=0` (not an error).
- Output is a fact list, not a registration plan. Composer must not narrate as eligibility advice.
- Business errors: same as OP7.

### OP10 — `estimate_alignment_improvement`

Estimates the alignment score change if `planned_courses` are added to `completed_courses`.

- Both `completed_courses` and `planned_courses` must be non-empty.
- Planned courses already in `completed_courses` are deduplicated and reported in `ignored_planned_courses`.
- Returns current alignment, projected alignment, improvement delta, newly covered skills, and still-missing skills.
- `planned_courses` must be resolved by the Orchestrator before calling OP10. OP10 is a pure KG calculation.
- Orchestrator planned-course source priority: explicit query courses → session plan/roadmap → session planned assumptions → in-progress fallback. If in-progress fallback is used, Composer must state that assumption.
- Business errors: `no_role_provided`, `no_courses_provided`, `no_planned_courses_provided`, `role_not_found`, `role_has_no_required_skills`, `no_valid_courses_provided`, `no_valid_planned_courses_provided`.

### OP11 — `find_best_matching_roles`

Ranks all roles in the graph by weighted alignment score for the given completed courses.

- Only roles with `alignment_score > 0.0` are included in results.
- Roles with no `REQUIRES` edges are skipped silently.
- Results sorted by score descending, then by role name; each item includes a `rank` field.
- Same empty-courses MVP behavior as OP7.
- Business errors: `no_courses_provided`, `no_valid_courses_provided`, `no_roles_in_graph`.

### OP12 — `get_track_overview`

Returns a track's courses, all skills taught by those courses, and all roles with any alignment to the track's skills.

- Combines `_get_track_courses()`, `_get_track_skills()`, and `_get_track_supported_roles()`.
- Supported roles are roles with `alignment_score > 0.0` through the track skill set.
- Business errors: `no_track_provided`, `track_not_found`.

### OP13 — `compare_tracks`

Compares two distinct tracks side by side: course overlap/differences, skill overlap/differences, role alignment.

- Requires two distinct, valid track IDs; returns `identical_tracks_provided` if both are the same.
- Reports `track_1_only`, `track_2_only`, `shared` for courses, skills, and roles.
- Roles are classified per track based on alignment score > 0; a role appearing in both → shared.
- QU must always provide exactly two tracks for `compare_tracks`. If user mentions 3+, QU returns `clarification_needed`.
- Business errors: `missing_track_ids`, `identical_tracks_provided`, `track_not_found`.

### OP14 — `recommend_track_for_role`

Ranks all tracks by how well their course-taught skills cover a role's required skills.

- Uses `_track_alignment_score()` — same weighted formula as OP8 but for track-vs-role.
- Only tracks with alignment > 0.0 appear in results.
- Composer must frame as "curriculum-skill fit" not personal academic advice.
- Business errors: `no_role_provided`, `role_not_found`, `role_has_no_required_skills`, `no_tracks_in_graph`.

### OP15 — `recommend_track_for_skill`

Returns tracks that teach a specific skill, ranked by how many courses in that track teach it.

- Returns the courses within each track that teach the skill.
- Useful for "which track is best if I want to learn X?"
- Business errors: `no_skill_provided`, `skill_not_found`, `no_tracks_in_graph`.

### OP16 — `get_courses_by_track`

Returns all courses in a track with full planning metadata: code, name, credits, level, direct prerequisites (as course code list), semester offerings, and credit threshold.

- Ordered by `level` then `course_code`.
- Used by the Orchestrator to supply ALE with `available_courses` for semester planning and graduation roadmap generation.
- Does not decide eligibility or build semester plans — ALE owns those calculations.
- Required/elective split is not represented in current KG data; do not invent labels.
- Business errors: `no_track_provided`, `track_not_found`.

### OP17 — `get_focus_courses_for_target`

Given a role or track and a list of completed courses, returns uncompleted courses ranked by how many target-relevant skills they teach.

- `target_type` must be `"track"` or `"role"`.
- Empty `completed_courses` is valid (e.g., freshmen with no completed courses see all target courses).
- Courses are sorted by `relevant_skill_count` descending, then by course level ascending.
- Does not check eligibility, prerequisites, or credit limits. Composer must not narrate as eligibility advice.
- In-progress course exclusion: OP17 currently only excludes `completed_courses`. Whether to also exclude in-progress courses depends on query wording — this is an Orchestrator/QU decision for later integration.
- Business errors: `no_target_provided`, `invalid_target_type`, `track_not_found`, `role_not_found`.

### OP18 — `resolve_entity`

Resolves a natural-language entity reference to a canonical graph ID.

**Supported `entity_type` values:** `"course"`, `"role"`, `"track"`, `"skill"`

**Resolution pipeline (priority order):**

| Step | Match type | Confidence |
|---|---|---|
| 1 | Exact ID match (original trimmed text vs. `id_property`) | 1.0 |
| 2 | Exact normalized graph-name match | 1.0 |
| 3 | Alias lookup in `entity_aliases.json` | 0.95 |
| 4 | Explicit ambiguous term (returns `status=ambiguous`) | 0.85 |
| 5 | Partial graph-name match | 0.70 |
| — | Not found | — |

**Output shapes:**

| Status | Meaning |
|---|---|
| `ok` | Single confident match: `resolved_id`, `name`, `match_type`, `confidence` |
| `ambiguous` | Multiple matches: `matches` list (up to 10), `total_matches` |
| `not_found` | No match found |
| `error` | Infrastructure error: `unsupported_entity_type`, `empty_entity_text`, `alias_file_error`, `alias_validation_error` |

**Normalization applied before matching:** lowercase, strip, collapse spaces, replace `_`/`-` with space, remove punctuation, strip entity-type filler words (e.g., "course", "role", "track", "skill").

**Important:** QU must decide entity type before calling the resolver. The resolver does not guess entity type across types. Ambiguous phrases like "Software Engineering" may refer to a track, role, or course domain — QU uses session context or asks for clarification before resolving.

---

## 8. Entity Alias System

**File:** `engines/kg/data/entity_aliases.json`

### 8.1 Structure

The alias file has four top-level sections: `"course"`, `"role"`, `"track"`, `"skill"`. Each section contains:

- `"aliases"`: dict mapping canonical IDs to lists of post-normalized alias strings
- `"ambiguous_terms"`: dict mapping a normalized phrase to a list of candidate IDs (returns `status=ambiguous`)

All alias strings are post-normalization (lowercase, filler words removed, hyphens/underscores replaced with spaces). The alias file is loaded once and cached in `_entity_aliases_cache`.

### 8.2 Coverage Examples

**Course aliases (selected):**
- `C-CS219` → `"advanced programming"`, `"functional programming"`, `"ocaml"`, `"fp"`
- `C-AI321` → `"intro to machine learning"`, `"ml course"`, `"iml"`
- `C-CS213` → `"data structures"`, `"linked lists"`, `"trees"`

**Ambiguous course terms (selected):**
- `"machine learning"` → `[C-AI321, C-AI422]`
- `"database"` → `[C-DE312, C-DE413, C-DE414]`
- `"security"` → `[C-CS442, C-CS443, C-SW423, C-MA425]`
- `"ai"` → 7 AI courses

**Track aliases:**
- `"AI"` → `"ai"`, `"artificial intelligence"`, `"machine learning track"`
- `"DSE"` → `"dse"`, `"data science"`, `"data engineering"`
- `"SWE"` → `"swe"`, `"software engineering"`, `"software development"`
- `"CYS"` → `"cys"`, `"cybersecurity"`, `"cyber"`, `"infosec"`
- `"GEN"` → `"gen"`, and related aliases

**Role aliases (selected):**
- `RL_Data_Scientist` → `"data scientist"`, `"applied data scientist"`, `"data science engineer"`
- `RL_ML_Engineer` → `"ml engineer"`, `"machine learning engineer"`, `"applied ml engineer"`
- `RL_Penetration_Tester` → `"pentester"`, `"ethical hacker"`, `"red teamer"`

**Ambiguous role terms (selected):**
- `"engineer"` → 11 role IDs
- `"security"` → `[RL_Cybersecurity_Analyst, RL_Penetration_Tester, RL_Security_Engineer]`

### 8.3 Alias Maintenance Rules

- Aliases are **manually maintained** for MVP. Do not add LLM-generated aliases without human review.
- Aliases are **not canonical data**; canonical IDs are course codes, role IDs, track IDs, skill IDs.
- Whenever KG data changes (courses, roles, tracks, or skills added/removed/renamed), revalidate all aliases against the loaded graph to ensure no alias points to a removed entity.
- If an alias target is not found in the graph, the resolver returns `error=alias_validation_error`.
- QU preprocessing should normalize loose course-code forms (`cs219`, `c cs 219`) before passing to the resolver.

---

## 9. KGAdapter Contract

**File:** `adapters/kg_adapter.py` — class `KGAdapter`

### 9.1 Overview

`KGAdapter` is a thin adapter between the Orchestrator and the KG engine. It:

- Exposes all 18 KG operations via `call(operation, params)`.
- Creates and connects `Neo4jClient` on `__init__`.
- If Neo4j is unavailable at startup, sets `_client=None` and returns `kg_unavailable` on any subsequent `call()` — does not raise exceptions later.
- Maps operation name strings to adapter methods via an internal dispatch dict.
- Forwards `**params` directly to the corresponding query function.
- Business errors from `queries.py` pass through unchanged; the adapter does not re-wrap them.

### 9.2 Adapter-Level Error Codes

| Error | Condition |
|---|---|
| `kg_unavailable` | `_client` is `None` (Neo4j offline at startup or init failed) |
| `unknown_operation` | Operation name not in dispatch table |
| `bad_params` | `TypeError` from wrong/missing keyword arguments |
| `kg_error` | Unexpected exception during query execution |

These are distinct from query-level business errors (e.g., `course_not_found`, `role_not_found`). The Orchestrator must distinguish adapter errors (infrastructure failure) from business errors (entity/input not found in graph).

### 9.3 Startup Behavior

`KGAdapter.__init__()`:
- Attempts `Neo4jClient()` + `client.connect()`.
- On success: `_client` is set, logs connection.
- On failure: `_client=None`, logs a warning. All subsequent `call()` invocations return `{"error": "kg_unavailable", "detail": "..."}` without crashing.

### 9.4 Logging

The observability patch added structured logging to `call()`. Logger name: `adapters.kg_adapter`.

No output shapes were changed by the logging patch.

---

## 10. Logging and Observability

### 10.1 Neo4jClient Logs

`Neo4jClient` emits INFO/WARNING/ERROR logs for:
- Each connection attempt (URI, attempt number)
- Successful connection
- Each failed attempt with error message
- Final failure after all retries
- Connection close

### 10.2 KGAdapter.call() Logs

`KGAdapter.call()` emits structured `key=value` log lines for:

- **Start:** operation name and safe summarized params
- **Result:** operation name, status (`success` / `business_error` / `adapter_error`), result summary, duration in ms

**Safe param summarization:**
- Known scalar keys (`course_code`, `role_id`, `track_id`, `skill_id`, etc.) are logged verbatim.
- Known list keys (`completed_courses`, `skill_ids`, `planned_courses`) are summarized as `{count, preview[:3]}`.
- Unknown lists: `{count, preview[:5]}`.
- Dicts: `{_type: dict}`.

**Result summarization:**
- Lists of results are logged as counts only.
- Error codes are logged; full detail strings are not.

**Example log lines:**

```text
KGAdapter.call start operation=get_prerequisites params={'course_code': 'C-CS219', 'depth': 'full'}
KGAdapter.call result operation=get_prerequisites status=success summary={'keys': ['course_code', 'name', 'direct_prerequisites', ...], 'counts': {'direct_prerequisites': 1, 'full_prerequisite_tree': 1}} duration_ms=12

KGAdapter.call start operation=get_course_profile params={'course_code': 'C-FAKE999'}
KGAdapter.call result operation=get_course_profile status=business_error summary={'keys': ['error', 'submitted_code'], 'error': 'course_not_found'} duration_ms=5

KGAdapter.call start operation=bad_operation params={}
KGAdapter.call result operation=bad_operation status=adapter_error error=unknown_operation duration_ms=0

KGAdapter.call start operation=compute_skill_gap params={'role_id': 'RL_Data_Scientist', 'completed_courses': {'count': 12, 'preview': ['C-CS111', 'C-CS112', 'C-CS213', '...']}}
KGAdapter.call result operation=compute_skill_gap status=success summary={'keys': ['role_id', 'role_name', 'covered_skills', 'missing_skills', ...], 'counts': {'covered_skills': 7, 'missing_skills': 5}} duration_ms=34
```

**Log safety guarantees:**
- No full student transcript or completed-course lists
- No huge result payloads
- No secrets or passwords
- Only safe params, counts, result keys, compact scalar values, and error codes

---

## 11. Testing Summary

### 11.1 Direct KG Operation Tests

| File | Tests | Result | Scope |
|---|---|---|---|
| `engines/kg/tests/test_queries_operations.py` | 163 | 163 passed ✅ | OP1–OP18 directly via `Neo4jClient` — live Neo4j required |

**Per-operation coverage:**

| OP | Function | Tests |
|---|---|---|
| OP1 | `q_get_course_profile` | 8 |
| OP2 | `q_get_prerequisites` | 9 |
| OP3 | `q_get_skills_taught` | 8 |
| OP4 | `q_search_courses_by_skill` | 6 |
| OP5 | `q_get_role_profile` | 7 |
| OP6 | `q_get_roles_by_track` | 7 |
| OP7 | `q_compute_skill_gap` | 7 |
| OP8 | `q_compute_alignment_score` | 7 |
| OP9 | `q_recommend_courses_to_close_gap` | 8 |
| OP10 | `q_estimate_alignment_improvement` | 7 |
| OP11 | `q_find_best_matching_roles` | 7 |
| OP12 | `q_get_track_overview` | 7 |
| OP13 | `q_compare_tracks` | 5 |
| OP14 | `q_recommend_track_for_role` | 8 |
| OP15 | `q_recommend_track_for_skill` | 9 |
| OP16 | `q_get_courses_by_track` | 8 |
| OP17 | `q_get_focus_courses_for_target` | 10 |
| OP18 | `q_resolve_entity` | 30 |
| **Total** | | **163** |

Runtime: ~5.56s with Neo4j live.

### 11.2 KGAdapter Tests

| File | Tests | Result | Scope |
|---|---|---|---|
| `engines/kg/tests/test_kg_adapter.py` | 83 | 83 passed ✅ | Adapter dispatch, error handling, business error passthrough — live Neo4j required |

Key confirmed behaviors:
- All 18 operation names dispatch correctly.
- `depth="full"` passes through `get_prerequisites` correctly; full prerequisite trees returned.
- Query-level business errors pass through unchanged.
- Adapter errors separated from business errors.

### 11.3 Combined KG Suite

**246 passed, 0 failed** (163 direct + 83 adapter).

### 11.4 Logging Tests

| File | Tests | Result | Scope |
|---|---|---|---|
| `tests/test_kg_adapter_logging.py` | 6 | 6 passed ✅ | Safe caplog behavior — no live Neo4j required (uses `MagicMock`) |

Logging test coverage:
- Start log emitted with operation name
- Success result log with `status=success`
- `unknown_operation` → `status=adapter_error`
- `kg_unavailable` (`_client=None`) → `status=adapter_error`
- `bad_params` (`TypeError`) → `status=adapter_error`
- Business error (`course_not_found`) → `status=business_error`, not `adapter_error`

### 11.5 Compile Check and Latest Run

```powershell
python -m py_compile adapters/kg_adapter.py  # COMPILE OK
pytest tests/test_kg_adapter_logging.py -v   # 6 passed
pytest engines/kg/tests/test_kg_adapter.py -v
    # Neo4j offline: 1 passed, 82 skipped (expected)
    # Neo4j online:  83 passed, 0 failed
```

Full 83 adapter tests and 163 direct tests should be rerun when Neo4j is online after any KG code or data change.

---

## 12. Error Handling

### 12.1 Business Errors (from `queries.py`)

Business errors mean the KG is reachable but the requested entity or input was not valid or not found.

| Error code | Condition |
|---|---|
| `invalid_course_code` | Empty, None, or invalid format course code |
| `course_not_found` | Valid format but course not in graph |
| `invalid_depth_value` | `depth` not in `("direct", "full")` |
| `no_skill_ids_provided` | Empty or all-blank `skill_ids` list |
| `no_role_provided` | Empty or None `role_id` |
| `role_not_found` | Valid `role_id` but not in graph |
| `role_has_no_required_skills` | Role exists but has no `REQUIRES` edges |
| `no_courses_provided` | Empty or all-blank `completed_courses` |
| `no_valid_courses_provided` | All provided course codes unrecognized in graph |
| `no_planned_courses_provided` | Empty or all-blank `planned_courses` |
| `no_valid_planned_courses_provided` | All planned codes unrecognized |
| `no_track_provided` | Empty or None `track_id` |
| `track_not_found` | Valid `track_id` but not in graph |
| `missing_track_ids` | One or both `track_id` args empty/None |
| `identical_tracks_provided` | Both track IDs are the same |
| `no_target_provided` | Empty `target_id` |
| `invalid_target_type` | `target_type` not `"track"` or `"role"` |
| `no_skill_provided` | Empty `skill_id` |
| `skill_not_found` | Skill not in graph |
| `circular_dependency_detected` | Cycle found during recursive prerequisite traversal |
| `no_roles_in_graph` | No `Role` nodes exist |
| `no_tracks_in_graph` | No `Track` nodes exist |

### 12.2 Adapter-Level Errors (from `kg_adapter.py`)

Adapter errors mean infrastructure or dispatch failure:

| Error code | Condition |
|---|---|
| `kg_unavailable` | `_client=None` (Neo4j offline at startup) |
| `unknown_operation` | Operation name not in dispatch table |
| `bad_params` | `TypeError` — wrong or missing keyword arguments |
| `kg_error` | Any other unexpected exception during query execution |

### 12.3 Resolver-Specific Statuses

OP18 uses `status` rather than `error`:

| Status | Meaning |
|---|---|
| `ok` | Single resolved match |
| `ambiguous` | Multiple candidate matches |
| `not_found` | No match found |
| `error` | Infrastructure or input error |

---

## 13. Integration Boundaries

| Component | Role in KG integration |
|---|---|
| **QU** | Resolves entity type; calls OP18 to get canonical IDs; normalizes track IDs to canonical form; decides `depth` for OP2; handles disambiguation and `clarification_needed` |
| **Orchestrator** | Calls `KGAdapter.call()` with resolved canonical IDs; enriches ALE calls with KG data (e.g., OP16 → ALE `available_courses`); resolves planned-course source before OP10 |
| **KGAdapter** | Dispatches all 18 operations; returns structured facts and error dicts; never makes advising decisions |
| **ALE** | Receives KG facts as input; performs deterministic academic calculations (eligibility, semester plan, GPA) |
| **Composer** | Narrates KG facts with safe wording (connected roles not guaranteed careers; curriculum fit not academic advice; no `RL_*` or `SK_*` IDs in student-facing text; display limits are Composer concerns) |
| **RAG** | Unrelated to course catalogue and career graph. RAG owns handbook policy evidence only. |
| **SCP / Session** | Provide student transcript context. KG does not own transcript records. |

**Wording rules for Composer from KG data:**

- `get_roles_by_track`, `get_track_overview`, `compare_tracks` → "connected roles" / "related roles", not guaranteed careers.
- `recommend_track_for_role`, `recommend_track_for_skill` → curriculum-skill fit, not personal academic advice.
- `recommend_courses_to_close_gap`, `get_focus_courses_for_target` → fact list, not a registration plan.
- `estimate_alignment_improvement`, `compute_alignment_score` → alignment measurement, not employability readiness.
- "Top 3" or "brief" display limits are Composer concerns; KG returns complete structured results.

---

## 14. Known Limitations and Carry-Forward Items

| Item | Notes |
|---|---|
| Required/elective split | Current KG data does not label courses as required or elective within a track. Do not invent labels. Record as a data limitation. |
| OP1 constraint handling | Only `CREDIT_THRESHOLD` constraint type is parsed. If new `PrerequisiteConstraint` types are added (e.g., `MIN_GPA`, `CO_REQ`), add explicit branches. |
| Multiple constraints per course | OP1 returns only the first constraint if multiple exist (latent issue, non-blocking at current graph size). |
| OP2 full prerequisite recursion | Recursive DB calls are acceptable at current graph size. Future optimization possible if graph grows significantly. |
| Empty `completed_courses` for OP7–OP11 | MVP: returns `no_courses_provided`. Future: freshmen should see full missing-skill lists (OP7), 0% alignment (OP8), and relevant messaging (OP11). |
| OP17 in-progress exclusion | OP17 currently excludes `completed_courses` only. Orchestrator/QU should later decide whether to also exclude in-progress courses based on query wording. |
| Alias coverage is manual | Aliases must be revalidated after every KG data change. No LLM-generated aliases without human review. |
| Track ID normalization | Old informal track values (`Cyber`, `Data Science`, `SW`, `CS`, `General`) must be normalized at QU/SCP/Orchestrator level before any KG call. |
| Alignment scores ≠ employability | KG alignment measures curriculum-skill coverage only. It is not a guarantee of employment readiness, hiring readiness, or academic graduation eligibility. |
| Display limits | "Top 3", "brief summary" etc. are Composer concerns. KG always returns complete structured results. |
| OP18 carry-forward retest | Resolver was noted for retest after full integration to confirm alias coverage is sufficient under real QU preprocessing. |

---

## 15. How to Run / Verify KG

**Syntax check:**

```powershell
python -m py_compile adapters/kg_adapter.py engines/kg/neo4j_client.py engines/kg/queries.py
```

**Run logging tests (no Neo4j required):**

```powershell
pytest tests/test_kg_adapter_logging.py -v
```

**Run direct KG operation tests (Neo4j must be online, `.env` must have valid `NEO4J_PASSWORD`):**

```powershell
pytest engines/kg/tests/test_queries_operations.py -v
```

**Run KGAdapter tests (Neo4j must be online):**

```powershell
pytest engines/kg/tests/test_kg_adapter.py -v
```

**Run combined KG suite (Neo4j must be online):**

```powershell
pytest engines/kg/tests/test_kg_adapter.py engines/kg/tests/test_queries_operations.py -v
```

**Verify graph data after reload:**

Run `engines/kg/cypher/verify.cypher` in Neo4j Browser or `cypher-shell`. Every count assertion row must show `PASS`. Duplicate and dangling-reference queries must return 0 rows.

**Graph load/reset:** Run Cypher scripts in Neo4j Browser in this order:
1. `engines/kg/cypher/reset.cypher`
2. `engines/kg/cypher/load.cypher`
3. `engines/kg/cypher/verify.cypher`

Refer to README or local Neo4j documentation for the exact `cypher-shell` command.

---

## 16. Final Status

```text
Status: LOCKED after Phase 1 Step 1 — PASS / COMPLETE.
KG is ready for ALE integration and later E2E testing.
```

Further KG code changes should happen only if integration tests expose a real issue. KG data changes (courses, roles, tracks, skills) require:

1. Update the relevant CSV file(s).
2. Reload graph (`reset.cypher` → `load.cypher`).
3. Run `verify.cypher` and confirm all assertions pass.
4. Revalidate `entity_aliases.json` against the new graph state.
5. Rerun direct operation tests: `pytest engines/kg/tests/test_queries_operations.py -v`.
6. Rerun adapter tests: `pytest engines/kg/tests/test_kg_adapter.py -v`.
