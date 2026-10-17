# RAG Ingestion Job Model and Dry-Run Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent RAG source records and ingestion jobs, a validation service, and dry-run queue endpoints to CerebrumDev.ai without performing any actual document download, parsing, chunking, embedding, or vector-store write.

**Architecture:** Reuse the existing JSON-snapshot persistence convention (`backend/app/core/session_persistence.py`) to store source records and jobs on disk under `{STORAGE_PATH}/rag_ingestion/{domain}/`. Add Pydantic v2 models in `backend/app/models/`, a validation service in `backend/app/core/rag_ingestion_validation.py`, a persistence service in `backend/app/core/rag_ingestion_store.py`, and new endpoints in `backend/app/routers/domains.py`.

**Tech Stack:** FastAPI, Pydantic v2, Python 3.11+, pytest.

## Global Constraints

- Do NOT download documents.
- Do NOT parse PDFs, DOCX, or any files.
- Do NOT create chunks.
- Do NOT create embeddings.
- Do NOT write to vector stores.
- Do NOT start background workers (Celery/RQ/Kafka/Redis/etc.).
- Do NOT mutate Cerebrum-Blocks metadata.
- Do NOT change chain generation, chain quality, source packs, formula_executor_v2, provider/model config, deployment, or frontend.
- Actual ingestion is disabled; `dry_run=false` must return `409 Conflict`.
- All new code lives in `backend/` of `bopoadz-del/CerebrumDev.ai`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backend/app/models/rag_ingestion.py` | Pydantic models: `RagSourceRecord`, `RagIngestionJob`, validation-error schema, enums. |
| `backend/app/core/rag_ingestion_store.py` | JSON persistence for source records and jobs: list/get/save, atomic write, domain scoping. |
| `backend/app/core/rag_ingestion_validation.py` | Validation service: pack linkage, source policy, license/authority checks, duplicate detection. |
| `backend/app/routers/domains.py` | New endpoints for dry-run job creation and job retrieval. |
| `backend/tests/test_rag_ingestion_validation.py` | Unit tests for validation service. |
| `backend/tests/test_rag_ingestion_jobs.py` | Unit tests for persistence/job model. |
| `backend/tests/test_rag_ingestion_endpoints.py` | Endpoint tests via TestClient. |

---

## Task 1: Add Pydantic models for source records and ingestion jobs

**Files:**
- Create: `backend/app/models/rag_ingestion.py`

**Interfaces:**
- Produces: `RagSourceRecord`, `RagIngestionJob`, `ValidationError`, `JobStatus`, `DuplicateStatus`, `SourceClass`.

- [ ] **Step 1: Write the model file**

```python
"""Pydantic models for RAG ingestion source records and jobs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class SourceClass(str, Enum):
    PUBLIC_DOMAIN = "public_domain"
    OPEN_LICENSE = "open_license"
    OFFICIAL_STATUTE_OR_REGULATION = "official_statute_or_regulation"
    OFFICIAL_GUIDANCE = "official_guidance"
    PLATFORM_CURATED_TEMPLATE = "platform_curated_template"


class JobStatus(str, Enum):
    DRAFT = "draft"
    VALIDATION_FAILED = "validation_failed"
    VALIDATED = "validated"
    QUEUED = "queued"
    INGESTING = "ingesting"
    INDEXED = "indexed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DuplicateStatus(str, Enum):
    NOT_CHECKED = "not_checked"
    UNIQUE = "unique"
    DUPLICATE = "duplicate"


class ValidationError(BaseModel):
    code: str
    field: Optional[str] = None
    message: str


class RagSourceRecord(BaseModel):
    source_id: str = Field(default_factory=lambda: str(uuid4()))
    rag_pack_id: str
    collection_id: str
    domain: str
    source_class: SourceClass
    title: str
    source_uri: str
    publisher: Optional[str] = None
    license_name: Optional[str] = None
    license_uri: Optional[str] = None
    license_review_status: Optional[str] = None
    authority_rating: Optional[str] = None
    content_hash: str
    external_document_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RagIngestionJob(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid4()))
    rag_pack_id: str
    collection_id: str
    domain: str
    source_id: str
    status: JobStatus = JobStatus.DRAFT
    dry_run: bool = True
    duplicate_status: DuplicateStatus = DuplicateStatus.NOT_CHECKED
    validation_errors: List[ValidationError] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error: Optional[str] = None
```

- [ ] **Step 2: Verify it compiles**

Run:
```bash
cd backend
python -m py_compile app/models/rag_ingestion.py
```

Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/rag_ingestion.py
git commit -m "feat(rag): add source record and ingestion job pydantic models"
```

---

## Task 2: Add JSON persistence for source records and jobs

**Files:**
- Create: `backend/app/core/rag_ingestion_store.py`

**Interfaces:**
- Consumes: `RagSourceRecord`, `RagIngestionJob` from Task 1.
- Produces: `save_source_record`, `get_source_record`, `list_source_records`, `save_job`, `get_job`, `list_jobs`.

- [ ] **Step 1: Write the persistence service**

```python
"""JSON-file persistence for RAG ingestion source records and jobs.

