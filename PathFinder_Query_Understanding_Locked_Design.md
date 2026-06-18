# PathFinder — Query Understanding Locked Design

**Status:** LOCKED for QU implementation  
**Component:** Query Understanding (QU)  
**Project:** PathFinder — AI-powered academic and career advising system  
**Authority level:** Active implementation authority for QU  

---

## 0. Document Purpose

This document locks the Query Understanding design before implementation.

The purpose of QU is to convert a raw user message into an ordered `list[StructuredQuery]` for the Orchestrator.

QU is a parser/classifier, not an answer generator.

QU must classify intent, decompose compound turns, extract and resolve entities, detect policy queries, detect assumptions, handle follow-ups, and return structured objects only.

---

## 1. Authority Hierarchy

When implementing QU, follow this order:

1. `PathFinder_Query_Understanding_Locked_Design.md` — this file.
2. `PathFinder_Orchestrator_Phase1_Phase2_Locked_Design.md` after replacement with the final Phases 1–6 locked Orchestrator design.
3. Current codebase files as implementation material only.
4. `PathFinder_Orchestrator_Handoff.md` is archived/historical only.

If current code conflicts with this document, current code is outdated.

---

## 2. QU Scope

### QU Does

- Intent classification using only locked intents.
- Compound query decomposition into ordered `list[StructuredQuery]`.
- Entity extraction.
- Entity resolution using KG `resolve_entity` only.
- Ambiguity handling.
- Policy query detection and minimal policy rewrite.
- Student-referential detection.
- Session override detection.
- Follow-up/pronoun handling using `last_referenced` and recent turns.
- Deterministic fallback when LLM fails.

### QU Does Not

- Does not call ALE.
- Does not call RAG.
- Does not execute KG business operations other than entity resolution.
- Does not compose final user-facing answers.
- Does not apply session overrides.
- Does not compute academic logic.
- Does not update official transcript or student records.
- Does not send private student data to the LLM.

---

## 3. Privacy Contract

QU must not send the following to any LLM:

- student_id
- student name
- full transcript
- grades
- CGPA
- full StudentContext
- full session state
- any personal/PII-like raw student record

QU may use safe internal context for deterministic fallback or Orchestrator flags, but only minimal safe hints should be provided to the LLM, such as:

- recent turn summaries
- last referenced entity IDs/codes
- extracted non-PII entity mentions
- whether a query appears student-referential

---

## 4. Locked Intent Taxonomy

QU may output only these 26 intents.

### Domain 1 — Academic Planning

1. `plan_semester`
2. `generate_graduation_roadmap`
3. `run_graduation_audit`
4. `check_course_eligibility`
5. `simulate_gpa_forward`
6. `solve_target_gpa`

### Domain 2 — Course Info

7. `get_course_info`
8. `get_course_prerequisites`
9. `get_skills_taught`
10. `search_courses_by_skill`

### Domain 3 — Career / Role

11. `get_role_profile`
12. `get_roles_by_track`
13. `compute_skill_gap`
14. `compute_alignment_score`
15. `recommend_courses_to_close_gap`
16. `find_best_matching_roles`
17. `estimate_alignment_improvement`
18. `get_focus_courses_for_target`

### Domain 4 — Track Guidance

19. `get_track_overview`
20. `compare_tracks`
21. `recommend_track_for_role`
22. `recommend_track_for_skill`

### Domain 5 — Policy

23. `policy_query`

### Domain 6 — Student Record

24. `get_student_record`

### QU Control Intents

25. `clarification_needed`
26. `out_of_scope`

---

## 5. Forbidden Intent Names

QU must never output the following old, invented, or non-locked intent names:

- `get_prerequisites`
- `handbook_query`
- `check_eligibility`
- `simulate_gpa`
- `generate_semester_plan`
- `mixed_course_policy`
- `get_courses_in_track`
- `get_track_courses_for_role`
- `get_roles_by_skill`
- `graduation_audit_with_roadmap`
- `compare_courses`
- `rank_courses`
- any other invented intent

