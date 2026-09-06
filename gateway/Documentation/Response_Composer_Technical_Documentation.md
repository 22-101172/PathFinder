# PathFinder — Response Composer Technical Documentation

**Status:** COMPLETE ✅ — PASS / RESPONSE COMPOSER LOCKED  
**Component:** Response Composer  
**Phase:** Phase 1 (Step 8) Audit Complete

---

## 1. Purpose and Responsibility

The Response Composer is PathFinder's student-facing narration layer. It sits at the end of the pipeline, receiving the structured execution results from the Orchestrator and converting them into coherent natural-language responses that a student reads.

Key principles:
* **No Academic Logic:** The Composer does not compute eligibility, GPA, graduation status, semester plans, or any academic decision. All such logic is owned by ALE.
* **No Data Retrieval:** The Composer does not query KG, RAG, ALE, QU, SCP, or Session Manager.
* **No Session Mutation:** The Composer reads what it is given. It never writes to or modifies session state.
* **No Fact Invention:** The Composer never generates course names, credit hours, policy rules, grade thresholds, or academic decisions that were not provided in its input packets.

---

## 2. Responsibility Boundaries

### Composer Owns:
* Sorting `PerSQResult` entries by `sq_index` before narrating.
* Building intent-aware narration packets from each `PerSQResult`.
* Collecting and deduplicating citations from all results.
* Attempting LLM narration using a controlled prompt and packet.
* Falling back to deterministic narration when LLM is disabled, unavailable, empty, off-script, or all models fail.
* Applying student-facing display rules: names first, readable IDs, no internal engine names.
* Enforcing eligibility wording semantics (`in_progress`, `already_completed`, `retake_cap_exceeded`, eligible/not eligible).
* Propagating assumption and override notices when present.
* Personalizing credit-limit responses when CGPA and credit-limit policy evidence are both available.
* Returning a `QueryResponse` to the API Gateway.

### Composer Does NOT Own:
* **QU:** Intent classification, entity extraction, parameter parsing.
* **ALE:** Eligibility decisions, GPA calculations, graduation planning, retake policy application.
* **KG:** Curriculum graph queries, role/skill/track lookups.
* **RAG:** Policy text retrieval, rule bundle extraction.
* **SCP:** StudentContext construction.
* **Session Manager:** Session persistence, override storage, `build_effective_context`.
* **Orchestrator:** Intent routing, engine dispatch, context selection, turn-level status computation.
* **Citation invention:** If upstream results contain no citations, no Sources section is produced.

### Composer Must Never:
* Call KG, RAG, ALE, QU, SCP, or Session Manager.
* Receive raw `StudentContext` (student transcript, grade lists, or raw academic records).
* Mutate session state of any kind.
* Change facts, numbers, grades, credits, eligibility decisions, graduation decisions, policy rules, or citations provided by upstream results.
* Expose internal engine names (`KG`, `Neo4j`, `ALE`, `RAG`, `rule bundle`) to the student.

---

## 3. Files and Responsibilities

* `gateway/response_composer.py`: The entire Response Composer component. All packet extraction, narration logic, LLM invocation, fallback handling, display helpers, and logging.
* `tests/test_response_composer.py`: 125 unit tests covering all intent families, LLM/fallback paths, eligibility semantics, display formatting, credit-limit personalization, citation handling, and logging privacy.

---

## 4. Inputs and Outputs

**Public entry point:** `ResponseComposer.compose()`

```python
def compose(
    self,
    user_text: str,
    turn_wrapper: TurnWrapper,
    session_id: str,
    session_name: str,
) -> QueryResponse:
```

**Inputs:**
* `user_text`: The original student query string. Used as context for LLM narration. Never logged.
* `turn_wrapper`: A `TurnWrapper` containing one `PerSQResult` per sub-query, a `turn_status`, and turn-level flags (`has_error`, `has_clarification`, etc.).
* `session_id`: Used for logging (truncated to 8 characters) and included in `QueryResponse`.
* `session_name`: Included in `QueryResponse`.

**Internal unit:** `PerSQResult` — one per structured sub-query. Contains:
* `intent`: the locked intent name
* `status`: Orchestrator-assigned status (`success`, `informational`, `soft_no_evidence`, `clarification_needed`, `out_of_scope`, `error`)
* `data`: dict of engine results (shape varies by intent)
* `error_code`, `error_category`: present when `status="error"`
* `citations`: list of `Citation` objects when applicable
* `flags`: dict with fields like `assumptions_active`, `assumptions_excluded`, `override_state_active`

**Output:** `QueryResponse`

