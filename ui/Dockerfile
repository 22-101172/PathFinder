# ui/Dockerfile
# ──────────────
# Minimal chat interface for PathFinder.
# Streamlit is recommended for speed. React is fine if preferred.
#
# Streamlit option:
#   Build: app.py + requirements.txt (streamlit, httpx)
#   Port:  3000
#
# The UI only needs to:
#   1. Show a text input
#   2. POST to http://gateway:8000/query
#   3. Display answer_text + any citations
#   4. Persist session_id across turns (store in session_state / localStorage)

FROM python:3.11-slim

WORKDIR /app

# TODO: add your UI requirements
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt

# TODO: copy your UI source
# COPY . .

EXPOSE 3000

# TODO: start your UI
# CMD ["streamlit", "run", "app.py", "--server.port=3000", "--server.address=0.0.0.0"]
