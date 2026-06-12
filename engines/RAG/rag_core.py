"""
rag_core.py — The RAG Engine / Adapter

This is the only file your frontend / other systems need to call.
It takes a query, retrieves the right handbook chunks, calls the LLM,
and returns a clean JSON dict.

Usage:
    from rag_core import extract_facts

    result = extract_facts(
        query="What is the minimum attendance percentage?",
        groq_api_key="your_key",   # OR pass colab_url below
    )
    print(result)
    # {
    #   "found": true,
    #   "extracted_facts": ["Students must maintain 75% attendance per course."],
    #   "source_pages": [3],
    #   "query": "What is the minimum attendance percentage?"
    # }

Output schema (always returned, even on error):
    {
        "found":           true | false,
        "extracted_facts": ["fact 1", "fact 2", ...],
        "source_pages":    [3, 7, ...],
        "query":           "the original question"
    }
"""

from retriever import get_retriever
import requests
import json
import os
import time
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
load_dotenv(os.path.join(_ROOT, '.env'))

# ── singleton retriever ──────────────────────────────────────────────────────
try:
    retriever = get_retriever()
except Exception as e:
    logger.warning("RAG: retriever not ready at import time: %s", e)
    retriever = None

# ── system prompt ─────────────────────────────────────────────────────────────
SYSTEM_INSTRUCTION = """You are a strict data extraction engine.
You will be provided with an EXCERPT from an academic handbook and a STUDENT QUESTION.
Extract only the facts from the EXCERPT that directly answer the QUESTION.
Do NOT add conversational filler, advice, or outside knowledge.
Your output MUST be a valid JSON object with exactly this structure:
{
  "found": true or false,
  "extracted_facts": ["fact 1", "fact 2", ...]
}
If the EXCERPT does not contain the answer, return {"found": false, "extracted_facts": []}."""

# ── system prompt (structured mode) ──────────────────────────────────────────
SYSTEM_INSTRUCTION_STRUCTURED = """You are a strict data extraction engine.
You will be provided with an EXCERPT from an academic handbook and a STUDENT QUESTION.
Extract the facts from the EXCERPT that directly answer the QUESTION and format them exactly according to the provided JSON SCHEMA.
Do NOT add conversational filler.
Your output MUST be a valid JSON object matching this schema exactly.
If the EXCERPT does not contain the answer, return safe default values (e.g., null, 0, or false) for the schema fields.

EXPECTED JSON SCHEMA:
{schema_str}"""

# ── LLM backends ─────────────────────────────────────────────────────────────

def _call_groq(context: str, question: str, api_key: str, model: str, system_prompt: str = None) -> dict:
    """
    Calls the Groq free API (Llama-3.1-8B).
    Returns parsed dict depending on the system prompt used.
    """
    url = "https://api.groq.com/openai/v1/chat/completions"
    user_content = f"HANDBOOK EXCERPT:\n{context}\n\nSTUDENT QUESTION: {question}"
    
    prompt_to_use = system_prompt if system_prompt else SYSTEM_INSTRUCTION
    
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},   # enforced JSON — never breaks
        "messages": [
            {"role": "system", "content": prompt_to_use},
            {"role": "user",   "content": user_content}
        ]
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=60
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    return json.loads(raw)


# ── main public function ──────────────────────────────────────────────────────