```python
class QueryResponse(BaseModel):
    session_id: str
    session_name: str
    answer_text: str
    citations: list[Citation]
    status: Literal["ok", "error", "clarification_needed"] = "ok"
```

**Status mapping:**

| `TurnWrapper.turn_status` | `QueryResponse.status` |
|---|---|
| `needs_clarification` | `clarification_needed` |
| `failed` | `error` |
| anything else | `ok` |

---

## 5. Main Execution Flow

For each call to `compose()`:

1. **Sort results** — `PerSQResult` entries sorted by `sq_index` to ensure narrative order matches query order.
2. **Build narration packets** — Each result is passed to `_build_packet(result)`, which dispatches to an intent-aware extractor. The packet contains only safe fields needed for narration; no raw `StudentContext`.
3. **Collect citations** — Citations from all results are accumulated and deduplicated by `(source, page)` key.
4. **Attempt LLM generation** — If `COMPOSER_USE_LLM=true` and at least one model is configured, `_try_llm_chain` is called. It tries the primary model then each fallback in order.
5. **Fallback if needed** — If LLM is disabled, not configured, returns empty text, goes off-script, or all models fail, `_deterministic_answer(packets)` is called.
6. **Return `QueryResponse`** — Includes answer text, citations, status, `llm_used`, and `model_used`.

---

## 6. Narration Packet Design

Each `PerSQResult` is converted to a packet dict before narration. Packet design rules:

* **Intent-aware extraction:** Each intent has a dedicated `_extract_*` helper that pulls only the fields that Composer needs.
* **No raw `StudentContext`:** Packets never contain transcript data, grade lists, or student personal identifiers.
* **List capping:** Long lists (e.g., course lists) are capped to prevent context-window overflow.
* **Assumption/override flags preserved:** `assumptions_active`, `assumptions_excluded`, and `override_state_active` are carried from `PerSQResult.flags` into the packet so narration can include appropriate warnings.
* **Eligibility status propagation:** `eligibility_status` is always extracted and takes precedence over the raw `eligible` boolean in narration.

---

## 7. Supported Intent Families

### Academic Planning (D1)
| Intent | Narration focus |
|---|---|
| `plan_semester` | Recommended courses by semester, credit totals, ALE reason codes |
| `generate_graduation_roadmap` | Multi-semester plan, target graduation semester, per-semester course lists |
| `run_graduation_audit` | Graduation status, missing requirements, warnings, honors/standing |
| `check_course_eligibility` | Eligibility decision with correct status semantics (see §9) |
| `simulate_gpa_forward` | Projected CGPA, course/grade combinations, simplified projection notice |
| `solve_target_gpa` | Required grades or impossible target, readable course labels |

### Course Information (D2)
| Intent | Narration focus |
|---|---|
| `get_course_info` | Course name, code, credits, description, level |
| `get_course_prerequisites` | Prerequisite tree with readable course names |
| `get_skills_taught` | Skills in readable form, no raw `SK_*` IDs |
| `search_courses_by_skill` | Courses with matched skill names (not raw IDs) |

### Career / Role (D3)
| Intent | Narration focus |
|---|---|
| `get_role_profile` | Role name, required skills in readable form |
| `get_roles_by_track` | Related/connected roles, not guaranteed careers |
| `compute_skill_gap` | Missing skills in readable form, completed coverage |
| `compute_alignment_score` | Curriculum-skill alignment percentage, not employability guarantee |
| `recommend_courses_to_close_gap` | Gap-closing courses, not a registration plan |
| `find_best_matching_roles` | Top matching roles with alignment scores |
| `estimate_alignment_improvement` | Score delta from adding planned courses |
| `get_focus_courses_for_target` | High-impact courses for target, not a registration plan |

### Track (D4)
| Intent | Narration focus |
|---|---|
| `get_track_overview` | Track name, connected roles, skill areas |
| `compare_tracks` | Side-by-side comparison of two tracks |
| `recommend_track_for_role` | Track with best curriculum fit for a target role |
| `recommend_track_for_skill` | Track with best coverage of a target skill |

### Policy (D5)
| Intent | Narration focus |
|---|---|
| `policy_query` | Policy facts from RAG extracted facts, with citations |

### Student Record (D6)
| Intent | Narration focus |
|---|---|
| `get_student_record` | Completed courses, in-progress courses, CGPA, standing, credit summary |

### Control
| Intent | Narration focus |
|---|---|
| `clarification_needed` | Student-friendly clarification request, no internal engine names |
| `out_of_scope` | Polite decline, redirect to academic topics |

---

## 8. Student-Facing Display Rules

These rules apply to all narration paths (LLM-guided and deterministic):

