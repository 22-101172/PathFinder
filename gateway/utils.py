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