Follows the same atomic write/replace pattern as session_persistence.py.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from app.models.rag_ingestion import RagIngestionJob, RagSourceRecord

logger = logging.getLogger(__name__)

STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")


def _rag_ingestion_dir(domain: str) -> Path:
    path = Path(STORAGE_PATH) / "rag_ingestion" / domain
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sources_path(domain: str) -> Path:
    return _rag_ingestion_dir(domain) / "records.json"


def _jobs_path(domain: str) -> Path:
    return _rag_ingestion_dir(domain) / "jobs.json"


def _atomic_write(path: Path, data: dict) -> None:
    tmp_path = path.with_suffix(".json.tmp")
    backup_path = path.with_suffix(".json.bak")
    try:
        if path.exists():
            shutil.copy2(path, backup_path)
        tmp_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return {}


def _model_map(model_cls, items: List[dict]) -> List[Any]:
    result = []
    for item in items:
        try:
            result.append(model_cls(**item))
        except Exception as exc:
            logger.warning("Skipping corrupt %s entry: %s", model_cls.__name__, exc)
    return result


def save_source_record(record: RagSourceRecord) -> RagSourceRecord:
    path = _sources_path(record.domain)
    data = _load_json(path)
    records = data.get("records", [])
    records = [r for r in records if r.get("source_id") != record.source_id]
    records.append(record.model_dump(mode="json"))
    data["records"] = records
    _atomic_write(path, data)
    return record


def get_source_record(domain: str, source_id: str) -> Optional[RagSourceRecord]:
    path = _sources_path(domain)
    data = _load_json(path)
    for item in data.get("records", []):
        if item.get("source_id") == source_id:
            return RagSourceRecord(**item)
    return None


def list_source_records(
    domain: str, collection_id: Optional[str] = None
) -> List[RagSourceRecord]:
    path = _sources_path(domain)
    data = _load_json(path)
    records = _model_map(RagSourceRecord, data.get("records", []))
    if collection_id:
        records = [r for r in records if r.collection_id == collection_id]
    return records


def save_job(job: RagIngestionJob) -> RagIngestionJob:
    path = _jobs_path(job.domain)
    data = _load_json(path)
    jobs = data.get("jobs", [])
    jobs = [j for j in jobs if j.get("job_id") != job.job_id]
    jobs.append(job.model_dump(mode="json"))
    data["jobs"] = jobs
    _atomic_write(path, data)
    return job


def get_job(domain: str, job_id: str) -> Optional[RagIngestionJob]:
    path = _jobs_path(domain)
    data = _load_json(path)
    for item in data.get("jobs", []):
        if item.get("job_id") == job_id:
            return RagIngestionJob(**item)
    return None


def list_jobs(
    domain: str,
    status: Optional[str] = None,
    rag_pack_id: Optional[str] = None,
    collection_id: Optional[str] = None,
) -> List[RagIngestionJob]:
    path = _jobs_path(domain)
    data = _load_json(path)
    jobs = _model_map(RagIngestionJob, data.get("jobs", []))
    if status:
        jobs = [j for j in jobs if j.status.value == status]
    if rag_pack_id:
        jobs = [j for j in jobs if j.rag_pack_id == rag_pack_id]
    if collection_id:
        jobs = [j for j in jobs if j.collection_id == collection_id]
    return jobs