* **Courses:** `Course Name (COURSE_CODE)` — e.g., `Advanced Programming (C-CS219)`
* **Roles:** Readable role name only — e.g., `Data Scientist` (never `RL_Data_Scientist`)
* **Skills:** Readable skill name only — e.g., `Machine Learning` (never `SK_Machine_Learning`)
* **Tracks:** `Track Name (TRACK_ID)` — e.g., `Data Science and Engineering (DSE)` using `_TRACK_DISPLAY_MAP`
* **No raw IDs:** `RL_*` and `SK_*` IDs are stripped; readable fallback conversion applied:
  * `RL_Data_Scientist` → `Data Scientist`
  * `SK_Machine_Learning` → `Machine Learning`
* **Career framing:** Alignment scores describe curriculum-skill alignment, not employability or job guarantees.
* **Roles by track:** Must be described as "related" or "connected" roles, not guaranteed career outcomes.
* **Focus/gap courses:** Must not be narrated as registration plans unless ALE planning produced them.

Display helpers:
* `_fmt_course_label(name, code)` → `"Name (CODE)"`
* `_fmt_role_label(role_name)` → strips `RL_` prefix if name not available
* `_fmt_skill_label(skill_name)` → strips `SK_` prefix if name not available
* `_fmt_track_label(track_id)` → maps via `_TRACK_DISPLAY_MAP`
* `_safe_code_name(name, code)` → `"Name (CODE)"` format

---

## 9. Eligibility Wording Rules

The `check_course_eligibility` narration must check `eligibility_status` first, before the `eligible` boolean. Status semantics:

| `eligibility_status` | Required narration |
|---|---|
| `in_progress` | "You are already enrolled in / currently taking [course]." |
| `already_completed` | "You have already passed / completed [course]." |
| `retake_cap_exceeded` | "You have reached the retake cap for [course]." |
| `eligible` (or `eligible=True`) | "You are eligible to take [course]." |
| `not_eligible` (or `eligible=False`) | "You are not currently eligible. [Explain missing prerequisites or reason]." |

The `_extract_eligibility` helper always maps the ALE `status` field to `eligibility_status` and extracts `target_course_name` (or falls back to `course_name` or `name`).

---

## 10. Assumption and Override Wording

When assumption/override flags are present in `PerSQResult.flags`, narration must include appropriate notices:

| Flag | Required notice |
|---|---|
| `assumptions_active=True` | Warn that the answer reflects what-if assumptions, not the official academic record |
| `assumptions_excluded=True` | Warn that graduation audit uses the official record only and ignores active assumptions |
| `override_state_active=True` | Warn that scenario overrides are active and the answer reflects an adjusted view |

**Reset assumptions wording (target):**
> "I cleared your what-if assumptions. You are back to your official academic record."

**Current limitation (carry-forward):** The deterministic reset-assumptions wording is blocked until the Orchestrator propagates a structured `assumptions_cleared=True` flag into `PerSQResult.data`. Currently, the LLM system prompt includes the correct safe wording, but the deterministic path has no reliable signal to detect a clear event. The Orchestrator was intentionally not modified in Step 8.

---

## 11. LLM Model Chain and Configuration

Composer uses a separate model chain from QU. This is intentional: both QU and Composer run within a single `/chat` request, and sharing models between them would increase rate-limit contention.

**Current configuration:**

```env
COMPOSER_USE_LLM=true
COMPOSER_PRIMARY_MODEL=qwen/qwen3-32b
COMPOSER_FALLBACK_MODELS=llama-3.1-8b-instant,openai/gpt-oss-20b
COMPOSER_TIMEOUT_SECONDS=30
```

`llama-3.3-70b-versatile` was explicitly removed from Composer fallbacks because QU uses it as its primary model. Sharing it on a single `/chat` request caused rate-limit contention and degraded reliability.

**Model chain behavior:**
1. Try `COMPOSER_PRIMARY_MODEL`.
2. If it fails, times out, or returns empty: try each model in `COMPOSER_FALLBACK_MODELS` in order.
3. If all models fail: use deterministic fallback.

---

## 12. Model Specifications and Provider Notes

*Data as of 2026-06-24. Source: Groq supported-models documentation and Groq pricing page.*

| Model | Status | Role | Speed | Input price | Output price | Context | Max completion |
|---|---|---|---|---|---|---|---|
| `qwen/qwen3-32b` | Preview | Composer primary | 400 t/s | $0.29 / 1M | $0.59 / 1M | 131,072 | 40,960 |
| `llama-3.1-8b-instant` | Production | Fast/cheap fallback | 560 t/s | $0.05 / 1M | $0.08 / 1M | 131,072 | 131,072 |
| `openai/gpt-oss-20b` | Production | Second fallback | 1,000 t/s | $0.075 / 1M | $0.30 / 1M | 131,072 | 65,536 |

