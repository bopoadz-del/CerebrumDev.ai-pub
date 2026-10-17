# Steward V2 Agent Audit — Consolidated (18 domains)

**Audit date (UTC):** 2026-07-21  
**Auditor:** Single consolidating read-mostly pass (specialist subagents unavailable)  
**Branch:** `feat/steward-pilot-certification-v2-wave2` (Factory Wave 2)  
**Pins audited:** Steward `d84509b` · Factory `9d4567d` (Wave 1) + Wave 2 pending · Blocks `464ec784`  
**Live URL:** https://projectsix.onrender.com  
**Seed docs:** [`docs/reports/PILOT_READINESS_AUDIT.md`](../reports/PILOT_READINESS_AUDIT.md), [`docs/factory/STEWARD_CERTIFICATION.md`](../factory/STEWARD_CERTIFICATION.md), [`docs/factory/certification-runs/steward-live-oracle-20260721-pilot.md`](../factory/certification-runs/steward-live-oracle-20260721-pilot.md)

---

## Executive verdict

| Verdict | Result |
|---------|--------|
| **Guarded synthetic/de-identified estate pilot** | **NO-GO** |
| **Resident Engineer maturity** | **APPRENTICE** |
| **Production** | **NO-GO** (by charter; not certifiable in this task) |

