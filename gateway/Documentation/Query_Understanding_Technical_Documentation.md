# PathFinder — Query Understanding (QU) Technical Documentation

**Status:** PASS / QU LOCKED WITH CARRY-FORWARD NOTES  
**Component:** Query Understanding (QU)  
**Phase:** Phase 1 (Step 6) Audit Complete

---

## 1. Component Summary

The Query Understanding (QU) component serves as PathFinder’s dedicated parser and classifier layer. Its primary role is to convert raw, unstructured student text into an ordered list of structured intents (`list[StructuredQuery]`). 

Key principles of QU:
* **No Answering:** QU does not generate natural language responses or answer the user directly.
* **Isolation:** QU does not call the Academic Logic Engine (ALE), Retrieval-Augmented Generation (RAG), Composer, Orchestrator, or Knowledge Graph (KG) business operations.
* **KG Restriction:** QU may interact with the KG solely through the `resolve_entity` function, which is injected as a resolver.
* **Strict Privacy:** QU must never receive or send full `StudentContext`, student IDs, names, CGPA, grades, transcripts, or course history to the LLM.

## 2. Responsibility Boundaries

### QU Owns:
* Intent classification strictly limited to the 26 locked intents.
* Multi-intent decomposition for compound queries.
* Entity mention extraction.
* Parameter extraction (e.g., target GPA, depth).
* Session override extraction (e.g., "assume I passed X").
* Follow-up handling using `last_referenced`.
* Lightweight deterministic fallback.
* LLM output validation and normalization.
* Optional entity resolution via the injected resolver.

### QU Does NOT Own:
* **ALE:** Academic eligibility, GPA, graduation, and planning decisions.
* **KG:** Curriculum and career facts logic.
* **RAG:** Handbook facts retrieval.
* **SCP:** Student record construction.
* **Session Manager:** Session persistence and historical state management.
* **Orchestrator:** Routing, execution, and enrichment.
* **Composer:** Final natural-language response generation.

## 3. Files and Responsibilities

The QU component is distributed across the following core files:

* `gateway/qu_intents.py`: Defines the locked intent taxonomy and schemas.
* `gateway/qu_preprocessing.py`: Handles deterministic regex and keyword extraction (e.g., course codes, policies, semesters) to aid the LLM and support fallback.
* `gateway/qu_prompt.py`: Houses the massive system prompt that instructs the LLM on JSON formatting, decomposition, and intent mapping.
* `gateway/qu_llm_chain.py`: Manages the retry logic, timeout handling, and fallback progression across multiple configured LLMs.
* `gateway/query_understanding.py`: The main orchestrator of the QU component. Ties together preprocessing, the LLM chain, entity resolution, and deterministic fallback.
* `tests/test_query_understanding.py`: Contains focused unit and integration tests for parsing, schema validation, and fallback mechanisms.
* `scripts/one_query_qu_trial.py`: A controlled manual diagnostic tool used to test single queries against specific models without triggering full system execution.

## 4. Inputs and Outputs

The primary entry point for QU is the `understand_query` function. 

**Signature:**
```python
def understand_query(
    user_text: str,
    last_referenced: dict | None = None,
    recent_turns: list | None = None,
    resolver: Callable | None = None
) -> list[StructuredQuery]:
```

**Output:**
Returns a non-empty `list[StructuredQuery]`. Each `StructuredQuery` contains:
* `intent`: A string representing one of the 26 locked intents.
* `original_text`: The raw user query (or self-contained rewrite for policy queries).
* `entities`: A dictionary or EntitySet of resolved primary entities.
* `secondary_entities`: For comparison queries (e.g., track comparison).
* `params`: Extracted parameters (depth, semester, expected grades, etc.).
* `session_overrides`: Detected assumptions.
* `student_referential_fallback`: Boolean flag indicating if the query relies on personal pronouns or context.

**Operational Safety:**
QU is designed to never raise exceptions to the caller under normal operation. On total failure, it gracefully degrades to deterministic fallback or returns a `clarification_needed` intent.

## 5. Locked Intent Taxonomy

QU is restricted to 26 locked intents grouped by domain:

### Academic Planning:
* `plan_semester`
* `generate_graduation_roadmap`
* `run_graduation_audit`
* `check_course_eligibility`
* `simulate_gpa_forward`
* `solve_target_gpa`