### Required Mappings Instead

- “courses in a track” → `get_track_overview`
- “careers/jobs from a track” → `get_roles_by_track`
- “best track for a role” → `recommend_track_for_role`
- “best track for a skill” → `recommend_track_for_skill`
- “courses for a target role/track” → `get_focus_courses_for_target` or `recommend_courses_to_close_gap`
- “course prerequisites” → `get_course_prerequisites`
- “can I take course X?” → `check_course_eligibility`
- “can I graduate and roadmap?” → `[run_graduation_audit, generate_graduation_roadmap]`

---

## 6. StructuredQuery Contract

QU returns an ordered `list[StructuredQuery]`, not a single SQ.

Each SQ has exactly one intent.

Compound user messages become multiple SQs in logical order.

### Preferred Schema

Use existing Pydantic models where possible, especially `EntitySet` if it already exists.

```python
class StructuredQuery(BaseModel):
    intent: str
    original_text: str
    entities: EntitySet | dict[str, str | None]
    secondary_entities: EntitySet | dict[str, str | None] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    session_overrides: SessionOverrides = Field(default_factory=SessionOverrides)
    student_referential_fallback: bool = False
```

### Field Meanings

- `intent`: one of the 26 locked intents only.
- `original_text`: raw user text, except for `policy_query`, where it may contain a minimal self-contained handbook-safe rewrite.
- `entities`: resolved primary entities such as `course_code`, `role_id`, `track_id`, `skill_id`.
- `secondary_entities`: second entity set for compare queries, especially `compare_tracks`.
- `params`: intent-specific parameters.
- `session_overrides`: assumptions detected by QU; applied later by Session Manager.
- `student_referential_fallback`: true when query uses “my/I/me” semantics and Orchestrator may need StudentContext fallback.

### Control Intents

`clarification_needed` and `out_of_scope` are first-class SQs.

They are not errors.

Orchestrator should skip engine execution and pass them to Composer.

Example:

```python
{
    "intent": "clarification_needed",
    "original_text": "Which course did you mean?",
    "entities": {},
    "secondary_entities": None,
    "params": {},
    "session_overrides": SessionOverrides(),
    "student_referential_fallback": False,
}
```

---

## 7. SessionOverrides Contract

Use existing fields only.

Do not invent `clear_all` or new override schema fields.

### Existing Fields

```python
class SessionOverrides(BaseModel):
    added_courses: list[str] = Field(default_factory=list)
    assumed_passed_courses: list[str] = Field(default_factory=list)
    assumed_failed_courses: list[str] = Field(default_factory=list)
    target_role: str | None = None
    course_override_type: str = "none"
    override_action: str = "accumulate"
```

### Valid `course_override_type` Values

- `planned`
- `assumed_done`
- `assumed_failed`
- `assumed_passed`
- `gpa_scenario`
- `none`

### Valid `override_action` Values

- `accumulate`
- `replace`
- `clear`

### Natural Language Mapping

| User wording | Fields |
|---|---|
| “plan as if I take X” | `added_courses=[X]`, `course_override_type="planned"` |
| “assume I took/completed/done X” | `added_courses=[X]`, `course_override_type="assumed_done"` |
| “assume I passed X” | `assumed_passed_courses=[X]`, `course_override_type="assumed_passed"` |
| “assume I failed X” | `assumed_failed_courses=[X]`, `course_override_type="assumed_failed"` |
| “what if I get A/B/F in X” | `params.expected_grades`, not persistent override |
| “clear/reset/back to official record” | `override_action="clear"`, `course_override_type="none"` |

### MVP Mixed Override Rule

- Same persistent override type may accumulate.
- Different persistent override types across turns should prefer `replace` unless user explicitly asks to keep both.
- Mixed persistent override types in one turn should produce `clarification_needed` unless current Session Manager safely supports mixed types.
- Real-life claims like “I failed X last semester” must not update official records or create overrides unless user explicitly says assume/what-if/suppose/pretend.

