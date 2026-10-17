# The_Fork Baseline Inspection

> **Source commit:** `1cb621c7aed05ad67c8356f4f7b863aa655af1a3`  
> **Pinned for:** Automotive Safety Intelligence Pilot platform generation  
> **Inspected:** 2026-07-11  
> **Previous pins:** `008d5e7a53654d401ce60372212f5245a8067c76`, `31c1f9daf8b70ee115125c50e3e32fe34a381e70`

---

## Repository layout

```text
The_Fork/
├── app/                        # FastAPI backend (NOT backend/app/)
│   ├── main.py                 # App factory, router mounting, middleware
│   ├── core/                   # Business logic
│   │   ├── models.py           # SQLAlchemy ORM + pgvector chunk classes
│   │   ├── db.py               # SQLAlchemy engine/session factory
│   │   ├── rag/
│   │   │   ├── embeddings.py   # sentence-transformers / model2vec embedder
│   │   │   ├── vector_store.py # pgvector + BM25 hybrid store
│   │   │   └── retriever.py    # High-level retrieval + identifier extraction
│   │   ├── drive_auth.py       # Encrypted Drive token store
│   │   ├── file_crypto.py      # Fernet encryption (DATA_ENCRYPTION_KEY)
│   │   ├── projects.py         # Project/document CRUD + ownership
│   │   └── users.py            # User CRUD, password hashing, JWT
│   ├── routers/
│   │   ├── chat.py             # /chat, /chat/stream, /v1/chat/stream
│   │   ├── drive.py            # /v1/drive/*, /v1/projects/{id}/drive/*
│   │   ├── admin.py            # /v1/admin/* (API-key + admin role)
│   │   ├── projects.py         # /v1/projects/*
│   │   └── ...
│   ├── dependencies.py         # require_user, require_api_key
│   └── static/
├── alembic/                    # Database migrations (NOT migrations/)
│   └── versions/
├── frontend/
│   └── src/
│       ├── App.tsx             # React Router: /, /projects/:id, /admin
│       ├── pages/
│       │   ├── Login.tsx
│       │   ├── Projects.tsx
│       │   ├── ProjectWorkspace.tsx
│       │   └── AdminPage.tsx
│       ├── auth/
│       ├── components/
│       └── theme/
├── Dockerfile
├── render.yaml
├── docker-compose.yml
└── .env.example
```

---

## Authentication and authorization

- **Primary auth:** API key via `require_api_key` + JWT user sessions via `require_user`.
- **User model:** `app.core.models.User` (`id`, `email`, `password_hash`, `role` in `{user, admin}`).
- **Admin gating:** `app.routers.admin._require_admin(auth)` checks `auth["role"] == "admin"`.
- **Bootstrap admin:** `BOOTSTRAP_USER_EMAIL` + `BOOTSTRAP_USER_PASSWORD` create first admin at startup.
- **Project ownership:** `app.core.projects.Project.user_id` FK to `users.id`; endpoints return 404 (not 403) for unowned projects.

---

## Embedding model and vector namespace

- **Embedder:** `app.core.rag.embeddings.get_embedder()`.
- **Default model (env unset):** `minishlab/potion-base-8M` via `model2vec` → 256 dims.
- **Production-qualified model (when configured):** `BAAI/bge-small-en-v1.5` via `sentence-transformers` → 384 dims.
- **Default namespace:** `v2` (env `RAG_VECTOR_NAMESPACE`).
- **Resulting table name:** `chunks_v2`.
- **Important:** Setting `RAG_VECTOR_NAMESPACE=chunks_v2` would create `chunks_chunks_v2`.
- **Schema dimension:** Legacy `chunks` table is `vector(256)`; namespaced `chunks_v2` uses dynamic dimension from embedder at table creation.
- **Identity guard:** Every chunk row stores `embedding_model`, `embedding_dim`, `embedding_normalized`; namespace refuses mixed models.

---

## Database and migrations

- **ORM:** SQLAlchemy 2.0 with `DeclarativeBase`.
- **Backend:** PostgreSQL in production; SQLite fallback for local dev/tests.
- **Vector extension:** `pgvector` (`vector(dim)` columns).
- **Migration tool:** Alembic (`alembic/` directory).
- **BM25 on Postgres:** `text_search` tsvector column + GIN index added by Alembic 0003 for `chunks`; namespaced tables get the same column at runtime in `vector_store._ensure_schema`.
- **BM25 on SQLite:** FTS5 external-content virtual table + triggers.

---

## Hybrid retrieval

