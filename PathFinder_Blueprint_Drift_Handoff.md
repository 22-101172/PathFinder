# PathFinder Blueprint Drift Handoff

This file explains where the current codebase differs from older
blueprint-style assumptions. Use it when another AI model is tempted to build
against the original architecture instead of the code that actually exists now.

## How To Use This File

Open this file when you need:

- a comparison between the current MVP and the older Integration Phase
  blueprint
- clarification about what is current reality versus future architecture
- guardrails before making structural changes

If you only need the current project status, start with `README.md` first.

## Current Architecture Statement

For the current MVP, PathFinder is:

> A FastAPI-based modular monolith that exposes one user-facing backend API and
> internally calls local KG and RAG engine modules through gateway adapters.
> Neo4j remains external. The RAG retrieval path runs locally, and the answer
> generation step may call an external LLM endpoint. Separate KG/RAG HTTP
> services are not currently implemented.

## Main Drift Summary

| Area | Older assumption | Current code reality | What to do now |
|---|---|---|---|
| KG integration | Separate HTTP `kg-engine` service | `KGAdapter` calls local Python KG code and Neo4j directly | Keep local adapter path |
| RAG integration | Separate HTTP `rag-engine` service | `RAGAdapter` retrieves locally and may call `COLAB_LLM_URL` for answer generation | Keep local adapter path |
| Gateway role | API entry point plus orchestrator | Still true, with implemented QU, Orchestrator, and Composer layers | Keep |
| UI | Real app exists in repo | `ui` is still placeholder-only | Treat as future work |
| `/query` pipeline | End-to-end implemented later | End-to-end implemented now, with safe fallbacks when KG/RAG/LLM dependencies are unavailable | Preserve current ownership boundaries |
| Academic logic | Full ALE may be implied later | Academic calculations are intentionally out of scope for the current gateway integration | Do not implement full ALE here |

## Current Project Boundaries

These are deliberate current boundaries:

- `gateway/models/schemas.py` is data contracts only
- academic calculations are intentionally outside the current gateway
  integration
- `StudentContextProvider` loads raw student record data and course-status
  buckets only
- `SessionManager` owns overrides and last-referenced entities, not language
  understanding
- `QueryUnderstandingLayer` is the only layer that interprets raw user text
- `Orchestrator` is deterministic and does not call an LLM
- `ResponseComposer` presents facts but does not invent new conclusions

## What Changed Since The Older Blueprint

The current codebase now reflects these integration decisions:

- `main.py` shuts down with `kg_adapter.close()`
- `main.py` passes `session_state` into
  `QueryUnderstandingLayer.classify(...)`
- `QueryUnderstandingLayer` is implemented with:
  - rule-based intent detection
  - KG alias/entity resolution
  - follow-up resolution from session state
  - override detection
  - optional LLM fallback
- `Orchestrator` is implemented with KG-only, RAG-only, mixed,
  student-aware, and clarification workflows
- `ResponseComposer` is implemented with LLM presentation plus deterministic
  fallback
- session IDs are not reused across different students
- RAG citations are normalized to the public schema shape
- academic calculations were kept out of the gateway integration

## What Another Model Should Not Assume

Do not assume any of the following are true today:

- KG is behind `KG_ENGINE_URL`
- RAG is behind `RAG_ENGINE_URL`
- the UI is ready
- every local environment already has Neo4j running on `localhost:7687`
- every local environment already has the optional RAG retriever dependencies
  installed
- `KG_DATA_DIR` is always configured with the full CSV reference data
- the Academic Logic Engine exists
- the gateway owns academic calculations for graduation or credit-limit
  decisions

## What Another Model Should Do Next

The safe continuation path is:

1. Use `README.md` for the current status and runtime expectations
2. Use `docs/CODEBASE_UNDERSTANDING_REPORT.md` for file responsibilities
3. Use `docs/current/runtime_flow.md` for the live `/query` pipeline
4. Bring up the real environment dependencies before judging product behavior:
   Neo4j, KG CSV data, optional RAG retriever dependencies, and LLM endpoint
   config
5. Expand intents, aliases, and features without breaking the current layer
   boundaries

## When To Ignore This File

Ignore this file if you are doing a small local code edit and already know the
current architecture. This file is mainly for avoiding large wrong turns caused
by stale blueprint assumptions.