---

## 8. LLM Model Chain and Rate Limits

Use Groq-only chain for MVP because these models are available in the current account and share the same client path.

Model names must be configurable through environment variables/settings and must not be hardcoded inside QU logic.

### Model Chain

1. Primary: `llama-3.3-70b-versatile`
2. Fallback 1: `meta-llama/llama-4-scout-17b-16e-instruct`
3. Fallback 2: `qwen/qwen3-32b`
4. Emergency: `llama-3.1-8b-instant`

### Observed Groq Account Limits

| Model | RPM | RPD | TPM | TPD |
|---|---:|---:|---:|---:|
| `llama-3.3-70b-versatile` | 30 | 1K | 12K | 100K |
| `meta-llama/llama-4-scout-17b-16e-instruct` | 30 | 1K | 30K | 500K |
| `qwen/qwen3-32b` | 60 | 1K | 6K | 500K |
| `llama-3.1-8b-instant` | 30 | 14.4K | 6K | 500K |

### Fallback Behavior

Try models in order.

Move to next model on:

- timeout
- 429/rate limit
- invalid JSON
- unrecognized intent
- schema validation failure

If all LLM models fail:

1. deterministic policy keyword match → `policy_query`
2. deterministic simple course query with enough entity data → matching course intent
3. otherwise → `clarification_needed`

Never crash the turn.

### Model Acceptance Criteria

Each configured model must pass a sanity test before being trusted:

- valid JSON
- no invented intent names
- no old intent names
- correct multi-SQ decomposition
- correct override detection
- policy_query not overused
- acceptable latency

---

## 9. Deterministic Preprocessing

Preprocessing runs before or around the LLM to improve accuracy and support safe fallback.

### Required Detection

- course code regex
- course names/nicknames/abbreviations hints
- policy keywords
- out-of-scope keywords
- override keywords
- semester parsing
- target CGPA parsing
- grade parsing
- student-referential patterns

### Course Code Regex

Use a robust regex for examples such as `C-CS301`, `C-AI321`, `HUM011`.

### Policy Keywords

Include at least:

- withdrawal
- drop
- incomplete
- attendance
- absence
- missing exam
- warning
- probation
- dismissal
- appeal
- retake
- improve retake
- grading scale
- grade points
- GPA percentage
- credit limits
- summer semester
- graduation requirements
- honors
- military training
- academic regulations

### Out-of-Scope Keywords

Include likely non-advising topics such as:

- financial aid
- housing
- tuition
- admissions
- application deadline
- registrar
- bills/fees
- scholarships

Scope boundaries can be fuzzy. When unsure, clarify rather than hallucinate.

### Grade Language

Map casual language carefully:

- “ace” → likely A or A+ depending policy/config
- “bomb/flunk/fail” → F in GPA scenario
- “crushed” → passed/high grade only if user explicitly says assume/what-if

Do not mutate official record based on casual past-tense claims.

---

## 10. Entity Extraction and Resolution

QU owns entity grounding.

For course, role, track, and skill:

1. extract raw mention
2. call KG `resolve_entity`
3. if exactly one match, fill resolved ID/code
4. if no match or multiple matches, emit `clarification_needed`

Do not silently choose the first ambiguous entity.

Make the resolver injectable/mockable so QU tests can run without live KG.

### Entity Types

- course code
- course name
- role
- track
- skill
- semester
- target CGPA
- expected grade

Semester, CGPA, and grade are parsed/validated deterministically and do not require KG resolution.

---

## 11. Intent Classification Rules

Each user query maps to one or more `StructuredQuery` objects.

Each SQ has exactly one intent.

### Domain Examples

