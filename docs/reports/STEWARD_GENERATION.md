# Steward generation report

**Blueprint:** `blueprints/steward/steward.v1.yaml` (predefined golden)  
**Output:** `factory_outputs/Projectsix`  
**Generator:** Factory CLI + Product Architect pipeline  
**Sibling:** https://github.com/bopoadz-del/Projectsix (`main` @ `cf299d9`)  
**Live:** https://projectsix.onrender.com (Render `srv-d9fl136rnols73c72nf0`)

## Emitted artefacts

- Kernel contract vendored under `app/cerebrum_product_kernel/`
- Actions per planned capability
- Hats adapted from RetailClient patterns (`estate.base` + `estate.hat.*`)
- Workflows: ops loop, evidence chain, portfolio rollup
- UI modules under `frontend/src/modules/`
- Honest connector stubs (`not_implemented`)
- Dual-registered blocks under `vendor/blocks/`
- Dual RAG (Layer 1 SOP + Layer 2 estate docs) with ingest/query/citations
- Provenance: `docs/provenance/provenance.json`

## Live status (2026-07-21)

Generation + sibling push + Render verify are certified — see
`docs/factory/STEWARD_CERTIFICATION.md`.

**Embeddings honesty:** live runtime uses `local_feature_hash_v1` (JSONL indices).
Production Postgres/pgvector + FastEmbed governed packs are tracked in issue `#73`.

## Note

Steward is a **Factory product output**. Architecture today is predefined via golden
blueprint; the Product Architect API is the path for the in-Factory agent to
draft/plan/regenerate the same artefact. Do not hand-edit the sibling repo.
