# RAG Ingestion Contract

## Status

Design spec. No runtime code changes in this PR.

## Background

Cerebrum-Blocks now defines metadata-only prebuilt RAG packs in `block_store/shelves/rag_packs.json`. CerebrumDev.ai exposes them read-only via:

- `GET /v1/domains/rag-packs`
- `GET /v1/domains/{domain_id}/rag-activation`

The live smoke test confirms this metadata lane works. The next step is **not** to start pulling documents. It is to define the ingestion contract so that later document ingestion is traceable, licensed, idempotent, and domain-aligned.

## Goal

Define a contract that governs how prebuilt RAG packs move from metadata-only to indexed collections, without performing any ingestion in this PR.

## Non-goals

- Do not download, scrape, or ingest any documents yet.
- Do not create embeddings or vector stores yet.
- Do not change chain generation or chain quality logic.
- Do not change source packs, formula_executor_v2, or domain kit manifests.
- Do not touch The_Fork or Fork2.
- Do not deploy.
- Do not change provider/model config.

## Allowed sources for prebuilt RAG documents

Prebuilt RAG packs are **platform-curated reference collections**, not enterprise/client data. Allowed sources:

1. **Public domain or government publications** explicitly marked as public domain.
2. **Openly licensed reference material** with a clear license identifier (e.g., CC0, CC-BY, MIT for code/docs, Apache-2.0).
3. **Statutes and regulations** published by official government bodies in jurisdictions where redistribution of the text itself is permitted.
4. **Project-generated templates and guidance** authored by Cerebrum and released under an open license.

Disallowed sources:

1. Proprietary or commercial content without a documented license.
2. Client-specific or enterprise-private documents.
3. Web-scraped content of uncertain license.
4. Paid or subscription databases unless a specific redistribution agreement exists.

Every document added to a prebuilt RAG pack must carry a `source_record` that names the origin, license, and retrieval date.

## Source authority and licensing metadata

Extend each RAG pack in `rag_packs.json` with a `source_policy` section:

```json
{
  "source_policy": {
    "allowed_source_types": ["statutes", "regulations", "templates", "guidance", "domain_reference"],
    "default_license": "CC0-1.0",
    "requires_attribution": false,
    "prohibited_sources": ["proprietary", "client_private", "subscription"],
    "authority_levels": [
      {
        "level": "official",
        "description": "Text published by a government or standards body.",
        "examples": ["statutes", "regulations", "official guidance"]
      },
      {
        "level": "platform",
        "description": "Text authored or curated by Cerebrum and released under an open license.",
        "examples": ["templates", "domain_reference"]
      }
    ]
  }
}
```

Each document added later will include:

```json
{
  "source_record": {
    "url": "https://example.gov/regulation/123",
    "title": "Example Regulation",
    "authority_level": "official",
    "license": "CC0-1.0",
    "retrieved_at": "2026-07-10T00:00:00Z",
    "checksum": "sha256:...",
    "notes": ""
  }
}
```

## Collection ID mapping to domains

Each RAG pack already declares:

```json
{
  "id": "legal_core_rag",
  "domain": "legal",
  "collection_id": "prebuilt_legal_core"
}
```

The contract locks this mapping:

- `collection_id` is the stable identifier for the vector/embedding collection.
- `domain` is the single source-of-truth domain that owns the pack.
- One domain has exactly one prebuilt core RAG pack.
- `collection_id` must not change after first ingestion; changing it creates a new collection.
- Ingested chunks must store `collection_id` and `rag_pack_id` as metadata so retrieval can scope by pack or by domain.

## Duplicate protection

Duplicate protection applies at two levels:

### Document-level

A document is considered a duplicate if:

1. Its `source_record.checksum` matches an existing document in the same collection, OR
2. Its `source_record.url` and `source_record.retrieved_at` match within the same collection.

On duplicate detection:

- If the existing document has the same checksum and source metadata, skip re-ingestion.
- If the existing document differs only in retrieval date but the checksum matches, skip re-ingestion and update `last_seen_at`.
- If the checksum differs but URL matches, mark as `updated` and re-ingest, keeping the previous version flagged as `superseded`.

