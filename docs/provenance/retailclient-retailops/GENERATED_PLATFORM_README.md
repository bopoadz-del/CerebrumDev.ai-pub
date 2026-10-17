# CEREBRUM RetailOps — Generated Platform

RetailOps is a **generated, deployable retail-operations platform**. It ingests
fictional retail records, answers document-grounded questions with citations,
and executes three retail deliverable actions with full audit evidence — all
served through a generated business UI.

- **Platform runtime** (this package, `app/retailops/`) — owned by CerebrumDev.ai.
- **Action contract + Retail kit** — owned by [Cerebrum-Blocks], vendored here at
  the pinned commit recorded in `BLOCKS_COMMIT` (`app/retailops/__init__.py`) under
  `app/retailops/kit/`.

## Architecture

```
                     ┌──────────────── Business UI (React/Vite) ───────────────┐
                     │  Overview · Documents · Assistant · Actions · Audit      │
                     └───────────────────────────┬─────────────────────────────┘
                                                  │ HTTP (X-User/Tenant/Project)
                     ┌────────────────────────────▼────────────────────────────┐
   /v1/ask ─────────▶  LangGraph orchestrator                                   │
                     │   classify → (retrieve | resolve) → execute → synthesize │
                     │            → persist_audit                               │
   /v1/actions ──────▶  Generic ActionRegistry + execute_action (from kit)      │
   /v1/documents ────▶  Ingestion (PDF/DOCX/TXT/MD/CSV/XLSX)                     │
   /v1/audit ────────▶  Audit (action_runs)                                     │
                     └───────┬───────────────────────────────┬─────────────────┘
                             │ hybrid retrieval               │ providers
                     ┌───────▼─────────┐             ┌────────▼─────────┐
                     │ PostgreSQL 16   │             │ Azure OpenAI /   │
                     │ + pgvector      │             │ OpenAI-compat /  │
                     │ (vector+tsvector)│            │ deterministic fake│
                     └─────────────────┘             └──────────────────┘
```

## Database & retrieval

- **Store:** PostgreSQL 16 + pgvector. Tables: `tenants`, `users`, `projects`,
  `documents`, `document_chunks`, `action_runs`, `conversations`, `messages`
  (see `models.py`). Managed by **Alembic** (`migrations/`, revision
  `0001_initial`), applied automatically on container startup.
- **`document_chunks`** carries a dense `embedding vector(384)` (pgvector) **and**
  a generated `text_tsv tsvector` lexical column, plus provenance
  (`source_filename`, `page`, `sheet`, `row_start`, `row_end`, `chunk_ordinal`,
  `metadata`).
- **Hybrid retrieval** (`retrieval.py`): pgvector cosine similarity + Postgres
  full-text `ts_rank_cd` (a documented BM25-equivalent), fused with **Reciprocal
  Rank Fusion** (`score = Σ 1/(k+rank)`). Tenant + project filtering is applied
  **before** fusion (fail-closed). Results are structured citations with per-signal
  vector/lexical scores and fused rank.

## LangGraph orchestration

`orchestrator.py` builds a compiled `StateGraph` with six nodes:
`classify_request → (retrieve_context | resolve_action) → execute_action →
synthesize_response → persist_audit`. Conditional edges separate the **RAG path**
(document/factual questions) from the **action path** (registered deliverables)
and **unsupported** requests. The classifier is domain-neutral and anti-hijack:
a factual question is answered via RAG even when a matching action topic exists;
an imperative naming a registered deliverable routes to the action. Trusted
tenant/project/permission fields in the graph state are never replaced by model
output. Tests run with a deterministic fake provider.

## Action contract

The generic contract (vendored from Cerebrum-Blocks) defines `ActionStatus`,
`ActionContext` (trusted scope), `ActionSpec`, `ActionResult`, an `ActionRegistry`
(discovery, duplicate/namespace/handler/schema validation, exact resolution) and
`execute_action` (enforces domain allowance, permissions, confirmation, input
and output validation; converts exceptions to `execution_error` without leakage).
Action ids are namespaced `<domain>.<action>`. Retail ships three actions:
`retail.generate_operations_brief`, `retail.analyse_inventory_risk`,
`retail.check_promotion_compliance`. **Adding an action requires no router change.**

## LLM providers / Azure OpenAI

`providers.py` exposes a provider interface with an **Azure OpenAI** adapter, an
**OpenAI-compatible** fallback, and a **deterministic fake** provider (used for
tests and the credential-free local pilot). Configure Azure via env (no secrets
in code):

```
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_API_VERSION=2024-06-01
AZURE_OPENAI_DEPLOYMENT=...
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=...
RETAILOPS_LLM_PROVIDER=azure   # or auto (default) / openai / fake
```

Provider failures raise `ProviderError` and never produce fabricated action output.

## Local runbook (no Docker)

