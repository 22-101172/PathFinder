"""
rag_core.py — Normal RAG engine core for handbook policy Q&A.

Implements extract_facts() for normal handbook policy/evidence retrieval.
Callers in the main backend should go through RAGAdapter / the orchestrator,
not call this module directly.

Usage (direct / testing only):
    from engines.rag.rag_core import extract_facts

    result = extract_facts(
        query="What is the minimum attendance percentage?",
        groq_api_key="your_key",
    )
    print(result)
    # {
    #   "found": true,
    #   "extracted_facts": ["Students must maintain 75% attendance per course."],
    #   "source_documents": [{"page": 3, "text": "..."}],
    #   "query": "What is the minimum attendance percentage?"
    # }

Output schema (always returned, even on error):
    {
        "found":           true | false,
        "extracted_facts": ["fact 1", "fact 2", ...],
        "source_documents": [{"page": N, "text": "..."}, ...],
        "query":           "the original question"
    }

Model is configurable via RAG_GROQ_MODEL / RAG_FALLBACK_MODELS in .env.
Rule-bundle / structured extraction is handled separately by extract_structured().
"""

try:
    from .retriever import get_retriever
except ImportError:
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

# ── RAG timeout ──────────────────────────────────────────────────────────────

def _load_rag_timeout() -> float:
    """Read RAG_TIMEOUT_SECONDS from env; return 60.0 if missing or invalid."""
    try:
        return float(os.getenv("RAG_TIMEOUT_SECONDS", "60"))
    except (TypeError, ValueError):
        return 60.0

_RAG_TIMEOUT: float = _load_rag_timeout()

# ── model chain ───────────────────────────────────────────────────────────────

DEFAULT_RAG_MODEL = "llama-3.1-8b-instant"

def _load_rag_model_chain(explicit_model: str | None = None) -> list[str]:
    """Return an ordered, deduplicated list of model IDs to try.

    .env values are authoritative; DEFAULT_RAG_MODEL is a safe fallback used
    only when no env model is configured.

    Priority: explicit arg → RAG_GROQ_MODEL → GROQ_MODEL → DEFAULT_RAG_MODEL
              → RAG_FALLBACK_MODELS (comma-separated, deprecated models last).
    """
    candidates = [
        explicit_model,
        os.environ.get("RAG_GROQ_MODEL"),
        os.environ.get("GROQ_MODEL"),
        DEFAULT_RAG_MODEL,
    ]
    fallbacks_raw = os.environ.get("RAG_FALLBACK_MODELS", "")
    for m in fallbacks_raw.split(","):
        candidates.append(m.strip() or None)

    seen: set[str] = set()
    chain: list[str] = []
    for m in candidates:
        if m and m not in seen:
            seen.add(m)
            chain.append(m)
    return chain or [DEFAULT_RAG_MODEL]

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
CRITICAL: Do NOT add information from outside the EXCERPT, even if you know it from general knowledge. Every extracted fact must be literally present in the provided EXCERPT text.

Extraction rules:

NUMERIC BRACKETS AND RANGES:
- When the question gives an EXACT numeric value (e.g. a specific CGPA, GPA, or percentage score), identify the ONE bracket in the excerpt whose range CONTAINS that exact value, and extract ONLY that one bracket. Do not extract any neighboring bracket, even if it appears in the same list.
  Example: question says "my score is 87%". Excerpt shows (A) 84-87.9% -> B+ (3.2) and (B) 88-91.9% -> A- (3.4). Since 87 falls inside 84-87.9%, return ONLY the B+ row. The A- row must NOT appear in extracted_facts.
  Example: question says "my CGPA is 1.5". Excerpt shows (A) CGPA 1.0-1.99 -> 15 credit hours and (B) CGPA below 1.0 -> 12 credit hours. Since 1.5 is inside 1.0-1.99, return ONLY bullet A. Bullet B must NOT appear in extracted_facts.
- When the question asks about a BROAD range (e.g. "under 2.0", "CGPA less than 2.0"), preserve ALL relevant sub-ranges from the excerpt. Do not collapse them.
- When the question contains multiple parts, extract facts for every part answered in the excerpt.
- When a question asks about two contexts (e.g. normal semester and summer semester), extract facts for both if present in the excerpt.