def extract_facts(
    query:           str,
    major:           str  = "CIS",
    handbook_type:   str  = "Undergraduate",
    groq_api_key:    str  = None,
    groq_model:      str  = "llama-3.1-8b-instant",
) -> dict:
    """
    The RAG adapter. Takes a query, returns a JSON dict.

    Priority:
        1. groq_api_key  → uses Groq API
        2. GROQ_API_KEY env var → uses Groq API

    Returns:
        {
            "found":           true | false,
            "extracted_facts": ["fact 1", ...],
            "source_documents": [{"page": 3, "text": "..."}, ...],
            "query":           "original question"
        }
    """
    # ── safe defaults ──
    empty_result = {
        "found":           False,
        "extracted_facts": [],
        "source_documents": [],
        "query":           query
    }

    if not retriever:
        logger.error("RAG: retriever not initialized — run ingest.py first")
        return empty_result

    # ── retrieve parent chunks ──
    filter_dict  = {"major": major, "handbook_type": handbook_type}
    docs         = retriever.retrieve(query, k_vec=20, k_bm25=15, k_final=6,
                                      filter=filter_dict)

    if not docs:
        return empty_result

    source_documents = []
    seen_pages = set()
    for d in docs:
        page = d.metadata.get("page")
        if page and page not in seen_pages:
            seen_pages.add(page)
            source_documents.append({
                "page": page,
                "text": d.page_content[:600] + "..." if len(d.page_content) > 600 else d.page_content
            })

    # Keep top 3 parent chunks, truncate each to 1500 chars to stay within
    # context limits and avoid localtunnel timeouts on Colab CPU
    top_docs     = docs[:3]
    context_text = "\n\n---\n\n".join(
        f"[Page {d.metadata.get('page','?')}]:\n{d.page_content[:1500]}"
        for d in top_docs
    )

    # ── resolve backend ──
    api_key  = groq_api_key  or os.environ.get("GROQ_API_KEY")

    if not api_key:
        logger.error("RAG: no Groq API key configured — set GROQ_API_KEY in .env")
        return empty_result

    # ── call LLM ──
    try:
        llm_result = _call_groq(context_text, query, api_key, groq_model)

    except Exception as e:
        logger.error("RAG: LLM call failed: %s", e)
        return {**empty_result, "error": str(e)}

    # ── build final output ──
    return {
        "found":           llm_result.get("found", False),
        "extracted_facts": llm_result.get("extracted_facts", []),
        "source_documents": source_documents,
        "query":           query
    }


def extract_structured(
    query:           str,
    expected_schema: dict,
    major:           str  = "CIS",
    handbook_type:   str  = "Undergraduate",
    groq_api_key:    str  = None,
    groq_model:      str  = "llama-3.1-8b-instant",
) -> dict:
    """
    Extracts data strictly according to the provided expected_schema.
    Returns:
        {
            "data": { ... matches expected_schema ... },
            "source_documents": [ ... ],
            "query": "original question"
        }
    """
    empty_result = {
        "data": {},
        "source_documents": [],
        "query": query
    }

    if not retriever:
        logger.error("RAG: retriever not initialized — run ingest.py first")
        return empty_result

    # ── retrieve parent chunks ──
    filter_dict  = {"major": major, "handbook_type": handbook_type}
    docs         = retriever.retrieve(query, k_vec=20, k_bm25=15, k_final=6,
                                      filter=filter_dict)

    if not docs:
        return empty_result

    source_documents = []
    seen_pages = set()
    for d in docs:
        page = d.metadata.get("page")
        if page and page not in seen_pages:
            seen_pages.add(page)
            source_documents.append({
                "page": page,
                "text": d.page_content[:600] + "..." if len(d.page_content) > 600 else d.page_content
            })

    top_docs     = docs[:3]
    context_text = "\n\n---\n\n".join(
        f"[Page {d.metadata.get('page','?')}]:\n{d.page_content[:1500]}"
        for d in top_docs
    )

    api_key = groq_api_key or os.environ.get("GROQ_API_KEY")

    if not api_key:
        logger.error("RAG: no Groq API key configured — set GROQ_API_KEY in .env")
        return empty_result

    # Inject the schema into the system prompt
    schema_str = json.dumps(expected_schema, indent=2)
    system_prompt = SYSTEM_INSTRUCTION_STRUCTURED.format(schema_str=schema_str)

    try:
        llm_result = _call_groq(context_text, query, api_key, groq_model, system_prompt=system_prompt)
    except Exception as e:
        logger.error("RAG: LLM call failed: %s", e)
        return {**empty_result, "error": str(e)}

    return {
        "data": llm_result,
        "source_documents": source_documents,
        "query": query
    }


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What is the minimum attendance percentage?"

    print(f"\nQuery: {query}")
    print("-" * 60)

    result = extract_facts(query)   # reads GROQ_API_KEY from .env

    print(json.dumps(result, indent=2, ensure_ascii=False))
