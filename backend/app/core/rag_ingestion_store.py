"""JSON-file persistence for RAG ingestion source records and jobs.

Follows the same atomic write/replace pattern used by
``backend/app/core/session_persistence.py`` so that restarts do not lose
queued or validated ingestion jobs.

No documents, chunks, embeddings, or vector-store operations happen here
beyond persistence helpers for their metadata artifacts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from app.models.rag_ingestion import (
    RagAcquisitionReport,
    RagCanonicalChunk,
    RagCanonicalDocument,
    RagChunkEmbedding,
    RagEmbeddingRun,
    RagIngestionJob,
    RagSourceRecord,
    RagVectorIndexRecord,
    RagVectorIndexRun,
)

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_PATH = "./storage"


def _storage_path() -> str:
    """Return the current storage root, respecting runtime env changes."""
    return os.getenv("STORAGE_PATH", DEFAULT_STORAGE_PATH)


def _rag_ingestion_dir(domain: str) -> Path:
    """Return the per-domain storage directory, creating it if necessary."""
    path = Path(_storage_path()) / "rag_ingestion" / domain
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sources_path(domain: str) -> Path:
    return _rag_ingestion_dir(domain) / "records.json"


def _jobs_path(domain: str) -> Path:
    return _rag_ingestion_dir(domain) / "jobs.json"


def _acquisitions_path(domain: str) -> Path:
    return _rag_ingestion_dir(domain) / "acquisitions.json"


def _atomic_write(path: Path, data: dict) -> None:
    """Write *data* to *path* atomically, preserving a backup if it existed."""
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
    """Load JSON from *path*; return an empty dict if missing or corrupt."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return {}


def _model_map(model_cls: Any, items: List[dict]) -> List[Any]:
    """Instantiate model instances from raw dicts, skipping corrupt entries."""
    result = []
    for item in items:
        try:
            result.append(model_cls(**item))
        except Exception as exc:
            logger.warning("Skipping corrupt %s entry: %s", model_cls.__name__, exc)
    return result


def save_source_record(record: RagSourceRecord) -> RagSourceRecord:
    """Persist a source record, replacing any existing record with the same id."""
    path = _sources_path(record.domain)
    data = _load_json(path)
    records = data.get("records", [])
    records = [r for r in records if r.get("source_id") != record.source_id]
    records.append(record.model_dump(mode="json"))
    data["records"] = records
    _atomic_write(path, data)
    return record


def get_source_record(domain: str, source_id: str) -> Optional[RagSourceRecord]:
    """Fetch a single source record by domain and source id."""
    path = _sources_path(domain)
    data = _load_json(path)
    for item in data.get("records", []):
        if item.get("source_id") == source_id:
            return RagSourceRecord(**item)
    return None


def list_source_records(
    domain: str, collection_id: Optional[str] = None
) -> List[RagSourceRecord]:
    """List source records for a domain, optionally filtered by collection."""
    path = _sources_path(domain)
    data = _load_json(path)
    records = _model_map(RagSourceRecord, data.get("records", []))
    if collection_id:
        records = [r for r in records if r.collection_id == collection_id]
    return records


def save_job(job: RagIngestionJob) -> RagIngestionJob:
    """Persist an ingestion job, replacing any existing job with the same id."""
    path = _jobs_path(job.domain)
    data = _load_json(path)
    jobs = data.get("jobs", [])
    jobs = [j for j in jobs if j.get("job_id") != job.job_id]
    jobs.append(job.model_dump(mode="json"))
    data["jobs"] = jobs
    _atomic_write(path, data)
    return job


def get_job(domain: str, job_id: str) -> Optional[RagIngestionJob]:
    """Fetch a single ingestion job by domain and job id."""
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
    """List ingestion jobs for a domain, with optional filters."""
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


def save_acquisition_report(report: RagAcquisitionReport) -> RagAcquisitionReport:
    """Persist an acquisition report, replacing any existing report with the same id."""
    path = _acquisitions_path(report.domain)
    data = _load_json(path)
    reports = data.get("acquisitions", [])
    reports = [r for r in reports if r.get("acquisition_id") != report.acquisition_id]
    reports.append(report.model_dump(mode="json"))
    data["acquisitions"] = reports
    _atomic_write(path, data)
    return report


def get_acquisition_report(
    domain: str, acquisition_id: str
) -> Optional[RagAcquisitionReport]:
    """Fetch a single acquisition report by domain and acquisition id."""
    path = _acquisitions_path(domain)
    data = _load_json(path)
    for item in data.get("acquisitions", []):
        if item.get("acquisition_id") == acquisition_id:
            return RagAcquisitionReport(**item)
    return None


def list_acquisition_reports(
    domain: str,
    job_id: Optional[str] = None,
    source_id: Optional[str] = None,
) -> List[RagAcquisitionReport]:
    """List acquisition reports for a domain, optionally filtered by job/source."""
    path = _acquisitions_path(domain)
    data = _load_json(path)
    reports = _model_map(RagAcquisitionReport, data.get("acquisitions", []))
    if job_id:
        reports = [r for r in reports if r.job_id == job_id]
    if source_id:
        reports = [r for r in reports if r.source_id == source_id]
    return reports