COMPARISON OPERATORS — PRESERVE EXACTLY:
- Copy threshold conditions exactly as written in the excerpt. Never drop or rewrite comparison words.
  Example: excerpt says "a CGPA below 2.0 triggers an academic warning" -> write exactly that. Do NOT write "a CGPA of 2.0 triggers an academic warning".
- Preserve: below, above, at least, at most, greater than or equal to, less than, between, not exceeding.

YES/NO AND NEGATIVE POLICY QUESTIONS — WHAT "found" MEANS:
- "found": true means the EXCERPT contains relevant evidence (a requirement, prohibition, eligibility criterion, or condition) that addresses the question. It does NOT mean "the answer is yes."
- "found": false means the EXCERPT contains NO relevant evidence whatsoever.
- Rule: if you write any extracted_facts, you MUST set "found": true.
- For "Can a student do X if Y?" questions, return found=true when the excerpt contains a requirement or prohibition that answers the question, even if the excerpt uses positive wording while the question uses negative wording.
  Example: question "Can a student graduate with honors if they failed a course?" -> excerpt says "to qualify for honors a student must never have failed a course" -> set found=true, extract "To qualify for honors, a student must never have failed a course."

HANDBOOK TOPIC COVERAGE QUESTIONS:
- For "what topics does the handbook cover?" questions, extract only topics that the EXCERPT affirmatively explains or describes.
- If the EXCERPT says a topic is NOT covered, not found, or outside scope, do NOT list that topic in extracted_facts for a coverage question.
  Example: excerpt says "Minor/Double Major is not covered in this handbook" -> do NOT include "Minor/Double Major" as a covered topic.

FACT QUALITY:
- Only include items that are literally stated in the EXCERPT. Do not add topics, rules, or values from memory or general knowledge.
- Return complete standalone facts, not bare labels or values. Write "A D+ grade has a point value of 2.0" not just "D+" or "2".
- Deduplicate extracted_facts: if two facts are semantically identical, include only one.
- Do not say a fact is "not explicitly defined" if the excerpt contains a direct or partial definition relevant to the question.

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
    Calls the Groq API with the given model.
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
    if model.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = os.environ.get("RAG_REASONING_EFFORT", "low")
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=_RAG_TIMEOUT
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    return json.loads(raw)


# ── post-processing helpers ───────────────────────────────────────────────────

def _deduplicate_facts(facts: list[str]) -> list[str]:
    """Remove exact/near-exact duplicate facts, preserving original form and order."""
    seen: set[str] = set()
    result: list[str] = []
    for fact in facts:
        key = " ".join(fact.lower().split()).rstrip(".")
        if key not in seen:
            seen.add(key)
            result.append(fact)
    return result

# ── main public function ──────────────────────────────────────────────────────

