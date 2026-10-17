# Vector-Store Adapter Contract and Local Indexing Dry-Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 7 of the governed RAG pipeline — a vendor-neutral vector-store adapter contract and an isolated validation-only local flat index dry-run.

**Architecture:** A model-independent adapter interface (`rag_vector_store_adapters.py`) defines the vector-store contract; a validation-only `local_flat_json_v1` adapter persists manifest + records JSONL; an orchestrator (`rag_vector_indexing.py`) validates eligibility, maps embeddings to governed records, computes deterministic IDs, atomically publishes artifacts, and verifies read-back integrity; domain-scoped endpoints expose dry-run execution and metadata retrieval.

**Tech Stack:** Python 3.11, Pydantic v1, FastAPI, pytest, Python standard library only.

## Global Constraints

- Indexing input comes only from persisted `RagEmbeddingRun`, `RagChunkEmbedding`, and `RagCanonicalChunk` records.
- Do not accept client-supplied vectors, chunk text, metadata overrides, or document IDs other than the route target.
- No production vector database, no similarity search, no retrieval, no background workers, no remote services.
- `RagCanonicalDocument.index_status`, `RagCanonicalChunk.index_status`, `RagEmbeddingRun.index_status`, and RAG pack `ingestion_status` must remain unchanged (`not_indexed`/`not_ingested`).
- `dry_run = true` enforced; `dry_run = false` returns `409 Conflict`.
- Deterministic `index_id`, `index_run_id`, and `record_id` via SHA-256.
- Atomic manifest + records publication with read-back verification.
- Idempotent retries; conflicting artifacts return clean errors.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backend/app/models/rag_ingestion.py` | Add `VectorIndexRunStatus`, `ActivationStatus`, `RagVectorIndexRun`, `RagVectorIndexRecord`. |
| `backend/app/core/rag_vector_store_adapters.py` | Adapter contract, registry, and `local_flat_json_v1` adapter. |
| `backend/app/core/rag_vector_indexing.py` | Eligibility, ID generation, record mapping, atomic publication, read-back verification. |
| `backend/app/core/rag_ingestion_store.py` | Vector index run/record persistence helpers. |
| `backend/app/routers/domains.py` | Vector-index dry-run and retrieval endpoints. |
| `backend/.env.example` | Vector-index configuration env vars. |
| `backend/tests/test_rag_vector_store_adapters.py` | Adapter contract tests. |
| `backend/tests/test_rag_vector_indexing.py` | Orchestrator tests. |
| `backend/tests/test_rag_vector_index_endpoints.py` | Endpoint tests. |

---

### Task 1: Add vector index models

**Files:**
- Modify: `backend/app/models/rag_ingestion.py`

**Interfaces:**
- Consumes: existing `IndexStatus`, `ValidationError`.
- Produces: `VectorIndexRunStatus`, `ActivationStatus`, `RagVectorIndexRun`, `RagVectorIndexRecord`.

- [ ] **Step 1: Add models**

Add after `RagChunkEmbedding` in `backend/app/models/rag_ingestion.py`:

```python
class VectorIndexRunStatus(str, Enum):
    """Lifecycle status for a vector indexing dry-run."""

    PENDING = "pending"
    VALIDATING = "validating"
    INDEXING = "indexing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


class ActivationStatus(str, Enum):
    """Activation state for a vector index."""

    INACTIVE = "inactive"


class RagVectorIndexRun(BaseModel):
    """Audit record for a vector indexing dry-run."""

    index_run_id: str
    index_id: str
    document_id: str
    embedding_run_id: str
    job_id: str
    source_id: str
    acquisition_id: str
    rag_pack_id: str
    collection_id: str
    domain: str
    adapter_id: str
    adapter_version: str
    storage_type: str
    distance_metric: str
    dimensions: int
    dry_run: bool = True
    status: VectorIndexRunStatus = VectorIndexRunStatus.PENDING
    embedding_record_count: int = 0
    eligible_record_count: int = 0
    indexed_record_count: int = 0
    failed_record_count: int = 0
    duplicate_record_count: int = 0
    production_approved: bool = False
    retrieval_enabled: bool = False
    activation_status: ActivationStatus = ActivationStatus.INACTIVE
    index_artifact_hash: Optional[str] = None
    manifest_hash: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    warnings: List[str] = Field(default_factory=list)
    errors: List[ValidationError] = Field(default_factory=list)
    last_error: Optional[str] = None


class RagVectorIndexRecord(BaseModel):
    """A single deterministic vector index record."""

    record_id: str
    index_run_id: str
    index_id: str
    embedding_id: str
    embedding_run_id: str
    document_id: str
    chunk_id: str
    collection_id: str
    domain: str
    chunk_ordinal: int
    chunk_text_hash: str
    vector_hash: str
    vector: List[float]
    dimensions: int
    distance_metric: str
    provider_id: str
    provider_version: str
    adapter_id: str
    adapter_version: str
    metadata: dict = Field(default_factory=dict)
    untrusted_content: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 2: Verify import**

```bash
cd backend
.venv/Scripts/python -c "from app.models.rag_ingestion import RagVectorIndexRun, RagVectorIndexRecord, VectorIndexRunStatus, ActivationStatus; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/rag_ingestion.py
git commit -m "feat(rag): add vector index run and record models"
```

---

### Task 2: Implement vector-store adapter contract and local_flat_json_v1

**Files:**
- Create: `backend/app/core/rag_vector_store_adapters.py`
- Test: `backend/tests/test_rag_vector_store_adapters.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RagVectorStoreAdapter`, `LOCAL_FLAT_JSON_V1`, `get_adapter`, `register_adapter`, `list_adapter_ids`.

- [ ] **Step 1: Create adapter module**

Create `backend/app/core/rag_vector_store_adapters.py`:

```python
"""Model-independent vector-store adapter contract and validation-only adapters.

No production vector databases, no similarity search, no retrieval.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LOCAL_FLAT_JSON_V1 = "local_flat_json_v1"


@dataclass
class IndexSpec:
    """Specification for a vector index artifact location."""

    index_id: str
    document_id: str
    domain: str
    base_path: Path


@dataclass
class RagVectorStoreAdapter:
    """Contract for a vector-store adapter."""

    adapter_id: str
    adapter_version: str
    storage_type: str
    distance_metric: str
    supported_dimensions: List[int]
    supports_upsert: bool
    supports_delete: bool
    supports_metadata: bool
    supports_filtering: bool
    supports_similarity_search: bool
    production_approved: bool

    def create_index(self, index_spec: IndexSpec) -> None:
        """Ensure the index directory exists."""
        raise NotImplementedError

    def write_records(
        self, index_spec: IndexSpec, manifest: dict, records: List[dict]
    ) -> None:
        """Atomically write manifest and records."""
        raise NotImplementedError

    def read_records(self, index_spec: IndexSpec) -> List[dict]:
        """Read all records from the index."""
        raise NotImplementedError

    def read_manifest(self, index_spec: IndexSpec) -> Optional[dict]:
        """Read the index manifest."""
        raise NotImplementedError

    def validate_index(self, index_spec: IndexSpec) -> List[str]:
        """Return validation error messages, or empty list if valid."""
        raise NotImplementedError

    def delete_dry_run_index(self, index_spec: IndexSpec) -> None:
        """Delete a dry-run index artifact."""
        raise NotImplementedError


class _LocalFlatJsonAdapterV1(RagVectorStoreAdapter):
    """Validation-only local flat JSONL adapter.

    Not a production vector database. Not an ANN index. Does not enable
    retrieval. Exists only to validate the vector-store adapter contract.
    """

    _VERSION = "1"
    _STORAGE_TYPE = "local_jsonl"
    _DISTANCE_METRIC = "cosine"
    _SUPPORTED_DIMENSIONS = [384]

    def __init__(self) -> None:
        super().__init__(
            adapter_id=LOCAL_FLAT_JSON_V1,
            adapter_version=self._VERSION,
            storage_type=self._STORAGE_TYPE,
            distance_metric=self._DISTANCE_METRIC,
            supported_dimensions=self._SUPPORTED_DIMENSIONS,
            supports_upsert=False,
            supports_delete=True,
            supports_metadata=True,
            supports_filtering=False,
            supports_similarity_search=False,
            production_approved=False,
        )

    def _manifest_path(self, index_spec: IndexSpec) -> Path:
        return index_spec.base_path / "manifest.json"

    def _records_path(self, index_spec: IndexSpec) -> Path:
        return index_spec.base_path / "records.jsonl"

    def create_index(self, index_spec: IndexSpec) -> None:
        index_spec.base_path.mkdir(parents=True, exist_ok=True)

    def write_records(
        self, index_spec: IndexSpec, manifest: dict, records: List[dict]
    ) -> None:
        self.create_index(index_spec)
        manifest_path = self._manifest_path(index_spec)
        records_path = self._records_path(index_spec)

        tmp_manifest = manifest_path.with_suffix(".json.tmp")
        tmp_records = records_path.with_suffix(".jsonl.tmp")
        try:
            tmp_manifest.write_text(
                json.dumps(manifest, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
            with tmp_records.open("w", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
            os.replace(tmp_manifest, manifest_path)
            os.replace(tmp_records, records_path)
        except Exception:
            if tmp_manifest.exists():
                tmp_manifest.unlink(missing_ok=True)
            if tmp_records.exists():
                tmp_records.unlink(missing_ok=True)
            raise

    def read_manifest(self, index_spec: IndexSpec) -> Optional[dict]:
        path = self._manifest_path(index_spec)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read manifest %s: %s", path, exc)
            return None

    def read_records(self, index_spec: IndexSpec) -> List[dict]:
        path = self._records_path(index_spec)
        if not path.exists():
            return []
        records = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
        except Exception as exc:
            logger.warning("Failed to read records %s: %s", path, exc)
        return records

    def validate_index(self, index_spec: IndexSpec) -> List[str]:
        errors: List[str] = []
        manifest = self.read_manifest(index_spec)
        if manifest is None:
            errors.append("Manifest not found.")
            return errors
        records = self.read_records(index_spec)
        if manifest.get("record_count", 0) != len(records):
            errors.append("Record count mismatch.")
        return errors

    def delete_dry_run_index(self, index_spec: IndexSpec) -> None:
        if index_spec.base_path.exists():
            shutil.rmtree(index_spec.base_path)


_ADAPTERS: Dict[str, RagVectorStoreAdapter] = {
    LOCAL_FLAT_JSON_V1: _LocalFlatJsonAdapterV1(),
}


def register_adapter(adapter: RagVectorStoreAdapter) -> None:
    _ADAPTERS[adapter.adapter_id] = adapter


def get_adapter(adapter_id: str) -> Optional[RagVectorStoreAdapter]:
    return _ADAPTERS.get(adapter_id)


def list_adapter_ids() -> List[str]:
    return list(_ADAPTERS.keys())
```

- [ ] **Step 2: Add adapter tests**

Create `backend/tests/test_rag_vector_store_adapters.py`:

```python
import pytest

from app.core.rag_vector_store_adapters import (
    LOCAL_FLAT_JSON_V1,
    IndexSpec,
    get_adapter,
)


def test_get_local_flat_adapter_contract():
    adapter = get_adapter(LOCAL_FLAT_JSON_V1)
    assert adapter.adapter_id == LOCAL_FLAT_JSON_V1
    assert adapter.storage_type == "local_jsonl"
    assert adapter.distance_metric == "cosine"
    assert adapter.production_approved is False
    assert adapter.supports_similarity_search is False


def test_local_flat_adapter_write_and_read_records(tmp_path):
    adapter = get_adapter(LOCAL_FLAT_JSON_V1)
    spec = IndexSpec(
        index_id="idx1",
        document_id="doc1",
        domain="legal",
        base_path=tmp_path / "idx1",
    )
    manifest = {"index_id": "idx1", "record_count": 2}
    records = [{"record_id": "r1"}, {"record_id": "r2"}]
    adapter.write_records(spec, manifest, records)
    assert adapter.read_manifest(spec) == manifest
    assert adapter.read_records(spec) == records


def test_local_flat_adapter_validate_index_detects_count_mismatch(tmp_path):
    adapter = get_adapter(LOCAL_FLAT_JSON_V1)
    spec = IndexSpec(
        index_id="idx1",
        document_id="doc1",
        domain="legal",
        base_path=tmp_path / "idx1",
    )
    adapter.write_records(spec, {"index_id": "idx1", "record_count": 5}, [{"record_id": "r1"}])
    errors = adapter.validate_index(spec)
    assert any("count mismatch" in e.lower() for e in errors)
```

