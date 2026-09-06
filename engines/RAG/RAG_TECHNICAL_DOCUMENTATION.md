# PathFinder RAG Technical Documentation

---

## 1. Component Summary

The RAG (Retrieval-Augmented Generation) engine is PathFinder's handbook-policy evidence layer. It answers questions drawn directly from the CIS Student Handbook — regulations on attendance, grading, retakes, credit limits, warnings, graduation requirements, and honors.

RAG has two roles:

1. **Normal policy Q&A** — given a self-contained handbook question, retrieve relevant chunks and extract facts verbatim from them.
2. **Structured rule-bundle extraction** — given a JSON schema, extract schema-conformant rule data from handbook chunks. This is used at startup to supply ALE with all 8 policy rule bundles it needs for deterministic academic calculations.

**Strict privacy boundary:** RAG must never receive raw student context (student ID, name, CGPA, transcript, completed courses, grades) unless the question itself is a generalized handbook query. Student-specific interpretation — eligibility checks, GPA projections, semester plans — belongs to the Orchestrator, Composer, and ALE. RAG only extracts what the handbook says.

---

## 2. Files and Responsibilities

| File | Responsibility |
|---|---|
| `engines/rag/ingest.py` | Builds RAG index from CIS handbook: loads, pages, chunks, embeds, persists |
| `engines/rag/retriever.py` | Hybrid retrieval: vector + BM25 + RRF + cross-encoder reranking |
| `engines/rag/rag_core.py` | Normal extraction (`extract_facts`) and structured extraction (`extract_structured`) via Groq LLM |
| `engines/rag/CIS_Handbook.md` | Source policy document (CIS Student Handbook, version 2026-03-05) |
| `engines/rag/chroma_db/` | Persisted ChromaDB vector store (child chunks) |
| `engines/rag/chunks.pkl` | Persisted parent chunk store (pickle dict keyed by UUID) |
| `adapters/rag_adapter.py` | Stable Orchestrator-facing adapter: `execute()`, `execute_structured()`, `get_rule_bundles()` |
| `engines/rag/manual_eval/` | Manual evaluation runner and test query files (not CI, not production code) |
| `tests/test_rag_adapter_execute.py` | Unit tests: `execute()` contract — 10 cases |
| `tests/test_rag_adapter_structured.py` | Unit tests: `execute_structured()` contract — 9 cases |
| `tests/test_rag_core_structured.py` | Unit tests: `extract_structured()` in rag_core — 8 cases |
| `tests/test_rag_rule_bundles.py` | Unit tests: rule-bundle conversion, values, normalization, delay config, partial failure isolation — 16 cases |

---

## 3. Data Source

| Property | Value |
|---|---|
| Source document | `engines/rag/CIS_Handbook.md` |
| Document identity | `"CIS Student Handbook"` |
| Version date | `2026-03-05` (hardcoded in `ingest.py`) |
| Page marker style | `--- PAGE N ---` (regex: `---\s*PAGE\s+(\d+)\s*---`) |
| Major filter | `"CIS"` |
| Handbook type filter | `"Undergraduate"` |

**What RAG can answer:**

- Academic regulations and attendance policy
- Grading scale (letter grades, GPA points, percentage bands)
- CGPA policies and probation / dismissal thresholds
- Credit-hour registration limits by CGPA bracket
- Retake rules (failed courses, improvement retakes, grade caps)
- Academic warnings — thresholds, consecutive/total limits, dismissal conditions, appeal terms
- Graduation requirements (total credits, minimum CGPA, semesters, zero-credit courses, military training)
- Honors eligibility criteria
- Summer semester registration rules
- Student level definitions (Freshman / Sophomore / Junior / Senior credit-hour thresholds)

**What RAG should not answer:**

