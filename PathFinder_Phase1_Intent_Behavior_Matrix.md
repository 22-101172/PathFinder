# PathFinder — Phase 1 Step 0A: Intent Behavior Matrix

> **Purpose:** Define expected user-facing behavior for all 26 locked intents before integration testing begins.
>
> This document is a **behavior contract only**. It answers: what should the student experience?
>
> It does NOT contain implementation details. Those belong to later artifacts:
>
> **Status:** Batch 1A in review — Intents 1, 2, 3

---

## What This Document Is and Is Not

| Is | Is Not |
|----|--------|
| Behavior contract — what the student should experience | Test execution record |
| Realistic query examples by level | Expected QU output or internal route |
| Expected student-facing response | Required logs specification |
| Edge and failure behavior | Testing status tracker |
| Known issues and open decisions | Component quality gates |

---

## Deferred to Later Phase 1 Artifacts

These sections were removed from the behavior matrix intentionally.
They will be defined in the right place:

```
Expected QU Output         → defined during QU component audit (Phase 1 Step 7)
Expected Route             → defined during Orchestrator component audit (Phase 1 Step 8)
Required Logs              → defined during logging pass per component
Testing Status             → tracked in Phase 2 test reports
Deficiency Register        → updated after actual tests begin
Component Quality Gates    → defined in Component Audit Reports per component
```

---

## Test Level Reference

| Level | Name | Purpose |
|-------|------|---------|
| L1 | Clean single-intent | Happy path, straightforward phrasing |
| L2 | Natural student language | Realistic, informal, how students actually type |
| L3 | Entity variation | Names vs codes vs abbreviations vs aliases |
| L4 | Student-aware | Requires StudentContext to answer correctly |
| L5 | Failure / edge case | Not found, ambiguous, missing data, cannot_compute |
| L6 | Multi-turn / session | Uses session state, last_referenced, assumptions |
| L7 | Compound query | Multiple intents from one message |

---

## Intent List

| # | Intent | Domain | Student Context | Batch |
|---|--------|--------|----------------|-------|
| 1 | get_student_record | Student Record (D6) | Yes | 1A ✓ |
| 2 | get_course_info | Course Info (D2) | No | 1A ✓ |
| 3 | get_course_prerequisites | Course Info (D2) | No | 1A ✓ |
| 4 | check_course_eligibility | Academic Planning (D1) | Yes | 1B |
| 5 | plan_semester | Academic Planning (D1) | Yes | 1B |
| 6 | run_graduation_audit | Academic Planning (D1) | Yes (base only) | 1B |
| 7 | generate_graduation_roadmap | Academic Planning (D1) | Yes | 1C |
| 8 | simulate_gpa_forward | Academic Planning (D1) | Yes | 1C |
| 9 | solve_target_gpa | Academic Planning (D1) | Yes | 1C |
| 10 | get_skills_taught | Course Info (D2) | No | 2 |
| 11 | search_courses_by_skill | Course Info (D2) | No | 2 |
| 12 | get_role_profile | Career / Role (D3) | No | 2 |
| 13 | get_roles_by_track | Career / Role (D3) | Conditional | 2 |
| 14 | compute_skill_gap | Career / Role (D3) | Yes | 2 |
| 15 | compute_alignment_score | Career / Role (D3) | Yes | 2 |
| 16 | recommend_courses_to_close_gap | Career / Role (D3) | Yes | 2 |
| 17 | find_best_matching_roles | Career / Role (D3) | Yes | 2 |
| 18 | estimate_alignment_improvement | Career / Role (D3) | Yes | 2 |
| 19 | get_focus_courses_for_target | Career / Role (D3) | Yes | 2 |
| 20 | get_track_overview | Track Guidance (D4) | Conditional | 3 |
| 21 | compare_tracks | Track Guidance (D4) | Conditional | 3 |
| 22 | recommend_track_for_role | Track Guidance (D4) | No | 3 |
| 23 | recommend_track_for_skill | Track Guidance (D4) | No | 3 |
| 24 | policy_query | Policy (D5) | No | 3 |
| 25 | clarification_needed | Control | — | 3 |
| 26 | out_of_scope | Control | — | 3 |

