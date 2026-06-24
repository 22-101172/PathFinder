# Config / Startup / README — Technical Documentation

**Step:** 11 — Config / Startup / README Audit
**Status:** COMPLETE ✅ — PASS / CONFIG + STARTUP + README LOCKED
**Date:** 2026-06-24

---

## Scope

This document covers the configuration, startup, and documentation layer of PathFinder:

- `README.md` — project documentation and setup guide
- `.env.example` — environment variable template
- `main.py` — FastAPI backend entrypoint and startup lifecycle
- `ui/streamlit_app.py` — Streamlit frontend entrypoint
- `requirements.txt` — backend dependencies
- `pytest.ini` — pytest configuration

---

## Config Responsibilities

| Config area | Owner | Key env vars |
|---|---|---|
| LLM shared client | `gateway/llm_client.py` | `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS` |
| Query Understanding model chain | `gateway/qu_llm_chain.py` | `QU_PRIMARY_MODEL`, `QU_FALLBACK_MODELS`, `QU_TIMEOUT_SECONDS` |
| Response Composer model chain | `gateway/response_composer.py` | `COMPOSER_USE_LLM`, `COMPOSER_PRIMARY_MODEL`, `COMPOSER_FALLBACK_MODELS`, `COMPOSER_TIMEOUT_SECONDS` |
| RAG engine | `engines/RAG/rag_core.py` | `GROQ_API_KEY`, `RAG_GROQ_MODEL`, `GROQ_MODEL`, `RAG_FALLBACK_MODELS`, `RAG_TIMEOUT_SECONDS`, `RAG_RULE_BUNDLE_DELAY_SECONDS`, `RAG_REASONING_EFFORT` |
| RAG artifact paths | `engines/RAG/rag_core.py` | `RAG_HANDBOOK_PATH`, `RAG_CHROMA_DIR`, `RAG_CHUNKS_FILE` |
| Knowledge Graph | `engines/kg/neo4j_client.py` | `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `NEO4J_Import_folder` |
| Session store | `gateway/session_store/sqlite_store.py` | `SESSION_DB_PATH` |
| QU context window | `gateway/session_manager.py` | `QU_CONTEXT_TURNS` |
| Streamlit API URL | `ui/streamlit_app.py` | `PATHFINDER_API_URL` |
| Dev-only endpoints | `main.py` | `APP_ENV`, `DEV_MODE` |
| HuggingFace downloads | RAG embedding/reranker models | `HF_TOKEN` |

---

## Environment Variables

### Complete Reference

```env
# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<secret>
NEO4J_DATABASE=neo4j
NEO4J_Import_folder=/path/to/neo4j/import

# RAG / Groq
GROQ_API_KEY=<secret>
RAG_GROQ_MODEL=llama-3.3-70b-versatile        # primary rule bundle extraction model
GROQ_MODEL=llama-3.1-8b-instant               # fallback if RAG_GROQ_MODEL unset
RAG_FALLBACK_MODELS=openai/gpt-oss-20b
RAG_TIMEOUT_SECONDS=60
RAG_RULE_BUNDLE_DELAY_SECONDS=2               # delay between startup LLM calls; increase on Groq 429
RAG_REASONING_EFFORT=low                       # for openai/gpt-oss-* models only
RAG_HANDBOOK_PATH=engines/RAG/CIS_Handbook.md
RAG_CHROMA_DIR=engines/RAG/chroma_db
RAG_CHUNKS_FILE=engines/RAG/chunks.pkl

# Shared LLM client
LLM_PROVIDER=groq
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=<secret>
LLM_MODEL=llama-3.1-8b-instant               # default fallback; overridden by QU/Composer vars
LLM_TIMEOUT_SECONDS=20

# Query Understanding
QU_PRIMARY_MODEL=llama-3.3-70b-versatile
QU_FALLBACK_MODELS=openai/gpt-oss-120b,openai/gpt-oss-20b
QU_TIMEOUT_SECONDS=30