- “Can I graduate?” → `run_graduation_audit`
- “Give me a roadmap” → `generate_graduation_roadmap`
- “What should I take next semester?” → `plan_semester`
- “Can I take OS?” → `check_course_eligibility`
- “If I get A in OS, what is my GPA?” → `simulate_gpa_forward`
- “What grades do I need for 3.5?” → `solve_target_gpa`
- “Tell me about Algorithms” → `get_course_info`
- “Prerequisites for OS” → `get_course_prerequisites`
- “What skills does AI teach?” → `get_skills_taught`
- “Courses that teach Python” → `search_courses_by_skill`
- “What is Data Scientist?” → `get_role_profile`
- “Careers for AI track” → `get_roles_by_track`
- “What am I missing for Data Scientist?” → `compute_skill_gap`
- “How aligned am I with ML Engineer?” → `compute_alignment_score`
- “Courses to become Data Scientist” → `recommend_courses_to_close_gap`
- “What careers suit me?” → `find_best_matching_roles`
- “If I take ML, does my alignment improve?” → `estimate_alignment_improvement`
- “Core/focus courses for Data Scientist” → `get_focus_courses_for_target`
- “Tell me about AI track” → `get_track_overview`
- “Compare AI and CS” → `compare_tracks`
- “Best track for Data Scientist” → `recommend_track_for_role`
- “Best track for Python” → `recommend_track_for_skill`
- “Warning policy?” → `policy_query`
- “Show my record/progress snapshot” → `get_student_record`

---

## 12. Compound Query Decomposition

Compound queries produce multiple SQs in logical order.

### Rules

- Explicit “and” with different intents → split.
- “if not” with consequence → condition intent first, consequence intent second.
- Clear repeated single-target eligibility → one SQ per course.
- Vague or conflicting compound query → `clarification_needed`.
- Preserve user logical order.
- Do not create fake merged intents.

### Examples

“Can I graduate, and if not give me a roadmap?”

→ `[run_graduation_audit, generate_graduation_roadmap]`

“Tell me about OS and can I take it?”

→ `[get_course_info, check_course_eligibility]`

“Can I take C-AI311 and C-AI421?”

→ `[check_course_eligibility(C-AI311), check_course_eligibility(C-AI421)]`

---

## 13. Policy Query Handling

QU does not call RAG.

QU only creates `policy_query` SQs.

### Detection Layers

1. deterministic handbook keywords
2. LLM handbook-topic judgment
3. clarification if too vague or multi-intent ambiguous

### Text Handling

For standalone policy queries:

- If clear and self-contained, preserve `original_text`.
- If vague, pronoun-based, or personal, minimally rewrite to a handbook-safe self-contained question.

For policy subqueries in multi-intent messages:

- Extract only the policy part.
- Preserve wording if clear.
- Rewrite only enough to remove pronouns/personal details and make it self-contained.

Examples:

- “What is the withdrawal policy?” → preserve.
- “If I fail this course, what happens by policy?” → “What is the policy for failing and retaking a course?”
- “Can I take OS and what happens if I withdraw?” → `[check_course_eligibility, policy_query("What is the course withdrawal policy?")]`

---

## 14. Student-Referential Detection

Set `student_referential_fallback=True` when the query uses student-centered wording or requires StudentContext.

Examples:

- “my track”
- “my GPA”
- “my courses”
- “what roles suit me”
- “can I graduate”
- “am I eligible”

Orchestrator handles missing StudentContext. QU should not fabricate student data.

---

## 15. Follow-Up Handling

QU uses `last_referenced` and recent turns to resolve pronouns.

Examples:

- Prior: “Tell me about C-CS301.” Follow-up: “What are its prerequisites?” → course is C-CS301.
- Prior mentioned two courses. Follow-up: “What about it?” → ambiguous; emit `clarification_needed`.
- Prior role is Data Scientist. Follow-up: “What skills am I missing for that role?” → role is Data Scientist.

Recent-turn count is implementation-configurable. Keep summaries compact.

---

## 16. Ambiguity Handling

Emit `clarification_needed` when:

- required entity is missing
- entity is ambiguous
- KG `resolve_entity` returns no match
- KG `resolve_entity` returns multiple matches
- intent is ambiguous
- pronoun has no clear prior reference
- query is empty/minimal/noisy
- mixed persistent override types occur in one turn and current session design cannot safely support them

Clarification should be concise and ask for the missing decision.

---

## 17. Out-of-Scope Handling

Emit `out_of_scope` when query is outside PathFinder academic/career advising scope.

Examples:

- financial aid
- housing
- tuition payment
- admissions application
- general registrar opening hours
- non-academic unrelated requests

If the query has both in-scope and out-of-scope parts, decompose where useful or ask clarification if boundary is unclear.

---

## 18. Runtime Prompt Requirements

The QU system prompt must be compact but clear.

It must include:

- role statement
- strict JSON-only output instruction
- locked intent list
- forbidden intent names
- concise intent descriptions
- decomposition rules
- entity extraction/resolution rules
- policy handling rules
- override rules
- expected grades vs persistent override distinction
- student-referential rules
- follow-up/pronoun rules
- output schema
- few-shot examples

It must not include full handbook text or private student data.

---

## 19. Implementation Blueprint

### Files Likely to Create

- `gateway/qu_preprocessing.py`
- `gateway/qu_llm_client.py`
- `gateway/qu_system_prompt.py`
- tests for QU

### Files Likely to Modify

- `gateway/query_understanding.py`
- schema file containing `StructuredQuery`, `EntitySet`, `SessionOverrides`
- minimal session/update compatibility only if needed for QU tests

### Current `query_understanding.py`

The existing QU file is likely outdated and based on the old single-SQ architecture.

Implementation agent must inspect it first.

Reuse only safe helper ideas if useful:

- regex extraction
- recent-turn formatting
- JSON-mode call pattern
- deterministic fallback handling

If current structure conflicts with locked QU, replace the file content with a clean implementation.

Do not preserve old architecture just for compatibility.

---

## 20. Testing Strategy

QU tests must use realistic student language, not only course codes.

Tests must include:

- exact course codes
- course names
- course nicknames/abbreviations
- casual phrases
- typos
- policy queries
- compound queries
- assumptions
- clear assumptions
- expected grades
- follow-ups/pronouns
- ambiguous entities
- out-of-scope cases
- anti-invented-intent cases

### Must-Pass Categories

100% must pass:

- valid JSON
- no invented intents
- no old intent names
- locked intent list validation
- clear compound decomposition
- policy vs clarification boundary
- expected grades not stored as persistent overrides
- anti-invented-intent tests

Clarification is acceptable for genuinely ambiguous cases.

### Anti-Invented Intent Tests

Include tests such as:

- “What courses are in the AI track?” → `get_track_overview`, not `get_courses_in_track`.
- “What roles need Python?” → no `get_roles_by_skill`; clarify or map to valid locked intent.
- “Which track courses should I take for Data Scientist?” → `get_focus_courses_for_target` or `recommend_courses_to_close_gap`, not `get_track_courses_for_role`.
- “Can I take OS?” → `check_course_eligibility`, not `check_eligibility`.
- “Prerequisites for OS?” → `get_course_prerequisites`, not `get_prerequisites`.
- “Tell me about this course and can I take it?” → decompose, not `mixed_course_policy`.

### Test Cleanup Before Coding

Before implementing tests, clean:

- duplicate test IDs
- inconsistent test counts
- final T19/T30 MVP versions
- expected alternatives format

---

## 21. Implementation Stop Conditions

Before editing, implementation agent must inspect relevant files and print:

- files to modify/create
- final function signatures
- resolver injection strategy
- fallback model strategy
- test organization

Stop and report if there is a design conflict.

After implementation, report:

- files changed
- tests added
- tests passed/failed
- skipped tests and why
- remaining risks
- exact next step

Stop after QU implementation and QU tests.

Do not implement Orchestrator, Composer, API Gateway, Streamlit UI, or unrelated components.