def extract_facts(
    query:           str,
    major:           str       = "CIS",
    handbook_type:   str       = "Undergraduate",
    groq_api_key:    str       = None,
    groq_model:      str | None = None,
    allow_fallback:  bool      = True,
) -> dict:
    """
    Normal RAG policy Q&A. Takes a query, returns a JSON dict.

    API key priority:
        1. groq_api_key argument
        2. GROQ_API_KEY env var

    Model priority (see _load_rag_model_chain):
        explicit groq_model → RAG_GROQ_MODEL → GROQ_MODEL → DEFAULT_RAG_MODEL
        → RAG_FALLBACK_MODELS (only when allow_fallback=True)

    Returns:
        {
            "found":           true | false,
            "extracted_facts": ["fact 1", ...],
            "source_documents": [{"page": N, "text": "..."}, ...],
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

    if not query or not query.strip():
        return empty_result

    if not retriever:
        logger.error("RAG: retriever not initialized — run ingest.py first")
        return empty_result

    # ── build model chain ──
    model_chain = _load_rag_model_chain(groq_model)
    if not allow_fallback:
        model_chain = model_chain[:1]

    # ── retrieve parent chunks ──
    filter_dict = {"major": major, "handbook_type": handbook_type}
    try:
        docs = retriever.retrieve(query, k_vec=20, k_bm25=15, k_final=6,
                                  filter=filter_dict)
    except Exception:
        logger.exception("RAG: retrieval failed for query %r", query)
        return {**empty_result, "error": "rag_retrieval_error"}

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
    api_key = groq_api_key or os.environ.get("GROQ_API_KEY")

    if not api_key:
        logger.error("RAG: no Groq API key configured — set GROQ_API_KEY in .env")
        return empty_result

    # ── call LLM with fallback ──
    llm_result = None
    for model in model_chain:
        try:
            llm_result = _call_groq(context_text, query, api_key, model)
            break
        except Exception as exc:
            logger.warning("RAG: model %r failed (%s: %s); trying next",
                           model, type(exc).__name__, exc)
    if llm_result is None:
        logger.error("RAG: all models in chain failed for query %r", query)
        return {**empty_result, "error": "rag_llm_error"}

    # ── build final output ──
    found = llm_result.get("found", False)
    facts = _deduplicate_facts(llm_result.get("extracted_facts", []))
    # Normalize: non-empty facts always mean evidence was found, regardless of LLM's found field
    if facts:
        found = True
    return {
        "found":            found,
        "extracted_facts":  facts,
        "source_documents": source_documents if found and facts else [],
        "query":            query
    }


def extract_structured(
    query:           str,
    expected_schema: dict,
    major:           str       = "CIS",
    handbook_type:   str       = "Undergraduate",
    groq_api_key:    str       = None,
    groq_model:      str | None = None,   # None → uses model chain (RAG_GROQ_MODEL / GROQ_MODEL / default)
    allow_fallback:  bool      = True,
) -> dict:
    """
    Extracts data strictly according to the provided expected_schema.

    On success:
        {"data": {...}, "source_documents": [...], "query": query}

    On error:
        {"data": {}, "source_documents": [], "query": query, "error": "<code>"}

    Safe error codes:
        empty_query          — query is empty or whitespace
        invalid_schema       — expected_schema is missing, not a dict, or empty
        rag_unavailable      — retriever not initialised (run ingest.py first)
        rag_not_configured   — GROQ_API_KEY not set
        rag_retrieval_error  — retriever raised an exception
        rag_llm_error        — all models in the chain failed
    """
    empty_result = {
        "data": {},
        "source_documents": [],
        "query": query,
    }

    if not query or not query.strip():
        return {**empty_result, "error": "empty_query"}

    if not expected_schema or not isinstance(expected_schema, dict):
        return {**empty_result, "error": "invalid_schema"}

    if not retriever:
        logger.error("RAG: retriever not initialised — run ingest.py first")
        return {**empty_result, "error": "rag_unavailable"}

    # ── retrieve parent chunks ──
    filter_dict = {"major": major, "handbook_type": handbook_type}
    try:
        docs = retriever.retrieve(query, k_vec=20, k_bm25=15, k_final=6,
                                  filter=filter_dict)
    except Exception:
        logger.exception("RAG: structured retrieval failed for query %r", query)
        return {**empty_result, "error": "rag_retrieval_error"}

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
        return {**empty_result, "error": "rag_not_configured"}

    # ── model chain (same mechanism as extract_facts) ──
    model_chain = _load_rag_model_chain(groq_model)
    if not allow_fallback:
        model_chain = model_chain[:1]

    schema_str    = json.dumps(expected_schema, indent=2)
    system_prompt = SYSTEM_INSTRUCTION_STRUCTURED.format(schema_str=schema_str)

    llm_result = None
    for model in model_chain:
        try:
            llm_result = _call_groq(context_text, query, api_key, model,
                                    system_prompt=system_prompt)
            break
        except Exception as exc:
            logger.warning("RAG: structured model %r failed (%s: %s); trying next",
                           model, type(exc).__name__, exc)

    if llm_result is None:
        logger.error("RAG: all models in chain failed for structured query %r", query)
        return {**empty_result, "error": "rag_llm_error"}

    return {
        "data":             llm_result,
        "source_documents": source_documents,
        "query":            query,
    }


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What is the minimum attendance percentage?"

    print(f"\nQuery: {query}")
    print("-" * 60)

    result = extract_facts(query)   # reads GROQ_API_KEY from .env

    print(json.dumps(result, indent=2, ensure_ascii=False))
