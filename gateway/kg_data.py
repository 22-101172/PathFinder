"""
kg_data.py  [Integration sprint — entity alias loader]
─────────────────────────────────────────────────────
Loads the small reference CSVs published by the KG team and exposes them as
lookup tables for the Query Understanding Layer.

Why this exists:
  The QU layer must convert free text like "Data Scientist" into the exact
  schema ID "RL_Data_Scientist" that the KG operations expect. Hard-coding
  every alias by hand would rot — the KG team's CSVs are the source of truth,
  so we read them once at start-up.

Source of truth:
  The folder pointed to by the KG_DATA_DIR environment variable. In this
  workspace the authoritative folder is "PathFinder KG-Engine/data/" (a
  sibling of the integration repo). Falls back to "<repo>/data/kg/" if that
  exists, then to a tiny in-memory starter set so the gateway still boots
  during demos where the CSV folder is missing.

CSV files expected (only the columns we use are listed):
  - courses.csv  : course_code, name
  - roles.csv    : role_id, name
  - tracks.csv   : track_id, name
  - skills.csv   : skill_id, name

NOT in scope for this module:
  - Calling Neo4j or any KG operation
  - Holding student data
  - Maintaining live state past process start (callers should treat the
    loaded data as immutable for the process lifetime)
"""

from __future__ import annotations

import csv
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Module-level constants ───────────────────────────────────────────────────

# Resolved repo root: <pathfinder>/gateway/kg_data.py → <pathfinder>
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Fallback locations to check when KG_DATA_DIR is not set. Order matters —
# the first existing directory wins.
_FALLBACK_DIRS: tuple[Path, ...] = (
    _REPO_ROOT / "data" / "kg",
    _REPO_ROOT.parent / "PathFinder KG-Engine" / "data",
)

# Whitespace and punctuation we strip when building name → id keys.
# Keeps "Data Scientist", "data-scientist", "DATA  SCIENTIST" all matching.
_NORMALIZE_RE = re.compile(r"[\s\-_/]+")


def _normalize_name(value: str) -> str:
    """Lowercase + collapse separators. Used for case-insensitive name lookup."""
    if not value:
        return ""
    return _NORMALIZE_RE.sub(" ", value.strip().lower()).strip()


# ── Public data container ────────────────────────────────────────────────────

@dataclass
class KGReferenceData:
    """
    Read-only-by-convention lookup tables for the Query Understanding layer.

    The dataclass is mutable for ease of construction, but callers must not
    mutate the maps after load. Tests can build fresh instances cheaply.
    """

    # course_code → human course name (preserves the canonical code casing)
    courses: dict[str, str] = field(default_factory=dict)
    # normalized course name → canonical course_code
    course_name_to_code: dict[str, str] = field(default_factory=dict)

    # role_id → role name
    roles: dict[str, str] = field(default_factory=dict)
    # normalized role name → canonical role_id (e.g. "data scientist" → "RL_Data_Scientist")
    role_name_to_id: dict[str, str] = field(default_factory=dict)

    # track_id → track name
    tracks: dict[str, str] = field(default_factory=dict)
    # normalized track name → canonical track_id (e.g. "artificial intelligence" → "AI")
    track_name_to_id: dict[str, str] = field(default_factory=dict)

    # skill_id → skill name
    skills: dict[str, str] = field(default_factory=dict)
    # normalized skill name → canonical skill_id
    skill_name_to_id: dict[str, str] = field(default_factory=dict)

    # Hand-curated short aliases. Only added when we are sure they are unambiguous.
    # Example: "ml" → "SK_ML". "ai" is NOT here because it collides with the AI track.
    skill_aliases: dict[str, str] = field(default_factory=dict)
    role_aliases: dict[str, str] = field(default_factory=dict)

    # Where we loaded from. None means the starter fallback was used.
    source_dir: Optional[Path] = None

    @property
    def loaded_from_disk(self) -> bool:
        return self.source_dir is not None

    # ── Convenience accessors used by Query Understanding ────────────────────

    def resolve_course_code(self, code: str) -> Optional[str]:
        """Return the canonical course_code if known, else None.

        Accepts the literal code as it appears in the CSV. We do NOT silently
        strip a leading 'C-' here — the QU layer handles that since it knows
        the original surface form."""
        if not code:
            return None
        return code if code in self.courses else None

    def resolve_course_name(self, text: str) -> Optional[str]:
        """Return a course_code if the given text contains a known course name.

        Performs a longest-match scan over the normalized name table so that
        'Introduction to Machine Learning' wins over 'Machine Learning' if both
        are in the table."""
        return _best_substring_match(text, self.course_name_to_code)

    def resolve_role(self, text: str) -> Optional[str]:
        """Return a role_id from a free-text role phrase, or None.

        Tries the hand-curated alias table first (single tokens), then falls
        back to a longest-substring match over the canonical role names."""
        norm = _normalize_name(text)
        if norm in self.role_aliases:
            return self.role_aliases[norm]
        return _best_substring_match(text, self.role_name_to_id)

    def resolve_track(self, text: str) -> Optional[str]:
        """Return a track_id from a free-text track phrase or the bare code."""
        if not text:
            return None
        bare = text.strip()
        if bare in self.tracks:
            return bare
        return _best_substring_match(text, self.track_name_to_id)

    def resolve_skill(self, text: str) -> Optional[str]:
        """Return a skill_id from a free-text skill phrase, or None."""
        norm = _normalize_name(text)
        if norm in self.skill_aliases:
            return self.skill_aliases[norm]
        return _best_substring_match(text, self.skill_name_to_id)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _best_substring_match(text: str, name_to_id: dict[str, str]) -> Optional[str]:
    """
    Return the id whose normalized name is the longest substring of `text`.

    The longest-match rule prevents "Data Engineer" from being shadowed by a
    shorter alias such as "Data" when both are present.
    """
    if not text or not name_to_id:
        return None
    norm_text = _normalize_name(text)
    if not norm_text:
        return None

    best_name = ""
    best_id: Optional[str] = None
    for name, ident in name_to_id.items():
        if not name:
            continue
        if name in norm_text and len(name) > len(best_name):
            best_name = name
            best_id = ident
    return best_id


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV file into a list of dicts. Empty list on any I/O failure."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except FileNotFoundError:
        logger.warning("kg_data: CSV not found at %s", path)
        return []
    except OSError as exc:
        logger.warning("kg_data: failed to read %s: %s", path, exc)
        return []


