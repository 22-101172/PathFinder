#!/usr/bin/env python3
"""
Controlled one-query QU trial (Step 6D-1 diagnostic).

Tests a single query against each QU model SEPARATELY — no fallback chain.
Each model is tried alone so you see exactly which model succeeds or fails.

Does NOT call /chat, Orchestrator, Composer, ALE, RAG, or KG.
Uses fake resolver only (no Neo4j needed).

Usage:
  python scripts/one_query_qu_trial.py \\
    --query "i wanna be data scientist what am i missing" \\
    --expected-intent compute_skill_gap \\
    --delay 15 --timeout 30

  python scripts/one_query_qu_trial.py \\
    --query "what focus courses should i still take for data scientist" \\
    --expected-intent get_focus_courses_for_target \\
    --expected-param student_referential_fallback=true \\
    --delay 15

  python scripts/one_query_qu_trial.py \\
    --query "compare ai and data science" \\
    --expected-intent compare_tracks \\
    --models llama-3.3-70b-versatile \\
    --delay 5

Environment overrides:
  QU_TIMEOUT_SECONDS   per-call timeout (default: 30)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

# ── Late imports (after sys.path and dotenv) ──────────────────────────────────
from gateway.llm_client import LLMError, get_llm_client, parse_json_object
from gateway.models.schemas import LastReferenced
from gateway.qu_intents import LOCKED_INTENTS
from gateway.qu_llm_chain import IntentValidationError, _extract_sq_list, _validate_intents
from gateway.qu_prompt import build_system_prompt, build_user_message
from gateway.query_understanding import _parse_raw_sq, _resolve_sq


# ── Fake resolver (no Neo4j needed) ──────────────────────────────────────────

_RESOLVE_DB: dict[str, dict[str, dict]] = {
    "course": {
        "operating systems": {"status": "ok", "resolved_id": "C-CS316"},
        "os": {"status": "ok", "resolved_id": "C-CS316"},
        "advanced programming": {"status": "ok", "resolved_id": "C-CS219"},
        "oop": {"status": "ok", "resolved_id": "C-CS112"},
        "data structures": {"status": "ok", "resolved_id": "C-CS221"},
        "machine learning": {"status": "ok", "resolved_id": "C-AI421"},
        "databases": {"status": "ok", "resolved_id": "C-CS231"},
        "intro ai": {"status": "ok", "resolved_id": "C-AI311"},
    },
    "role": {
        "data scientist": {"status": "ok", "resolved_id": "RL_Data_Scientist"},
        "ml engineer": {"status": "ok", "resolved_id": "RL_ML_Engineer"},
        "machine learning engineer": {"status": "ok", "resolved_id": "RL_ML_Engineer"},
        "software engineer": {"status": "ok", "resolved_id": "RL_Software_Engineer"},
        "cybersecurity analyst": {"status": "ok", "resolved_id": "RL_Cybersecurity_Analyst"},
        "backend engineer": {"status": "ok", "resolved_id": "RL_Backend_Engineer"},
    },
    "track": {
        "ai": {"status": "ok", "resolved_id": "AI"},
        "cyber": {"status": "ok", "resolved_id": "CYS"},
        "cybersecurity": {"status": "ok", "resolved_id": "CYS"},
        "cys": {"status": "ok", "resolved_id": "CYS"},
        "data science": {"status": "ok", "resolved_id": "DSE"},
        "dse": {"status": "ok", "resolved_id": "DSE"},
        "software engineering": {"status": "ok", "resolved_id": "SWE"},
        "swe": {"status": "ok", "resolved_id": "SWE"},
    },
    "skill": {
        "database": {"status": "ok", "resolved_id": "SK_Database_Design"},
        "databases": {"status": "ok", "resolved_id": "SK_Database_Design"},
        "python": {"status": "ok", "resolved_id": "SK_Python"},
        "cybersecurity": {"status": "ok", "resolved_id": "SK_Cybersecurity"},
        "machine learning": {"status": "ok", "resolved_id": "SK_Machine_Learning"},
    },
}


def fake_resolver(entity_type: str, entity_text: str) -> dict:
    lower = entity_text.lower().strip()
    entry = _RESOLVE_DB.get(entity_type, {}).get(lower)
    if entry is None:
        # Prompt instructs LLM to use underscore format (e.g. "data_scientist").
        # Normalize underscores to spaces for the lookup.
        normalized = lower.replace("_", " ")
        entry = _RESOLVE_DB.get(entity_type, {}).get(normalized)
    return entry if entry is not None else {"status": "not_found"}


# ── Rate-limit signal detector ────────────────────────────────────────────────

class _RateLimitDetector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.detected = False
        self.last_message = ""

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        if "429" in msg or "rate" in msg.lower() or "rate_limit" in msg.lower():
            self.detected = True
            self.last_message = msg

    def reset(self) -> None:
        self.detected = False
        self.last_message = ""


_rl_detector = _RateLimitDetector()
logging.getLogger("gateway").addHandler(_rl_detector)
logging.getLogger("gateway").setLevel(logging.WARNING)


# ── Rate-limit type detection ─────────────────────────────────────────────────

def _detect_rl_type(msg: str) -> str:
    lower = msg.lower()
    if "tokens per day" in lower or " tpd" in lower:
        return "TPD"
    if "tokens per minute" in lower or " tpm" in lower:
        return "TPM"
    return "RATE_LIMIT"


# ── Per-model trial ───────────────────────────────────────────────────────────

def run_model_trial(
    model: str,
    query: str,
    timeout: float,
) -> dict[str, Any]:
    """
    Call one model with one query. No fallback chain — if the model fails,
    status is RATE_LIMIT or ERROR, not a silent fallback to another model.

    Returns a result dict with full diagnostics.
    """
    result: dict[str, Any] = {
        "model": model,
        "status": "UNKNOWN",
        "raw_output_truncated": None,
        "parsed_intents": [],
        "entities": [],
        "params": [],
        "student_referential_fallback": [],
        "elapsed_s": 0.0,
        "rate_limited": False,
        "rate_limit_type": None,
        "error": None,
    }

    _rl_detector.reset()

    system = build_system_prompt()
    user_msg = build_user_message(query, LastReferenced(), [])

    t0 = time.monotonic()
    try:
        client = get_llm_client()
        if not client.is_configured():
            result["status"] = "ERROR"
            result["error"] = "LLM client not configured — check LLM_PROVIDER / LLM_BASE_URL / LLM_API_KEY in .env"
            return result

        raw = client.chat(
            system=system,
            user=user_msg,
            json_mode=True,
            model=model,
            temperature=0.0,
            timeout_seconds=timeout,
        )

        result["raw_output_truncated"] = raw[:3000] if raw else None

        data = parse_json_object(raw)
        sq_list_raw = _extract_sq_list(data)
        _validate_intents(sq_list_raw, LOCKED_INTENTS)

        sqs_parsed = [_parse_raw_sq(r, query) for r in sq_list_raw if r]
        sqs_resolved = [_resolve_sq(sq, fake_resolver) for sq in sqs_parsed]

        result["parsed_intents"] = [sq.intent for sq in sqs_resolved]
        result["entities"] = [
            {
                "course_code": sq.entities.course_code,
                "role_id": sq.entities.role_id,
                "track_id": sq.entities.track_id,
                "skill_id": sq.entities.skill_id,
            }
            for sq in sqs_resolved
        ]
        result["params"] = [sq.params for sq in sqs_resolved]
        result["student_referential_fallback"] = [sq.student_referential_fallback for sq in sqs_resolved]
        result["status"] = "PASS"

    except LLMError as exc:
        msg = str(exc)
        result["error"] = msg[:600]
        if "429" in msg or "rate" in msg.lower():
            result["rate_limited"] = True
            result["rate_limit_type"] = _detect_rl_type(msg)
            result["status"] = "RATE_LIMIT"
        else:
            result["status"] = "ERROR"

    except IntentValidationError as exc:
        result["error"] = f"InvalidIntent: {exc}"
        result["status"] = "ERROR"

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["status"] = "ERROR"

    finally:
        result["elapsed_s"] = round(time.monotonic() - t0, 2)
        # Catch rate-limit signals that surfaced through log handler but not exception
        if _rl_detector.detected and not result["rate_limited"]:
            result["rate_limited"] = True
            if not result["rate_limit_type"]:
                result["rate_limit_type"] = _detect_rl_type(_rl_detector.last_message)

    return result


# ── Expected-param parsing and evaluation ─────────────────────────────────────

def _parse_expected_params(raw_list: list[str]) -> dict[str, Any]:
    """Convert ["key=value", ...] to a typed dict."""
    out: dict[str, Any] = {}
    for item in raw_list:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        k, v = k.strip(), v.strip()
        v_lower = v.lower()
        if v_lower in ("true", "1", "yes"):
            out[k] = True
        elif v_lower in ("false", "0", "no"):
            out[k] = False
        else:
            try:
                out[k] = float(v) if "." in v else int(v)
            except ValueError:
                out[k] = v
    return out


def _check_expected(
    result: dict[str, Any],
    expected_intent: str | None,
    expected_params: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Return (all_match, list_of_mismatch_strings)."""
    mismatches: list[str] = []

    if expected_intent:
        intents = result.get("parsed_intents", [])
        got = intents[0] if intents else "none"
        if got != expected_intent:
            mismatches.append(f"intent: expected={expected_intent!r}  got={got!r}")

    if expected_params and result.get("parsed_intents"):
        srfs = result.get("student_referential_fallback", [])
        params0 = result["params"][0] if result["params"] else {}
        for key, exp_val in expected_params.items():
            if key == "student_referential_fallback":
                actual = srfs[0] if srfs else False
            else:
                actual = params0.get(key)
            if actual != exp_val:
                mismatches.append(f"{key}: expected={exp_val!r}  got={actual!r}")

    return len(mismatches) == 0, mismatches


