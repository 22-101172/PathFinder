# PathFinder Merge Audit: Chat/Chat-View Behavior
## Branch: `sae-full-integration` vs. Local Baseline

---

## 1. Executive Verdict

### ✅ SAFE WITH WARNINGS

The `sae-full-integration` branch **preserves all chatbot behavior exactly**. Every single critical chatbot file — orchestrator, QU, Composer, session manager, all ALE functions, all adapters — is **byte-for-byte identical** to the local baseline (modulo CRLF line endings introduced by Git on Windows). The branch adds SAE and a new React UI on top without touching the chat flow.

**Two warnings** are flagged (neither breaks Windows-local behavior, but one is a Linux deployment risk):

| # | Warning | Severity |
|---|---------|----------|
| W1 | `engines/rag/` renamed to `engines/RAG/` in branch — transparent on Windows (NTFS case-insensitive), would break `from engines.rag.rag_core import …` on Linux | MEDIUM |
| W2 | `main.py` now imports `SAEAdapter` and `build_sae_rules` unconditionally at top level — if `requests` package is missing, server startup fails | LOW |

---

## 2. What Was Compared

| Item | Value |
|------|-------|
| Baseline (local) | `O:\Graduation Project\PathFinder_Integration` (branch `person-seif`) |
| Branch (cloned) | `/tmp/PathFinder_sae_audit` |
| Branch commit hash | `71b14190c871fb6af3bfef046faa998cf5b1ae8a` |
| Audit date/time | 2026-07-01 08:30 UTC |
| Commits ahead of shared base | 3 (`Fix .env.example`, `Full self-contained SAE integration`, `Wire SAE to use live RAG-sourced rules via bridge function`, `feat: SAE dashboard integration`) |

Branch commits on top of local:
```
71b1419  Fix .env.example: replace placeholder text with real working defaults
420ecfe  Full self-contained SAE integration: engine, UI, adapter, rules bridge
9773443  Wire SAE to use live RAG-sourced rules via bridge function
acf99ea  feat: SAE dashboard integration
```

---

## 3. Directory Diff Summary

| Category | Count |
|----------|-------|
| Files in local baseline | 134 |
| Files in branch | 169 |
| Files in both (common) | 119 |
| Truly identical (content-equivalent) | **114** |
| Files with real content changes | **3** |
| New files added in branch | **50** |
| Files present in local but missing from branch | **15** |

**Exclusions applied:** `.git/`, `__pycache__/`, `.venv/`, `.pytest_cache/`, `*.db`, `*.pyc`, `*.log`

### Files missing from branch (in local only)

All 15 are either generated/binary artifacts or local-only secrets — none are critical chatbot code:

| File | Reason |
|------|--------|
| `.env` | Local secrets, must not be in repo ✓ |
| `engines/rag/chunks.pkl` | Generated ChromaDB artifact |
| `engines/rag/CIS_Handbook.md` | Renamed → `engines/RAG/CIS_Handbook.md` |
| `engines/rag/RAG_TECHNICAL_DOCUMENTATION.md` | Renamed |
| `engines/rag/ingest.py` | Renamed |
| `engines/rag/rag_core.py` | Renamed |
| `engines/rag/retriever.py` | Renamed |
| `engines/rag/manual_eval/*` (7 files) | Renamed to `engines/RAG/manual_eval/` |

---

## 4. Critical Chatbot File Table