---

## Note on plan_semester

> **Current codebase intent name: `plan_semester`**
>
> All entries in this matrix use `plan_semester` — the current locked name in `qu_intents.py` and `orchestrator.py`.
>
> Phase 1 Fix Task (during ALE + Orchestrator audit):
> - Restrict behavior to next-semester-only (Fall/Spring default, Summer must be explicitly requested)
> - Controlled rename: `plan_semester` → `plan_next_semester`
> - Files: `qu_intents.py`, `orchestrator.py`, `ale_adapter.py`, ALE function, `schemas.py`
>
> **Do not rename before the audit. Test Phase 1 against `plan_semester`.**

---

## Known Phase 0 Issues (Deficiency Register Seed)

| ID | Intent | Issue | Priority |
|----|--------|-------|----------|
| D001 | get_student_record | Reset assumptions says "updated your record" instead of "cleared assumptions" | P2 |
| D002 | plan_semester | "What courses should I take?" returned fake missing-data answer | P1 |
| D003 | check_course_eligibility | status=in_progress narrated as "missing prerequisites" not "already enrolled" | P1/P2 |
| D004 | compare_tracks | "Compare AI and Data Science" produced two SQs, asked for clarification | P1 |
| D005 | All intents | Responses use internal codes only (C-CS219) instead of names (Advanced Programming (C-CS219)) | P2 |

---
---

# Intent 1: get_student_record

**Intent Code:** `get_student_record`

**Domain:** Student Record (D6)

**Student Context Required?** YES

**Purpose:**
Return the student's current academic snapshot: completed courses, in-progress courses, failed courses, CGPA, credit hours, academic standing, and current semester. When session assumptions are active, the response should reflect the official record and clearly note that a what-if scenario is active in the session. When assumptions are cleared, the response should confirm the return to the official record.

---

## Test Queries by Level

### L1 — Clean Single-Intent
- "What is my academic record?"
- "What courses have I completed?"
- "What is my GPA?"
- "What is my current academic standing?"
- "Show me my student record"

### L2 — Natural Student Language
- "What courses did I finish?"
- "What's my current status?"
- "How am I doing academically?"
- "Am I in good standing?"
- "How many credits have I earned so far?"
- "Show me my progress"
- "What's my GPA looking like?"

### L3 — Entity Variation
*(No external entity — student is always the subject)*
- "Pull up my record" → same intent
- "Give me a summary of where I stand" → same intent

### L4 — Student-Aware Cases

These must produce correct and different answers per student:

| Student Scenario | Expected Response Content |
|-----------------|--------------------------|
| STU000031 | CGPA 3.48, good standing, 5 completed, 5 in-progress |
| Student with academic warning | Warning status explained, consecutive warnings noted |
| Student with failed courses | Failed courses listed separately |
| Student with no courses yet | "No courses recorded. You may be in your first semester." |
| Student with Graduated status | Graduation noted, no in-progress courses |
| Student with Suspended status | Suspension noted clearly |

### L5 — Failure / Edge Cases
- Unknown student ID → clear "student not found" message, no crash
- Student with all Withdrawn registrations → 0 completed, 0 failed, note about withdrawals
- Student with only Incomplete (I) grades → 0 completed, courses noted as incomplete
- Rule bundle for academic standing missing → standing shown as "unavailable", not crash
- CGPA is None or not yet computed → handle gracefully

### L6 — Multi-Turn / Session Cases
- T1: Set what-if assumption → T2: "Show my record" → must show official record, not assumption-inflated record. Session note: "You have an active what-if assumption in this session."
- T1: Any query → T2: "Reset assumptions" → T3: "What is my record?" → clean official record, confirms assumptions cleared
- **[D001]** T1: "Reset assumptions" → response must say "cleared your what-if assumptions" NOT "your academic record was updated"

