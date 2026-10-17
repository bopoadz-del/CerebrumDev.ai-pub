# Implementation Plan: Automotive Safety Intelligence Pilot — PR 1 (Revised)

> **Plan file:** `docs/superpowers/plans/2026-07-11-automotive-pilot-pr1-domain-platform-generation.md`  
> **Parent design spec:** `docs/superpowers/specs/2026-07-11-automotive-safety-intelligence-pilot-design.md`  
> **Branch:** `feat/rag-vector-index-dry-run`  
> **PR title:** `feat(platform): domain-neutral Fork template, automotive kit and Google Drive connector`  
> **Target repo:** `bopoadz-del/CerebrumDev.ai` (generator); changes also land in `bopoadz-del/Cerebrum-Blocks` (kit) and `bopoadz-del/The_Fork` (template baseline, read-only pin)  
> **Estimated effort:** large

---

## Outcome

PR 1 creates the **reusable generation machinery** for the Automotive Safety Intelligence Pilot:

- A domain-neutral platform template derived from The_Fork pinned at commit `008d5e7a53654d401ce60372212f5245a8067c76`.
- A versioned automotive platform manifest consumed by the generator.
- Cerebrum-Blocks `automotive_v2` kit hardening (prompts, schemas, source manifest, evaluation definitions) in the existing Blocks layout.
- A multi-user Google Drive capability that reuses The_Fork's proven Drive OAuth/tokens and exposes a CerebrumDev.ai factory wrapper.
- A reproducible generator that emits a deployable automotive platform package containing the Fork-derived runtime.

No live construction data, secrets or frontend UI conversion happens in this PR. PR 2 and PR 3 target the **generated platform package**, not the CerebrumDev.ai factory backend.

---

## Scope guard

**In scope**

- Inspect and pin The_Fork baseline.
- Create/extend domain-neutral platform generator in CerebrumDev.ai.
- Define automotive platform manifest schema and validation.
- Harden Cerebrum-Blocks automotive kit in existing `block_store/kits/automotive/` and `app/blocks/automotive_v2.py` locations.
- Reuse The_Fork Drive implementation (`app/routers/drive.py`, `app/core/drive_auth.py`, `app/core/file_crypto.py`) in the generated platform.
- Add CerebrumDev.ai factory Google Drive workspace-binding routes (thin wrapper around the same connector core).
- Add generated-platform Drive module wiring and manifest feature flag.
- Add ownership/isolation tests for Drive connections.
- Add generation reproducibility test.

**Out of scope**

- NHTSA harvesting and embedding/indexing (PR 2).
- Frontend conversion and E2E browser tests (PR 3).
- Live deployment (PR 3).
- Changes to The_Fork `main` production branch (it is only pinned and read as a baseline).

---

## Files to inspect before writing

```text
The_Fork/                      (pinned at 008d5e7a53654d401ce60372212f5245a8067c76)
  app/main.py
  app/routers/
  app/core/models.py
  app/core/rag/
  app/core/drive_auth.py
  app/routers/drive.py
  app/core/file_crypto.py
  frontend/src/
  alembic/
  Dockerfile
  .env.example

Cerebrum-Blocks/
  block_store/kits/automotive/manifest.json
  app/blocks/automotive_v2.py
  app/containers/automotive.py
  block_registry/

CerebrumDev.ai/
  backend/app/core/platform_packager.py
  backend/app/main.py
  backend/app/routers/
  backend/app/models/
  backend/app/config.py
  backend/requirements.txt
  backend/tests/
  docs/superpowers/specs/2026-07-11-automotive-safety-intelligence-pilot-design.md
```

---

## Part A — Pin and inspect The_Fork baseline

### 1. Record pinned baseline

Create:

```text
docs/superpowers/records/2026-07-11-fork-baseline-inspection.md
```

Capture:

```text
source commit: 008d5e7a53654d401ce60372212f5245a8067c76
The_Fork layout: app/ (not backend/app/), alembic/ (not migrations/)
active embedding model: BAAI/bge-small-en-v1.5 in production data (when configured)
active embedding dimensions: 384 (when BGE configured; schema default is 256)
active vector namespace: v2 (table name chunks_v2, not namespace literal "chunks_v2")
database schema summary
hybrid retrieval implementation location: app/core/rag/retriever.py
authentication implementation: API key + optional JWT users
project ownership model
admin permissions model
audit model
frontend routes
SSE chat contract
source citation contract (to be added in PR 2)
deployment configuration
test and CI commands
```

