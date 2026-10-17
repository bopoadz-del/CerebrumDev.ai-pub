# Implementation Plan: Automotive Safety Intelligence Pilot — PR 2 (Revised)

> **Plan file:** `docs/superpowers/plans/2026-07-11-automotive-pilot-pr2-automotive-intelligence.md`  
> **Parent design spec:** `docs/superpowers/specs/2026-07-11-automotive-safety-intelligence-pilot-design.md`  
> **Branch:** `feat/rag-vector-index-dry-run`  
> **PR title:** `feat(rag): automotive intelligence — NHTSA harvest, embeddings, retrieval, activation and evaluation`  
> **Target repo:** `bopoadz-del/CerebrumDev.ai` (harvester, models, evaluation live here); generated platform code is emitted into the Fork-derived package template  
> **Estimated effort:** large

---

## Outcome

PR 2 produces a working **Automotive Core RAG** layer in the generated automotive platform:

- Official NHTSA public data is harvested, normalized and versioned.
- Canonical automotive record models exist.
- Documents are chunked deterministically.
- Real semantic embeddings are generated with **BAAI/bge-small-en-v1.5** at **384 dimensions** into the **`v2`** namespace (table `chunks_v2`).
- Embeddings are indexed in **Postgres/pgvector** and **BM25** using The_Fork's existing RAG stack.
- Layer-aware hybrid retrieval returns public foundation evidence, with hooks for later client-overlay blending.
- The foundation RAG pack can be activated.
- A 50-question golden evaluation pack proves the retrieval and citation gates.

No production vector database other than Postgres/pgvector is used. No similarity-search API is exposed to end users yet. No client-private documents are handled in this PR — that belongs to PR 3.

**Important:** The runtime implementation lives in the generated Fork-derived platform package (paths like `app/core/rag/...`, `app/routers/admin.py`, `alembic/`). The harvester and evaluation runner live in CerebrumDev.ai factory backend (`backend/scripts/`, `backend/app/core/`).

---

## Scope guard

**In scope**

- NHTSA source acquisition and normalization (CerebrumDev.ai factory scripts + generated platform ingestion).
- Canonical automotive record models (in generated platform).
- Foundation RAG pack build pipeline in generated platform.
- Reuse of The_Fork's production RAG stack (`app/core/rag/embeddings.py`, `app/core/rag/vector_store.py`, `app/core/rag/retriever.py`).
- Postgres/pgvector and BM25 indexing for the automotive corpus.
- Layer-aware retrieval service (foundation layer only; client-overlay wiring in PR 3).
- Source citation envelope added to chat SSE.
- 50-question evaluation pack and evaluation runner.
- Admin verification/activation API for the foundation pack.

**Out of scope**

- Frontend conversion.
- Generated-platform deployment.
- Client-private document upload and project overlay (PR 3).
- Google Drive UI journeys (connector core in PR 1; end-to-end in PR 3).
- Production semantic embedders other than BGE-small-en-v1.5.
- Changes to The_Fork `main` production branch.

---

## Files to inspect before writing

```text
CerebrumDev.ai/backend/
  app/core/rag_ingestion_store.py
  app/core/rag_canonical_documents.py
  app/core/rag_embeddings.py
  app/core/rag_embedding_providers.py
  app/core/rag_vector_indexing.py
  app/core/rag_vector_store_adapters.py
  app/models/rag_ingestion.py
  app/routers/domains.py
  app/config.py
  .env.example
  tests/test_rag_*.py

The_Fork/app/
  core/models.py
  core/rag/embeddings.py
  core/rag/vector_store.py
  core/rag/retriever.py
  core/rag/search.py
  routers/admin.py
  routers/chat.py
  alembic/versions/

Cerebrum-Blocks/
  block_store/kits/automotive/source_manifest.json
```

---

## Part A — NHTSA harvester and source manifest

### 1. Source families and authority

Create a versioned source manifest under Cerebrum-Blocks:

```text
block_store/kits/automotive/source_manifest.json
```

Source families:

```text
recalls           NHTSA Recall Campaigns        https://www.nhtsa.gov/nhtsa-datasets-and-apis
investigations    NHTSA ODI Investigations      https://www-odi.nhtsa.dot.gov/downloads/
complaints        NHTSA Consumer Complaints     https://www-odi.nhtsa.dot.gov/downloads/
safety_ratings    NHTSA 5-Star Safety Ratings   https://www.nhtsa.gov/ratings
vpic              vPIC Make/Model/Manufacturer  https://vpic.nhtsa.dot.gov/api/
```

Each entry records:

```json
{
  "source_id": "nhtsa_recalls",
  "source_family": "recall",
  "official_publisher": "National Highway Traffic Safety Administration",
  "source_uri": "https://www.nhtsa.gov/nhtsa-datasets-and-apis",
  "retrieval_method": "download_csv_zip",
  "source_class": "approved_official_public_data",
  "licence_evidence": "US federal public data",
  "authority_rating": "primary",
  "jurisdiction": "US",
  "coverage_start": "2015-01-01",
  "coverage_end": "<harvest_date>",
  "harvest_timestamp": "<iso_timestamp>",
  "content_hash": "<sha256_of_raw_archive>",
  "record_count": 0,
  "normalization_version": "automotive_core_v1",
  "chunking_version": "v2",
  "embedding_identity": "bge-small-en-v1.5-384",
  "pack_version": "automotive_core_rag_v1.0.0",
  "refresh_policy": "monthly_full_rebuild"
}
```

### 2. Harvest CLI

Add in CerebrumDev.ai:

```text
backend/scripts/harvest_nhtsa.py
```

Behavior:

- Download each family’s dataset to a local working directory outside Git.
- Verify archive hash.
- Extract CSVs.
- Record row counts and harvest timestamp.
- Write raw metadata to:

```text
STORAGE_PATH/automotive_core_rag_v1/harvests/<iso_date>/raw/<source_family>/
```

- Idempotent: skip re-download if archive hash already harvested today.
- No credentials beyond public URLs.

CLI example:

```bash
cd backend
python -m scripts.harvest_nhtsa --output-dir "$STORAGE_PATH/automotive_core_rag_v1/harvests" --dry-run
```

### 3. Raw-data handling rule

The harvested CSVs and extracted rows are **build artifacts**, not repo files. Add to `.gitignore`:

```text
# Automotive foundation pack build artifacts
storage/automotive_core_rag_v1/
*.nhtsa.zip
```

---

## Part B — Automotive canonical record models

### 1. Model module

Create in generated platform:

```text
generated/automotive-safety-intelligence/app/models/automotive_records.py
```

Record classes (same as previous plan).

### 2. Normalizer service

Create in generated platform:

```text
generated/automotive-safety-intelligence/app/core/automotive_normalizers.py
```

### 3. Canonical document bridge

Use The_Fork's existing ingestion models and add an automotive pack builder:

```text
generated/automotive-safety-intelligence/app/core/automotive_pack_builder.py
```

Create canonical documents with:

```text
canonicalization_status = canonicalized
chunking_status = not_chunked
index_status = not_indexed
source_class = approved_official_public_data
knowledge_layer = automotive_core_v1
```

---

## Part C — Foundation pack build pipeline

### 1. Builder module

Create in generated platform:

```text
generated/automotive-safety-intelligence/app/core/automotive_pack_builder.py
generated/automotive-safety-intelligence/scripts/build_automotive_core_pack.py
```

Pipeline stages:

```text
1. load_source_manifest()
2. harvest_or_reuse_raw()
3. normalize_records()
4. canonicalize_records()
5. chunk_documents()
6. generate_embeddings(provider=BAAI/bge-small-en-v1.5, dimensions=384, namespace=v2)
7. create_pgvector_index(namespace=v2)
8. create_bm25_index(namespace=v2)
9. verify_index_integrity()
10. update_pack_manifest(status=validated)
11. activate_pack(version=automotive_core_rag_v1.0.0)
```

### 2. Reuse The_Fork embedding provider

Use The_Fork's existing `app/core/rag/embeddings.py`. Configure:

```text
RAG_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RAG_EMBEDDING_DIMENSIONS=384
RAG_VECTOR_NAMESPACE=v2
```

**Note:** `RAG_VECTOR_NAMESPACE=v2` produces table `chunks_v2`. Setting it to `chunks_v2` would produce `chunks_chunks_v2`.

If `sentence-transformers` is not installed, fail with `PRODUCTION_EMBEDDING_NOT_CONFIGURED`. Do not fall back to `local_feature_hash_v1`.

### 3. Schema migration for 384-dim vectors

The_Fork default schema uses `vector(256)`. Add an Alembic migration in the generated package:

```text
generated/automotive-safety-intelligence/alembic/versions/0003_automotive_vector_384.py
```

This migration creates the `chunks_v2` table with `vector(384)` or a fresh namespace. Do not alter existing `chunks` table if it contains 256-dim vectors.

### 4. Pack manifest

Write:

```text
STORAGE_PATH/automotive_core_rag_v1/pack_manifest.json
```

---

## Part D — Layer-aware hybrid retrieval

### 1. Retrieval service

Extend The_Fork's existing `app/core/rag/retriever.py`:

```python
class RetrievalRequest(BaseModel):
    query: str
    knowledge_layers: list[Literal["automotive_core_v1", "client_private"]] = ["automotive_core_v1"]
    project_id: str | None = None
    top_k_vector: int = 10
    top_k_lexical: int = 10
    fusion_k: int = 60

class RetrievedEvidence(BaseModel):
    knowledge_layer: Literal["automotive_core_v1", "client_private"]
    source_family: str
    source_title: str
    source_authority: str
    source_url: str | None
    record_reference: str
    retrieval_score: float
    chunk_text: str
    metadata: dict[str, Any]
```