### L7 — Compound Queries
- "Show me my record and can I take Advanced Physics?" → SQ1: get_student_record, SQ2: check_course_eligibility
- "What courses did I finish and what are the prerequisites of Machine Learning?" → SQ1: get_student_record, SQ2: get_course_prerequisites

---

## Expected Student-Facing Behavior

**Success response must include:**
```
Completed courses — listed by name (code), e.g. "Elementary Physics (C-PH111)"
In-progress courses — listed by name (code)
Failed courses — listed by name (code), if any
CGPA — displayed as number (e.g. 3.48)
Credit hours earned
Academic standing — plain language ("Good standing" / "Academic warning")
Current semester
```

**Format rules:**
- Course names first, codes in brackets
- Academic standing explained in plain language (not raw rule values)
- If warning: explain what it means briefly
- If assumptions are active: note "You have an active what-if scenario in your session"
- No internal field names, no engine names, no database codes

**Reset assumptions response must say:**
```
"I cleared your what-if assumptions. You are back to your official academic record."
NOT: "Your academic record has been updated."
```

---

## Edge / Failure Behavior

| Case | Expected Response |
|------|-----------------|
| Student not found | "I couldn't find an academic record for this student ID. Please verify your student ID." |
| No courses recorded | "No courses are recorded yet. You may be starting your first semester." |
| All courses withdrawn | "No completed or failed courses on record. Some courses were withdrawn." |
| Academic standing unavailable | Show CGPA and courses, note "Academic standing currently unavailable." |
| Graduated student | "You have completed your graduation requirements. Congratulations!" |
| Suspended student | Note suspension status clearly and suggest speaking with an advisor |

---

## Known Phase 0 Issues

**[D001] Reset assumptions wording (P2)**
Query: "Reset Assumptions"
Phase 0 result: "Your academic record has been updated to reflect your official study status."
Expected: "I cleared your what-if assumptions. You are back to your official academic record."

---

## Open Questions / Decisions to Lock

```
Q1: Should get_student_record always show the OFFICIAL record regardless of active assumptions,
    or should it optionally show the what-if snapshot if assumptions are active?
    → Proposed: Always official record. Only note that assumptions are active in session.
    → Status: TO CONFIRM during SCP / Session Manager audit.

Q2: Who computes academic_standing — SCP, Orchestrator, or ALE?
    → Currently Orchestrator computes it using academic_warning_rules bundle inline.
    → Phase 1 audit must decide the correct owner.
    → Status: TO DECIDE during Orchestrator audit.

Q3: Should completed course names be enriched from KG (to show full names), or
    does SCP/Excel already provide names?
    → Phase 1 audit must check what Excel contains vs what KG can provide.
    → Status: TO CONFIRM during SCP audit.
```

---
---

# Intent 2: get_course_info

**Intent Code:** `get_course_info`

**Domain:** Course Info (D2)

**Student Context Required?** NO

**Purpose:**
Return factual information about a course: its name, code, credit hours, level, description, prerequisites, skills taught, and semester availability. This is a pure information retrieval intent with no student-specific logic. The student should be able to ask about any course by name, code, or common alias.

---

## Test Queries by Level

### L1 — Clean Single-Intent
- "What is Advanced Physics?"
- "Tell me about Advanced Programming"
- "Show me the course info for Machine Learning"
- "What is C-PH112?"

### L2 — Natural Student Language
- "What's Advanced Physics about?"
- "What does Advanced Programming teach?"
- "I heard Machine Learning is hard, what exactly is it?"
- "How many credits is Advanced Physics?"
- "Is Advanced Physics a hard course?"
- "Tell me more about that physics course" *(see L6 for session dependency)*

### L3 — Entity Variation

All of the following should resolve to the same course:

| Query | Raw Mention | Should Resolve To |
|-------|------------|------------------|
| "What is Advanced Physics?" | "Advanced Physics" | C-PH112 |
| "What is C-PH112?" | "C-PH112" | C-PH112 |
| "What is Physics 2?" | "Physics 2" | C-PH112 (or clarify if ambiguous) |
| "Tell me about PH112" | "PH112" | C-PH112 (partial code) |
| "Tell me about intro to CS" | "intro to CS" | C-CS111 |
| "What is algorithms?" | "algorithms" | Resolve or clarify if multiple matches |

### L4 — Student-Aware Cases
*(This intent is not student-aware by design. One edge case:)*
- "Tell me about the course I'm taking" → Too ambiguous if student has multiple in-progress courses. Ask: "Which course do you mean?"

### L5 — Failure / Edge Cases
- "What is Quantum Computing?" (not in KG) → "I couldn't find a course called 'Quantum Computing'. Try searching by course code or the full official name."
- "What is C-XX999?" (fake code) → "No course found with code C-XX999. Please check the code or try the full course name."
- "What is that course?" (no session context for "that") → "Could you clarify which course you mean?"
- "What is physics?" (genuinely ambiguous — multiple matches) → List options: "There are multiple Physics courses. Did you mean: Elementary Physics (C-PH111), Advanced Physics (C-PH112)...?"
- KG unavailable → "I'm unable to retrieve course information right now. Please try again in a moment."

### L6 — Multi-Turn / Session Cases
- T1: "What is Advanced Physics?" → T2: "Tell me more about it" → "it" resolves to C-PH112 from session
- T1: "What is Advanced Physics?" → T2: "What are its prerequisites?" → intent switches to get_course_prerequisites for C-PH112
- T1: "What is Advanced Physics?" → T2: "Can I take it?" → intent switches to check_course_eligibility for C-PH112
- T1: "What are the prerequisites of Machine Learning?" → T2: "What is it about?" → "it" resolves to ML course, intent: get_course_info

### L7 — Compound Queries
- "What is Advanced Physics and what are its prerequisites?" → SQ1: get_course_info (C-PH112), SQ2: get_course_prerequisites (C-PH112)
- "Tell me about Advanced Physics and Advanced Programming" → SQ1: get_course_info (C-PH112), SQ2: get_course_info (C-CS219)
- "What is Machine Learning and can I take it?" → SQ1: get_course_info (ML), SQ2: check_course_eligibility (ML)

---

## Expected Student-Facing Behavior

**Success response format:**
```
Advanced Physics (C-PH112)

Credits:        3 credit hours
Level:          Year 2 (Intermediate)
Prerequisites:  Elementary Physics (C-PH111)
Skills Taught:  [list of skills by name]
Offered:        Fall and Spring
Description:    [course description]
```

**Format rules:**
- Course heading: Name (Code)
- Prerequisites listed by name (code), never code only
- Skills listed by name, never internal IDs
- If no prerequisites: "No prerequisites. You can take it anytime you meet registration requirements."
- If no description available: show available fields and note description is not available
- No internal field names, no KG/database terms

---

## Edge / Failure Behavior

| Case | Expected Response |
|------|-----------------|
| Course not found by name | "I couldn't find 'X'. Try searching by course code or the full official name." |
| Course not found by code | "No course found with code X. Please check the code or try the full name." |
| Ambiguous name (multiple matches) | List the options and ask which one |
| No session context for "that course" | Ask for clarification |
| KG unavailable | "Course information is temporarily unavailable. Please try again." |
| Course with no prerequisites | "No prerequisites. You can take it anytime." |
| Course with no description in KG | Show available fields, note description unavailable |

---

## Known Phase 0 Issues

**[D005] Course codes displayed without names (P2)**
Phase 0 showed prerequisites listed as "C-PH111" only.
Expected: "Elementary Physics (C-PH111)".
Root cause TBD: may be KG not returning prerequisite names, or Composer not enriching them.
To investigate during KG + Composer audit.

