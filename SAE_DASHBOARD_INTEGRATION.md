# SAE Full Integration Guide

This branch (`sae-full-integration`) is a **completely self-contained** version of PathFinder with the Student Analysis Engine (SAE) bundled directly inside the repo. A `git clone` followed by filling in `.env` is the entire setup process — no separate SAE folder needed.

---

## 1. What is inside this repo now

| Folder / File | What it is |
|---|---|
| `SAE/` | The complete SAE engine — analysis, risk flags, CGPA computation, course analytics |
| `SAE/sae/` | SAE Python package (`api.py`, `engine.py`, `data_loader.py`, etc.) |
| `SAE/data/` | Course catalogue Excel file |
| `SAE/students_anonymous (1).xlsx` | Anonymised student dataset |
| `SAE/engines/ale/rules/curriculum_2026.json` | Prerequisite graph + curriculum data |
| `SAE/requirements.txt` | SAE-only Python dependencies |
| `SAE/.env.example` | SAE env vars template (just GROQ key for LLM features) |
| `adapters/sae_adapter.py` | PathFinder-side HTTP client to SAE |
| `gateway/sae_rules_bridge.py` | Converts RAG rule bundles to SAE's flat dict format |
| `ui_react/` | React frontend — login, chat, Student Analysis, Advisor Console |
| `.env.example` | Complete env vars for both PathFinder and SAE |

---

## 2. Three services, one repo

| Service | Command | Port |
|---|---|---|
| **SAE** (analytics engine) | `cd SAE/ && uvicorn sae.api:app --port 8502` | 8502 |
| **PathFinder** (chat backend) | `uvicorn main:app --port 8000` | 8000 |
| **Frontend** (static React) | `cd ui_react/ && python -m http.server 5500` | 5500 |

PathFinder connects to SAE at startup via `SAE_BASE_URL` in `.env`. If SAE is not running, PathFinder starts anyway and logs a warning — the chat pipeline still works, only the `/sae/*` routes return 503.

---

## 3. Setup (fresh clone)

```bash
# 1. Clone and enter
git clone https://github.com/22-101172/PathFinder
cd PathFinder
git checkout sae-full-integration

# 2. Install PathFinder dependencies
pip install -r requirements.txt

# 3. Install SAE dependencies
cd SAE/
pip install -r requirements.txt
cd ..

# 4. Configure environment
cp .env.example .env          # PathFinder config
cp SAE/.env.example SAE/.env  # SAE config (only GROQ key needed for LLM)
# Edit .env and fill in: Neo4j credentials, GROQ_API_KEY, LLM keys

# 5. Start everything (three terminals)
cd SAE/ && uvicorn sae.api:app --port 8502          # Terminal 1
uvicorn main:app --reload --port 8000               # Terminal 2
cd ui_react/ && python -m http.server 5500          # Terminal 3
```

Open `http://localhost:5500`. Log in with any student ID (`STU000528`) or advisor ID (`ADV001`).

Watch Terminal 2 for:
```
PathFinder: SAE connected at http://localhost:8502
```

If it says `SAE not reachable`, start SAE first and restart PathFinder.

---

## 4. Environment variables

### PathFinder `.env` (repo root)

All PathFinder vars are already documented in `.env.example`. The SAE-related ones:

```
SAE_BASE_URL=http://localhost:8502   # Where SAE is running
SAE_TIMEOUT_SECONDS=30               # HTTP timeout for SAE calls
```

### SAE `SAE/.env`

```
GROQ_API_KEY=your_groq_key_here     # LLM features (optional — analysis works without it)
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile
```

SAE's LLM features (advisor session guides) are completely optional. All CGPA analysis, risk scoring, and course analytics work without any API key.

---

## 5. What was changed vs `person-seif`

### New: `SAE/` folder
The entire SAE engine is now part of the repo. Paths inside SAE are anchored to the SAE folder itself using `Path(__file__).parent.parent`, so it works regardless of where the repo is cloned.

### New: `adapters/sae_adapter.py`
Thin HTTP client that proxies PathFinder's `/sae/*` routes to SAE's API. Same shape as `KGAdapter` and `RAGAdapter` — never raises, returns `{"error": "..."}` on failure.

### New: `gateway/sae_rules_bridge.py`
Converts PathFinder's 8 RAG rule bundles (loaded at startup from RAG) into SAE's flat dict format. Covers 28 of SAE's 63 policy keys; the other 35 fall back to SAE's own EUI-calibrated defaults.

### Edited: `main.py`
Added SAE adapter initialization at startup + 5 proxy routes (`/sae/student/{id}`, `/sae/advisor/overview`, `/sae/student/{id}/analysis`, `/sae/courses/risk`, `/sae/student/{id}/simulate`). The existing chat pipeline is untouched.

### New: `ui_react/`
React frontend with real data wired to the backend. No build step — open `index.html` directly or serve with any static server. React, ReactDOM, and Babel are bundled locally in `ui_react/vendor/`.

---

## 6. Smoke test checklist

- [ ] `curl http://localhost:8502/sae/health` → `{"status":"ok"}`
- [ ] `curl http://localhost:8000/sae/health` → confirms PathFinder proxy reaches SAE
- [ ] Log in as `STU000528` → chat page loads, ask "What is my current GPA?" → real answer
- [ ] Click **My Analysis** → CGPA chart and metrics load with Maxwell Avila's data
- [ ] Log out → log in as `ADV001` → Advisor Console loads (first load ~30–45s, then cached)
- [ ] Enter `STU000528` in Advisor Console search → student profile appears
- [ ] Try invalid ID at login → "couldn't find that ID" error, no crash

---

## 7. Known gaps

1. **Advisor Console cold start** — First call to `/sae/advisor/overview` computes analytics for all 710 students and takes ~30–45s. Subsequent calls use a 24h file cache and return in ~2s.
2. **No real auth** — Login is prefix-based (`STU...` → student, `ADV...` → advisor). Fine for demo, not for production.
3. **CORS is wide open** — `allow_origins=["*"]` in `main.py`. Tighten before any real deploy.
4. **GPA simulation not in chat** — The `/sae/student/{id}/simulate` endpoint exists, the JS client has `PF_API.simulateGpa`, but no chat intent triggers it automatically yet.