### 2. Add pinned commit reference to generator config

Create:

```text
backend/app/platform_generator/fork_baseline.py
```

```python
FORK_BASELINE_COMMIT = "008d5e7a53654d401ce60372212f5245a8067c76"
```

---

## Part B — Automotive platform manifest schema

### 1. Manifest model

Create:

```text
backend/app/models/platform_manifest.py
```

```python
class AutomotivePlatformManifest(BaseModel):
    schema_version: str = "1.0.0"
    platform_id: str = "automotive-safety-intelligence"
    platform_name: str = "Automotive Safety Intelligence"
    domain: str = "automotive"
    domain_kit: str = "automotive"
    domain_block: str = "automotive_v2"
    formula_block: str = "formula_executor_v2"
    foundation_rag_pack: str = "automotive_core_rag_v1"
    foundation_collection: str = "automotive_core_v1"
    client_overlay_enabled: bool = True
    client_collection_strategy: str = "project_scoped"
    reference_identifiers: list[str] = [
        "nhtsa_campaign_number",
        "odi_number",
        "investigation_number",
        "make",
        "model",
        "model_year",
        "component",
        "manufacturer",
    ]
    branding: BrandingConfig
    features: FeaturesConfig
```

### 2. Validation

Add:

```python
def validate_manifest(manifest: AutomotivePlatformManifest) -> None: ...
```

Fail fast on:

```text
missing required blocks
unknown domain kit
unrecognized reference identifiers
invalid collection strategy
missing branding fields
missing feature flags
```

### 3. Example manifest file

Create:

```text
docs/superpowers/fixtures/automotive_platform_manifest.json
```

---

## Part C — Domain-neutral platform template generator

### 1. Generator module

Create or extend:

```text
backend/app/platform_generator/__init__.py
backend/app/platform_generator/generator.py
backend/app/platform_generator/template_filters.py
backend/app/platform_generator/renderers.py
backend/scripts/generate_automotive_platform.py
```

**Relationship to existing packager:** `backend/app/core/platform_packager.py` vendors Cerebrum-Blocks engines and produces engine-only deployment packages. The new `backend/app/platform_generator/` is a separate **Fork-derived platform generator** that:
- Consumes The_Fork as the runtime baseline.
- Applies manifest-driven branding and domain customization.
- May call `platform_packager.py` to embed the Cerebrum-Blocks engine/automotive blocks into the generated platform.
- Emits a deployable application package (Dockerfile, alembic, frontend), not just an engine bundle.

Keep responsibilities separate: `platform_packager.py` remains the engine bundler; `platform_generator` becomes the product-template generator.

### 2. Template rules

The generator copies The_Fork baseline and applies transformations:

- Replace construction branding strings using manifest `branding`.
- Replace construction reference identifiers with automotive identifiers.
- Remove construction-only quick actions and labels.
- Keep authentication, project ownership, admin gating, audit, SSE chat, citations (stub), document upload.
- Keep Postgres, pgvector, BM25, hybrid retrieval, health checks, metrics.
- Keep deployment files but rename service names to automotive pilot.
- Preserve The_Fork layout (`app/`, `alembic/`, `frontend/src/`, `Dockerfile`, etc.).

### 3. Parameterized strings

Create a mapping file:

```text
backend/app/platform_generator/construction_to_automotive.json
```

Example entries:

```json
{
  "The Fork": "{{ product_name }}",
  "Construction Workspace": "{{ workspace_name }}",
  "training_material": "automotive_core_knowledge",
  "RFI": "recall",
  "NCR": "complaint",
  "VO": "investigation",
  "BOQ": "safety_rating"
}
```

### 4. Reproducibility

Generation must be deterministic given:

```text
pinned Fork commit
pinned Cerebrum-Blocks commit
platform manifest
```

Add a hash of inputs to generated output:

```text
generated/automotive-safety-intelligence/.generation_inputs_hash
```

### 5. Generated package sanitization

Before emitting the package, strip:

```text
.env files
runtime data/
construction project records
tokens or encrypted files
uploaded documents
```

---

## Part D — Cerebrum-Blocks automotive kit hardening

### 1. Files to add/update in Cerebrum-Blocks

Use the existing layout:

```text
block_store/kits/automotive/manifest.json   (update version and add RAG pack refs)
app/blocks/automotive_v2.py                 (add normalization/schemas if needed)
app/containers/automotive.py                (add container metadata)
block_store/kits/automotive/prompts/assistant_v1.txt
block_store/kits/automotive/schemas/recall.json
block_store/kits/automotive/schemas/complaint.json
block_store/kits/automotive/schemas/investigation.json
block_store/kits/automotive/schemas/safety_rating.json
block_store/kits/automotive/schemas/vehicle_identity.json
block_store/kits/automotive/source_manifest.json
block_store/kits/automotive/evaluation/golden_questions.jsonl
```

Do **not** create a top-level `automotive_v2/` directory.

### 2. Kit manifest update

Update `block_store/kits/automotive/manifest.json` preserving all existing required keys (`name`, `description`, `status`, `author`, `tags`, `source`, `container`, `blocks`, `data`, `core_modules`, `artifacts`, `price_cents`, `install_requires`). Add new fields under a `rag` section:

```json
{
  "id": "automotive",
  "name": "Automotive & Mobility Suite",
  "version": "2.0.0",
  "description": "...",
  "status": "available",
  "author": "bopoadz-del",
  "tags": ["domain", "container", "automotive"],
  "source": {
    "repo": "https://github.com/bopoadz-del/Cerebrum-Blocks",
    "ref": "main",
    "publish_script": "scripts/publish_kit.py"
  },
  "container": {
    "class": "app.containers.automotive.AutomotiveContainer",
    "default_chat_prompt": null
  },
  "blocks": ["pdf", "ocr", "chat", "image", "automotive_v2", "formula_executor_v2"],
  "prompts": [],
  "data": [],
  "core_modules": ["...existing modules..."],
  "artifacts": ["...existing artifacts plus prompts/schemas/source_manifest/evaluation..."],
  "rag": {
    "pack": "automotive_core_rag_v1",
    "source_manifest": "source_manifest.json",
    "schemas": ["schemas/"],
    "prompts": ["prompts/"],
    "evaluation": ["evaluation/golden_questions.jsonl"]
  },
  "price_cents": 0,
  "install_requires": {
    "min_platform_version": "2.0.0",
    "python": ">=3.10"
  }
}
```

Add new files (`prompts/`, `schemas/`, `source_manifest.json`, `evaluation/`) to the `artifacts` list so the kit loader copies them into generated packages.

### 3. Source manifest

Place the authoritative source manifest in:

```text
block_store/kits/automotive/source_manifest.json
```

It defines the NHTSA source families, authority, jurisdiction, and refresh policy. The CerebrumDev.ai harvester consumes this manifest.

### 4. Keep corpus out of Git

Ensure `.gitignore` in Cerebrum-Blocks excludes:

```text
*.csv
*.zip
*.jsonl
harvests/
embeddings/
indexes/
```

Only schemas, manifests, prompts, evaluation definitions, and small fixtures are source-controlled.

---

## Part E — Multi-user Google Drive connector

### 1. Reuse The_Fork Drive implementation in generated platform

The generated platform package copies The_Fork's existing Drive implementation:

```text
app/routers/drive.py          (existing per-user OAuth routes)
app/core/drive_auth.py        (OAuth flow + encrypted token storage)
app/core/file_crypto.py       (Fernet encryption keyed by DATA_ENCRYPTION_KEY)
```

These already support:

- Per-user OAuth.
- Encrypted refresh tokens with `DATA_ENCRYPTION_KEY`.
- Project-scoped imports.
- Disconnect.

Do **not** introduce a new `DRIVE_TOKEN_ENCRYPTION_KEY`.

### 2. CerebrumDev.ai factory Drive wrapper

For the factory, add a thin wrapper that lets a CerebrumDev.ai session bind a Google Drive folder to a workspace config. The actual OAuth/token handling reuses the same connector core (copied/ported into CerebrumDev.ai or imported as a shared package).

Add:

```text
backend/app/core/google_drive_connector.py   (shared core, subset of The_Fork logic)
backend/app/routers/factory_drive.py
```

Routes:

```text
GET  /v1/sessions/{session_id}/drive/auth
GET  /v1/sessions/{session_id}/drive/callback
POST /v1/sessions/{session_id}/drive/disconnect
GET  /v1/sessions/{session_id}/drive/folders
POST /v1/sessions/{session_id}/drive/bind
```

Ownership scope:

```text
session_id
user_id (from session state)
drive_connection_id
folder_binding_id
```

