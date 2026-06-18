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
