# Design: RAG Embedding Contract and Offline Dry-Run (Phase 6)

## Context

Phase 5 added canonical documents and deterministic structural chunks. Phase 6 adds a model-independent embedding layer that reads **only** persisted canonical chunks and produces deterministic validation vectors, without touching a production vector database or enabling retrieval.

## Goal

Provide an embedding provider contract and an offline validation-only provider (`local_feature_hash_v1`) so we can validate:

- chunk eligibility,
- deterministic batching,
- vector dimensions and normalization,
- deterministic run/embedding identities,
- idempotency,
- audit persistence,
- future indexing compatibility.

## Non-goals

- Production semantic embedding providers (OpenAI, Cohere, Hugging Face, etc.).
- Vector database connections (pgvector, FAISS, Qdrant, etc.).
- Retrieval, reranking, hybrid search.
- Background workers.
- Changing RAG pack, document, chunk, or job statuses to indexed/ingesting.

## Architecture

```text
RagCanonicalDocument + RagCanonicalChunk (persisted)
        ↓
rag_embeddings.validate_eligibility()
        ↓
rag_embeddings.load_chunks_from_store()
        ↓
provider = rag_embedding_providers.get_provider(provider_id)
        ↓
batches = deterministic_batches(chunks, provider.maximum_batch_size)
        ↓
vectors = provider.embed_texts([chunk.text for chunk in batch])
        ↓
validate_vectors()
        ↓
persist RagEmbeddingRun + RagChunkEmbedding artifacts
        ↓
return run metadata
```

## Components

### 1. Provider contract

File: `backend/app/core/rag_embedding_providers.py`

```python
@dataclass
class RagEmbeddingProvider:
    provider_id: str
    provider_version: str
    algorithm: str
    dimensions: int
    distance_metric: str
    normalization: str
    maximum_batch_size: int
    maximum_input_characters: int
    production_approved: bool

    def embed_texts(self, texts: List[str]) -> List[List[float]]: ...
```

Initial provider: `local_feature_hash_v1` — deterministic signed feature hashing.

- Tokenize on whitespace/punctuation deterministically.
- Map each token to a dimension via SHA-256 mod dimensions.
- Derive sign from another byte of the hash.
- Accumulate weights per token occurrence.
- L2-normalize.
- Round to 8 decimal places.

### 2. Embedding orchestration

File: `backend/app/core/rag_embeddings.py`

Responsibilities:

- Eligibility validation of canonical document and chunks.
- Load chunks from `rag_ingestion_store`.
- Deterministic batching by ordinal.
- Call provider.
- Validate vectors (finite, correct dimension, not zero, L2 norm ≈ 1).
- Compute deterministic `run_id` and `embedding_id`.
- Persist run and chunk embeddings.
- Handle idempotency/conflicts.

### 3. Persistence

File: `backend/app/core/rag_ingestion_store.py`

Add:

- `_embeddings_dir(domain, document_id)`
- `save_embedding_run(run, chunk_embeddings)`
- `get_embedding_run(domain, document_id, run_id)`
- `list_embedding_runs(domain, document_id)`
- `get_chunk_embeddings(domain, document_id, run_id, include_vectors=False)`
- `get_chunk_embedding(domain, document_id, run_id, embedding_id)`

Layout:

```text
STORAGE_PATH/rag_ingestion/<domain>/embeddings/<document_id>/<run_id>.json
STORAGE_PATH/rag_ingestion/<domain>/embeddings/<document_id>/<run_id>.vectors.jsonl
```

### 4. Models

File: `backend/app/models/rag_ingestion.py`

Add:

- `EmbeddingRunStatus` enum: `PENDING`, `VALIDATING`, `EMBEDDING`, `COMPLETED`, `FAILED`.
- `RagEmbeddingRun` model.
- `RagChunkEmbedding` model.

### 5. Endpoints

File: `backend/app/routers/domains.py`

- `POST /v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/embedding-dry-run`
- `GET /v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/embedding-runs`
- `GET /v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/embedding-runs/{run_id}`
- `GET /v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/embedding-runs/{run_id}/embeddings?include_vectors=false`

### 6. Configuration

File: `backend/.env.example`

Add:

```text
RAG_EMBEDDING_DEFAULT_PROVIDER=local_feature_hash_v1
RAG_EMBEDDING_DIMENSIONS=384
RAG_EMBEDDING_BATCH_SIZE=64
RAG_EMBEDDING_VECTOR_PRECISION=8
RAG_EMBEDDING_NORM_TOLERANCE=0.00001
```

### 7. Tests

New files:

- `backend/tests/test_rag_embedding_providers.py`
- `backend/tests/test_rag_embeddings.py`
- `backend/tests/test_rag_embedding_endpoints.py`

Cover determinism, validation, state isolation, idempotency, domain ownership, and rejection of non-dry-run requests.

## State isolation

- `RagCanonicalDocument.index_status` stays `not_indexed`.
- `RagCanonicalChunk.index_status` stays `not_indexed`.
- RAG pack `ingestion_status.state` stays `not_ingested`.
- Ingestion job status stays `validated` or `queued`.

## Identities

- `run_id = sha256(collection_id + ":" + document_id + ":" + provider_id + ":" + provider_version + ":" + dimensions + ":" + normalization)`
- `embedding_id = sha256(run_id + ":" + chunk_id + ":" + provider_id + ":" + provider_version)`

## Vector serialization

- Canonical JSON array, 8 decimal places, no whitespace.
- `vector_hash = sha256(serialized_vector_bytes)`
- `vector_artifact_hash = sha256(canonical JSONL artifact bytes)`

## Error codes

- `DRY_RUN_REQUIRED`
- `DOCUMENT_NOT_FOUND`
- `DOCUMENT_NOT_ELIGIBLE`
- `DOCUMENT_HAS_NO_CHUNKS`
- `CHUNK_LINKAGE_MISMATCH`
- `CHUNK_TEXT_HASH_MISMATCH`
- `EMBEDDING_PROVIDER_NOT_FOUND`
- `EMBEDDING_PROVIDER_CONTRACT_INVALID`
- `EMBEDDING_CONFIGURATION_INVALID`
- `EMBEDDING_VECTOR_INVALID`
- `EMBEDDING_DIMENSION_MISMATCH`
- `EMBEDDING_ZERO_VECTOR`
- `EMBEDDING_NON_FINITE_VALUE`
- `EMBEDDING_RUN_CONFLICT`
- `EMBEDDING_ARTIFACT_CONFLICT`
- `EMBEDDING_RUN_FAILED`

## Warnings

- `SOURCE_DOCUMENT_TRUNCATED`
- `VALIDATION_ONLY_PROVIDER`
- `PRODUCTION_EMBEDDING_NOT_CONFIGURED`
