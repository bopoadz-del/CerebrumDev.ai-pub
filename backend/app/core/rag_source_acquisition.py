"""Safe source acquisition and parsing preview for RAG ingestion jobs.

This module fetches approved HTTPS sources, verifies their SHA-256 content hash,
detects document-level duplicates, and produces bounded parsing previews. It does
not create chunks, embeddings, or vector-store writes, and it does not persist
raw source documents.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import socket
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx

from app.models.rag_ingestion import (
    AcquisitionStatus,
    DuplicateStatus,
    JobStatus,
    ParseStatus,
    RagAcquisitionReport,
    RagIngestionJob,
    RagSourceRecord,
    ValidationError,
)

from .rag_ingestion_store import (
    get_job,
    get_source_record,
    list_source_records,
    save_acquisition_report,
    save_source_record,
)
from .rag_ingestion_validation import validate_source_record
from .rag_source_parsers import parse_bytes

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 25 * 1024 * 1024  # 25 MiB
DEFAULT_MAX_REDIRECTS = 3
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0

RAG_ACQUISITION_MAX_BYTES = int(
    os.getenv("RAG_ACQUISITION_MAX_BYTES", str(DEFAULT_MAX_BYTES))
)
RAG_ACQUISITION_MAX_REDIRECTS = int(
    os.getenv("RAG_ACQUISITION_MAX_REDIRECTS", str(DEFAULT_MAX_REDIRECTS))
)
RAG_ACQUISITION_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("RAG_ACQUISITION_CONNECT_TIMEOUT_SECONDS", str(DEFAULT_CONNECT_TIMEOUT))
)
RAG_ACQUISITION_READ_TIMEOUT_SECONDS = float(
    os.getenv("RAG_ACQUISITION_READ_TIMEOUT_SECONDS", str(DEFAULT_READ_TIMEOUT))
)
RAG_PARSE_MAX_PAGES = int(os.getenv("RAG_PARSE_MAX_PAGES", "250"))
RAG_PARSE_MAX_CHARACTERS = int(os.getenv("RAG_PARSE_MAX_CHARACTERS", "200000"))
RAG_PARSE_PREVIEW_CHARACTERS = int(os.getenv("RAG_PARSE_PREVIEW_CHARACTERS", "10000"))

_ALLOWED_SCHEMES = {"https"}
_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/html",
    "application/xhtml+xml",
}

# Cloud metadata endpoints that must never be reachable.
_CLOUD_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.azure.internal",
    "169.254.170.2",
}


class AcquisitionError(Exception):
    """Raised when a source cannot be acquired safely."""

    def __init__(self, code: str, message: str, field: Optional[str] = None):
        self.code = code
        self.message = message
        self.field = field
        super().__init__(message)

    def to_validation_error(self) -> ValidationError:
        return ValidationError(code=self.code, field=self.field, message=self.message)


@dataclass
class FetchResult:
    """Result of a safe HTTP fetch."""

    status_code: int
    content_type: Optional[str]
    content_length: Optional[int]
    final_url: str
    redirect_count: int
    raw_bytes: bytes


def _error(code: str, message: str, field: Optional[str] = None) -> AcquisitionError:
    return AcquisitionError(code=code, message=message, field=field)


def _normalize_uri(uri: str) -> str:
    return re.sub(r"#.*$", "", uri.rstrip("/")).lower()


def _is_private_or_blocked_host(hostname: str) -> Tuple[bool, Optional[str]]:
    """Return (blocked, reason) for a hostname."""
    if not hostname:
        return True, "empty hostname"

    host_lower = hostname.lower()
    if host_lower in _CLOUD_METADATA_HOSTS:
        return True, "cloud metadata endpoint"
    if host_lower in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True, "loopback/localhost"

    try:
        addr = ipaddress.ip_address(host_lower)
        if addr.is_loopback:
            return True, "loopback address"
        if addr.is_private:
            return True, "private address"
        if addr.is_link_local:
            return True, "link-local address"
        if addr.is_multicast:
            return True, "multicast address"
        if addr.is_reserved:
            return True, "reserved address"
        if addr.is_unspecified:
            return True, "unspecified address"
        return False, None
    except ValueError:
        # Hostname, not an IP literal. Resolve and check returned addresses.
        pass

    try:
        info = socket.getaddrinfo(host_lower, None)
    except socket.gaierror as exc:
        return True, f"cannot resolve hostname: {exc}"

    for item in info:
        addr = item[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or str(ip) in _CLOUD_METADATA_HOSTS
        ):
            return True, f"resolved to blocked address {addr}"
    return False, None


def _public_ips_for_host(hostname: str) -> Set[str]:
    """Resolve *hostname* and return only addresses that pass the SSRF check."""
    blocked, _reason = _is_private_or_blocked_host(hostname)
    if blocked:
        return set()
    try:
        info = socket.getaddrinfo(hostname.lower(), None)
    except socket.gaierror:
        return set()
    ips: Set[str] = set()
    for item in info:
        addr = item[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or str(ip) in _CLOUD_METADATA_HOSTS
        ):
            continue
        ips.add(str(ip))
    return ips


@contextmanager
def _pin_resolved_ips(hostname: str, pinned: Set[str]) -> Iterator[None]:
    """Force getaddrinfo for *hostname* to the IPs we already validated.

    Closes the DNS-rebinding window between validate and connect: httpx
    cannot resolve a later private address for the same name.
    """
    orig = socket.getaddrinfo

    def _pinned(host, port, *args, **kwargs):
        if host and str(host).lower() == hostname.lower():
            results = []
            for ip in pinned:
                family = socket.AF_INET6 if ":" in ip else socket.AF_INET
                sock_port = port if port is not None else 0
                results.append((family, socket.SOCK_STREAM, 0, "", (ip, sock_port)))
            if results:
                return results
        return orig(host, port, *args, **kwargs)

    socket.getaddrinfo = _pinned  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = orig


def validate_source_uri(uri: str) -> None:
    """Validate a source URI for safe acquisition.

    Raises AcquisitionError if the scheme, credentials, host, or resolved address
    is not allowed.
    """
    if not uri:
        raise _error("SOURCE_URI_INVALID", "Source URI is required.", "source_uri")

    parsed = urlparse(uri)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise _error(
            "SOURCE_URI_INVALID",
            f"Scheme '{parsed.scheme}' is not allowed. Only https is supported.",
            "source_uri",
        )

    if parsed.username is not None or parsed.password is not None:
        raise _error(
            "SOURCE_URI_INVALID",
            "URLs containing embedded credentials are not allowed.",
            "source_uri",
        )

    hostname = parsed.hostname
    if not hostname:
        raise _error("SOURCE_URI_INVALID", "URL is missing a hostname.", "source_uri")

    blocked, reason = _is_private_or_blocked_host(hostname)
    if blocked:
        raise _error(
            "SOURCE_URI_BLOCKED",
            f"The source URI resolves to a prohibited network address: {reason}.",
            "source_uri",
        )


def _build_httpx_client() -> httpx.Client:
    """Return a locked-down httpx client for source acquisition."""
    timeout = httpx.Timeout(
        connect=RAG_ACQUISITION_CONNECT_TIMEOUT_SECONDS,
        read=RAG_ACQUISITION_READ_TIMEOUT_SECONDS,
    )
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        verify=True,
        trust_env=False,  # do not inherit proxy credentials unexpectedly
    )


def _content_type_from_response(response: httpx.Response) -> Optional[str]:
    """Return the normalized content type from an httpx response."""
    content_type = response.headers.get("content-type")
    if content_type:
        return content_type.split(";")[0].strip().lower()
    return None


def _check_content_length(header_value: Optional[str], max_bytes: int) -> None:
    """Reject oversized Content-Length before streaming the body."""
    if not header_value:
        return
    try:
        length = int(header_value)
    except ValueError:
        return
    if length > max_bytes:
        raise _error(
            "SOURCE_TOO_LARGE",
            f"Content-Length {length} exceeds maximum {max_bytes} bytes.",
            "source_uri",
        )


def _stream_body(
    response: httpx.Response, max_bytes: int
) -> Tuple[bytes, Optional[int]]:
    """Stream the response body, aborting if the byte limit is exceeded."""
    chunks: List[bytes] = []
    total = 0
    try:
        for chunk in response.iter_bytes(chunk_size=64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise _error(
                    "SOURCE_TOO_LARGE",
                    f"Stream exceeded maximum {max_bytes} bytes.",
                    "source_uri",
                )
            chunks.append(chunk)
    finally:
        response.close()
    return b"".join(chunks), total


def _fetch_with_redirect_guard(
    client: httpx.Client,
    url: str,
    max_redirects: int,
    max_bytes: int,
    redirect_count: int = 0,
) -> FetchResult:
    """Fetch a URL, validating each redirect target."""
    validate_source_uri(url)
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    pinned = _public_ips_for_host(hostname) if hostname else set()
    if hostname and not pinned:
        raise _error(
            "SOURCE_URI_BLOCKED",
            "The source URI has no pinned public address.",
            "source_uri",
        )

    try:
        with _pin_resolved_ips(hostname, pinned):
            response = client.get(
                url, headers={"Accept": ", ".join(_ALLOWED_CONTENT_TYPES)}
            )
    except httpx.TimeoutException as exc:
        raise _error("SOURCE_TIMEOUT", f"Request timed out: {exc}", "source_uri") from exc
    except httpx.TransportError as exc:
        raise _error(
            "SOURCE_URI_BLOCKED", f"Cannot reach source: {exc}", "source_uri"
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise _error(
            "SOURCE_HTTP_ERROR",
            f"Source returned HTTP {exc.response.status_code}.",
            "source_uri",
        ) from exc

    if response.status_code in (301, 302, 303, 307, 308):
        if redirect_count >= max_redirects:
            raise _error(
                "SOURCE_TOO_MANY_REDIRECTS",
                f"Exceeded maximum {max_redirects} redirects.",
                "source_uri",
            )
        location = response.headers.get("location")
        if not location:
            raise _error(
                "SOURCE_REDIRECT_BLOCKED",
                "Redirect response missing Location header.",
                "source_uri",
            )
        location = urljoin(url, location)
        # Validate redirect target before following.
        try:
            validate_source_uri(location)
        except AcquisitionError as exc:
            raise _error(
                "SOURCE_REDIRECT_BLOCKED",
                f"Redirect target is not allowed: {exc.message}",
                "source_uri",
            ) from exc
        return _fetch_with_redirect_guard(
            client, location, max_redirects, max_bytes, redirect_count + 1
        )

    if response.status_code < 200 or response.status_code >= 300:
        raise _error(
            "SOURCE_HTTP_ERROR",
            f"Source returned HTTP {response.status_code}.",
            "source_uri",
        )

    _check_content_length(response.headers.get("content-length"), max_bytes)
    content_type = _content_type_from_response(response)
    raw_bytes, total = _stream_body(response, max_bytes)

    return FetchResult(
        status_code=response.status_code,
        content_type=content_type,
        content_length=total,
        final_url=str(response.url),
        redirect_count=redirect_count,
        raw_bytes=raw_bytes,
    )


def fetch_source(
    uri: str, client: Optional[httpx.Client] = None
) -> FetchResult:
    """Securely fetch an HTTPS source with SSRF protection and size limits."""
    validate_source_uri(uri)
    own_client = client is None
    if own_client:
        client = _build_httpx_client()
    try:
        return _fetch_with_redirect_guard(
            client,
            uri,
            max_redirects=RAG_ACQUISITION_MAX_REDIRECTS,
            max_bytes=RAG_ACQUISITION_MAX_BYTES,
        )
    finally:
        if own_client:
            client.close()


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_content_type(content_type: Optional[str]) -> None:
    if content_type is None:
        raise _error(
            "UNSUPPORTED_CONTENT_TYPE",
            "Response did not include a Content-Type header.",
            "source_uri",
        )
    normalized = content_type.split(";")[0].strip().lower()
    if normalized not in _ALLOWED_CONTENT_TYPES:
        raise _error(
            "UNSUPPORTED_CONTENT_TYPE",
            f"Content type '{content_type}' is not supported.",
            "source_uri",
        )


def _detect_duplicate_in_collection(
    record: RagSourceRecord,
    computed_hash: str,
    final_uri: str,
) -> Tuple[DuplicateStatus, Optional[str]]:
    """Return (duplicate_status, matching_source_id) scoped to the collection."""
    existing = list_source_records(record.domain, record.collection_id)
    normalized = _normalize_uri(final_uri)
    for src in existing:
        if src.source_id == record.source_id:
            continue
        if src.content_hash and src.content_hash == computed_hash:
            return DuplicateStatus.DUPLICATE, src.source_id
        if (
            src.external_document_id
            and src.external_document_id == record.external_document_id
        ):
            return DuplicateStatus.DUPLICATE, src.source_id
        if _normalize_uri(src.source_uri) == normalized:
            return DuplicateStatus.DUPLICATE, src.source_id
    return DuplicateStatus.UNIQUE, None


def _job_eligibility_errors(
    job: RagIngestionJob, record: Optional[RagSourceRecord]
) -> List[ValidationError]:
    errors: List[ValidationError] = []
    if job.dry_run is not True:
        errors.append(
            ValidationError(
                code="JOB_NOT_ELIGIBLE",
                field="dry_run",
                message="Only dry-run jobs can be acquired.",
            )
        )
    if job.status not in (JobStatus.VALIDATED, JobStatus.QUEUED):
        errors.append(
            ValidationError(
                code="JOB_NOT_ELIGIBLE",
                field="status",
                message=f"Job status {job.status.value} is not eligible for acquisition.",
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
                message="Job is already marked as a duplicate.",
            )
        )
    if record is None:
        errors.append(
            ValidationError(
                code="SOURCE_RECORD_NOT_FOUND",
                field="source_id",
                message="Source record for this job was not found.",
            )
        )
        return errors
    if record.source_id != job.source_id:
        errors.append(
            ValidationError(
                code="SOURCE_RECORD_NOT_FOUND",
                field="source_id",
                message="Source record does not match the job.",
            )
        )
    if record.domain != job.domain:
        errors.append(
            ValidationError(
                code="JOB_NOT_ELIGIBLE",
                field="domain",
                message="Source record domain does not match the job.",
            )
        )
    if record.collection_id != job.collection_id:
        errors.append(
            ValidationError(
                code="JOB_NOT_ELIGIBLE",
                field="collection_id",
                message="Source record collection does not match the job.",
            )
        )
    return errors


def _write_temp_artifact(raw_bytes: bytes, suffix: str = ".bin") -> Path:
    """Write raw bytes to a temporary file outside executable paths."""
    fd, tmp_path = tempfile.mkstemp(prefix="rag_acq_", suffix=suffix)
    os.close(fd)
    path = Path(tmp_path)
    path.write_bytes(raw_bytes)
    return path


def run_acquisition_preview(
    domain_id: str,
    job_id: str,
    dry_run: bool = True,
    parse: bool = True,
    client: Optional[httpx.Client] = None,
) -> RagAcquisitionReport:
    """Run a safe acquisition preview for an eligible dry-run ingestion job.

    This is the main orchestrator: validates eligibility, fetches the source,
    verifies the content hash, detects duplicates, optionally parses, and
    persists an audit report. Temporary artifacts are always removed.
    """
    started_at = datetime.utcnow()
    errors: List[ValidationError] = []

    if not dry_run:
        raise _error(
            "DRY_RUN_REQUIRED",
            "Actual acquisition is not enabled. Use dry_run=true.",
            "dry_run",
        )

    job = get_job(domain_id, job_id)
    if job is None:
        raise _error("JOB_NOT_ELIGIBLE", "Job not found.", "job_id")

    record = get_source_record(domain_id, job.source_id) if job else None
    errors.extend(_job_eligibility_errors(job, record))

    if record is None:
        report = RagAcquisitionReport(
            job_id=job_id,
            source_id=job.source_id if job else "",
            rag_pack_id=job.rag_pack_id if job else "",
            collection_id=job.collection_id if job else "",
            domain=domain_id,
            source_uri="",
            status=AcquisitionStatus.FAILED,
            errors=errors,
            started_at=started_at,
            completed_at=datetime.utcnow(),
        )
        save_acquisition_report(report)
        raise _error(
            "JOB_NOT_ELIGIBLE",
            "Job is not eligible for acquisition.",
        )

    # Re-run source policy validation in case pack metadata changed.
    validation = validate_source_record(record)
    if not validation.valid:
        errors.extend(validation.errors)

    report = RagAcquisitionReport(
        acquisition_id=str(uuid4()),
        job_id=job_id,
        source_id=record.source_id,
        rag_pack_id=record.rag_pack_id,
        collection_id=record.collection_id,
        domain=domain_id,
        source_uri=record.source_uri,
        source_class=record.source_class,
        status=AcquisitionStatus.PENDING,
        parse_status=ParseStatus.NOT_REQUESTED if not parse else ParseStatus.NOT_STARTED,
        errors=errors,
        started_at=started_at,
    )

    if errors:
        report.status = AcquisitionStatus.FAILED
        report.completed_at = datetime.utcnow()
        save_acquisition_report(report)
        raise _error(
            "JOB_NOT_ELIGIBLE",
            "Job is not eligible for acquisition.",
        )

    report.status = AcquisitionStatus.FETCHING
    save_acquisition_report(report)

    temp_path: Optional[Path] = None
    try:
        fetch_result = fetch_source(record.source_uri, client=client)
        report.http_status = fetch_result.status_code
        report.content_type = fetch_result.content_type
        report.content_length_bytes = fetch_result.content_length
        report.final_source_uri = fetch_result.final_url
        report.redirect_count = fetch_result.redirect_count
        report.status = AcquisitionStatus.FETCHED

        _check_content_type(fetch_result.content_type)

        computed_hash = _compute_sha256(fetch_result.raw_bytes)
        report.computed_content_hash = computed_hash

        # Verify declared content hash if present.
        if record.content_hash and record.content_hash != computed_hash:
            raise _error(
                "CONTENT_HASH_MISMATCH",
                "Computed content hash does not match the declared hash.",
                "content_hash",
            )

        # Persist observed hash when none was declared.
        if not record.content_hash:
            record.content_hash = computed_hash
            save_source_record(record)

        # Post-fetch duplicate detection.
        dup_status, match_id = _detect_duplicate_in_collection(
            record, computed_hash, fetch_result.final_url
        )
        report.duplicate_status = dup_status
        if dup_status == DuplicateStatus.DUPLICATE:
            report.duplicate_match_source_id = match_id
            report.status = AcquisitionStatus.DUPLICATE
            report.completed_at = datetime.utcnow()
            save_acquisition_report(report)
            raise _error(
                "DOCUMENT_DUPLICATE",
                "Acquired document duplicates an existing source in this collection.",
                "content_hash",
            )

        if parse:
            report.parse_status = ParseStatus.NOT_STARTED
            temp_path = _write_temp_artifact(
                fetch_result.raw_bytes,
                suffix=_suffix_for_content_type(fetch_result.content_type),
            )
            parse_result = parse_bytes(
                fetch_result.raw_bytes,
                content_type=fetch_result.content_type or "application/octet-stream",
                temp_path=temp_path,
                max_pages=RAG_PARSE_MAX_PAGES,
                max_chars=RAG_PARSE_MAX_CHARACTERS,
                preview_chars=RAG_PARSE_PREVIEW_CHARACTERS,
            )
            report.parser_id = parse_result.parser_id
            report.parser_version = parse_result.parser_version
            report.page_count = parse_result.page_count
            report.extracted_characters = len(parse_result.text)
            report.extracted_text = parse_result.text
            report.text_preview = parse_result.text[:RAG_PARSE_PREVIEW_CHARACTERS]
            report.warnings.extend(parse_result.warnings)
            report.parser_truncated = parse_result.truncated
            report.parse_status = ParseStatus.PARSED
            report.status = AcquisitionStatus.PARSED
        else:
            report.status = AcquisitionStatus.FETCHED

        report.completed_at = datetime.utcnow()
        save_acquisition_report(report)
        return report

    except AcquisitionError as exc:
        report.status = AcquisitionStatus.FAILED
        report.errors.append(exc.to_validation_error())
        report.completed_at = datetime.utcnow()
        save_acquisition_report(report)
        raise
    except Exception as exc:
        logger.exception("Acquisition preview failed for job %s", job_id)
        report.status = AcquisitionStatus.FAILED
        report.errors.append(
            ValidationError(
                code="ACQUISITION_FAILED",
                message=f"Unexpected acquisition failure: {type(exc).__name__}",
            )
        )
        report.completed_at = datetime.utcnow()
        save_acquisition_report(report)
        raise _error(
            "ACQUISITION_FAILED",
            "Unexpected acquisition failure.",
        ) from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception as cleanup_exc:
                logger.warning(
                    "Failed to remove temporary artifact %s: %s", temp_path, cleanup_exc
                )
                report.warnings.append(
                    f"Temporary artifact cleanup failed: {cleanup_exc}"
                )
                save_acquisition_report(report)


def _suffix_for_content_type(content_type: Optional[str]) -> str:
    mapping = {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "text/html": ".html",
        "application/xhtml+xml": ".xhtml",
    }
    if content_type:
        return mapping.get(content_type.split(";")[0].strip().lower(), ".bin")
    return ".bin"
