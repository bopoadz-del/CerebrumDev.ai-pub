FROM public.ecr.aws/docker/library/python:3.11-slim-bookworm

WORKDIR /app

# Build deps for chromadb/hnswlib. Neon is Postgres 18; Debian bookworm's
# postgresql-client is 15 and pg_dump then exits 1 (version mismatch).
# Install the PGDG 18 client so nightly dumps match the server.
# Write the PGDG ASCII key and point apt signed-by at it. Slim images often
# omit the gpg binary even when gnupg is listed, which failed CI in ~20s.
# SQLAlchemy fallback in app.core.backup still covers a mismatch if apt pins drift.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ cmake git ca-certificates curl util-linux bash nodejs npm \
    && mkdir -p /usr/share/keyrings \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/keyrings/pgdg.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.lock backend/requirements.txt backend/requirements-marker.txt ./
# Install the locked graph, not a floating requirements.txt resolve.
# requirements.txt remains the direct manifest; lock is the image contract.
# Do NOT install requirements-marker.txt in production images.
# marker-pdf is local-only; see README for details.
RUN pip install --no-cache-dir -r requirements.lock

# The CodeWhale writer worker (Phase 5): headless `codewhale exec` drives the
# WRITER role. npm-global, version-pinned like every other binary in this
# image. Credentials stay OUT of the image: the CLI reads its model config
# from the deployment env at runtime.
ARG CODEWHALE_VERSION=0.9.13
RUN npm install -g --no-fund --no-audit codewhale@${CODEWHALE_VERSION} \
    && codewhale --version

COPY backend/app /app/app
# Golden product blueprints (Steward + examples) — required by ProductArchitect in prod
COPY blueprints /app/blueprints
# Factory store pin. factory_repo_root() is /app in this image, so
# default_lock_path() is /app/blocks.lock.json. S07 wrote the lock at the
# repo root but the image never copied it; CLONER then treated every
# store-sourced block as unlocked (live Steward Continue sess_5782f226
# died at database with the lock-hash that was already committed).
COPY blocks.lock.json /app/blocks.lock.json
# The Store commit this image was built and locked against (store.pin);
# engine resolution reads it -- the Factory never follows Store main.
COPY store.pin /app/store.pin

# The Store, baked at image build. Cerebrum-Blocks is private, so the
# runtime clone (engine_discovery) has no anonymous path any more -- the
# deploy workflow clones the Store into store-blocks/ with CROSS_REPO_PAT
# and this COPY ships it. CI's docker-build job ships an empty placeholder
# instead; blocks_source ignores a root with no block_registry/, so a CI
# image still falls through to the mirror instead of trusting a dead root.
COPY store-blocks /app/store-blocks
ENV CEREBRUM_BLOCKS_ROOT=/app/store-blocks
# Alembic migration system for the accounts DB (runs at boot; see backend/alembic/)
COPY backend/alembic.ini /app/alembic.ini
COPY backend/alembic /app/alembic

# Operational entry points: the backup CLI (manual snapshot/restore/drill; the
# nightly backup itself runs IN-PROCESS in the web service, not as a cron —
# Render cron jobs cannot mount the disk) and the Postgres cutover script.
# Without this COPY those tools fail at import.
COPY backend/scripts /app/scripts

# S0 preflight fingerprints repo-relative paths from factory_repo_root()
# (/app in this image): backend/app/factory/**, cerebrum_product_kernel, and
# .github/workflows/ci.yml. COPY backend/app → /app/app drops the backend/
# prefix those paths use, and never shipped ci.yml. Plant both so
# factory_source_missing cannot fire on Approve & build.
RUN mkdir -p /app/backend && ln -s /app/app /app/backend/app
COPY .github/workflows/ci.yml /app/.github/workflows/ci.yml

# The manufacturing executor is the CodeWhale worker (FACTORY_CODEWHALE_WRITER):
# headless codewhale exec with DEEPSEEK_API_KEY, version-pinned above.
# No kimi / claude / cursor binaries ship in this image.
ENV PORT=8000
# libpq defaults sslcert to $HOME/.postgresql/postgresql.crt. python:slim
# leaves HOME=/root. After the entrypoint drops to uid 10001 that path is
# EACCES and Alembic/Neon boot dies — Neon only needs sslmode=require.
# HOME=/app is owned by appuser; do not set PGSSLCERT.
ENV HOME=/app
# Non-root runtime. The entrypoint chowns $STORAGE_PATH when started as root
# (Render disk mounts are often root-owned) then drops to uid 10001.
# The HTTP contract is unchanged: bind 0.0.0.0:$PORT after migrations.
RUN useradd --system --uid 10001 --home-dir /app --no-create-home appuser \
    && mkdir -p /app/storage /app/storage/factory_outputs \
    && chown -R appuser:appuser /app
# Runtime: Render mounts cerebrumdev-storage at /app/storage (hides this layer).
# docker-entrypoint.sh re-mkdirs $STORAGE/factory_outputs and symlinks
# /app/factory_outputs → that path so Factory ledgers survive deploys.
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod 755 /app/docker-entrypoint.sh

# Read by app/core/metrics.py; without it the factory's S0 preflight
# refuses every generation with git_sha_unknown.
ARG GIT_COMMIT=""
ENV GIT_COMMIT=$GIT_COMMIT

ENTRYPOINT ["/app/docker-entrypoint.sh"]
# Apply accounts-DB migrations before serving (no-op when already at head).
# A failed migration must stop the boot. The previous form swallowed the
# failure with `|| echo`, which produced a RUNNING service on the wrong schema:
# /health still answered 200, Render never restarted it, no alert fired, and
# users got 500s from any path touching a missing column. Crashing is louder
# and safer -- Render surfaces a failed deploy and keeps the previous version
# serving.
CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