- Career skill gaps or role-to-skill mapping (KG owns this)
- Course prerequisites from the curriculum graph (KG owns this)
- Student eligibility based on their transcript (ALE owns this)
- GPA simulation math or grade projections (ALE owns this)
- Student-specific transcript questions or personalized plans
- Any topic not explicitly present in the handbook

---

## 4. Ingestion Pipeline

**File:** `engines/rag/ingest.py`

**Step-by-step:**

1. Load `CIS_Handbook.md` from `RAG_HANDBOOK_PATH` (defaults to `engines/rag/CIS_Handbook.md`).
2. Split into page-level `Document` objects using the `--- PAGE N ---` marker.
3. Attach metadata to every document: `doc_id`, `version_date`, `page`, `major`, `handbook_type`.
4. Run parent/child chunking on each page document:

| Parameter | Value |
|---|---|
| Parent chunk size | 800 characters |
| Parent overlap | 250 characters |
| Child chunk size | 200 characters |
| Child overlap | 40 characters |
| Splitter separators | `["\n\n", "\n", ".", " ", ""]` |

5. Each parent chunk gets a UUID as `parent_id`. Each child chunk inherits the same `parent_id`.
6. Persist:
   - Parent chunks → `chunks.pkl` (pickle dict: `{parent_id: Document}`)
   - Child chunks → ChromaDB at `chroma_db/` (via `langchain_community.vectorstores.Chroma`)
7. Embedding model used: `BAAI/bge-small-en-v1.5`

**Rebuild command:**

```powershell
python -m engines.rag.ingest
```

This wipes and rebuilds `chroma_db/` and `chunks.pkl`. Run this whenever `CIS_Handbook.md` changes.

---

## 5. Retrieval Pipeline

**File:** `engines/rag/retriever.py` — class `HybridRetriever`

**4-step pipeline for each query:**

| Step | What happens |
|---|---|
| 1. Vector search | `child_db.similarity_search(query, k=k_vec)` on child chunks in ChromaDB |
| 2. BM25 search | `BM25Okapi.get_scores()` on tokenized parent chunks |
| 3. Parent-child mapping + RRF merge | Children mapped back to parents; both ranked lists merged with Reciprocal Rank Fusion (k=60) |
| 4. Cross-encoder reranking | Top 15 merged parents scored by `CrossEncoder.predict()`, sorted descending, top `k_final` returned |

**Configured models:**

