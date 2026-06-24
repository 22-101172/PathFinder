# Student Context Provider — Technical Documentation

> **Status:** PASS / LOCKED WITH INTEGRATION CARRY-FORWARD NOTES (2026-06-23)
>
> SCP is the authoritative source of student-record facts extracted from the
> registrar Excel file.  Nothing in this module makes academic decisions.

---

## 1. Purpose

The Student Context Provider (`gateway/student_context_provider.py`) converts
anonymized registrar data from an Excel workbook into a `StudentContext` object.

`StudentContext` is the single authoritative record of **what the registrar
knows** about a student.  It is consumed by the Orchestrator, which then feeds
the relevant subsets to ALE (academic decisions), KG (curriculum/career data),
and the Response Composer (student-facing wording).

SCP does not answer questions.  It does not make eligibility decisions.
It does not know about academic rules, prerequisites, or graduation requirements.
It extracts, normalizes, and classifies registrar facts — nothing more.

---

## 2. Inputs

### 2.1 Excel workbook — `students_anonymous.xlsx`

Must contain exactly two sheets:

**`data` sheet** — one row per student

| Column | Type | Notes |
|--------|------|-------|
| ID | str | Student identifier |
| Name | str | Student name (display only; not used in logic) |
| Program | str | Free-text program string; SCP normalizes to KG track ID |
| Level | str | "Freshman" / "Sophomore" / "Junior" / "Senior"; blank/invalid → `level=None` |
| Study Status | str | "Studying", "Graduated", "Suspended", etc. |
| Cumulative GPA | float | `cgpa`; NaN → `None` |
| Consecutive Warning | int | NaN → 0 |
| Total Warnings | int | NaN → 0 |
| Military Status | str | NaN → `None` |
| First Semester | str | Semester of first enrollment |
| Cumulative PHs | int | Passed credit hours → `total_credit_hours_earned` |
| Cumulative CHs | int | Counted credit hours (for GPA denominator) |
| Cumulative CPs | float | Cumulative quality points |
| Last Semester GPA | float | |
| Last Semester CHs | int | |
| Last Semester CPs | float | |
| Last Semester PHs | int | |
| Last Semester Warning | int | NaN → `None` |
| Current Semester CHs | int | NaN → 0 |

**`registrations` sheet** — one row per course registration attempt

| Column | Type | Notes |
|--------|------|-------|
| ID | str | Student identifier |
| Course Code | str | e.g. `C-CS219` |
| Semester | str | e.g. `Fall 2025`, `Spring 2026` |
| Registration Status | str | Comma-separated tags: `Fresh`, `Succeed`, `Withdrawn`, etc. |
| Letter Grade | str | `A`, `B+`, `C-`, `F`, `Abs`, `I`, `W`, `P`, `Con`; blank for active enrollments |

### 2.2 Load sequence

```python
scp.load_excel("data/students_anonymous.xlsx")   # must be called once at startup
ctx = scp.get_context("STU000001")               # returns StudentContext or None
```

`load_excel()` validates both sheets (schema and non-empty), then computes
`_global_current_semester` from the full registrations sheet before returning.

---

## 3. Outputs

### 3.1 `StudentContext`

```
student_id: str
name: str
program: str                              # raw registrar string, not normalized
track_id: Optional[str]                   # KG canonical: AI / CYS / DSE / SWE / GEN / None
track_status: Literal["supported","unsupported"]
track_error_code: Optional[str]           # "unsupported_track" when track_id is None

level: Optional[int]                      # 1–4; None when blank or unrecognized
first_semester: str
study_status: str                         # "Studying", "Graduated", "Suspended", ...
military_status: Optional[str]

cgpa: Optional[float]                     # None when Excel cell is blank
last_semester_gpa: Optional[float]
total_credit_hours_earned: int            # passed hours from registrar
cumulative_chs: Optional[int]
cumulative_cps: Optional[float]
last_semester_chs/cps/phs: Optional[int/float]
current_semester_chs: int
consecutive_warnings: int
total_warnings: int
last_semester_warning: Optional[int]

course_history: list[CourseRecord]        # one entry per registration attempt
completed_courses: list[str]              # course codes — best outcome = passed/repeated
failed_courses: list[str]                 # latest meaningful status = failed, never passed
in_progress_courses: list[str]            # active blank-registered in global current semester
zero_credit_courses_passed: list[str]     # courses with P grade

completed_regular_semesters: int          # Fall/Spring only; excludes current semester
current_semester: Optional[str]           # globally inferred or system-clock fallback
retake_count: dict[str, int]              # non-withdrawn attempt count per course
total_improve_retakes_used: int           # distinct course codes with Improve tag (non-withdrawn)
```

### 3.2 `CourseRecord`

```
course_code: str
credit_hours: Optional[int] = None        # ALWAYS None — KG/Orchestrator must patch
grade: Optional[str]                      # None for active enrollments
semester_taken: str
status: Literal["passed","repeated","failed","in_progress","withdrawn","incomplete"]
```

