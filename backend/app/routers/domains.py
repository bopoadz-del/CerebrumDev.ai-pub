from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from ..core.domain_loader import list_available_domains
from ..core.rag_activation import build_rag_activation_status
from ..core.rag_canonical_documents import (
    CanonicalDocumentError,
    create_canonical_document,
)
from ..core.rag_embeddings import EmbeddingError, run_embedding_dry_run
from ..core.rag_ingestion_store import (
    get_acquisition_report,
    get_canonical_chunk,
    get_canonical_document,
    get_canonical_text,
    get_chunk_embeddings,
    get_embedding_run,
    get_job,
    get_vector_index_records,
    get_vector_index_run,
    list_acquisition_reports,
    list_canonical_chunks,
    list_canonical_documents,
    list_embedding_runs,
    list_jobs,
    list_vector_index_runs,
    save_canonical_document,
    save_job,
    save_source_record,
)
from ..core.rag_vector_indexing import VectorIndexError, run_vector_index_dry_run
from ..core.rag_ingestion_validation import create_ingestion_job
from ..core.rag_pack_loader import (
    RagPackLoaderError,
    list_rag_packs,
)
from ..core.rag_source_acquisition import (
    AcquisitionError,
    run_acquisition_preview,
)
from ..core.source_pack_loader import (
    SourcePackLoaderError,
    list_source_packs,
)
from ..core.virgin_shelf_loader import (
    VirginShelfLoaderError,
    list_virgin_domains,
)
from ..models.rag_ingestion import RagSourceRecord, SourceClass

router = APIRouter()


class CreateIngestionJobRequest(BaseModel):
    """Request body for creating a dry-run RAG ingestion job."""

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
    content_hash: Optional[str] = None
    external_document_id: Optional[str] = None
    dry_run: bool = True
    queue: bool = False


class AcquisitionPreviewRequest(BaseModel):
    """Request body for running an acquisition preview."""

    dry_run: bool = True
    parse: bool = True


class CanonicalDocumentRequest(BaseModel):
    """Request body for creating a canonical document."""

    dry_run: bool = True
    create_chunks: bool = True


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


@router.get("/")
async def get_domains():
    """List available domain kits from the Cerebrum store."""
    domains = await list_available_domains()
    if not domains:
        raise HTTPException(status_code=503, detail="Domain store unreachable")
    return {"domains": domains}


@router.get("/virgin")
async def get_virgin_domains():
    """List Domain Virgin Edition manifests from the Cerebrum-Blocks shelf.

    Read-only: exposes what the store engine advertises as its minimal clean
    domain editions. Does not affect runtime block availability.
    """
    try:
        editions = list_virgin_domains()
    except VirginShelfLoaderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"shelf_id": "virgin_domains", "editions": editions}


@router.get("/source-packs")
async def get_source_packs():
    """List Domain Source Pack metadata from the Cerebrum-Blocks shelf.

    Read-only: exposes how each domain should think, respond, and wire blocks.
    Does not affect runtime chain generation.
    """
    try:
        packs = list_source_packs()
    except SourcePackLoaderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"shelf_id": "source_packs", "packs": packs}


@router.get("/rag-packs")
async def get_rag_packs():
    """List Prebuilt Domain RAG Pack metadata from the Cerebrum-Blocks shelf.

    Read-only: exposes what prebuilt domain knowledge collections exist.
    Does not perform ingestion, create embeddings, or affect runtime behavior.
    """
    try:
        packs = list_rag_packs()
    except RagPackLoaderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"shelf_id": "rag_packs", "packs": packs}


@router.get("/{domain_id}/rag-activation")
async def get_rag_activation(domain_id: str):
    """Return the activation/status contract for a domain's prebuilt RAG pack.

    Read-only metadata status. Does not ingest documents, create embeddings,
    or change chain generation.
    """
    try:
        return build_rag_activation_status(domain_id)
    except RagPackLoaderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{domain_id}/rag-ingestion/jobs")
async def create_rag_ingestion_job(
    domain_id: str, request: CreateIngestionJobRequest
):
    """Create a dry-run RAG ingestion job (validation + optional queue).

    Actual ingestion is not enabled. Use ``dry_run=true`` to validate or queue
    a proposed source record without downloading, parsing, embedding, or writing
    to a vector store.
    """
    if not request.dry_run:
        raise HTTPException(
            status_code=409,
            detail="Actual ingestion is not enabled. Use dry_run=true.",
        )

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
    jobs = list_jobs(
        domain_id, status=status, rag_pack_id=rag_pack_id, collection_id=collection_id
    )
    return {"domain": domain_id, "jobs": [j.model_dump(mode="json") for j in jobs]}


@router.post("/{domain_id}/rag-ingestion/jobs/{job_id}/acquisition-preview")
async def create_acquisition_preview(
    domain_id: str,
    job_id: str,
    request: AcquisitionPreviewRequest,
):
    """Fetch and preview-parse the source for an eligible dry-run ingestion job.

    Does not chunk, embed, index, or persist raw source bytes. Actual
    acquisition is rejected; use ``dry_run=true``.
    """
    try:
        report = run_acquisition_preview(
            domain_id=domain_id,
            job_id=job_id,
            dry_run=request.dry_run,
            parse=request.parse,
        )
    except AcquisitionError as exc:
        status_code = 409 if exc.code == "DRY_RUN_REQUIRED" else 422
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message, "field": exc.field},
        ) from exc
    return report.model_dump(mode="json")