```bash
# 1. Postgres 16 + pgvector reachable; set the URL
export RETAILOPS_DATABASE_URL="postgresql+psycopg://retailops:<password>@localhost:5432/retailops"

# 2. Install deps
pip install -r backend/app/retailops/deploy/requirements.txt

# 3. Boot the runtime (migrations run automatically on startup)
uvicorn app.retailops.app_factory:app --host 0.0.0.0 --port 8000   # from backend/

# 4. (optional) load the fictional fixture pack
python -c "from app.retailops.db import init_engine,session_scope; init_engine(); \
from app.retailops.fixtures.loader import load_fixtures; \
from app.retailops.db import session_scope; \
import json; \
s=None
"
```

Then open http://localhost:8000/ and paste a project id.

## Docker runbook

```bash
# Build (from repo root) and run the full stack (Postgres + app):
docker compose -f backend/app/retailops/deploy/docker-compose.yml up --build
# App on http://localhost:8000/, /health returns {"status":"ok",...}
```

The image is multi-stage (frontend build + lean Python runtime), runs as a
non-root user, binds `0.0.0.0:$PORT`, and fails startup if mandatory migrations
fail.

## Render deployment runbook

`deploy/render.yaml` is a Blueprint that provisions managed **Postgres 16**
(pgvector) and one Docker **web service** using the same Dockerfile. Secrets
(`AZURE_OPENAI_*`) use `sync: false`. Deploy by pointing Render at this repo and
the blueprint, or via the Render MCP/CLI. The service health check is `/health`.

## API examples

```bash
H='-H X-User-Id:u_ops_manager -H X-Tenant-Id:northstar -H X-Project-Id:<PID> -H Content-Type:application/json'
curl -s $H localhost:8000/v1/actions
curl -s $H -d '{"question":"What is the escalation process for a stock discrepancy?"}' localhost:8000/v1/ask
curl -s $H -d '{"arguments":{"period":"daily"}}' localhost:8000/v1/actions/retail.generate_operations_brief/execute
curl -s $H localhost:8000/v1/audit
```

## Fixture data

`fixtures/northstar/` — fictional **Northstar Global Retail** data for two
canonical projects: *Northstar Riyadh Region* and *Northstar Melbourne Region*.
Covers PDF/DOCX/MD/TXT/CSV/XLSX and every pilot scenario (stock discrepancy
escalation, promotion display rules, supplier notice, store incident, inventory
risk, promotion compliance, operations brief, missing-dependency handling,
project isolation). Load with `fixtures.loader.load_fixtures(session)`. **No real
client/employer data is used.**

## Security & isolation

- Trusted context (tenant/project/permissions) is resolved server-side from the
  authenticated principal; model/caller arguments can never set trust scope
  (reserved keys are stripped and reported).
- Every tenant/project query is filtered by both ids; retrieval is fail-closed.
  Negative isolation tests assert one project cannot read another's evidence.
- Audit records store an input hash and output metadata only — never secrets.

## Known limitations

- Default embeddings are a deterministic feature-hash (great for reproducible
  tests/pilots). Set `RETAILOPS_EMBED_BACKEND=fastembed` + add `fastembed` for
  higher-quality ONNX embeddings (re-embed after switching; dimension is fixed
  at 384).
- The fake provider grounds strictly on retrieved context; connect Azure/OpenAI
  for fluent natural-language synthesis.
- `ivfflat` index uses a fixed `lists=100`; tune for larger corpora.

## Two-minute demo script

1. `docker compose -f backend/app/retailops/deploy/docker-compose.yml up --build`.
2. Load the Northstar fixtures; copy the Riyadh project id.
3. Open http://localhost:8000/, paste the project id.
4. **Overview** — 7 documents, DB health ok.
5. **Documents** — all six formats processed.
6. **Assistant** — ask "What is the escalation process for a stock discrepancy?"
   → grounded answer with citations.
7. **Actions** — run *Analyse Inventory Risk* → success with stockout/anomaly
   findings and evidence; try it on an empty project → `dependency_required`.
8. **Audit** — see the persisted run with duration, status and evidence count.

## Resume project paragraph (AI Platform Engineer)

> Designed and built CEREBRUM RetailOps, a generated multi-tenant retail AI
> platform (FastAPI, PostgreSQL 16 + pgvector, SQLAlchemy/Alembic, LangGraph,
> React/TypeScript). Implemented hybrid retrieval (pgvector semantic + Postgres
> lexical fused with reciprocal rank fusion) with strict tenant/project isolation
> and structured citations, a domain-neutral typed action contract with a generic
> registry/execution engine and audit trail, and a LangGraph orchestrator with
> anti-hijack RAG-vs-action routing. Delivered a pluggable LLM provider layer
> (Azure OpenAI + deterministic fake), containerized the hardened package for
> Docker/Render, and validated the full pipeline with an automated PostgreSQL-backed
> test suite and an end-to-end product demo.

[Cerebrum-Blocks]: https://github.com/bopoadz-del/Cerebrum-Blocks