def _load_courses(data: KGReferenceData, path: Path) -> int:
    rows = _read_csv(path)
    for row in rows:
        code = (row.get("course_code") or "").strip()
        name = (row.get("name") or "").strip()
        if not code:
            continue
        data.courses[code] = name
        if name:
            data.course_name_to_code[_normalize_name(name)] = code
    return len(rows)


def _load_roles(data: KGReferenceData, path: Path) -> int:
    rows = _read_csv(path)
    for row in rows:
        rid = (row.get("role_id") or "").strip()
        name = (row.get("name") or "").strip()
        if not rid:
            continue
        data.roles[rid] = name
        if name:
            data.role_name_to_id[_normalize_name(name)] = rid
    return len(rows)


def _load_tracks(data: KGReferenceData, path: Path) -> int:
    rows = _read_csv(path)
    for row in rows:
        tid = (row.get("track_id") or "").strip()
        name = (row.get("name") or "").strip()
        if not tid:
            continue
        data.tracks[tid] = name
        if name:
            data.track_name_to_id[_normalize_name(name)] = tid
    return len(rows)


def _load_skills(data: KGReferenceData, path: Path) -> int:
    rows = _read_csv(path)
    for row in rows:
        sid = (row.get("skill_id") or "").strip()
        name = (row.get("name") or "").strip()
        if not sid:
            continue
        data.skills[sid] = name
        if name:
            data.skill_name_to_id[_normalize_name(name)] = sid
    return len(rows)


def _seed_unambiguous_aliases(data: KGReferenceData) -> None:
    """Add hand-curated short aliases that are safe across the loaded data.

    We only add an alias when:
      - the alias is not already a canonical name
      - the target id exists in the loaded data
      - the alias is unambiguous within its category
    """
    # Skills — common informal short forms.
    safe_skill_aliases = {
        "ml": "SK_ML",
        "dl": "SK_DL",
        "nlp": "SK_NLP",
        "cv": "SK_Computer_Vision",
        "rl": "SK_Reinforcement_Learning",
        "stats": "SK_Statistics",
        "linear algebra": "SK_Linear_Algebra",
        "data viz": "SK_Data_Visualization",
        "big data": "SK_Big_Data",
        "web dev": "SK_Web_Dev",
    }
    for alias, sid in safe_skill_aliases.items():
        if sid in data.skills:
            data.skill_aliases[_normalize_name(alias)] = sid

    # Roles — none added by default. "AI" is ambiguous with the AI track; the
    # caller should prefer full role phrases like "AI Engineer". Anything we
    # add here must be unambiguous.


