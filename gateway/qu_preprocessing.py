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
STRICT_COURSE_CODE_RE = re.compile(r'\b(C-[A-Z]+\d{2,4}[A-Z]?)\b', re.IGNORECASE)

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

# ── Reset Keywords ────────────────────────────────────────────────────────────

_RESET_KEYWORDS: frozenset[str] = frozenset({
    "reset assumptions", "clear assumptions", "cancel what-if",
    "back to official record", "remove what we assumed",
    "forget the what-if scenario",
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
    reset_signal: bool = False
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
        reset_signal=detect_reset_signal(user_text),
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


def detect_reset_signal(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _RESET_KEYWORDS)


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


# ── D2 Course Candidate Extraction ────────────────────────────────────────────

# Verb patterns that strongly suggest "tell me about <course>" intent
_D2_COURSE_INFO_VERBS_RE = re.compile(
    r'(?:tell\s+me\s+about|talk\s+(?:to\s+me\s+)?about|'
    r'what\s+(?:about|is)\s+|give\s+me\s+info(?:rmation)?\s+(?:about|on)|'
    r'inform\s+me\s+(?:about|on)|explain\s+|describe\s+|'
    r'is\s+\w+\s+a\s+course|'
    r'how\s+many\s+credits\s+is\s+|what\s+level\s+is\s+|'
    r'course\s+description\s+for\s+|'
    r'what\s+will\s+i\s+learn\s+in\s+)',
    re.IGNORECASE,
)

# Verb patterns for prerequisites
_D2_PREREQ_VERBS_RE = re.compile(
    r'(?:prerequisites?\s+(?:of|for)|prereqs?\s+(?:of|for)|'
    r'what\s+do\s+i\s+need\s+(?:before|for)|before\s+taking|'
    r'(?:complete\s+)?prerequisite\s+chain\s+(?:of|for)|'
    r'full\s+prereqs?\s+(?:of|for)|'
    r'what\s+(?:courses?\s+)?do\s+i\s+need\s+(?:to\s+take\s+)?before)',
    re.IGNORECASE,
)

# Verb patterns for skills taught
_D2_SKILLS_TAUGHT_VERBS_RE = re.compile(
    r'(?:skills?\s+(?:does|do|taught\s+by|in)|'
    r'what\s+will\s+i\s+learn\s+in|'
    r'what\s+do\s+i\s+learn\s+(?:in|from)|'
    r'what\s+skills?\s+(?:will|does|can)\s+(?:i\s+)?(?:learn|get|gain)\s+(?:in|from)|'
    r'what\s+(?:is|are)\s+taught\s+(?:in|by))',
    re.IGNORECASE,
)

# Verb patterns for skill search (which courses teach X)
_D2_SKILL_SEARCH_VERBS_RE = re.compile(
    r'(?:which\s+courses?\s+teach|what\s+courses?\s+teach|'
    r'courses?\s+that\s+teach|courses?\s+for\s+|'
    r'where\s+can\s+i\s+learn|what\s+teaches|'
    r'what\s+courses?\s+cover|subjects?\s+that?\s+teach|'
    r'what\s+courses?\s+make\s+me\s+better\s+at)',
    re.IGNORECASE,
)

# Extract the course/subject name after a D2 info verb
_D2_COURSE_AFTER_VERB_RE = re.compile(
    r'(?:tell\s+me\s+about|talk\s+(?:to\s+me\s+)?about|'
    r'what\s+about|give\s+me\s+info(?:rmation)?\s+(?:about|on)|'
    r'inform\s+me\s+(?:about|on)|explain\s+|describe\s+|'
    r'what\s+is\s+|'
    r'how\s+many\s+credits\s+is\s+|what\s+level\s+is\s+|'
    r'course\s+description\s+for\s+|'
    r'what\s+will\s+i\s+learn\s+in\s+|'
    r'is\s+(.+?)\s+a\s+course)'
    r'\s*([^?!,\n]{2,60})',
    re.IGNORECASE,
)

# Extract skill candidate from "courses for X / where can I learn X / which courses teach X"
_D2_SKILL_AFTER_VERB_RE = re.compile(
    r'(?:which\s+courses?\s+teach|what\s+courses?\s+teach|'
    r'courses?\s+(?:that\s+teach|for)|'
    r'where\s+can\s+i\s+learn|what\s+teaches|'
    r'what\s+courses?\s+cover|subjects?\s+(?:that\s+)?teach|'
    r'what\s+courses?\s+make\s+me\s+better\s+at)'
    r'\s+([^?!,\n]{2,60})',
    re.IGNORECASE,
)

# Extract course name from "prerequisites of X / prereq of X / complete prereq chain of X"
_D2_PREREQ_AFTER_VERB_RE = re.compile(
    r'(?:prerequisites?\s+(?:of|for)|prereqs?\s+(?:of|for)|'
    r'(?:complete\s+)?prerequisite\s+chain\s+(?:of|for)|'
    r'full\s+prereqs?\s+(?:of|for)|'
    r'what\s+do\s+i\s+need\s+before\s+taking\s+|before\s+taking\s+)'
    r'\s*([^?!,\n]{2,60})',
    re.IGNORECASE,
)

# Extract course name from "skills does X teach / skills taught by X"
_D2_SKILLS_COURSE_AFTER_VERB_RE = re.compile(
    r'(?:skills?\s+(?:does|do)\s+|skills?\s+(?:taught|given)\s+by\s+|'
    r'what\s+(?:will\s+i\s+)?learn\s+(?:in|from)\s+|'
    r'what\s+(?:is|are)\s+taught\s+(?:in|by)\s+)'
    r'([^?!,\n]{2,60})',
    re.IGNORECASE,
)


# ── Prerequisite Depth Detection ──────────────────────────────────────────────

# Matches "full prerequisites", "all prereqs", "complete prerequisite chain", etc.
_PREREQ_FULL_DEPTH_RE = re.compile(
    r'\b(?:full|all|complete|entire|whole|recursive)\s+'
    r'(?:prerequisites?|prereqs?|prerequisite\s+(?:chain|tree)|prereq\s+(?:chain|tree))\b'
    r'|'
    r'\b(?:full|complete|entire|whole)\s+(?:chain|tree)\s+(?:of|for)\b'
    r'|'
    r'\bfull\s+prereq(?:s|uisite)?\b',
    re.IGNORECASE,
)

# Matches clear prerequisite request patterns (but NOT generic mentions of the word)
_PREREQ_INTENT_RE = re.compile(
    r'\b(?:'
    r'prerequisites?\s+(?:of|for|to)'
    r'|prereqs?\s+(?:of|for|to)'
    r'|what\s+do\s+i\s+need\s+(?:before|for)'
    r'|before\s+taking\b'
    r'|prerequisite\s+(?:chain|tree)\s+(?:of|for)'
    r'|prereq\s+(?:chain|tree)\s+(?:of|for)'
    r')\b',
    re.IGNORECASE,
)


def detect_prereq_full_depth(text: str) -> bool:
    """Return True if query clearly requests full/all/complete prerequisite tree.

    High-confidence signal: safe to patch depth="full" when LLM also outputs
    get_course_prerequisites. Does NOT force the prerequisite intent itself.
    """
    return bool(_PREREQ_FULL_DEPTH_RE.search(text))


def detect_prereq_intent(text: str) -> bool:
    """Return True if query is a clear prerequisite request (not just mentioning the word).

    Use as a supporting signal; never override LLM intent based on this alone.
    """
    return bool(_PREREQ_INTENT_RE.search(text))


def detect_d2_course_info_verb(text: str) -> bool:
    """Return True if text contains a course-info request verb pattern."""
    return bool(_D2_COURSE_INFO_VERBS_RE.search(text))


def detect_d2_prereq_verb(text: str) -> bool:
    """Return True if text contains a prerequisite-request verb pattern."""
    return bool(_D2_PREREQ_VERBS_RE.search(text))


def detect_d2_skills_taught_verb(text: str) -> bool:
    """Return True if text contains a skills-taught verb pattern."""
    return bool(_D2_SKILLS_TAUGHT_VERBS_RE.search(text))


def detect_d2_skill_search_verb(text: str) -> bool:
    """Return True if text contains a skill-search (which-courses-teach) verb pattern."""
    return bool(_D2_SKILL_SEARCH_VERBS_RE.search(text))


def extract_d2_course_candidate(text: str) -> str | None:
    """
    Extract a course name/mention candidate from D2 course-info patterns.
    Returns the candidate string or None.
    """
    # Try skills-taught pattern first ("what does X teach", "skills taught by X")
    m = _D2_SKILLS_COURSE_AFTER_VERB_RE.search(text)
    if m:
        candidate = m.group(1).strip().rstrip(".,?!")
        if candidate and len(candidate) > 1:
            return candidate

    # Try prereq pattern ("prereq of X", "before X")
    m = _D2_PREREQ_AFTER_VERB_RE.search(text)
    if m:
        candidate = m.group(1).strip().rstrip(".,?!")
        if candidate and len(candidate) > 1:
            return candidate

    # Try general course-info verb pattern ("tell me about X")
    m = _D2_COURSE_AFTER_VERB_RE.search(text)
    if m:
        # group 2 is the extracted name (group 1 is the "is X a course" special case)
        candidate = (m.group(2) or m.group(1) or "").strip().rstrip(".,?!")
        if candidate and len(candidate) > 1:
            return candidate

    return None


def extract_d2_skill_candidate(text: str) -> str | None:
    """
    Extract a skill name/mention candidate from D2 skill-search patterns.
    Returns the candidate string or None.
    """
    m = _D2_SKILL_AFTER_VERB_RE.search(text)
    if m:
        candidate = m.group(1).strip().rstrip(".,?!")
        if candidate and len(candidate) > 1:
            return candidate
    return None


# ── D6 Record Focus Detection ─────────────────────────────────────────────────

_D6_ASSUMPTION_ONLY_RE = re.compile(
    r'^(?:assume|pretend|suppose|let\'s say|say i|imagine|hypothetically)[,\s]+'
    r'(?:i|that i)\s+(?:failed|passed|took|completed)\b',
    re.IGNORECASE,
)

_D6_FOLLOWUP_SIGNALS: frozenset[str] = frozenset({
    "what should", "what will", "what happens", "what now", "plan",
    "schedule", "roadmap", "graduate", "graduation", "recommend",
    "suggest", "affect my", "impact my", "can i take", "register",
})

_D6_COURSE_STATUS_KEYWORDS: frozenset[str] = frozenset({
    "did i take", "did i pass", "did i fail", "did i complete",
    "am i taking", "have i taken", "have i passed", "have i completed",
    "is it in my courses", "just yes or no", "yes or no:",
    "am i enrolled in",
})

_D6_LAST_SEM_GPA_KEYWORDS: frozenset[str] = frozenset({
    "last semester gpa", "gpa last semester", "previous semester gpa",
    "last term gpa", "last semester grade", "last semester result",
    "gpa this past semester",
})

_D6_CGPA_KEYWORDS: frozenset[str] = frozenset({
    "my cgpa", "my gpa", "what is my cgpa", "what is my gpa",
    "what's my cgpa", "what's my gpa",
    "tell me my gpa", "tell me my cgpa", "only cgpa", "only gpa",
    "cgpa only", "gpa only",
})

_D6_LEVEL_KEYWORDS: frozenset[str] = frozenset({
    "my level", "academic level", "what is my level", "what's my level",
    "what level am i", "which level am i", "what year am i",
    "am i freshman", "am i sophomore", "am i junior", "am i senior",
})

_D6_STANDING_KEYWORDS: frozenset[str] = frozenset({
    "academic standing", "academic status", "good standing",
    "academic danger", "at risk academically", "in trouble academically",
    "am i in danger", "am i failing academically", "am i cooked",
    "cooked academically",
})

_D6_PROBATION_KEYWORDS: frozenset[str] = frozenset({
    "probation", "on probation", "academic probation",
})

_D6_WARNINGS_KEYWORDS: frozenset[str] = frozenset({
    "academic warning", "number of warnings", "how many warnings",
    "warning count", "consecutive warnings", "total warnings", "any warnings",
    "have i been warned",
})

_D6_COMPLETED_KEYWORDS: frozenset[str] = frozenset({
    "completed courses", "finished courses", "passed courses",
    "courses i completed", "courses i passed", "courses i finished",
    "courses i took", "courses i have taken", "what did i complete",
    "what did i pass", "what did i finish", "what courses have i done",
    "what have i completed", "what have i passed", "courses completed",
    "done so far", "completed so far", "courses so far",
})

_D6_IN_PROGRESS_KEYWORDS: frozenset[str] = frozenset({
    "current courses", "in progress courses", "in-progress courses",
    "courses i am taking", "courses i'm taking", "currently taking",
    "currently enrolled", "what am i taking", "what courses am i taking",
    "enrolled courses", "taking now", "courses now", "taking this semester",
    "enrolled this semester",
})

_D6_FAILED_KEYWORDS: frozenset[str] = frozenset({
    "failed courses", "courses i failed", "what did i fail",
    "what have i failed", "do i have fails", "any fails",
    "have i failed", "my fails",
})

_D6_FAILED_HISTORY_KEYWORDS: frozenset[str] = frozenset({
    "all courses i failed", "courses i ever failed", "failed before",
    "even if i retook", "even if retook", "failed history",
    "historically failed", "ever failed", "failed at some point",
    "failed at least once", "courses that i failed even",
})

_D6_CREDITS_KEYWORDS: frozenset[str] = frozenset({
    "credit hours", "credits earned", "credits completed",
    "how many credits", "total credits", "completed credits",
    "credit hours earned", "credits i have",
})

_D6_TRACK_KEYWORDS: frozenset[str] = frozenset({
    "my track", "what track", "which track", "my program",
    "what program", "which program", "my track id",
})

_D6_CURRENT_SEM_KEYWORDS: frozenset[str] = frozenset({
    "current semester", "what semester am i in", "which semester am i in",
    "what semester is it", "my current semester", "current term",
})

_D6_STUDY_STATUS_KEYWORDS: frozenset[str] = frozenset({
    "study status", "am i still studying", "am i graduated",
    "am i on leave",
})

# ── D6 Response Style Detection ───────────────────────────────────────────────

_D6_YES_NO_RE = re.compile(
    r'\b(?:yes or no|yes/no|just a yes or no|answer (?:yes or no|with yes|with no))\b',
    re.IGNORECASE,
)

_D6_ONE_SENTENCE_RE = re.compile(
    r'\b(?:one sentence|in one sentence|single sentence)\b',
    re.IGNORECASE,
)

_D6_ONLY_RE = re.compile(
    r'^(?:only|just)\b|'
    r'\b(?:only|just)\s+(?:tell|show|give|the|my)\b|'
    r'\btell\s+me\s+only\b|'
    r'\bno summary\b|\bnothing else\b',
    re.IGNORECASE,
)

_D6_CONCISE_RE = re.compile(
    r'\b(?:briefly|quick(?:ly)?|concise(?:ly)?|short answer|in short|in brief)\b',
    re.IGNORECASE,
)


def detect_d6_record_focus(text: str) -> str | None:
    """Return a record_focus string for D6 student-record queries, or None if undetermined."""
    lower = text.lower()
    # Assumption-only check (standalone "Assume I failed X" with no follow-up intent)
    if _D6_ASSUMPTION_ONLY_RE.match(text.strip()):
        if not any(kw in lower for kw in _D6_FOLLOWUP_SIGNALS):
            return "assumption_acknowledgement"
    if any(kw in lower for kw in _D6_FAILED_HISTORY_KEYWORDS):
        return "failed_course_history"
    if any(kw in lower for kw in _D6_LAST_SEM_GPA_KEYWORDS):
        return "last_semester_gpa"
    if any(kw in lower for kw in _D6_CGPA_KEYWORDS):
        return "cgpa"
    if any(kw in lower for kw in _D6_LEVEL_KEYWORDS):
        return "academic_level"
    if any(kw in lower for kw in _D6_STANDING_KEYWORDS):
        return "academic_standing"
    if any(kw in lower for kw in _D6_PROBATION_KEYWORDS):
        return "probation_status"
    if any(kw in lower for kw in _D6_WARNINGS_KEYWORDS):
        return "academic_warnings"
    if any(kw in lower for kw in _D6_COMPLETED_KEYWORDS):
        return "completed_courses"
    if any(kw in lower for kw in _D6_IN_PROGRESS_KEYWORDS):
        return "in_progress_courses"
    if any(kw in lower for kw in _D6_FAILED_KEYWORDS):
        return "failed_courses"
    if any(kw in lower for kw in _D6_CREDITS_KEYWORDS):
        return "completed_credits"
    if any(kw in lower for kw in _D6_TRACK_KEYWORDS):
        return "track"
    if any(kw in lower for kw in _D6_CURRENT_SEM_KEYWORDS):
        return "current_semester"
    if any(kw in lower for kw in _D6_STUDY_STATUS_KEYWORDS):
        return "study_status"
    if any(kw in lower for kw in _D6_COURSE_STATUS_KEYWORDS):
        return "course_status_check"
    return None


def detect_d6_response_style(text: str) -> str:
    """Return response_style for a D6 query."""
    if _D6_YES_NO_RE.search(text):
        return "yes_no"
    if _D6_ONE_SENTENCE_RE.search(text):
        return "one_sentence"
    if _D6_ONLY_RE.search(text):
        return "only"
    if _D6_CONCISE_RE.search(text):
        return "concise"
    return "normal"


# ── Turn Memory Follow-up Helpers ─────────────────────────────────────────────

_ORDINAL_RE = re.compile(
    r'\b(?:the\s+)?(?P<ordinal>first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\s+one\b',
    re.IGNORECASE,
)

_ORDINAL_MAP: dict[str, int] = {
    "first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
    "1st": 0, "2nd": 1, "3rd": 2, "4th": 3, "5th": 4,
}

_SUBSET_REF_RE = re.compile(r'\b(?:these|those|them|they)\b', re.IGNORECASE)

_SUBSET_STATUS_WORDS: dict[str, str] = {
    "complete": "completed", "completed": "completed",
    "pass": "completed", "passed": "completed",
    "fail": "failed", "failed": "failed",
    "taking": "in_progress", "enrolled": "in_progress",
    "in_progress": "in_progress", "current": "in_progress", "currently": "in_progress",
}

# list-type focuses that should never use one_sentence style
_D6_LIST_FOCUSES: frozenset[str] = frozenset({
    "in_progress_courses", "completed_courses", "failed_courses",
    "failed_course_history", "full_record", "progress_summary",
})

# phrases that imply a list answer (incompatible with one_sentence)
_D6_LIST_PHRASE_RE = re.compile(
    r'\b(?:show\s+me\s+all|list\s+all|all\s+(?:the\s+)?courses?|what\s+courses?)\b',
    re.IGNORECASE,
)

# yes/no patterns for student-status questions
_D6_STATUS_YES_NO_RE = re.compile(
    r'\b(?:did\s+i\s+(?:pass|fail|take|complete|finish)|'
    r'have\s+i\s+(?:taken|passed|failed|completed)|'
    r'am\s+i\s+(?:taking|enrolled(?:\s+in)?)|'
    r'is\s+(?:it|this|that)\s+(?:in\s+my|on\s+my))\b',
    re.IGNORECASE,
)


def detect_ordinal_reference(text: str) -> tuple[str, int] | None:
    """Return (ordinal_word, 0-based index) if an ordinal reference is found, else None."""
    m = _ORDINAL_RE.search(text)
    if m:
        word = m.group("ordinal").lower()
        idx = _ORDINAL_MAP.get(word)
        if idx is not None:
            return word, idx
    return None


def detect_subset_reference(text: str) -> bool:
    """Return True if text contains a pronoun reference to a previous list."""
    return bool(_SUBSET_REF_RE.search(text))


def detect_subset_status_filter(text: str) -> str | None:
    """Return a status_filter string for subset course queries, or None."""
    lower = text.lower()
    for keyword, status in _SUBSET_STATUS_WORDS.items():
        if keyword in lower:
            return status
    return None


def is_list_incompatible_style(text: str, record_focus: str | None) -> bool:
    """Return True when one_sentence style is inappropriate for this query."""
    if record_focus in _D6_LIST_FOCUSES:
        return True
    if _D6_LIST_PHRASE_RE.search(text):
        return True
    return False


def detect_status_yes_no(text: str) -> bool:
    """Return True for 'did I pass/fail/take/am I taking' style status checks."""
    return bool(_D6_STATUS_YES_NO_RE.search(text))