`CourseRecord.credit_hours` is always `None`.  SCP has no authoritative
source for per-course credit data.  The Orchestrator must provide
`kg_data["course_credit_lookup"]` to ALE for any computation that needs credits.

---

## 4. Boundaries

### SCP owns

- All fields extracted from the registrar `data` and `registrations` sheets
- Status classification for each registration attempt
- Track normalization to KG canonical IDs
- Current-semester inference from registrar enrollment data
- Derived counts: `completed_regular_semesters`, `total_improve_retakes_used`, `retake_count`

### SCP does NOT own

| Domain | Owner |
|--------|-------|
| Course names | KG (`get_course_profile`) |
| Per-course credit hours | KG → Orchestrator → ALE |
| Prerequisites | KG (`get_prerequisites`) |
| Eligibility decisions | ALE (`check_course_eligibility`) |
| Semester plans | ALE (`generate_semester_plan`) |
| Graduation audit | ALE (`run_graduation_audit`) |
| Career recommendations | KG (`compute_skill_gap`, `find_best_matching_roles`) |
| Response wording | Response Composer |
| Session assumptions | Session Manager |

---

## 5. Current-Semester Inference

### 5.1 Why global inference

Per-student inference (looking at only one student's rows) is vulnerable to stale
blank-grade rows in old semesters.  A student with a missing grade from Fall 2020
would falsely infer Fall 2020 as current.  A dataset-level approach aggregates
evidence from all students, making the signal much stronger.

### 5.2 Algorithm

`_compute_global_current_semester(df_reg)` runs once during `load_excel()`:

1. Scan every row in the registrations sheet.  Count "active blank-registered"
   rows per semester (see definition below).
2. If the semester with the highest count has ≥ 100 active rows, that semester
   is the global current semester (method: `active_blank_threshold`).
3. Fallback: chronologically latest non-withdrawn row across all students
   (method: `latest_non_withdrawn_global`).
4. Final fallback: `utils.get_current_semester()` — system clock
   (method: `system_clock`).

Result from real data: **Spring 2026 = 3170 active blank rows** — well above
the threshold of 100.  Spring 2026 wins decisively.

### 5.3 Active blank-registered row

A row qualifies as active blank-registered when ALL of the following hold:

- `Letter Grade` is blank (None / NaN)
- `Registration Status` contains `Registered`
- `Registration Status` does NOT contain `Succeeded`, `Failed`, `Withdrawn`,
  or `Forced Withdraw`

This excludes old rows like `"Succeeded, Registered"` with a missing grade
(data anomaly: 42 such rows exist in old semesters) and `"Failed, Registered"`
with a missing grade (12 rows).  These rows would incorrectly appear
in-progress under a naïve blank-grade check.

### 5.4 Effect on student context

```
current_semester         = _global_current_semester   (or system clock if None)
in_progress_courses      = student's active blank-registered rows in current semester
completed_regular_semesters excludes current semester (if None → excludes nothing)
```

When SCP is used in unit tests via `_inject()` without calling `load_excel()`,
`_global_current_semester` defaults to `None`.  In that state:
`in_progress_courses = []` and no semester is excluded from
`completed_regular_semesters`.  Tests that need in-progress behavior must pass
`global_current_sem=` to `_inject()`.

---

## 6. Status Mapping

`_map_status(reg_status, grade)` classifies each registration attempt:

| Status | Condition | Counts as attempt? | Notes |
|--------|-----------|-------------------|-------|
| `passed` | Grade A/B/C/D, P, or Con; or `Succeeded` tag | Yes | P = zero-credit pass; Con = conditional pass (graduation project pending) |
| `repeated` | `Repeat` tag + `Succeeded` tag | Yes | Used by `best_outcome` to detect completion |
| `failed` | Grade F or Abs; or `Failed` tag | Yes | Abs = absent from final exam, treated as failed |
| `incomplete` | Grade I | Yes | Past semester, grade unresolved; NOT in `in_progress_courses` |
| `in_progress` | Blank grade, no terminal tag | Yes | Only active-blank rows in global current semester appear in `in_progress_courses` |
| `withdrawn` | W grade, `Withdrawn` tag, or `Forced Withdraw` tag | No | Not an attempt; excluded from all derived lists |

**Key distinction:** `course_history` records `status="in_progress"` for every
blank-grade row.  But `in_progress_courses` only includes rows that are also in
the global current semester and pass the active-blank test.

**I-grade behavior:** An unresolved incomplete (`status="incomplete"`) is in
`course_history` and counts as an attempt in `retake_count`, but it does NOT
appear in `in_progress_courses`.

**Forced Withdraw:** Currently mapped to `withdrawn` regardless of the attached
grade (blank, Abs, or W).  Real data shows all 31 FW rows are in Fall 2025.
Supervisor/registrar confirmation is required before treating FW+Abs as `failed`.

**Grade casing:** `_clean_grade()` normalizes P, Con, I, F, Abs, W to canonical
form regardless of input case (e.g., `"abs"` → `"Abs"`).

---

## 7. Track Handling

### 7.1 Supported tracks

| Registrar program string (contains) | KG canonical ID |
|--------------------------------------|-----------------|
| `artificial intelligence` | `AI` |
| `cyber security` / `cybersecurity` | `CYS` |
| `data science and engineering` / `data science` | `DSE` |
| `software engineering` | `SWE` |
| `general program` / `general` | `GEN` |

Matching is substring, case-insensitive.  Longer keys take priority in lookup
order to avoid partial matches.

### 7.2 Unsupported tracks

| Program string (contains) | Result |
|---------------------------|--------|
| `computer science` | `track_id=None`, `track_status="unsupported"`, `track_error_code="unsupported_track"` |
| Any unrecognized string | Same |
| Blank / NaN | Same |

Unsupported track does **not** block student-record loading.  `StudentContext`
is fully populated.  However, the Orchestrator must block track-dependent flows
(planning, roadmap, skill-gap) for unsupported students, because no KG curriculum
data exists for those programs.

### 7.3 Real-data track distribution

```
AI   (53 students)  → supported
CYS  (33 students)  → supported
DSE  (15 students)  → supported
SWE  (11 students)  → supported
GEN (693 students)  → supported
CS   (11 students)  → unsupported (no KG node)
```

---

## 8. Credit Handling

### 8.1 Student-level totals — stay in SCP

`total_credit_hours_earned` (from `Cumulative PHs`), `cumulative_chs`,
`cumulative_cps`, `last_semester_chs`, etc. come directly from the registrar
`data` sheet.  These are trusted registrar values that SCP correctly owns.

### 8.2 Per-course credits — None everywhere

`CourseRecord.credit_hours = None` for every record.  SCP reads only course
codes and grades from the `registrations` sheet — credit data is not present
there.  The authoritative per-course credit source is the KG.

**Integration rule:** The Orchestrator must call `kg_adapter.call("get_course_profile", …)`
for each relevant course and build a `course_credit_lookup` dict, then pass it
as `kg_data["course_credit_lookup"]` to every ALE call that uses credits.

The ALE adapter falls back to `credits = 0` when `course_credit_lookup` is
absent.  A missing lookup produces `cannot_compute` or silently wrong GPA math.

---

## 9. Logging and Privacy

SCP logs:

- Load validation results: student count, registration row count
- Global current semester: value and inference method
- Per-student context summary: track, level, CGPA, study status, counts of
  completed/failed/in-progress courses, current semester, improve-retake count
- Warnings: invalid level, blank program, unknown program

SCP does NOT log:

- Student names or national IDs
- Raw grade lists or transcript content
- Full course history dumps
- Any data that could identify a student if the log file were exposed

`student_id` appears in log lines because the dataset is anonymized (IDs are
`STU000001` etc., not real national IDs).

---

## 10. Tests

**Test file:** `tests/test_student_context_provider.py`  
**Total:** 90 tests, 0 failures

| Group | Count | Description |
|-------|-------|-------------|
| Tests 1–11 | 11 | Synthetic baseline: not-found, zero-regs, Con/I/W/P grades, semester counting, best-outcome |
| Test 12 | 1 | Real-data smoke: STU000001 basic load |
| Tests 13–22 | 10 | Regression: semester exclusion, failed-tag edge cases, status normalization, load_excel validation |
| Tests 23–43 | 21 | Phase 1 audit: credit_hours=None, track normalization (AI/CYS/DSE/SWE/GEN/CS/unknown), level=None, semester inference, course outcomes, improve-retake counting |
| Tests 44–50 | 7 | Real-record integration: STU000001/005/017/026/041/100 — full invariant checks |
| Tests 51–56 | 36 | Phase 2 audit: global inference, active-blank in_progress, terminal-tag exclusion, `_is_active_blank` parametrized (11 cases), FW grade variants, `_clean_grade` normalization (19 cases) |

**Supporting test suites:**

- `tests/test_ale_adapter.py` + `tests/smoke_test_ale_adapter.py`: 74 tests, 0 failures — confirms ALE adapter compatibility with `credit_hours=None`

---

## 11. Final Verdict

**PASS / LOCKED WITH INTEGRATION CARRY-FORWARD NOTES**

SCP correctly extracts, normalizes, and classifies registrar facts.
All 9 logic fixes and the global current-semester refactor are complete.
90 tests pass.  No logic regressions in ALE/adapter.

Integration responsibilities carried forward to the Orchestrator audit:

1. Block track-dependent flows when `track_status == "unsupported"`
2. Enrich course names from KG before student-facing answers
3. Always provide `kg_data["course_credit_lookup"]` for ALE credit-based operations
4. Do not use `retake_count` as `improve_retake_number`
5. Confirm Forced Withdraw+Abs policy with supervisor/registrar