- **Module:** `app.core.rag.retriever`.
- **Public function:** `retrieve(query, project_id, k=5, intent=None)`.
- **Components:**
  - Semantic search via `vector_store.search()` (pgvector cosine or numpy fallback).
  - BM25 search via `vector_store.bm25_search()`.
  - RRF fusion when `RAG_HYBRID_SEARCH=true` and `query_text` supplied.
  - Identifier extraction for construction reference codes (VO, RFI, NCR, PRC, etc.).
  - General-knowledge merge from `RAG_GENERAL_KNOWLEDGE_PROJECTS` (default `training_material`).
- **Env knobs:** `RAG_GK_SCORE_MARGIN`, `RAG_OWN_DOC_BOOST`, `RAG_GK_TOPK_CAP`, `RAG_GK_LEXICAL_FOLD`.

---

## Chat and SSE contract

- **Streaming endpoint:** `POST /v1/chat/stream`.
- **SSE event types emitted today:**
  - `start`
  - `route`
  - `token` (single-word chunks)
  - `iteration`, `tool_call`, `tool_result`, `final` (heavy reasoning)
  - `error`
  - `end`
- **Citation support:** Not present today. Project document snippets are injected into prompt context via `_with_doc_search`.
- **Heartbeat:** Env `CHAT_STREAM_HEARTBEAT_SECONDS=15`.

---

## Google Drive integration

- **Module:** `app.routers.drive`.
- **OAuth flow:**
  - `GET /v1/drive/connect` → returns `auth_url`.
  - Google redirects to `GET /v1/drive/callback`.
  - Token encrypted and stored per-user by `app.core.drive_auth`.
- **Scope:** `https://www.googleapis.com/auth/drive.readonly`.
- **Existing project routes:**
  - `POST /v1/projects/{id}/drive/import` — single file.
  - `POST /v1/projects/{id}/drive/index-folder` — recursive folder import (background task).
  - `GET /v1/projects/{id}/drive/index-folder/job/{job_id}` — poll status.
- **Encryption:** `app.core.file_crypto` using `DATA_ENCRYPTION_KEY` (Fernet).

---

## Project and document model

- **Project:** `Project` table (`id`, `name`, `client`, `status`, `user_id`, `is_approved`, `origin`).
- **Document:** `Document` table (`id`, `project_id`, `original_name`, `stored_as`, `file_path`, `doc_type`, `doc_role`, `size`, `content_sha256`, `metadata`).
- **Upload/indexing:** `app.routers.projects` handles uploads; `app.core.doc_index` extracts, chunks, and indexes into `chunks_<namespace>`.

---

## Admin endpoints

- **Base:** `/v1/admin/*`.
- **Auth:** `require_api_key` + `_require_admin`.
- **Examples:** `/v1/admin/debug/doc-extract`, `/v1/admin/projects/{id}/approve`, `/v1/admin/corpus/collections`, `/v1/admin/debug/pilot-preflight`.

---

## Frontend

- **Framework:** React + React Router.
- **Routes:** `/login`, `/`, `/projects/:id`, `/admin`.
- **Auth context:** `frontend/src/auth/AuthContext`.
- **Construction branding present in:** `Projects.tsx`, `ProjectWorkspace.tsx`, `AdminPage.tsx`, chat components, document labels.

---

## Deployment

- **Primary:** Render web service (`render.yaml`).
- **Container:** `Dockerfile`.
- **Local stack:** `docker-compose.yml`.
- **Required env (production):** `SECRET_KEY`, `DATABASE_URL`, `DATA_ENCRYPTION_KEY`, `BOOTSTRAP_USER_EMAIL`, `BOOTSTRAP_USER_PASSWORD`.
- **Optional but important:** `REDIS_URL` for multi-worker deployments.

---

## Test and CI commands

- **Backend tests:** `pytest tests/`
- **Frontend:** `npm run lint`, `npm run build`
- **CI:** `.github/workflows/test.yml` with Postgres + pgvector service.

---

## Changes since previous pin (`008d5e7`)

- `app/main.py` now requires `DATABASE_URL` in production (`ENV=production`); without it the app hard-fails at startup instead of silently falling back to an empty SQLite database.

## Automotive pilot implications

- The generator must copy from `app/` (not `backend/app/`).
- Migrations must come from `alembic/`.
- BGE-small 384 must use a fresh `chunks_v2` namespace or a migration that sets `vector(384)`.
- Drive token encryption must reuse `DATA_ENCRYPTION_KEY`.
- New folder-binding/sync routes should extend the existing `drive.py` engine.
- Admin foundation-pack controls fit the existing `/v1/admin/*` pattern.
- Generated automotive deployments must set a real `DATABASE_URL` in production; the SQLite fallback is no longer permitted.