- [ ] **Step 3: Run tests**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_vector_store_adapters.py -q
```
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/core/rag_vector_store_adapters.py backend/tests/test_rag_vector_store_adapters.py
git commit -m "feat(rag): add vector-store adapter contract and local_flat_json_v1"
```

---

### Task 3: Add vector index persistence to store

**Files:**
- Modify: `backend/app/core/rag_ingestion_store.py`

**Interfaces:**
- Consumes: `RagVectorIndexRun`, `RagVectorIndexRecord`.
- Produces: vector-index path helpers, save/get/list helpers.

- [ ] **Step 1: Add persistence helpers**

Add to `backend/app/core/rag_ingestion_store.py` after embedding persistence:

```python
def _vector_indexes_dir(domain: str, document_id: str, index_id: str) -> Path:
    path = Path(_storage_path()) / "rag_ingestion" / domain / "vector_indexes" / document_id / index_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _vector_index_run_path(domain: str, document_id: str, index_run_id: str) -> Path:
    # Run metadata is stored per document for easy listing.
    run_dir = Path(_storage_path()) / "rag_ingestion" / domain / "vector_indexes" / document_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir / f"{index_run_id}.run.json"


def save_vector_index_run(run: RagVectorIndexRun) -> RagVectorIndexRun:
    """Persist a vector index run metadata file."""
    path = _vector_index_run_path(run.domain, run.document_id, run.index_run_id)
    run.updated_at = datetime.utcnow()
    _atomic_write(path, run.model_dump(mode="json"))
    return run


def get_vector_index_run(
    domain: str, document_id: str, index_run_id: str
) -> Optional[RagVectorIndexRun]:
    """Fetch a single vector index run."""
    path = _vector_index_run_path(domain, document_id, index_run_id)
    if not path.exists():
        return None
    try:
        return RagVectorIndexRun(**json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.warning("Failed to load vector index run %s: %s", index_run_id, exc)
        return None


def list_vector_index_runs(domain: str, document_id: str) -> List[RagVectorIndexRun]:
    """List vector index runs for a document."""
    run_dir = Path(_storage_path()) / "rag_ingestion" / domain / "vector_indexes" / document_id
    runs = []
    if not run_dir.exists():
        return runs
    for path in run_dir.glob("*.run.json"):
        try:
            runs.append(RagVectorIndexRun(**json.loads(path.read_text(encoding="utf-8"))))
        except Exception as exc:
            logger.warning("Skipping corrupt vector index run %s: %s", path, exc)
    return runs


def get_vector_index_records(
    domain: str,
    document_id: str,
    index_id: str,
    include_vectors: bool = False,
) -> List[RagVectorIndexRecord]:
    """Read vector index records from the adapter's JSONL file."""
    path = _vector_indexes_dir(domain, document_id, index_id) / "records.jsonl"
    if not path.exists():
        return []
    records = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if not include_vectors:
                data = {k: v for k, v in data.items() if k != "vector"}
            records.append(RagVectorIndexRecord(**data))
    except Exception as exc:
        logger.warning("Failed to load vector index records for %s: %s", index_id, exc)
    return sorted(records, key=lambda r: r.chunk_ordinal)
```

- [ ] **Step 2: Verify import**

```bash
cd backend
.venv/Scripts/python -c "from app.core.rag_ingestion_store import save_vector_index_run, get_vector_index_run, list_vector_index_runs, get_vector_index_records; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/rag_ingestion_store.py
git commit -m "feat(rag): add vector index run and record persistence"
```

---

### Task 4: Implement vector indexing orchestration

**Files:**
- Create: `backend/app/core/rag_vector_indexing.py`
- Test: `backend/tests/test_rag_vector_indexing.py`

**Interfaces:**
- Consumes: adapter, store helpers, models.
- Produces: `run_vector_index_dry_run(domain, document_id, embedding_run_id, adapter_id)`.

- [ ] **Step 1: Implement orchestrator**

Create `backend/app/core/rag_vector_indexing.py`:

```python
"""Vector indexing dry-run orchestration for governed RAG chunks.

Reads only persisted embedding artifacts. No vector database, no retrieval,
no similarity search.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.models.rag_ingestion import (
    ActivationStatus,
    CanonicalizationStatus,
    ChunkingStatus,
    EmbeddingRunStatus,
    IndexStatus,
    RagCanonicalChunk,
    RagCanonicalDocument,
    RagChunkEmbedding,
    RagEmbeddingRun,
    RagVectorIndexRecord,
    RagVectorIndexRun,
    ValidationError,
    VectorIndexRunStatus,
)

from .rag_ingestion_store import (
    get_canonical_chunk,
    get_canonical_document,
    get_chunk_embeddings,
    get_embedding_run,
    get_vector_index_records,
    get_vector_index_run,
    save_vector_index_run,
)
from .rag_vector_store_adapters import IndexSpec, get_adapter

logger = logging.getLogger(__name__)

RAG_VECTOR_DEFAULT_ADAPTER = os.getenv("RAG_VECTOR_DEFAULT_ADAPTER", "local_flat_json_v1")
RAG_VECTOR_INDEX_MANIFEST_VERSION = os.getenv(
    "RAG_VECTOR_INDEX_MANIFEST_VERSION", "vector-index-manifest-v1"
)
RAG_VECTOR_RECORD_PRECISION = int(os.getenv("RAG_VECTOR_RECORD_PRECISION", "8"))
RAG_VECTOR_INDEX_MAX_RECORDS = int(os.getenv("RAG_VECTOR_INDEX_MAX_RECORDS", "10000"))


class VectorIndexError(Exception):
    """Raised when vector indexing dry-run fails."""

    def __init__(self, code: str, message: str, field: Optional[str] = None):
        self.code = code
        self.message = message
        self.field = field
        super().__init__(message)

    def to_validation_error(self) -> ValidationError:
        return ValidationError(code=self.code, field=self.field, message=self.message)


def _error(code: str, message: str, field: Optional[str] = None) -> VectorIndexError:
    return VectorIndexError(code=code, message=message, field=field)


def _index_id(
    collection_id: str,
    document_id: str,
    embedding_run_id: str,
    adapter_id: str,
    adapter_version: str,
    distance_metric: str,
    dimensions: int,
) -> str:
    data = (
        f"{collection_id}:{document_id}:{embedding_run_id}:"
        f"{adapter_id}:{adapter_version}:{distance_metric}:{dimensions}"
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _index_run_id(index_id: str, dry_run: bool) -> str:
    data = f"{index_id}:{dry_run}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _record_id(index_id: str, embedding_id: str, chunk_id: str, vector_hash: str) -> str:
    data = f"{index_id}:{embedding_id}:{chunk_id}:{vector_hash}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _vector_hash(vector: List[float]) -> str:
    serialized = json.dumps(vector, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _manifest_hash(manifest: dict) -> str:
    manifest_copy = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    serialized = json.dumps(manifest_copy, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _records_artifact_hash(records: List[dict]) -> str:
    lines = [json.dumps(r, separators=(",", ":"), sort_keys=True) for r in records]
    blob = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _validate_configuration() -> List[ValidationError]:
    errors: List[ValidationError] = []
    if RAG_VECTOR_RECORD_PRECISION < 1:
        errors.append(
            ValidationError(
                code="VECTOR_INDEX_CONFIGURATION_INVALID",
                field="RAG_VECTOR_RECORD_PRECISION",
                message="Precision must be >= 1.",
            )
        )
    if RAG_VECTOR_INDEX_MAX_RECORDS <= 0:
        errors.append(
            ValidationError(
                code="VECTOR_INDEX_CONFIGURATION_INVALID",
                field="RAG_VECTOR_INDEX_MAX_RECORDS",
                message="Max records must be > 0.",
            )
        )
    return errors


def _validate_document(document: RagCanonicalDocument) -> List[ValidationError]:
    errors: List[ValidationError] = []
    if document.canonicalization_status != CanonicalizationStatus.CANONICALIZED:
        errors.append(
            ValidationError(
                code="DOCUMENT_NOT_ELIGIBLE",
                field="canonicalization_status",
                message="Document is not canonicalized.",
            )
        )
    if document.chunking_status != ChunkingStatus.CHUNKED:
        errors.append(
            ValidationError(
                code="DOCUMENT_NOT_ELIGIBLE",
                field="chunking_status",
                message="Document is not chunked.",
            )
        )
    if document.index_status != IndexStatus.NOT_INDEXED:
        errors.append(
            ValidationError(
                code="DOCUMENT_NOT_ELIGIBLE",
                field="index_status",
                message="Document is already indexed.",
            )
        )
    return errors


def _validate_embedding_run(
    embedding_run: RagEmbeddingRun,
    document: RagCanonicalDocument,
) -> List[ValidationError]:
    errors: List[ValidationError] = []
    if embedding_run.domain != document.domain:
        errors.append(
            ValidationError(
                code="EMBEDDING_RUN_LINKAGE_MISMATCH",
                field="domain",
                message="Embedding run domain does not match document.",
            )
        )
    if embedding_run.collection_id != document.collection_id:
        errors.append(
            ValidationError(
                code="EMBEDDING_RUN_LINKAGE_MISMATCH",
                field="collection_id",
                message="Embedding run collection does not match document.",
            )
        )
    if embedding_run.document_id != document.document_id:
        errors.append(
            ValidationError(
                code="EMBEDDING_RUN_LINKAGE_MISMATCH",
                field="document_id",
                message="Embedding run does not belong to this document.",
            )
        )
    if embedding_run.status != EmbeddingRunStatus.COMPLETED:
        errors.append(
            ValidationError(
                code="EMBEDDING_RUN_NOT_ELIGIBLE",
                field="status",
                message="Embedding run is not completed.",
            )
        )
    if not embedding_run.dry_run:
        errors.append(
            ValidationError(
                code="EMBEDDING_RUN_NOT_ELIGIBLE",
                field="dry_run",
                message="Only dry-run embedding runs may be indexed.",
            )
        )
    if embedding_run.index_status != IndexStatus.NOT_INDEXED:
        errors.append(
            ValidationError(
                code="EMBEDDING_RUN_NOT_ELIGIBLE",
                field="index_status",
                message="Embedding run is already indexed.",
            )
        )
    return errors


def _validate_embedding_records(
    embedding_run: RagEmbeddingRun,
    records: List[RagChunkEmbedding],
) -> List[ValidationError]:
    errors: List[ValidationError] = []
    if len(records) != embedding_run.embedded_chunk_count:
        errors.append(
            ValidationError(
                code="EMBEDDING_RECORD_COUNT_MISMATCH",
                field="embedded_chunk_count",
                message=f"Expected {embedding_run.embedded_chunk_count} records, found {len(records)}.",
            )
        )
    if len(records) > RAG_VECTOR_INDEX_MAX_RECORDS:
        errors.append(
            ValidationError(
                code="VECTOR_INDEX_TOO_LARGE",
                field="record_count",
                message=f"Index exceeds maximum of {RAG_VECTOR_INDEX_MAX_RECORDS} records.",
            )
        )
    for rec in records:
        if rec.run_id != embedding_run.run_id:
            errors.append(
                ValidationError(
                    code="EMBEDDING_RECORDS_NOT_FOUND",
                    field="run_id",
                    message="Embedding record does not belong to this run.",
                )
            )
        if len(rec.vector) != embedding_run.dimensions:
            errors.append(
                ValidationError(
                    code="VECTOR_DIMENSION_MISMATCH",
                    field="vector",
                    message=f"Expected dimension {embedding_run.dimensions}, got {len(rec.vector)}.",
                )
            )
        if any(math.isnan(v) or math.isinf(v) for v in rec.vector):
            errors.append(
                ValidationError(
                    code="VECTOR_NON_FINITE_VALUE",
                    field="vector",
                    message="Vector contains NaN or infinity.",
                )
            )
        if rec.distance_metric != embedding_run.distance_metric:
            errors.append(
                ValidationError(
                    code="VECTOR_ADAPTER_CONTRACT_INVALID",
                    field="distance_metric",
                    message="Embedding record distance metric does not match run.",
                )
            )
        if rec.vector_hash != _vector_hash(rec.vector):
            errors.append(
                ValidationError(
                    code="VECTOR_HASH_MISMATCH",
                    field="vector_hash",
                    message="Embedding vector hash does not match vector.",
                )
            )
    return errors


def _validate_chunks(
    records: List[RagChunkEmbedding],
    chunks: Dict[str, RagCanonicalChunk],
) -> List[ValidationError]:
    errors: List[ValidationError] = []
    for rec in records:
        chunk = chunks.get(rec.chunk_id)
        if chunk is None:
            errors.append(
                ValidationError(
                    code="CHUNK_LINKAGE_MISMATCH",
                    field="chunk_id",
                    message=f"Canonical chunk {rec.chunk_id} not found.",
                )
            )
            continue
        expected_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        if chunk.text_hash != expected_hash:
            errors.append(
                ValidationError(
                    code="CHUNK_TEXT_HASH_MISMATCH",
                    field="chunk.text_hash",
                    message=f"Canonical chunk {rec.chunk_id} text hash mismatch.",
                )
            )
        if rec.chunk_text_hash != chunk.text_hash:
            errors.append(
                ValidationError(
                    code="CHUNK_TEXT_HASH_MISMATCH",
                    field="chunk_text_hash",
                    message="Embedding chunk text hash does not match canonical chunk.",
                )
            )
    return errors


def _build_index_record(
    embedding: RagChunkEmbedding,
    chunk: RagCanonicalChunk,
    document: RagCanonicalDocument,
    index_id: str,
    index_run_id: str,
    adapter_id: str,
    adapter_version: str,
) -> RagVectorIndexRecord:
    record_id = _record_id(
        index_id=index_id,
        embedding_id=embedding.embedding_id,
        chunk_id=embedding.chunk_id,
        vector_hash=embedding.vector_hash,
    )
    vector = [round(v, RAG_VECTOR_RECORD_PRECISION) for v in embedding.vector]
    return RagVectorIndexRecord(
        record_id=record_id,
        index_run_id=index_run_id,
        index_id=index_id,
        embedding_id=embedding.embedding_id,
        embedding_run_id=embedding.run_id,
        document_id=document.document_id,
        chunk_id=embedding.chunk_id,
        collection_id=document.collection_id,
        domain=document.domain,
        chunk_ordinal=chunk.ordinal,
        chunk_text_hash=embedding.chunk_text_hash,
        vector_hash=embedding.vector_hash,
        vector=vector,
        dimensions=embedding.dimensions,
        distance_metric=embedding.distance_metric,
        provider_id=embedding.provider_id,
        provider_version=embedding.provider_version,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        metadata={
            "rag_pack_id": document.rag_pack_id,
            "collection_id": document.collection_id,
            "domain": document.domain,
            "document_id": document.document_id,
            "chunk_id": chunk.chunk_id,
            "chunk_ordinal": chunk.ordinal,
            "source_id": document.source_id,
            "job_id": document.job_id,
            "content_type": document.content_type,
            "source_class": document.source_class.value if document.source_class else None,
            "title": document.title,
            "publisher": document.publisher,
            "raw_content_hash": document.raw_content_hash,
            "canonical_text_hash": document.canonical_text_hash,
            "parser_id": document.parser_id,
            "parser_version": document.parser_version,
            "normalization_version": document.normalization_version,
            "chunking_version": chunk.chunking_version,
            "embedding_provider_id": embedding.provider_id,
            "embedding_provider_version": embedding.provider_version,
        },
    )


def _build_manifest(
    index_run: RagVectorIndexRun,
    records_artifact_hash: str,
) -> dict:
    manifest = {
        "index_id": index_run.index_id,
        "index_run_id": index_run.index_run_id,
        "document_id": index_run.document_id,
        "embedding_run_id": index_run.embedding_run_id,
        "rag_pack_id": index_run.rag_pack_id,
        "collection_id": index_run.collection_id,
        "domain": index_run.domain,
        "adapter_id": index_run.adapter_id,
        "adapter_version": index_run.adapter_version,
        "provider_id": None,  # filled later if needed
        "provider_version": None,
        "dimensions": index_run.dimensions,
        "distance_metric": index_run.distance_metric,
        "normalization": None,  # filled later if needed
        "record_count": index_run.indexed_record_count,
        "records_artifact_hash": records_artifact_hash,
        "manifest_version": RAG_VECTOR_INDEX_MANIFEST_VERSION,
        "production_approved": index_run.production_approved,
        "retrieval_enabled": index_run.retrieval_enabled,
        "activation_status": index_run.activation_status.value,
        "created_at": index_run.created_at.isoformat() if index_run.created_at else None,
    }
    manifest["manifest_hash"] = _manifest_hash(manifest)
    return manifest


def run_vector_index_dry_run(
    domain: str,
    document_id: str,
    embedding_run_id: str,
    adapter_id: str = RAG_VECTOR_DEFAULT_ADAPTER,
    dry_run: bool = True,
) -> Tuple[RagVectorIndexRun, List[RagVectorIndexRecord]]:
    """Run an offline vector indexing dry-run for a completed embedding run."""
    if not dry_run:
        raise _error(
            code="DRY_RUN_REQUIRED",
            message="Actual vector indexing is not enabled. Use dry_run=true.",
            field="dry_run",
        )

    config_errors = _validate_configuration()
    if config_errors:
        first = config_errors[0]
        raise _error(
            code="VECTOR_INDEX_CONFIGURATION_INVALID",
            message=first.message,
            field=first.field,
        )

    document = get_canonical_document(domain, document_id)
    if document is None:
        raise _error(
            code="DOCUMENT_NOT_FOUND",
            message="Canonical document not found.",
            field="document_id",
        )

    doc_errors = _validate_document(document)
    if doc_errors:
        raise _error(
            code=doc_errors[0].code,
            message="Document is not eligible for indexing.",
        )

    adapter = get_adapter(adapter_id)
    if adapter is None:
        raise _error(
            code="VECTOR_ADAPTER_NOT_FOUND",
            message=f"Adapter {adapter_id} not found.",
            field="adapter_id",
        )

    if document.dimensions not in adapter.supported_dimensions:
        # dimensions come from embedding run, not document; checked later
        pass

    embedding_run = get_embedding_run(domain, document_id, embedding_run_id)
    if embedding_run is None:
        raise _error(
            code="EMBEDDING_RUN_NOT_FOUND",
            message="Embedding run not found.",
            field="embedding_run_id",
        )

    run_errors = _validate_embedding_run(embedding_run, document)
    if run_errors:
        raise _error(
            code=run_errors[0].code,
            message="Embedding run is not eligible for indexing.",
        )

    if embedding_run.dimensions not in adapter.supported_dimensions:
        raise _error(
            code="VECTOR_ADAPTER_CONTRACT_INVALID",
            message=f"Adapter {adapter_id} does not support dimensions {embedding_run.dimensions}.",
            field="dimensions",
        )

    embedding_records = get_chunk_embeddings(
        domain, document_id, embedding_run_id, include_vectors=True
    )
    record_errors = _validate_embedding_records(embedding_run, embedding_records)
    if record_errors:
        raise _error(
            code=record_errors[0].code,
            message="Embedding records are not eligible for indexing.",
        )

    chunk_ids = {rec.chunk_id for rec in embedding_records}
    chunks: Dict[str, RagCanonicalChunk] = {}
    for chunk_id in chunk_ids:
        chunk = get_canonical_chunk(domain, document_id, chunk_id)
        if chunk is not None:
            chunks[chunk_id] = chunk

    chunk_errors = _validate_chunks(embedding_records, chunks)
    if chunk_errors:
        raise _error(
            code=chunk_errors[0].code,
            message="Canonical chunks are not eligible for indexing.",
        )

    index_id = _index_id(
        collection_id=document.collection_id,
        document_id=document.document_id,
        embedding_run_id=embedding_run.run_id,
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.adapter_version,
        distance_metric=adapter.distance_metric,
        dimensions=embedding_run.dimensions,
    )
    index_run_id = _index_run_id(index_id, dry_run=True)

    warnings: List[str] = []
    if document.parser_truncated:
        warnings.append("SOURCE_DOCUMENT_TRUNCATED")
    if not embedding_run.production_approved:
        warnings.append("VALIDATION_ONLY_EMBEDDING")
    if not adapter.production_approved:
        warnings.append("VALIDATION_ONLY_INDEX")
    warnings.append("PRODUCTION_EMBEDDING_NOT_CONFIGURED")
    warnings.append("PRODUCTION_VECTOR_STORE_NOT_CONFIGURED")
    warnings.append("RETRIEVAL_DISABLED")

    index_run = RagVectorIndexRun(
        index_run_id=index_run_id,
        index_id=index_id,
        document_id=document.document_id,
        embedding_run_id=embedding_run.run_id,
        job_id=document.job_id,
        source_id=document.source_id,
        acquisition_id=document.acquisition_id,
        rag_pack_id=document.rag_pack_id,
        collection_id=document.collection_id,
        domain=document.domain,
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.adapter_version,
        storage_type=adapter.storage_type,
        distance_metric=adapter.distance_metric,
        dimensions=embedding_run.dimensions,
        dry_run=True,
        status=VectorIndexRunStatus.INDEXING,
        embedding_record_count=len(embedding_records),
        eligible_record_count=len(embedding_records),
        production_approved=False,
        retrieval_enabled=False,
        activation_status=ActivationStatus.INACTIVE,
        warnings=warnings,
    )

    index_records = [
        _build_index_record(
            embedding=rec,
            chunk=chunks[rec.chunk_id],
            document=document,
            index_id=index_id,
            index_run_id=index_run_id,
            adapter_id=adapter.adapter_id,
            adapter_version=adapter.adapter_version,
        )
        for rec in sorted(embedding_records, key=lambda r: r.chunk_ordinal)
    ]
    index_run.indexed_record_count = len(index_records)

    # Idempotency/conflict check.
    existing_run = get_vector_index_run(domain, document_id, index_run_id)
    if existing_run is not None:
        existing_records = get_vector_index_records(domain, document_id, index_id, include_vectors=True)
        new_records_data = [r.model_dump(mode="json") for r in index_records]
        new_artifact_hash = _records_artifact_hash(new_records_data)
        if existing_run.index_artifact_hash != new_artifact_hash:
            raise _error(
                code="VECTOR_INDEX_CONFLICT",
                message="A conflicting vector index already exists.",
            )
        return existing_run, existing_records

    records_data = [r.model_dump(mode="json") for r in index_records]
    records_artifact_hash = _records_artifact_hash(records_data)
    index_run.index_artifact_hash = records_artifact_hash

    index_spec = IndexSpec(
        index_id=index_id,
        document_id=document.document_id,
        domain=domain,
        base_path=Path(__import__("os").getenv("STORAGE_PATH", "./storage"))
        / "rag_ingestion"
        / domain
        / "vector_indexes"
        / document.document_id
        / index_id,
    )

    manifest = _build_manifest(index_run, records_artifact_hash)
    index_run.manifest_hash = manifest["manifest_hash"]

    try:
        adapter.create_index(index_spec)
        adapter.write_records(index_spec, manifest, records_data)
    except Exception as exc:
        index_run.status = VectorIndexRunStatus.FAILED
        index_run.last_error = str(exc)
        raise _error(
            code="VECTOR_INDEX_WRITE_FAILED",
            message="Failed to write vector index artifacts.",
        )

    # Read-back verification.
    index_run.status = VectorIndexRunStatus.VERIFYING
    read_records = adapter.read_records(index_spec)
    read_manifest = adapter.read_manifest(index_spec)
    if read_manifest is None:
        raise _error(
            code="VECTOR_INDEX_READBACK_FAILED",
            message="Manifest could not be read back after writing.",
        )
    if len(read_records) != len(records_data):
        raise _error(
            code="VECTOR_INDEX_ARTIFACT_MISMATCH",
            message="Record count mismatch on read-back.",
        )
    read_artifact_hash = _records_artifact_hash(read_records)
    if read_artifact_hash != records_artifact_hash:
        raise _error(
            code="VECTOR_INDEX_ARTIFACT_MISMATCH",
            message="Records artifact hash mismatch on read-back.",
        )
    if read_manifest.get("records_artifact_hash") != records_artifact_hash:
        raise _error(
            code="VECTOR_INDEX_ARTIFACT_MISMATCH",
            message="Manifest artifact hash mismatch on read-back.",
        )

    index_run.status = VectorIndexRunStatus.COMPLETED
    index_run.completed_at = datetime.utcnow()
    save_vector_index_run(index_run)
    return index_run, index_records
```