**`qwen/qwen3-32b` (Composer primary):**
* Developer limits: 300K TPM / 1K RPM.
* **Preview model warning:** Preview models are intended for evaluation. They may be discontinued at short notice and should not be used as long-term production dependencies.

**`llama-3.1-8b-instant` (first fallback):**
* Developer limits: 250K TPM / 1K RPM.
* Production model — stable for production use.

**`openai/gpt-oss-20b` (second fallback):**
* Developer limits: 250K TPM / 1K RPM.
* Production model.
* Cached input price: $0.0375 per 1M tokens (per Groq pricing page).
* Cached tokens do not count toward rate limits per Groq documentation.

**General Groq notes:**
* Rate limits listed above are developer-tier values from Groq documentation.
* Actual account limits must be verified in the Groq console, as they may differ by tier.
* Current Groq docs distinguish RPM, RPD, TPM, TPD, ITPM, and OTPM. These are separate dimensions.
* Cached tokens do not count toward rate limits per Groq documentation.

**Production roadmap option:**

For a more stable production configuration that eliminates preview-model dependency:
```env
COMPOSER_PRIMARY_MODEL=openai/gpt-oss-20b
COMPOSER_FALLBACK_MODELS=llama-3.1-8b-instant
```
This uses only production models and maintains speed headroom via `llama-3.1-8b-instant` as fallback.

---

## 13. LLM Safety and Fallback Behavior

The following safety mechanisms are applied to every LLM response before it reaches the student:

* **Qwen `<think>` stripping:** `qwen/qwen3-32b` sometimes emits chain-of-thought wrapped in `<think>...</think>`. These blocks are stripped before the answer is used.
* **Off-script detection:** If the LLM response asks the student for information the system already has or could retrieve (e.g., "Can you tell me your student ID?" or "What is your CGPA?"), it is treated as off-script and the deterministic fallback is used instead.
* **Fabricated-source stripping:** If the LLM output contains a Sources or References section that was not derived from real upstream citations, it is removed.
* **Empty response fallback:** If the LLM returns an empty or whitespace-only answer, the deterministic fallback is used.
* **All-model-failed fallback:** If every model in the chain (primary + all fallbacks) fails or times out, the deterministic fallback is used unconditionally.
* **`COMPOSER_USE_LLM=false` deterministic path:** When LLM narration is disabled entirely, Composer skips the LLM chain and goes directly to `_deterministic_answer(packets)`. All formatting, eligibility wording, and display rules still apply.

---

## 14. Citation Behavior

* Composer collects citations from all `PerSQResult` entries in the turn.
* Citations are deduplicated by `(source, page)` key before being included in `QueryResponse`.
* Composer only exposes citations that were already provided by upstream results (primarily from RAG policy queries).
* Composer never invents sources, page numbers, or handbook references.
* If no real citations exist in the upstream results, no Sources section is produced in the answer.
* Current `Citation` schema in `QueryResponse` preserves `source` and `page` only. Rich excerpt text is not preserved in the current schema.

---

## 15. Credit-Limit Personalization

For turns that combine a `get_student_record` result and a `policy_query` result, Composer can personalize the maximum credit-hour answer when both CGPA evidence and credit-limit policy evidence are present in the same turn.

The `_personalize_credit_limit(student_packet, policy_packet)` helper applies only when the policy packet is clearly about credit limits (detected by keyword matching in the policy content).

**Deterministic credit-limit mapping (from QU handbook):**

| CGPA range | Maximum credit hours |
|---|---|
| > 3.0 | 21 credit hours |
| 2.0 – 3.0 | 18 credit hours |
| 1.0 – 2.0 | 15 credit hours |
| < 1.0 | 12 credit hours |

This personalization uses only data already present in the narration packets; it does not call any engine or read student records directly.

---

## 16. Logging Design

All log entries use structured `key=value` format.

**Logging helpers:**

| Helper | Purpose |
|---|---|
| `_duration_ms(start)` | Computes elapsed milliseconds from a `time.time()` start |
| `_safe_session_id(session_id)` | Returns first 8 characters of session ID only |
| `_summarize_packets(packets)` | Returns a safe dict with intent/status counts only; no content |

**Start log (`compose()` entry):**

| Field | Value |
|---|---|
| `session` | First 8 chars of `session_id` |
| `turn_status` | Value from `TurnWrapper.turn_status` |
| `results` | Count of `PerSQResult` entries |

**Result log (`compose()` exit):**

