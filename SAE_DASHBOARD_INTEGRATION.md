# SAE Dashboard Integration — Notes for Seif

This branch adds the **Student Analysis Engine (SAE) dashboard** on top of
PathFinder: a real login screen, a Student Analysis page, and an Advisor
Console — all wired to live data through new proxy routes in `main.py`.

Your chat pipeline (`/chat`, sessions, QU → Orchestrator → Composer,
RAG/KG/ALE) is **completely untouched**. This only adds new things alongside it.

---

## 1. The three moving pieces

| Piece | What it is | Where it runs |
|---|---|---|
| **SAE** | The analytics engine (CGPA, risk flags, course difficulty, advisor insights) | Its own process, port `8502` |
| **PathFinder backend** | This repo — `main.py` | Port `8000`, unchanged location |
| **New frontend** | `ui_react/` — login, chat, Student Analysis, Advisor Console | Static files, any port (e.g. `5500`) |

SAE is a **separate service**, not part of this repo's Python process. PathFinder
talks to it over HTTP through a new adapter, exactly the same pattern as how
`KGAdapter` talks to Neo4j or `RAGAdapter` talks to the vector store — just
swap "Neo4j" for "another FastAPI service."

---

## 2. What was actually changed in this repo

### New file: `adapters/sae_adapter.py`
A thin HTTP client, same shape as `KGAdapter`/`RAGAdapter`/`ALEAdapter`. Exposes:
`get_student_analysis`, `get_advisor_overview`, `get_advisor_analysis`,
`get_course_risk`, `simulate_gpa`, `health_check`. Every method returns a
plain dict — `{"error": "..."}` on failure, never raises, so callers can
decide what to do with it.

### Edited: `main.py`
Three small additions, all isolated:
1. Import + global `_sae: Optional[SAEAdapter]`
2. In `lifespan()`: instantiate `SAEAdapter()` at startup and log whether it's
   reachable (non-fatal if not — same degrade-gracefully pattern as the KG
   resolver when Neo4j is down).
3. Six new routes, all prefixed `/sae/`, right before the existing `/health`
   endpoint. They just proxy to the adapter and translate its dict responses
   into proper HTTP status codes (404 / 502 / 200).

Nothing in the existing `/chat`, `/sessions`, or session-management routes
was touched.

### New folder: `ui_react/`
A plain React app (Babel-in-browser, no webpack/vite build step — open
`index.html` directly or serve it with any static server). React, ReactDOM,
and Babel are bundled locally in `ui_react/vendor/` so there's no CDN
dependency for the demo.

```
ui_react/
├── index.html          ← entry point, sets window.__PF_API_BASE__
├── app.css              ← your existing EUI-branded design system, unchanged
├── vendor/              ← react, react-dom, babel (local, no CDN)
└── js/
    ├── components.js    ← Icon, LogoMark, Spinner, ErrorState, etc.
    ├── api.js            ← NEW — every fetch() call to the backend lives here
    ├── data_flows.js     ← i18n strings only (mock chat flows removed)
    ├── sae_pages.js      ← StudentAnalysisPage + AdvisorConsole, now data-driven
    └── main_app.js       ← login screen + real chat wiring + page routing
```

---

## 3. How this maps to the UI you built in Claude Design

Your `StudentAnalysisPage` and `AdvisorConsole` components were already
fully designed — they just rendered hardcoded mock objects (`PF_ANALYSIS.student`,
`PF_ANALYSIS.advisor`, `PF_ANALYSIS.sample`) and every action button showed a
"Ready for backend — not wired yet" toast.

What changed: those components now call `PF_API.getStudentAnalysis(id)` /
`PF_API.getAdvisorOverview(id)` / `PF_API.getAdvisorAnalysis(id)` on mount,
show a spinner while loading, an error state with Retry if the call fails,
and map the real JSON response into the exact same props your components
already expected (`s.gpa`, `s.gpaHistory`, `s.cohortStanding`, etc.). The
visual design is identical — only the data source changed.

I also added a "Deeper Analysis" section using your existing `.an-card` style
that surfaces things SAE computes but the original mock didn't have a slot
for: risk flags, anomaly alerts, subject-area performance, prerequisite
bottlenecks, semester difficulty, graduation outlook, and suggestions.

I left the "Export report" / "Flag for outreach" / "Open transcript" buttons
as stubs (still show "Coming soon") since there's no backend feature for
those yet — didn't want to fake it.