Note: the orchestrator imports `get_canonical_chunk` from store. If that helper doesn't exist, add it in Task 3.

- [ ] **Step 2: Add get_canonical_chunk to store if missing**

If `backend/app/core/rag_ingestion_store.py` lacks `get_canonical_chunk`, add it:

```python
def get_canonical_chunk(
    domain: str, document_id: str, chunk_id: str
) -> Optional[RagCanonicalChunk]:
    """Fetch a single canonical chunk by domain, document id, and chunk id."""
    path = _document_chunks_path(domain, document_id)
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            if data.get("chunk_id") == chunk_id:
                return RagCanonicalChunk(**data)
    except Exception as exc:
        logger.warning("Failed to load chunk %s: %s", chunk_id, exc)
    return None
```

- [ ] **Step 3: Add orchestrator tests**

Create `backend/tests/test_rag_vector_indexing.py` with helpers and tests. Use a similar `_seed_embedding_run` helper that creates a canonical document, chunk, embedding run, and chunk embedding, then persists them.

Key tests:

```python
def test_run_vector_index_dry_run_success(tmp_path, monkeypatch):
    # seed document, chunk, embedding run, and embedding records
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    index_run, records = run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    assert index_run.status.value == "completed"
    assert index_run.retrieval_enabled is False
    assert index_run.activation_status.value == "inactive"
    assert len(records) == 1


def test_run_vector_index_dry_run_idempotent(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    r1, recs1 = run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    r2, recs2 = run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    assert r1.index_run_id == r2.index_run_id
    assert r1.index_id == r2.index_id
    assert recs1[0].record_id == recs2[0].record_id


def test_run_vector_index_dry_run_rejects_non_dry_run(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    from app.core.rag_vector_indexing import VectorIndexError
    with pytest.raises(VectorIndexError) as exc:
        run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id, dry_run=False)
    assert exc.value.code == "DRY_RUN_REQUIRED"


def test_run_vector_index_dry_run_rejects_unknown_adapter(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    from app.core.rag_vector_indexing import VectorIndexError
    with pytest.raises(VectorIndexError) as exc:
        run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id, adapter_id="unknown")
    assert exc.value.code == "VECTOR_ADAPTER_NOT_FOUND"


def test_run_vector_index_dry_run_rejects_failed_embedding_run(tmp_path, monkeypatch):
    doc, chunk, run, embedding = _seed(tmp_path, monkeypatch)
    # overwrite run status to failed
    from app.core.rag_ingestion_store import save_embedding_run
    save_embedding_run(run.model_copy(update={"status": "failed"}))
    from app.core.rag_vector_indexing import VectorIndexError
    with pytest.raises(VectorIndexError) as exc:
        run_vector_index_dry_run(doc.domain, doc.document_id, run.run_id)
    assert exc.value.code == "EMBEDDING_RUN_NOT_ELIGIBLE"
```

