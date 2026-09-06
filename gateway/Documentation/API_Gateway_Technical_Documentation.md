# API Gateway — Technical Documentation

**Component:** `main.py`
**Phase 1 Step:** 9
**Status:** COMPLETE ✅ LOCKED (2026-06-24)

---

## Purpose and Responsibility

The API Gateway is the FastAPI application entry point. It owns:

- Application lifecycle (startup/shutdown via `lifespan`)
- Component initialization (KG, RAG, ALE, Orchestrator, Composer, rule bundles, entity resolver)
- Request validation via Pydantic schemas (`QueryRequest`)
- Dispatching `/chat` through the SCP → QU → Orchestrator → Composer pipeline
- Returning `QueryResponse` to clients
- Session management endpoints (list, load history, delete)
- Dev-only bulk-delete endpoints behind `APP_ENV=dev` / `DEV_MODE=true` guard
- Safe error handling and privacy-respecting logging

## What the API Does NOT Own

- Intent classification (owned by QU)
- Entity resolution logic beyond injecting the KG resolver into QU
- Academic logic: GPA, eligibility, graduation policy (owned by ALE)
- Final answer narration (owned by ResponseComposer)
- UI behavior or rendering

---

## Lifecycle / Startup

Implemented via `@asynccontextmanager async def lifespan(app)`:

1. `load_dotenv(override=True)` — loaded at module top, before app creation
2. `load_excel(excel_path)` — SCP student data loaded once
3. `KGAdapter()`, `RAGAdapter()`, `ALEAdapter()` — adapters instantiated
4. `Orchestrator(kg, rag, ale)` and `ResponseComposer()` — wired
5. `_rag.get_rule_bundles()` — RAG rule bundles loaded once; warning if empty
6. `_make_resolver(kg)` — KG entity resolver created; disabled safely if KG unavailable
7. On shutdown: `_kg.close()` called if KG was initialized

Startup failures are logged. If any adapter raises during init, FastAPI will log the exception; the app will not serve `/chat` calls reliably. The 503 guard in `/chat` catches this case.

---

## `/chat` Pipeline

```
POST /chat (QueryRequest)
  → 503 if _orchestrator or _composer is None
  → get_context(student_id) → 404 if not found
  → get_or_create_session(...)
  → [try block]
      understand_query(user_text, last_referenced, recent_turns, resolver)
      → list[StructuredQuery]
      Orchestrator.execute_turn(sqs, session, rule_bundles)
      → TurnWrapper
      ResponseComposer.compose(user_text, turn, session_id, session_name)
      → QueryResponse
  → [except] → HTTP 500 (no stack trace exposed)
  → merge_turn_overrides(sqs) → (new_overrides, had_clear)
  → Orchestrator.extract_last_referenced(sqs) → LastReferenced
  → update_session_after_turn(...)  — stores qr.answer_text (not stale placeholder)
  → return QueryResponse
```

Key invariants:
- `answer_text` stored in history is always the Composer's output, never a stub
- `replace_overrides=had_clear` wires the clear action correctly
- `recent_turns` passes the last `QU_CONTEXT_TURNS` (default: 5) turns to QU
- `resolver=_resolver` is always passed; may be `None` if KG is unavailable

---

## Session Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/sessions/{student_id}` | List all sessions for a student |
| `GET` | `/students/{student_id}/sessions/{session_id}/history` | Ownership-safe session history |
| `DELETE` | `/students/{student_id}/sessions/{session_id}` | Delete one session (ownership verified) |
| `GET` | `/session/{session_id}/history` | **DEPRECATED** — returns 410 Gone |

Ownership is enforced at the Session Manager layer: `get_session_history_for_student` and `delete_session_for_student` both verify `session.student_id == student_id` before returning data.

---

## Dev-Only Endpoints

| Method | Path | Guard |
|--------|------|-------|
| `DELETE` | `/dev/students/{student_id}/sessions` | `APP_ENV=dev` or `DEV_MODE=true` |
| `DELETE` | `/dev/sessions` | `APP_ENV=dev` or `DEV_MODE=true` |

Both return `403 Forbidden` when the guard is not satisfied. The guard is checked in `_is_dev_mode()` which reads env vars at call time (not cached), so changing env vars at runtime takes effect immediately.

---

## Schemas

**Input:** `QueryRequest`
```python
class QueryRequest(BaseModel):
    session_id: Optional[str] = None
    user_text: str
    student_id: str
```

**Output:** `QueryResponse`
```python
class QueryResponse(BaseModel):
    session_id: str
    session_name: str
    answer_text: str
    citations: list[Citation] = []
    status: Literal["ok", "error", "clarification_needed"] = "ok"
```

Fields `answer`, `llm_used`, `model_used` do not exist in `QueryResponse` and must not be used in API responses or UI consumers.

---

## Error Handling

| Scenario | HTTP Code | Detail |
|----------|-----------|--------|
| Student not found | 404 | `"Student 'X' not found"` |
| Orchestrator/Composer None | 503 | `"Service not ready"` |
| Pipeline exception (QU/Orch/Composer) | 500 | `"Internal pipeline error"` |
| Invalid request body | 422 | FastAPI default (Pydantic validation) |
| Dev endpoint outside dev mode | 403 | `"This endpoint is only available in dev mode."` |
| Session not found / wrong owner | 404 | `"Session not found"` |

Stack traces are never exposed in error responses. The `except HTTPException: raise` re-raises intentional HTTP errors; all other exceptions are caught and logged as `type(exc).__name__` only.

---

## Logging Privacy

**Included in logs:**
- Student ID truncated to first 3 chars + `***` (via `_mask_student_id`)
- Session ID truncated to first 8 chars
- Query length (not raw text)
- Intent/status map per turn
- Answer length (not raw text)
- Duration is available via FastAPI middleware if added later

**Never logged:**
- Raw `user_text`
- Full `answer_text`
- Student name, transcript, grades, CGPA
- Full LLM prompts
- Full narration packets

---

## Tests

File: `tests/test_main.py` — **34 tests, all pass**

Coverage:
- `/health` returns 200
- `/chat` returns correct `QueryResponse` shape and fields
- `/chat` calls QU → Orchestrator → Composer in order with correct args
- `/chat` stores Composer's `answer_text` in history (not stub)
- `/chat` passes `recent_turns`, `last_referenced`, `resolver`, `rule_bundles` correctly
- `/chat` returns 404 for unknown student
- `/chat` returns 503 when `_orchestrator` or `_composer` is `None`
- `/chat` returns 500 on pipeline exception without leaking stack trace
- `/chat` handles `replace_overrides` flag and `new_last_referenced`
- Session list, ownership-safe history, ownership-safe delete
- Dev endpoints blocked outside dev mode, functional in dev mode
- Deprecated `/session/{id}/history` returns 410

---

## Carry-Forwards

- **Startup failure visibility:** If an adapter (KG, RAG, ALE) raises during `lifespan`, FastAPI logs the traceback but continues. `/chat` calls will hit the 503 guard. A future improvement: log each component's init status individually and expose it in `/health`.
- **`_make_turn_history_summary`:** Retained for test compatibility; no longer called in the main pipeline (Composer replaced it). Can be removed in a future cleanup pass.
- **CORS policy:** Currently `allow_origins=["*"]`. For production, restrict to the Streamlit UI origin.
