"""
Deterministic preprocessing for QU.

Runs before/around the LLM to improve classification accuracy and support
safe fallback when all LLMs fail. Never overrules clear LLM output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Course Code ───────────────────────────────────────────────────────────────

COURSE_CODE_RE = re.compile(r'\b([A-Z]+-?[A-Z]*\d{2,4}[A-Z]?)\b', re.IGNORECASE)

# ── Policy Keywords ───────────────────────────────────────────────────────────

_POLICY_KEYWORDS: frozenset[str] = frozenset({
    "withdrawal", "withdraw", "drop course", "incomplete",
    "attendance", "absence", "absent", "missing exam",
    "warning", "academic warning", "probation", "dismissal",
    "appeal", "retake", "improve retake", "re-take",
    "grading scale", "grade points", "gpa percentage",
    "credit limit", "credit limits", "summer semester",
    "graduation requirement", "graduation requirements",
    "honors", "military training",
    "academic regulation", "academic regulations", "academic calendar",
    "policy", "policies", "handbook", "regulation", "regulations",
    "what happens if i fail", "what happens if", "what happens when",
    "disciplinary",
})

# ── Out-of-Scope Keywords ─────────────────────────────────────────────────────

_OOS_KEYWORDS: frozenset[str] = frozenset({
    "financial aid", "housing", "tuition", "admissions", "admission",
    "application deadline", "registrar", "bill", "bills", "fees", "fee",
    "scholarship", "scholarships", "dorm", "dormitory",
    "dining", "cafeteria", "transportation", "bus", "parking",
    "health insurance", "visa", "enrollment certificate",
})

# ── Override Keywords ─────────────────────────────────────────────────────────

_OVERRIDE_KEYWORDS: frozenset[str] = frozenset({
    "assume", "pretend", "suppose", "what if", "as if", "imagine",
    "hypothetically", "hypothetical", "let's say", "say i",
    "if i had", "if i had taken", "if i pass", "if i passed",
    "if i fail", "if i failed",
})

# ── Student-Referential Pattern ───────────────────────────────────────────────
# Patterns that clearly indicate the query is about the student personally.
# "me" alone is intentionally excluded — "tell me about X" is not student-referential.

_STUDENT_REF_RE = re.compile(
    r'\b('
    r'my\s+\w+'                                           # my GPA, my track, my courses
    r'|(?:can|am|do|should|will|would)\s+i\b'            # can I, am I, do I
    r'|i\s+(?:want|need|have|took|passed|failed|plan|am\s+taking)\b'  # I want, I need
    r'|(?:what|how)\s+(?:am|do|should|would|can)\s+i\b'  # what am I, how do I
    r'|what\s+suits?\s+me\b'                              # what suits me
    r'|best\s+(?:for\s+)?me\b'                           # best for me
    r'|for\s+my\s+\w+'                                   # for my profile, for my track
    r')',
    re.IGNORECASE,
)

# ── Semester Pattern ──────────────────────────────────────────────────────────

_SEMESTER_RE = re.compile(
    r'\b(fall|spring|summer)\s+(\d{4})\b',
    re.IGNORECASE,
)

# ── CGPA / Target GPA Pattern ─────────────────────────────────────────────────

_TARGET_CGPA_RE = re.compile(
    r'(?:'
    r'target(?:ed)?\s+(?:cgpa|gpa)\s+(?:of\s+)?(\d+(?:\.\d+)?)'
    r'|reach\s+(?:a\s+)?(?:cgpa|gpa)\s+(?:of\s+)?(\d+(?:\.\d+)?)'
    r'|(?:cgpa|gpa)\s+(?:of\s+)?(\d+(?:\.\d+)?)'
    r')',
    re.IGNORECASE,
)

# ── Grade / Expected Grade Pattern ───────────────────────────────────────────

_GRADE_IN_COURSE_RE = re.compile(
    r'\b(a\+?|b\+?|c\+?|d|f)\s+(?:in|on)\s+([A-Z]+-?[A-Z]*\d{2,4}[A-Z]?)',
    re.IGNORECASE,
)


# ── Result Dataclass ──────────────────────────────────────────────────────────

@dataclass
class PreprocessResult:
    course_codes: list[str] = field(default_factory=list)
    policy_signal: bool = False
    out_of_scope_signal: bool = False
    student_referential: bool = False
    semester: str | None = None
    target_cgpa: float | None = None
    override_signal: bool = False
    expected_grades: dict[str, str] = field(default_factory=dict)


# ── Public Functions ──────────────────────────────────────────────────────────

def preprocess(user_text: str) -> PreprocessResult:
    """Run all deterministic extractors on user_text."""
    return PreprocessResult(
        course_codes=extract_course_codes(user_text),
        policy_signal=detect_policy_signal(user_text),
        out_of_scope_signal=detect_out_of_scope(user_text),
        student_referential=detect_student_referential(user_text),
        semester=parse_semester(user_text),
        target_cgpa=parse_target_cgpa(user_text),
        override_signal=detect_override_signal(user_text),
        expected_grades=extract_expected_grades(user_text),
    )


def extract_course_codes(text: str) -> list[str]:
    """Return deduplicated course codes found in text, normalized to uppercase."""
    return list(dict.fromkeys(m.upper() for m in COURSE_CODE_RE.findall(text)))


def detect_policy_signal(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _POLICY_KEYWORDS)


def detect_out_of_scope(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _OOS_KEYWORDS)


def detect_student_referential(text: str) -> bool:
    return bool(_STUDENT_REF_RE.search(text))


def detect_override_signal(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _OVERRIDE_KEYWORDS)


def parse_semester(text: str) -> str | None:
    m = _SEMESTER_RE.search(text)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    return None


def parse_target_cgpa(text: str) -> float | None:
    m = _TARGET_CGPA_RE.search(text)
    if m:
        raw = m.group(1) or m.group(2) or m.group(3)
        if raw:
            try:
                val = float(raw)
                if 0.0 <= val <= 4.0:
                    return val
            except ValueError:
                pass
    return None


def extract_expected_grades(text: str) -> dict[str, str]:
    """Extract 'if I get A in C-CS301' style expected grades."""
    grades: dict[str, str] = {}
    for m in _GRADE_IN_COURSE_RE.finditer(text):
        grade = m.group(1).upper()
        course = m.group(2).upper()
        grades[course] = grade
    return grades
