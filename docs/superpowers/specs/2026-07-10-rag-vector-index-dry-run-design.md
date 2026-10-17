# Design: RAG Vector-Store Adapter Contract and Local Indexing Dry-Run (Phase 7)

## Context

Phase 6 produced deterministic validation embeddings and embedding-run artifacts. Phase 7 consumes those artifacts through a vendor-neutral vector-store adapter contract and builds an isolated, validation-only local flat index. It proves indexing mechanics without enabling retrieval or production vector stores.

## Goal

Create a model-independent vector-store adapter layer plus a `local_flat_json_v1` validation adapter that:

- Reads only persisted `RagEmbeddingRun`, `RagChunkEmbedding`, and `RagCanonicalChunk` records.
- Builds deterministic index/run/record identities.
- Produces an atomic manifest + records JSONL artifact.
- Verifies read-back integrity.
- Exposes metadata-only inspection endpoints.
- Preserves state isolation.

## Non-goals

- Production vector database adapters (pgvector, FAISS, Qdrant, Pinecone, Weaviate, Milvus, Chroma, Elasticsearch, Redis, etc.).
- Similarity search, nearest-neighbor search, query embeddings, retrieval.
- RAG pack activation or production deployment.
- Background workers.

## Architecture

```text
RagEmbeddingRun + RagChunkEmbedding (persisted)
        ↓
rag_vector_indexing.validate_eligibility()
        ↓
adapter = rag_vector_store_adapters.get_adapter(adapter_id)
        ↓
index_spec = build_index_spec(document, embedding_run, adapter)
        ↓
records = map_embeddings_to_records(embedding_records, chunks, document)
        ↓
adapter.write_records(index_spec, records)
        ↓
adapter.read_records(index_spec) → verify integrity
        ↓
persist RagVectorIndexRun
        ↓
return bounded metadata
```

## Components

### 1. Models

File: `backend/app/models/rag_ingestion.py`

Add:

- `VectorIndexRunStatus` enum: `PENDING`, `VALIDATING`, `INDEXING`, `VERIFYING`, `COMPLETED`, `FAILED`.
- `ActivationStatus` enum: `INACTIVE`.
- `RagVectorIndexRun` model.
- `RagVectorIndexRecord` model with constrained `metadata` shape.

### 2. Adapter contract

File: `backend/app/core/rag_vector_store_adapters.py`

```python
@dataclass
class RagVectorStoreAdapter:
    adapter_id: str
    adapter_version: str
    storage_type: str
    distance_metric: str
    supported_dimensions: List[int]
    supports_upsert: bool
    supports_delete: bool
    supports_metadata: bool
    supports_filtering: bool
    supports_similarity_search: bool
    production_approved: bool

    def create_index(self, index_spec): ...
    def write_records(self, index_spec, records): ...
    def read_records(self, index_spec): ...
    def validate_index(self, index_spec): ...
    def delete_dry_run_index(self, index_spec): ...
```

Initial adapter: `local_flat_json_v1`.

### 3. Indexing orchestration

File: `backend/app/core/rag_vector_indexing.py`

Responsibilities:

- Eligibility validation of document, embedding run, and embedding records.
- Load canonical chunks and verify chunk text hashes.
- Verify vector hashes and dimensions.
- Map embeddings into `RagVectorIndexRecord` objects with governance metadata.
- Deterministic `index_id`, `index_run_id`, `record_id` generation.
- Adapter selection and contract validation.
- Atomic manifest + records publication.
- Read-back integrity verification.
- Idempotency and conflict detection.

### 4. Persistence

File: `backend/app/core/rag_ingestion_store.py`

Add:

- `_vector_indexes_dir(domain, document_id, index_id)`
- `save_vector_index_run(run, records)`
- `get_vector_index_run(domain, document_id, index_run_id)`
- `list_vector_index_runs(domain, document_id)`
- `get_vector_index_records(domain, document_id, index_run_id, include_vectors=False)`

Layout:

```text
STORAGE_PATH/rag_ingestion/<domain>/vector_indexes/<document_id>/<index_id>/
  manifest.json
  records.jsonl
  run.json
```

### 5. Endpoints

File: `backend/app/routers/domains.py`

- `POST /v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/embedding-runs/{embedding_run_id}/vector-index-dry-run`
- `GET /v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/vector-index-runs`
- `GET /v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/vector-index-runs/{index_run_id}`
- `GET /v1/domains/{domain_id}/rag-ingestion/documents/{document_id}/vector-index-runs/{index_run_id}/records?include_vectors=false`

### 6. Configuration

File: `backend/.env.example`

Add:

```text
RAG_VECTOR_DEFAULT_ADAPTER=local_flat_json_v1
RAG_VECTOR_INDEX_MANIFEST_VERSION=vector-index-manifest-v1
RAG_VECTOR_RECORD_PRECISION=8
RAG_VECTOR_INDEX_MAX_RECORDS=10000
RAG_VECTOR_INDEX_INCLUDE_VECTORS_DEFAULT=false
```

### 7. Tests

New files:

- `backend/tests/test_rag_vector_store_adapters.py`
- `backend/tests/test_rag_vector_indexing.py`
- `backend/tests/test_rag_vector_index_endpoints.py`

## Identities

- `index_id = sha256(collection_id + ":" + document_id + ":" + embedding_run_id + ":" + adapter_id + ":" + adapter_version + ":" + distance_metric + ":" + dimensions)`
- `index_run_id = sha256(index_id + ":" + str(dry_run))`
- `record_id = sha256(index_id + ":" + embedding_id + ":" + chunk_id + ":" + vector_hash)`

## State isolation

- `RagCanonicalDocument.index_status` stays `not_indexed`.
- `RagCanonicalChunk.index_status` stays `not_indexed`.
- `RagEmbeddingRun.index_status` stays `not_indexed`.
- RAG pack `ingestion_status.state` stays `not_ingested`.
- Ingestion job status stays `validated` or `queued`.
- `RagVectorIndexRun.retrieval_enabled = false`, `activation_status = inactive`, `production_approved = false`.

## Error codes

- `DRY_RUN_REQUIRED`
- `DOCUMENT_NOT_FOUND`
- `DOCUMENT_NOT_ELIGIBLE`
- `EMBEDDING_RUN_NOT_FOUND`
- `EMBEDDING_RUN_NOT_ELIGIBLE`
- `EMBEDDING_RUN_LINKAGE_MISMATCH`
- `EMBEDDING_RECORDS_NOT_FOUND`
- `EMBEDDING_RECORD_COUNT_MISMATCH`
- `CHUNK_LINKAGE_MISMATCH`
- `CHUNK_TEXT_HASH_MISMATCH`
- `VECTOR_HASH_MISMATCH`
- `VECTOR_DIMENSION_MISMATCH`
- `VECTOR_NON_FINITE_VALUE`
- `VECTOR_ADAPTER_NOT_FOUND`
- `VECTOR_ADAPTER_CONTRACT_INVALID`
- `VECTOR_INDEX_CONFIGURATION_INVALID`
- `VECTOR_INDEX_TOO_LARGE`
- `VECTOR_INDEX_CONFLICT`
- `VECTOR_INDEX_RECORD_CONFLICT`
- `VECTOR_INDEX_WRITE_FAILED`
- `VECTOR_INDEX_READBACK_FAILED`
- `VECTOR_INDEX_ARTIFACT_MISMATCH`
- `VECTOR_INDEX_RUN_FAILED`

## Warnings

- `SOURCE_DOCUMENT_TRUNCATED`
- `VALIDATION_ONLY_EMBEDDING`
- `VALIDATION_ONLY_INDEX`
- `PRODUCTION_EMBEDDING_NOT_CONFIGURED`
- `PRODUCTION_VECTOR_STORE_NOT_CONFIGURED`
- `RETRIEVAL_DISABLED`