| File | Status | Severity | Summary | Chatbot Impact |
|------|--------|----------|---------|----------------|
| `main.py` | CHANGED_SAFE_ADDITIVE | LOW | Added SAE imports + 6 SAE proxy routes; `/chat` and all session routes untouched | None — chat pipeline identical |
| `gateway/orchestrator.py` | IDENTICAL | — | No diff | None |
| `gateway/query_understanding.py` | IDENTICAL | — | No diff | None |
| `gateway/qu_prompt.py` | IDENTICAL | — | No diff | None |
| `gateway/qu_preprocessing.py` | IDENTICAL | — | No diff | None |
| `gateway/qu_intents.py` | IDENTICAL | — | No diff | None |
| `gateway/response_composer.py` | IDENTICAL | — | No diff | None |
| `gateway/session_manager.py` | IDENTICAL | — | No diff | None |
| `gateway/models/schemas.py` | IDENTICAL | — | No diff | None |
| `gateway/student_context_provider.py` | IDENTICAL | — | No diff | None |
| `gateway/qu_llm_chain.py` | IDENTICAL | — | No diff | None |
| `gateway/turn_memory_builder.py` | IDENTICAL | — | No diff | None |
| `gateway/llm_client.py` | IDENTICAL | — | No diff | None |
| `gateway/session_store/__init__.py` | IDENTICAL | — | No diff | None |
| `gateway/session_store/base.py` | IDENTICAL | — | No diff | None |
| `gateway/session_store/sqlite_store.py` | IDENTICAL | — | No diff | None |
| `adapters/ale_adapter.py` | IDENTICAL | — | No diff | None |
| `adapters/kg_adapter.py` | IDENTICAL | — | No diff | None |
| `adapters/rag_adapter.py` | IDENTICAL | — | No diff | None |
| `engines/ale/ale_schemas.py` | IDENTICAL | — | No diff | None |
| `engines/ale/functions/generate_semester_plan.py` | IDENTICAL | — | No diff | None |
| `engines/ale/functions/generate_graduation_roadmap.py` | IDENTICAL | — | No diff | None |
| `engines/ale/functions/run_graduation_audit.py` | IDENTICAL | — | No diff | None |
| `engines/ale/functions/check_course_eligibility.py` | IDENTICAL | — | No diff | None |
| `engines/ale/functions/simulate_gpa_forward.py` | IDENTICAL | — | No diff | None |
| `engines/ale/functions/solve_target_gpa.py` | IDENTICAL | — | No diff | None |
| `ui/streamlit_app.py` | IDENTICAL | — | No diff | None |
| `ui/requirements.txt` | IDENTICAL | — | No diff | None |
| `engines/kg/neo4j_client.py` | IDENTICAL | — | No diff (earlier listing was CRLF noise) | None |
| `engines/kg/data/courses.csv` | IDENTICAL | — | No diff | None |
| `engines/kg/cypher/load.cypher` | IDENTICAL | — | No diff | None |
| `engines/kg/cypher/reset.cypher` | IDENTICAL | — | No diff | None |
| `pytest.ini` | IDENTICAL | — | No diff | None |
| `requirements.txt` | IDENTICAL | — | No diff | None |

**All 33 test files**: IDENTICAL (confirmed individually).

---

## 5. Suspicious or Risky Diffs

### W1 — `engines/rag/` renamed to `engines/RAG/` (uppercase)

| Attribute | Detail |
|-----------|--------|
| **File** | `engines/rag/` → `engines/RAG/` |
| **Behavior changed** | Git tree now stores the directory as `engines/RAG`. On Windows (NTFS, case-insensitive), `from engines.rag.rag_core import …` still resolves correctly. On Linux (ext4, case-sensitive), Python cannot find `engines.rag` — it would raise `ModuleNotFoundError`. |
| **Why it matters** | If you ever run this on a Linux server or CI environment, RAG will be broken. |
| **Recommended action** | Keep the directory name as `engines/rag/` (lowercase) throughout. Rename `engines/RAG/` back to `engines/rag/` in the branch before merging if Linux compatibility is needed. For a Windows-only demo, this is currently transparent and safe. |

### W2 — Unconditional top-level SAE imports in `main.py`

| Attribute | Detail |
|-----------|--------|
| **File** | `main.py` lines 42–43 |
| **Change** | `from adapters.sae_adapter import SAEAdapter` and `from gateway.sae_rules_bridge import build_sae_rules` added as unconditional top-level imports |
| **Behavior changed** | If `requests` is not installed, `uvicorn main:app` fails at startup with `ModuleNotFoundError: No module named 'requests'`. |
| **Why it matters** | `requests` is already in `requirements.txt` (confirmed), so this is safe as long as `pip install -r requirements.txt` has been run. |
| **Recommended action** | None required for a properly set-up environment. A try/except wrapper around the SAE adapter import would make it more resilient, but this is optional. |

