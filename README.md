# PathFinder Integration

PathFinder is an academic advising assistant for Egyptian University of Informatics (EUI). It combines structured curriculum and career data, handbook retrieval, student transcript context, and rule-based academic logic behind a single chat interface.

The project is split into a FastAPI backend and a Streamlit frontend. A student logs in with a student ID, asks advising questions in natural language, and the system routes the request to the most suitable engine:

- Knowledge Graph for curriculum, tracks, skills, and career-role relationships
- RAG for handbook and policy questions
- Academic Logic Engine (ALE) for eligibility, graduation audit, semester planning, and GPA simulation
- LLM-based query understanding and response composition to make the experience conversational

## What The Project Does

PathFinder supports several advising workflows:

- Course exploration: course profile, credits, description, level, and prerequisites
- Skill exploration: what a course teaches and which courses teach a given skill
- Career guidance: role profiles, role-track fit, best matching roles, and skill gaps
- Track guidance: track overviews, comparisons, and recommendations
- Policy Q&A: handbook-based answers with citations
- Student-aware advising: uses the logged-in student's academic context from the Excel dataset
- Session-based chat: keeps conversation history and resolves follow-up references like "that course" or "that track"

## Architecture

### 1. API Layer

`main.py` exposes the backend service using FastAPI:

- `POST /chat` handles user questions
- `GET /sessions/{student_id}` returns previous chat sessions for a student
- `GET /session/{session_id}/history` returns conversation history
- `GET /health` returns a basic health response

### 2. Gateway Layer

The `gateway/` package coordinates the system:

- `query_understanding.py`: classifies the question into an intent and engine pattern
- `orchestrator.py`: routes the request to KG, RAG, ALE, or mixed execution
- `response_composer.py`: turns raw engine output into a user-friendly answer
- `student_context_provider.py`: loads student data from Excel and builds a normalized student context
- `session_manager.py`: stores sessions and recent chat history in memory

### 3. Engine Layer

The `engines/` package contains the reasoning backends:

- `engines/kg/`: Neo4j-backed knowledge graph queries for courses, tracks, skills, and roles; includes a multi-step entity resolver that maps natural-language names to graph IDs
- `engines/rag/`: handbook retrieval pipeline using Chroma, BM25, and a cross-encoder reranker
- `engines/ale/`: academic logic modules for eligibility, graduation audit, semester planning, and GPA simulation

### 4. Adapter Layer

The `adapters/` package gives the orchestrator a clean interface for each engine:

- `KGAdapter`
- `RAGAdapter`
- `ALEAdapter`

### 5. UI Layer

`ui/streamlit_app.py` provides a simple student-facing chat UI with:

- Student ID login
- New chat / session history selection
- Chat-style message flow
- Citation display for handbook answers

## How A Request Flows

1. The student sends a message from the Streamlit UI.
2. FastAPI receives the request on `POST /chat`.
3. The system loads or reuses the student's session and academic context.
4. The query understanding layer detects the intent and required engine.
5. The orchestrator calls the matching backend:
   - KG for structured curriculum/career questions
   - RAG for handbook/policy questions
   - ALE for academic decision logic
   - Mixed for questions that need both structured data and handbook context
6. The response composer rewrites the raw result into a clear answer.
7. The answer, citations, and updated session metadata are returned to the UI.

## Data Sources

This project currently depends on several local and external data sources:

- `data/students_anonymous.xlsx`
  - `data` sheet for student profile fields
  - `registrations` sheet for transcript and registration history
- `engines/rag/CIS_Handbook.md`
  - source document used to build the RAG index
- `engines/rag/chroma_db/` and `engines/rag/chunks.pkl`
  - generated retrieval artifacts
- `engines/ale/rules/curriculum_2026.json`
  - curriculum and academic rules used by ALE
- Neo4j database
  - stores the curriculum / skills / role knowledge graph
- `engines/kg/data/entity_aliases.json`
  - alias and ambiguous-term table used by the KG entity resolver; maintained manually

## RAG Pipeline

The handbook QA engine uses a hybrid retrieval pipeline:

- Parent/child chunking during ingestion
- Dense vector retrieval with `BAAI/bge-small-en-v1.5`
- Sparse retrieval with BM25
- Reciprocal rank fusion
- Cross-encoder reranking with `cross-encoder/ms-marco-MiniLM-L-6-v2`

You only need to rebuild the index when the handbook source changes.

## Knowledge Graph Engine

