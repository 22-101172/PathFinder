from __future__ import annotations

from sae.llm_client import call_llm

_STUDENT_FOCUS_SYSTEM = """You are an academic advisor writing directly to a university student.
Your job is to write ONE clear, honest, and supportive paragraph — not a list.
This paragraph must:
1. Acknowledge their current situation in one sentence (honest, not harsh)
2. Identify their single most important priority right now
3. Explain the specific consequence of acting vs not acting on this priority
4. End with one concrete next step they can take this week

STRICT RULES:
- Write exactly ONE paragraph, 4-6 sentences maximum
- Never mention more than one priority — pick the most critical one only
- Only reference data explicitly provided — never invent courses, policies, or facts
- Never use bullet points, headers, or lists — flowing paragraph only
- Be direct but supportive — do not sugarcoat critical issues
- The final sentence must be a specific action, not general advice
- Do not start with "I" — start with the student's situation"""

_ADVISOR_GUIDE_SYSTEM = """You are helping an academic advisor prepare for a student meeting.
The advisor already sees all the student's data on screen.
Your job is to help with the HUMAN CONVERSATION — not the data.
Produce exactly three things:

OPENING: One sentence the advisor can use to start the conversation that acknowledges the student's situation with empathy before discussing problems. Should feel human, not clinical.

KEY_QUESTION: The single most important open-ended question to ask this specific student. This question should be designed to reveal the root cause behind their academic pattern — something the data cannot tell you. Not "why is your GPA low?" — something more specific and insightful.

SESSION_GOAL: One concrete outcome the advisor should aim to agree on before the meeting ends. Specific and actionable — something both advisor and student can commit to.

STRICT RULES:
- Only reference data explicitly provided
- Never repeat observations already visible in the dashboard
- Focus on the conversation, not the data
- Be specific to this student's situation — no generic advising templates
- Format your response exactly as:
  OPENING: [text]
  KEY_QUESTION: [text]
  SESSION_GOAL: [text]"""


def generate_student_focus_statement(
    profile: dict,
    key_points: list,
    cgpa: float,
    credits: int,
    category_performance: "dict | None" = None,
    rules: "dict | None" = None,
) -> "str | None":
    total_req = (rules or {}).get("total_credits_required", 133)
    consec_w  = profile.get("consecutive_warning", 0)

    critical_high = [p["message"] for p in key_points if p["severity"] in ("critical", "high")]
    weakest_cat   = None
    weakest_grade = None
    if category_performance:
        worst_cat  = min(category_performance, key=lambda c: category_performance[c]["pass_rate"], default=None)
        if worst_cat:
            weakest_cat   = worst_cat
            weakest_grade = category_performance[worst_cat].get("avg_letter_grade", "")

    user_prompt = f"""STUDENT DATA (use ONLY this information):
Level: {profile.get('level')} | CGPA: {cgpa} (minimum required: 2.0) | Credits: {credits}/{total_req}
Consecutive academic warnings: {consec_w}

TOP ISSUES (verified — these are facts):
{chr(10).join(f'- {m}' for m in critical_high[:2]) or 'None identified'}

SUBJECT AREA:
{f'Weakest area: {weakest_cat} (avg grade {weakest_grade})' if weakest_cat else 'Not available'}

Write ONE paragraph (4-6 sentences) addressed directly to this student.
Start with their current situation. Identify their single most important priority.
Explain the consequence of acting vs not acting. End with one concrete action this week."""

    result = call_llm(_STUDENT_FOCUS_SYSTEM, user_prompt, max_tokens=300)
    return result.strip() if result and result.strip() else None


def generate_advisor_session_guide(
    profile: dict,
    key_points: list,
    cgpa: float,
    credits: int,
    cohort_stats: dict,
    category_performance: "dict | None" = None,
) -> "dict | None":
    consec_w   = profile.get("consecutive_warning", 0)
    pct        = (cohort_stats or {}).get("student_percentile")
    cohort_str = (
        f"{pct:.0f}th percentile in cohort of {cohort_stats.get('cohort_size', '?')}"
        if pct is not None else "cohort percentile unavailable"
    )
    critical_high = [p["message"] for p in key_points if p["severity"] in ("critical", "high")]
    weakest_cat   = None
    if category_performance:
        weakest_cat = min(category_performance, key=lambda c: category_performance[c]["pass_rate"], default=None)

    user_prompt = f"""STUDENT DATA (use ONLY this information):
Name: {profile.get('name')} | Level: {profile.get('level')}
CGPA: {cgpa} | Credits: {credits}/133 | Consecutive warnings: {consec_w}
{cohort_str}

CRITICAL/HIGH ISSUES:
{chr(10).join(f'- {m}' for m in critical_high) or 'None'}

WEAKEST SUBJECT AREA: {weakest_cat or 'Not available'}

Respond in EXACTLY this format:
OPENING: [one empathetic sentence to open the conversation]
KEY_QUESTION: [one specific open-ended question to reveal root cause]
SESSION_GOAL: [one concrete outcome to agree on before the meeting ends]"""

    result = call_llm(_ADVISOR_GUIDE_SYSTEM, user_prompt, max_tokens=300)
    if not result:
        return None

    parsed: dict[str, str] = {}
    for line in result.split("\n"):
        line = line.strip()
        for key in ("OPENING", "KEY_QUESTION", "SESSION_GOAL"):
            if line.startswith(key + ":"):
                parsed[key.lower().replace("_", "_")] = line[len(key) + 1:].strip()
                break

    mapped = {
        "opening":      parsed.get("opening"),
        "key_question": parsed.get("key_question"),
        "session_goal": parsed.get("session_goal"),
    }
    return mapped if all(mapped.values()) else None