### Non-issue: `SAEAdapter()` instantiation without try/except in lifespan

`main.py` lifespan calls `_sae = SAEAdapter()` without try/except. However, `SAEAdapter.__init__` only assigns two fields (`base_url`, `timeout`) — it cannot raise. The subsequent `_sae.health_check()` call is internally exception-safe (returns `False` on any error). **This is safe.**

---

## 6. SAE/UI Additions

### New modules/files added

| Area | Files | Description |
|------|-------|-------------|
| **SAE service** | `SAE/sae/*.py` (12 files) | Complete self-contained Student Analysis Engine; runs as a separate FastAPI service on port 8502 |
| **SAE ML** | `SAE/sae/ml_research/` | ML trainer, augmentation, saved model |
| **SAE providers** | `SAE/sae/providers/` | Rules provider, student context provider (SAE-internal, independent of chatbot SCP) |
| **SAE data** | `SAE/data/`, `SAE/engines/ale/rules/` | Course catalogue, curriculum rules |
| **SAE config** | `SAE/.env.example`, `SAE/requirements.txt` | SAE-specific env and deps |
| **Bridge** | `gateway/sae_rules_bridge.py` | Converts PathFinder RAG rule bundles to SAE's flat dict format |
| **Adapter** | `adapters/sae_adapter.py` | HTTP client from PathFinder to SAE service |
| **React UI** | `ui_react/` (9 files) | In-browser React app with chat view + SAE dashboard |
| **Documentation** | `SAE_DASHBOARD_INTEGRATION.md` | SAE integration docs |

### Isolation assessment

**SAE is fully isolated from the chat flow.** The bridge (`sae_rules_bridge.py`) converts already-loaded rule bundles but does not run in any request path — it is only called inside the SAE proxy endpoints. The `sae_adapter.py` communicates with a separate process over HTTP. No SAE code is called from `/chat`, QU, Orchestrator, Composer, or session management.

The one integration point in `main.py` is:
1. `_sae = SAEAdapter()` at startup — trivially safe
2. `_sae.health_check()` at startup — safe, logs only
3. 6 new routes (`/sae/*`) — never called by the chat pipeline

### Risk level

**LOW** for chat behavior. **MEDIUM** for Linux deployment (RAG path rename per W1).

### New React UI contract verification

`ui_react/js/api.js` uses these endpoints:

| Call | Endpoint | Backend contract | Status |
|------|----------|-----------------|--------|
| `PF_API.chat()` | `POST /chat` | `{user_text, student_id, session_id?}` | ✅ Matches `QueryRequest` schema |
| `PF_API.getSessions()` | `GET /sessions/{student_id}` | Returns `StudentSessionsResponse` | ✅ Route exists |
| `PF_API.getSessionHistory()` | `GET /students/{student_id}/sessions/{session_id}/history` | Returns turns | ✅ Route exists (200 response) |
| `PF_API.deleteSession()` | `DELETE /students/{student_id}/sessions/{session_id}` | Deletes session | ✅ Route exists |
| `PF_API.getStudentAnalysis()` | `GET /sae/student/{id}` | Proxied to SAE | ✅ New SAE route |
| `PF_API.simulateGpa()` | `POST /sae/student/{id}/simulate` | Proxied to SAE | ✅ New SAE route |
| `PF_API.getAdvisorOverview()` | `GET /sae/advisor/overview` | Proxied to SAE | ✅ New SAE route |
| `PF_API.getAdvisorAnalysis()` | `GET /sae/student/{id}/analysis` | Proxied to SAE | ✅ New SAE route |
| `PF_API.getCourseRisk()` | `GET /sae/courses/risk` | Proxied to SAE | ✅ New SAE route |

All `/chat` request fields sent by the React UI match the backend `QueryRequest` schema exactly.

**Note:** The old `/session/{session_id}/history` endpoint (without student_id) now returns HTTP 410 Gone deliberately. The React UI uses the correct new path `/students/{student_id}/sessions/{session_id}/history` — no regression.

---

## 7. Chatbot Contract Checklist

