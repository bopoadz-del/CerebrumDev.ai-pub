"""Validation service for proposed RAG ingestion sources and jobs.

This module enforces governance rules defined by a RAG pack's
``source_policy`` before any document is downloaded, parsed, chunked,
embedded, or written to a vector store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import List

from app.core.rag_ingestion_store import list_source_records
from app.core.rag_pack_loader import get_rag_pack
from app.models.rag_ingestion import (
    DuplicateStatus,
    JobStatus,
    RagIngestionJob,
    RagSourceRecord,
    ValidationError,
)


@dataclass
class ValidationResult:
    """Outcome of validating a proposed source record."""

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
    """Normalize a URI for duplicate comparison.

    Drops the fragment and trailing slash and lower-cases the result.
    """
    return re.sub(r"#.*$", "", uri.rstrip("/")).lower()


def validate_source_record(record: RagSourceRecord) -> ValidationResult:
    """Validate a proposed source record against its RAG pack policy.

    Returns a ``ValidationResult`` containing machine-readable errors.
    """
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
                message=(
                    f"Source class {record.source_class.value} is not allowed by "
                    "this pack's source_policy."
                ),
            )
        )

    precluded = set(source_policy.get("precluded_source_classes", []))
    if record.source_class.value in precluded:
        errors.append(
            ValidationError(
                code="SOURCE_CLASS_PRECLUDED",
                field="source_class",
                message=(
                    f"Source class {record.source_class.value} is explicitly "
                    "precluded by this pack."
                ),
            )
        )

    if source_policy.get("requires_source_record"):
        if not record.title:
            errors.append(
                ValidationError(
                    code="TITLE_REQUIRED",
                    field="title",
                    message="Source record title is required.",
                )
            )
        if not record.source_uri:
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

    # Duplicate detection scoped to the target collection.
    existing = list_source_records(record.domain, record.collection_id)
    normalized = _normalize_uri(record.source_uri)

    for src in existing:
        if src.source_id == record.source_id:
            continue
        if src.content_hash and src.content_hash == record.content_hash:
            errors.append(
                ValidationError(
                    code="DUPLICATE_CONTENT_HASH",
                    field="content_hash",
                    message=(
                        f"Duplicate content_hash in collection {record.collection_id}."
                    ),
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
    """Validate a source record and create an auditable ingestion job.

    The returned job has status ``validated`` or ``validation_failed``.
    If ``queue`` is true and validation passes, the status is ``queued``.
    """
    result = validate_source_record(record)
    status = JobStatus.VALIDATED if result.valid else JobStatus.VALIDATION_FAILED
    duplicate_status = (
        DuplicateStatus.UNIQUE if result.valid else DuplicateStatus.NOT_CHECKED
    )

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