# Response Composer
COMPOSER_USE_LLM=true
COMPOSER_PRIMARY_MODEL=qwen/qwen3-32b
COMPOSER_FALLBACK_MODELS=llama-3.1-8b-instant,openai/gpt-oss-20b
COMPOSER_TIMEOUT_SECONDS=30

# Session
SESSION_DB_PATH=pathfinder_sessions.db
QU_CONTEXT_TURNS=5

# UI
PATHFINDER_API_URL=http://localhost:8000

# Dev endpoints (do not set in production)
APP_ENV=dev
DEV_MODE=false

# Optional
HF_TOKEN=<token>
```

### Model Selection Notes

- QU uses `llama-3.3-70b-versatile` as primary for intent classification accuracy.
- Composer uses `qwen/qwen3-32b` for narration quality; it is a preview model — production roadmap moves to `openai/gpt-oss-20b`.
- Composer intentionally avoids `llama-3.3-70b-versatile` to distribute provider load.
- `LLM_MODEL` is a last-resort fallback only; it is overridden by `QU_PRIMARY_MODEL` and `COMPOSER_PRIMARY_MODEL`.

---

## Startup Flow

```
1. load_dotenv()                    # .env loaded before any module import reads it
2. load_excel(excel_path)           # StudentContextProvider loads students_anonymous.xlsx
3. KGAdapter()                      # tries Neo4j connect; warns and degrades if unavailable
4. RAGAdapter()                     # loads Chroma + BM25 artifacts; fails if ingest not run
5. ALEAdapter()                     # wraps ALE functions; no external connections
6. Orchestrator(kg, rag, ale)       # wires adapters
7. ResponseComposer()               # reads COMPOSER_* env vars; no network call at init
8. rag.get_rule_bundles()           # 11 Groq LLM calls with RAG_RULE_BUNDLE_DELAY_SECONDS delay
9. _make_resolver(kg)               # KG entity resolver closure; None if KG unavailable
10. yield                           # app is ready; log line: "PathFinder: ready."
```

**Startup artifacts required:**
- `data/students_anonymous.xlsx` — must exist; contains `data` and `registrations` sheets
- `engines/RAG/chroma_db/` — Chroma vector index; built by `python engines/RAG/ingest.py`
- `engines/RAG/chunks.pkl` — BM25/parent-chunk index; built by `python engines/RAG/ingest.py`
- Neo4j running and populated with knowledge graph

**Startup timing:**
- RAG embedding and reranker model loading: 80–100 s (first run; cached after)
- Rule bundle extraction (11 Groq calls): 25–35 s
- Total cold start: 25–110 s

---

## Backend Entrypoint

```bash
python -m uvicorn main:app --reload
```

- API docs: `http://localhost:8000/docs`
- Health check: `GET http://localhost:8000/health` → `{"status": "ok", "service": "PathFinder"}`

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat` | Main chat endpoint; returns `session_id`, `session_name`, `answer_text`, `citations`, `status` |
| GET | `/sessions/{student_id}` | List sessions for a student |
| GET | `/students/{student_id}/sessions/{session_id}/history` | Session history (ownership-safe) |
| DELETE | `/students/{student_id}/sessions/{session_id}` | Delete a specific session (ownership-safe) |
| GET | `/health` | Basic health check |
| GET | `/session/{session_id}/history` | **DEPRECATED** — returns 410 Gone |
| DELETE | `/dev/students/{student_id}/sessions` | DEV ONLY — delete all sessions for a student |
| DELETE | `/dev/sessions` | DEV ONLY — delete all sessions globally |

Dev endpoints require `APP_ENV=dev` or `DEV_MODE=true`.

---

## UI Entrypoint

```bash
python -m streamlit run ui/streamlit_app.py
```

- UI: `http://localhost:8501`
- Reads `PATHFINDER_API_URL` from environment (default: `http://localhost:8000`)

