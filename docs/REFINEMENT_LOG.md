# Refinement Log

This file records small, intentional cleanup decisions made before and during
the current integration phase. Use it to understand why some ownership
boundaries were tightened and what changed as the gateway moved from scaffolded
pipeline pieces to a working end-to-end `/query` flow.

## How To Use This File

Use this file when you need:

- the latest refinement decisions in chronological form
- a quick explanation of why something was moved or removed before the next
  phase
- guardrails before starting the next implementation phase

If you need the overall project status, start with `README.md` instead.

## 2026-05-12 Refinement Pass

Scope of this pass:

- keep the current MVP stable
- avoid implementing academic-calculation logic in the gateway
- fix small blockers and ownership drift before the integration sprint

Changes made:

- `gateway/models/schemas.py` is kept as data contracts only.
- `gateway/main.py` now shuts down with `kg_adapter.close()`.
- `QueryUnderstandingLayer.classify(...)` now accepts `session_state`.
- `gateway/main.py` now fetches the session state and passes it into QU.
- `SessionManager` no longer reuses a provided session ID if it belongs to a
  different active student.
- `RAGAdapter` citations now match the public `Citation` schema shape.

## 2026-05-12 Academic Scope Cleanup

Follow-up cleanup made after the refinement pass:

- `gateway/config/academic_rules.py` was removed.
- `StudentContextProvider` no longer computes credit-limit or
  graduation-style snapshot fields.
- academic calculations are explicitly outside the current gateway integration
  scope.
- any future academic calculations should belong to the Academic module rather
  than the gateway integration layer.

## 2026-05-13 Integration Status Update

The integration sprint is now effectively complete at the gateway layer:

- `QueryUnderstandingLayer` is implemented with rule-first routing, optional
  LLM fallback, follow-up resolution, and override detection.
- `Orchestrator` is implemented with deterministic KG-only, RAG-only, mixed,
  student-aware, and clarification workflows.
- `ResponseComposer` is implemented with privacy-safe LLM presentation and a
  deterministic fallback.
- `POST /query` now returns a valid `QueryResponse` across the supported
  workflows, including safe fallback behavior when environment dependencies are
  unavailable.
- endpoint and unit coverage now spans `gateway/tests/` rather than only the
  provider/session pieces.

## Practical Meaning For The Next Phase

These decisions mean:

- the main project risk is now environment wiring and data coverage, not
  missing core gateway layers
- QU, Orchestrator, and Composer ownership boundaries should be preserved
  rather than collapsed together
- schemas should stay focused on contracts, not academic-calculation ownership
- future academic logic can be added in its own module without first undoing
  the current gateway design
- the next useful work is improving live environment setup, intent coverage,
  and domain depth, not rebuilding the current architecture
