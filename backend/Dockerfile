# LOCAL / COMPOSE ONLY — not the production image.
#
# Production is built from the repo-root Dockerfile (Alembic at boot,
# blueprints copied in, postgresql-client-18, lockfile install, non-root USER).
# docker-compose.yml maps host 8001 -> container 8000 so this image does not
# collide with a native `uvicorn --port 8000` on the same machine.
#
# Do not deploy this file on Render.
FROM python:3.11-slim
WORKDIR /app

# Install build dependencies for chromadb/hnswlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ cmake \
    && rm -rf /var/lib/apt/lists/*

# Prefer the lockfile when present so local compose tracks CI/prod.
COPY requirements.lock requirements.txt ./
RUN if [ -f requirements.lock ]; then \
      pip install --no-cache-dir -r requirements.lock; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi
COPY ./app /app/app
# Golden blueprints live at repo root; mount or copy them for ProductArchitect.
# Production image is built from repo-root Dockerfile (copies /blueprints).
ENV PORT=8000
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