| Field | Value |
|---|---|
| `session` | First 8 chars of `session_id` |
| `qr_status` | `QueryResponse.status` |
| `llm_used` | Whether LLM narration was used |
| `model` | Model that produced the answer, or `None` |
| `fallback_reason` | Why fallback was used (`llm_disabled`, `llm_not_configured`, `all_models_failed`, `empty_response`, `off_script`) |
| `answer_len` | Character count of answer text |
| `citations` | Count of deduplicated citations |
| `duration_ms` | Total Composer time in milliseconds |
| `packet_summary` | Safe summary from `_summarize_packets` (intents + statuses, no content) |

**LLM attempt logs (per model tried):**

| Event | Fields |
|---|---|
| Model success | `model`, `answer_len` |
| Empty response | `model`, result=`empty` |
| Model failure | `model`, result=`failed` |
| Not configured | result=`not_configured` |

**Explicitly NOT logged:**
* Raw user text
* Full LLM prompt
* Full narration packet content
* Final answer text
* Student ID or student name
* Transcript or course history
* Individual grades

---

## 17. Error and Status Handling

Terminal `PerSQResult.status` values that Composer treats as non-productive:

| Status | Composer behavior |
|---|---|
| `error` | Produces a student-friendly error message without leaking error codes, engine names, or stack traces |
| `clarification_needed` | Produces a polite clarification request; routes `QueryResponse.status` to `clarification_needed` |
| `out_of_scope` | Produces a polite out-of-scope decline; returned as `ok` status (not an error) |
| `soft_no_evidence` | "I couldn't find specific policy information" — does not imply system failure |
| `informational` | Narrates the not-found or not-applicable result student-friendly |

All error messages produced by the deterministic fallback use generic, safe wording. Internal engine names (`KG`, `Neo4j`, `ALE`, `RAG`, `rule bundle`) never appear in student-facing output.

---

## 18. Test Coverage Summary

**Test file:** `tests/test_response_composer.py`  
**Final result: 125 passed, 0 failed**

All tests use mocked LLM calls. No live API calls are made.

**Coverage areas:**

| Category | Tests |
|---|---|
| LLM success path | Yes |
| LLM disabled path (`COMPOSER_USE_LLM=false`) | Yes |
| LLM failure / all-models-failed path | Yes |
| Deterministic fallback (every trigger reason) | Yes |
| Every `PerSQResult.status` family | Yes |
| Multi-SQ ordering and combined narration | Yes |
| Citation merging and deduplication | Yes |
| Assumption/override notice propagation | Yes |
| Display formatting helpers (`_fmt_*`, `_safe_code_name`) | Yes |
| Eligibility status semantics (`in_progress`, `already_completed`, `retake_cap_exceeded`) | Yes |
| Role/skill/track ID cleanup (no raw `RL_*` / `SK_*`) | Yes |
| Plan/roadmap name-first formatting | Yes |
| Credit-limit personalization | Yes |
| Logging privacy and diagnostic fields (`TestComposerLogging`, 7 tests) | Yes |
| No engine calls / no raw `StudentContext` leakage | Yes |
| Qwen `<think>` tag stripping | Yes |
| Off-script detection and fallback | Yes |
| Fabricated-source stripping | Yes |

---

## 19. Known Carry-Forwards (Non-Blocking)

| ID | Item | Deferred To |
|---|---|---|
| COMP-CF-1 | Deterministic reset-assumptions wording requires Orchestrator to propagate `assumptions_cleared=True` into `PerSQResult.data` | Orchestrator enhancement (post Phase 1) |
| COMP-CF-2 | Full chatbot quality and intent-behavior correctness deferred to Phase 2 integration, E2E, and manual behavior testing | Phase 2 |
| COMP-CF-3 | Production model-chain hardening: replace `qwen/qwen3-32b` preview primary with a production model (e.g., `openai/gpt-oss-20b`) before long-term deployment | Production readiness |
| COMP-CF-4 | Rich citation excerpts are not preserved in current `QueryResponse.Citation` schema; source/page only | Schema evolution (if needed) |

---

## 20. Final Verdict

**Status:** COMPLETE ✅ — PASS / RESPONSE COMPOSER LOCKED

The Response Composer correctly narrates all 26 locked intents, enforces name-first display formatting, applies correct eligibility status semantics, personalizes credit-limit responses, propagates assumption and override notices, strips unsafe LLM output, falls back deterministically when LLM is unavailable, and produces privacy-safe structured logs. No raw `StudentContext` is exposed. No engine calls are made. 125 tests pass. Composer is component-locked and ready for API Gateway audit and later integration testing.
