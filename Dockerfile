# ── Dockerfile ────────────────────────────────────────────────
FROM python:3.11-slim

# System deps for Python packages that need a compiler
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ----- Python deps -----
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ----- Project code -----
COPY backend/   ./backend/
COPY bot/  ./bot/

ENV PYTHONUNBUFFERED=1

# ── Start Command ─────────────────────────────────────────────
# Render injects the desired port as $PORT (e.g. 10000/8080).
# Using `sh -c` lets us substitute that env var inside CMD.
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
