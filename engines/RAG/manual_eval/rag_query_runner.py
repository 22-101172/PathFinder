"""
rag_query_runner.py — Manual evaluation runner for normal RAG handbook Q&A.

Tests extract_facts() directly. Does NOT test RAGAdapter, rule-bundle extraction,
QU, Orchestrator, or any other subsystem.

Usage:
    # Single query, primary model from .env
    python -m engines.rag.manual_eval.rag_query_runner --query "What is the attendance policy?"

    # Single query, forced single model (no fallback)
    python -m engines.rag.manual_eval.rag_query_runner --model openai/gpt-oss-20b --query "What is the attendance policy?"
    python -m engines.rag.manual_eval.rag_query_runner --model llama-3.1-8b-instant --query "What is the attendance policy?"

    # Batch from file
    python -m engines.rag.manual_eval.rag_query_runner --model openai/gpt-oss-20b --queries-file engines/rag/manual_eval/sample_queries.txt

    # Batch with inter-query delay (avoids Groq 429 on lower-tier models)
    python -m engines.rag.manual_eval.rag_query_runner --model llama-3.1-8b-instant --queries-file engines/rag/manual_eval/sample_queries.txt --delay-seconds 6

    # Batch with delay and automatic retry on rate-limit errors
    python -m engines.rag.manual_eval.rag_query_runner --model llama-3.1-8b-instant --queries-file engines/rag/manual_eval/sample_queries.txt --delay-seconds 6 --max-retries 2 --retry-delay-seconds 15
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Reconfigure stdout/stderr to UTF-8 on Windows so handbook Unicode characters
# (bullets, accents, special dashes) do not crash the terminal or file redirect.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Load .env from project root before importing rag_core
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

try:
    from engines.rag.rag_core import extract_facts
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from rag_core import extract_facts


def _clean_for_console(text: str) -> str:
    """Strip newlines and encode/decode with replacement to drop unprintable chars."""
    text = text.replace("\n", " ").replace("\r", " ")
    return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def _call_with_retry(
    query: str,
    kwargs: dict,
    max_retries: int,
    retry_delay_seconds: float,
) -> tuple[dict, float]:
    """Call extract_facts with retry on rag_llm_error. Returns (result, elapsed)."""
    result: dict = {}
    elapsed: float = 0.0
    for attempt in range(max_retries + 1):
        t0 = time.monotonic()
        result = extract_facts(query, **kwargs)
        elapsed = time.monotonic() - t0
        if result.get("error") == "rag_llm_error" and attempt < max_retries:
            print(
                f"  [Attempt {attempt + 1}/{max_retries + 1} failed (rag_llm_error) "
                f"— retrying in {retry_delay_seconds:.0f}s...]"
            )
            time.sleep(retry_delay_seconds)
            continue
        break
    return result, elapsed


def _run_query(
    query: str,
    model: str | None,
    allow_fallback: bool,
    max_retries: int = 0,
    retry_delay_seconds: float = 10.0,
) -> None:
    print(f"\n{'='*70}")
    print(f"Query        : {query}")
    print(f"Model        : {model or '(from env / default)'}")
    print(f"Fallback     : {'enabled' if allow_fallback else 'disabled (single-model mode)'}")
    print(f"{'-'*70}")

    kwargs = {"allow_fallback": allow_fallback}
    if model:
        kwargs["groq_model"] = model

    result, elapsed = _call_with_retry(query, kwargs, max_retries, retry_delay_seconds)

    found     = result.get("found", False)
    facts     = result.get("extracted_facts", [])
    sources   = result.get("source_documents", [])
    error     = result.get("error")

    print(f"Found        : {found}")
    print(f"Elapsed      : {elapsed:.2f}s")

    if error:
        print(f"Error        : {error}")

    if facts:
        print("Extracted facts:")
        for i, f in enumerate(facts, 1):
            print(f"  {i}. {f}")
    else:
        print("Extracted facts: (none)")

    if sources:
        print("Source pages :")
        for s in sources:
            snippet = _clean_for_console(s.get("text", ""))[:120]
            print(f"  p.{s.get('page','?')} - {snippet}...")
    else:
        print("Source pages : (none)")

    print("\nFull JSON result:")
    print(json.dumps(result, indent=2, ensure_ascii=True))


def _load_queries_file(path: str) -> list[str]:
    queries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                queries.append(line)
    return queries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manual RAG model evaluation runner — tests extract_facts() only."
    )
    parser.add_argument("--query", help="Single query to run")
    parser.add_argument("--queries-file", help="Path to query file (one per line, # = comment)")
    parser.add_argument(
        "--model",
        default=None,
        help="Force a specific model ID. Disables fallback for fair single-model comparison.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.0,
        metavar="N",
        help="Seconds to sleep between queries (default: 0). Use 6+ for Llama on Groq free tier.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=0,
        metavar="N",
        help="Retry count on rag_llm_error (e.g. 429). Default: 0 (no retry).",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=float,
        default=10.0,
        metavar="N",
        help="Seconds to wait between retries (default: 10).",
    )
    args = parser.parse_args()

    if not args.query and not args.queries_file:
        parser.error("Provide --query or --queries-file")

    # When a model is explicitly forced, disable fallback for fair comparison
    allow_fallback = args.model is None

    queries: list[str] = []
    if args.query:
        queries.append(args.query)
    if args.queries_file:
        queries.extend(_load_queries_file(args.queries_file))

    print(f"RAG Manual Evaluator")
    print(f"Model        : {args.model or '(from env / default chain)'}")
    print(f"Fallback     : {'enabled' if allow_fallback else 'disabled'}")
    print(f"Queries      : {len(queries)}")
    print(f"Delay        : {args.delay_seconds}s between queries")
    print(f"Max retries  : {args.max_retries} (retry delay {args.retry_delay_seconds}s)")

    for i, q in enumerate(queries):
        if i > 0 and args.delay_seconds > 0:
            print(f"\n  [Waiting {args.delay_seconds:.1f}s before next query...]")
            time.sleep(args.delay_seconds)
        _run_query(
            q,
            args.model,
            allow_fallback,
            max_retries=args.max_retries,
            retry_delay_seconds=args.retry_delay_seconds,
        )

    print(f"\n{'='*70}")
    print(f"Done — {len(queries)} query/queries evaluated.")


if __name__ == "__main__":
    main()