### UI Features

- Student ID login / logout
- New chat button
- Session history panel with per-session delete (🗑) button
- Chat-style message flow
- Citation expander for handbook answers

---

## Data and Artifact Expectations

| Artifact | Path | How to create |
|---|---|---|
| Student dataset | `data/students_anonymous.xlsx` | Manual; must have `data` + `registrations` sheets |
| Handbook source | `engines/RAG/CIS_Handbook.md` | Manual; edit and re-run ingest if changed |
| Chroma vector index | `engines/RAG/chroma_db/` | `python engines/RAG/ingest.py` |
| BM25/parent chunks | `engines/RAG/chunks.pkl` | `python engines/RAG/ingest.py` |
| KG graph | Neo4j instance | Load via `engines/kg/cypher/load.cypher`; verify with `verify.cypher` |
| Entity aliases | `engines/kg/data/entity_aliases.json` | Maintained manually for alias/ambiguous-term resolution |
| Session DB | `SESSION_DB_PATH` (default: `pathfinder_sessions.db`) | Created automatically on first startup |

---

## Test Commands

```bash
# Individual component tests
python -m pytest tests/test_main.py -v --tb=short
python -m pytest tests/test_orchestrator.py -v --tb=short
python -m pytest tests/test_response_composer.py -v --tb=short
python -m pytest tests/test_query_understanding.py -v --tb=short
python -m pytest tests/test_session_manager.py -v --tb=short
python -m pytest tests/test_student_context_provider.py -v --tb=short
python -m pytest tests/test_utils.py -v --tb=short

# Full non-live suite (excludes smoke tests)
python -m pytest tests/ -v --tb=short -k "not smoke"
```

Do not run `smoke_test_*.py` or `acceptance_*.py` without live LLM + Neo4j.

---

## Issues Found and Fixed (Step 11)

| # | Issue | Fix |
|---|---|---|
| 1 | README Architecture listed deprecated `GET /session/{session_id}/history` as active | Corrected: documented as deprecated/410; added ownership-safe and delete endpoints |
| 2 | README said "19 KG operations"; adapter has 18 | Fixed to 18 |
| 3 | README Configuration missing `LLM_MODEL`, `LLM_TIMEOUT_SECONDS`, `QU_TIMEOUT_SECONDS`, `COMPOSER_TIMEOUT_SECONDS`, `APP_ENV`, `DEV_MODE` | Added all |
| 4 | README "Current Limitations" said delete_session not exposed via API — stale | Removed stale line; IS exposed via `DELETE /students/{student_id}/sessions/{session_id}` |
| 5 | README Setup had no venv creation step, no `.env.example` copy step, no browser URLs | Added full setup sequence |
| 6 | `.env.example` missing `LLM_MODEL`, `LLM_TIMEOUT_SECONDS`, `APP_ENV`, `DEV_MODE` | Added all |
| 7 | README "Suggested Next Improvements" listed completed items | Removed done items; updated with real carry-forwards |
| 8 | README had no test commands, no model config section, no demo section | Added all three |
| 9 | QU intents count not mentioned in Architecture | Added "26 locked intents" to query_understanding description |

---

## Known Carry-Forwards

| ID | Item | Phase |
|---|---|---|
| CF-CONFIG-1 | Composer reset-assumptions wording needs `assumptions_cleared=True` from Orchestrator | Phase 1.5 |
| CF-CONFIG-2 | LangChain Chroma deprecation warning (migrate to `langchain_chroma`) | Phase 5 |
| CF-CONFIG-3 | Per-component health checks not yet implemented | Post-Phase 2 |
| CF-CONFIG-4 | `qwen/qwen3-32b` is preview — production Composer primary model to be decided in Phase 2 | Phase 2 |
| CF-CONFIG-5 | Startup cold time ~110 s (RAG model loading) | Phase 5 optimization |