---

## Open Questions / Decisions to Lock

```
Q1: Does KG return prerequisite names alongside codes in get_course_profile?
    Or does Composer need to perform a secondary name lookup?
    → Status: TO CONFIRM during KG engine audit.

Q2: When multiple courses match a vague name (e.g., "physics"),
    should QU ask for clarification before calling Orchestrator,
    or should Orchestrator/KG return the list and Composer present options?
    → Status: TO DECIDE during QU audit.

Q3: Should session last_referenced update after get_course_info?
    → Proposed: Yes. Every course-mentioning intent updates last_referenced.course_code.
    → Status: TO CONFIRM during Orchestrator audit.
```

---
---

# Intent 3: get_course_prerequisites

**Intent Code:** `get_course_prerequisites`

**Domain:** Course Info (D2)

**Student Context Required?** NO

**Purpose:**
Return the prerequisite courses (and any non-course prerequisites like credit hour thresholds) that a student must satisfy before enrolling in a specific course. The default is direct prerequisites only. The student can ask for the full chain. This intent is purely informational — it does not check whether the student has met the prerequisites (that is check_course_eligibility).

---

## Test Queries by Level

### L1 — Clean Single-Intent
- "What are the prerequisites for Advanced Physics?"
- "What do I need to take before Advanced Programming?"
- "What courses must I complete to take Machine Learning?"
- "What are the full prerequisites of Machine Learning?" *(depth = full)*

### L2 — Natural Student Language
- "What do I need before Advanced Physics?"
- "Can I jump straight into Advanced Programming or do I need something first?"
- "Is there anything I have to take before Machine Learning?"
- "What's required before I can take Advanced Physics?"
- "Do I need to take anything before Advanced Physics?"
- "Prerequisites of Advanced Physics please"

### L3 — Entity Variation

| Query | Raw Mention | Should Resolve To |
|-------|------------|------------------|
| "Prerequisites of Advanced Physics" | "Advanced Physics" | C-PH112 |
| "Prerequisites of C-PH112" | "C-PH112" | C-PH112 |
| "What do I need before PH112?" | "PH112" | C-PH112 (partial) |
| "What do I need before Physics 2?" | "Physics 2" | C-PH112 (or clarify) |
| "Prerequisites of Algorithms" | "Algorithms" | Resolve to correct code |

### L4 — Student-Aware Cases
*(Not student-aware by design. One session-dependent case:)*
- "What are the prerequisites?" with no course given and no session context → "Which course's prerequisites would you like to see?"
- "What are the prerequisites?" after discussing Advanced Physics → Resolves to C-PH112 via last_referenced

### L5 — Failure / Edge Cases
- "Prerequisites of Quantum Robotics" (not in KG) → "I couldn't find a course called 'Quantum Robotics'. Try the course code or full official name."
- "Prerequisites of C-PH112" when it has no prerequisites → "Advanced Physics (C-PH112) has no prerequisites. You can enroll anytime you meet registration requirements."
- No course given, no session context → "Which course's prerequisites would you like to see?"
- "Full prerequisites of Advanced Programming" → Returns transitive chain, not just direct
- Course with credit-hour threshold (non-course prerequisite) → Must be clearly stated: "You must have completed at least X credit hours"

### L6 — Multi-Turn / Session Cases
- T1: "What is Advanced Physics?" → T2: "What are its prerequisites?" → "its" resolves to C-PH112
- **[Phase 0 PASS]** T1: "What are the prerequisites of Advanced Physics?" → returned Elementary Physics (C-PH111) correctly
- T1: "What are the prerequisites of Advanced Physics?" → T2: "Can I take it?" → intent switches to check_course_eligibility for C-PH112
- T1: "What are the prerequisites of Advanced Physics?" → T2: "What about Advanced Programming?" → T2 is get_course_prerequisites for C-CS219; last_referenced updates
- T1: "What are the prerequisites of Advanced Physics?" → T2: "Assume I pass that, can I take Advanced Physics?" → T2: assumption on C-PH111, check_course_eligibility for C-PH112

