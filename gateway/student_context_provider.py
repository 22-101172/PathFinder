"""
student_context_provider.py  [T02]
──────────────────────────────────
Responsibility:
  Load the student JSON record and return a normalized StudentContext object.
  Compute derived views (completed_courses, failed_courses, in_progress_courses)
  from course_history at load time.

Rules (Blueprint §5.4):
  ✓ Load student record by student_id
  ✓ Normalize into the canonical StudentContext schema
  ✓ Compute derived views from course_history
  ✗ Must NOT store session data
  ✗ Must NOT permanently modify the student record
  ✗ Must NOT perform domain reasoning or call engines

Done when:
  - get_student("S_000123") returns a valid StudentContext object
  - get_student("INVALID_ID") returns None without raising
  - derived views (completed / failed / in_progress) are computed correctly
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from gateway.models.schemas import (
    CourseRecord,
    StudentContext,
    _GRADUATION_CREDIT_HOURS,
)

logger = logging.getLogger(__name__)

# File path resolved from the script's own directory so it is correct regardless
# of where the process is launched from. Docker overrides via STUDENT_DATA_PATH.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_PATH: str = os.environ.get(
    "STUDENT_DATA_PATH",
    str(_REPO_ROOT / "data" / "student_profile.json"),
)


class StudentContextProvider:
    """
    Loads student records and returns normalized StudentContext objects.

    Storage abstraction: all file I/O lives in _load_record(). When the
    project switches to a database, only that one method changes. Every
    caller always receives the same StudentContext schema.

    Caching: each student_id is loaded once per process lifetime. The
    cache is a plain dict; no expiry is needed for a single-student demo.
    """

    def __init__(self, data_path: str = _DATA_PATH) -> None:
        self._data_path = data_path
        self._cache: dict[str, StudentContext] = {}

    # ── Public interface ──────────────────────────────────────────────────────

    def get_student(self, student_id: str) -> Optional[StudentContext]:
        """
        Return a normalized StudentContext for student_id, or None on any failure.

        Execution order:
          1. Cache check (returns immediately on hit)
          2. Load raw JSON record
          3. Verify student_id matches
          4. Parse course_history into CourseRecord objects
          5. Compute derived views (completed / failed / in_progress)
          6. Compute credit_hours_remaining and max_credit_hours_allowed
          7. Build and cache StudentContext
          8. Return result

        Never raises — returns None at any failed step.
        """
        if student_id in self._cache:
            return self._cache[student_id]

        raw = self._load_record()
        if raw is None:
            return None

        if raw.get("student_id") != student_id:
            logger.warning(
                "student_id=%s not found in record (found student_id=%s).",
                student_id,
                raw.get("student_id"),
            )
            return None

        course_history = self._parse_course_history(
            raw.get("course_history", []), student_id
        )
        if course_history is None:
            return None

        completed, failed, in_progress = self._compute_derived_views(course_history)

        earned: int = raw.get("total_credit_hours_earned", 0)
        credit_hours_remaining = _GRADUATION_CREDIT_HOURS - earned
        max_credit_hours = self._compute_max_credit_hours(raw.get("cgpa", 0.0))

        context = self._build_context(
            raw=raw,
            course_history=course_history,
            completed=completed,
            failed=failed,
            in_progress=in_progress,
            credit_hours_remaining=credit_hours_remaining,
            max_credit_hours=max_credit_hours,
            student_id=student_id,
        )
        if context is None:
            return None

        self._cache[student_id] = context
        return context

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_record(self) -> Optional[dict]:
        """
        Read the JSON file and return its contents as a dict.
        Returns None on any I/O or parse failure — never raises.

        DATABASE MIGRATION NOTE:
          This is the ONLY method that changes when switching from JSON to a
          database. Replace the file-open/json.load block with a database
          query that returns a dict with the same shape. All callers continue
          to receive the same dict without modification.
        """
        path = self._data_path
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            logger.error(
                "File not found at %s. Check the STUDENT_DATA_PATH environment variable.", path
            )
            return None
        except json.JSONDecodeError as exc:
            logger.error("File at %s is not valid JSON: %s", path, exc)
            return None
        except OSError as exc:
            logger.error("OS error reading %s: %s", path, exc)
            return None

    def _parse_course_history(
        self,
        raw_history: list,
        student_id: str,
    ) -> Optional[list[CourseRecord]]:
        """
        Build a validated list of CourseRecord objects from raw JSON entries.

        Returns None (not a partial list) if any single entry fails validation.
        Reason: partial data is worse than no data — a silently missing course
        would corrupt KGEngine skill-gap and alignment calculations downstream.
        """
        records: list[CourseRecord] = []
        for entry in raw_history:
            try:
                records.append(CourseRecord(**entry))
            except Exception as exc:
                logger.error(
                    "course_history parse error for student_id=%s, entry=%s — %s",
                    student_id,
                    entry,
                    exc,
                )
                return None
        return records

    def _compute_derived_views(
        self,
        course_history: list[CourseRecord],
    ) -> tuple[list[str], list[str], list[str]]:
        """
        Split course_history into three lists by status.

        Returns (completed, failed, in_progress) as lists of course_code strings.
        KGEngine functions (e.g. compute_skill_gap) accept List[str], not
        List[CourseRecord], so only the code is kept.
        """
        completed: list[str] = []
        failed: list[str] = []
        in_progress: list[str] = []

        for record in course_history:
            if record.status == "passed":
                completed.append(record.course_code)
            elif record.status == "failed":
                failed.append(record.course_code)
            elif record.status == "in_progress":
                in_progress.append(record.course_code)
            else:
                # Pydantic's Literal constraint catches invalid values first,
                # but log defensively in case this runs against unvalidated data.
                logger.warning(
                    "Unrecognised status=%r on course %s — skipped from derived views.",
                    record.status,
                    record.course_code,
                )

        return completed, failed, in_progress

    def _compute_max_credit_hours(self, cgpa: float) -> int:
        """
        Return the maximum credit hours allowed per semester based on CGPA.

        Source: CIS Handbook §5 (credit-hour registration limits).
        Each line is an independent, readable handbook rule — not an elif chain.
        """
        if cgpa > 3.0:
            return 21
        if cgpa >= 2.0:
            return 18
        if cgpa >= 1.0:
            return 15
        return 12

    def _build_context(
        self,
        *,
        raw: dict,
        course_history: list[CourseRecord],
        completed: list[str],
        failed: list[str],
        in_progress: list[str],
        credit_hours_remaining: int,
        max_credit_hours: int,
        student_id: str,
    ) -> Optional[StudentContext]:
        """
        Construct a StudentContext from validated components.

        Keyword-only arguments (enforced by the bare *) prevent silent
        positional-order bugs when calling with 8 parameters.

        planned_courses is always [] here. Session Manager populates it
        in build_effective_context() for the duration of a single turn.
        """
        try:
            return StudentContext(
                student_id=raw["student_id"],
                name=raw["name"],
                track_id=raw["track_id"],
                level=raw["level"],
                current_semester=raw["current_semester"],
                cgpa=raw["cgpa"],
                academic_standing=raw["academic_standing"],
                total_credit_hours_earned=raw["total_credit_hours_earned"],
                credit_hours_remaining=credit_hours_remaining,
                max_credit_hours_allowed=max_credit_hours,
                course_history=course_history,
                completed_courses=completed,
                failed_courses=failed,
                in_progress_courses=in_progress,
                planned_courses=[],
            )
        except KeyError as exc:
            logger.error(
                "Missing required field %s in record for student_id=%s.",
                exc,
                student_id,
            )
            return None
        except Exception as exc:
            logger.error(
                "Failed to build StudentContext for student_id=%s: %s",
                student_id,
                exc,
            )
            return None