| Role | Model ID |
|---|---|
| Embedding | `BAAI/bge-small-en-v1.5` |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` |

**Default retrieval parameters (as called by `rag_core.py`):**

| Parameter | Default value |
|---|---|
| `k_vec` | 20 |
| `k_bm25` | 15 |
| `k_final` | 6 |

**Metadata filter applied:** `{"major": "CIS", "handbook_type": "Undergraduate"}` (ChromaDB `$and` filter when both keys present).

**Important behavior:**

- Empty or whitespace-only query returns `[]` immediately (no retrieval attempted).
- `ChromaDB` directory (`chroma_db/`) must exist — `FileNotFoundError` is raised otherwise.
- `chunks.pkl` must exist — `FileNotFoundError` is raised otherwise.
- First startup downloads `BAAI/bge-small-en-v1.5` and `cross-encoder/ms-marco-MiniLM-L-6-v2` from HuggingFace (can be slow; set `HF_TOKEN` to avoid rate limits).
- `HybridRetriever` is a singleton — loaded once via `get_retriever()` and cached in `_retriever_instance`.

**Carry-forward:** `langchain_community.vectorstores.Chroma` emits a deprecation warning. Migration to `langchain_chroma` is a Phase 5 cleanup item.

---

## 6. RAG Core — Normal Policy Extraction

**File:** `engines/rag/rag_core.py` — function `extract_facts()`

**Purpose:** Given a handbook question, retrieve relevant chunks, call the Groq LLM as a strict fact-extraction engine, and return extracted facts.

**Signature:**

```python
extract_facts(
    query: str,
    major: str = "CIS",
    handbook_type: str = "Undergraduate",
    groq_api_key: str = None,
    groq_model: str | None = None,
    allow_fallback: bool = True,
) -> dict
```

**Output shape (always returned, even on error):**

```python
{
    "found":            bool,
    "extracted_facts":  list[str],
    "source_documents": list[dict],   # [{"page": N, "text": "..."}]
    "query":            str,
    # "error": str  — only present on infrastructure failure
}
```

**Processing steps:**

1. Retrieve parent chunks via `HybridRetriever.retrieve()` with the metadata filter.
2. Collect unique-page source documents (page text truncated to 600 chars).
3. Use the top 3 parent chunks, each truncated to 1500 chars, as context.
4. Call the Groq LLM using `SYSTEM_INSTRUCTION` and a `user` message: `HANDBOOK EXCERPT: ...\n\nSTUDENT QUESTION: ...`
5. Parse JSON response (`response_format: json_object` enforced).
6. Deduplicate extracted facts (`_deduplicate_facts()`).
7. Normalize: if `extracted_facts` is non-empty, `found=True` regardless of LLM's own `found` field.

**Safe error codes:**

| Code | Condition |
|---|---|
| *(no error key)* | Empty query (returns empty result silently) |
| `rag_retrieval_error` | Retriever raised an exception |
| `rag_llm_error` | All models in chain failed |

**No-evidence behavior:** `found=False`, `extracted_facts=[]`, `source_documents=[]`. Composer should treat this as "handbook has no relevant information" — not as a signal to hallucinate.

---

## 7. RAG Core — Structured Extraction

**File:** `engines/rag/rag_core.py` — function `extract_structured()`

**Purpose:** Schema-forced extraction used by `RAGAdapter.get_rule_bundles()` at startup. Not intended for direct Orchestrator use.

**Signature:**

```python
extract_structured(
    query: str,
    expected_schema: dict,
    major: str = "CIS",
    handbook_type: str = "Undergraduate",
    groq_api_key: str = None,
    groq_model: str | None = None,
    allow_fallback: bool = True,
) -> dict
```

**Output shape:**

```python
{
    "data":             dict,          # schema-matched result; {} on failure
    "source_documents": list[dict],
    "query":            str,
    # "error": str  — only present on failure
}
```

**Safe error codes:**

| Code | Condition |
|---|---|
| `empty_query` | Query is empty or whitespace |
| `invalid_schema` | `expected_schema` is None, not a dict, or empty dict |
| `rag_unavailable` | `retriever` module-level var is `None` (run `ingest.py` first) |
| `rag_not_configured` | `GROQ_API_KEY` not set |
| `rag_retrieval_error` | Retriever raised an exception |
| `rag_llm_error` | All models in chain failed |

**Uses `SYSTEM_INSTRUCTION_STRUCTURED`** (not `SYSTEM_INSTRUCTION`): instructs LLM to output JSON matching the provided schema; safe default values (null, 0, false) if evidence is missing.

**Model chain:** same `_load_rag_model_chain()` as `extract_facts`. Fallback is active by default.

**`openai/gpt-oss-*` models:** receive an extra `reasoning_effort` parameter from `RAG_REASONING_EFFORT` env var (default `"low"`).

---

## 8. Prompts / System Instructions

### `SYSTEM_INSTRUCTION` (normal extraction)

Key behavior:

- Strict fact-extraction engine — only extract what is literally in the provided excerpt.
- **Exact bracket selection:** when the question contains a specific numeric value, identify and return only the one bracket whose range contains it. Neighboring brackets must not appear.
- **Preserve comparison operators exactly:** "below", "at least", "greater than or equal to", etc. Never rewrite thresholds.
- **`found=true` means evidence exists**, not "the answer is yes." If extracted_facts is non-empty, `found` must be `true`.
- No hallucination. No general knowledge. No outside-excerpt additions.
- Output: `{"found": true|false, "extracted_facts": ["fact 1", ...]}`

### `SYSTEM_INSTRUCTION_STRUCTURED` (structured extraction)

Key behavior:

- Strict schema extraction engine.
- Output must be a valid JSON object matching the provided schema exactly.
- If evidence is missing for a field, use safe defaults (null, 0, false).
- Used only for rule-bundle queries; not for normal conversational Q&A.

---

## 9. Model Configuration and External Provider Facts

### 9.1 Environment Variables

| Variable | Purpose | Default if unset |
|---|---|---|
| `GROQ_API_KEY` | Authentication for Groq API | Required; RAG fails without it |
| `RAG_GROQ_MODEL` | Primary RAG model override | Falls through to `GROQ_MODEL` then built-in default |
| `GROQ_MODEL` | Shared model fallback | Falls through to built-in default |
| `RAG_FALLBACK_MODELS` | Comma-separated fallback model list | Empty (no additional fallback beyond default) |
| `RAG_REASONING_EFFORT` | `reasoning_effort` for `openai/gpt-oss-*` models only | `"low"` |
| `RAG_TIMEOUT_SECONDS` | HTTP timeout for Groq API calls | `60` seconds |
| `RAG_RULE_BUNDLE_DELAY_SECONDS` | Seconds to sleep between LLM calls during startup rule-bundle extraction | `2.0` seconds |
| `RAG_HANDBOOK_PATH` | Path to the handbook markdown file | `engines/rag/CIS_Handbook.md` |
| `RAG_CHROMA_DIR` | Path to ChromaDB persist directory | `engines/rag/chroma_db` |
| `RAG_CHUNKS_FILE` | Path to parent chunks pickle file | `engines/rag/chunks.pkl` |
| `HF_TOKEN` | HuggingFace API token (optional) |  unauthenticated downloads used |

### 9.2 Model-Chain Priority

For each `extract_facts()` or `extract_structured()` call, the model is selected in this order (first non-empty, non-duplicate value wins):

1. Explicit `groq_model` argument (if passed)
2. `RAG_GROQ_MODEL` env var
3. `GROQ_MODEL` env var
4. Built-in default: `llama-3.1-8b-instant`
5. `RAG_FALLBACK_MODELS` (comma-separated, tried in order)

All models are deduplicated. If `allow_fallback=False`, only the first model in the chain is tried.

**Project selection:**
- **Primary:** `llama-3.1-8b-instant` — chosen after Step 2A/2B manual evaluation as the best balance of speed, cost, and extraction quality.
- **Fallback:** `openai/gpt-oss-20b` — kept for robustness; better reasoning at higher output cost.

### 9.3 External Groq Model Facts

> **These are externally verified facts — recheck before deployment/demo.**
> Source: official Groq model documentation, checked 2026-06-22.
> Provider specs, rate limits, and pricing may change. Verify at [console.groq.com](https://console.groq.com) before final demo or production deployment.

| Model ID | Role in PathFinder RAG | Provider | Context window | Max output tokens | Pricing input | Pricing cached input | Pricing output | Notes |
|---|---|---|---|---|---|---|---|---|
| `llama-3.1-8b-instant` | Primary RAG model | Groq / Meta Llama | 131,072 tokens | 131,072 tokens | $0.05 / 1M tokens | Not listed in checked model card | $0.08 / 1M tokens | Very cheap and fast; good for normal handbook extraction and startup rule-bundle extraction. |
| `openai/gpt-oss-20b` | RAG fallback model | Groq / OpenAI open-weight GPT-OSS | 131,072 tokens | 65,536 tokens | $0.075 / 1M tokens | $0.037 / 1M cached input tokens | $0.30 / 1M tokens | More expensive output than Llama 3.1 8B; kept as fallback for robustness. |

**Optional related model** (used by QU and Composer, not the locked primary RAG path unless configured via env):

- `llama-3.3-70b-versatile`: 131,072-token context window, 32,768 max output tokens, $0.59 / 1M input tokens, $0.79 / 1M output tokens.

### 9.4 Rate Limits and Why the Delay Exists

- Groq rate limits are account/model/plan dependent. Do not assume a universal RPM/TPM/RPD number.
- During Step 2A evaluation, `llama-3.1-8b-instant` hit Groq 429 rate-limit errors on full-file manual runs without throttling.
- `get_rule_bundles()` performs **10 sequential LLM calls** (some bundles use two-query strategies). Without delay, this risks 429 errors or provider overload at startup.
- Therefore `RAG_RULE_BUNDLE_DELAY_SECONDS` exists. Suggested values:

| Value | Use case |
|---|---|
| `2.0` (default) | Faster startup; acceptable risk if Groq quota is healthy |
| `5.0` | Safer default for live demos |
| `8.0` | Safest — used during manual verification in Step 2D |

**Future improvement:** cache or version rule bundles instead of regenerating them on every startup. This would also eliminate startup rate-limit risk.

### 9.5 Provider / API Technical Details

| Detail | Value |
|---|---|
| Provider | Groq |
| API style | OpenAI-compatible Chat Completions |
| Endpoint | `https://api.groq.com/openai/v1/chat/completions` |
| Authentication | `Authorization: Bearer <GROQ_API_KEY>` |
| HTTP library | `requests.post` (direct, not via shared `LLMClient`) |
| Timeout | `RAG_TIMEOUT_SECONDS` (default 60s) |
| JSON output | `response_format: {"type": "json_object"}` enforced in every call |
| Temperature | `0.1` (low randomness for consistent extraction) |

