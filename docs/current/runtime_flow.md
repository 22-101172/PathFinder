# Runtime Flow — How One `/query` Request Travels Through The Gateway

This file walks through one `POST /query` call from the moment it lands in
FastAPI to the moment it returns. Use it whenever you are debugging a request
or before you change a pipeline component, so you understand which layer is
allowed to do what.

For higher-level architecture see `README.md`. For per-file ownership see the
sibling files in this folder.

## The Pipeline At A Glance

```
HTTP
 └─ gateway/main.py — handle_query
     ├─ Phase 1  Preparation
     │   ├─ SessionManager.get_or_create_session
     │   ├─ StudentContextProvider.get_student
     │   └─ SessionManager.build_effective_context (+ get_session)
     │
     ├─ Phase 2  Understanding
     │   └─ QueryUnderstandingLayer.classify
     │       ├─ rule layer
     │       └─ LLM fallback (if configured)
     │
     ├─ Phase 2.5  Apply overrides detected by QU
     │   └─ SessionManager.apply_overrides  →  rebuild effective context
     │
     ├─ Phase 3  Execution
     │   └─ Orchestrator.run
     │       ├─ KGAdapter.call             (kg-only / student-aware / mixed-kg)
     │       └─ RAGAdapter.execute         (rag-only / mixed-rag)
     │
     └─ Phase 4  Presentation
         └─ ResponseComposer.compose
             ├─ LLM (if configured)
             └─ deterministic fallback
```

## Step-By-Step Walkthrough

### Phase 1 — Preparation
1. `SessionManager.get_or_create_session` looks up the session id supplied by
   the caller. It also rotates the session if the id belongs to a different
   student (a deliberate protection — see session_manager.md).
2. `StudentContextProvider.get_student` loads the student record from
   `data/student_profile.json` and computes the derived buckets
   `completed_courses`, `failed_courses`, `in_progress_courses`. If the
   student does not exist, the gateway returns HTTP 404.
3. `SessionManager.build_effective_context` returns a NEW `StudentContext`
   that includes any session overrides accumulated so far. The base context
   is never mutated.

### Phase 2 — Understanding
4. `QueryUnderstandingLayer.classify` interprets the raw user text:
    - **Rule layer**: keyword-ordered intent table + KG-data alias resolution
      for entities. Validates that any required entity (e.g. `course_code`
      for prerequisites) was extracted; if not, it returns an ambiguous
      `StructuredQuery` with a clarification prompt and never proceeds.
    - **LLM fallback**: only consulted when the rule layer cannot match an
      intent AND an `LLMClient` is configured. The prompt carries only the
      user text, alias hints, and non-personal session hints — never PII.
    - **Override detection** runs unconditionally — even when the query is
      ambiguous — so a target-role declaration in an otherwise empty
      sentence still gets recorded.

### Phase 2.5 — Apply overrides
5. If the QU output contains overrides, `SessionManager.apply_overrides`
   merges them into the session state and the gateway rebuilds the
   effective context. Critically, QU **detects** overrides and the session
   manager **applies** them — never the other way around.

### Phase 3 — Execution
6. `Orchestrator.run` is a deterministic dispatcher. There is no LLM here.
    - `needs_clarification` → return a clarification ResultPackage.
    - `engine_pattern == "mixed"` → call both KG (if `course_code` is set)
      and RAG, then aggregate.
    - `query_type == "student_aware"` → call the appropriate KG operation
      with `completed_courses` (and `planned_courses` for
      `estimate_alignment_improvement`).
    - `engine_pattern == "kg"` → single KG call.
    - `engine_pattern == "rag"` → single RAG call with the original user
      text and no student context.
7. KG errors arrive in two shapes (`{"error": "..."}` from
   `engines/kg/queries.py` and `{"status": "error", "message": "..."}`
   from the adapter wrappers). The orchestrator handles both.

### Phase 4 — Presentation
8. `ResponseComposer.compose` produces the user-visible answer:
    - `clarification_needed` and `error` paths skip the LLM entirely and
      return canned, sanitized text.
    - `ok` path calls the LLM with a privacy-safe prompt containing the
      KG result, RAG answer + citations, and a tiny non-personal student
      summary (`track_id`, `level`, `current_semester` only).
    - If the LLM is not configured or fails, a deterministic fallback
      formats the KG/RAG data directly so the request still succeeds.
9. The gateway fills in the `session_id` on the response, records the turn,
   and returns the `QueryResponse`.

## Log Lines To Watch

Every request emits at least these INFO log lines, in order:

```
gateway.request.received  student=<hash> session=<present|none> text_len=<n>
qu.classified             layer=<rule|llm|fallback> intent=… engine=… type=…
orchestrator.workflow     workflow=<kg_only|rag_only|mixed|student_aware|clarification> intent=… status=…
composer.mode             mode=<ok|clarification|error>   (DEBUG)
gateway.response.sent     student=<hash> session=<id> status=<ok|error|clarification_needed>
```

Set `LOG_LEVEL=DEBUG` (or pass `--log-cli-level=DEBUG` to pytest) to also see
adapter call summaries and composer LLM outcomes.

## Hard Boundaries (Worth Repeating Here)

- QU is the **only** layer that interprets raw user text.
- SessionManager applies overrides; it never detects them.
- Orchestrator is deterministic — no LLM, no Neo4j, no `engines.kg.queries`
  import.
- ResponseComposer is presentation-only. It must not invent facts, change
  decisions, or make eligibility/graduation/GPA claims.
- No external LLM call ever receives student PII.
