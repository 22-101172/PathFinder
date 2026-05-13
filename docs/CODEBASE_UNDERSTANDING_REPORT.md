# Codebase Understanding Report

This file is the detailed map of the current codebase. Use it after `README.md`
when you want a more precise understanding of what each part owns and how the
running gateway behaves today.

## How To Use This File

Use this file when you need:

- a file-by-file responsibility map
- the real runtime flow through the gateway
- a quick view of what is implemented versus environment-dependent
- guidance on where to edit for a specific change

## Executive Summary

- The project is a FastAPI gateway for academic advising that combines local KG
  and RAG integrations behind `POST /query`.
- `StudentContextProvider`, `SessionManager`, `KGAdapter`, `RAGAdapter`,
  `QueryUnderstandingLayer`, `Orchestrator`, and `ResponseComposer` are all
  implemented and covered by tests.
- The gateway is designed to return a valid `QueryResponse` even when external
  dependencies are unavailable.
- Current local-runtime caveats are environmental rather than architectural:
  Neo4j must be reachable for KG-backed answers, optional RAG dependencies must
  be installed for handbook retrieval, and `KG_DATA_DIR` improves entity
  resolution beyond the built-in starter aliases.
- Academic calculations remain intentionally outside the gateway integration.
- The current MVP keeps KG/RAG as local modules behind adapters rather than
  standalone microservices.

## Repository Map

```text
pathfinder/
|- README.md
|- PathFinder_Blueprint_Drift_Handoff.md
|- docs/
|  |- CODEBASE_UNDERSTANDING_REPORT.md
|  |- REFINEMENT_LOG.md
|  |- current/
|     |- gateway_main.md
|     |- kg_adapter.md
|     |- kg_data.md
|     |- llm_client.md
|     |- orchestrator.md
|     |- query_understanding.md
|     |- rag_adapter.md
|     |- response_composer.md
|     |- runtime_flow.md
|     |- session_manager.md
|     |- student_context_provider.md
|- data/
|  |- student_profile.json
|  |- handbook/
|- gateway/
|  |- Dockerfile
|  |- __init__.py
|  |- conftest.py
|  |- kg_data.py
|  |- llm_client.py
|  |- main.py
|  |- orchestrator.py
|  |- query_understanding.py
|  |- requirements.txt
|  |- response_composer.py
|  |- session_manager.py
|  |- student_context_provider.py
|  |- adapters/
|  |  |- kg_adapter.py
|  |  |- rag_adapter.py
|  |- models/
|  |  |- schemas.py
|  |- tests/
|     |- test_orchestrator.py
|     |- test_query_endpoint.py
|     |- test_query_understanding.py
|     |- test_response_composer.py
|     |- test_t02_t03.py
|- engines/
|  |- kg/
|  |- rag/
|- ui/
|  |- Dockerfile
|- docker-compose.yml
|- .env.example
```

## Runtime Flow

Current request flow in `gateway/main.py`:

1. `SessionManager.get_or_create_session(...)`
2. `StudentContextProvider.get_student(...)`
3. `SessionManager.build_effective_context(...)`
4. `SessionManager.get_session(...)`
5. `QueryUnderstandingLayer.classify(user_text, effective_context, session_state=...)`
6. optional `SessionManager.apply_overrides(...)`
7. `Orchestrator.run(...)`
8. `ResponseComposer.compose(...)`
9. `SessionManager.update_last_referenced(...)`
10. `SessionManager.record_turn(...)`

Runtime caveats to remember:

- KG-backed queries need a reachable Neo4j instance.
- RAG-backed queries need the retriever dependencies plus `COLAB_LLM_URL`.
- If `KG_DATA_DIR` is unset and no CSV directory is found, entity resolution
  falls back to the tiny built-in starter dataset.
- If `LLM_API_KEY` is blank, Query Understanding uses rules only and the
  Response Composer falls back to deterministic text.

## Implemented Vs Environment-Dependent

| Component | Status | Notes |
|---|---|---|
| `gateway/main.py` | Implemented | `/health` and `/query` are fully wired |
| `gateway/student_context_provider.py` | Implemented | Loads and normalizes student data |
| `gateway/session_manager.py` | Implemented | Tracks overrides and last references |
| `gateway/adapters/kg_adapter.py` | Implemented | Talks to Neo4j through local engine code |
| `gateway/adapters/rag_adapter.py` | Implemented with runtime caveats | Retriever dependency and endpoint config are external |
| `gateway/query_understanding.py` | Implemented | Rule layer, LLM fallback, follow-up resolution, override detection |
| `gateway/orchestrator.py` | Implemented | Deterministic KG-only, RAG-only, mixed, student-aware, clarification paths |
| `gateway/response_composer.py` | Implemented | LLM presentation with deterministic fallback |

## File Responsibilities

### `README.md`

