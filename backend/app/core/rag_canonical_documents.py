"""Canonical document and deterministic chunking orchestration for RAG ingestion.

Converts a successful acquisition preview into a normalized canonical text
document and deterministic chunks. No embeddings, vector-store writes, or pack
status mutation occur here.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import List, Optional, Tuple

from app.models.rag_ingestion import (
    CanonicalizationStatus,
    ChunkingStatus,
    DuplicateStatus,
    IndexStatus,
    JobStatus,
    RagAcquisitionReport,
    RagCanonicalChunk,
    RagCanonicalDocument,
    RagIngestionJob,
    ValidationError,
)

from .rag_canonical_text import (
    DEFAULT_NORMALIZATION_VERSION,
    canonical_text_hash,
    normalize_text,
)
from .rag_chunking import (
    DEFAULT_CHUNKING_VERSION,
    ChunkConfig,
    chunk_hash,
    chunk_text,
)

logger = logging.getLogger(__name__)

RAG_CANONICAL_NORMALIZATION_VERSION = os.getenv(
    "RAG_CANONICAL_NORMALIZATION_VERSION", DEFAULT_NORMALIZATION_VERSION
)
RAG_CHUNKING_ALGORITHM = os.getenv("RAG_CHUNKING_ALGORITHM", "structural-character")
RAG_CHUNKING_VERSION = os.getenv("RAG_CHUNKING_VERSION", DEFAULT_CHUNKING_VERSION)
RAG_CHUNK_TARGET_CHARACTERS = int(os.getenv("RAG_CHUNK_TARGET_CHARACTERS", "3200"))
RAG_CHUNK_MAX_CHARACTERS = int(os.getenv("RAG_CHUNK_MAX_CHARACTERS", "4000"))
RAG_CHUNK_OVERLAP_CHARACTERS = int(os.getenv("RAG_CHUNK_OVERLAP_CHARACTERS", "400"))
RAG_CHUNK_MIN_CHARACTERS = int(os.getenv("RAG_CHUNK_MIN_CHARACTERS", "200"))
RAG_CANONICAL_PREVIEW_CHARACTERS = int(
    os.getenv("RAG_CANONICAL_PREVIEW_CHARACTERS", "10000")
)


class CanonicalDocumentError(Exception):
    """Raised when canonical document creation fails."""

    def __init__(self, code: str, message: str, field: Optional[str] = None):
        self.code = code
        self.message = message
        self.field = field
        super().__init__(message)

    def to_validation_error(self) -> ValidationError:
        return ValidationError(code=self.code, field=self.field, message=self.message)


def _error(code: str, message: str, field: Optional[str] = None) -> CanonicalDocumentError:
    return CanonicalDocumentError(code=code, message=message, field=field)


def _chunk_config() -> ChunkConfig:
    cfg = ChunkConfig(
        algorithm=RAG_CHUNKING_ALGORITHM,
        version=RAG_CHUNKING_VERSION,
        target_characters=RAG_CHUNK_TARGET_CHARACTERS,
        maximum_characters=RAG_CHUNK_MAX_CHARACTERS,
        overlap_characters=RAG_CHUNK_OVERLAP_CHARACTERS,
        minimum_characters=RAG_CHUNK_MIN_CHARACTERS,
    )
    cfg.validate()
    return cfg


def _document_id(
    collection_id: str, raw_content_hash: str, normalization_version: str
) -> str:
    """Return a deterministic document identity scoped to the collection."""
    data = f"{collection_id}:{raw_content_hash}:{normalization_version}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _validate_eligibility(
    job: RagIngestionJob,
    report: RagAcquisitionReport,
) -> List[ValidationError]:
    """Return validation errors if the job/report cannot produce a canonical document."""
    errors: List[ValidationError] = []

    if job.dry_run is not True:
        errors.append(
            ValidationError(
                code="JOB_NOT_ELIGIBLE",
                field="dry_run",
                message="Only dry-run jobs can produce canonical documents.",
            )
        )
    if job.status not in (JobStatus.VALIDATED, JobStatus.QUEUED):
        errors.append(
            ValidationError(
                code="JOB_NOT_ELIGIBLE",
                field="status",
                message=f"Job status {job.status.value} is not eligible.",
            )
        )
    if job.validation_errors:
        errors.append(
            ValidationError(
                code="JOB_NOT_ELIGIBLE",
                field="validation_errors",
                message="Job has unresolved validation errors.",
            )
        )
    if job.duplicate_status == DuplicateStatus.DUPLICATE:
        errors.append(
            ValidationError(
                code="JOB_NOT_ELIGIBLE",
                field="duplicate_status",
                message="Job is marked as a duplicate.",
            )
        )

    if report.domain != job.domain:
        errors.append(
            ValidationError(
                code="ACQUISITION_LINKAGE_MISMATCH",
                field="domain",
                message="Acquisition domain does not match job domain.",
            )
        )
    if report.job_id != job.job_id:
        errors.append(
            ValidationError(
                code="ACQUISITION_LINKAGE_MISMATCH",
                field="job_id",
                message="Acquisition does not belong to this job.",
            )
        )
    if report.source_id != job.source_id:
        errors.append(
            ValidationError(
                code="ACQUISITION_LINKAGE_MISMATCH",
                field="source_id",
                message="Acquisition source does not match job source.",
            )
        )
    if report.rag_pack_id != job.rag_pack_id:
        errors.append(
            ValidationError(
                code="ACQUISITION_LINKAGE_MISMATCH",
                field="rag_pack_id",
                message="Acquisition RAG pack does not match job.",
            )
        )
    if report.collection_id != job.collection_id:
        errors.append(
            ValidationError(
                code="ACQUISITION_LINKAGE_MISMATCH",
                field="collection_id",
                message="Acquisition collection does not match job.",
            )
        )
    if report.status.value != "parsed":
        errors.append(
            ValidationError(
                code="ACQUISITION_NOT_ELIGIBLE",
                field="status",
                message="Acquisition did not complete parsing successfully.",
            )
        )
    if report.parse_status.value != "parsed":
        errors.append(
            ValidationError(
                code="ACQUISITION_NOT_ELIGIBLE",
                field="parse_status",
                message="Acquisition parse status is not parsed.",
            )
        )
    if report.duplicate_status == DuplicateStatus.DUPLICATE:
        errors.append(
            ValidationError(
                code="ACQUISITION_NOT_ELIGIBLE",
                field="duplicate_status",
                message="Acquisition is marked as a duplicate.",
            )
        )
    if not report.computed_content_hash:
        errors.append(
            ValidationError(
                code="ACQUISITION_NOT_ELIGIBLE",
                field="computed_content_hash",
                message="Acquisition is missing a computed content hash.",
            )
        )

    return errors


def create_canonical_document(
    job: RagIngestionJob,
    report: RagAcquisitionReport,
    dry_run: bool = True,
    create_chunks: bool = True,
) -> Tuple[RagCanonicalDocument, List[RagCanonicalChunk]]:
    """Create a canonical document and optional deterministic chunks.

    Returns the canonical document and a (possibly empty) list of chunks.
    """
    if not dry_run:
        raise _error(
            "DRY_RUN_REQUIRED",
            "Actual canonicalization is not enabled. Use dry_run=true.",
            "dry_run",
        )

    errors = _validate_eligibility(job, report)
    if errors:
        raise _error(
            "ACQUISITION_NOT_ELIGIBLE",
            "Acquisition is not eligible for canonical document creation.",
        )

    raw_text = report.extracted_text
    if not raw_text:
        raise _error(
            "CANONICAL_TEXT_UNAVAILABLE",
            "Acquisition report contains no extracted text.",
            "extracted_text",
        )

    normalization_version = RAG_CANONICAL_NORMALIZATION_VERSION
    try:
        canonical_text, norm_warnings = normalize_text(raw_text, normalization_version)
    except Exception as exc:
        raise _error(
            "CANONICALIZATION_FAILED",
            f"Text normalization failed: {exc}",
            "canonical_text",
        ) from exc

    if not canonical_text:
        raise _error(
            "CANONICAL_TEXT_EMPTY",
            "Normalized canonical text is empty.",
            "canonical_text",
        )

    document_id = _document_id(
        report.collection_id, report.computed_content_hash, normalization_version
    )

    # The parser's typed truncation flag, never a search of its warning prose.
    parser_truncated = bool(report.parser_truncated)

    chunking_status = ChunkingStatus.NOT_REQUESTED
    chunks: List[RagCanonicalChunk] = []
    chunk_count = 0

    if create_chunks:
        chunking_status = ChunkingStatus.PENDING
        try:
            config = _chunk_config()
            raw_chunks = chunk_text(canonical_text, config)
            for rc in raw_chunks:
                text_hash = hashlib.sha256(rc.text.encode("utf-8")).hexdigest()
                chunk_id = chunk_hash(
                    document_id,
                    config.version,
                    rc.ordinal,
                    rc.character_start,
                    rc.character_end,
                    text_hash,
                )
                chunks.append(
                    RagCanonicalChunk(
                        chunk_id=chunk_id,
                        document_id=document_id,
                        job_id=job.job_id,
                        source_id=report.source_id,
                        acquisition_id=report.acquisition_id,
                        rag_pack_id=report.rag_pack_id,
                        collection_id=report.collection_id,
                        domain=report.domain,
                        ordinal=rc.ordinal,
                        text=rc.text,
                        text_hash=text_hash,
                        character_start=rc.character_start,
                        character_end=rc.character_end,
                        character_count=len(rc.text),
                        overlap_from_previous=rc.overlap_from_previous,
                        structural_type=rc.structural_type,
                        heading=rc.heading,
                        chunking_algorithm=config.algorithm,
                        chunking_version=config.version,
                        target_characters=config.target_characters,
                        maximum_characters=config.maximum_characters,
                        overlap_characters=config.overlap_characters,
                    )
                )
            chunk_count = len(chunks)
            chunking_status = ChunkingStatus.CHUNKED
        except Exception as exc:
            raise _error(
                "CHUNKING_FAILED",
                f"Deterministic chunking failed: {exc}",
                "chunks",
            ) from exc

    document = RagCanonicalDocument(
        document_id=document_id,
        job_id=job.job_id,
        source_id=report.source_id,
        acquisition_id=report.acquisition_id,
        rag_pack_id=report.rag_pack_id,
        collection_id=report.collection_id,
        domain=report.domain,
        source_uri=report.source_uri,
        final_source_uri=report.final_source_uri,
        title="",  # populated from source record if available by caller
        publisher=None,
        source_class=report.source_class,
        content_type=report.content_type,
        raw_content_hash=report.computed_content_hash,
        canonical_text_hash=canonical_text_hash(canonical_text),
        normalization_algorithm="canonical-text",
        normalization_version=normalization_version,
        parser_id=report.parser_id,
        parser_version=report.parser_version,
        character_count=len(canonical_text),
        line_count=canonical_text.count("\n") + 1,
        chunk_count=chunk_count,
        parser_truncated=parser_truncated,
        canonicalization_status=CanonicalizationStatus.CANONICALIZED,
        chunking_status=chunking_status,
        index_status=IndexStatus.NOT_INDEXED,
        untrusted_content=True,
    )

    return document, chunks, canonical_text