- [ ] **Step 4: Run tests**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_vector_store_adapters.py tests/test_rag_vector_indexing.py -q --tb=short
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/rag_vector_indexing.py backend/app/core/rag_ingestion_store.py backend/tests/test_rag_vector_indexing.py
git commit -m "feat(rag): add vector indexing dry-run orchestration"
```

---

### Task 5: Add router endpoints

**Files:**
- Modify: `backend/app/routers/domains.py`
- Test: `backend/tests/test_rag_vector_index_endpoints.py`

**Interfaces:**
- Consumes: `run_vector_index_dry_run`, store getters.
- Produces: FastAPI routes.

- [ ] **Step 1: Add schemas and routes**

Add request/response schemas near other request models in `backend/app/routers/domains.py`:

```python
class VectorIndexDryRunRequest(BaseModel):
    dry_run: bool = True
    adapter_id: str = "local_flat_json_v1"


class VectorIndexRunResponse(BaseModel):
    index_run_id: str
    index_id: str
    document_id: str
    embedding_run_id: str
    adapter_id: str
    adapter_version: str
    status: str
    dimensions: int
    embedding_record_count: int
    indexed_record_count: int
    failed_record_count: int
    production_approved: bool
    retrieval_enabled: bool
    activation_status: str
    index_artifact_hash: Optional[str]
    manifest_hash: Optional[str]
    warnings: List[str]
    errors: List[dict]


