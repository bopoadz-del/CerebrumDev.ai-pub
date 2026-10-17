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
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    excluded = {"created_at"}
    lines = [
        json.dumps(
            {k: v for k, v in r.items() if k not in excluded},
            separators=(",", ":"),
            sort_keys=True,
        )
        for r in records
    ]
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
            "chunk_ordinal": str(chunk.ordinal),
            "source_id": document.source_id,
            "job_id": document.job_id,
            "content_type": document.content_type or "",
            "source_class": document.source_class.value if document.source_class else "",
            "title": document.title,
            "publisher": document.publisher or "",
            "raw_content_hash": document.raw_content_hash,
            "canonical_text_hash": document.canonical_text_hash,
            "parser_id": document.parser_id or "",
            "parser_version": document.parser_version or "",
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
        "provider_id": None,
        "provider_version": None,
        "dimensions": index_run.dimensions,
        "distance_metric": index_run.distance_metric,
        "normalization": None,
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

    storage_root = Path(os.getenv("STORAGE_PATH", "./storage"))
    index_spec = IndexSpec(
        index_id=index_id,
        document_id=document.document_id,
        domain=domain,
        base_path=storage_root
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
    validation_errors = adapter.validate_index(index_spec)
    if validation_errors:
        index_run.status = VectorIndexRunStatus.FAILED
        index_run.last_error = "; ".join(validation_errors)
        raise _error(
            code="VECTOR_INDEX_VALIDATION_FAILED",
            message="; ".join(validation_errors),
        )

    index_run.status = VectorIndexRunStatus.COMPLETED
    index_run.completed_at = datetime.utcnow()
    save_vector_index_run(index_run)
    return index_run, index_records