### 9.6 Why RAG Does Not Use the QU/Composer Model Chains

QU and Composer share the `LLMClient` gateway with their own `LLM_API_KEY`, `LLM_BASE_URL`, `QU_PRIMARY_MODEL`, `COMPOSER_PRIMARY_MODEL`, etc.

RAG uses direct `requests.post` to Groq with a separate key and model chain. This separation is intentional because:

- RAG has strict extraction prompts (`SYSTEM_INSTRUCTION`, `SYSTEM_INSTRUCTION_STRUCTURED`) that require JSON output enforcement and low temperature.
- RAG manages its own retrieval context assembly before the LLM call.
- Sharing the chain with QU/Composer would require threading retrieval into the shared client.

Possible future improvement: unify under a shared client only after confirming RAG extraction behavior is identical and no tests regress.

---

## 10. RAGAdapter Contract

**File:** `adapters/rag_adapter.py` — class `RAGAdapter`

`RAGAdapter` is the **only** backend-facing interface to the RAG engine. The Orchestrator must not import `rag_core` directly.

### `execute(sub_query, student_context=None)`

**Purpose:** Normal handbook policy query.

**Privacy:** `student_context` is accepted to match the Orchestrator call signature but is **never forwarded to the RAG engine** (hard boundary). Only `sub_query` reaches `extract_facts()`.

