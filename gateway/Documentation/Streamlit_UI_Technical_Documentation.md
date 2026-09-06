# Streamlit UI — Technical Documentation

**Component:** `ui/streamlit_app.py`
**Phase 1 Step:** 10
**Status:** COMPLETE ✅ LOCKED (2026-06-24)

---

## Purpose and Responsibility

The Streamlit UI is a thin demo interface. It owns:

- Student ID input and login state
- Chat input and message display
- Session list display, session switching, and per-session deletion
- Loading session history from the API
- Showing citations from responses
- Displaying friendly backend error messages

## What the UI Does NOT Own

- Academic logic (GPA, eligibility, graduation policy)
- Intent routing or classification
- Manual parsing of answer text for academic data
- Session management logic (delegated entirely to API)
- Authentication or access control beyond holding `student_id` in session state

---

## API Contract

**Base URL:** `os.getenv("PATHFINDER_API_URL", "http://localhost:8000")`

| Operation | Method | Endpoint | Request | Response |
|-----------|--------|----------|---------|----------|
| Chat | POST | `/chat` | `{student_id, user_text, session_id?}` | `QueryResponse` |
| List sessions | GET | `/sessions/{student_id}` | — | `StudentSessionsResponse` |
| Load history | GET | `/students/{student_id}/sessions/{session_id}/history` | — | `SessionHistoryResponse` |
| Delete session | DELETE | `/students/{student_id}/sessions/{session_id}` | — | `{"deleted": true, "session_id": ...}` |

**`QueryResponse` fields read by UI:**
- `answer_text` — displayed as assistant message
- `session_id` — stored in `st.session_state.session_id` after first response
- `session_name` — stored in `st.session_state.session_name`; triggers session list refresh
- `status` — `"ok"` / `"error"` / `"clarification_needed"` (not currently surfaced visually beyond error shape)
- `citations` — displayed in `📚 Sources` expander

**Fields NOT used:** `answer`, `llm_used`, `model_used` — these do not exist in `QueryResponse`.

---

## Session State Keys

| Key | Type | Description |
|-----|------|-------------|
| `student_id` | `str \| None` | Logged-in student ID |
| `session_id` | `str \| None` | Active session UUID; `None` for new chat |
| `session_name` | `str \| None` | Display name for active session |
| `messages` | `list[dict]` | `[{"role": "user"\|"assistant", "content": str}]` |
| `all_sessions` | `list[dict]` | Session summaries from `/sessions/{student_id}` |

---

## Chat Flow

1. User enters Student ID → `api_get_sessions(student_id)` loads session list → sidebar shows sessions
2. User types message → `api_chat(user_text, student_id, session_id)` called
3. On 200: `answer_text` displayed; `session_id` / `session_name` updated in state; session list refreshed if `session_name` is set
4. On non-200 or exception: friendly error message shown in assistant bubble (no raw JSON, no traceback)

**New chat:** clears `session_id`, `session_name`, `messages` but keeps `student_id` and `all_sessions`.

**Logout:** clears all state keys including `all_sessions`.

**Loading a session:** calls `api_load_history(student_id, session_id)` → maps turns to `messages` list:
```python
[
  {"role": "user", "content": turn["user"]},
  {"role": "assistant", "content": turn["answer"]},
  ...
]
```

---

## Citations Display

When `citations` is non-empty, a `📚 Sources` expander is shown:

```
- {source}{, p.{page} if page exists}
```

If no citations exist, the expander is hidden. Fabricated source sections are stripped by the Composer before the response reaches the UI.

---

## Error Handling

| Scenario | UI Behavior |
|----------|-------------|
| Backend unreachable | "Cannot reach backend: ..." in assistant bubble |
| Non-200 HTTP response | "Server error {code}: {text}" in assistant bubble |
| Session history load fails | Empty message list (silent; user sees blank chat) |
| Session list load fails | Empty sidebar list (silent; user can still chat) |

No raw tracebacks are shown. No raw JSON is rendered unless `st.write` / `st.json` is explicitly added (it is not).

---

## Session Delete Flow

Each session row in the sidebar renders as two columns (85/15 split):
- Left column: `💬 {session_name}` button — loads history into active chat
- Right column: `🗑` button — triggers per-session delete

