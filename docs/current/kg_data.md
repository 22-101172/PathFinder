# `gateway/kg_data.py`

## 1. Purpose
Loads the KG team's reference CSVs (`courses.csv`, `roles.csv`, `tracks.csv`,
`skills.csv`) and exposes them as in-memory alias tables so the Query
Understanding Layer can turn free text like "Data Scientist" into the
canonical schema id `RL_Data_Scientist`.

Treat this module as the source of truth for human-name → KG-id mappings.
Hard-coding aliases anywhere else is a maintenance hazard.

## 2. What's Inside
- `KGReferenceData` dataclass: per-entity dicts plus helpers
  (`resolve_course_code`, `resolve_course_name`, `resolve_role`,
  `resolve_track`, `resolve_skill`).
- `load_kg_reference_data(data_dir=None, reload=False)` — module-level
  cached loader.
- Resolution order for the data directory:
  1. The `data_dir` argument.
  2. The `KG_DATA_DIR` environment variable.
  3. `<repo>/data/kg/`.
  4. `<repo>/../PathFinder KG-Engine/data/` (this workspace's authoritative
     folder).
  5. A built-in starter dataset (a one-time warning is logged so the
     fallback does not pass silently in production).
- `_seed_unambiguous_aliases(...)` — adds the handful of safe short aliases
  (e.g. `"ml" → "SK_ML"`). New aliases must be unambiguous within their
  category before being added.

## 3. Inputs / Outputs
- Inputs: CSV files at the resolved data directory.
- Outputs: a populated `KGReferenceData` instance (or the starter dataset).
- Side effects: caches the loaded data at module level. Tests can pass
  their own instance to bypass the cache.

## 4. Who Calls It
- `gateway/main.py` at import time.
- `gateway.query_understanding.QueryUnderstandingLayer` for entity
  resolution.

## 5. What It Calls
- The standard library's `csv.DictReader`. No network or DB access.

## 6. Debugging / Tracing
- INFO: "kg_data: loaded reference data from <dir> (courses=… roles=… …)."
- WARNING: "kg_data: KG_DATA_DIR not set and no CSV directory found; using
  built-in starter aliases. Entity resolution will be limited."
- WARNING: "kg_data: directory <dir> exists but produced no rows; falling
  back to starter dataset."
- Common failure modes:
  - **Most entities resolve to None** — starter dataset is active. Set
    `KG_DATA_DIR` to a folder containing the four CSVs.
  - **Alias collides** — two CSV names normalize to the same string. The
    second wins; rename one entity in the CSV.

## 7. What NOT To Put In It
- KG operations or Neo4j queries — this module deliberately knows nothing
  about the graph itself, only its catalogue of ids and names.
- Student-level data.
- Heavy fuzzy-matching logic. The current matcher uses normalized longest
  substrings; if the QU layer needs smarter resolution, add it there with a
  dedicated test.