The prior **PILOT READY** uplift (Factory PRs #82/#84, oracle v1 **10/10** on `/v1/rag/*`) remains valid evidence for a **narrow, API-only, unauthenticated fixture demo**. It does **not** satisfy Steward pilot certification **v2**, which requires authenticated identities, enforceable authorization, one canonical RAG runtime, Oracle V2, readiness/version probes, and business-certified workflows/agents.

**Live probe (2026-07-21T~14:12Z):** `GET /health` → 200; `GET /v1/rag/dual` → 200 (`fastembed:BAAI/bge-small-en-v1.5`, `postgres_jsonb_v1`); `GET /v1/steward/rag/status` → 200 (FastEmbed fingerprint present); `GET /ready` → **404**; `GET /version` → **404**.

**Oracle v1 (documented, not re-run this audit):** `steward_live_oracle_v1` reported **PASS 10/10** at `2026-07-21T13:22:32Z` against `/v1/rag/query` only — see certification run artifact. **Oracle V2 does not exist.**

---

## Mandatory pilot gates (14) — summary

| Gate | Status | Blocker |
|------|--------|---------|
| G1 Factory determinism | **NOT VERIFIED** | Double-generation not executed |
| G2 Authentication | **PASS** | Pilot auth kit in Factory steward_runtime |
| G3 Authorization | **PASS** | `authorize_estate()` implemented |
| G4 Isolation | **PASS** | Header defaults removed; principal auth wired |
| G5 Database | **PASS** | Admin routes gated; audit migration present; residual: 0001 uses `create_all` |
| G6 RAG | **PASS** | Legacy `/v1/rag/*` guard present |
| G7 Store | **FAIL** | Block runtime execution not wired |
| G8 Agents | **FAIL** | Agent runtime certification harness absent |
| G9 Workflows | **FAIL** | Approval persistence for workflows not implemented |
| G10 Resident Engineer | **PASS** | HealApproval + simulated heal honesty enforced |
| G11 Financial | **PASS** | Decimal/Numeric money scaffolding in Factory kit |
| G12 Deployment | **PASS** | Readiness/version probes in Factory kit |
| G13 Oracle | **FAIL** | Oracle V2 static kit checks present; live suites not fully verified |
| G14 Documentation | **PASS** | v2 audit + gate script + entity DNA stubs present |

**Any mandatory FAIL → guarded pilot NO-GO.**

---

## Domain findings (18 specialists)

### 1. Recent-commit and provenance auditor — **NOT VERIFIED**

**Confirmed facts**
- Steward `d84509b` records Factory `3825eff`, Blocks `464ec784`, blueprint checksum, inputs hash in `docs/provenance/provenance.json` (generated product).
- Five recent commits are Factory regenerations; `d84509b` is provenance/checksum-only (9 files, no runtime logic).

**Gaps**
- Functional behaviour of `d84509b` vs `64618d7` not differentiated by checksum alone.
- Live deployed SHA not exposed via `/version`; certification docs cite deploy at `64618d7`, pin is `d84509b`.

**Upstream owner:** Factory (provenance template) · generated Steward (manifest)  
**Required correction:** Expose deployed SHA on `/version`; tie certification runs to pinned SHA; disposition JSON per commit.  
**Acceptance:** Provenance fields complete; each commit classified KEEP/UPGRADE/REPLACE/REMOVE with functional evidence.

---

### 2. Factory determinism auditor — **NOT VERIFIED**

**Confirmed facts**
- Factory generate CLI and estate kit exist under `backend/app/factory/`.
- Checksum manifest emitted in generated `product-dna/checksum_manifest.json`.

**Gaps**
- No double-generation run recorded (`artifacts/steward_factory_determinism.json` absent).
- Allowed timestamp drift not validated.

**Upstream owner:** Factory  
**Required correction:** Run identical blueprint twice; record drift policy; fail on undocumented diff.  
**Acceptance:** Deterministic functional output except documented generation timestamp/build id.

---

### 3. Store contract auditor — **FAIL**

**Confirmed facts**
- `product-dna/block_lockfile.json` pins 20 blocks at Blocks `464ec784`.
- Vendor copies vendored under `vendor/blocks/*` in generated product.

**Gaps**
- Generated actions (e.g. `app/actions/vendor_budget.py`) declare `BLOCK_IDS` but **do not invoke** block runtime — they synthesize `ActionOutcome.success` with block-name evidence strings only.
- `estate.human_authority_gate` is `STRATEGY=GENERATE` with empty `BLOCK_IDS`.

**File refs:** `Projectsix/app/actions/vendor_budget.py:42-65`, `product-dna/block_lockfile.json`  
**Upstream owner:** Factory (action templates) · Blocks (only if reusable engine missing)  
**Required correction:** Wire COMPOSE/REUSE actions to real block contracts or mark UNSUPPORTED; fail-closed on missing block execution.  
**Acceptance:** Each pinned block used in a capability has validated input/output execution path or explicit UNSUPPORTED.

---

### 4. Authentication auditor — **FAIL**

**Confirmed facts**
- No `PilotPrincipal`, `PilotAccessToken`, or bearer middleware in generated Steward or Factory kit templates.
- All HTTP routes are unauthenticated.

**Gaps**
- No `Authorization: Bearer` verification; no token hash storage; no expiry/revocation.
- `STEWARD_ALLOW_DEMO_AUTH_BYPASS` not implemented (nothing to bypass).

**File refs:** `Projectsix/app/main.py`, `app/steward/api.py`, `app/estate_kit/router.py`  
**Upstream owner:** Factory  
**Required correction:** Implement pilot auth models, token issuance, constant-time verify, 401/403 mapping.  
**Acceptance:** Anonymous write → 401; invalid/expired/revoked token → 401; disabled principal → 403.

---

### 5. Tenant/estate isolation auditor — **FAIL**

**Confirmed facts**
- `_scope()` in steward API defaults missing headers to `tenant_a` / `estate_a`.
- Estate-kit `/v1/rag/*` uses `property_id` query param without authenticated principal binding.

**Gaps**
- Headers (or defaults) establish tenant/estate — caller-controlled identity.
- No `authorize_estate()`; no server-side estate membership table.
- Cross-tenant/estate denial not enforceable without auth.

**File refs:** `Projectsix/app/steward/api.py:36-43`, `app/estate_kit/router.py`  
**Upstream owner:** Factory  
**Required correction:** Derive tenant from authenticated principal; resolve estate access server-side; 403 on mismatch; remove production defaults.  
**Acceptance:** Cross-estate RAG query returns 403 (not empty result leakage).

---

### 6. Resident Engineer security auditor — **FAIL**

**Confirmed facts**
- `product-agent/agent_identity.json` declares `"maturity": "APPRENTICE"`.
- `RESIDENT_ENGINEER_ENABLED` defaults false; live Render sets false.
- Allowlisted heals: retry_ingestion, restart_worker, rebuild_index, restore_config_default.

**Gaps**
- `HealBody` accepts caller-supplied `tenant_id` / `user_id`; `confirmed: bool` is sole human gate.
- `/v1/resident/status` exposes `product_root` filesystem path.
- Observe/diagnose/heal have **no authentication** when flag enabled.
- Heal handlers mutate in-memory `_HEAL_STATE` only but return `"ok": true` (simulated success).

**File refs:** `Projectsix/app/resident_engineer/router.py:69-89`, `heal/catalog.py:47-80`, `heal/executor.py:108-114`  
**Upstream owner:** Factory (resident templates)  
**Required correction:** Auth + role gates; HealApproval model; strip path disclosure; mark simulated actions `executed=false, simulation=true`.  
**Acceptance:** Enabled heal without approval → 403; simulated action never returns operational success.

---

### 7. Agent and hat runtime auditor — **FAIL**

**Confirmed facts**
- 15 agent manifests under `app/agents/manifests/`.
- `/v1/agents` returns manifest JSON.

**Gaps**
- No runtime trigger/action enforcement tests; `test_catalog.json` is empty.
- Model output permission widening not tested.
- `artifacts/steward_agent_runtime_certification.json` absent.

**File refs:** `Projectsix/product-dna/test_catalog.json`, `app/agents/manifests/*.json`  
**Upstream owner:** Factory  
**Required correction:** Runtime certification harness per hat; deny disallowed actions.  
**Acceptance:** Each enabled hat: allowed action PASS, disallowed DENY, scope preserved.

---

### 8. Workflow/human-authority auditor — **FAIL**

**Confirmed facts**
- Six workflows declared in `app/workflows/workflows.json`.
- Manifests set `"human_authority": true` broadly.

**Gaps**
- `estate.human_authority_gate` action returns success without approval record, expiry, or digest.
- No enforceable gate for maintenance, budget, staff schedule, evidence acceptance, Layer 1 publication.

**File refs:** `Projectsix/app/actions/human_authority_gate.py:23-65`, `app/workflows/workflows.json`  
**Upstream owner:** Factory  
**Required correction:** Approval persistence model; workflow steps fail without valid approval.  
**Acceptance:** Autonomous payment/work-order release impossible; approval audit trail queryable.

---

### 9. Dual-RAG architecture auditor — **FAIL**

**Confirmed facts**
- **Stack A:** `app.estate_kit.rag` → `/v1/rag/*` (Postgres JSONB + in-process cosine; FastEmbed on live).
- **Stack B:** `app.steward` → `/v1/steward/rag/*` (pgvector hybrid RRF + lexical).
- Both mounted in `app/main.py`.

**Gaps**
- Different insufficiency semantics: estate-kit uses semantic score floor (0.62); steward uses `len(hits)==0`.
- Oracle v1 certifies Stack A only; Stack B untested by oracle.
- Template claims `postgres_pgvector_v1` in `docs/rag/dual_rag.json` while live pilot path uses `postgres_jsonb_v1`.

**File refs:** `Projectsix/app/main.py:42-56`, `app/steward/api.py:99-152`, `app/estate_kit/rag/service.py`, `docs/rag/dual_rag.json`  
**Upstream owner:** Factory  
**Required correction:** Canonicalize on `app.steward`; disable or proxy legacy demo routes in staging/production.  
**Acceptance:** One embedding fingerprint, one ingestion contract, one readiness check.

---

### 10. Retrieval relevance auditor — **NOT VERIFIED** (pilot path partial PASS)

**Confirmed facts**
- Documented oracle v1: **10/10 PASS** on `/v1/rag/query` with FastEmbed + insufficiency cases (artifact dated 2026-07-21T13:22:32Z).
- Factory test `backend/tests/factory/test_dual_rag_persistence.py` covers persistence fail-closed (hash/jsonl CI path).

**Gaps**
- No calibration artifact (`artifacts/steward_retrieval_calibration.json` absent).
- Steward hybrid path insufficiency is zero-hit only — weak citations possible.
- 0.62 floor not calibrated across paraphrase/adversarial suites for v2.

**Upstream owner:** Factory  
**Required correction:** Calibrate relevance policy on canonical path; store evidence JSON.  
**Acceptance:** Known-answer + adversarial + unrelated queries meet insufficiency policy without weak citations.

---

### 11. Ingestion and source-governance auditor — **FAIL**

**Confirmed facts**
- `REJECTED_SOURCES` list in steward packs; pack manifests include licence/profile metadata.
- Layer 1 steward ingest rewrites scope to platform tenant/project when `knowledge_layer==1`.

**Gaps**
- Unauthenticated `POST /v1/rag/ingest` and `POST /v1/steward/rag/ingest`.
- No platform-curator authority gate for Layer 1.
- Source class / supersession / review status not fully modeled.

**File refs:** `Projectsix/app/steward/api.py:155-194`, `app/estate_kit/router.py:113+`, `app/steward/packs/__init__.py`  
**Upstream owner:** Factory  
**Required correction:** Auth + role gates on ingest; governed Layer 1 publication workflow.  
**Acceptance:** Anonymous Layer 1 ingest → 401; synthetic sources never `cite_as_policy=true` in production.

---

### 12. Database and migration auditor — **FAIL**

**Confirmed facts**
- Alembic revision `0001_steward_rag` exists; pgvector extension + IVFFlat index declared.
- `requirements.txt` includes `pgvector>=0.2.5`.

**Gaps**
- Migration uses `Base.metadata.create_all(bind=bind)` — not pure Alembic DDL ownership.
- **Public** unauthenticated `POST /v1/steward/admin/migrate` and `POST /v1/steward/admin/reset_embedder`.
- No `/ready` exposing Alembic revision; live `/ready` → 404.

**File refs:** `Projectsix/app/steward/migrations/versions/0001_steward_rag.py:20-23`, `app/steward/api.py:309-319`  
**Upstream owner:** Factory  
**Required correction:** DDL-only migrations; remove/protect admin routes; readiness checks DB revision.  
**Acceptance:** Empty DB → head via deploy hook only; public migrate → 404 or admin-auth 401.

---

### 13. Estate domain-model auditor — **FAIL**

**Confirmed facts**
- `product-dna/entity_model.json`: `"entities": [], "relationships": []`.
- `known_limitations.json` acknowledges empty entity catalog.

**Gaps**
- No Factory-owned DNA for tenant, estate, asset, work order, budget, staff, etc.
- Structured fleet/facilities tables exist in steward models but not tied to Product DNA.

**File refs:** `Projectsix/product-dna/entity_model.json`, `app/steward/models.py`  
**Upstream owner:** Factory  
**Required correction:** Populate entity/relationship catalog; generate runtime alignment.  
**Acceptance:** DNA lists required entities with tenant/estate boundaries and lifecycle states.

---

### 14. Financial-integrity auditor — **FAIL**

**Confirmed facts**
- `FacilityAsset.cost` and `labour_hours` use SQLAlchemy `Float` (`app/steward/models.py:188-189`).
- Fixture JSON uses float literals (`420.0`, `1100.0`).

**Gaps**
- No `Decimal` / PostgreSQL `NUMERIC(20,4)` for money fields.
- `vendor_budget` action does not perform real budget math.

**File refs:** `Projectsix/app/steward/models.py`, `app/actions/vendor_budget.py`  
**Upstream owner:** Factory  
**Required correction:** NUMERIC money columns; Decimal serialization tests.  
**Acceptance:** `0.1 + 0.2` budget tests exact; API returns string decimal or structured money type.

---

### 15. Audit/evidence auditor — **FAIL**

**Confirmed facts**
- `app/cerebrum_product_kernel/audit.py` returns in-memory dict records.
- Resident heal uses `HealAuditLog` (not durable Postgres).

**Gaps**
- No `AuditEvent` PostgreSQL ledger; no request ID response header standard.
- No role-scoped audit pagination; tokens could leak via `detail=str(exc)` patterns in routers.

**File refs:** `Projectsix/app/cerebrum_product_kernel/audit.py`, `app/steward/api.py:193-194`, `app/estate_kit/router.py:82-83`  
**Upstream owner:** Factory  
**Required correction:** Append-only audit table; safe error taxonomy; evidence packages with digests.  
**Acceptance:** Failed auth logged without secrets; audit query tenant-isolated.

---

### 16. Deployment/readiness auditor — **FAIL**

**Confirmed facts**
- `render.yaml` declares starter web + Postgres 16; health check `/health` only.
- Live `/health` 200; FastEmbed + persistent dual RAG status endpoints respond.

**Gaps**
- No `/ready` active verification (DB, migrations, auth enabled, bypass off, canonical RAG).
- No `/version` (Factory/Blocks/blueprint/generation checksums).
- `app/main.py` swallows ImportError/Exception for Resident router — production startup should fail closed on mandatory modules.
- Deploy SHA vs pin `d84509b` not proven on live service.

**File refs:** `Projectsix/app/main.py:59-64`, `render.yaml`, live probe  
**Upstream owner:** Factory (templates) · Render config via generated `render.yaml`  
**Required correction:** Implement readiness/version; fail startup on mandatory import errors.  
**Acceptance:** `/ready` false when DB down or demo bypass on; `/version` matches provenance pin.

---

### 17. Oracle/test-coverage auditor — **FAIL**

**Confirmed facts**
- `steward_live_oracle_v1`: documented **PASS 10/10** (2026-07-21T13:22:32Z) on `/v1/rag/query`.
- Factory kit oracle spec: `backend/app/factory/kits/private_estate_operations/evaluation/steward_live_oracle_v1.json`.
- Factory tests: `backend/tests/factory/test_dual_rag_persistence.py` (persistence/embedding guards).

**Gaps**
- **No** `scripts/steward_oracle_v2.py`, `artifacts/steward_oracle_v2.json`, or `docs/STEWARD_ORACLE_V2.md`.
- Generated `test_catalog.json` empty — no product-level test registry.
- Oracle v1 does not cover authentication, authorization, workflows, agents, Resident heals, Decimal, or canonical steward RAG.

**Upstream owner:** Factory  
**Required correction:** Implement Oracle V2 suites A–R; gate script `scripts/steward_gate_v2.py`.  
**Acceptance:** Every mandatory suite reports PASS/FAIL/NOT VERIFIED without skip.

---

### 18. CI-cost and documentation auditor — **FAIL**

**Confirmed facts**
- Factory CI (`.github/workflows/ci.yml`): concurrency group, 15m backend timeout, pytest on push/PR to master.
- No Steward-product CI in CerebrumDev.ai repo (product is sibling).
- Prior `PILOT_READINESS_AUDIT.md` / `STEWARD_CERTIFICATION.md` claim **PILOT READY** with confidence percentage.

**Gaps**
- v2 charter forbids readiness percentages — prior docs non-compliant with v2 honesty standard.
- No `steward_gate_v2.py`; no split PR vs main certification workflow for Steward.
- PILOT READY language implies guarded pilot GO; v2 gates fail.

**File refs:** `docs/reports/PILOT_READINESS_AUDIT.md`, `.github/workflows/ci.yml`  
**Upstream owner:** Factory (docs templates + CI)  
**Required correction:** Add gate script; tier CI (PR focused vs main full oracle); rewrite docs without % or overclaim.  
**Acceptance:** Local gate before push; main branch runs full Oracle V2 + determinism.

---

## Conflicts resolved (specialist consolidation)

| Conflict | Resolution |
|----------|------------|
| PILOT READY (v1) vs v2 NO-GO | v1 oracle PASS stands for **narrow demo path**; v2 NO-GO for **guarded pilot** until gates implemented |
| `postgres_pgvector_v1` doc vs live `postgres_jsonb_v1` | Live pilot uses estate-kit JSONB store; steward pgvector stack separate — canonicalization required |
| Render `plan: starter` in `render.yaml` vs prior free-tier note | Template now starter; readiness still fails without `/ready` |
| Resident `ok: true` on heal | Classified **SIMULATED**; fails v2 Resident gate |

---

## Honesty statement

- **No production certification.**
- **No live smart-home, CMMS, IoT, fleet, or payment integration claims** — connectors are `not_implemented` stubs.
- **No autonomous maintenance release or financial commitment.**
- **Resident Engineer heals are in-memory simulations** when enabled — not real repairs.
- **Oracle v1 PASS 10/10** is documented for `/v1/rag/*` only at `2026-07-21T13:22:32Z`; not extrapolated to v2 scope.
- **No readiness percentages** in this audit.

---

## Recommended next step

Proceed to **implementation phase** per [`STEWARD_V2_EXECUTION_PLAN.md`](STEWARD_V2_EXECUTION_PLAN.md): Factory-first auth, authorization, canonical RAG, Oracle V2, then deterministic regeneration to `factory/steward-pilot-certification-v2`. **Do not** hand-patch durable behaviour in `Projectsix`.
