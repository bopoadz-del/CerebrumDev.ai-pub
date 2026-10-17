"""Entrypoint for the generated platform.

Runs standalone: uvicorn app.main:app. No factory, no block store, no
outbound dependency at runtime (P1: Delivered platforms run in-process against vendored blocks; local/scripted OCR only; no Store URL, no cloud LLM, no Ollama, no outbound HTTP at runtime.).
Kernel jobs are at GET /v1/jobs.
GET /health is fail-closed (process, disk, DB, Alembic head).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.health import health_response
from app.observe import install_observability
from app.routes import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail-closed: a revision behind head refuses boot.
    from app.migrations import upgrade_head
    from app.observe import configure_logging

    upgrade_head()
    # Uvicorn configures logging after import; win it back for JSON lines.
    configure_logging()
    yield


app = FastAPI(title="Role Runner Smoke Product", lifespan=lifespan)
install_observability(app)
app.include_router(router, prefix="/v1")


@app.get("/health")
def health():
    return health_response()