def _canonical_documents_dir(domain: str) -> Path:
    path = Path(_storage_path()) / "rag_ingestion" / domain / "canonical_documents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _canonical_text_dir(domain: str) -> Path:
    path = Path(_storage_path()) / "rag_ingestion" / domain / "canonical_text"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _canonical_chunks_dir(domain: str) -> Path:
    path = Path(_storage_path()) / "rag_ingestion" / domain / "canonical_chunks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _document_meta_path(domain: str, document_id: str) -> Path:
    return _canonical_documents_dir(domain) / f"{document_id}.json"


def _document_text_path(domain: str, document_id: str) -> Path:
    return _canonical_text_dir(domain) / f"{document_id}.txt"


def _document_chunks_path(domain: str, document_id: str) -> Path:
    return _canonical_chunks_dir(domain) / f"{document_id}.jsonl"


def save_canonical_document(
    document: RagCanonicalDocument, canonical_text: str, chunks: List[RagCanonicalChunk]
) -> RagCanonicalDocument:
    """Persist a canonical document, its text, and its chunks atomically."""
    domain = document.domain
    document_id = document.document_id

    meta_path = _document_meta_path(domain, document_id)
    text_path = _document_text_path(domain, document_id)
    chunks_path = _document_chunks_path(domain, document_id)

    _atomic_write(meta_path, document.model_dump(mode="json"))
    _atomic_write_text(text_path, canonical_text)
    _atomic_write_jsonl(chunks_path, [c.model_dump(mode="json") for c in chunks])
    return document


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically write text to *path*."""
    tmp_path = path.with_suffix(".txt.tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _atomic_write_jsonl(path: Path, records: List[dict]) -> None:
    """Atomically write JSONL records to *path*."""
    tmp_path = path.with_suffix(".jsonl.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, default=str) + "\n")
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def get_canonical_document(domain: str, document_id: str) -> Optional[RagCanonicalDocument]:
    """Fetch a canonical document's metadata."""
    path = _document_meta_path(domain, document_id)
    if not path.exists():
        return None
    try:
        return RagCanonicalDocument(**json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.warning("Failed to load canonical document %s: %s", document_id, exc)
        return None


def get_canonical_text(domain: str, document_id: str) -> Optional[str]:
    """Fetch the canonical text for a document."""
    path = _document_text_path(domain, document_id)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to load canonical text %s: %s", document_id, exc)
        return None


def get_canonical_chunk(domain: str, document_id: str, chunk_id: str) -> Optional[RagCanonicalChunk]:
    """Fetch a single chunk by domain, document id, and chunk id."""
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


def list_canonical_chunks(domain: str, document_id: str) -> List[RagCanonicalChunk]:
    """List all chunks for a canonical document in ordinal order."""
    path = _document_chunks_path(domain, document_id)
    if not path.exists():
        return []
    chunks = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            chunks.append(RagCanonicalChunk(**json.loads(line)))
    except Exception as exc:
        logger.warning("Failed to load chunks for %s: %s", document_id, exc)
    return sorted(chunks, key=lambda c: c.ordinal)


def list_canonical_documents(
    domain: str,
    rag_pack_id: Optional[str] = None,
    collection_id: Optional[str] = None,
    job_id: Optional[str] = None,
    source_id: Optional[str] = None,
    canonicalization_status: Optional[str] = None,
    chunking_status: Optional[str] = None,
) -> List[RagCanonicalDocument]:
    """List canonical documents for a domain, with optional filters."""
    doc_dir = _canonical_documents_dir(domain)
    documents = []
    for path in doc_dir.glob("*.json"):
        try:
            doc = RagCanonicalDocument(**json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("Skipping corrupt canonical document %s: %s", path, exc)
            continue
        if rag_pack_id and doc.rag_pack_id != rag_pack_id:
            continue
        if collection_id and doc.collection_id != collection_id:
            continue
        if job_id and doc.job_id != job_id:
            continue
        if source_id and doc.source_id != source_id:
            continue
        if canonicalization_status and doc.canonicalization_status.value != canonicalization_status:
            continue
        if chunking_status and doc.chunking_status.value != chunking_status:
            continue
        documents.append(doc)
    return documents


def _embeddings_dir(domain: str, document_id: str) -> Path:
    path = Path(_storage_path()) / "rag_ingestion" / domain / "embeddings" / document_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _embedding_run_path(domain: str, document_id: str, run_id: str) -> Path:
    return _embeddings_dir(domain, document_id) / f"{run_id}.json"


def _embedding_vectors_path(domain: str, document_id: str, run_id: str) -> Path:
    return _embeddings_dir(domain, document_id) / f"{run_id}.vectors.jsonl"


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
    """Return a deterministic hash over chunk embedding records."""
    # Exclude timestamps so the hash is deterministic across reruns.
    excluded = {"created_at", "updated_at"}
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
                data["vector"] = []
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
                data["vector"] = []
            return RagChunkEmbedding(**data)
    except Exception as exc:
        logger.warning("Failed to load chunk embedding %s: %s", embedding_id, exc)
    return None


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
                data["vector"] = []
            records.append(RagVectorIndexRecord(**data))
    except Exception as exc:
        logger.warning("Failed to load vector index records for %s: %s", index_id, exc)
    return sorted(records, key=lambda r: r.chunk_ordinal)
