import re
from datetime import date


def get_current_semester() -> str:
    """
    Returns the current academic semester label based on today's date.
    This is a system-wide utility — not derived from any student's data.
    Called by the orchestrator when it needs a temporal anchor.

    Calendar mapping:
      September–January  → Fall   YYYY
      February–June      → Spring YYYY
      July–August        → Summer YYYY
    """
    today = date.today()
    month = today.month
    year  = today.year
    if month >= 9:
        return f"Fall {year}"
    elif month >= 7:
        return f"Summer {year}"
    else:
        return f"Spring {year}"


def get_next_semester(current_semester: str) -> str:
    """
    Returns the semester following the given one.
    Fall YYYY  → Spring YYYY+1
    Spring YYYY → Fall YYYY
    Summer YYYY → Fall YYYY
    Unknown format → returns get_current_semester() as safe fallback.
    """
    if not current_semester:
        return get_current_semester()
    parts = current_semester.strip().split()
    if len(parts) != 2:
        return get_current_semester()
    season, year_str = parts[0], parts[1]
    try:
        year = int(year_str)
    except ValueError:
        return get_current_semester()
    if season == "Fall":
        return f"Spring {year + 1}"
    if season in ("Spring", "Summer"):
        return f"Fall {year}"
    return get_current_semester()


def resolve_relative_semester_text(text: str, current_semester: str) -> "tuple[str, int] | None":
    """
    Resolve a relative semester phrase to (season, year).

    Supported patterns (case-insensitive, N in 1–6 for MVP):
      next Fall / next Spring / next Summer
      N Fall(s)/Spring(s)/Summer(s) from now
      Ordinal forms: first/second/.../sixth Fall from now
      Word forms: one/two/.../six Fall from now

    Convention: N seasons from now = (season, current_year + N).
    "next" is treated as N=1. Plural season names accepted.

    Returns None if the phrase cannot be resolved.
    """
    if not current_semester:
        return None
    parts = current_semester.strip().split()
    if len(parts) != 2:
        return None
    try:
        current_year = int(parts[1])
    except ValueError:
        return None

    text_lower = text.strip().lower()

    season_map = {
        "fall": "Fall", "falls": "Fall",
        "spring": "Spring", "springs": "Spring",
        "summer": "Summer", "summers": "Summer",
    }
    word_to_n = {
        "1": 1, "one": 1, "first": 1,
        "2": 2, "two": 2, "second": 2,
        "3": 3, "three": 3, "third": 3,
        "4": 4, "four": 4, "fourth": 4,
        "5": 5, "five": 5, "fifth": 5,
        "6": 6, "six": 6, "sixth": 6,
    }

    # "next <season>" — N=1
    m = re.fullmatch(r"next\s+(falls?|springs?|summers?)", text_lower)
    if m:
        return (season_map[m.group(1)], current_year + 1)

    # "<N> <season>(s) from now"
    m = re.fullmatch(r"(\w+)\s+(falls?|springs?|summers?)\s+from\s+now", text_lower)
    if m:
        n = word_to_n.get(m.group(1))
        if n is None:
            return None
        return (season_map[m.group(2)], current_year + n)

    return None
