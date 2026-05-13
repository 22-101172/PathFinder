# `gateway/session_manager.py`

## 1. Purpose
In-memory store for conversational state and the layer that merges session
overrides into the effective student context for each turn. It owns the
*runtime* `SessionState` dataclass — distinct from the API-facing
`SessionState` in `gateway/models/schemas.py`.

## 2. What's Inside
- `SessionState` dataclass (runtime): `session_id`, `active_student_id`,
  `created_at`, `last_updated`, `turn_count`, `last_referenced`, `overrides`.
- `SessionManager` class with:
  - `get_or_create_session(student_id, session_id?)` — creates a fresh
    session if needed and refuses cross-student reuse.
  - `get_session(session_id)`.
  - `apply_overrides(session_id, overrides_dict)` — extends
    `added_courses`, replaces `target_role`.
  - `update_last_referenced(session_id, course_code?, role_id?, workflow?)` —
    selective updates only.
  - `build_effective_context(base_context, session_id)` — returns a
    `StudentContext` copy with `planned_courses` reflecting the session's
    `added_courses`. Never mutates `base_context`.
  - `record_turn(session_id)`.
  - Private `_get_session`, `_set_session`, `_new_session` — the only
    swap-points for a future Redis migration.

## 3. Inputs / Outputs
- Inputs: student ids, session ids, override dicts shaped like
  `{"added_courses": [...], "target_role": "..."}`.
- Outputs: a session id (string), `SessionState` instances, and effective
  `StudentContext` objects.
- Never returns or raises — missing-session updates log a warning and
  silently no-op.

## 4. Who Calls It
- `gateway/main.py` — every request.
- `gateway/tests/test_t02_t03.py` — covers most of its behaviour.

## 5. What It Calls
- Reads `StudentContextProvider` only for forward compatibility; the public
  methods take `base_context` directly, so no provider call is currently made.

## 6. Debugging / Tracing
- The session manager logs warnings when it has to rotate or create sessions:
  - "Session … belongs to student … not …; creating a new session." — caller
    sent a session id that belongs to another student.
  - "Session … not found — creating a new session for student …" — stale id
    (probably from a server restart).
- Common failure modes:
  - **Overrides not visible to KG** — the orchestrator reads
    `effective_context.planned_courses`, which is populated by
    `build_effective_context`. If overrides do not show up, confirm
    `apply_overrides` was called between QU and orchestrator.
  - **`last_referenced` not updated** — `main.py` updates it AFTER the
    orchestrator returns, using the *original* `structured_query.entities`.
    If QU returned no entity, the field stays as the previous value.

## 7. What NOT To Put In It
- Any interpretation of raw user text. Override detection is QU's job; the
  manager only APPLIES.
- Engine calls.
- Permanent mutation of the base `StudentContext`. The base context is
  cached and shared by the provider.
- Academic calculations (eligibility, GPA, graduation, credit-limits).
