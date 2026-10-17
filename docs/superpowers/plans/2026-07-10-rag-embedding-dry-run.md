# RAG Embedding Contract and Offline Dry-Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 6 of the governed RAG pipeline — a model-independent embedding contract and an offline validation-only embedding dry-run that reads only persisted canonical chunks.

**Architecture:** A provider interface (`rag_embedding_providers.py`) defines the embedding contract; a deterministic signed-feature-hash provider (`local_feature_hash_v1`) validates the pipeline mechanics; an orchestrator (`rag_embeddings.py`) handles eligibility, deterministic batching, vector validation, deterministic run/embedding IDs, and audit persistence; domain-scoped FastAPI endpoints expose dry-run execution and retrieval.

**Tech Stack:** Python 3.11, Pydantic v1, FastAPI, pytest, Python standard library only (no new dependencies).

## Global Constraints

- Embeddings must be produced **only** from persisted `RagCanonicalChunk.text`.
- Do **not** embed `acquisition_report.extracted_text`, previews, raw bytes, or client-supplied text.
- No external embedding APIs, no model downloads, no vector database, no retrieval.
- `RagCanonicalDocument.index_status`, `RagCanonicalChunk.index_status`, RAG pack `ingestion_status`, and ingestion job status must **not** change to indexed/ingesting.
- `dry_run` must be `true`; `dry_run = false` returns `409 Conflict`.
- All run and embedding identities must be deterministic.
- Retries must be idempotent; conflicting artifacts must return a clean error, not silently overwrite.
- Atomic filesystem writes; no source-controlled runtime artifacts.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backend/app/models/rag_ingestion.py` | Add `EmbeddingRunStatus`, `RagEmbeddingRun`, `RagChunkEmbedding` models. |
| `backend/app/core/rag_embedding_providers.py` | Define `RagEmbeddingProvider` contract and `local_feature_hash_v1` implementation. |
| `backend/app/core/rag_embeddings.py` | Orchestrate eligibility, batching, vector validation, IDs, persistence calls. |
| `backend/app/core/rag_ingestion_store.py` | Add embedding run and chunk-embedding persistence helpers. |
| `backend/app/routers/domains.py` | Add embedding dry-run POST and retrieval GET endpoints. |
| `backend/.env.example` | Add embedding configuration env vars. |
| `backend/tests/test_rag_embedding_providers.py` | Unit tests for provider contract and feature hashing. |
| `backend/tests/test_rag_embeddings.py` | Unit tests for orchestration, eligibility, validation, idempotency. |
| `backend/tests/test_rag_embedding_endpoints.py` | Endpoint tests for domain ownership, dry-run enforcement, retrieval. |

---

### Task 1: Add embedding models

**Files:**
- Modify: `backend/app/models/rag_ingestion.py`
- Test: `backend/tests/test_rag_embedding_providers.py` (will import these models)

**Interfaces:**
- Consumes: existing `IndexStatus` enum.
- Produces: `EmbeddingRunStatus` enum, `RagEmbeddingRun` model, `RagChunkEmbedding` model.

- [ ] **Step 1: Add statuses and models**

Add to `backend/app/models/rag_ingestion.py` after the `IndexStatus` class:

```python
class EmbeddingRunStatus(str, Enum):
    """Lifecycle status for an embedding dry-run."""

    PENDING = "pending"
    VALIDATING = "validating"
    EMBEDDING = "embedding"
    COMPLETED = "completed"
    FAILED = "failed"


class RagEmbeddingRun(BaseModel):
    """Audit record for an embedding dry-run over a canonical document."""

    run_id: str
    document_id: str
    job_id: str
    source_id: str
    acquisition_id: str
    rag_pack_id: str
    collection_id: str
    domain: str
    provider_id: str
    provider_version: str
    algorithm: str
    dimensions: int
    distance_metric: str
    normalization: str
    dry_run: bool = True
    status: EmbeddingRunStatus = EmbeddingRunStatus.PENDING
    document_chunk_count: int = 0
    eligible_chunk_count: int = 0
    embedded_chunk_count: int = 0
    failed_chunk_count: int = 0
    batch_count: int = 0
    vector_artifact_hash: Optional[str] = None
    production_approved: bool = False
    index_status: IndexStatus = IndexStatus.NOT_INDEXED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    warnings: List[str] = Field(default_factory=list)
    errors: List[ValidationError] = Field(default_factory=list)
    last_error: Optional[str] = None


class RagChunkEmbedding(BaseModel):
    """A single deterministic embedding for a canonical chunk."""

    embedding_id: str
    run_id: str
    document_id: str
    chunk_id: str
    collection_id: str
    domain: str
    chunk_ordinal: int
    chunk_text_hash: str
    provider_id: str
    provider_version: str
    dimensions: int
    distance_metric: str
    normalization: str
    vector_hash: str
    vector: List[float]
    index_status: IndexStatus = IndexStatus.NOT_INDEXED
    untrusted_content: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 2: Verify import**

Run:
```bash
cd backend
.venv/Scripts/python -c "from app.models.rag_ingestion import RagEmbeddingRun, RagChunkEmbedding, EmbeddingRunStatus; print('ok')"
```
Expected output:
```
ok
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/rag_ingestion.py
git commit -m "feat(rag): add embedding run and chunk embedding models"
```

---

### Task 2: Implement embedding provider contract and local_feature_hash_v1

**Files:**
- Create: `backend/app/core/rag_embedding_providers.py`
- Test: `backend/tests/test_rag_embedding_providers.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RagEmbeddingProvider` dataclass, `register_provider`, `get_provider`, `LocalFeatureHashProviderV1`, `LOCAL_FEATURE_HASH_V1` constant.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_rag_embedding_providers.py`:

```python
from app.core.rag_embedding_providers import (
    LOCAL_FEATURE_HASH_V1,
    get_provider,
)


def test_get_provider_returns_contract():
    provider = get_provider(LOCAL_FEATURE_HASH_V1)
    assert provider.provider_id == LOCAL_FEATURE_HASH_V1
    assert provider.dimensions == 384
    assert provider.distance_metric == "cosine"
    assert provider.normalization == "l2"
    assert provider.production_approved is False