### L7 — Compound Queries
- "What are the prerequisites of Advanced Physics and can I take it?" → SQ1: get_course_prerequisites (C-PH112), SQ2: check_course_eligibility (C-PH112)
- "What are the prerequisites of Advanced Physics and Advanced Programming?" → SQ1: get_course_prerequisites (C-PH112), SQ2: get_course_prerequisites (C-CS219)
- "What is Advanced Physics and what are its prerequisites?" → SQ1: get_course_info (C-PH112), SQ2: get_course_prerequisites (C-PH112)

---

## Expected Student-Facing Behavior

**Success — course with prerequisites:**
```
Advanced Physics (C-PH112) requires:
  • Elementary Physics (C-PH111)

Complete this course before enrolling in Advanced Physics.
```

**Success — course with multiple prerequisites:**
```
Machine Learning requires:
  • Programming Fundamentals (C-CS112)
  • Linear Algebra (C-MA201)
  • Data Structures (C-CS201)

All must be completed before enrolling.
```

**Success — course with credit threshold:**
```
Advanced Physics (C-PH112) requires:
  • Elementary Physics (C-PH111)
  • At least 30 credit hours completed

Both conditions must be met before enrolling.
```

**Success — no prerequisites:**
```
Advanced Physics (C-PH112) has no prerequisites.
You can enroll anytime you meet the registration requirements.
```

**Full chain (depth = full):**
```
Full prerequisites for Machine Learning:
  Direct: Programming Fundamentals (C-CS112), Linear Algebra (C-MA201)
  Also required: [transitive chain listed clearly]
```

**Format rules:**
- Prerequisites listed by name (code), never code only
- Credit thresholds phrased naturally ("at least X credit hours"), not as raw data
- "full" vs "direct" distinction is clear to the student

---

## Edge / Failure Behavior

| Case | Expected Response |
|------|-----------------|
| Course not found | "I couldn't find that course. Try using the course code or full official name." |
| No prerequisites | "No prerequisites. You can enroll anytime." (positive framing) |
| No course specified | "Which course's prerequisites would you like to see?" |
| KG unavailable | "Prerequisite information is temporarily unavailable. Please try again." |
| Course with only credit threshold | "You must have completed at least X credit hours to enroll in this course." |

---

## Known Phase 0 Issues

**Phase 0 Test 2 — PASS**
Query: "What are the prerequisites of Advanced Physics?"
Result: Correctly returned Elementary Physics (C-PH111). This path works.

**[D005] Code-only display (P2)**
Phase 0 showed "C-PH111" without the name "Elementary Physics".
To investigate: does KG return prerequisite names, or only codes?

---

## Open Questions / Decisions to Lock

```
Q1: Does KG get_prerequisites return both course_code AND name for each prerequisite?
    Or only course_code, requiring a secondary lookup for names?
    → Status: TO CONFIRM during KG engine audit.

Q2: Should "full prerequisites" (transitive chain) be supported?
    The locked Orchestrator design mentions depth as a param.
    → Status: TO CONFIRM the current ALE/KG implementation supports depth="full".

Q3: What is the format when a course has BOTH course prerequisites AND credit thresholds?
    → Proposed: List course prerequisites first, then credit threshold as a separate bullet.
    → Status: TO CONFIRM behavior is consistent across all such courses.

Q4: Should last_referenced update after get_course_prerequisites?
    → Proposed: Yes. The queried course becomes the last referenced course.
    → Status: TO CONFIRM during Orchestrator audit.
```

---

---

*End of Batch 1A. Pending review before Batch 1B begins.*

*Batch 1B will cover: check_course_eligibility, plan_semester, run_graduation_audit*