- First-stop handoff document
- Current status, architecture, next steps, and doc guide

### `PathFinder_Blueprint_Drift_Handoff.md`

- Explains architecture drift versus older blueprint assumptions
- Prevents another model from rebuilding around nonexistent microservices or
  older scaffold-era assumptions

### `gateway/main.py`

- FastAPI entry point
- Instantiates providers, adapters, managers, and pipeline components
- Owns the request pipeline and HTTP error behavior

### `gateway/models/schemas.py`

- Canonical Pydantic contracts
- Data shapes only
- No business-rule or academic-calculation logic should live here

### `gateway/student_context_provider.py`

- Loads student JSON from `data/student_profile.json` by default
- Validates into `StudentContext`
- Derives:
  - `completed_courses`
  - `failed_courses`
  - `in_progress_courses`
- Caches loaded students in memory
- Does not perform academic calculations in the current integration

### `gateway/session_manager.py`

- Creates and stores in-memory session state
- Applies session overrides
- Tracks last referenced entities
- Builds effective context without mutating base context
- Refuses to reuse a session ID when it belongs to a different student

### `gateway/query_understanding.py`

- Owns user-text interpretation
- Classifies intents, extracts entities, detects overrides, and resolves
  follow-ups from session state
- Uses rule-first routing with optional LLM fallback

### `gateway/orchestrator.py`

- Owns deterministic workflow dispatch
- Calls KG only, RAG only, mixed, student-aware, or clarification paths
- Normalizes adapter outcomes into `ResultPackage`

### `gateway/response_composer.py`

- Owns final answer generation
- Converts `ResultPackage` to `QueryResponse`
- Uses LLM presentation only on the `ok` path and falls back deterministically

### `gateway/adapters/kg_adapter.py`

- Thin adapter over local KG engine queries
- Returns structured error dicts instead of throwing when possible

### `gateway/adapters/rag_adapter.py`

- Thin adapter over local RAG retrieval and external answer generation
- Returns citations as `{"source": ..., "page": ...}`
- Boots safely even when optional retriever packages are missing

### `gateway/tests/`

- `test_t02_t03.py` covers provider and session-manager behavior
- `test_query_understanding.py` covers the rule layer, LLM fallback, follow-up
  resolution, override detection, and privacy guardrails
- `test_orchestrator.py` covers each workflow path with fake adapters
- `test_response_composer.py` covers LLM and fallback presentation behavior
- `test_query_endpoint.py` covers `/health`, `/query`, 404 handling, and
  session reuse

## Current Data And Session Model Notes

- Demo student source: `data/student_profile.json`
- Current demo student ID: `S_000123`
- `planned_courses` is reserved for hypothetical session-time additions
- `base_context` is never mutated
- Academic calculations are intentionally deferred to a future Academic module

## Adapter Contracts

Use these actual contracts when tracing or extending the gateway:

### KG

```python
KGAdapter.call(operation: str, params: dict) -> dict
```

### RAG

```python
RAGAdapter.execute(sub_query: str, student_context: Optional[dict] = None) -> dict
```

Current RAG return shape:

```json
{
  "answer": "text answer",
  "citations": [
    {
      "source": "CIS Student Handbook",
      "page": 12
    }
  ]
}
```

## Tests

Current command:

```bash
python -m pytest gateway/tests -v
```

Current coverage:

- student loading and derived views
- cache reuse
- session creation, reuse, and cross-student protection
- override accumulation and base-context immutability
- query understanding, LLM fallback behavior, and privacy guardrails
- orchestrator workflow routing
- response composer LLM/fallback rendering
- `/health` and `/query` endpoint behavior

## Safe Editing Guide

If you need to make a change, start here:

- student data loading or derived student fields:
  edit `gateway/student_context_provider.py`
- session behavior or multi-turn state:
  edit `gateway/session_manager.py`
- schema shape:
  edit `gateway/models/schemas.py`
- request pipeline or FastAPI behavior:
  edit `gateway/main.py`
- query classification:
  edit `gateway/query_understanding.py`
- workflow routing:
  edit `gateway/orchestrator.py`
- answer formatting:
  edit `gateway/response_composer.py`
- entity alias loading:
  edit `gateway/kg_data.py`
- provider-agnostic LLM HTTP behavior:
  edit `gateway/llm_client.py`

## Recommended Next Work

1. Hook up the real Neo4j instance and verify KG-backed answers against the
   live graph.
2. Install the optional RAG retriever dependencies and configure
   `COLAB_LLM_URL` to exercise handbook queries end-to-end.
3. Point `KG_DATA_DIR` at the KG CSV directory so entity resolution uses the
   full alias set instead of the starter fallback.
4. Expand the Query Understanding intent/alias coverage based on real traffic.
5. Add new product features only after preserving the current ownership
   boundaries.