# ── Printer ───────────────────────────────────────────────────────────────────

_STATUS_ICON = {
    "PASS": "PASS",
    "FAIL": "FAIL",
    "RATE_LIMIT": "RATE",
    "ERROR": "ERR ",
    "UNKNOWN": "????",
}


def print_model_result(
    r: dict[str, Any],
    expected_intent: str | None,
    expected_params: dict[str, Any],
) -> None:
    icon = _STATUS_ICON.get(r["status"], r["status"])
    print(f"\n  [{icon}] {r['model']}  ({r['elapsed_s']}s)")

    if r["rate_limited"]:
        print(f"         429 / rate-limited  ({r['rate_limit_type']})")

    if r["error"]:
        truncated = r["error"][:250]
        print(f"         Error: {truncated}")

    if r["parsed_intents"]:
        print(f"         Intents : {r['parsed_intents']}")

    for i, (ent, par, srf) in enumerate(
        zip(r["entities"], r["params"], r["student_referential_fallback"])
    ):
        non_null = {k: v for k, v in ent.items() if v is not None}
        print(f"         SQ[{i}] entities  : {non_null if non_null else '{}'}")
        if par:
            print(f"         SQ[{i}] params    : {par}")
        print(f"         SQ[{i}] student_ref: {srf}")

    if expected_intent or expected_params:
        if r["status"] == "RATE_LIMIT":
            print("         Expected check : SKIPPED (rate-limited)")
        else:
            ok, mismatches = _check_expected(r, expected_intent, expected_params)
            if ok:
                print("         Expected check : OK")
            else:
                print("         Expected check : MISMATCH")
                for m in mismatches:
                    print(f"           - {m}")

    if r.get("raw_output_truncated"):
        preview = r["raw_output_truncated"][:300].replace("\n", " ")
        print(f"         Raw (300 chars): {preview!r}")