**Output shape:**

```python
{
    "found":            bool,
    "answer":           str,
    "extracted_facts":  list[str],
    "citations":        list[dict],   # [{"source": "CIS Handbook", "page": N, "text": "..."}]
    # "error": str  — only present on infrastructure/input failure
}
```

**Exit paths:**

| Condition | `found` | `answer` | `error` |
|---|---|---|---|
| Evidence found | `True` | `" ".join(extracted_facts)` | absent |
| No evidence | `False` | `"Not found in handbook."` | absent |
| Empty / whitespace query | `False` | `"Not found in handbook."` | `"empty_query"` |
| RAG unavailable (init failed) | `False` | `"The handbook search service is currently unavailable."` | `"rag_unavailable"` |
| rag_core safe error code | `False` | `"I could not search the handbook safely right now."` | preserved code |
| Unexpected exception in fn | `False` | `"I could not search the handbook safely right now."` | `"rag_adapter_error"` |

### `execute_structured(sub_query, expected_schema)`

**Purpose:** Schema-forced extraction, primarily called by `get_rule_bundles()`. Not for direct Orchestrator use.

**Output shape:**

```python
{
    "data":      dict,
    "citations": list[dict],
    # "error": str  — only present on failure
}
```

**Exit paths:**

| Condition | `data` | `error` | `citations` |
|---|---|---|---|
| Success | extracted dict | absent | built from source_documents |
| Empty query | `{}` | `"empty_query"` | `[]` |
| Invalid schema | `{}` | `"invalid_schema"` | `[]` |
| RAG unavailable | `{}` | `"rag_unavailable"` | `[]` |
| rag_core safe error | `{}` | preserved code | built from available source_documents |
| Unexpected exception | `{}` | `"rag_adapter_error"` | `[]` |

