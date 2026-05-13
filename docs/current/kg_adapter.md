# `gateway/adapters/kg_adapter.py`

## 1. Purpose
Thin adapter between the gateway orchestrator and the local Knowledge Graph
query layer. Exposes 15 KG operations through a single dispatching method,
`call(operation, params) -> dict`. Decides nothing about routing — it just
translates a (name, params) pair into the corresponding Cypher query and
returns the result, normalizing failures into a structured error dict.

## 2. What's Inside
- `KGAdapter` class.
  - `__init__` opens a `Neo4jClient` connection at startup; connection
    failures only log a warning so the gateway can still boot.
  - `close()` releases the driver from FastAPI's `lifespan` handler.
  - `call(operation, params)` — the public entry point used by the
    Orchestrator.
  - One thin method per supported KG operation (e.g. `get_course_profile`,
    `compute_skill_gap`, `find_best_matching_roles`, …). Each is a
    try/except wrapper around the corresponding `engines.kg.queries.q_*`
    function.

## 3. Inputs / Outputs
- Input: `operation: str` and `params: dict` shaped according to the
  underlying query (see `engines/kg/queries.py`).
- Output: a `dict`. Two failure shapes flow through here:
  - From `engines/kg/queries.py`: `{"error": "...", ...}` (business-logic
    failures, e.g. `course_not_found`).
  - From the adapter wrapper: `{"status": "error", "message": "..."}` when
    Cypher / driver / Python raises.
  The orchestrator handles both shapes; do not "normalize" them away —
  losing the original key would cost diagnostic detail.

## 4. Who Calls It
- `gateway.orchestrator.Orchestrator` exclusively.
- `gateway.main` for the `close()` call at shutdown.

## 5. What It Calls
- `engines.kg.neo4j_client.Neo4jClient` — the database connection.
- `engines.kg.queries.q_*` — the actual Cypher functions.

## 6. Debugging / Tracing
- Logs `KGAdapter: Neo4j connection failed at startup: …` (WARNING) when the
  database is unreachable. Subsequent `call()`s will return structured
  errors instead of crashing.
- Per-operation exception logs use the operation name and params:
  `KGAdapter.call('compute_skill_gap') bad params {...}: …`.
- Common failure modes:
  - `"Unknown KG operation: …"` — the orchestrator's intent map is out of
    sync with this adapter's dispatch dict.
  - `"Bad params for …"` — the orchestrator's `_build_kg_params` returned a
    shape the Cypher function does not accept. Usually a missing required
    field.

## 7. What NOT To Put In It
- Routing decisions (which operation to call for a query).
- LLM calls.
- Raw user text. The adapter only sees structured parameters.
- Anything that interprets KG results — the orchestrator and composer split
  that responsibility.
