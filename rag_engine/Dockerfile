# rag_engine/Dockerfile
# ──────────────────────
# Wrap the existing RAG pipeline in an HTTP service.
# Copy your existing RAG engine code into this directory,
# then expose it on port 8002.
#
# The RAG wrapper in gateway/ expects a POST /query endpoint
# that accepts { "query": str } and returns { "answer": str, "citations": list }.

FROM python:3.11-slim

WORKDIR /app

# TODO: copy your RAG engine source files here
# COPY . .

# TODO: install RAG engine requirements
# RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8002

# TODO: start your RAG engine HTTP wrapper
# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
