# RAG Backend Integration

This document outlines the steps taken to fully integrate the RAG pipeline into the backend Orchestrator, and instructions on how to run it without the frontend using the Colab LLM link.

## What Was Done

1. **RAGWrapper Integration in Orchestrator**:
   - Edited `gateway/orchestrator.py` to import the `RAGWrapper` from `backend.wrappers.rag_wrapper`.
   - Instantiated `self._rag = RAGWrapper()` inside the `Orchestrator` initialization.
2. **RAG Workflow Execution**:
   - Modified the `process_query` method in `orchestrator.py` to handle the `"rag"` engine pattern.
   - When the `engine_pattern == "rag"`, the Orchestrator now calls `self._rag.execute(sub_query=query.intent, student_context=context)` and packages the response into the `AggregatedResult` under the `rag_result` key.
3. **No Frontend Requirement**:
   - The Orchestrator correctly handles this logic directly in the backend, meaning no frontend UI is strictly necessary to perform RAG queries.

---

## How to Run the RAG Backend (No Frontend)

Since the RAG model relies on the Mistral-7B model hosted on Google Colab, you need to spin that up first and provide the API link to your local backend.

### Step 1: Start the Colab LLM Server
1. Upload and run the `colab_llm_server.ipynb` file in Google Colab.
2. Ensure you have a T4 GPU enabled.
3. Run all cells. The final cell will start a localtunnel and provide a public URL (e.g., `https://some-random-words.loca.lt`). 
4. **Copy that URL.**

### Step 2: Set the Environment Variable Locally
Before starting your local FastAPI backend, you must set the environment variable `COLAB_LLM_URL` so the `RAGWrapper` knows where to send the prompts.

**On Windows (PowerShell):**
```powershell
$env:COLAB_LLM_URL="https://your-localtunnel-url.loca.lt"
```

**On Mac/Linux (Bash):**
```bash
export COLAB_LLM_URL="https://your-localtunnel-url.loca.lt"
```

### Step 3: Start the Backend Server
Start your FastAPI backend server (usually via Uvicorn). Ensure you are in the directory where your main app lives.
```bash
uvicorn gateway.main:app --reload
# Or however your specific start command is defined
```

### Step 4: Test via API (cURL or Postman)
Since there is no frontend, you can test the RAG functionality by directly querying the backend API. Assuming your backend exposes a `/query` endpoint on port 8000:

**Example cURL Request:**
```bash
curl -X POST "http://localhost:8000/query" \
     -H "Content-Type: application/json" \
     -d '{
           "user_text": "What are the rules for academic probation?",
           "active_student_id": "123456"
         }'
```

*Note: The query understanding layer must parse the text and assign `engine_pattern: "rag"` for the query to route through the newly implemented RAG workflow in the Orchestrator.*

### Debugging & Logs
If the RAG fails:
1. Ensure the Colab localtunnel link hasn't expired.
2. Verify you clicked the "Click to Continue" button on the localtunnel reminder page if prompted.
3. Check your local backend terminal for `RAGWrapper` logs.