### Chunk-level

Chunks are regenerated whenever a document is (re-)ingested. Chunk IDs are deterministic per ingestion run: `sha256(collection_id + document_id + chunk_index + chunk_text)`. This makes chunk replacement idempotent.

## Ingestion status lifecycle

Replace the current single `ingestion_status` string with a small state machine:

```text
not_ingested → queued → ingesting → indexed
                      ↘ failed
```

Status definitions:

- `not_ingested`: metadata only, no documents queued.
- `queued`: documents are registered and awaiting ingestion worker.
- `ingesting`: worker is actively processing documents and creating chunks.
- `indexed`: all queued documents are processed and embeddings are available for retrieval.
- `failed`: ingestion stopped due to an error; requires manual review before retry.

Status is stored per pack in `rag_packs.json`:

```json
{
  "ingestion_status": {
    "current": "not_ingested",
    "last_updated": "2026-07-10T00:00:00Z",
    "job_id": null,
    "error": null
  }
}
```

## Chunk-to-collection linkage

Every chunk produced for a prebuilt RAG pack must carry these metadata fields:

```json
{
  "chunk_metadata": {
    "rag_pack_id": "legal_core_rag",
    "collection_id": "prebuilt_legal_core",
    "domain": "legal",
    "document_id": "doc_sha256",
    "source_record": { ... },
    "chunk_index": 0,
    "ingested_at": "2026-07-10T00:00:00Z"
  }
}
```

This makes retrieval scoping simple:

- By domain: filter chunks where `domain == <domain>`.
- By collection: filter chunks where `collection_id == <collection_id>`.
- By pack: filter chunks where `rag_pack_id == <pack_id>`.

## Ingestion job record

Introduce a separate lightweight record for ingestion jobs, stored outside the shelf so the shelf remains metadata-only until ingestion is requested:

```json
{
  "job_id": "rag_ingest_20260710_legal",
  "collection_id": "prebuilt_legal_core",
  "status": "queued",
  "documents_queued": 12,
  "documents_processed": 0,
  "documents_failed": 0,
  "created_at": "2026-07-10T00:00:00Z",
  "updated_at": "2026-07-10T00:00:00Z"
}
```

The shelf stays read-only metadata; job records are runtime state.

## Proposed schema changes to `rag_packs.json`

Add to each pack:

```json
{
  "source_policy": { ... },
  "ingestion_status": {
    "current": "not_ingested",
    "last_updated": "...",
    "job_id": null,
    "error": null
  }
}
```

Keep `fetch_mode: "metadata_only"` until ingestion begins.

## Rollout phases

1. **Phase 1 (this PR):** Write this spec; add `source_policy` and structured `ingestion_status` to `rag_packs.json` as metadata placeholders; update loader validation; add tests.
2. **Phase 2:** Add ingestion job model and queueing mechanism in CerebrumDev.ai.
3. **Phase 3:** Add document ingestion worker with checksum-based duplicate protection.
4. **Phase 4:** Add chunking/embedding pipeline and connect to `knowledge` / `vector_search` blocks.
5. **Phase 5:** Curate first real prebuilt documents under the source policy.

## Tests for Phase 1

- Every RAG pack has a `source_policy` with allowed source types.
- Every RAG pack has `ingestion_status.current == "not_ingested"`.
- `collection_id` is unique and follows the `prebuilt_<domain>_core` pattern.
- `rag_pack_id`, `collection_id`, and `domain` are consistent within each pack.
- Loader validates the new fields.

## Files touched in Phase 1

- `docs/superpowers/specs/2026-07-10-rag-ingestion-contract.md` (this file)
- `block_store/shelves/rag_packs.json` in Cerebrum-Blocks
- `app/core/rag_pack_loader.py` in Cerebrum-Blocks
- `tests/core/test_rag_pack_loader.py` in Cerebrum-Blocks

## Risks

- Source licensing for real documents is a legal task, not just engineering.
- Deterministic chunk IDs may need adjustment if chunking strategy changes.
- This spec does not yet cover enterprise/client-private RAG; that is a later layer.
