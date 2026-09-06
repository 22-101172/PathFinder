# PathFinder

PathFinder is an AI-powered academic advising chatbot for Egyptian University of Informatics (EUI).

The current project keeps only the chatbot stack built on:

- `RAG` for handbook and policy retrieval with citations
- `KG` for curriculum, skills, tracks, and career relationships
- `ALE` for deterministic academic planning, audits, eligibility, and GPA logic

It includes a FastAPI backend and a Streamlit chat UI.

## Core Capabilities

- Course information, prerequisites, and skills taught
- Career roles, skill-gap analysis, and track guidance
- Semester planning, graduation roadmaps, and graduation audits
- Eligibility checks and GPA simulations
- Student-record answers from the loaded transcript
- Policy answers grounded in the handbook with citations

## API

`main.py` exposes:

- `POST /chat`
- `GET /sessions/{student_id}`
- `GET /students/{student_id}/sessions/{session_id}/history`
- `DELETE /students/{student_id}/sessions/{session_id}`
- `DELETE /dev/students/{student_id}/sessions`
- `DELETE /dev/sessions`
- `GET /health`

## Main Components

- `gateway/` handles query understanding, orchestration, response composition, student context, and session memory.
- `engines/rag/` answers handbook and policy questions and loads rule bundles.
- `engines/kg/` serves course, track, skill, and role data from Neo4j.
- `engines/ale/` performs planning and academic decision logic.
- `ui/streamlit_app.py` is the browser chat UI.

## Setup

1. Create and activate a Python 3.10+ virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure `.env` with your LLM, Neo4j, session, and UI settings.
4. Start Neo4j and load the KG if needed.
5. Build the RAG index if needed:

```bash
python engines/rag/ingest.py
```

6. Start the backend:

```bash
python -m uvicorn main:app --reload
```

7. Run the Streamlit UI:

```bash
python -m streamlit run ui/streamlit_app.py
```

## Typical Configuration

```env
LLM_PROVIDER=groq
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=your_key_here
LLM_MODEL=llama-3.1-8b-instant
LLM_TIMEOUT_SECONDS=20

QU_PRIMARY_MODEL=llama-3.3-70b-versatile
QU_FALLBACK_MODELS=openai/gpt-oss-120b,openai/gpt-oss-20b
QU_TIMEOUT_SECONDS=30

COMPOSER_USE_LLM=true
COMPOSER_PRIMARY_MODEL=qwen/qwen3-32b
COMPOSER_FALLBACK_MODELS=llama-3.1-8b-instant,openai/gpt-oss-20b
COMPOSER_TIMEOUT_SECONDS=30

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j

GROQ_API_KEY=your_groq_key
RAG_GROQ_MODEL=llama-3.1-8b-instant
RAG_FALLBACK_MODELS=llama-3.1-8b-instant
RAG_TIMEOUT_SECONDS=60
RAG_RULE_BUNDLE_DELAY_SECONDS=2.0

SESSION_DB_PATH=./pathfinder_sessions.db
QU_CONTEXT_TURNS=5
PATHFINDER_API_URL=http://localhost:8000

APP_ENV=dev
DEV_MODE=true
PATHFINDER_TRACE=false
```

## Testing

Useful offline checks:

```bash
python -m pytest tests/test_main.py -v --tb=short
python -m pytest tests/test_orchestrator.py -v --tb=short
python -m pytest tests/test_query_understanding.py -v --tb=short
python -m pytest tests/test_response_composer.py -v --tb=short
python -m pytest tests/test_session_manager.py -v --tb=short
python -m pytest tests/test_turn_memory.py -v --tb=short
python -m pytest tests/test_student_context_provider.py -v --tb=short
python -m pytest tests/test_ale_adapter.py tests/test_semester_plan_redesign.py -v --tb=short
python -m pytest tests/test_integration_contracts.py -v --tb=short
```

## Notes

- Sessions are stored in SQLite.
- The current semester label is derived from system date.