### `get_rule_bundles(inter_call_delay=None)`

**Purpose:** Load all 8 ALE rule bundles at startup by querying RAG with expected schemas.

**Delay behavior:**
- If `inter_call_delay=None` (default), reads `RAG_RULE_BUNDLE_DELAY_SECONDS` from env (default 2.0).
- Explicit argument takes precedence over env.
- `time.sleep(inter_call_delay)` is called after each LLM call.

**The 8 rule bundles (in extraction order):**

| Key | Pydantic class | Handbook source |
|---|---|---|
| `grading_scale_rules` | `GradingScaleRules` | Handbook p.11 Table 1 |
| `graduation_requirement_rules` | `GraduationRequirementRules` | Handbook p.10–11 (two-query) |
| `academic_warning_rules` | `AcademicWarningRules` | Handbook p.9 (two-query) |
| `honors_rules` | `HonorsRules` | Handbook p.12 |
| `credit_limit_rules` | `CreditLimitRules` | Handbook p.7 (two-query) |
| `retake_rules` | `RetakeRules` | Handbook p.8–9 |
| `summer_semester_rules` | `SummerSemesterRules` | Handbook p.7 |
| `student_level_rules` | `StudentLevelRules` | Handbook section 2a |

**Post-processing applied:**

| Bundle | Post-processing |
|---|---|
| `graduation_requirement_rules` | Deterministic normalization: `must_pass_zero_credit_courses=True` (required for all students), `military_training_required_for_males=True` (required for male students only). Both are forced to `True` regardless of LLM output — handbook explicitly states these requirements. Protects against unsafe false defaults from structured extraction. |
| `academic_warning_rules` | `dismissal_extension_credits_percentage`: if LLM returns a whole number (e.g., 80), divided by 100.0 to produce 0.80 as expected by ALE. |
| `summer_semester_rules` | Fallback fill: if extraction returns `None` for any of the three fields, handbook-backed constants are used (default=2, CGPA≥3=3, threshold=3.0). Only fills `None`; never overwrites a real extracted value. |
| `grading_scale_rules` | `Abs` grade point normalized to 0.0 if returned as `None`; `percentage_to_letter` converted to `PercentageRange` Pydantic objects. |
| All bundles | `_sanitize_nulls()`: LLM string literals `"null"`, `"none"`, `"n/a"` converted to Python `None` before Pydantic instantiation. |

**Pydantic conversion:** each raw dict is passed to the corresponding Pydantic schema class. If conversion fails, that bundle becomes `None`; other bundles are unaffected. The function returns `{}` only if all 8 conversions fail.

---

## 11. Startup Integration

**File:** `main.py`

```python
_rag = RAGAdapter()                           # loads rag_core, initializes retriever
_rule_bundles = _rag.get_rule_bundles()       # 10 LLM calls with inter-call delay
```

