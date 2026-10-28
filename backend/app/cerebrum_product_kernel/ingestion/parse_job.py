"""Typed, persisted parse-job status for an uploaded object (Phase 2 §1, F-1a).

A parse job is the REAL record of where an uploaded document sits on the way to
the F-1b parser. F-1a creates the job and genuinely leaves it ``queued`` — a
document that has been stored and is waiting for the parser, not a 501/stub.
F-1b will move it through ``parsing`` to ``parsed`` / ``unsupported`` / ``failed``
by reusing ``cerebrum_product_kernel/ingestion/chunker.chunk_document``.

Modelled on ``app.models.rag_ingestion`` (the project's existing status layer):
a :class:`ParseStatus` enum and a small pydantic record. Jobs persist as JSON
under the tenant's own uploads dir (``jobs/<job_id>.json``), so they share the
exact tenant-isolation boundary the blobs use — a job is reachable only under
the digest of the identity that created it.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from .object_store import tenant_uploads_dir


class ParseStatus(str, Enum):
    """Lifecycle of a document parse job.

    F-1a rests at ``QUEUED``: stored and genuinely awaiting the F-1b parser.
    """

    NOT_STARTED = "not_started"
    QUEUED = "queued"
    PARSING = "parsing"
    PARSED = "parsed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class ParseJob(BaseModel):
    """A persisted parse-job record for one uploaded object."""

    job_id: str = Field(default_factory=lambda: uuid4().hex)
    object_id: str
    source_name: str
    status: ParseStatus = ParseStatus.QUEUED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_error: Optional[str] = None
    #: Shape metrics the F-1b parser fills in; honest ``None`` until then.
    pages: Optional[int] = None
    sheets: Optional[int] = None
    rows: Optional[int] = None
    #: Whether the parsed chunks reached retrieval (F-1c). ``None`` until the
    #: parser runs ingest-on-parse; then ``"indexed"`` or
    #: ``"not_indexed: <reason>"`` (e.g. "retrieval backend not configured").
    #: A SURFACED state — never a silent debug no-op.
    index_state: Optional[str] = None


def _jobs_dir(identity: str) -> Path:
    return tenant_uploads_dir(identity) / "jobs"


def _job_path(identity: str, job_id: str) -> Path:
    return _jobs_dir(identity) / f"{job_id}.json"


def chunks_path(identity: str, job_id: str) -> Path:
    """Where the F-1b parser persists a job's produced chunks.

    ``jobs/<job_id>/chunks.json`` sits under the SAME tenant digest dir the job
    record uses, so parsed chunks share the blob/job isolation boundary and are
    reachable only under the digest of the identity that uploaded the object.
    The job record itself is ``jobs/<job_id>.json``; the per-job subdirectory
    never collides with it, and :func:`list_jobs` (which globs ``*.json``) steps
    over the subdirectory cleanly.
    """
    return _jobs_dir(identity) / str(job_id or "").strip() / "chunks.json"


def write_chunks(identity: str, job_id: str, chunks: List[dict]) -> Path:
    """Persist a job's parsed chunks (tenant-scoped); returns the written path."""
    path = chunks_path(identity, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(chunks)), encoding="utf-8")
    return path


def read_chunks(identity: str, job_id: str) -> List[dict]:
    """Load a job's persisted chunks for ``identity``, or ``[]`` if none."""
    path = chunks_path(identity, job_id)
    if not path.is_file():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def _write(identity: str, job: ParseJob) -> ParseJob:
    jobs = _jobs_dir(identity)
    jobs.mkdir(parents=True, exist_ok=True)
    _job_path(identity, job.job_id).write_text(
        job.model_dump_json(), encoding="utf-8"
    )
    return job


def create_job(
    identity: str,
    *,
    object_id: str,
    source_name: str,
    status: ParseStatus = ParseStatus.QUEUED,
) -> ParseJob:
    """Create (and persist) a parse job, queued for the F-1b parser by default."""
    job = ParseJob(object_id=object_id, source_name=source_name, status=status)
    return _write(identity, job)


def get_job(identity: str, job_id: str) -> Optional[ParseJob]:
    """Load one parse job for ``identity``, or ``None`` if it has no such job."""
    path = _job_path(identity, str(job_id or "").strip())
    if not path.is_file():
        return None
    return ParseJob.model_validate_json(path.read_text(encoding="utf-8"))


def list_jobs(identity: str) -> List[ParseJob]:
    """Every parse job for ``identity`` — scoped to its own digest directory."""
    jobs = _jobs_dir(identity)
    if not jobs.is_dir():
        return []
    out: List[ParseJob] = []
    for path in sorted(jobs.glob("*.json")):
        try:
            out.append(ParseJob.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return out


def set_status(
    identity: str,
    job_id: str,
    status: ParseStatus,
    *,
    last_error: Optional[str] = None,
    pages: Optional[int] = None,
    sheets: Optional[int] = None,
    rows: Optional[int] = None,
    index_state: Optional[str] = None,
) -> ParseJob:
    """Advance a job's status (used by F-1b); refuses an unknown job by name."""
    job = get_job(identity, job_id)
    if job is None:
        raise ValueError(f"no parse job for this tenant: {job_id}")
    job.status = status
    job.updated_at = datetime.utcnow()
    if last_error is not None:
        job.last_error = last_error
    if pages is not None:
        job.pages = pages
    if sheets is not None:
        job.sheets = sheets
    if rows is not None:
        job.rows = rows
    if index_state is not None:
        job.index_state = index_state
    return _write(identity, job)
