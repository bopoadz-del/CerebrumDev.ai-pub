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
    _embedding_artifact_hash,
    get_canonical_document,
    get_chunk_embeddings,
    get_embedding_run,
    list_canonical_chunks,
    save_embedding_run,
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
        expected_chunk_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        if chunk.text_hash != expected_chunk_hash:
            errors.append(
                ValidationError(
                    code="CHUNK_TEXT_HASH_MISMATCH",
                    field="text_hash",
                    message="Chunk text hash does not match chunk text.",
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
        first = config_errors[0]
        raise _error(
            code="EMBEDDING_CONFIGURATION_INVALID",
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

    eligibility_errors = _validate_document_eligibility(document)
    if eligibility_errors:
        raise _error(
            code=eligibility_errors[0].code,
            message="Document is not eligible for embedding.",
        )

    provider = get_provider(provider_id)
    if provider is None:
        raise _error(
            code="EMBEDDING_PROVIDER_NOT_FOUND",
            message=f"Provider {provider_id} not found.",
            field="provider_id",
        )

    chunks = list_canonical_chunks(domain, document_id)
    chunk_errors = _validate_chunks(document, chunks)
    if chunk_errors:
        first = chunk_errors[0]
        raise _error(
            code=first.code,
            message=first.message,
            field=first.field,
        )

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
        production_approved=provider.production_approved,
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

    vector_records = [ce.model_dump(mode="json") for ce in chunk_embeddings]
    # Check for existing run artifact conflicts before persisting.
    existing = get_embedding_run(domain, document_id, run_id)
    if existing is not None:
        if existing.vector_artifact_hash != _embedding_artifact_hash(vector_records):
            raise _error(
                code="EMBEDDING_RUN_CONFLICT",
                message="A conflicting embedding run already exists.",
            )
        # Idempotent return: reload persisted embeddings.
        return existing, get_chunk_embeddings(domain, document_id, run_id, include_vectors=True)

    run.completed_at = datetime.utcnow()
    save_embedding_run(run, chunk_embeddings)
    return run, chunk_embeddings