### Course Info:
* `get_course_info`
* `get_course_prerequisites`
* `get_skills_taught`
* `search_courses_by_skill`

### Career / Role:
* `get_role_profile`
* `get_roles_by_track`
* `compute_skill_gap`
* `compute_alignment_score`
* `recommend_courses_to_close_gap`
* `find_best_matching_roles`
* `estimate_alignment_improvement`
* `get_focus_courses_for_target`

### Track:
* `get_track_overview`
* `compare_tracks`
* `recommend_track_for_role`
* `recommend_track_for_skill`

### Policy:
* `policy_query`

### Student Record:
* `get_student_record`

### Control:
* `clarification_needed`
* `out_of_scope`

### Forbidden / Stale Intents
QU explicitly rejects and must remap the following historical intents:
* `get_prerequisites`, `handbook_query`, `check_eligibility`, `simulate_gpa`, `generate_semester_plan`, `mixed_course_policy`, `get_courses_in_track`, `get_track_courses_for_role`, `get_roles_by_skill`, `graduation_audit_with_roadmap`, `compare_courses`, `rank_courses`, `get_course_profile`, `plan_next_semester`.

These are forbidden to enforce a unified terminology layer for the Orchestrator.

## 6. Prompt Design

The core logic of QU is heavily prompt-driven. Major prompt rules include:
* **Output Format:** Strict JSON output only. Must always be an object containing a `queries` array.
* **Intent Constraints:** Use only the locked intents.
* **Clarification Guard:** Output `clarification_needed` if ambiguous.
* **Prerequisite Depth:** Must extract and specify `direct` vs `full` prerequisite depth.
* **Planning vs Career:** `plan_semester` is strictly for course registration scheduling, not career learning recommendations.
* **Course Focus:** Strict boundary between `get_focus_courses_for_target` (general core courses) vs `recommend_courses_to_close_gap` (personalized missing courses).
* **Policy Rewrite:** `policy_query` must have the original text rewritten into a self-contained handbook question.
* **Decomposition:** Compound queries must be split chronologically into multiple intent objects.
* **Overrides:** Detect and extract session overrides.
* **Semester Extraction:** 
  * Explicit semester → `target_semester`, `target_semester_type`, `semester_resolution_source="explicit"`
  * Relative semester → `target_semester_text`, `semester_resolution_source="relative"`
  * Note: QU does not resolve relative years; the Orchestrator handles this using `StudentContext.current_semester`.
* **GPA Tools:** Expected grade extraction for GPA simulation, and target GPA extraction for `solve_target_gpa`.
* **Out-of-Scope Guard:** Broad boundary detection for `out_of_scope` requests.

## 7. Preprocessing

Deterministic preprocessing operates alongside the LLM to improve robustness. It identifies:
* Course codes
* Policy signals
* Out-of-scope signals
* Student-referential usage
* Semester references
* Target CGPA and expected grades
* Override/reset assumptions signals

**Role of Preprocessing:** Preprocessing supports the LLM and powers deterministic fallback. However, it *does not overrule* a clear, structurally valid LLM output. Deterministic fallback acts strictly as a safety net, not the primary classifier.

## 8. LLM Model Chain

### 8.1 Current QU Model Chain

**Current intended config:**
* `QU_PRIMARY_MODEL=llama-3.3-70b-versatile`
* `QU_FALLBACK_MODELS=openai/gpt-oss-120b,openai/gpt-oss-20b`
* `QU_TIMEOUT_SECONDS=30`
* `QU_CONTEXT_TURNS=<current repo/env default>`

**Explanation:**
* Llama 3.3 70B is kept as demo-primary because it performed well in manual trials before quota was exhausted.
* GPT-OSS 120B and GPT-OSS 20B are cheaper fallback / production-candidate models.
* Deprecated/preview fallbacks were removed from default fallback chain.
* The chain moves to the next model on timeout, 429/rate limit, invalid JSON, or invalid intent.
* The fallback chain prevents the `/chat` flow from failing immediately, but it does not solve quota limits if all models are rate-limited.

### 8.2 Current Official Groq Model Data