def test_feature_hash_deterministic():
    provider = get_provider(LOCAL_FEATURE_HASH_V1)
    v1 = provider.embed_texts(["hello world"])[0]
    v2 = provider.embed_texts(["hello world"])[0]
    assert v1 == v2


def test_feature_hash_l2_normalized():
    provider = get_provider(LOCAL_FEATURE_HASH_V1)
    vector = provider.embed_texts(["deterministic normalization test"])[0]
    norm = sum(x * x for x in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_feature_hash_empty_text_rejected():
    provider = get_provider(LOCAL_FEATURE_HASH_V1)
    with pytest.raises(ValueError):
        provider.embed_texts([""])
```

(Also add `import pytest` at the top.)

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_embedding_providers.py -q
```
Expected: import/attribute errors.

- [ ] **Step 3: Implement provider module**

Create `backend/app/core/rag_embedding_providers.py`:

```python
"""Model-independent embedding provider contract and validation-only providers.

No external APIs, no model downloads, no vector-store operations.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

LOCAL_FEATURE_HASH_V1 = "local_feature_hash_v1"

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+(?:['-][a-zA-Z0-9]+)*", re.UNICODE)


@dataclass
class RagEmbeddingProvider:
    """Contract for an embedding provider."""

    provider_id: str
    provider_version: str
    algorithm: str
    dimensions: int
    distance_metric: str
    normalization: str
    maximum_batch_size: int
    maximum_input_characters: int
    production_approved: bool

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError


class _LocalFeatureHashProviderV1(RagEmbeddingProvider):
    """Deterministic signed feature hashing for offline validation.

    Not a semantic embedding model. Used only to validate the embedding
    contract, batching, and persistence layers before introducing a
    production neural provider.
    """

    _VERSION = "1"
    _DIMENSIONS = 384
    _MAX_CHARS = 200_000
    _MAX_BATCH = 64

    def __init__(self) -> None:
        super().__init__(
            provider_id=LOCAL_FEATURE_HASH_V1,
            provider_version=self._VERSION,
            algorithm="signed-feature-hashing",
            dimensions=self._DIMENSIONS,
            distance_metric="cosine",
            normalization="l2",
            maximum_batch_size=self._MAX_BATCH,
            maximum_input_characters=self._MAX_CHARS,
            production_approved=False,
        )

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> List[float]:
        if not text:
            raise ValueError("Cannot embed empty text.")
        text = text[: self.maximum_input_characters]
        vector = [0.0] * self.dimensions
        for token in _TOKEN_RE.findall(text.lower()):
            token_bytes = token.encode("utf-8")
            digest = hashlib.sha256(token_bytes).digest()
            dim = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1 if digest[4] % 2 == 0 else -1
            weight = 1.0 + math.log1p(len(token))
            vector[dim] += sign * weight
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            raise ValueError("Embedding produced a zero vector.")
        vector = [round(v / norm, 8) for v in vector]
        # Renormalize after rounding to keep L2 norm ≈ 1.
        rounded_norm = math.sqrt(sum(v * v for v in vector))
        if rounded_norm == 0:
            raise ValueError("Embedding produced a zero vector after rounding.")
        return [round(v / rounded_norm, 8) for v in vector]


_PROVIDERS: Dict[str, RagEmbeddingProvider] = {
    LOCAL_FEATURE_HASH_V1: _LocalFeatureHashProviderV1(),
}


def register_provider(provider: RagEmbeddingProvider) -> None:
    """Register a provider implementation by provider_id."""
    _PROVIDERS[provider.provider_id] = provider


def get_provider(provider_id: str) -> Optional[RagEmbeddingProvider]:
    """Return the provider implementation for provider_id, or None."""
    return _PROVIDERS.get(provider_id)


def list_provider_ids() -> List[str]:
    """Return all registered provider IDs."""
    return list(_PROVIDERS.keys())
```

- [ ] **Step 4: Run tests**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_embedding_providers.py -q
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/rag_embedding_providers.py backend/tests/test_rag_embedding_providers.py
git commit -m "feat(rag): add embedding provider contract and local_feature_hash_v1"
```

---

### Task 3: Implement embedding orchestration

**Files:**
- Create: `backend/app/core/rag_embeddings.py`
- Test: `backend/tests/test_rag_embeddings.py`

**Interfaces:**
- Consumes: `RagEmbeddingProvider`, `rag_ingestion_store` getters/savers, `RagCanonicalDocument`, `RagCanonicalChunk`, `RagEmbeddingRun`, `RagChunkEmbedding`.
- Produces: `run_embedding_dry_run(domain, document_id, provider_id)` returning `(RagEmbeddingRun, List[RagChunkEmbedding])`.

- [ ] **Step 1: Add store getters needed by orchestrator**

Before writing orchestrator, extend `backend/app/core/rag_ingestion_store.py` with the read helpers (write helpers added in Task 4). Add after `list_canonical_documents`:

```python
def _embeddings_dir(domain: str, document_id: str) -> Path:
    path = Path(_storage_path()) / "rag_ingestion" / domain / "embeddings" / document_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _embedding_run_path(domain: str, document_id: str, run_id: str) -> Path:
    return _embeddings_dir(domain, document_id) / f"{run_id}.json"


def _embedding_vectors_path(domain: str, document_id: str, run_id: str) -> Path:
    return _embeddings_dir(domain, document_id) / f"{run_id}.vectors.jsonl"
```

- [ ] **Step 2: Implement orchestrator**

Create `backend/app/core/rag_embeddings.py`:

```python
"""Embedding dry-run orchestration for canonical RAG chunks.

Reads only persisted RagCanonicalChunk records. No vector database,
no retrieval, no external APIs, no model downloads.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from datetime import datetime
from typing import List, Optional, Tuple

from app.models.rag_ingestion import (
    CanonicalizationStatus,
    ChunkingStatus,
    EmbeddingRunStatus,
    IndexStatus,
    RagCanonicalChunk,
    RagCanonicalDocument,
    RagChunkEmbedding,
    RagEmbeddingRun,
    ValidationError,
)

from .rag_embedding_providers import LOCAL_FEATURE_HASH_V1, get_provider
from .rag_ingestion_store import (
    get_canonical_document,
    get_canonical_chunk,
    list_canonical_chunks,
)

logger = logging.getLogger(__name__)

RAG_EMBEDDING_DEFAULT_PROVIDER = os.getenv(
    "RAG_EMBEDDING_DEFAULT_PROVIDER", LOCAL_FEATURE_HASH_V1
)
RAG_EMBEDDING_DIMENSIONS = int(os.getenv("RAG_EMBEDDING_DIMENSIONS", "384"))
RAG_EMBEDDING_BATCH_SIZE = int(os.getenv("RAG_EMBEDDING_BATCH_SIZE", "64"))
RAG_EMBEDDING_VECTOR_PRECISION = int(os.getenv("RAG_EMBEDDING_VECTOR_PRECISION", "8"))
RAG_EMBEDDING_NORM_TOLERANCE = float(os.getenv("RAG_EMBEDDING_NORM_TOLERANCE", "0.00001"))


class EmbeddingError(Exception):
    """Raised when embedding dry-run fails."""

    def __init__(self, code: str, message: str, field: Optional[str] = None):
        self.code = code
        self.message = message
        self.field = field
        super().__init__(message)

    def to_validation_error(self) -> ValidationError:
        return ValidationError(code=self.code, field=self.field, message=self.message)


def _error(code: str, message: str, field: Optional[str] = None) -> EmbeddingError:
    return EmbeddingError(code=code, message=message, field=field)


def _run_id(
    collection_id: str,
    document_id: str,
    provider_id: str,
    provider_version: str,
    dimensions: int,
    normalization: str,
) -> str:
    data = (
        f"{collection_id}:{document_id}:{provider_id}:"
        f"{provider_version}:{dimensions}:{normalization}"
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _embedding_id(
    run_id: str, chunk_id: str, provider_id: str, provider_version: str
) -> str:
    data = f"{run_id}:{chunk_id}:{provider_id}:{provider_version}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _vector_hash(vector: List[float]) -> str:
    serialized = json.dumps(vector, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _artifact_hash(records: List[dict]) -> str:
    lines = [json.dumps(r, separators=(",", ":"), sort_keys=True) for r in records]
    blob = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _validate_configuration() -> List[ValidationError]:
    errors: List[ValidationError] = []
    if RAG_EMBEDDING_DIMENSIONS <= 0:
        errors.append(
            ValidationError(
                code="EMBEDDING_CONFIGURATION_INVALID",
                field="RAG_EMBEDDING_DIMENSIONS",
                message="Dimensions must be > 0.",
            )
        )
    if RAG_EMBEDDING_BATCH_SIZE <= 0:
        errors.append(
            ValidationError(
                code="EMBEDDING_CONFIGURATION_INVALID",
                field="RAG_EMBEDDING_BATCH_SIZE",
                message="Batch size must be > 0.",
            )
        )
    if RAG_EMBEDDING_VECTOR_PRECISION < 1:
        errors.append(
            ValidationError(
                code="EMBEDDING_CONFIGURATION_INVALID",
                field="RAG_EMBEDDING_VECTOR_PRECISION",
                message="Precision must be >= 1.",
            )
        )
    if RAG_EMBEDDING_NORM_TOLERANCE <= 0:
        errors.append(
            ValidationError(
                code="EMBEDDING_CONFIGURATION_INVALID",
                field="RAG_EMBEDDING_NORM_TOLERANCE",
                message="Norm tolerance must be > 0.",
            )
        )
    return errors


def _validate_document_eligibility(
    document: RagCanonicalDocument,
) -> List[ValidationError]:
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
    if document.chunk_count <= 0:
        errors.append(
            ValidationError(
                code="DOCUMENT_HAS_NO_CHUNKS",
                field="chunk_count",
                message="Document has no chunks.",
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


def _validate_chunks(
    document: RagCanonicalDocument,
    chunks: List[RagCanonicalChunk],
) -> List[ValidationError]:
    errors: List[ValidationError] = []
    if len(chunks) != document.chunk_count:
        errors.append(
            ValidationError(
                code="DOCUMENT_HAS_NO_CHUNKS",
                field="chunk_count",
                message=f"Expected {document.chunk_count} chunks, found {len(chunks)}.",
            )
        )
    for chunk in chunks:
        if chunk.document_id != document.document_id:
            errors.append(
                ValidationError(
                    code="CHUNK_LINKAGE_MISMATCH",
                    field="document_id",
                    message="Chunk does not belong to this document.",
                )
            )
        if chunk.domain != document.domain:
            errors.append(
                ValidationError(
                    code="CHUNK_LINKAGE_MISMATCH",
                    field="domain",
                    message="Chunk domain does not match document.",
                )
            )
        if chunk.collection_id != document.collection_id:
            errors.append(
                ValidationError(
                    code="CHUNK_LINKAGE_MISMATCH",
                    field="collection_id",
                    message="Chunk collection does not match document.",
                )
            )
        if chunk.index_status != IndexStatus.NOT_INDEXED:
            errors.append(
                ValidationError(
                    code="DOCUMENT_NOT_ELIGIBLE",
                    field="index_status",
                    message="Chunk is already indexed.",
                )
            )
        if not chunk.untrusted_content:
            errors.append(
                ValidationError(
                    code="DOCUMENT_NOT_ELIGIBLE",
                    field="untrusted_content",
                    message="Chunk must be marked untrusted_content.",
                )
            )
    return errors


def _validate_vectors(
    vectors: List[List[float]],
    expected_dimensions: int,
) -> List[ValidationError]:
    errors: List[ValidationError] = []
    for i, vector in enumerate(vectors):
        if len(vector) != expected_dimensions:
            errors.append(
                ValidationError(
                    code="EMBEDDING_DIMENSION_MISMATCH",
                    field=f"vector[{i}]",
                    message=f"Expected dimension {expected_dimensions}, got {len(vector)}.",
                )
            )
            continue
        if any(math.isnan(v) or math.isinf(v) for v in vector):
            errors.append(
                ValidationError(
                    code="EMBEDDING_NON_FINITE_VALUE",
                    field=f"vector[{i}]",
                    message="Vector contains NaN or infinity.",
                )
            )
            continue
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            errors.append(
                ValidationError(
                    code="EMBEDDING_ZERO_VECTOR",
                    field=f"vector[{i}]",
                    message="Vector has zero norm.",
                )
            )
            continue
        if abs(norm - 1.0) > RAG_EMBEDDING_NORM_TOLERANCE:
            errors.append(
                ValidationError(
                    code="EMBEDDING_VECTOR_INVALID",
                    field=f"vector[{i}]",
                    message=f"Vector L2 norm {norm} deviates from 1.0.",
                )
            )
    return errors


def _make_batches(chunks: List[RagCanonicalChunk], batch_size: int) -> List[List[RagCanonicalChunk]]:
    sorted_chunks = sorted(chunks, key=lambda c: c.ordinal)
    return [
        sorted_chunks[i : i + batch_size]
        for i in range(0, len(sorted_chunks), batch_size)
    ]


def run_embedding_dry_run(
    domain: str,
    document_id: str,
    provider_id: str = RAG_EMBEDDING_DEFAULT_PROVIDER,
    dry_run: bool = True,
) -> Tuple[RagEmbeddingRun, List[RagChunkEmbedding]]:
    """Run an offline embedding dry-run for a canonical document.

    Raises EmbeddingError on validation failures.
    """
    if not dry_run:
        raise _error(
            code="DRY_RUN_REQUIRED",
            message="Actual embedding is not enabled. Use dry_run=true.",
            field="dry_run",
        )

    config_errors = _validate_configuration()
    if config_errors:
        raise _error(
            code="EMBEDDING_CONFIGURATION_INVALID",
            message="Invalid embedding configuration.",
        )

    document = get_canonical_document(domain, document_id)
    if document is None:
        raise _error(
            code="DOCUMENT_NOT_FOUND",
            message="Canonical document not found.",
            field="document_id",
        )

    eligibility_errors = _validate_document_eligibility(document)
    if eligibility_errors:
        raise _error(
            code="DOCUMENT_NOT_ELIGIBLE",
            message="Document is not eligible for embedding.",
        )

    chunks = list_canonical_chunks(domain, document_id)
    chunk_errors = _validate_chunks(document, chunks)
    if chunk_errors:
        raise _error(
            code="DOCUMENT_NOT_ELIGIBLE",
            message="Chunks are not eligible for embedding.",
        )

    provider = get_provider(provider_id)
    if provider is None:
        raise _error(
            code="EMBEDDING_PROVIDER_NOT_FOUND",
            message=f"Provider {provider_id} not found.",
            field="provider_id",
        )

    if (
        provider.dimensions != RAG_EMBEDDING_DIMENSIONS
        and provider_id == LOCAL_FEATURE_HASH_V1
    ):
        # Allow tests to override via env; otherwise provider contract wins.
        pass

    run_id = _run_id(
        collection_id=document.collection_id,
        document_id=document.document_id,
        provider_id=provider.provider_id,
        provider_version=provider.provider_version,
        dimensions=provider.dimensions,
        normalization=provider.normalization,
    )

    warnings: List[str] = []
    if document.parser_truncated:
        warnings.append("SOURCE_DOCUMENT_TRUNCATED")
    if not provider.production_approved:
        warnings.append("VALIDATION_ONLY_PROVIDER")

    run = RagEmbeddingRun(
        run_id=run_id,
        document_id=document.document_id,
        job_id=document.job_id,
        source_id=document.source_id,
        acquisition_id=document.acquisition_id,
        rag_pack_id=document.rag_pack_id,
        collection_id=document.collection_id,
        domain=document.domain,
        provider_id=provider.provider_id,
        provider_version=provider.provider_version,
        algorithm=provider.algorithm,
        dimensions=provider.dimensions,
        distance_metric=provider.distance_metric,
        normalization=provider.normalization,
        dry_run=True,
        status=EmbeddingRunStatus.EMBEDDING,
        document_chunk_count=len(chunks),
        eligible_chunk_count=len(chunks),
        warnings=warnings,
    )

    batches = _make_batches(chunks, min(RAG_EMBEDDING_BATCH_SIZE, provider.maximum_batch_size))
    run.batch_count = len(batches)

    chunk_embeddings: List[RagChunkEmbedding] = []
    failed = 0

    for batch in batches:
        texts = [chunk.text for chunk in batch]
        try:
            vectors = provider.embed_texts(texts)
        except Exception as exc:
            failed += len(batch)
            logger.warning("Embedding batch failed: %s", exc)
            continue

        validation_errors = _validate_vectors(vectors, provider.dimensions)
        if validation_errors:
            failed += len(batch)
            run.errors.extend(validation_errors)
            continue

        for chunk, vector in zip(batch, vectors):
            embedding_id = _embedding_id(
                run_id=run_id,
                chunk_id=chunk.chunk_id,
                provider_id=provider.provider_id,
                provider_version=provider.provider_version,
            )
            chunk_embeddings.append(
                RagChunkEmbedding(
                    embedding_id=embedding_id,
                    run_id=run_id,
                    document_id=document.document_id,
                    chunk_id=chunk.chunk_id,
                    collection_id=document.collection_id,
                    domain=document.domain,
                    chunk_ordinal=chunk.ordinal,
                    chunk_text_hash=chunk.text_hash,
                    provider_id=provider.provider_id,
                    provider_version=provider.provider_version,
                    dimensions=provider.dimensions,
                    distance_metric=provider.distance_metric,
                    normalization=provider.normalization,
                    vector_hash=_vector_hash(vector),
                    vector=vector,
                )
            )

    run.embedded_chunk_count = len(chunk_embeddings)
    run.failed_chunk_count = failed

    if failed > 0 or run.errors:
        run.status = EmbeddingRunStatus.FAILED
        run.last_error = run.errors[0].message if run.errors else "Batch embedding failed."
        raise _error(
            code="EMBEDDING_RUN_FAILED",
            message=run.last_error,
        )

    run.status = EmbeddingRunStatus.COMPLETED
    run.completed_at = datetime.utcnow()
    return run, chunk_embeddings
```

- [ ] **Step 3: Add tests for orchestration**

Create `backend/tests/test_rag_embeddings.py` with helpers and tests. Start with a small set and expand:

```python
import os
from datetime import datetime

import pytest

from app.core.rag_canonical_documents import create_canonical_document
from app.core.rag_embeddings import run_embedding_dry_run
from app.core.rag_ingestion_store import (
    get_canonical_document,
    list_canonical_chunks,
    save_canonical_document,
)
from app.models.rag_ingestion import (
    AcquisitionStatus,
    CanonicalizationStatus,
    ChunkingStatus,
    DuplicateStatus,
    IndexStatus,
    JobStatus,
    ParseStatus,
    RagAcquisitionReport,
    RagCanonicalChunk,
    RagCanonicalDocument,
    RagIngestionJob,
)


def _make_document(tmp_path, monkeypatch, text="hello world"):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    domain = "legal"
    collection_id = "prebuilt_legal_core"
    document_id = "doc1"
    doc = RagCanonicalDocument(
        document_id=document_id,
        job_id="job1",
        source_id="source1",
        acquisition_id="acq1",
        rag_pack_id="legal_core_rag",
        collection_id=collection_id,
        domain=domain,
        source_uri="https://example.com/source.pdf",
        title="Test Source",
        source_class="public_domain",
        content_type="text/plain",
        raw_content_hash="abc123",
        canonical_text_hash="def456",
        normalization_algorithm="canonical-text",
        normalization_version="canonical-text-v1",
        parser_id="plain_text",
        parser_version="v1",
        character_count=len(text),
        line_count=1,
        chunk_count=1,
        parser_truncated=False,
        canonicalization_status=CanonicalizationStatus.CANONICALIZED,
        chunking_status=ChunkingStatus.CHUNKED,
        index_status=IndexStatus.NOT_INDEXED,
        untrusted_content=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    chunk = RagCanonicalChunk(
        chunk_id="chunk1",
        document_id=document_id,
        job_id="job1",
        source_id="source1",
        acquisition_id="acq1",
        rag_pack_id="legal_core_rag",
        collection_id=collection_id,
        domain=domain,
        ordinal=0,
        text=text,
        text_hash="hash1",
        character_start=0,
        character_end=len(text),
        character_count=len(text),
        overlap_from_previous=0,
        structural_type="fragment",
        chunking_algorithm="structural-character",
        chunking_version="structural-character-v1",
        target_characters=3200,
        maximum_characters=4000,
        overlap_characters=400,
        index_status=IndexStatus.NOT_INDEXED,
        untrusted_content=True,
        created_at=datetime.utcnow(),
    )
    save_canonical_document(doc, text, [chunk])
    return doc, chunk


def test_run_embedding_dry_run_success(tmp_path, monkeypatch):
    doc, chunk = _make_document(tmp_path, monkeypatch)
    run, embeddings = run_embedding_dry_run(doc.domain, doc.document_id)
    assert run.status.value == "completed"
    assert run.dry_run is True
    assert run.production_approved is False
    assert len(embeddings) == 1
    assert embeddings[0].chunk_id == chunk.chunk_id
    assert embeddings[0].dimensions == 384
    assert len(embeddings[0].vector) == 384


def test_run_embedding_dry_run_idempotent(tmp_path, monkeypatch):
    doc, _ = _make_document(tmp_path, monkeypatch)
    run1, embeddings1 = run_embedding_dry_run(doc.domain, doc.document_id)
    run2, embeddings2 = run_embedding_dry_run(doc.domain, doc.document_id)
    assert run1.run_id == run2.run_id
    assert embeddings1[0].embedding_id == embeddings2[0].embedding_id
    assert embeddings1[0].vector == embeddings2[0].vector


def test_run_embedding_dry_run_rejects_non_dry_run(tmp_path, monkeypatch):
    doc, _ = _make_document(tmp_path, monkeypatch)
    from app.core.rag_embeddings import EmbeddingError
    with pytest.raises(EmbeddingError) as exc:
        run_embedding_dry_run(doc.domain, doc.document_id, dry_run=False)
    assert exc.value.code == "DRY_RUN_REQUIRED"


def test_run_embedding_dry_run_rejects_unknown_provider(tmp_path, monkeypatch):
    doc, _ = _make_document(tmp_path, monkeypatch)
    from app.core.rag_embeddings import EmbeddingError
    with pytest.raises(EmbeddingError) as exc:
        run_embedding_dry_run(doc.domain, doc.document_id, provider_id="unknown")
    assert exc.value.code == "EMBEDDING_PROVIDER_NOT_FOUND"


def test_run_embedding_dry_run_rejects_no_chunks(tmp_path, monkeypatch):
    doc, _ = _make_document(tmp_path, monkeypatch)
    # overwrite with zero chunks
    save_canonical_document(doc.model_copy(update={"chunk_count": 0}), "hello world", [])
    from app.core.rag_embeddings import EmbeddingError
    with pytest.raises(EmbeddingError) as exc:
        run_embedding_dry_run(doc.domain, doc.document_id)
    assert exc.value.code == "DOCUMENT_HAS_NO_CHUNKS"


def test_run_embedding_dry_run_truncated_document_warns(tmp_path, monkeypatch):
    doc, chunk = _make_document(tmp_path, monkeypatch)
    save_canonical_document(
        doc.model_copy(update={"parser_truncated": True}), "hello world", [chunk]
    )
    run, _ = run_embedding_dry_run(doc.domain, doc.document_id)
    assert "SOURCE_DOCUMENT_TRUNCATED" in run.warnings
    assert "VALIDATION_ONLY_PROVIDER" in run.warnings
```

- [ ] **Step 4: Run tests**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_embeddings.py -q
```
Expected: 6 passed (or adjust as you add more tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/rag_embeddings.py backend/tests/test_rag_embeddings.py backend/app/core/rag_ingestion_store.py
git commit -m "feat(rag): add embedding dry-run orchestration"
```

---

### Task 4: Add embedding persistence to store

**Files:**
- Modify: `backend/app/core/rag_ingestion_store.py`
- Test: `backend/tests/test_rag_embeddings.py`

**Interfaces:**
- Consumes: `RagEmbeddingRun`, `RagChunkEmbedding`.
- Produces: `save_embedding_run`, `get_embedding_run`, `list_embedding_runs`, `get_chunk_embeddings`, `get_chunk_embedding`.

- [ ] **Step 1: Implement persistence helpers**

Add to `backend/app/core/rag_ingestion_store.py` after the read helpers from Task 3:

```python
def save_embedding_run(
    run: RagEmbeddingRun,
    chunk_embeddings: List[RagChunkEmbedding],
) -> RagEmbeddingRun:
    """Persist an embedding run and its chunk embeddings atomically."""
    domain = run.domain
    document_id = run.document_id
    run_id = run.run_id

    run_path = _embedding_run_path(domain, document_id, run_id)
    vectors_path = _embedding_vectors_path(domain, document_id, run_id)

    vector_records = [ce.model_dump(mode="json") for ce in chunk_embeddings]
    artifact_hash = _embedding_artifact_hash(vector_records)
    run.vector_artifact_hash = artifact_hash
    run.updated_at = datetime.utcnow()

    _atomic_write(run_path, run.model_dump(mode="json"))
    _atomic_write_jsonl(vectors_path, vector_records)
    return run


def _embedding_artifact_hash(records: List[dict]) -> str:
    import hashlib, json
    lines = [json.dumps(r, separators=(",", ":"), sort_keys=True) for r in records]
    blob = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def get_embedding_run(
    domain: str, document_id: str, run_id: str
) -> Optional[RagEmbeddingRun]:
    """Fetch a single embedding run."""
    path = _embedding_run_path(domain, document_id, run_id)
    if not path.exists():
        return None
    try:
        return RagEmbeddingRun(**json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.warning("Failed to load embedding run %s: %s", run_id, exc)
        return None


def list_embedding_runs(domain: str, document_id: str) -> List[RagEmbeddingRun]:
    """List all embedding runs for a canonical document."""
    doc_dir = _embeddings_dir(domain, document_id)
    runs = []
    for path in doc_dir.glob("*.json"):
        if path.name.endswith(".vectors.jsonl"):
            continue
        try:
            runs.append(RagEmbeddingRun(**json.loads(path.read_text(encoding="utf-8"))))
        except Exception as exc:
            logger.warning("Skipping corrupt embedding run %s: %s", path, exc)
    return runs


def get_chunk_embeddings(
    domain: str,
    document_id: str,
    run_id: str,
    include_vectors: bool = False,
) -> List[RagChunkEmbedding]:
    """List chunk embeddings for a run, optionally omitting vectors."""
    path = _embedding_vectors_path(domain, document_id, run_id)
    if not path.exists():
        return []
    embeddings = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            if not include_vectors:
                data = {k: v for k, v in data.items() if k != "vector"}
            embeddings.append(RagChunkEmbedding(**data))
    except Exception as exc:
        logger.warning("Failed to load chunk embeddings for %s: %s", run_id, exc)
    return sorted(embeddings, key=lambda e: e.chunk_ordinal)


def get_chunk_embedding(
    domain: str,
    document_id: str,
    run_id: str,
    embedding_id: str,
    include_vector: bool = False,
) -> Optional[RagChunkEmbedding]:
    """Fetch a single chunk embedding by ID."""
    path = _embedding_vectors_path(domain, document_id, run_id)
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            if data.get("embedding_id") != embedding_id:
                continue
            if not include_vector:
                data = {k: v for k, v in data.items() if k != "vector"}
            return RagChunkEmbedding(**data)
    except Exception as exc:
        logger.warning("Failed to load chunk embedding %s: %s", embedding_id, exc)
    return None
```

- [ ] **Step 2: Update orchestrator to persist**

Modify `backend/app/core/rag_embeddings.py` to import and call `save_embedding_run` at the end of `run_embedding_dry_run`:

```python
from .rag_ingestion_store import (
    get_canonical_document,
    get_canonical_chunk,
    list_canonical_chunks,
    save_embedding_run,
)
```

At the end of `run_embedding_dry_run`, before returning:

```python
    # Check for existing run artifact conflicts before persisting.
    existing = get_embedding_run(domain, document_id, run_id)
    if existing is not None:
        if existing.vector_artifact_hash != _artifact_hash(...):
            raise _error(
                code="EMBEDDING_RUN_CONFLICT",
                message="A conflicting embedding run already exists.",
            )
        # Idempotent return: reload persisted embeddings.
        return existing, get_chunk_embeddings(domain, document_id, run_id, include_vectors=True)

    run.completed_at = datetime.utcnow()
    save_embedding_run(run, chunk_embeddings)
    return run, chunk_embeddings
```

Wait — `_artifact_hash` in `rag_embeddings.py` uses the same formula as `_embedding_artifact_hash` in store. Remove the duplicate in `rag_embeddings.py` and use store's helper, or keep both consistent. Better: import `_embedding_artifact_hash` from store, or move the helper to a shared module. For simplicity, import it from store.

Replace `_artifact_hash` in `rag_embeddings.py` with an import:

```python
from .rag_ingestion_store import (
    _embedding_artifact_hash,
    get_canonical_document,
    ...
)
```

And use `_embedding_artifact_hash(vector_records)`.

- [ ] **Step 3: Add persistence tests**

Add to `backend/tests/test_rag_embeddings.py`:

```python
from app.core.rag_ingestion_store import (
    get_embedding_run,
    get_chunk_embeddings,
    list_embedding_runs,
    save_embedding_run,
)


def test_save_and_retrieve_embedding_run(tmp_path, monkeypatch):
    doc, _ = _make_document(tmp_path, monkeypatch)
    run, embeddings = run_embedding_dry_run(doc.domain, doc.document_id)
    assert get_embedding_run(doc.domain, doc.document_id, run.run_id) is not None
    assert len(list_embedding_runs(doc.domain, doc.document_id)) == 1
    assert len(get_chunk_embeddings(doc.domain, doc.document_id, run.run_id, include_vectors=True)) == 1
    assert len(get_chunk_embeddings(doc.domain, doc.document_id, run.run_id, include_vectors=False)[0].vector) == 0
```

- [ ] **Step 4: Run tests**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_embeddings.py -q
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/rag_ingestion_store.py backend/app/core/rag_embeddings.py backend/tests/test_rag_embeddings.py
git commit -m "feat(rag): persist embedding runs and chunk embeddings"
```

---

### Task 5: Add router endpoints

**Files:**
- Modify: `backend/app/routers/domains.py`
- Test: `backend/tests/test_rag_embedding_endpoints.py`

**Interfaces:**
- Consumes: `run_embedding_dry_run`, store getters.
- Produces: FastAPI routes for POST dry-run and GET retrieval.

- [ ] **Step 1: Add Pydantic request/response schemas**

In `backend/app/routers/domains.py`, add near other request models:

```python
class EmbeddingDryRunRequest(BaseModel):
    dry_run: bool = True
    provider_id: str = "local_feature_hash_v1"


class EmbeddingDryRunResponse(BaseModel):
    run_id: str
    status: str
    provider_id: str
    dimensions: int
    document_chunk_count: int
    eligible_chunk_count: int
    embedded_chunk_count: int
    failed_chunk_count: int
    batch_count: int
    vector_artifact_hash: Optional[str]
    warnings: List[str]
    errors: List[dict]


class EmbeddingRunResponse(BaseModel):
    run_id: str
    document_id: str
    provider_id: str
    provider_version: str
    status: str
    dimensions: int
    document_chunk_count: int
    embedded_chunk_count: int
    failed_chunk_count: int
    batch_count: int
    vector_artifact_hash: Optional[str]
    production_approved: bool
    index_status: str
    warnings: List[str]
    errors: List[dict]
    created_at: datetime
    completed_at: Optional[datetime]


class ChunkEmbeddingListItem(BaseModel):
    embedding_id: str
    chunk_id: str
    chunk_ordinal: int
    chunk_text_hash: str
    provider_id: str
    provider_version: str
    dimensions: int
    distance_metric: str
    normalization: str
    vector_hash: str
    index_status: str
    vector: Optional[List[float]] = None
```

- [ ] **Step 2: Add routes**

Add to `backend/app/routers/domains.py` after the canonical document routes:

```python
from app.core.rag_embeddings import EmbeddingError, run_embedding_dry_run
from app.core.rag_ingestion_store import (
    get_canonical_document,
    get_chunk_embedding,
    get_chunk_embeddings,
    get_embedding_run,
    list_embedding_runs,
)


@router.post(
    "/v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/embedding-dry-run"
)
def create_embedding_dry_run(
    domain_id: str,
    document_id: str,
    request: EmbeddingDryRunRequest,
):
    if not request.dry_run:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "DRY_RUN_REQUIRED",
                "message": "Actual embedding is not enabled. Use dry_run=true.",
            },
        )
    try:
        run, _ = run_embedding_dry_run(
            domain=domain_id,
            document_id=document_id,
            provider_id=request.provider_id,
            dry_run=True,
        )
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message, "field": exc.field},
        )
    return EmbeddingDryRunResponse(
        run_id=run.run_id,
        status=run.status.value,
        provider_id=run.provider_id,
        dimensions=run.dimensions,
        document_chunk_count=run.document_chunk_count,
        eligible_chunk_count=run.eligible_chunk_count,
        embedded_chunk_count=run.embedded_chunk_count,
        failed_chunk_count=run.failed_chunk_count,
        batch_count=run.batch_count,
        vector_artifact_hash=run.vector_artifact_hash,
        warnings=run.warnings,
        errors=[e.model_dump(mode="json") for e in run.errors],
    )


@router.get(
    "/v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/embedding-runs"
)
def list_embedding_runs_endpoint(domain_id: str, document_id: str):
    document = get_canonical_document(domain_id, document_id)
    if document is None or document.domain != domain_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    runs = list_embedding_runs(domain_id, document_id)
    return [
        EmbeddingRunResponse(
            run_id=r.run_id,
            document_id=r.document_id,
            provider_id=r.provider_id,
            provider_version=r.provider_version,
            status=r.status.value,
            dimensions=r.dimensions,
            document_chunk_count=r.document_chunk_count,
            embedded_chunk_count=r.embedded_chunk_count,
            failed_chunk_count=r.failed_chunk_count,
            batch_count=r.batch_count,
            vector_artifact_hash=r.vector_artifact_hash,
            production_approved=r.production_approved,
            index_status=r.index_status.value,
            warnings=r.warnings,
            errors=[e.model_dump(mode="json") for e in r.errors],
            created_at=r.created_at,
            completed_at=r.completed_at,
        )
        for r in runs
    ]


@router.get(
    "/v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/embedding-runs/{run_id}"
)
def get_embedding_run_endpoint(domain_id: str, document_id: str, run_id: str):
    document = get_canonical_document(domain_id, document_id)
    if document is None or document.domain != domain_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    run = get_embedding_run(domain_id, document_id, run_id)
    if run is None or run.domain != domain_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    return EmbeddingRunResponse(
        run_id=run.run_id,
        document_id=run.document_id,
        provider_id=run.provider_id,
        provider_version=run.provider_version,
        status=run.status.value,
        dimensions=run.dimensions,
        document_chunk_count=run.document_chunk_count,
        embedded_chunk_count=run.embedded_chunk_count,
        failed_chunk_count=run.failed_chunk_count,
        batch_count=run.batch_count,
        vector_artifact_hash=run.vector_artifact_hash,
        production_approved=run.production_approved,
        index_status=run.index_status.value,
        warnings=run.warnings,
        errors=[e.model_dump(mode="json") for e in run.errors],
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


@router.get(
    "/v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/embedding-runs/{run_id}/embeddings"
)
def list_chunk_embeddings_endpoint(
    domain_id: str,
    document_id: str,
    run_id: str,
    include_vectors: bool = False,
):
    document = get_canonical_document(domain_id, document_id)
    if document is None or document.domain != domain_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    run = get_embedding_run(domain_id, document_id, run_id)
    if run is None or run.domain != domain_id or run.document_id != document_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    embeddings = get_chunk_embeddings(domain_id, document_id, run_id, include_vectors=include_vectors)
    return [
        ChunkEmbeddingListItem(
            embedding_id=e.embedding_id,
            chunk_id=e.chunk_id,
            chunk_ordinal=e.chunk_ordinal,
            chunk_text_hash=e.chunk_text_hash,
            provider_id=e.provider_id,
            provider_version=e.provider_version,
            dimensions=e.dimensions,
            distance_metric=e.distance_metric,
            normalization=e.normalization,
            vector_hash=e.vector_hash,
            index_status=e.index_status.value,
            vector=e.vector if include_vectors else None,
        )
        for e in embeddings
    ]
```

- [ ] **Step 3: Add endpoint tests**

Create `backend/tests/test_rag_embedding_endpoints.py`. Use the existing `client` fixture pattern from `test_rag_canonical_document_endpoints.py`. Example:

```python
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _seed_document(client, domain="legal"):
    # Use existing seed helpers or directly call store utilities.
    # For isolation, set STORAGE_PATH via monkeypatch in fixture.
    pass


def test_create_embedding_dry_run_endpoint(client):
    # seed document with chunks
    response = client.post(
        "/v1/domains/legal/rag-ingestion/documents/doc1/embedding-dry-run",
        json={"dry_run": True, "provider_id": "local_feature_hash_v1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["provider_id"] == "local_feature_hash_v1"
    assert data["dry_run"] is True  # if included


def test_create_embedding_dry_run_rejects_non_dry_run(client):
    response = client.post(
        "/v1/domains/legal/rag-ingestion/documents/doc1/embedding-dry-run",
        json={"dry_run": False},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DRY_RUN_REQUIRED"


def test_get_embedding_runs_wrong_domain(client):
    response = client.get(
        "/v1/domains/medical/rag-ingestion/documents/doc1/embedding-runs"
    )
    assert response.status_code == 404
```

For exact seeding, reuse the `_make_document` helper from `test_rag_embeddings.py` or call store utilities directly in a fixture that sets `STORAGE_PATH` to a temp directory.

- [ ] **Step 4: Run endpoint tests**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_embedding_endpoints.py -q
```
Expected: all pass after fixing seeding.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/domains.py backend/tests/test_rag_embedding_endpoints.py
git commit -m "feat(rag): add embedding dry-run endpoints"
```

---

### Task 6: Add configuration to .env.example

**Files:**
- Modify: `backend/.env.example`

- [ ] **Step 1: Add env vars**

Append to `backend/.env.example`:

```text
# Embedding dry-run configuration
RAG_EMBEDDING_DEFAULT_PROVIDER=local_feature_hash_v1
RAG_EMBEDDING_DIMENSIONS=384
RAG_EMBEDDING_BATCH_SIZE=64
RAG_EMBEDDING_VECTOR_PRECISION=8
RAG_EMBEDDING_NORM_TOLERANCE=0.00001
```

- [ ] **Step 2: Commit**

```bash
git add backend/.env.example
git commit -m "chore(config): add embedding dry-run env vars"
```

---

### Task 7: Full regression and final verification

- [ ] **Step 1: Run targeted embedding tests**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_embedding_providers.py tests/test_rag_embeddings.py tests/test_rag_embedding_endpoints.py -q --tb=short
```
Expected: all pass.

- [ ] **Step 2: Run broader RAG regression**

```bash
cd backend
.venv/Scripts/python -m pytest tests/test_rag_canonical_text.py tests/test_rag_chunking.py tests/test_rag_canonical_documents.py tests/test_rag_canonical_document_endpoints.py tests/test_rag_source_acquisition.py tests/test_rag_source_parsers.py tests/test_rag_acquisition_endpoints.py tests/test_rag_ingestion_validation.py tests/test_rag_ingestion_jobs.py tests/test_rag_ingestion_endpoints.py tests/test_engine_discovery.py tests/test_rag_pack_loader.py tests/test_rag_pack_endpoint.py tests/test_rag_activation.py tests/test_rag_activation_endpoint.py tests/test_source_pack_endpoint.py tests/test_chain_generator_source_packs.py -q --tb=short
```
Expected: all pass.

- [ ] **Step 3: Run full backend suite**

```bash
cd backend
.venv/Scripts/python -m pytest tests -q
```
Expected: 258+ passed, 0 failed.

- [ ] **Step 4: Push branch and create PR**

```bash
git push -u origin feat/rag-embedding-dry-run
gh pr create --title "feat(rag): add embedding contract and offline dry-run" --body "..." --base master
```

Wait for CI, then merge:

```bash
gh pr merge <NUMBER> --squash --delete-branch=false
```

---

## Self-Review

**Spec coverage:**
- Provider contract: Task 2.
- Offline validation algorithm: Task 2.
- Run and chunk embedding models: Task 1.
- Deterministic IDs: Task 3.
- Eligibility: Task 3.
- Endpoint: Task 5.
- Batching: Task 3 `_make_batches`.
- Vector validation: Task 3 `_validate_vectors`.
- Persistence: Task 4.
- Retrieval endpoints: Task 5.
- Idempotency/conflicts: Task 3/4 existing-run check.
- Configuration: Task 6.
- State isolation: enforced by never mutating index_status in orchestrator; tests verify.
- Tests: Tasks 2/3/5.

**Placeholder scan:** No TBD/TODO/fill-in-details.

**Type consistency:** `RagEmbeddingRun` and `RagChunkEmbedding` fields match usage across orchestrator, store, and router.
