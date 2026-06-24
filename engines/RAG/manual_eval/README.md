# RAG Manual Model Evaluation

This folder is for **manual normal RAG model evaluation only**.

It is **not** part of the automated test suite and is **not** run by CI.

## Scope

- Tests `extract_facts()` / normal handbook policy Q&A only.
- Does **not** test RAGAdapter.
- Does **not** test rule-bundle extraction (`extract_structured`).
- Does **not** test QU, Orchestrator, ALE, KG, or Composer.

## Prerequisites

1. `engines/rag/ingest.py` must have been run at least once so ChromaDB and `chunks.pkl` exist.
2. `.env` at the project root must contain a valid `GROQ_API_KEY`.
3. `RAG_GROQ_MODEL` and `RAG_FALLBACK_MODELS` should be set as desired (see `.env.example`).

## Model notes

| Model | Status |
|---|---|
| `openai/gpt-oss-20b` | Primary model. Reasoning-capable (`RAG_REASONING_EFFORT=low`). |
| `llama-3.1-8b-instant` | **Deprecated on Groq Free/Developer tiers. Shutdown: 2026-08-16.** Treat as temporary fallback only. |

## How to run

All commands are run from the **project root**.

### Run GPT-OSS 20B on one question

```bash
python -m engines.rag.manual_eval.rag_query_runner \
  --model openai/gpt-oss-20b \
  --query "What is the attendance policy?"
```

### Run Llama 3.1 8B on one question

```bash
python -m engines.rag.manual_eval.rag_query_runner \
  --model llama-3.1-8b-instant \
  --query "What is the attendance policy?"
```

### Run the full sample query set with GPT-OSS 20B

```bash
python -m engines.rag.manual_eval.rag_query_runner \
  --model openai/gpt-oss-20b \
  --queries-file engines/rag/manual_eval/sample_queries.txt
```

### Run the full sample query set with Llama 3.1 8B (with rate-limit delay)

Llama on Groq Free/Developer tiers hits 429 rate limits on batch runs.
Use `--delay-seconds 6` (or higher) to pace queries.

```bash
python -m engines.rag.manual_eval.rag_query_runner \
  --model llama-3.1-8b-instant \
  --queries-file engines/rag/manual_eval/sample_queries.txt \
  --delay-seconds 6
```

### Run Llama with delay and automatic retry on rate-limit errors

If 429s still occur despite the delay, add `--max-retries`:

```bash
python -m engines.rag.manual_eval.rag_query_runner \
  --model llama-3.1-8b-instant \
  --queries-file engines/rag/manual_eval/sample_queries.txt \
  --delay-seconds 6 \
  --max-retries 2 \
  --retry-delay-seconds 15
```

Retry fires when a query returns `"error": "rag_llm_error"` (which covers Groq 429).
Each failed attempt is logged with the attempt number and wait time.
If all retries are exhausted the error result is kept and evaluation continues.

### Run with primary model from `.env` (with fallback chain enabled)

```bash
python -m engines.rag.manual_eval.rag_query_runner \
  --query "What is the attendance policy?"
```

## How to compare outputs manually

1. Run both models against the same query file and save outputs:

   ```bash
   python -m engines.rag.manual_eval.rag_query_runner \
     --model openai/gpt-oss-20b \
     --queries-file engines/rag/manual_eval/sample_queries.txt \
     > eval_gpt_oss.txt

   python -m engines.rag.manual_eval.rag_query_runner \
     --model llama-3.1-8b-instant \
     --queries-file engines/rag/manual_eval/sample_queries.txt \
     --delay-seconds 6 \
     > eval_llama.txt
   ```

2. Compare side-by-side:

   ```bash
   diff eval_gpt_oss.txt eval_llama.txt
   ```

3. Key things to compare per query:
   - `found` value correct?
   - `extracted_facts` accurate and complete?
   - `source_documents` pages relevant?
   - Elapsed time acceptable?
   - Any `error` codes?

## Forced single-model mode

When `--model` is provided, fallback is automatically disabled. This ensures you are testing exactly one model with no silent fallover, giving a fair per-model comparison.

When no `--model` is given, the full model chain from `.env` is used with fallback enabled (production behavior).