| Model | Role in QU | Approx speed | Price / 1M input tokens | Price / 1M output tokens | Developer TPM/RPM | Context window | Max completion tokens | Notes for QU |
|---|---|---|---|---|---|---|---|---|
| `llama-3.3-70b-versatile` | primary demo model | 280 t/s | $0.59 | $0.79 | 300K TPM, 1K RPM | 131,072 | 32,768 | strong JSON/function-style behavior in trials; expensive vs GPT-OSS; high token use can hit TPD quickly. Kept as demo-primary; provider lifecycle should be rechecked before production. |
| `openai/gpt-oss-120b` | first fallback / production candidate | 500 t/s | $0.15 | $0.60 | 250K TPM, 1K RPM | 131,072 | 65,536 | cheaper than Llama 70B input; manual trials showed good QU behavior after prompt patch. |
| `openai/gpt-oss-20b` | second fallback / budget candidate | 1000 t/s | $0.075 | $0.30 | 250K TPM, 1K RPM | 131,072 | 65,536 | cheapest and fastest; had one JSON validation error in manual full-prerequisite query, so monitor during Phase 2. |
| `meta-llama/llama-4-scout-17b-16e-instruct` | removed old fallback / not current default | 750 t/s | $0.11 | $0.34 | 300K TPM, 1K RPM | 131,072 | 8,192 | not currently used; max completion is smaller; old chain treated it as fallback but we removed it for stability/simplicity. |
| `llama-3.1-8b-instant` | removed old fallback / not current default | 560 t/s | $0.05 | $0.08 | 250K TPM, 1K RPM | 131,072 | 131,072 | cheap, but removed from default chain due to provider lifecycle/deprecation concerns from prior audit. |
| `qwen/qwen3-32b` | removed old fallback / not current QU default | - | - | - | - | - | - | previously considered but removed from QU fallback chain to keep production-oriented candidates focused. If adding exact pricing/limits, verify against current provider docs first. |

> **Note:** Provider model availability, pricing, and limits can change. Recheck official provider docs before production deployment.

### 8.3 Token Budget and Cost Estimate

**Observed manual testing:**
* QU prompt is large.
* Groq 429 messages showed a request of roughly 5.6K tokens for one QU call.
* This is acceptable for MVP correctness but not ideal for broad testing or production scale.
* With an observed 100K TPD limit on Llama in the testing account, 5.6K tokens/request means roughly 17–18 full QU requests/day before hitting that specific daily limit.

**Cost Estimate (Assuming one typical QU call):**
* input tokens ≈ 5,600
* output tokens ≈ 300

* **Llama 3.3 70B:**
  * input: 5,600 / 1,000,000 × $0.59 ≈ $0.0033
  * output: 300 / 1,000,000 × $0.79 ≈ $0.00024
  * **total ≈ $0.0035 per QU call**

* **GPT-OSS 120B:**
  * input: 5,600 / 1,000,000 × $0.15 ≈ $0.00084
  * output: 300 / 1,000,000 × $0.60 ≈ $0.00018
  * **total ≈ $0.0010 per QU call**

* **GPT-OSS 20B:**
  * input: 5,600 / 1,000,000 × $0.075 ≈ $0.00042
  * output: 300 / 1,000,000 × $0.30 ≈ $0.00009
  * **total ≈ $0.00051 per QU call**

**Interpretation:**
* QU call cost is small for demo.
* Broad testing is limited by TPD/TPM before money.
* Production scale needs paid/enterprise quota and/or prompt compression.

### 8.4 Rate Limit Findings from Manual Trials

* In manual one-query testing, `llama-3.3-70b-versatile` hit TPD after early calls.
* GPT-OSS fallbacks continued to handle most tested queries correctly.
* No more broad live QU testing should be done under current free/on-demand limits.
* Manual single-query trials are the preferred diagnostic method until quotas are upgraded.
* Use delays between calls and avoid parallel tests.

### 8.5 Model Decision

**Final Phase 1 Decision:**
* Keep Llama 3.3 70B as primary for demo for now.
* Keep GPT-OSS 120B then GPT-OSS 20B as fallbacks.
* Do not re-add old preview/deprecated fallback models now.
* Continue monitoring GPT-OSS 20B because it had one JSON validation error.
* Composer model chain must be audited separately because it still may contain stale preview/deprecated defaults.

### 8.6 Production Improvement Roadmap

* **Hybrid deterministic-first QU router for obvious cases:**
  * direct course-code eligibility
  * policy keywords
  * out-of-scope keywords
  * reset assumptions
  * exact GPA/grade patterns
  * direct/full prerequisite code queries
