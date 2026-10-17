# Standalone image. Blocks are vendored into the repository at build
# time. Network posture: P1 — no outbound at runtime.
# Base image pin: Docker Hub library/python:3.12-slim (2026-08-23).
FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .
ENV PYTHONPATH=/app
# Persistence is a sqlite file on the mounted disk (F23).
ENV STORAGE_PATH=/app/data
RUN mkdir -p /app/data

# F19: a red suite must not produce a deployable image.
RUN python3 scripts/release_gate.py

EXPOSE 8000
# S10: migrate against the persistent disk, then serve. Failure refuses boot.
# scripts/entrypoint.sh: alembic upgrade head && uvicorn app.main:app
ENTRYPOINT ["sh", "/app/scripts/entrypoint.sh"]