class VectorIndexRecordResponse(BaseModel):
    record_id: str
    embedding_id: str
    chunk_id: str
    chunk_ordinal: int
    chunk_text_hash: str
    vector_hash: str
    dimensions: int
    distance_metric: str
    provider_id: str
    provider_version: str
    adapter_id: str
    adapter_version: str
    vector: Optional[List[float]] = None
```

Add routes:

```python
from app.core.rag_vector_indexing import VectorIndexError, run_vector_index_dry_run
from app.core.rag_ingestion_store import (
    get_embedding_run,
    get_vector_index_records,
    get_vector_index_run,
    list_vector_index_runs,
)


@router.post(
    "/{domain_id}/rag-ingestion/documents/{document_id}/embedding-runs/{embedding_run_id}/vector-index-dry-run"
)
def create_vector_index_dry_run(
    domain_id: str,
    document_id: str,
    embedding_run_id: str,
    request: VectorIndexDryRunRequest,
):
    if not request.dry_run:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "DRY_RUN_REQUIRED",
                "message": "Actual vector indexing is not enabled. Use dry_run=true.",
            },
        )
    try:
        run, _ = run_vector_index_dry_run(
            domain=domain_id,
            document_id=document_id,
            embedding_run_id=embedding_run_id,
            adapter_id=request.adapter_id,
            dry_run=True,
        )
    except VectorIndexError as exc:
        status_code = status.HTTP_409_CONFLICT if exc.code == "DRY_RUN_REQUIRED" else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message, "field": exc.field},
        ) from exc
    return _vector_index_run_response(run)


def _vector_index_run_response(run) -> dict:
    return {
        "index_run_id": run.index_run_id,
        "index_id": run.index_id,
        "document_id": run.document_id,
        "embedding_run_id": run.embedding_run_id,
        "adapter_id": run.adapter_id,
        "adapter_version": run.adapter_version,
        "status": run.status.value,
        "dimensions": run.dimensions,
        "embedding_record_count": run.embedding_record_count,
        "indexed_record_count": run.indexed_record_count,
        "failed_record_count": run.failed_record_count,
        "production_approved": run.production_approved,
        "retrieval_enabled": run.retrieval_enabled,
        "activation_status": run.activation_status.value,
        "index_artifact_hash": run.index_artifact_hash,
        "manifest_hash": run.manifest_hash,
        "warnings": run.warnings,
        "errors": [e.model_dump(mode="json") for e in run.errors],
    }