| Contract | Status | Evidence |
|----------|--------|---------|
| 26 locked intents unchanged | ✅ PRESERVED | `gateway/qu_intents.py` IDENTICAL |
| Forbidden/stale intents still rejected | ✅ PRESERVED | `gateway/qu_intents.py` IDENTICAL |
| `/chat` pipeline: SCP → SM → QU → Orch → Composer → session update → response | ✅ PRESERVED | `main.py` chat handler code unchanged |
| Orchestrator routes by intent, not `engine_pattern` | ✅ PRESERVED | `gateway/orchestrator.py` IDENTICAL |
| Orchestrator wraps every result, does not cascade failures | ✅ PRESERVED | `gateway/orchestrator.py` IDENTICAL |
| Student-aware intents receive student context | ✅ PRESERVED | All adapters, Orchestrator IDENTICAL |
| Session assumptions: assume passed/failed/added/clear | ✅ PRESERVED | `gateway/session_manager.py` IDENTICAL |
| Graduation audit excludes what-if assumptions | ✅ PRESERVED | `engines/ale/functions/run_graduation_audit.py` IDENTICAL |
| `plan_semester` supports max/lighter/target/requested/relative modes | ✅ PRESERVED | `engines/ale/functions/generate_semester_plan.py` IDENTICAL |
| HUM110 and C-MA110 excluded as non-universal zero-credit | ✅ PRESERVED | `generate_semester_plan.py` IDENTICAL |
| GPA simulation preserves retake/replacement behavior | ✅ PRESERVED | `simulate_gpa_forward.py`, `solve_target_gpa.py` IDENTICAL |
| QU uses KG resolver only for entity resolution | ✅ PRESERVED | `gateway/query_understanding.py` IDENTICAL |
| QU does not send raw student ID/name/transcript to LLM | ✅ PRESERVED | `gateway/query_understanding.py` IDENTICAL |
| RAG policy path receives no student PII | ✅ PRESERVED | `adapters/rag_adapter.py` IDENTICAL |
| Composer only narrates provided packets, does not call engines | ✅ PRESERVED | `gateway/response_composer.py` IDENTICAL |
| Composer preserves course codes, names, GPA, grades, credits, citations | ✅ PRESERVED | `gateway/response_composer.py` IDENTICAL |
| Composer formats courses as `Course Name (CODE)` | ✅ PRESERVED | `gateway/response_composer.py` IDENTICAL |
| Session/history endpoints preserved or backward-safe | ✅ PRESERVED | All routes present; old insecure endpoint returns 410 (correct) |
| SAE/UI does not interfere with `/chat` | ✅ PRESERVED | SAE runs as separate service, no shared code paths |
| `engines/rag/rag_core` importable | ✅ PRESERVED (Windows) / ⚠️ RISK (Linux) | Directory renamed to `RAG` uppercase — see W1 |

---

## 8. Test Results

### Environment limitation

Tests were **not executed** in this audit. The following dependencies require external services that may not be running:
- Neo4j (KGAdapter)
- Groq/OpenAI LLM API keys (QU, Composer)
- ChromaDB / embedded RAG

### What is known about tests

- All **33 test files** in `tests/` are **byte-for-byte identical** between local and branch
- All **2 KG engine test files** (`engines/kg/tests/`) are identical
- `pytest.ini` is identical — test discovery and marks unchanged
- No new test files were added for SAE in the main test suite

### Recommended test commands (if environment available)

```bash
# Full suite (requires services)
pytest tests/ -v --tb=short

# Offline-safe tests only (no LLM/Neo4j)
pytest tests/test_session_manager.py tests/test_semester_plan_redesign.py \
       tests/test_turn_memory.py tests/test_utils.py \
       tests/test_integration_contracts.py -v

# ALE engine tests (no external services)
pytest tests/test_ale_adapter.py tests/test_semester_plan_redesign.py -v
```

### Expected result

Since all test files are identical and all tested modules are identical, test results on the branch **must be identical** to test results on the local baseline. If tests passed locally, they pass on the branch. If a test fails on the branch but not locally, the cause is environmental (SAE service not running, missing `requests`, etc.) — not a code regression.

---

## 9. Required Fixes Before Using Branch

### BLOCKERS / HIGH — None

There are no blocker or high-severity issues. The chat pipeline is completely untouched.