### 3. Generated-platform Drive module

The generated package already includes `app/routers/drive.py`. PR 1 adds:

- Folder binding/sync capability (currently The_Fork imports individual files).
- Manifest feature flag.
- Extended project-scoped routes:

```text
GET  /v1/projects/{project_id}/drive/connect
GET  /v1/projects/{project_id}/drive/callback
POST /v1/projects/{project_id}/drive/bind-folder
GET  /v1/projects/{project_id}/drive/folders
GET  /v1/projects/{project_id}/drive/sync-status
POST /v1/projects/{project_id}/drive/sync
POST /v1/projects/{project_id}/drive/disconnect
```

### 4. Feature flag

Manifest:

```json
{
  "features": {
    "google_drive": {
      "enabled": true,
      "multi_user": true,
      "personal_connections": true,
      "organisation_shared_bindings": true,
      "default_scope": "read_only"
    }
  }
}
```

---

## Part F — Database migrations

Migrations are needed only for the **generated platform** (The_Fork already has `alembic/`). PR 1 may add a new Alembic revision to the generated package template for:

```text
drive_connection
drive_folder_binding
drive_sync_job
drive_file_record
indexed_document (for Drive-imported docs)
audit_event
```

The_Fork already has `users`, `projects`, `documents`, `chunks`, etc. Add the Drive-specific tables as a new migration revision in the generated package template.

CerebrumDev.ai factory continues to use its existing session/file-based persistence; no Alembic/SQLAlchemy stack is added to the factory.

---

## Part G — Tests

Add:

```text
backend/tests/test_platform_manifest.py
backend/tests/test_platform_generator.py
backend/tests/test_factory_drive_isolation.py
backend/tests/test_generated_drive_wiring.py
```

Prove:

- Manifest validates required fields.
- Invalid manifest fails with clear errors.
- Generator produces deterministic output from same inputs.
- Generated package does not contain construction secrets or live data.
- Generated package contains automotive manifest and Drive module.
- Factory Drive OAuth state validation rejects mismatched state.
- Tokens are encrypted and never exposed in API responses.
- Disconnect removes binding and indexed documents.
- One user’s disconnect does not affect another user.

---

## Part H — Verification commands

```bash
cd backend

python -m py_compile \
  app/models/platform_manifest.py \
  app/core/google_drive_connector.py \
  app/platform_generator/generator.py \
  app/routers/factory_drive.py

python -m pytest \
  tests/test_platform_manifest.py \
  tests/test_platform_generator.py \
  tests/test_factory_drive_isolation.py \
  tests/test_generated_drive_wiring.py \
  -q --tb=short

python -m pytest tests -q
```

---

## Part I — Commit plan

Create commits in this order:

1. `feat(platform): pin and inspect The_Fork baseline`
2. `feat(platform): add automotive platform manifest schema and validation`
3. `feat(platform): add domain-neutral Fork-derived platform generator`
4. `feat(automotive): harden Cerebrum-Blocks automotive kit`
5. `feat(drive): add reusable Google Drive connector core and factory wrapper`
6. `feat(drive): add generated-platform folder-binding Drive module`
7. `feat(drive): add generated-platform migrations and manifest feature flag`
8. `test(platform): add manifest, generator, and Drive isolation tests`

---

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Hidden construction coupling in template | Generate and diff against baseline; inspect for remaining construction identifiers. |
| Token encryption key mishandling | Reuse The_Fork `DATA_ENCRYPTION_KEY` and `file_crypto.py`; never log tokens. |
| OAuth redirect URI mismatch | Separate apps/configs for factory vs generated platforms; validate redirect URI. |
| Cerebrum-Blocks repo bloat | Strict `.gitignore`; reject large data files in PR review. |
| platform_packager.py conflict | Document relationship; keep Fork-derived generator separate from engine packager. |

---

## Definition of done

- [ ] The_Fork baseline is pinned and inspected.
- [ ] Automotive platform manifest schema exists and validates.
- [ ] Domain-neutral generator produces reproducible Fork-derived automotive platform package.
- [ ] Cerebrum-Blocks automotive kit contains schemas, prompts, source manifest, evaluation definitions in existing layout.
- [ ] Google Drive connector reuses The_Fork implementation; factory wrapper added.
- [ ] Generated-platform Drive module, migrations, and feature flag present.
- [ ] New tests pass and full backend suite passes.
- [ ] CI passes.