def _starter_dataset() -> KGReferenceData:
    """Tiny in-memory dataset used when no CSV directory is found.

    Just enough to keep `/query` answering during smoke tests and demos.
    Production deployments must point KG_DATA_DIR at the real CSVs.
    """
    data = KGReferenceData()
    data.courses = {
        "C-AI311": "Introduction to Artificial Intelligence",
        "C-AI321": "Introduction to Machine Learning",
        "C-CS111": "Introduction to Computing and Programming",
        "C-CS213": "Data Structures",
    }
    data.course_name_to_code = {
        _normalize_name(name): code for code, name in data.courses.items()
    }
    data.roles = {
        "RL_Data_Scientist": "Data Scientist",
        "RL_Data_Engineer": "Data Engineer",
        "RL_ML_Engineer": "Machine Learning Engineer",
        "RL_Software_Engineer": "Software Engineer",
        "RL_AI_Engineer": "AI Engineer",
    }
    data.role_name_to_id = {
        _normalize_name(name): rid for rid, name in data.roles.items()
    }
    data.tracks = {
        "AI": "Artificial Intelligence",
        "DSE": "Data Science and Engineering",
        "SWE": "Software Engineering",
        "CYS": "Cybersecurity",
    }
    data.track_name_to_id = {
        _normalize_name(name): tid for tid, name in data.tracks.items()
    }
    data.skills = {
        "SK_ML": "Machine Learning",
        "SK_NLP": "Natural Language Processing",
        "SK_Python": "Python",
        "SK_SQL": "SQL",
    }
    data.skill_name_to_id = {
        _normalize_name(name): sid for sid, name in data.skills.items()
    }
    _seed_unambiguous_aliases(data)
    return data


def _resolve_data_dir(explicit_dir: Optional[str]) -> Optional[Path]:
    """Pick a directory based on explicit arg → env var → fallback paths."""
    candidates: list[Path] = []
    if explicit_dir:
        candidates.append(Path(explicit_dir).expanduser())
    env_dir = os.environ.get("KG_DATA_DIR")
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    candidates.extend(_FALLBACK_DIRS)

    for candidate in candidates:
        if candidate and candidate.is_dir():
            return candidate
    return None


# ── Public loader ────────────────────────────────────────────────────────────

# Cached at module level so the CSV is parsed once per process. Tests bypass
# this by instantiating `KGReferenceData` themselves or by calling
# `load_kg_reference_data(reload=True)`.
_cached: Optional[KGReferenceData] = None


def load_kg_reference_data(
    data_dir: Optional[str] = None,
    reload: bool = False,
) -> KGReferenceData:
    """
    Return the cached `KGReferenceData`, loading it on first call.

    Resolution order:
      1. The `data_dir` argument, if provided.
      2. The `KG_DATA_DIR` environment variable, if set.
      3. `<repo>/data/kg/` (so a deployment can ship CSVs in-tree).
      4. `<repo>/../PathFinder KG-Engine/data/` (this workspace's authoritative folder).
      5. A built-in starter dataset (logged as a warning so it does not pass
         silently in production).
    """
    global _cached
    if _cached is not None and not reload:
        return _cached

    resolved = _resolve_data_dir(data_dir)
    if resolved is None:
        logger.warning(
            "kg_data: KG_DATA_DIR not set and no CSV directory found; "
            "using built-in starter aliases. Entity resolution will be limited."
        )
        _cached = _starter_dataset()
        return _cached

    data = KGReferenceData()
    data.source_dir = resolved
    courses_n = _load_courses(data, resolved / "courses.csv")
    roles_n = _load_roles(data, resolved / "roles.csv")
    tracks_n = _load_tracks(data, resolved / "tracks.csv")
    skills_n = _load_skills(data, resolved / "skills.csv")

    if not (data.courses or data.roles or data.tracks or data.skills):
        logger.warning(
            "kg_data: directory %s exists but produced no rows; falling back to starter dataset.",
            resolved,
        )
        _cached = _starter_dataset()
        return _cached

    _seed_unambiguous_aliases(data)
    logger.info(
        "kg_data: loaded reference data from %s "
        "(courses=%d roles=%d tracks=%d skills=%d).",
        resolved, courses_n, roles_n, tracks_n, skills_n,
    )
    _cached = data
    return data
