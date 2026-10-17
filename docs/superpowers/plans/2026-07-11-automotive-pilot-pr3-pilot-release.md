# Implementation Plan: Automotive Safety Intelligence Pilot — PR 3 (Revised)

> **Plan file:** `docs/superpowers/plans/2026-07-11-automotive-pilot-pr3-pilot-release.md`  
> **Parent design spec:** `docs/superpowers/specs/2026-07-11-automotive-safety-intelligence-pilot-design.md`  
> **Branch:** `feat/rag-vector-index-dry-run`  
> **PR title:** `feat(pilot): automotive frontend, admin, deployment and E2E acceptance`  
> **Target repo:** `bopoadz-del/CerebrumDev.ai` (generator + deployment scripts); runtime changes land in generated Fork-derived package  
> **Estimated effort:** large

---

## Outcome

PR 3 delivers a complete, deployed **Automotive Safety Intelligence Pilot** that a user can operate through a browser:

- Automotive-branded frontend derived from The_Fork.
- Login, foundation workspace, streaming chat with citations.
- Project workspace, private document upload, and client overlay retrieval.
- Multi-user Google Drive folder binding/sync for private project documents.
- Admin foundation-pack controls.
- Security and tenant-isolation tests.
- Live deployed instance with health checks and audit visibility.

No construction-specific branding or retrieval assumptions remain active. The public Automotive Core RAG and client-private overlays are visibly separated.

**Important:** The runtime implementation lives in the generated Fork-derived platform package (`generated/automotive-safety-intelligence/frontend/`, `app/`, etc.). PR 3 adds the generator transformations, generated-package runtime code, deployment scripts, and E2E tests.

---

## Scope guard

**In scope**

- Frontend branding conversion in generated platform (strings, navigation, quick actions, empty states, source labels).
- Admin Automotive Core pack status/controls page in generated platform.
- Project-scoped private document upload and indexing in generated platform.
- Google Drive folder binding and sync for private projects in generated platform.
- Layer-aware retrieval blending public foundation + private project evidence.
- Citation panel with `Automotive Core` / `Private Project` labels.
- End-to-end browser tests for critical journeys.
- Security tests for JWT, ownership, non-leaking 404s, upload controls, CORS, etc.
- Generated deployment package and live pilot deployment.
- Final release report.

**Out of scope**

- New foundation corpus harvesting (PR 2).
- New embedding/indexing backend (PR 2).
- Changes to The_Fork `main` production branch.

---

## Files to inspect before writing

```text
The_Fork/frontend/src/
  App.tsx
  pages/
  components/Chat/
  components/LeftPanel.tsx
  components/Admin/
  components/Projects/
  components/Drive/

The_Fork/app/
  routers/projects.py
  routers/drive.py
  routers/admin.py
  core/models.py

CerebrumDev.ai/backend/
  app/platform_generator/
  scripts/generate_automotive_platform.py
  scripts/deploy_automotive_pilot.py
  .github/workflows/ci.yml
```

---

## Part A — Frontend branding conversion

### 1. Manifest-driven branding

The generated platform manifest contains:

```json
{
  "branding": {
    "product_name": "Automotive Safety Intelligence",
    "workspace_name": "Vehicle Safety Workspace",
    "foundation_name": "Automotive Core Knowledge"
  }
}
```

The generator replaces construction strings in the copied frontend files.

### 2. Required string replacements

| Old (construction) | New (automotive) |
|--------------------|------------------|
| The Fork | Automotive Safety Intelligence |
| Construction Workspace | Vehicle Safety Workspace |
| RFI / NCR / VO / BOQ | Recall / Complaint / Investigation / Safety Rating |
| Drawing reference | Campaign / ODI / Investigation number |
| Training material | Automotive Core Knowledge |
| Construction knowledge base | Automotive Core Knowledge |

### 3. Navigation and quick actions

Update sidebar/nav labels:

```text
Vehicle Safety Workspace
Automotive Core Knowledge
My Projects
Admin
```

Quick actions:

```text
Check recalls by vehicle
Search campaign number
Review complaint patterns
Compare vehicle safety records
Summarize an investigation
```

### 4. Empty states and help text

Replace construction examples with automotive examples.

---

## Part B — Admin foundation-pack controls

### 1. Admin page

Create in generated platform:

```text
generated/automotive-safety-intelligence/frontend/src/pages/AdminAutomotiveCorePage.tsx
```

Display pack version, status, harvest timestamp, source families, counts, embedding identity, namespace, evaluation result, activation status.

Buttons:

```text
Build / Resume Pack
Verify Pack
Run Evaluation
Activate Validated Version
Rollback to Prior Version
```

Destructive rebuild requires typed confirmation modal.

### 2. Admin route guard

Reuse The_Fork's existing admin gating (`/v1/admin/*`).

---

## Part C — Project-private document overlay

### 1. Project model

Reuse The_Fork project ownership model. Private documents belong to a project.

Document indexing target:

```text
knowledge_layer: client_private
project_id: <project_id>
```

### 2. Upload flow

Browser journey:

```text
Create Project → Upload Document → Indexing Progress → Ask Question
```

API endpoints already present in The_Fork:

```text
POST   /v1/projects/{project_id}/documents
GET    /v1/projects/{project_id}/documents
GET    /v1/projects/{project_id}/documents/{document_id}/status
DELETE /v1/projects/{project_id}/documents/{document_id}
```

Indexing uses the same BGE-small-en-v1.5 384-dim pipeline as the foundation pack, but into the project-scoped collection.