The KG engine exposes 16 operations across four query groups:

- **Course catalogue (A2)**: course profile, prerequisites (direct or full recursive tree; non-course constraints are stored as `PrerequisiteConstraint` nodes), skills taught by a course, and course search by skill name
- **Career role exploration (B1)**: role profiles with weighted required skills, and roles reachable through a track's courses and skills
- **Skill gap and alignment (B2)**: skill gap analysis, weighted alignment scoring, gap-closing course recommendations, alignment improvement estimation for planned courses, and full role ranking by alignment
- **Track guidance (B3)**: track overview (courses, skills, supported roles), side-by-side track comparison, and track recommendations for a given role or skill

Skills carry a numeric weight that drives all alignment calculations. Weights map to three tiers: `core` (≥ 0.8), `supporting` (≥ 0.6), and `optional` (< 0.6).

The engine also includes a `resolve_entity` operation that maps a natural-language name to a graph ID for any entity type (course, role, track, skill). The resolver runs a six-step pipeline — exact ID match, exact normalized name match, alias lookup, explicit ambiguous-term lookup, partial name match — and loads its alias table from `engines/kg/data/entity_aliases.json`.

## Project Structure

```text
PathFinder_Integration/
|- adapters/              # Thin wrappers around KG, RAG, and ALE
|- data/                  # Student dataset
|- engines/
|  |- ale/                # Academic logic engine
|  |- kg/                 # Neo4j queries and client
|  |- rag/                # Handbook ingestion and retrieval
|- gateway/               # Routing, context, session, response composition
|- ui/                    # Streamlit frontend
|- main.py                # FastAPI entrypoint
|- requirements.txt       # Backend dependencies
|- README.md
```

## Requirements

- Python environment with the packages in `requirements.txt`
- Neo4j instance populated with the knowledge graph
- An OpenAI-compatible LLM endpoint for query understanding / response composition
- Student dataset Excel file present at `data/students_anonymous.xlsx`

## Configuration

The code reads configuration from `.env`. The important variables used by the project are:

- `LLM_PROVIDER`
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_TIMEOUT_SECONDS`
- `COLAB_LLM_URL`
- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`
- `PATHFINDER_API_URL` for the Streamlit UI, if the backend is not on `http://localhost:8000`

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure the environment:

- Edit `.env`
- Add your `LLM_API_KEY`
- Set the rest of the LLM / Neo4j variables as needed for your environment

Place the student dataset:

- Ensure `data/students_anonymous.xlsx` is present

Build the RAG index the first time, or whenever the handbook changes:

```bash
python engines/rag/ingest.py
```

Run the backend:

```bash
uvicorn main:app --reload
```

Run the UI from a separate terminal:

```bash
python -m streamlit run ui/streamlit_app.py
```

## Example Questions

You can use prompts like:

- "Tell me about C-CS301"
- "What are the prerequisites for C-AI421?"
- "What skills does Deep Learning teach?"
- "Which track is best for a data scientist?"
- "What careers fit my profile?"
- "Can I take C-CS401?"
- "Am I on track to graduate?"
- "What is the warning policy?"
- "How many absences are allowed?"

## Current Limitations

These are worth knowing if you continue developing the project:

- Sessions are stored in memory only. Restarting the backend clears chat history.
- The current semester is hardcoded in the student context provider as `Spring 2026`.
- Student login is based only on IDs found in the Excel sheet.
- The backend assumes the student Excel file has the expected sheet names and columns.
- Semester planning is wired through ALE, but the current orchestrator passes an empty offerings list, so this feature needs more integration work to produce realistic plans.
- GPA simulation is implemented in ALE, but the current chat flow does not yet collect a rich simulation scenario from the user.
- The frontend is a lightweight internal UI and does not include authentication beyond student ID entry.

## Development Notes

- FastAPI and Streamlit are run as separate processes.
- Query understanding and response composition both use the shared `gateway/llm_client.py`.
- The RAG engine can call a Colab-hosted generator through `COLAB_LLM_URL`, or fall back to the same OpenAI-compatible LLM client used elsewhere.
- Neo4j connectivity is verified when the KG adapter starts up.

## Suggested Next Improvements

- Persist sessions in a database instead of memory
- Add proper authentication and authorization
- Connect semester planning to real course offerings
- Add richer GPA simulation input handling in the UI
- Add tests for routing, adapters, and ALE modules
- Add deployment instructions for backend, frontend, Neo4j, and vector index artifacts