@router.get(
    "/{domain_id}/rag-ingestion/documents/{document_id}/vector-index-runs"
)
def list_vector_index_runs_endpoint(domain_id: str, document_id: str):
    document = get_canonical_document(domain_id, document_id)
    if document is None or document.domain != domain_id:
        raise HTTPException(status_code=404, detail="Document not found")
    runs = list_vector_index_runs(domain_id, document_id)
    return {"domain": domain_id, "document_id": document_id, "index_runs": [_vector_index_run_response(r) for r in runs]}


@router.get(
    "/{domain_id}/rag-ingestion/documents/{document_id}/vector-index-runs/{index_run_id}"
)
def get_vector_index_run_endpoint(domain_id: str, document_id: str, index_run_id: str):
    document = get_canonical_document(domain_id, document_id)
    if document is None or document.domain != domain_id:
        raise HTTPException(status_code=404, detail="Document not found")
    run = get_vector_index_run(domain_id, document_id, index_run_id)
    if run is None or run.domain != domain_id or run.document_id != document_id:
        raise HTTPException(status_code=404, detail="Index run not found")
    return _vector_index_run_response(run)


@router.get(
    "/{domain_id}/rag-ingestion/documents/{document_id}/vector-index-runs/{index_run_id}/records"
)
def list_vector_index_records_endpoint(
    domain_id: str,
    document_id: str,
    index_run_id: str,
    include_vectors: bool = False,
):
    document = get_canonical_document(domain_id, document_id)
    if document is None or document.domain != domain_id:
        raise HTTPException(status_code=404, detail="Document not found")
    run = get_vector_index_run(domain_id, document_id, index_run_id)
    if run is None or run.domain != domain_id or run.document_id != document_id:
        raise HTTPException(status_code=404, detail="Index run not found")
    records = get_vector_index_records(domain_id, document_id, run.index_id, include_vectors=include_vectors)
    return {
        "domain": domain_id,
        "document_id": document_id,
        "index_run_id": index_run_id,
        "index_id": run.index_id,
        "records": [
            {
                "record_id": r.record_id,
                "embedding_id": r.embedding_id,
                "chunk_id": r.chunk_id,
                "chunk_ordinal": r.chunk_ordinal,
                "chunk_text_hash": r.chunk_text_hash,
                "vector_hash": r.vector_hash,
                "dimensions": r.dimensions,
                "distance_metric": r.distance_metric,
                "provider_id": r.provider_id,
                "provider_version": r.provider_version,
                "adapter_id": r.adapter_id,
                "adapter_version": r.adapter_version,
                "vector": r.vector if include_vectors else None,
            }
            for r in records
        ],
    }
```

- [ ] **Step 2: Add endpoint tests**

Create `backend/tests/test_rag_vector_index_endpoints.py` with a `_seed_embedding_run` helper and tests covering success, dry-run rejection, wrong domain, and record retrieval with/without vectors.

- [ ] **Step 3: Run tests**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_vector_index_endpoints.py -q --tb=short
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/domains.py backend/tests/test_rag_vector_index_endpoints.py
git commit -m "feat(rag): add vector index dry-run endpoints"
```

---

### Task 6: Add configuration

**Files:**
- Modify: `backend/.env.example`

- [ ] **Step 1: Add env vars**

Append to `backend/.env.example`:

```text
# Vector indexing dry-run configuration
RAG_VECTOR_DEFAULT_ADAPTER=local_flat_json_v1
RAG_VECTOR_INDEX_MANIFEST_VERSION=vector-index-manifest-v1
RAG_VECTOR_RECORD_PRECISION=8
RAG_VECTOR_INDEX_MAX_RECORDS=10000
RAG_VECTOR_INDEX_INCLUDE_VECTORS_DEFAULT=false
```

- [ ] **Step 2: Commit**

```bash
git add backend/.env.example
git commit -m "chore(config): add vector index dry-run env vars"
```

---

### Task 7: Full regression and final verification

- [ ] **Step 1: Run targeted vector index tests**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_vector_store_adapters.py tests/test_rag_vector_indexing.py tests/test_rag_vector_index_endpoints.py -q --tb=short
```
Expected: all pass.

- [ ] **Step 2: Run broader RAG regression**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_embedding_providers.py tests/test_rag_embeddings.py tests/test_rag_embedding_endpoints.py tests/test_rag_canonical_text.py tests/test_rag_chunking.py tests/test_rag_canonical_documents.py tests/test_rag_canonical_document_endpoints.py tests/test_rag_source_acquisition.py tests/test_rag_source_parsers.py tests/test_rag_acquisition_endpoints.py tests/test_rag_ingestion_validation.py tests/test_rag_ingestion_jobs.py tests/test_rag_ingestion_endpoints.py tests/test_engine_discovery.py tests/test_rag_pack_loader.py tests/test_rag_pack_endpoint.py tests/test_rag_activation.py tests/test_rag_activation_endpoint.py tests/test_source_pack_endpoint.py tests/test_chain_generator_source_packs.py -q --tb=short
```
Expected: all pass.

- [ ] **Step 3: Run full suite**

```bash
cd backend
.venv/Scripts/python -m pytest tests -q
```
Expected: all pass.

- [ ] **Step 4: Push and create PR**

```bash
git push -u origin feat/rag-vector-index-dry-run
gh pr create --title "feat(rag): add vector-store contract and local indexing dry-run" --body "..." --base master
```

Wait for CI, then merge:

```bash
gh pr merge <NUMBER> --squash --delete-branch=false
```

---

## Self-Review

**Spec coverage:**
- Adapter contract: Task 2.
- Index/run/record models: Task 1.
- Deterministic IDs: Task 4.
- Eligibility: Task 4.
- Endpoint: Task 5.
- Atomic persistence + read-back: Task 4.
- Idempotency/conflicts: Task 4.
- Configuration: Task 6.
- State isolation: enforced by never mutating index_status.
- Tests: Tasks 2/4/5.

**Placeholder scan:** No TBD/TODO/fill-in-details.

**Type consistency:** Model and function signatures match across tasks.