On 🗑 click:
1. `api_delete_session(student_id, session_id)` called
2. If success: `api_get_sessions` re-fetches updated list; `_apply_delete_to_state` clears active-session fields if the deleted session was active
3. If failure: `st.warning("Could not delete session. Please try again.")` shown in sidebar
4. `st.rerun()` always called (to refresh the sidebar)

Cross-student deletion is prevented at the API layer (`DELETE /students/{student_id}/sessions/{session_id}` verifies ownership before deleting).

---

## Helper Functions

### `api_chat(user_text, student_id, session_id=None)`
- POSTs to `/chat` with 60-second timeout
- Returns parsed dict on 200 or safe error-shape dict on failure
- Never raises; always returns a dict with at minimum `answer_text`, `status`, `session_id`, `session_name`, `citations`

### `api_get_sessions(student_id)`
- GETs `/sessions/{student_id}` with 5-second timeout
- Returns `list[dict]` on 200 or `[]` on failure
- Never raises

### `api_load_history(student_id, session_id)`
- GETs `/students/{student_id}/sessions/{session_id}/history` with 5-second timeout
- Returns `(list[dict], str)` — messages and session name — on 200
- Returns `([], "")` on failure
- Uses the ownership-safe endpoint; deprecated `/session/{id}/history` is NOT called

### `api_delete_session(student_id, session_id)`
- DELETEs `/students/{student_id}/sessions/{session_id}` with 5-second timeout
- Returns `True` on HTTP 200, `False` on any other status or exception
- Never raises

### `_apply_delete_to_state(state, deleted_session_id)`
- Pure function (no Streamlit calls); takes and mutates a plain dict mirroring session state
- Removes the deleted session from `state["all_sessions"]`
- Clears `session_id`, `session_name`, `messages` if the deleted session was the active one
- Returns the mutated state dict

---

## Demo Limitations

- No authentication; any student ID can be entered
- Session list does not auto-refresh while another browser tab is active
- Long answers display correctly via `st.markdown` (Markdown rendered)
- `status="clarification_needed"` is not visually distinguished from `"ok"` — the Composer already converts clarification prompts to natural language
- No mobile layout optimization

---

## Tests

File: `tests/test_streamlit_app.py` — **21 tests, all pass**

Coverage:
- `api_chat` returns parsed payload on 200
- `api_chat` returns error shape on non-200 (status code in message)
- `api_chat` returns error shape on network exception
- `api_chat` omits `session_id` from POST body when `None`
- `api_chat` includes `session_id` in POST body when provided
- `api_get_sessions` returns session list on 200
- `api_get_sessions` returns `[]` on non-200
- `api_get_sessions` returns `[]` on exception
- `api_load_history` maps turns to user/assistant message dicts
- `api_load_history` returns `([], "")` on non-200
- `api_load_history` returns `([], "")` on exception
- `api_load_history` calls ownership-safe `/students/{sid}/sessions/{ses}/history`
- `api_load_history` returns empty messages for session with no turns
- `api_delete_session` returns `True` on 200
- `api_delete_session` returns `False` on non-200
- `api_delete_session` returns `False` on exception
- `api_delete_session` calls ownership-safe `DELETE /students/{sid}/sessions/{ses}`
- `_apply_delete_to_state` clears active session fields when active session deleted
- `_apply_delete_to_state` keeps active session untouched when deleting a different session
- `_apply_delete_to_state` removes deleted session from `all_sessions`
- `_apply_delete_to_state` returns the mutated state dict

No live backend required. Streamlit is fully mocked at import time.

**Manual smoke checks (Phase 1.5):**
- Enter STU000001 → sessions list appears in sidebar
- Send a chat message → response appears as assistant bubble
- Load a past session → message history reconstructed correctly
- Logout → all state cleared

---

## Carry-Forwards

- **`status` visual indicator:** `"clarification_needed"` and `"error"` statuses are not visually distinguished. A future improvement: add a warning banner or color indicator when `status != "ok"`.
- **Session list refresh on all new turns:** Currently refreshes only when `session_name` is truthy in the response. Since `session_name` is always populated for non-empty user queries, this works in practice but could be made explicit.
- **CORS:** The UI assumes the API is reachable at `PATHFINDER_API_URL`. No auth headers are sent; CORS must be open on the API side (`allow_origins=["*"]` is current behavior).