- If `_rule_bundles` is empty, a warning is logged and ALE intents may return engine errors.
- Rule-bundle extraction is the primary source of startup latency for the RAG component (Phase 0 measured ~108s total startup including embedding/reranker load).
- Rule bundles are passed to `orchestrator.execute_turn()` on every request and consumed by ALE.
- `RAG_RULE_BUNDLE_DELAY_SECONDS` can be increased if Groq rate-limit or overload errors occur at startup.

---

## 12. Logging and Observability

Logging was added as part of Phase 1 Step 2 (RAGAdapter observability patch). Logger name: `adapters.rag_adapter`.

**Sample log lines:**

```text
RAGAdapter.execute start query_len=42 query_preview='What is the minimum attendance percentage?'
RAGAdapter.execute result found=True facts=2 citations=1 answer_preview='Students must maintain 75% attendance...' duration_ms=1847
RAGAdapter.execute result found=False facts=0 citations=0 error=empty_query duration_ms=0
RAGAdapter.execute result found=False facts=0 citations=0 error=rag_llm_error duration_ms=3210
RAGAdapter.execute_structured start query_len=65 query_preview='What are graduation requirements...' schema_keys=['total_credits_required', 'minimum_cgpa']
RAGAdapter.execute_structured result data_keys=['total_credits_required', 'minimum_cgpa'] citations=1 duration_ms=2103
RAGAdapter.get_rule_bundles start inter_call_delay=2.00
RAGAdapter.get_rule_bundles bundle=grading_scale_rules status=ok model=GradingScaleRules
RAGAdapter.get_rule_bundles bundle=graduation_requirement_rules status=ok model=GraduationRequirementRules
RAGAdapter.get_rule_bundles bundle=summer_semester_rules status=ok model=SummerSemesterRules
RAGAdapter.get_rule_bundles complete loaded=8 missing=[] duration_ms=94317
```

**Safety guarantees:**
- No full prompts in logs
- No full source document text in logs
- No student context in logs
- Only short previews (≤180 chars via `_safe_preview()`), counts, and error codes

---

## 13. Testing Summary

| Test file | Cases | Result | What it proves |
|---|---|---|---|
| `tests/test_rag_adapter_execute.py` | 10 | 10 passed ✅ | `execute()` contract: all exit paths, privacy boundary, citation building, error code preservation |
| `tests/test_rag_adapter_structured.py` | 9 | 9 passed ✅ | `execute_structured()` contract: all exit paths, error preservation, malformed source_documents |
| `tests/test_rag_core_structured.py` | 8 | 8 passed ✅ | `extract_structured()` in rag_core: error codes, empty/invalid guard, fallback chain, safety |
| `tests/test_rag_rule_bundles.py` | 16 | 16 passed ✅ | Rule-bundle conversion, Pydantic values, normalization, delay config, partial failure isolation |

**Manual evaluation (Step 2B Round 3):**

| Metric | Value |
|---|---|
| Total queries | 64 |
| PASS | 53 |
| WEAK | 11 |
| FAIL | 0 |

Round 3 was the final regression sweep after prompt hardening. WEAK items are accepted carry-forwards (minor duplicates, excerpt-boundary truncation, wording imprecision). No blocking failures remained.

**Total focused RAG unit tests: 43 passed, 0 failed.**

**Key confirmed behaviors:**
- All unit tests run without a live Groq connection (stubs/monkeypatching).
- `get_rule_bundles(inter_call_delay=8.0)` returned all 8 bundles successfully in live verification.
- Graduation boolean normalization (`must_pass_zero_credit_courses`, `military_training_required_for_males`) correctly overrides `False` → `True` in test and live runs.

---

## 14. Known Limitations and Carry-Forward Items

