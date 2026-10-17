# Canonical RAG Documents and Deterministic Chunking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Convert a successful governed acquisition preview into a persistent canonical text document and deterministic, auditable chunks, without embeddings, vector-store writes, or pack status mutation.

**Architecture:** Extend the acquisition report to retain the full bounded extracted text. Add `RagCanonicalDocument` and `RagCanonicalChunk` models, a deterministic normalization service (`rag_canonical_text.py`), a deterministic structural chunking service (`rag_chunking.py`), and domain-scoped endpoints. Persist metadata as JSON, canonical text as UTF-8 `.txt`, and chunks as JSONL.

**Tech Stack:** FastAPI, Pydantic v2, Python 3.11+.

## Global Constraints

- Canonical text comes from the trusted parser output stored on the acquisition report, never from the API client or the preview.
- Raw downloaded binaries remain discarded.
- No embeddings, vector stores, retrieval, background workers.
- Pack `ingestion_status.state` stays `not_ingested`.
- All content marked `untrusted_content = true`.

---

## Task 1: Extend acquisition report with full extracted text

**Files:**
- Modify: `backend/app/models/rag_ingestion.py` — add `extracted_text` to `RagAcquisitionReport`.
- Modify: `backend/app/core/rag_source_acquisition.py` — populate `extracted_text` from parser output.

## Task 2: Add canonical document and chunk models

**Files:**
- Modify: `backend/app/models/rag_ingestion.py`

Add enums: `CanonicalizationStatus`, `ChunkingStatus`, `IndexStatus`. Add `RagCanonicalDocument` and `RagCanonicalChunk`.

## Task 3: Add normalization service

**Files:**
- Create: `backend/app/core/rag_canonical_text.py`

Provide `normalize_text(text, version)` returning `(normalized_text, warnings)`. Implement `canonical-text-v1`.

## Task 4: Add deterministic chunking service

**Files:**
- Create: `backend/app/core/rag_chunking.py`

Provide `chunk_canonical_text(text, config)` returning `List[Chunk]`. Implement `structural-character-v1` with structural boundary priority and deterministic overlap.

## Task 5: Add canonical document orchestration and persistence

**Files:**
- Create: `backend/app/core/rag_canonical_documents.py`
- Modify: `backend/app/core/rag_ingestion_store.py` — save/get/list canonical docs, text, chunks.

## Task 6: Add endpoints

**Files:**
- Modify: `backend/app/routers/domains.py`

Add:
- `POST /v1/domains/{domain_id}/rag-ingestion/jobs/{job_id}/acquisition-previews/{acquisition_id}/canonical-document`
- `GET /v1/domains/{domain_id}/rag-ingestion/documents`
- `GET /v1/domains/{domain_id}/rag-ingestion/documents/{document_id}`
- `GET /v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/chunks`
- `GET /v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/chunks/{chunk_id}`

## Task 7: Add configuration

**Files:**
- Modify: `backend/.env.example`

Add canonical/chunking env vars.

## Task 8: Add tests

**Files:**
- Create: `backend/tests/test_rag_canonical_text.py`
- Create: `backend/tests/test_rag_chunking.py`
- Create: `backend/tests/test_rag_canonical_documents.py`
- Create: `backend/tests/test_rag_canonical_document_endpoints.py`

## Task 9: Run tests and open PR

Run new tests, existing ingestion/acquisition/RAG tests, full suite. Push, create PR, merge after CI.