* Shorter prompt / modular prompt sections by query category.
* Cache stable prompt prefix if provider supports prompt caching.
* Better structured-output enforcement if provider/API supports it.
* Paid/enterprise quotas before multi-user deployment.
* Logging already supports diagnosing `source=llm` vs deterministic fallback and model failure behavior.

## 9. Entity Resolution

* QU attempts to extract entity mentions first.
* If a `resolver` is provided, QU tries to resolve course, role, track, and skill mentions via KG `resolve_entity`.
* If `resolver` is None, QU filters out unsafe unresolved mentions.
* Resolved canonical IDs populate the `EntitySet`.
* If resolution fails (ambiguity, not found), QU can degrade the query to `clarification_needed`.
* `LastReferenced` supports context for `course_code`, `role_id`, `track_id`, and `skill_id`.
* Session follow-ups ("it", "that role", "that track", "that skill") strictly depend on `LastReferenced`.
* `compare_tracks` relies heavily on populating `secondary_entities.track_id`.

## 10. Session Context and Privacy

* `recent_turns` are injected by the Session Manager, limited by `QU_CONTEXT_TURNS`.
* `build_user_message` actively strips previous assistant answer text; the full Composer answer is never exposed back to the LLM.
* QU never receives `StudentContext`.
* QU never sends `student_id`, name, grades, CGPA, transcript, or completed courses to the LLM.
* `last_referenced` contains safe curriculum/career IDs only.
* `session_overrides` are extracted but not applied by QU; state persistence is delegated to Session Manager and Orchestrator.

## 11. Session Overrides

**Supported Override Fields:**
* `added_courses`
* `assumed_passed_courses`
* `assumed_failed_courses`
* `target_role`
* `course_override_type`
* `override_action`

**Semantics:**
* "if I pass X / assume I passed X" → `assumed_passed_courses`
* "assume I failed X" → `assumed_failed_courses`
* Planned courses for alignment improvement map to `params.planned_courses` or `added_courses` depending on the current contract.
* "reset assumptions / clear assumptions" → `override_action="clear"`
* Crucially, "yes" or "confirm" alone must not clear assumptions.

## 12. Parameter Normalization

QU enforces strict schemas on extracted parameters:
* `target_cgpa` is normalized to `target_gpa`.
* `target_gpa` must be a float between 0.0 and 4.0.
* `depth` is normalized to `direct` or `full`.
* `expected_grades` is validated as a dictionary.
* `planned_courses` is validated as a list.
* `target_semester_type` normalized to `Fall`, `Spring`, or `Summer`.
* `target_semester` must strictly match `Fall/Spring/Summer YYYY`.
* `semester_resolution_source` must be `explicit` or `relative`.
* Invalid values are aggressively removed or safely normalized.

## 13. Error Handling

* **LLM not configured:** Immediate deterministic fallback.
* **All models failed:** Deterministic fallback.
* **Invalid intent or invalid JSON:** Shift to next model in the chain.
* **Resolver not_found/ambiguous/error:** Replaced with `clarification_needed` where appropriate.
* **Empty result list:** Defaults to `clarification_needed`.
* `understand_query` guarantees returning a non-empty list and never crashing the caller thread.

## 14. Logging

The QU logging contract ensures tight observability without leaking PII.

**Log Points:**
* `QU.start`: `query_len`, `resolver_enabled`, `recent_turns` count, `last_ref` flags.
* `QU.preprocess`: `course_codes` count, boolean flags (policy/oos/student_ref/semester/target_cgpa/override/reset), `expected_grades` count.
* `QU.model_chain`: `model` count, `timeout`.
* `QU model success/failure`: `model` name, `SQ` count or safe error preview.
* `QU.resolve`: `resolver` enabled, `SQ` count before/after, `clarification`/`out_of_scope` count, entity presence counts, `params` keys, override state.
* `QU.resolve_failed`: `intent`, `entity_type`, `resolver` status.
* `QU.result`: `SQ` count, `intent` list, `classification` source, `resolver` enabled, `duration_ms`.

**Privacy Guarantees:**
* Raw `user_text` is NOT logged.
* Raw prompts are NOT logged.
* Raw LLM outputs are NOT logged.
* Raw `recent_turns` are NOT logged.
* Student PII is NOT logged.

## 15. Testing Summary

Testing for QU is centralized in `tests/test_query_understanding.py`.

**Current Results (Post-Logging Patch):**
```bash
python -m pytest tests/test_query_understanding.py -v --tb=short
# 119 passed in 0.28s
```

