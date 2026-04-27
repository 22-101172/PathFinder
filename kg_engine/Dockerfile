# kg_engine/Dockerfile
# ─────────────────────
# Wrap the existing KGEngine codebase in an HTTP service.
# Copy your existing KG engine code into this directory,
# then expose it on port 8001.
#
# Recommended: add a thin FastAPI wrapper in kg_engine/main.py
# that exposes the 15 KGEngine methods as POST endpoints.
# The KGWrapper in gateway/ will call these endpoints.
#
# Alternatively: if you prefer direct Python import (no HTTP),
# remove this Dockerfile and import KGEngine directly in kg_wrapper.py.

FROM python:3.11-slim

WORKDIR /app

# TODO: copy your KG engine source files here
# COPY . .

# TODO: install KG engine requirements
# RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8001

# TODO: start your KG engine HTTP wrapper
# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