| Item | Notes |
|---|---|
| Slow startup | Embedding model (`BAAI/bge-small-en-v1.5`) and cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`) load at first `get_retriever()` call. Rule-bundle extraction adds 10 LLM calls with delay. Phase 0 measured ~108s total startup. Deferred to Phase 5. |
| Groq rate limits | Live rule-bundle extraction can hit 429 errors without adequate delay. Increase `RAG_RULE_BUNDLE_DELAY_SECONDS` if this occurs. |
| LangChain Chroma deprecation | `from langchain_community.vectorstores import Chroma` emits a deprecation warning. Migration to `langchain_chroma` is Phase 5 cleanup. |
| Handbook-only answers | RAG can only extract what is in `CIS_Handbook.md`. Questions outside handbook scope return `found=False`. |
| Structured extraction is LLM-dependent | Critical booleans are normalized after extraction, and Pydantic conversion protects schema shape. But non-normalized fields still depend on LLM accuracy. |
| Rule bundle caching | Bundles should eventually be cached/versioned for production. Currently regenerated on every startup, which is slow and Groq-dependent. |
| Provider specs may change | Groq model IDs, pricing, rate limits, and availability documented in Section 9.3 were verified 2026-06-22. Recheck before final demo/deployment. |
| GPT-OSS model availability | `openai/gpt-oss-20b` on Groq may be preview/permission-dependent. If unavailable, update `RAG_FALLBACK_MODELS` to another supported model. |
| WEAK queries (Round 3) | 11 queries returned correct but imprecise or slightly duplicated facts. These are acceptable at RAG level — Composer refines wording and ALE provides deterministic values where precision matters. |

---

## 15. How to Rebuild / Run / Verify RAG

**Rebuild the index (after handbook changes):**

```powershell
python -m engines.rag.ingest
```

**Start the backend:**

```powershell
python -m uvicorn main:app --reload
```

Or see `README.md` for the current startup command if it differs.

**Manual live rule-bundle check:**

```python
from adapters.rag_adapter import RAGAdapter

rag = RAGAdapter()
bundles = rag.get_rule_bundles(inter_call_delay=8.0)
for k, v in bundles.items():
    print(k, type(v).__name__ if v else None)
    if v:
        print(v.model_dump())
```

**Run unit tests:**

```powershell
pytest tests/test_rag_adapter_execute.py -v
pytest tests/test_rag_adapter_structured.py -v
pytest tests/test_rag_core_structured.py -v
pytest tests/test_rag_rule_bundles.py -v
```

**Verify no compile errors:**

```powershell
python -m py_compile adapters/rag_adapter.py engines/rag/rag_core.py engines/rag/retriever.py engines/rag/ingest.py
```

**Manual evaluation runner (not CI):**

```powershell
python -m engines.rag.manual_eval.rag_query_runner --model llama-3.1-8b-instant --queries-file engines/rag/manual_eval/rag_policy_deep_tests.txt --delay-seconds 6
```

---

## 16. Design Boundaries

| Component | Responsibility |
|---|---|
| **QU** | Decides intent; rewrites policy questions into self-contained handbook questions before routing to RAG |
| **Orchestrator** | Routes `policy_query` intent to `RAGAdapter.execute()`; routes rule-bundle startup to `get_rule_bundles()` |
| **RAG** | Retrieves handbook chunks; extracts facts or structured data; does not interpret, decide, or personalize |
| **Composer** | Converts RAG evidence into student-facing language; selects the most relevant fact; adds context and tone |
| **ALE** | Consumes rule bundles from RAG; performs deterministic academic calculations (eligibility, GPA, plans) |
| **KG** | Owns curriculum facts: prerequisites, course-skill mapping, role-skill mapping, track comparisons |

---

## 17. Final Status

```text
Status: LOCKED after Phase 1 Step 2 — PASS WITH FIXES.
RAG is ready for ALE integration and later E2E testing.
```

Further changes should happen only if integration tests expose a real issue. Do not touch `SYSTEM_INSTRUCTION`, retrieval parameters, or rule-bundle extraction logic without running the full test suite and re-running at least a subset of the manual evaluation queries.

Provider/model limits and pricing should be rechecked before final deployment or demo.