# ── Main ──────────────────────────────────────────────────────────────────────

DEFAULT_MODELS = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-query QU trial: test each model separately, no fallback masking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--query", required=True, help="Student query to test")
    parser.add_argument(
        "--expected-intent",
        default=None,
        metavar="INTENT",
        help="Expected first intent (e.g. compute_skill_gap)",
    )
    parser.add_argument(
        "--expected-param",
        action="append",
        default=[],
        metavar="key=value",
        help="Expected param (repeatable). E.g. --expected-param student_referential_fallback=true",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=10.0,
        help="Seconds to wait between model calls (default: 10)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("QU_TIMEOUT_SECONDS", "30")),
        help="Per-call timeout in seconds (default: QU_TIMEOUT_SECONDS env or 30)",
    )
    parser.add_argument(
        "--models",
        default=None,
        metavar="m1,m2,...",
        help=(
            "Comma-separated model list "
            "(default: llama-3.3-70b-versatile,openai/gpt-oss-120b,openai/gpt-oss-20b)"
        ),
    )
    parser.add_argument(
        "--report-path",
        default="reports/one_query_qu_trial.json",
        metavar="PATH",
        help="Output JSON report path (default: reports/one_query_qu_trial.json)",
    )
    args = parser.parse_args()

    models = (
        [m.strip() for m in args.models.split(",") if m.strip()]
        if args.models
        else DEFAULT_MODELS
    )
    expected_params = _parse_expected_params(args.expected_param)

    print("=" * 70)
    print("PathFinder One-Query QU Trial  (one model at a time, no fallback)")
    print(f"Query   : {args.query!r}")
    print(f"Expected: intent={args.expected_intent!r}  params={expected_params or '{}'}")
    print(f"Models  : {models}")
    print(f"Delay   : {args.delay}s between calls  |  Timeout: {args.timeout}s per call")
    print("=" * 70)

    model_results: list[dict[str, Any]] = []

    for i, model in enumerate(models):
        print(f"\n[{i + 1}/{len(models)}] {model}")
        result = run_model_trial(model=model, query=args.query, timeout=args.timeout)

        # Evaluate pass/fail against expectations if the call succeeded
        if result["status"] not in ("RATE_LIMIT", "ERROR"):
            ok, _ = _check_expected(result, args.expected_intent, expected_params)
            if not ok:
                result["status"] = "FAIL"

        print_model_result(result, args.expected_intent, expected_params)
        model_results.append(result)

        if i < len(models) - 1:
            print(f"\n  ... waiting {args.delay}s before next model call ...\n")
            time.sleep(args.delay)

    # Summary
    n_pass = sum(1 for r in model_results if r["status"] == "PASS")
    n_fail = sum(1 for r in model_results if r["status"] == "FAIL")
    n_rl = sum(1 for r in model_results if r["status"] == "RATE_LIMIT")
    n_err = sum(1 for r in model_results if r["status"] == "ERROR")

    print("\n" + "=" * 70)
    print(
        f"SUMMARY  {n_pass} PASS  |  {n_fail} FAIL  |  {n_rl} RATE_LIMIT  |  {n_err} ERROR"
    )
    print("=" * 70)

    # Save JSON report
    report_path = ROOT / args.report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "query": args.query,
        "expected_intent": args.expected_intent,
        "expected_params": expected_params,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "models_tested": models,
        "summary": {
            "passed": n_pass,
            "failed": n_fail,
            "rate_limited": n_rl,
            "errors": n_err,
        },
        "model_results": model_results,
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Report saved -> {report_path}")

    sys.exit(0 if n_fail == 0 and n_err == 0 else 1)


if __name__ == "__main__":
    main()