@router.get("/{domain_id}/rag-ingestion/jobs/{job_id}/acquisition-previews")
async def list_acquisition_previews(domain_id: str, job_id: str):
    """List acquisition preview reports for a job, scoped to its domain."""
    reports = list_acquisition_reports(domain_id, job_id=job_id)
    return {"domain": domain_id, "job_id": job_id, "acquisitions": [r.model_dump(mode="json") for r in reports]}


@router.get(
    "/{domain_id}/rag-ingestion/jobs/{job_id}/acquisition-previews/{acquisition_id}"
)
async def get_acquisition_preview(domain_id: str, job_id: str, acquisition_id: str):
    """Retrieve a single acquisition preview report, scoped to its domain."""
    report = get_acquisition_report(domain_id, acquisition_id)
    if report is None or report.job_id != job_id:
        raise HTTPException(status_code=404, detail="Acquisition preview not found")
    return report.model_dump(mode="json")


@router.post(
    "/{domain_id}/rag-ingestion/jobs/{job_id}/acquisition-previews/{acquisition_id}/canonical-document"
)
async def create_canonical_document_endpoint(
    domain_id: str,
    job_id: str,
    acquisition_id: str,
    request: CanonicalDocumentRequest,
):
    """Create a canonical document and optional deterministic chunks.

    The canonical text comes from the trusted parser output stored on the
    acquisition report, not from the API client or the bounded preview.
    """
    job = get_job(domain_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    report = get_acquisition_report(domain_id, acquisition_id)
    if report is None or report.job_id != job_id:
        raise HTTPException(status_code=404, detail="Acquisition preview not found")

    try:
        document, chunks, canonical_text = create_canonical_document(
            job=job,
            report=report,
            dry_run=request.dry_run,
            create_chunks=request.create_chunks,
        )
    except CanonicalDocumentError as exc:
        status_code = 409 if exc.code == "DRY_RUN_REQUIRED" else 422
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message, "field": exc.field},
        ) from exc

    # Idempotency: if document already exists with matching identity and config,
    # return the existing persisted document without overwriting.
    existing = get_canonical_document(domain_id, document.document_id)
    if existing is not None:
        if (
            existing.normalization_version != document.normalization_version
            or existing.chunking_status != document.chunking_status
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CANONICAL_DOCUMENT_CONFLICT",
                    "message": "A canonical document already exists with incompatible configuration.",
                },
            )
        return existing.model_dump(mode="json")

    save_canonical_document(document, canonical_text, chunks)
    return document.model_dump(mode="json")


@router.get("/{domain_id}/rag-ingestion/documents")
async def list_canonical_documents_endpoint(
    domain_id: str,
    rag_pack_id: Optional[str] = Query(None),
    collection_id: Optional[str] = Query(None),
    job_id: Optional[str] = Query(None),
    source_id: Optional[str] = Query(None),
    canonicalization_status: Optional[str] = Query(None),
    chunking_status: Optional[str] = Query(None),
):
    """List canonical documents for a domain, with optional filters."""
    documents = list_canonical_documents(
        domain_id,
        rag_pack_id=rag_pack_id,
        collection_id=collection_id,
        job_id=job_id,
        source_id=source_id,
        canonicalization_status=canonicalization_status,
        chunking_status=chunking_status,
    )
    return {
        "domain": domain_id,
        "documents": [d.model_dump(mode="json") for d in documents],
    }


@router.get("/{domain_id}/rag-ingestion/documents/{document_id}")
async def get_canonical_document_endpoint(domain_id: str, document_id: str):
    """Retrieve a canonical document's metadata, scoped to its domain."""
    document = get_canonical_document(domain_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Canonical document not found")
    return document.model_dump(mode="json")


@router.get("/{domain_id}/rag-ingestion/documents/{document_id}/text")
async def get_canonical_document_text(domain_id: str, document_id: str):
    """Retrieve the canonical text for a document, scoped to its domain."""
    document = get_canonical_document(domain_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Canonical document not found")
    text = get_canonical_text(domain_id, document_id)
    if text is None:
        raise HTTPException(status_code=404, detail="Canonical text not found")
    return text


@router.get("/{domain_id}/rag-ingestion/documents/{document_id}/chunks")
async def list_canonical_chunks_endpoint(domain_id: str, document_id: str):
    """List chunks for a canonical document in ordinal order."""
    document = get_canonical_document(domain_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Canonical document not found")
    chunks = list_canonical_chunks(domain_id, document_id)
    return {
        "domain": domain_id,
        "document_id": document_id,
        "chunks": [c.model_dump(mode="json") for c in chunks],
    }


@router.get("/{domain_id}/rag-ingestion/documents/{document_id}/chunks/{chunk_id}")
async def get_canonical_chunk_endpoint(
    domain_id: str, document_id: str, chunk_id: str
):
    """Retrieve a single chunk, scoped to its document and domain."""
    document = get_canonical_document(domain_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Canonical document not found")
    chunk = get_canonical_chunk(domain_id, document_id, chunk_id)
    if chunk is None or chunk.document_id != document_id:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunk.model_dump(mode="json")


@router.post(
    "/{domain_id}/rag-ingestion/documents/{document_id}/embedding-dry-run"
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
    "/{domain_id}/rag-ingestion/documents/{document_id}/embedding-runs"
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
    "/{domain_id}/rag-ingestion/documents/{document_id}/embedding-runs/{run_id}"
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
    "/{domain_id}/rag-ingestion/documents/{document_id}/embedding-runs/{run_id}/embeddings"
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