**Test Categories:**
* Preprocessing extraction and boundary checks
* Schema and intent validation
* Deterministic fallback activation
* LLM mock parsing capabilities
* Rejection of forbidden intents
* Edge case isolation
* Model-chain and configuration handling
* Logging privacy and diagnostics

**Deleted Tests:**
* `tests/test_query_understanding_behavior.py` was intentionally removed.
* Broad live behavior testing is deferred to the Phase 2 integration phase.
* `scripts/one_query_qu_trial.py` remains as a controlled, manual diagnostic tool.

## 16. Manual Live Trial Findings

The manual one-query trial tool allows running a single query against isolated models.
* It does not call `/chat`, Orchestrator, Composer, ALE, RAG, or KG business ops.
* Uses a fake resolver.
* Avoids fallback masking by testing models separately.

**Observed Findings:**
* **Career Gap:** "i wanna be data scientist what am i missing" → correctly mapped to `compute_skill_gap` across models.
* **Important Courses:** "what are the important courses for data scientist" → `get_focus_courses_for_target`.
* **Typo/Personal Focus:** "what courses should i focus on for data scientst" → `get_focus_courses_for_target` (worked on GPT-OSS fallbacks).
* **Track Comparison:** "compare ai and swe" worked on GPT-OSS fallbacks.
* **Next Semester:** Registration phrasing worked on GPT-OSS fallbacks and accurately produced relative semester params.
* **GPA Simulation:** Worked on GPT-OSS fallbacks; successfully resolved "Operating Systems" to `C-CS316`.
* **Policy Query:** CGPA below 2 triggered `policy_query` with a highly accurate self-contained policy rewrite.
* **Full Prerequisites:** Worked on GPT-OSS 120B. GPT-OSS 20B encountered one JSON validation error on this query.
* **Out of Scope:** Queries like "tuition" successfully mapped to `out_of_scope`.
* **Rate Limiting:** `llama-3.3-70b-versatile` hit Groq TPD limits early, explicitly proving that broad live testing is unsustainable under current free/on-demand limits.

**Important Interpretation:**
A query like "what should I study to become a data scientist" returning `recommend_courses_to_close_gap` is entirely acceptable. It is arguably better than `get_focus_courses_for_target` given the personal, goal-oriented wording. Because of rate-limiting, no further broad live testing should be performed at this stage.

## 17. Known Limitations / Carry-Forward Notes

* **Phase 1 Complete:** QU is good enough for Phase 1 but is not yet a perfect, production-scale QU system.
* **Fallback Observation:** Live behavior under fallback models must continue to be watched heavily during Phase 2 integration testing.
* **Token Cost:** The QU prompt is extremely large, causing inherently high token usage.
* **Primary Model:** The Llama primary model serves as demo-only due to provider lifecycle and deprecation statuses.
* **Model Stability:** GPT-OSS 20B exhibited a minor JSON validation issue on a full-prerequisite query, indicating a need for retry resilience.
* **Composer Responsibility:** Composer must correctly handle policy answer narration relevance. It must distinctively narrate `get_focus_courses_for_target` vs `recommend_courses_to_close_gap`, concisely narrate policy results, and clearly distinguish assumptions from official records.
* **Orchestrator Responsibility:** The Orchestrator must correctly consume `depth` for prerequisites, `target_semester_text` / `semester_resolution_source`, explicit `target_semester`, `compare_tracks` secondary entities, and `student_referential_fallback`.
* **Future Production Improvements:**
  * Implement deterministic / router-first QU for obvious intents to save tokens.
  * Shorten prompt context.
  * Provision provider/rate-limit upgrades.
  * Evaluate structured-output provider improvements natively.
  * Build a better live evaluation harness after Phase 2.
* The QU technical documentation includes current provider pricing/limits as of the audit date, but these must be rechecked before production.
* Composer model-chain audit is a separate carry-forward item.

## 18. Final Verdict

**Status:** PASS / QU LOCKED WITH CARRY-FORWARD NOTES

QU is locked for Phase 1 component-audit purposes. It performs accurately under manual load, meets privacy standards, gracefully handles failures, and safely outputs structured data required by downstream systems. It is ready to feed into the Orchestrator audit. Remaining issues revolve around provider scale, token optimization, and Phase 2 downstream integration, none of which block the immediate QU component scope.
