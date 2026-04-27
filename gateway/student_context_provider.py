"""
student_context_provider.py  [T02 — Person A]
──────────────────────────────────────────────
Responsibility:
  Load the student JSON record and return a normalized StudentContext object.
  Compute derived views (completed_courses, failed_courses, in_progress_courses)
  from course_history at load time.

Rules (from blueprint Section 5.4):
  ✓ Load student record by student_id
  ✓ Normalize into the canonical StudentContext schema
  ✓ Compute derived views from course_history
  ✗ Must NOT store session data
  ✗ Must NOT permanently modify the student record
  ✗ Must NOT perform domain reasoning or call engines

Done when:
  - get_student("S_000123") returns a valid StudentContext object
  - get_student("INVALID_ID") returns None without raising
  - derived views (completed/failed/in_progress) are computed correctly
"""

import json
import os
from typing import Optional

from models.schemas import StudentContext, CourseRecord

# Path to the student JSON file.
# In Docker, this is mounted from docker-compose.yml as a read-only volume.
_DATA_PATH = os.environ.get(
    "STUDENT_DATA_PATH",
    os.path.join(os.path.dirname(__file__), "data", "student_profile.json")
)


class StudentContextProvider:
    """
    Loads student records and returns normalized StudentContext objects.

    This phase uses a single JSON file. When switching to a database later,
    only this class changes — all consumers always receive the same schema.
    """

    def __init__(self, data_path: str = _DATA_PATH):
        self._data_path = data_path
        self._cache: dict[str, StudentContext] = {}

    def get_student(self, student_id: str) -> Optional[StudentContext]:
        """
        Load and return a normalized StudentContext for the given student_id.
        Returns None if the student is not found.

        The derived views (completed_courses, failed_courses, in_progress_courses)
        are computed here from course_history — not stored in the JSON.
        """
        # TODO: implement
        # Steps:
        #   1. Load the JSON file (or return from cache)
        #   2. Find the record matching student_id
        #   3. Build list[CourseRecord] from course_history
        #   4. Compute derived views from course_history
        #   5. Return StudentContext(**fields)
        raise NotImplementedError

    def _load_record(self) -> Optional[dict]:
        """Read the JSON file. Returns raw dict or None on error."""
        # TODO: implement
        raise NotImplementedError

    def _compute_derived_views(self, course_history: list[CourseRecord]) -> tuple[list[str], list[str], list[str]]:
        """
        Compute (completed_courses, failed_courses, in_progress_courses)
        from course_history.

        Returns three lists of course_codes, one per status bucket.
        """
        # TODO: implement
        # Filter course_history by status field:
        #   "passed"      → completed_courses
        #   "failed"      → failed_courses
        #   "in_progress" → in_progress_courses
        raise NotImplementedError

    def _compute_max_credit_hours(self, cgpa: float) -> int:
        """
        Return the max credit hours allowed per semester based on CGPA.
        Handbook rule (Section 5):
          cgpa > 3.0  → 21 hrs
          cgpa >= 2.0 → 18 hrs
          cgpa >= 1.0 → 15 hrs
          cgpa < 1.0  → 12 hrs
        """
        # TODO: implement
        raise NotImplementedError