---

## 4. Login

There's no real auth system yet, so login is ID-prefix based:
- `STU...` → validated against SAE (`GET /sae/student/{id}`); rejected if not found → routed to Chat + My Analysis
- `ADV...` → **not validated** against a real advisor table (SAE simulates the
  advisor→student assignment with a hash internally) → routed straight to
  Advisor Console

This needs a real identity check before this goes anywhere beyond a demo —
flagging it explicitly so it doesn't get missed.

---

## 5. How to run all three pieces

```bash
# Terminal 1 — SAE
cd SAE/
uvicorn sae.api:app --port 8502

# Terminal 2 — PathFinder backend (this repo)
cp .env.example .env   # if not already done; then add SAE_BASE_URL=http://localhost:8502
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Terminal 3 — frontend
cd ui_react/
python3 -m http.server 5500
```
Open `http://localhost:5500`. If your backend isn't on `localhost:8000`, change
the one line at the top of `ui_react/index.html`:
```js
window.__PF_API_BASE__ = "http://localhost:8000";
```

Watch Terminal 2's startup log for:
```
PathFinder: SAE connected at http://localhost:8502
```
If it says "SAE not reachable" instead, start SAE first and restart this.

---

## 6. What I actually verified vs. what still needs a real test

Being precise here so nothing gets assumed that wasn't checked:

**Verified:**
- `main.py` and `adapters/sae_adapter.py` parse as valid Python (no syntax errors)
- All 5 files in `ui_react/js/` compile through Babel with zero JSX errors
- Rendered the actual UI in headless Chromium: login screen works, advisor
  login routes correctly, and when no backend is reachable the UI shows a
  clean "Cannot reach server / Retry" state instead of crashing — screenshots
  attached separately if useful

**Not verified (couldn't be, without your live SAE + this backend running together):**
- The real end-to-end flow with actual student data flowing through both services
- That every field name I assumed SAE returns (`official_cgpa`, `risk_flags`,
  `cohort_percentile`, `category_performance`, etc.) matches exactly what your
  current SAE build outputs — I worked from the SAE API contract as it stood
  at the time, but SAE's response shape may have shifted slightly since
- One real bug I found on a second pass and already fixed: SAE might return
  "student not found" as either a true HTTP 404 *or* an HTTP 200 with
  `{"error": "..."}` in the body depending on how its FastAPI layer was
  written. `main.py`'s `_sae_or_502()` helper now checks for both, but worth
  double-checking which one your SAE actually does

**First thing to do when you pull this:** run the smoke-test checklist below.
If a field is missing or named differently than expected, the affected card
will silently show blank/`N/A` rather than crash — check the browser console
network tab for the raw JSON if a section looks empty.

---

## 7. Smoke test checklist

- [ ] `curl http://localhost:8502/sae/health` → `{"status":"ok"}`
- [ ] `curl http://localhost:8000/health` → `{"status":"ok",...}`
- [ ] `curl http://localhost:8000/sae/health` → confirms the proxy route reaches SAE
- [ ] Log in as a real student ID → land on Chat, ask a question → real answer from the orchestrator
- [ ] Click "My Analysis" → CGPA chart + metrics load (check Network tab if blank)
- [ ] Log out, log in as `ADV001` (or any `ADV###`) → Advisor Console loads with risk summary
- [ ] Search a real student ID in Advisor Console → profile + key points + session guide load
- [ ] Try an invalid student ID at login → should show "couldn't find that ID," not crash

---

## 8. Known gaps / things to decide together

1. **RAG rules aren't passed to SAE yet.** `main.py` calls the adapter without
   a `rules=` argument, so SAE uses its own correct EUI-handbook defaults.
   If you want the live RAG rule bundle (`_rag.get_rule_bundles()`, already
   loaded at startup) to override SAE's defaults, that's a one-line addition
   per route — just say the word.
2. **GPA simulation isn't wired into chat yet.** The endpoint exists
   (`POST /sae/student/{id}/simulate`) and the JS client supports it
   (`PF_API.simulateGpa`), but no chat intent calls it automatically. The
   UI's "Simulate my GPA" suggestion chip currently just sends that text as a
   normal chat message.
3. **CORS is wide open** (`allow_origins=["*"]`), matching what was already
   there — fine for local testing, should be tightened before any real deploy.
4. **Advisor identity** — see §4 above.