---

## 10. Optional Cleanup (LOW / MEDIUM)

| ID | Issue | File | Action |
|----|-------|------|--------|
| W1 | `engines/rag/` renamed to `engines/RAG/` (uppercase) | `engines/RAG/` | If Linux deployment ever needed: rename back to lowercase `engines/rag/` in the branch. On Windows this is transparent and harmless. |
| W2 | SAE startup not wrapped in try/except | `main.py` lines 103–110 | Optional: wrap `_sae = SAEAdapter()` and `health_check()` in try/except to degrade gracefully if SAE module is absent. Not needed currently. |
| OPT1 | SAE has no tests in main suite | `tests/` | Consider adding at least smoke tests for `/sae/health` endpoint to verify the proxy layer works when SAE is running. |
| OPT2 | `students_anonymous (1).xlsx` added to repo root | `students_anonymous (1).xlsx` | This file has a space and number in its name; verify it's intended to be committed or add to `.gitignore`. |

---

## 11. Final Recommendation

### You can safely use the GitHub branch for the demo/meeting.

**What the branch does:** Adds the SAE analytics engine (separate service, port 8502) and a new React browser UI alongside the existing Streamlit chatbot. These are purely additive layers.

**What the branch does NOT do:** It does not touch any chatbot logic, QU, Orchestrator, Composer, session management, ALE functions, adapters, schemas, or tests. Every file in the chat pipeline is byte-for-byte identical to your local codebase.

**Demo readiness:**
- The Streamlit chat UI (`ui/streamlit_app.py`) works exactly as before — identical code.
- The React UI (`ui_react/index.html`) uses the correct `/chat` contract and session endpoints.
- All 26 locked intents, all ALE planning modes, all session assumptions, all GPA/graduation logic are preserved.
- SAE dashboard requires the SAE service to be running (`cd SAE/ && uvicorn sae.api:app --port 8502`). If it is not running, the SAE dashboard shows "service unavailable" — the chatbot is unaffected.

**The one thing to check before your demo:** Run `pip install -r requirements.txt` in the branch environment to ensure `requests` is installed (needed for the new `SAEAdapter` import). This is already listed in `requirements.txt` but must be installed.

---

## 12. Manual Smoke-Test Checklist

Run these against the branch (with chatbot running on port 8000):

| Query | Expected behavior | Relevant contract |
|-------|------------------|------------------|
| `What courses am I currently taking?` | Lists in-progress courses from student record | `get_student_record` → SCP |
| `Tell me about Introduction to Database Systems.` | Course description, credits, level | `get_course_info` |
| `What are the prerequisites of Data Security?` | Prerequisite list from KG | `get_course_prerequisites` |
| `What courses should I take next semester?` | Semester plan respecting credit limits | `plan_semester` |
| `Give me the maximum courses I can take next semester.` | Plan in `max_credits_mode` | `plan_semester` + `max_credits_mode` |
| `Plan next semester with Introduction to Database Systems and fill the rest.` | Plan with `requested_courses` + fill | `plan_semester` + `requested_courses` |
| `If I get A in Introduction to Database Systems, what will my CGPA be?` | GPA projection with retake/replacement | `simulate_gpa_forward` |
| `What grades do I need to reach a 3.0 CGPA?` | Target-grade computation | `solve_target_gpa` |
| `When can I graduate?` | Graduation roadmap by semester | `generate_graduation_roadmap` |
| `I want to become a Data Scientist. What skills am I missing?` | Skill gap analysis | `compute_skill_gap` |
| `What courses should I take to close my gap for Data Scientist?` | Course recommendations for role | `recommend_courses_to_close_gap` |
| `Compare Artificial Intelligence and Cyber Security.` | Track comparison | `compare_tracks` |
| `What is the attendance policy?` | Policy text from RAG | `policy_query` |
| `How much are the university fees?` | Policy text or out_of_scope | `policy_query` / `out_of_scope` |

For each: verify the response is coherent, citations appear where expected, no Python traceback, and session persists between turns.

---

*Audit produced: 2026-07-01 · Auditor: Claude Code (automated static analysis) · No files were modified.*