### 3. Deletion

Deleting a private document removes its chunks and embeddings from the private index. Foundation corpus remains untouched.

---

## Part D — Multi-user Google Drive integration UI

### 1. Connector status

Add to project settings in generated platform frontend:

```text
Connect Google Drive → folder picker → bind folder → sync
```

### 2. Folder picker

Use Google Picker API or Drive file selector.

### 3. Sync and indexing

Extend The_Fork's `app/routers/drive.py` to support folder binding and periodic sync:

```text
POST /v1/projects/{project_id}/drive/bind-folder
POST /v1/projects/{project_id}/drive/sync
GET  /v1/projects/{project_id}/drive/sync-status
```

Files index into `client_private` layer only.

### 4. Disconnect

Revoke OAuth, remove binding, delete indexed documents for that binding only.

---

## Part E — Layer-aware retrieval and citations in chat

### 1. Chat request

When a project is active, include `project_id` in the retrieval request.

```python
retrieval_request = RetrievalRequest(
    query=user_message,
    knowledge_layers=["client_private", "automotive_core_v1"],
    project_id=active_project_id,
)
```

### 2. Citation rendering

Each citation shows:

```text
[Automotive Core] Recall 20V123 — NHTSA
[Private Project] Dealer Service Report — My Project
```

### 3. Ranking behavior

- Project-specific questions: client-private first, automotive core supplement.
- General automotive questions: automotive core first; client-private only if explicitly relevant.

---

## Part F — End-to-end browser tests

Add E2E harness if not present. Add Playwright tests in generated platform:

```text
generated/automotive-safety-intelligence/frontend/e2e/automotive_journeys.spec.ts
```

Required journeys:

```text
1. register or login
2. open foundation workspace
3. ask exact recall question
4. ask broad complaint-pattern question
5. inspect citations
6. create private project
7. upload private document
8. wait for indexing
9. ask private-document question
10. verify private citation
11. verify foundation supplement
12. delete private document
13. verify deleted evidence no longer retrieved
14. admin rebuild/resume test
15. evaluation execution
16. logout and unauthorised access test
17. second-user isolation test
```

---

## Part G — Security tests

Add in generated platform:

```text
generated/automotive-safety-intelligence/tests/test_automotive_security.py
```

Prove:

- JWT enforcement on all routes.
- Admin-route enforcement.
- Project ownership enforcement.
- Non-leaking 404 behavior.
- Upload size/type controls.
- SSRF controls.
- Safe filename handling.
- No filesystem-path exposure.
- No raw exception leakage.
- CORS restrictions.
- Secret scanning.
- Dependency scanning.
- SQL injection checks.
- Stored-content escaping.
- Cross-user project isolation.
- Google Drive token encryption and non-exposure.

---

## Part H — Deployment

### 1. Generated package

PR 1 generator produces a deployable package. PR 3 ensures it contains:

```text
Fork-derived platform runtime
automotive domain manifest
automotive_v2 assets
formula_executor_v2
automotive assistant configuration
foundation-pack configuration
database migrations
frontend branding
deployment files
environment template
health checks
evaluation suite
```

### 2. Live pilot instance

Deploy one isolated instance with:

```text
own database
own persistent storage
own secret key
own admin account
own OAuth config
own vector namespace
own foundation corpus
```

Add deployment script in CerebrumDev.ai:

```text
backend/scripts/deploy_automotive_pilot.py
```

### 3. Environment template

Add to generated package `.env.example`:

```text
DATABASE_URL
SECRET_KEY
BOOTSTRAP_USER_EMAIL
BOOTSTRAP_USER_PASSWORD
RAG_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RAG_EMBEDDING_DIMENSIONS=384
RAG_VECTOR_NAMESPACE=v2
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REDIRECT_URI
DATA_ENCRYPTION_KEY
```

### 4. Health checks

Ensure `/health`, `/ready`, and `/metrics` endpoints exist and pass.

---

## Part I — Verification commands

```bash
# Generated platform backend
cd generated/automotive-safety-intelligence
python -m pytest tests -q

# Generated platform frontend
cd frontend
npm run lint
npm run build
npm run test:e2e

# CerebrumDev.ai factory
cd ../../backend
python -m pytest tests -q
```

---

## Part J — Final release report

After merge, produce:

```text
docs/superpowers/reports/2026-07-11-automotive-pilot-release-report.md
```

---

## Part K — Commit plan

1. `feat(pilot): add frontend branding generator transformations`
2. `feat(pilot): add admin automotive core pack controls to generated platform`
3. `feat(pilot): add project-private document overlay to generated platform`
4. `feat(pilot): add Google Drive folder binding and sync UI`
5. `feat(pilot): wire layer-aware retrieval and citations in chat`
6. `feat(pilot): add deployment script and environment template`
7. `test(pilot): add E2E browser journeys`
8. `test(pilot): add security and isolation tests`
9. `chore(pilot): add final release report`

---

## Definition of done

- [ ] Frontend is automotive-branded with no construction-specific strings active.
- [ ] Admin can view and control the Automotive Core pack.
- [ ] Users can create projects and upload private documents.
- [ ] Google Drive can be connected, bound to a project, and synced.
- [ ] Chat streams answers with separated `Automotive Core` and `Private Project` citations.
- [ ] E2E critical journeys pass.
- [ ] Security tests pass.
- [ ] Deployed pilot instance is live and healthy.
- [ ] Evaluation gates meet targets or deviations are documented.
- [ ] Full backend and frontend CI passes.