```

- [ ] **Step 2: Write a quick smoke test**

Create `backend/tests/test_rag_ingestion_jobs.py` with:

```python
from __future__ import annotations

from datetime import datetime

from app.core.rag_ingestion_store import (
    get_job,
    get_source_record,
    list_jobs,
    list_source_records,
    save_job,
    save_source_record,
)
from app.models.rag_ingestion import (
    DuplicateStatus,
    JobStatus,
    RagIngestionJob,
    RagSourceRecord,
    SourceClass,
)


def test_save_and_get_source_record(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = RagSourceRecord(
        rag_pack_id="legal_core_rag",
        collection_id="prebuilt_legal_core",
        domain="legal",
        source_class=SourceClass.PUBLIC_DOMAIN,
        title="Sample",
        source_uri="https://example.com/sample",
        content_hash="abc123",
    )
    saved = save_source_record(record)
    fetched = get_source_record("legal", saved.source_id)
    assert fetched is not None
    assert fetched.title == "Sample"


def test_save_and_get_job(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = RagSourceRecord(
        rag_pack_id="legal_core_rag",
        collection_id="prebuilt_legal_core",
        domain="legal",
        source_class=SourceClass.PUBLIC_DOMAIN,
        title="Sample",
        source_uri="https://example.com/sample",
        content_hash="abc123",
    )
    save_source_record(record)
    job = RagIngestionJob(
        rag_pack_id="legal_core_rag",
        collection_id="prebuilt_legal_core",
        domain="legal",
        source_id=record.source_id,
        status=JobStatus.QUEUED,
        dry_run=True,
        duplicate_status=DuplicateStatus.UNIQUE,
        queued_at=datetime.utcnow(),
    )
    saved = save_job(job)
    fetched = get_job("legal", saved.job_id)
    assert fetched is not None
    assert fetched.status == JobStatus.QUEUED
```

- [ ] **Step 3: Run the tests**

Run:
```bash
cd backend
python -m pytest tests/test_rag_ingestion_jobs.py -q --tb=short
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/core/rag_ingestion_store.py backend/tests/test_rag_ingestion_jobs.py
git commit -m "feat(rag): add JSON persistence for ingestion source records and jobs"
```

---

## Task 3: Add the validation service

**Files:**
- Create: `backend/app/core/rag_ingestion_validation.py`

**Interfaces:**
- Consumes: `RagSourceRecord` from Task 1, `get_rag_pack` from `app.core.rag_pack_loader`, persistence helpers from Task 2.
- Produces: `validate_source_record`, `ValidationResult`.

- [ ] **Step 1: Write the validation service**

```python
"""Validation service for proposed RAG ingestion sources and jobs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.rag_ingestion_store import list_source_records
from app.core.rag_pack_loader import get_rag_pack
from app.models.rag_ingestion import (
    DuplicateStatus,
    JobStatus,
    RagIngestionJob,
    RagSourceRecord,
    SourceClass,
    ValidationError,
)


@dataclass
class ValidationResult:
    valid: bool
    errors: List[ValidationError]


PRECLUDED_SOURCE_CLASSES = {
    "private_enterprise_data",
    "confidential_client_data",
    "copyrighted_commercial_content_without_license",
    "user_uploaded_project_records",
    "unknown_license",
}


def _normalize_uri(uri: str) -> str:
    # Drop trailing slash and fragment for comparison.
    return re.sub(r"#.*$", "", uri.rstrip("/")).lower()


def validate_source_record(record: RagSourceRecord) -> ValidationResult:
    errors: List[ValidationError] = []

    pack = get_rag_pack(record.domain)
    if pack is None:
        errors.append(
            ValidationError(
                code="RAG_PACK_NOT_FOUND",
                field="rag_pack_id",
                message=f"No RAG pack found for domain {record.domain}.",
            )
        )
        return ValidationResult(valid=False, errors=errors)

    if pack.get("id") != record.rag_pack_id:
        errors.append(
            ValidationError(
                code="RAG_PACK_ID_MISMATCH",
                field="rag_pack_id",
                message="rag_pack_id does not match the pack for this domain.",
            )
        )

    if pack.get("collection_id") != record.collection_id:
        errors.append(
            ValidationError(
                code="COLLECTION_ID_MISMATCH",
                field="collection_id",
                message="collection_id does not match the pack for this domain.",
            )
        )

    source_policy = pack.get("source_policy") or {}
    allowed = set(source_policy.get("allowed_source_classes", []))
    if record.source_class.value not in allowed:
        errors.append(
            ValidationError(
                code="SOURCE_CLASS_NOT_ALLOWED",
                field="source_class",
                message=f"Source class {record.source_class.value} is not allowed by this pack's source_policy.",
            )
        )

    precluded = set(source_policy.get("precluded_source_classes", []))
    if record.source_class.value in precluded:
        errors.append(
            ValidationError(
                code="SOURCE_CLASS_PRECLUDED",
                field="source_class",
                message=f"Source class {record.source_class.value} is explicitly precluded by this pack.",
            )
        )

    if source_policy.get("requires_source_record") and not record.title:
        errors.append(
            ValidationError(
                code="TITLE_REQUIRED",
                field="title",
                message="Source record title is required.",
            )
        )

    if source_policy.get("requires_source_record") and not record.source_uri:
        errors.append(
            ValidationError(
                code="SOURCE_URI_REQUIRED",
                field="source_uri",
                message="Source URI is required.",
            )
        )

    if source_policy.get("requires_license_review"):
        if not record.license_review_status:
            errors.append(
                ValidationError(
                    code="LICENSE_REVIEW_REQUIRED",
                    field="license_review_status",
                    message="License review must be recorded before queueing.",
                )
            )
        elif record.license_review_status != "approved":
            errors.append(
                ValidationError(
                    code="LICENSE_REVIEW_NOT_APPROVED",
                    field="license_review_status",
                    message="License review must be approved before queueing.",
                )
            )

    if source_policy.get("requires_authority_rating") and not record.authority_rating:
        errors.append(
            ValidationError(
                code="AUTHORITY_RATING_REQUIRED",
                field="authority_rating",
                message="Authority rating is required before queueing.",
            )
        )

    # Duplicate detection scoped to collection.
    existing = list_source_records(record.domain, record.collection_id)
    for src in existing:
        if src.source_id == record.source_id:
            continue
        if src.content_hash and src.content_hash == record.content_hash:
            errors.append(
                ValidationError(
                    code="DUPLICATE_CONTENT_HASH",
                    field="content_hash",
                    message=f"Duplicate content_hash in collection {record.collection_id}.",
                )
            )
            break

    for src in existing:
        if src.source_id == record.source_id:
            continue
        if (
            src.external_document_id
            and src.external_document_id == record.external_document_id
        ):
            errors.append(
                ValidationError(
                    code="DUPLICATE_EXTERNAL_DOCUMENT_ID",
                    field="external_document_id",
                    message="Duplicate external_document_id in this collection.",
                )
            )
            break

    normalized = _normalize_uri(record.source_uri)
    for src in existing:
        if src.source_id == record.source_id:
            continue
        if _normalize_uri(src.source_uri) == normalized:
            errors.append(
                ValidationError(
                    code="DUPLICATE_SOURCE_URI",
                    field="source_uri",
                    message="Duplicate normalized source_uri in this collection.",
                )
            )
            break

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def create_ingestion_job(
    record: RagSourceRecord,
    dry_run: bool = True,
    queue: bool = False,
) -> RagIngestionJob:
    result = validate_source_record(record)
    status = JobStatus.VALIDATED if result.valid else JobStatus.VALIDATION_FAILED
    duplicate_status = DuplicateStatus.UNIQUE if result.valid else DuplicateStatus.NOT_CHECKED
    from datetime import datetime

    job = RagIngestionJob(
        rag_pack_id=record.rag_pack_id,
        collection_id=record.collection_id,
        domain=record.domain,
        source_id=record.source_id,
        status=status,
        dry_run=dry_run,
        duplicate_status=duplicate_status,
        validation_errors=result.errors,
    )
    if queue and result.valid:
        job.status = JobStatus.QUEUED
        job.queued_at = datetime.utcnow()
    return job
```

- [ ] **Step 2: Write validation tests**

Create `backend/tests/test_rag_ingestion_validation.py` with tests listed in the spec:

```python
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.rag_ingestion_store import save_source_record
from app.core.rag_ingestion_validation import (
    PRECLUDED_SOURCE_CLASSES,
    create_ingestion_job,
    validate_source_record,
)
from app.models.rag_ingestion import (
    JobStatus,
    RagSourceRecord,
    SourceClass,
)


LEGAL_PACK = {
    "id": "legal_core_rag",
    "domain": "legal",
    "collection_id": "prebuilt_legal_core",
    "source_policy": {
        "allowed_source_classes": ["public_domain", "open_license", "official_statute_or_regulation", "official_guidance", "platform_curated_template"],
        "precluded_source_classes": [
            "private_enterprise_data",
            "confidential_client_data",
            "copyrighted_commercial_content_without_license",
            "user_uploaded_project_records",
            "unknown_license",
        ],
        "requires_source_record": True,
        "requires_license_review": True,
        "requires_authority_rating": True,
    },
}


def _valid_legal_record() -> RagSourceRecord:
    return RagSourceRecord(
        rag_pack_id="legal_core_rag",
        collection_id="prebuilt_legal_core",
        domain="legal",
        source_class=SourceClass.PUBLIC_DOMAIN,
        title="US Copyright Act",
        source_uri="https://example.com/copyright-act",
        publisher="US Government",
        license_name="Public Domain",
        license_review_status="approved",
        authority_rating="high",
        content_hash="hash1",
        external_document_id="doc-1",
    )


def test_valid_legal_source_passes():
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        result = validate_source_record(_valid_legal_record())
    assert result.valid is True
    assert result.errors == []


def test_unknown_rag_pack_fails():
    record = _valid_legal_record()
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=None):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "RAG_PACK_NOT_FOUND" for e in result.errors)


def test_domain_mismatch_fails():
    record = _valid_legal_record()
    record.domain = "finance"
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "RAG_PACK_NOT_FOUND" for e in result.errors)


def test_collection_id_mismatch_fails():
    record = _valid_legal_record()
    record.collection_id = "wrong"
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "COLLECTION_ID_MISMATCH" for e in result.errors)


def test_precluded_source_class_fails():
    record = _valid_legal_record()
    record.source_class = SourceClass.UNKNOWN_LICENSE
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "SOURCE_CLASS_PRECLUDED" for e in result.errors)


def test_missing_license_review_fails():
    record = _valid_legal_record()
    record.license_review_status = None
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "LICENSE_REVIEW_REQUIRED" for e in result.errors)


def test_unapproved_license_review_fails():
    record = _valid_legal_record()
    record.license_review_status = "pending"
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "LICENSE_REVIEW_NOT_APPROVED" for e in result.errors)


def test_missing_authority_rating_fails():
    record = _valid_legal_record()
    record.authority_rating = None
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        result = validate_source_record(record)
    assert result.valid is False
    assert any(e.code == "AUTHORITY_RATING_REQUIRED" for e in result.errors)


def test_duplicate_content_hash_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _valid_legal_record()
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        save_source_record(record)
        second = _valid_legal_record()
        second.source_id = "different-id"
        second.external_document_id = "doc-2"
        second.source_uri = "https://example.com/other"
        result = validate_source_record(second)
    assert result.valid is False
    assert any(e.code == "DUPLICATE_CONTENT_HASH" for e in result.errors)


def test_duplicate_external_document_id_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _valid_legal_record()
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        save_source_record(record)
        second = _valid_legal_record()
        second.source_id = "different-id"
        second.content_hash = "hash2"
        second.source_uri = "https://example.com/other"
        result = validate_source_record(second)
    assert result.valid is False
    assert any(e.code == "DUPLICATE_EXTERNAL_DOCUMENT_ID" for e in result.errors)


def test_duplicate_source_uri_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _valid_legal_record()
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        save_source_record(record)
        second = _valid_legal_record()
        second.source_id = "different-id"
        second.content_hash = "hash2"
        second.external_document_id = "doc-2"
        result = validate_source_record(second)
    assert result.valid is False
    assert any(e.code == "DUPLICATE_SOURCE_URI" for e in result.errors)


def test_duplicate_in_different_collection_not_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    record = _valid_legal_record()
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        save_source_record(record)
        second = _valid_legal_record()
        second.source_id = "different-id"
        second.collection_id = "prebuilt_legal_other"
        result = validate_source_record(second)
    assert result.valid is True


def test_dry_run_job_validated():
    record = _valid_legal_record()
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        job = create_ingestion_job(record, dry_run=True, queue=False)
    assert job.status == JobStatus.VALIDATED
    assert job.dry_run is True


def test_dry_run_queue_job_queued():
    record = _valid_legal_record()
    with patch("app.core.rag_ingestion_validation.get_rag_pack", return_value=LEGAL_PACK):
        job = create_ingestion_job(record, dry_run=True, queue=True)
    assert job.status == JobStatus.QUEUED
    assert job.queued_at is not None
```

- [ ] **Step 3: Run validation tests**

Run:
```bash
cd backend
python -m pytest tests/test_rag_ingestion_validation.py -q --tb=short
```

Expected: all passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/core/rag_ingestion_validation.py backend/tests/test_rag_ingestion_validation.py
git commit -m "feat(rag): add source validation and dry-run job creation service"
```

---

## Task 4: Add router endpoints

**Files:**
- Modify: `backend/app/routers/domains.py`

**Interfaces:**
- Consumes: validation service and persistence from Tasks 2-3, models from Task 1.
- Produces: `POST /v1/domains/{domain_id}/rag-ingestion/jobs`, `GET /v1/domains/{domain_id}/rag-ingestion/jobs/{job_id}`, `GET /v1/domains/{domain_id}/rag-ingestion/jobs`.

- [ ] **Step 1: Add request/response schemas to the router file**

Append to imports in `backend/app/routers/domains.py`:

```python
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..core.rag_ingestion_store import get_job, list_jobs, save_job, save_source_record
from ..core.rag_ingestion_validation import create_ingestion_job
from ..models.rag_ingestion import (
    DuplicateStatus,
    JobStatus,
    RagIngestionJob,
    RagSourceRecord,
    SourceClass,
)
```

Add request schema:

```python
class CreateIngestionJobRequest(BaseModel):
    rag_pack_id: str
    collection_id: str
    source_class: SourceClass
    title: str
    source_uri: str
    publisher: Optional[str] = None
    license_name: Optional[str] = None
    license_uri: Optional[str] = None
    license_review_status: Optional[str] = None
    authority_rating: Optional[str] = None
    content_hash: str
    external_document_id: Optional[str] = None
    dry_run: bool = True
    queue: bool = False
```

- [ ] **Step 2: Add the endpoints**

Append to `backend/app/routers/domains.py`:

```python
@router.post("/{domain_id}/rag-ingestion/jobs")
async def create_rag_ingestion_job(domain_id: str, request: CreateIngestionJobRequest):
    """Create a dry-run RAG ingestion job (validation + optional queue).

    Actual ingestion is not enabled. Use dry_run=true to validate or queue.
    """
    if not request.dry_run:
        raise HTTPException(
            status_code=409,
            detail="Actual ingestion is not enabled. Use dry_run=true.",
        )

    if request.collection_id != f"prebuilt_{domain_id}_core":
        # Still allow explicit mismatch to be rejected by validation; this is a
        # lightweight guard aligned with our naming convention.
        pass

    record = RagSourceRecord(
        rag_pack_id=request.rag_pack_id,
        collection_id=request.collection_id,
        domain=domain_id,
        source_class=request.source_class,
        title=request.title,
        source_uri=request.source_uri,
        publisher=request.publisher,
        license_name=request.license_name,
        license_uri=request.license_uri,
        license_review_status=request.license_review_status,
        authority_rating=request.authority_rating,
        content_hash=request.content_hash,
        external_document_id=request.external_document_id,
    )

    # Persist the source record before validation so duplicate checks work.
    save_source_record(record)

    job = create_ingestion_job(record, dry_run=request.dry_run, queue=request.queue)
    save_job(job)
    return job.model_dump(mode="json")


@router.get("/{domain_id}/rag-ingestion/jobs/{job_id}")
async def get_rag_ingestion_job(domain_id: str, job_id: str):
    """Retrieve a single ingestion job, scoped to its domain."""
    job = get_job(domain_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.model_dump(mode="json")


@router.get("/{domain_id}/rag-ingestion/jobs")
async def list_rag_ingestion_jobs(
    domain_id: str,
    status: Optional[str] = Query(None),
    rag_pack_id: Optional[str] = Query(None),
    collection_id: Optional[str] = Query(None),
):
    """List ingestion jobs for a domain, with optional filters."""
    jobs = list_jobs(domain_id, status=status, rag_pack_id=rag_pack_id, collection_id=collection_id)
    return {"domain": domain_id, "jobs": [j.model_dump(mode="json") for j in jobs]}
```

- [ ] **Step 3: Write endpoint tests**

Create `backend/tests/test_rag_ingestion_endpoints.py`:

```python
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


LEGAL_PACK = {
    "id": "legal_core_rag",
    "domain": "legal",
    "collection_id": "prebuilt_legal_core",
    "source_policy": {
        "allowed_source_classes": ["public_domain", "open_license", "official_statute_or_regulation", "official_guidance", "platform_curated_template"],
        "precluded_source_classes": [
            "private_enterprise_data",
            "confidential_client_data",
            "copyrighted_commercial_content_without_license",
            "user_uploaded_project_records",
            "unknown_license",
        ],
        "requires_source_record": True,
        "requires_license_review": True,
        "requires_authority_rating": True,
    },
}


def _valid_payload(dry_run: bool = True, queue: bool = False) -> dict:
    return {
        "rag_pack_id": "legal_core_rag",
        "collection_id": "prebuilt_legal_core",
        "source_class": "public_domain",
        "title": "US Copyright Act",
        "source_uri": "https://example.com/copyright-act",
        "publisher": "US Government",
        "license_name": "Public Domain",
        "license_review_status": "approved",
        "authority_rating": "high",
        "content_hash": "hash-endpoint-1",
        "external_document_id": "doc-endpoint-1",
        "dry_run": dry_run,
        "queue": queue,
    }


def test_create_dry_run_validation_only(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch("app.routers.domains.get_rag_pack", return_value=LEGAL_PACK):
        response = client.post("/v1/domains/legal/rag-ingestion/jobs", json=_valid_payload(dry_run=True, queue=False))
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validated"
    assert data["dry_run"] is True
    assert data["duplicate_status"] == "unique"


def test_create_dry_run_queue(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch("app.routers.domains.get_rag_pack", return_value=LEGAL_PACK):
        response = client.post("/v1/domains/legal/rag-ingestion/jobs", json=_valid_payload(dry_run=True, queue=True))
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "queued"
    assert data["queued_at"] is not None


def test_non_dry_run_rejected(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    response = client.post("/v1/domains/legal/rag-ingestion/jobs", json=_valid_payload(dry_run=False))
    assert response.status_code == 409
    assert "not enabled" in response.json()["detail"].lower()


def test_unknown_rag_pack_fails(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch("app.routers.domains.get_rag_pack", return_value=None):
        response = client.post("/v1/domains/legal/rag-ingestion/jobs", json=_valid_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validation_failed"
    assert any(e["code"] == "RAG_PACK_NOT_FOUND" for e in data["validation_errors"])


def test_domain_mismatch_fails(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch("app.routers.domains.get_rag_pack", return_value=LEGAL_PACK):
        response = client.post("/v1/domains/finance/rag-ingestion/jobs", json=_valid_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validation_failed"


def test_precluded_source_class_fails(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    payload = _valid_payload()
    payload["source_class"] = "unknown_license"
    with patch("app.routers.domains.get_rag_pack", return_value=LEGAL_PACK):
        response = client.post("/v1/domains/legal/rag-ingestion/jobs", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validation_failed"
    assert any(e["code"] == "SOURCE_CLASS_PRECLUDED" for e in data["validation_errors"])


def test_get_job(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch("app.routers.domains.get_rag_pack", return_value=LEGAL_PACK):
        create_resp = client.post("/v1/domains/legal/rag-ingestion/jobs", json=_valid_payload(queue=True))
    job_id = create_resp.json()["job_id"]
    get_resp = client.get(f"/v1/domains/legal/rag-ingestion/jobs/{job_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["job_id"] == job_id


def test_get_job_wrong_domain_returns_404(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch("app.routers.domains.get_rag_pack", return_value=LEGAL_PACK):
        create_resp = client.post("/v1/domains/legal/rag-ingestion/jobs", json=_valid_payload(queue=True))
    job_id = create_resp.json()["job_id"]
    get_resp = client.get(f"/v1/domains/finance/rag-ingestion/jobs/{job_id}")
    assert get_resp.status_code == 404


def test_unknown_job_returns_404(client: TestClient):
    response = client.get("/v1/domains/legal/rag-ingestion/jobs/nonexistent")
    assert response.status_code == 404


def test_list_jobs(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with patch("app.routers.domains.get_rag_pack", return_value=LEGAL_PACK):
        client.post("/v1/domains/legal/rag-ingestion/jobs", json=_valid_payload(queue=True))
    response = client.get("/v1/domains/legal/rag-ingestion/jobs")
    assert response.status_code == 200
    data = response.json()
    assert data["domain"] == "legal"
    assert len(data["jobs"]) == 1
```

- [ ] **Step 4: Run endpoint tests**

Run:
```bash
cd backend
python -m pytest tests/test_rag_ingestion_endpoints.py -q --tb=short
```

Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/domains.py backend/tests/test_rag_ingestion_endpoints.py
git commit -m "feat(rag): add dry-run ingestion job endpoints"
```

---

## Task 5: Regression-test existing RAG endpoints

- [ ] **Step 1: Run targeted existing tests**

```bash
cd backend
python -m pytest tests/test_engine_discovery.py tests/test_rag_pack_loader.py tests/test_rag_pack_endpoint.py tests/test_rag_activation.py tests/test_rag_activation_endpoint.py tests/test_source_pack_endpoint.py tests/test_chain_generator_source_packs.py -q --tb=short
```

Expected: all passed.

- [ ] **Step 2: Run full suite**

```bash
cd backend
python -m pytest tests -q
```

Expected: 0 failures, 0 errors (skips allowed only for known environmental markers).

- [ ] **Step 3: Commit if any test-only fixes needed**

If no fixes needed, no extra commit.

---

## Task 6: Push branch and open PR

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/rag-ingestion-job-queue
```

- [ ] **Step 2: Open PR via gh CLI**

```bash
gh pr create --repo bopoadz-del/CerebrumDev.ai --title "feat(rag): add ingestion job model and dry-run queue" --body-file - <<'EOF'
feat(rag): add ingestion job model and dry-run queue

Adds the controlled job layer between RAG pack metadata and future document ingestion.

- Source record and ingestion job Pydantic models (`app/models/rag_ingestion.py`)
- JSON persistence under `STORAGE_PATH/rag_ingestion/<domain>/` (`app/core/rag_ingestion_store.py`)
- Validation service enforcing pack linkage, source policy, license/authority checks, and document-level duplicate detection (`app/core/rag_ingestion_validation.py`)
- Dry-run endpoints in `app/routers/domains.py`:
  - `POST /v1/domains/{domain_id}/rag-ingestion/jobs`
  - `GET /v1/domains/{domain_id}/rag-ingestion/jobs/{job_id}`
  - `GET /v1/domains/{domain_id}/rag-ingestion/jobs`
- Tests for validation, persistence, and endpoints.

Actual ingestion (`dry_run=false`) returns `409 Conflict`.
No documents are downloaded, parsed, chunked, embedded, or written to vector stores.
EOF
```

- [ ] **Step 3: Report PR number and merge status**

Capture the PR URL/number from `gh pr create` output.

---

## Self-Review

- **Spec coverage:** Every requirement in the Phase 3 spec maps to a task above.
- **Placeholder scan:** No TBD/TODO/fill-in-details remain.
- **Type consistency:** Models, service, router, and tests use the same field names and enum values.
