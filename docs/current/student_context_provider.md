# `gateway/student_context_provider.py`

## 1. Purpose
Loads the student record from disk and turns it into a normalized
`StudentContext`. Computes the derived course buckets (`completed_courses`,
`failed_courses`, `in_progress_courses`) that the KG operations expect. Caches
per-process per-id so repeated calls are free.

It is intentionally *not* responsible for academic calculations — credit
limits, graduation eligibility, GPA simulation are explicitly out of scope.

## 2. What's Inside
- `_REPO_ROOT`, `_DATA_PATH` — resolved relative to the package so the path
  is correct regardless of cwd; `STUDENT_DATA_PATH` overrides.
- `StudentContextProvider` with:
  - `get_student(student_id)` — public entry point. Never raises.
  - `_load_record()` — the single I/O surface; the only thing that changes
    when moving to a real database.
  - `_parse_course_history(...)` — strict parse, returns `None` on the first
    invalid row (partial transcripts are worse than no transcript).
  - `_compute_derived_views(...)` — splits the course history by status.
  - `_build_context(...)` — wires raw + derived data into the schema.

## 3. Inputs / Outputs
- Input: `student_id: str`.
- Output: `StudentContext | None`. `None` for unknown ids, missing files,
  or invalid records.

## 4. Who Calls It
- `gateway/main.py` (and `SessionManager`'s constructor for forward
  compatibility).
- `gateway/tests/test_t02_t03.py`.

## 5. What It Calls
- File system: `data/student_profile.json` by default. Override path via
  `STUDENT_DATA_PATH`.

## 6. Debugging / Tracing
- ERROR logs on I/O / JSON / parse failures; WARNING when an entry has an
  unrecognized status.
- Common failure modes:
  - "File not found at …" — `STUDENT_DATA_PATH` mis-set or repo missing the
    JSON.
  - "student_id=… not found in record" — the demo JSON only ships one
    student.
  - "course_history parse error" — a row in the JSON is missing fields or
    has an invalid value; the entire context fails to load.

## 7. What NOT To Put In It
- Session-level state (overrides, last_referenced, planned_courses) — that
  is `SessionManager`'s territory.
- Mutations to the base record. The cached `StudentContext` is shared across
  requests.
- Academic logic (eligibility, GPA, graduation). These belong to the future
  Academic Logic Engine.
