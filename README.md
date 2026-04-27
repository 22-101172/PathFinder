# PathFinder — Integration Phase

AI-powered academic & career advising system for EUI students.

## Quick Start

```bash
cp .env.example .env          # fill in your keys
docker compose up --build     # starts everything
```

UI → http://localhost:3000  
Gateway API → http://localhost:8000  
Neo4j Browser → http://localhost:7474  

## Project Structure

```
pathfinder/
├── gateway/                  # FastAPI backend — all orchestration lives here
│   ├── main.py               # POST /query endpoint
│   ├── session_manager.py    # In-memory session state
│   ├── student_context_provider.py
│   ├── query_understanding.py
│   ├── orchestrator.py
│   ├── response_composer.py
│   ├── wrappers/
│   │   ├── kg_wrapper.py     # KGEngine method calls
│   │   └── rag_wrapper.py    # RAG pipeline calls
│   ├── models/
│   │   └── schemas.py        # All Pydantic data contracts
│   └── data/
│       └── student_profile.json
├── kg_engine/                # Existing KG codebase (KGEngine class)
├── rag_engine/               # Existing RAG pipeline
├── ui/                       # Frontend chat interface
├── docker-compose.yml
└── .env.example
```

## Implementation Order

| Step | Component | Owner |
|------|-----------|-------|
| T01 | student_profile.json | Both |
| T02 | student_context_provider.py | Person A |
| T03 | session_manager.py | Person A |
| T04 | main.py (Gateway) | Person B |
| T05 | kg_wrapper.py | Person B |
| T06 | rag_wrapper.py | Person B |
| T07 | query_understanding.py | Person A |
| T08 | orchestrator.py | Person B |
| T09 | response_composer.py | Person A |
| T10 | ui/ | Person B |
| T11 | Integration Testing | Both |

## Key Rules (read before coding)

- **Override detection** → Query Understanding only. Session Manager only **applies** overrides.
- **One endpoint** → POST /query handles all 6 workflow types. No extra routes needed.
- **LLM stays external** → QU fallback + Response Composer both call a hosted API. Nothing GPU-heavy runs locally.
- **Student record is never modified** → Session overrides are temporary. Base record is read-only.