### 2. Exact identifier handling

Detect automotive identifiers and issue direct metadata filter lookups:

```python
IDENTIFIER_PATTERNS = {
    "campaign_number": r"\b\d{2}[A-Z]\d{3,6}\b",
    "odi_number": r"\b\d{9,10}\b",
    "investigation_number": r"\bDP\d{2,3}\-\d{3,4}\b",
}
```

### 3. Hybrid fusion

Reuse The_Fork's RRF fusion.

### 4. Layer-aware ranking

Foundation layer active. Client-private stubbed for PR 3.

---

## Part E — Automotive grounded assistant configuration

### 1. System prompt

Create in generated platform:

```text
generated/automotive-safety-intelligence/app/prompts/automotive_assistant_v1.txt
```

### 2. Chat router integration

Extend The_Fork's `app/routers/chat.py` to:

- Call `retrieve_evidence` before generation.
- Pass evidence as context.
- Stream citations through SSE.
- Render `knowledge_layer` label per citation.

---

## Part F — Evaluation pack

### 1. Golden questions

Create in Cerebrum-Blocks:

```text
block_store/kits/automotive/evaluation/golden_questions.jsonl
```

50 items across categories.

### 2. Evaluation runner

Create in CerebrumDev.ai:

```text
backend/app/core/automotive_evaluation.py
backend/scripts/run_automotive_evaluation.py
```

The runner operates against a deployed/generated platform instance.

---

## Part G — Admin foundation-pack API

Add admin-only routes in generated platform:

```text
GET  /v1/admin/automotive-core/status
POST /v1/admin/automotive-core/build
POST /v1/admin/automotive-core/verify
POST /v1/admin/automotive-core/evaluate
POST /v1/admin/automotive-core/activate
POST /v1/admin/automotive-core/rollback
```

Reuse The_Fork's `app/routers/admin.py` and `_require_admin` gating.

---

## Part H — Tests

Add in generated platform:

```text
generated/automotive-safety-intelligence/tests/test_automotive_normalizers.py
generated/automotive-safety-intelligence/tests/test_automotive_pack_builder.py
generated/automotive-safety-intelligence/tests/test_automotive_retrieval.py
generated/automotive-safety-intelligence/tests/test_automotive_evaluation.py
generated/automotive-safety-intelligence/tests/test_admin_automotive_core.py
```

Add in CerebrumDev.ai:

```text
backend/tests/test_harvest_nhtsa.py
backend/tests/test_automotive_evaluation_runner.py
```

---

## Part I — Verification commands

```bash
# Generated platform
cd generated/automotive-safety-intelligence
python -m py_compile \
  app/models/automotive_records.py \
  app/core/automotive_normalizers.py \
  app/core/automotive_pack_builder.py \
  app/core/rag/retriever.py \
  app/routers/admin_automotive.py

python -m pytest tests -q

# CerebrumDev.ai factory
cd ../../backend
python -m pytest \
  tests/test_harvest_nhtsa.py \
  tests/test_automotive_evaluation_runner.py \
  -q --tb=short

python -m pytest tests -q
```

---

## Part J — Commit plan

1. `feat(automotive): add NHTSA source manifest to Cerebrum-Blocks`
2. `feat(automotive): add harvest CLI in CerebrumDev.ai factory`
3. `feat(automotive): add canonical automotive record models to generated platform`
4. `feat(automotive): add foundation pack builder with BGE-small 384 indexing`
5. `feat(automotive): add 384-dim vector migration for automotive namespace`
6. `feat(automotive): add layer-aware hybrid retrieval service`
7. `feat(automotive): add automotive assistant prompt and chat citations`
8. `feat(automotive): add 50-question evaluation pack and runner`
9. `feat(automotive): add admin foundation-pack activation API`
10. `test(automotive): add automotive intelligence tests`

---

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| NHTSA data format changes | Version source manifest; normalize defensively. |
| BGE-small model download failure | Cache model in build environment; fail fast. |
| Index build is slow | Batch commits; resume state; limit first pilot scope. |
| 256-dim vs 384-dim schema conflict | Use fresh `chunks_v2` namespace with `vector(384)` migration. |
| Evaluation gate misses 85% | Iterate retrieval fusion and exact-identifier boosts. |

---

## Definition of done

- [ ] NHTSA harvester downloads and records hashes.
- [ ] Canonical record models and normalizers are deterministic.
- [ ] Foundation pack builder produces `automotive_core_rag_v1` with real 384-dim embeddings.
- [ ] Postgres/pgvector and BM25 indexes are queryable.
- [ ] Layer-aware retrieval returns ranked foundation evidence.
- [ ] Admin API can build, verify, evaluate and activate the pack.
- [ ] 50-question evaluation meets gates or failure is documented.
- [ ] All new tests pass and full backend suite passes.
- [ ] CI passes.
