# `gateway/adapters/rag_adapter.py`

## 1. Purpose
Thin adapter between the gateway orchestrator and the local RAG retriever.
Takes a sub-query and returns a strict `{"answer": str, "citations": list}`
shape so the orchestrator and composer can rely on the contract regardless of
what changes inside the RAG engine.

## 2. What's Inside
- `RAGAdapter` class.
  - `__init__` lazy-imports `engines.rag.retriever.get_retriever`; any
    failure leaves `self.retriever = None` and is logged. This lets the
    gateway boot even when optional RAG dependencies are missing.
  - `execute(sub_query, student_context=None)` — public entry point.
    Retrieves top chunks, builds a strict prompt, posts to the configured
    Colab/Modal LLM endpoint at `COLAB_LLM_URL`, and returns
    `{"answer": ..., "citations": [{"source": "...", "page": ...}]}`.

## 3. Inputs / Outputs
- Input: `sub_query: str` (the user's text — the orchestrator passes the
  original query unchanged) and an optional `student_context` dict
  (currently not used by the gateway path; the orchestrator deliberately
  passes `None`).
- Output: dict with `answer: str` and `citations: list[dict]` where each
  citation has `source: str` and `page: int | None`. Errors come back as
  `{"answer": "An error occurred while searching the handbook: ...", "citations": []}`
  or similar configuration-error answers — never as raised exceptions.

## 4. Who Calls It
- `gateway.orchestrator.Orchestrator._rag_only_workflow` and `_mixed_workflow`.

## 5. What It Calls
- `engines.rag.retriever.get_retriever()` and its `retrieve(...)` method.
- The Colab/Modal LLM endpoint at `COLAB_LLM_URL` via `requests.post`.

## 6. Debugging / Tracing
- INFO log: "RAG wrapper retrieving for: <query>" before each retrieve.
- INFO log: "Calling LLM endpoint: <endpoint>" before the HTTP call.
- WARNING/ERROR logs cover initialization failure ("RAGAdapter failed to
  initialize retriever") and missing config ("COLAB_LLM_URL environment
  variable is missing").
- Common failure modes:
  - **Empty citation list** — the retriever returned zero documents OR the
    response prefix matched the "Not found in the handbook" / error
    branches. Citations are intentionally cleared in those branches.
  - **"RAG Engine is currently unavailable."** — `self.retriever is None`;
    the lazy import failed.
  - **HTTP timeout** — the Colab endpoint is slow / unreachable.
    `requests.post` is currently using `timeout=120`.

## 7. What NOT To Put In It
- KG/Neo4j calls.
- Workflow routing.
- Privacy violations: the orchestrator passes `student_context=None` for
  handbook lookups on purpose. Do not add transcript data into the prompt
  silently.
- Final user-facing formatting beyond passing the LLM answer through. The
  composer owns the surrounding text and citation rendering.
