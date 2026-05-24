"""
engines/rag/app.py
──────────────────
Backend RAG pipeline — no UI, no Streamlit.

Replaces the original Streamlit app.py with a pure backend module that:
  1. Loads the retriever (chroma_db + chunks.pkl) relative to THIS file
  2. Reads COLAB_LLM_URL from the .env at the repo root
  3. Runs the full pipeline: guardrails → retrieve → generate → return answer

Two ways to use it:
  A) As a standalone CLI (type questions, get answers):
       python engines/rag/app.py

  B) As a module imported by the RAGAdapter in adapters/rag_adapter.py:
       from engines.rag.app import ask
       result = ask("What are the probation rules?")
       # result → {"answer": "...", "citations": [...]}
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# ── Resolve paths relative to THIS file so the module works regardless of cwd ─
_THIS_DIR = Path(__file__).resolve().parent          # engines/rag/
_REPO_ROOT = _THIS_DIR.parent.parent                 # repo root

# Load .env from repo root (override so a re-run always picks up the latest URL)
load_dotenv(_REPO_ROOT / ".env", override=True)

# ── Lazy-load the retriever (singleton, heavy model load) ─────────────────────
_retriever = None

def _get_retriever():
    global _retriever
    if _retriever is None:
        from engines.rag.retriever import get_retriever
        _retriever = get_retriever()
    return _retriever


# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM = (
    "You are a highly strict, rules-based academic advisor for the "
    "Egyptian University of Informatics (EUI). "
    "You only state facts explicitly found in the provided text."
)


# ── Guardrails ────────────────────────────────────────────────────────────────
_ARABIC_RE      = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_FRANCO_RE      = re.compile(r"\b(law|3ashan|fadel|kam|hal|a2dar|yasta|ezzay|kda|bas|tab|bgd|y3ny|wla|msh|sa7|lamma)\b")
_HALLUCINATION  = [
    "grades that i need",
    "what will be gpa",
    "predict",
    "step-by-step appeal",
    "drop in order to focus",
    "minor in",
    "double major",
]

def _guard(question: str) -> Optional[str]:
    """Return an error string if the question should be blocked, else None."""
    q = question.lower()
    if _ARABIC_RE.search(question):
        return "Please ask your question in English. Arabic is not supported."
    if _FRANCO_RE.search(q):
        return "Please ask your question in English. Franco-Arabic is not supported."
    if any(t in q for t in _HALLUCINATION):
        return "Not found in the handbook."
    return None


# ── Prompt builder ────────────────────────────────────────────────────────────
def _build_prompt(docs: list, question: str) -> str:
    context = "\n\n---\n\n".join(d.page_content for d in docs)
    return (
        f"HANDBOOK EXCERPT:\n{context}\n\n"
        f"STUDENT QUESTION: {question}\n\n"
        "STRICT RULES YOU MUST FOLLOW:\n"
        "1. Keep your answer EXTREMELY concise and direct. "
        "Do not add conversational filler, unsolicited advice, or irrelevant rules.\n"
        "2. If the Excerpt contains the answer, DO NOT include the phrase "
        "'Not found in the handbook.' anywhere in your answer.\n"
        "3. If, and ONLY if, the entire Excerpt completely lacks the exact answer, "
        "your ENTIRE output must be EXACTLY: 'Not found in the handbook.' with no other text.\n"
        "4. When extracting numbers, grades, or points from tables, copy the exact "
        "value from the table. Do not use outside knowledge.\n"
        "5. Format your answer using short, professional bullet points."
    )


# ── Main callable: ask() ──────────────────────────────────────────────────────

def ask(question: str) -> dict:
    """
    Full RAG pipeline for one question.

    Returns dict with keys: answer, citations, status
    """
    # 1. Guardrails
    block = _guard(question)
    if block:
        return {"answer": block, "citations": [], "status": "guardrail"}

    # 2. Retrieve
    try:
        retriever = _get_retriever()
        docs = retriever.retrieve(question, k_vec=20, k_bm25=15, k_final=6)
    except Exception as exc:
        return {
            "answer": f"Retriever error: {exc}",
            "citations": [],
            "status": "retriever_error",
        }

    citations = [
        {"source": "CIS Student Handbook", "page": d.metadata.get("page")}
        for d in docs
    ]

    if not docs:
        return {"answer": "Not found in the handbook.", "citations": [], "status": "no_docs"}

    # 3. Build payload
    prompt = _build_prompt(docs, question)
    payload = {"system": _SYSTEM, "context": "", "question": prompt}

    # 4. Call LLM — prefer Colab tunnel if configured, fall back to Groq
    colab_url = os.getenv("COLAB_LLM_URL", "").strip()

    if colab_url:
        endpoint = (
            colab_url
            if "modal.run" in colab_url
            else f"{colab_url.rstrip('/')}/generate"
        )
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers={"Bypass-Tunnel-Reminder": "true"},
                timeout=120,
            )
            response.raise_for_status()
            answer = response.json().get("answer", "No answer returned.")
        except Exception as exc:
            answer = f"LLM call failed: {exc}"
    else:
        # Groq fallback — uses the same credentials as the rest of the system
        try:
            sys.path.insert(0, str(_REPO_ROOT))
            from gateway.llm_client import LLMClient
            _llm = LLMClient()
            answer = _llm.chat(
                system=_SYSTEM,
                user=_build_prompt(docs, question),
                temperature=0.1,
            )
        except Exception as exc:
            return {
                "answer": f"LLM call failed: {exc}",
                "citations": citations,
                "status": "llm_error",
            }

    return {"answer": answer, "citations": citations, "status": "ok"}


# ── CLI prompt loop ───────────────────────────────────────────────────────────

def _cli() -> None:
    colab_url = os.getenv("COLAB_LLM_URL", "").strip()
    if not colab_url:
        print("\n⚠  COLAB_LLM_URL is not set in .env")
    else:
        print(f"\n✓  Colab LLM: {colab_url}")

    print("Loading retriever...")
    try:
        _get_retriever()
    except Exception as exc:
        print(f"\n✗  Retriever failed: {exc}")
        print("   Run:  python engines/rag/ingest.py   to build the index first.")
        sys.exit(1)

    print("✓  RAG engine ready.\n")
    print("Type a handbook question. Press Ctrl+C or type 'exit' to quit.")
    print("─" * 60)

    while True:
        try:
            question = input("\nQuestion: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
            break

        if not question or question.lower() in {"exit", "quit", "q"}:
            print("Bye.")
            break

        result = ask(question)
        print(f"\nStatus : {result['status']}")
        print(f"Answer : {result['answer']}")
        if result["citations"]:
            print("Sources:")
            for c in result["citations"]:
                page = f"  p.{c['page']}" if c.get("page") is not None else ""
                print(f"  • {c['source']}{page}")


if __name__ == "__main__":
    _cli()
