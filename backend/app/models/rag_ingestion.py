"""Pydantic models for RAG ingestion source records and jobs.

This module defines the persistent metadata layer that sits between RAG pack
metadata and future document ingestion. No documents, chunks, embeddings, or
vector-store writes happen here.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class SourceClass(str, Enum):
    """Source class values that may appear in a source record.

    A RAG pack's ``source_policy`` determines which of these are allowed or
    precluded for that pack.
    """

    PUBLIC_DOMAIN = "public_domain"
    OPEN_LICENSE = "open_license"
    OFFICIAL_STATUTE_OR_REGULATION = "official_statute_or_regulation"
    OFFICIAL_GUIDANCE = "official_guidance"
    PLATFORM_CURATED_TEMPLATE = "platform_curated_template"
    PRIVATE_ENTERPRISE_DATA = "private_enterprise_data"
    CONFIDENTIAL_CLIENT_DATA = "confidential_client_data"
    COPYRIGHTED_COMMERCIAL_CONTENT_WITHOUT_LICENSE = "copyrighted_commercial_content_without_license"
    USER_UPLOADED_PROJECT_RECORDS = "user_uploaded_project_records"
    UNKNOWN_LICENSE = "unknown_license"


class JobStatus(str, Enum):
    """Lifecycle status for an ingestion job."""

    DRAFT = "draft"
    VALIDATION_FAILED = "validation_failed"
    VALIDATED = "validated"
    QUEUED = "queued"
    INGESTING = "ingesting"
    INDEXED = "indexed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DuplicateStatus(str, Enum):
    """Result of a document-level duplicate check."""

    NOT_CHECKED = "not_checked"
    UNIQUE = "unique"
    DUPLICATE = "duplicate"


class ValidationError(BaseModel):
    """Machine-readable validation error returned by the ingestion validator."""

    code: str
    field: Optional[str] = None
    message: str


class RagSourceRecord(BaseModel):
    """A proposed source document to be ingested into a RAG collection."""

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
    content_hash: Optional[str] = None
    external_document_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RagIngestionJob(BaseModel):
    """An auditable ingestion job linking a source record to a RAG pack."""

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


class AcquisitionStatus(str, Enum):
    """Lifecycle status for an acquisition attempt."""

    PENDING = "pending"
    FETCHING = "fetching"
    FETCHED = "fetched"
    DUPLICATE = "duplicate"
    PARSED = "parsed"
    FAILED = "failed"


class ParseStatus(str, Enum):
    """Parsing outcome recorded in an acquisition report."""

    NOT_REQUESTED = "not_requested"
    NOT_STARTED = "not_started"
    PARSED = "parsed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class RagAcquisitionReport(BaseModel):
    """Audit report for a safe source acquisition and parsing preview.

    No raw source bytes or full extracted text are stored here — only bounded
    metadata, a preview, warnings, and structured errors.
    """

    acquisition_id: str = Field(default_factory=lambda: str(uuid4()))
    job_id: str
    source_id: str
    rag_pack_id: str
    collection_id: str
    domain: str
    source_uri: str
    final_source_uri: Optional[str] = None
    status: AcquisitionStatus = AcquisitionStatus.PENDING
    http_status: Optional[int] = None
    redirect_count: int = 0
    source_class: SourceClass = SourceClass.PUBLIC_DOMAIN
    content_type: Optional[str] = None
    content_length_bytes: Optional[int] = None
    computed_content_hash: Optional[str] = None
    duplicate_status: DuplicateStatus = DuplicateStatus.NOT_CHECKED
    duplicate_match_source_id: Optional[str] = None
    parser_id: Optional[str] = None
    parser_version: Optional[str] = None
    parse_status: ParseStatus = ParseStatus.NOT_REQUESTED
    page_count: Optional[int] = None
    extracted_characters: int = 0
    extracted_text: str = ""
    text_preview: str = ""
    warnings: List[str] = Field(default_factory=list)
    #: The parser cut the extracted text at its character bound.
    parser_truncated: bool = False
    errors: List[ValidationError] = Field(default_factory=list)
    raw_artifact_persisted: bool = False
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CanonicalizationStatus(str, Enum):
    """Lifecycle status for canonical document creation."""

    PENDING = "pending"
    CANONICALIZED = "canonicalized"
    FAILED = "failed"


class ChunkingStatus(str, Enum):
    """Chunking outcome for a canonical document."""

    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    CHUNKED = "chunked"
    FAILED = "failed"


class IndexStatus(str, Enum):
    """Indexing state for documents and chunks."""

    NOT_INDEXED = "not_indexed"


class EmbeddingRunStatus(str, Enum):
    """Lifecycle status for an embedding dry-run."""

    PENDING = "pending"
    VALIDATING = "validating"
    EMBEDDING = "embedding"
    COMPLETED = "completed"
    FAILED = "failed"


class VectorIndexRunStatus(str, Enum):
    """Lifecycle status for a vector-index dry-run."""

    PENDING = "pending"
    VALIDATING = "validating"
    INDEXING = "indexing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


class ActivationStatus(str, Enum):
    """Activation state for a vector index."""

    INACTIVE = "inactive"


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


class RagCanonicalDocument(BaseModel):
    """A persisted, normalized canonical text document from a governed source."""

    document_id: str
    job_id: str
    source_id: str
    acquisition_id: str
    rag_pack_id: str
    collection_id: str
    domain: str
    source_uri: str
    final_source_uri: Optional[str] = None
    title: str
    publisher: Optional[str] = None
    source_class: SourceClass
    content_type: Optional[str] = None
    raw_content_hash: str
    canonical_text_hash: str
    normalization_algorithm: str
    normalization_version: str
    parser_id: Optional[str] = None
    parser_version: Optional[str] = None
    character_count: int = 0
    line_count: int = 0
    chunk_count: int = 0
    parser_truncated: bool = False
    canonicalization_status: CanonicalizationStatus = CanonicalizationStatus.PENDING
    chunking_status: ChunkingStatus = ChunkingStatus.NOT_REQUESTED
    index_status: IndexStatus = IndexStatus.NOT_INDEXED
    untrusted_content: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_error: Optional[str] = None


class RagCanonicalChunk(BaseModel):
    """A deterministic structural chunk of a canonical document."""

    chunk_id: str
    document_id: str
    job_id: str
    source_id: str
    acquisition_id: str
    rag_pack_id: str
    collection_id: str
    domain: str
    ordinal: int
    text: str
    text_hash: str
    character_start: int
    character_end: int
    character_count: int
    overlap_from_previous: int = 0
    structural_type: Optional[str] = None
    heading: Optional[str] = None
    chunking_algorithm: str
    chunking_version: str
    target_characters: int
    maximum_characters: int
    overlap_characters: int
    index_status: IndexStatus = IndexStatus.NOT_INDEXED
    untrusted_content: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RagVectorIndexRun(BaseModel):
    """Audit record for a vector-index dry-run over an embedding run."""

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
    """A deterministic vector-store record produced from a chunk embedding."""

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
    metadata: Dict[str, str] = Field(default_factory=dict)
    untrusted_content: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
